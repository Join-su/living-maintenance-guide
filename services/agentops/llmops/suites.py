"""Evaluation suites for Agent Pack releases (EV-002 CHG-11, CHG-12): read from the Pack work folder's evals/<suite>/ (read
only) and copied into the data dir; scorecard computed from a finished suite Run — columns kept apart, never one score.

suite.yaml: suite, split (fixture|holdout), graph, dataset, review_policy {default, missing_fixture}, judge {enabled | settings},
expectations, review_fixtures (spec v0.5 supplement 2, 15.1; EV-007 CHG-37, CHG-38)
dataset.jsonl line: {"case_id" | "id", "input", "expected": {"final": [check], "nodes": {node_id: [check]}}, "review": {action, edits, reason}}
expectations line: {"case_id", "final": [check], "nodes": {node_id: [check]}}
review_fixtures line: {"case_id", "decision", "edit": {field, value} | [..], "reason", "nodes": {review node: {decision, edit}}}
check: {"path": "a.b[0]" ("" = the value itself), "equals" | "contains" | "contains_all" | "in" | "not_equals" | "not_contains_any": value}, "any_of": [check, …],
"max_key" (+ "tie_order"), or {"reached": false} (nodes only); "rule" is a name the check does not read"""
import hashlib
import json
import re
import shutil
from pathlib import Path

from packrt.ir import strict_yaml
from packrt.ir.releases import dir_hash

POLICIES = ("fixture", "stop_before", "auto_approve")
NOT_MEASURED = "측정 안 됨(실제 사람 검토 없음)"
STOPPED_STATE = "검토 전 멈춤(2차 범위)"
UNFINISHED = ("실패", "timeout", "실행 안 됨")   # spec 15.1 supplement 4: unvisited reached:false is not counted in these cases
STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
ACTIONS = ("approve", "edit", "escalate", "reject")
JUDGE_SETTING_NOTE = "검토 설정: Project provider(suite judge 설정 미적용)"   # decision D-8: suite judge settings are not used yet


class SuiteError(ValueError):
    pass


def available(work_folder):
    base = Path(work_folder) / "evals"
    return sorted(p.name for p in base.iterdir() if (p / "suite.yaml").is_file()) if base.is_dir() else []


def _suite_file(folder, rel, what):
    p = folder / str(rel)
    if not p.is_file() or folder.resolve() not in p.resolve().parents:
        raise SuiteError(f"{what} 파일이 없습니다: {rel}")
    return p


def _jsonl(path, label):
    """(line number, object) of a JSONL file; every line must be a JSON object."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise SuiteError(f"{label} {n}줄을 읽을 수 없습니다 — {e}")
        if not isinstance(row, dict):
            raise SuiteError(f"{label} {n}줄이 JSON 객체가 아닙니다")
        out.append((n, row))
    return out


def _keyed(rows, label, names):
    """Lines of a separate file by case_id: present, known to the dataset, not repeated (CHG-38)."""
    out = {}
    for n, row in rows:
        cid = row.get("case_id")
        if cid in (None, ""):
            raise SuiteError(f"{label} {n}줄에 'case_id'가 없습니다")
        cid = str(cid)
        if cid not in names:
            raise SuiteError(f"{label} {n}줄의 case_id '{cid}'가 dataset에 없습니다")
        if cid in out:
            raise SuiteError(f"{label} {n}줄의 case_id '{cid}'가 중복됩니다")
        out[cid] = (n, row)
    return out


def _edits(edit, where):
    items = edit if isinstance(edit, list) else [edit]
    out = {}
    for e in items:
        if not isinstance(e, dict) or not isinstance(e.get("field"), str) or not e["field"] or "value" not in e:
            raise SuiteError(f"{where}의 edit에 field·value가 없습니다")
        out[e["field"]] = e["value"]
    return out


def _decision(d, where):
    """{decision, edit?} -> {action, edits?}; nothing else is carried over (the ignored fields never reach the runtime)."""
    if not isinstance(d, dict) or d.get("decision") not in ACTIONS:
        raise SuiteError(f"{where}의 decision이 approve·edit·escalate·reject가 아닙니다")
    out = {"action": d["decision"]}
    if d.get("edit") is not None:
        out["edits"] = _edits(d["edit"], where)
    return out


def _fixture(row, where, review_nodes):
    out = _decision(row, where)
    if row.get("reason") is not None:
        out["reason"] = row["reason"]
    nodes = row.get("nodes")
    if nodes is not None:
        if not isinstance(nodes, dict):
            raise SuiteError(f"{where}의 nodes가 mapping이 아닙니다")
        out["by_node"] = {}
        for nid, d in nodes.items():
            if nid not in review_nodes:
                raise SuiteError(f"{where}의 nodes 키 '{nid}'가 이 release의 검토 node가 아닙니다(검토 node: {', '.join(review_nodes) or '없음'})")
            out["by_node"][nid] = _decision(d, f"{where} nodes.{nid}")
    return out


def _then(then, where):
    """EV-079 (D-463 덧 5): a two-step case — {"set": {input path: value}, "take": {input path: first output path}, "drop": [input path]}."""
    if not isinstance(then, dict) or not then or set(then) - {"set", "take", "drop"}:
        raise SuiteError(f"{where}의 then은 set · take · drop만 가진 mapping이어야 합니다")
    path = lambda p: isinstance(p, str) and p.strip() != ""
    if "set" in then and (not isinstance(then["set"], dict) or not all(path(k) for k in then["set"])):
        raise SuiteError(f"{where}의 then.set은 {{입력 경로: 값}}이어야 합니다")
    if "take" in then and (not isinstance(then["take"], dict) or not all(path(k) and path(v) for k, v in then["take"].items())):
        raise SuiteError(f"{where}의 then.take는 {{입력 경로: 첫 출력 경로}}이어야 합니다")
    if "drop" in then and (not isinstance(then["drop"], list) or not all(path(p) for p in then["drop"])):
        raise SuiteError(f"{where}의 then.drop은 입력 경로 목록이어야 합니다")


def _expectation(row, where):
    out = {}
    if "final" in row:
        if not isinstance(row["final"], list) or not all(isinstance(c, dict) for c in row["final"]):
            raise SuiteError(f"{where}의 final이 검사 목록이 아닙니다")
        out["final"] = row["final"]
    if "nodes" in row:
        if not isinstance(row["nodes"], dict) or not all(isinstance(v, list) and all(isinstance(c, dict) for c in v) for v in row["nodes"].values()):
            raise SuiteError(f"{where}의 nodes가 node별 검사 목록이 아닙니다")
        for v in row["nodes"].values():
            for c in v:
                if "evidence" in c:
                    _evidence(c["evidence"], where)
        out["nodes"] = row["nodes"]
    return out


def _locator(loc):
    """A gold source position (EV-043 CHG-210, the c1a15-eval format): history {record_id}, manual {doc, section?, char_start, char_end}."""
    if not isinstance(loc, dict):
        return False
    if "record_id" in loc:
        return isinstance(loc["record_id"], (str, int)) and str(loc["record_id"]) != ""
    s, e = loc.get("char_start"), loc.get("char_end")
    return isinstance(loc.get("doc"), str) and bool(loc["doc"]) and all(isinstance(x, int) and not isinstance(x, bool) for x in (s, e)) and 0 <= s < e


def _evidence(items, where):
    """A retriever node's gold check (EV-043 CHG-210 · notice 080-02): {"evidence": [{store, locator, rule}, …]}."""
    if not isinstance(items, list) or not items or not all(isinstance(x, dict) and _locator(x.get("locator")) for x in items):
        raise SuiteError(f"{where}의 evidence가 정답 위치 목록이 아닙니다(원소 {{store, locator, rule}}, locator = {{record_id}} 또는 {{doc, char_start, char_end}})")


def _judge(meta):
    """(on, settings): a judge mapping turns the model reviewer on unless `enabled: false` (CHG-37, D-2); keys other than enabled
    are settings, which are not used yet (D-8)."""
    j = meta.get("judge")
    if not j and not isinstance(j, dict):   # absent or a false value (false, 0, "", []): off, as before
        return False, False
    if not isinstance(j, dict):   # a true value that is not a mapping used to fail registration without a message
        raise SuiteError("suite.yaml의 judge는 mapping이어야 합니다")
    return j.get("enabled", True) is not False, any(k != "enabled" for k in j)


def read_suite(work_folder, name, pack, review_nodes=()):
    """Check evals/<name>/ of a Pack work folder against the registered Pack. Returns the suite record (cases included).
    review_nodes: human_review node ids of the release's entrypoint graph (review_fixtures `nodes` keys must be among them)."""
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", name or "") or ".." in name:
        raise SuiteError("suite 이름을 쓸 수 없습니다")
    folder = Path(work_folder) / "evals" / name
    if not (folder / "suite.yaml").is_file():
        raise SuiteError(f"평가 suite가 없습니다: evals/{name}/suite.yaml")
    try:
        meta, _ = strict_yaml.load((folder / "suite.yaml").read_text(encoding="utf-8"))
    except strict_yaml.StrictYAMLError as e:
        raise SuiteError(f"suite.yaml {e}")
    if not isinstance(meta, dict):
        raise SuiteError("suite.yaml은 mapping이어야 합니다")
    rp = meta.get("review_policy") or {}
    policy, missing = rp.get("default", "fixture"), rp.get("missing_fixture", "stop_before")
    if policy == "model" or missing == "model":
        raise SuiteError("review_policy 'model'은 이번 버전에서 지원하지 않습니다(fixture·stop_before·auto_approve)")
    if policy not in POLICIES or missing not in ("stop_before", "auto_approve"):
        raise SuiteError(f"review_policy를 쓸 수 없습니다: default {policy!r}, missing_fixture {missing!r}")
    if meta.get("split", "holdout") not in ("fixture", "holdout"):
        raise SuiteError("split은 fixture 또는 holdout입니다")
    if meta.get("graph", pack.get("entrypoint")) != pack.get("entrypoint"):
        raise SuiteError(f"suite의 graph '{meta.get('graph')}'가 이 release의 entrypoint '{pack.get('entrypoint')}'와 다릅니다")
    ds = folder / str(meta.get("dataset", "dataset.jsonl"))
    if not ds.is_file() or ds.resolve().parent != folder.resolve():
        raise SuiteError(f"dataset 파일이 없습니다: {meta.get('dataset', 'dataset.jsonl')}")
    judge, judge_settings = _judge(meta)
    separate = meta.get("expectations") is not None or meta.get("review_fixtures") is not None
    cases, lines = [], {}
    for n, line in enumerate(ds.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise SuiteError(f"dataset {n}줄을 읽을 수 없습니다 — {e}")
        if not isinstance(row, dict) or "input" not in row:
            raise SuiteError(f"dataset {n}줄에 'input'이 없습니다")
        rev = row.get("review")
        if rev is not None and (not isinstance(rev, dict) or rev.get("action") not in ACTIONS):
            raise SuiteError(f"dataset {n}줄의 review.action이 approve·edit·escalate·reject가 아닙니다")
        cid = str(row["case_id"] if row.get("case_id") not in (None, "") else row.get("id", n))   # spec 15.1: case_id, else id, else line
        if separate and cid in lines:
            raise SuiteError(f"dataset {n}줄의 case 이름 '{cid}'가 {lines[cid]}줄과 중복됩니다")
        lines.setdefault(cid, n)
        for chks in (((row.get("expected") or {}).get("nodes") or {}) if isinstance(row.get("expected"), dict) else {}).values():
            for c in chks if isinstance(chks, list) else []:
                if isinstance(c, dict) and "evidence" in c:   # EV-043: an inline gold check is checked like an expectations line
                    _evidence(c["evidence"], f"dataset {n}줄")
        if "then" in row:
            _then(row["then"], f"dataset {n}줄")
        cases.append({"id": cid, "input": row["input"], "expected": row.get("expected") or {}, "review": rev, **({"then": row["then"]} if "then" in row else {})})
    if not cases:
        raise SuiteError("dataset에 case가 없습니다")
    if meta.get("expectations") is not None:   # expectations are only for the scorecard; nothing here reaches the Pack (CHG-38 a)
        label = str(meta["expectations"])
        rows = _keyed(_jsonl(_suite_file(folder, meta["expectations"], "expectations"), label), label, lines)
        for c in cases:
            if c["id"] in rows:
                n, row = rows[c["id"]]
                c["expected"] = _expectation(row, f"{label} {n}줄")
    if meta.get("human_sample") is not None:   # EV-043 (notice 080-02): the person-checked gold of a sample of cards, with their auto gold
        label = str(meta["human_sample"])
        try:
            hs = json.loads(_suite_file(folder, meta["human_sample"], "human_sample").read_text(encoding="utf-8"))
        except ValueError as e:
            raise SuiteError(f"{label}을 읽을 수 없습니다 — {e}")
        rows = hs if isinstance(hs, list) else [{"card_id": k, **v} for k, v in hs.items() if isinstance(v, dict)] if isinstance(hs, dict) else None
        if rows is None or not all(isinstance(r, dict) and "card_id" in r for r in rows):
            raise SuiteError(f"{label}이 card_id 목록이 아닙니다")
        rows = [r for r in rows if str(r["card_id"]) in lines]   # a work-suite subset keeps only its own cards (build_subset keeps the file whole)
        if not rows:
            raise SuiteError(f"{label}의 card_id가 dataset case와 하나도 맞지 않습니다")
        for r in rows:
            for side in ("human", "auto"):
                _evidence(r.get(side), f"{label} {r['card_id']} {side}")
        by = {str(r["card_id"]): {"human": r["human"], "auto": r["auto"]} for r in rows}
        for c in cases:
            if c["id"] in by:
                c["human_sample"] = by[c["id"]]
    if meta.get("review_fixtures") is not None:   # converted to the runtime decision shape; other fields are dropped (CHG-38 b)
        label = str(meta["review_fixtures"])
        rows = _keyed(_jsonl(_suite_file(folder, meta["review_fixtures"], "review_fixtures"), label), label, lines)
        for c in cases:
            if c["id"] in rows:
                n, row = rows[c["id"]]
                c["review"] = _fixture(row, f"{label} {n}줄", list(review_nodes))
    out = {"name": str(meta.get("suite", name)), "folder": name, "split": meta.get("split", "holdout"), "graph": pack.get("entrypoint"),
           "review_policy": {"default": policy, "missing_fixture": missing}, "judge": judge,
           "suite_revision": "sha256:" + dir_hash(folder, exclude=()), "dataset_hash": "sha256:" + hashlib.sha256(ds.read_bytes()).hexdigest(),
           "pack_id": pack["id"], "cases": cases, "source": str(folder)}
    if judge_settings:
        out["judge_settings"] = True
    return out


# ---------------------------------------------------------------- EV-051 R-5 (CHG-R5-1, D-372 · D-387): a gold suite from outside the Pack
TEST_CARD = re.compile(r"EC-T\d+")   # c1a15-eval's test cards: only ever an LLMOps-side confirm suite (D-381 · D-387)
PLAIN_FILE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")   # a file named in a suite.yaml: one plain name in the suite folder


def _plain(name, what):
    name = str(name)
    if not PLAIN_FILE.fullmatch(name) or ".." in name:
        raise SuiteError(f"{what} 파일 이름을 쓸 수 없습니다: {name!r} — suite 폴더 안 이름 하나만")
    return name


def _jsonl_lines(path):
    return [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def gold_stage(gold, dest_root, node, store, inputs=None, cards=None, part=None):
    """CHG-R5-1: <dest_root>/evals/<name>/ made from a hand-off folder (gold: suite.yaml · dataset · expectations · human-sample, one
    node) and, when its inputs are empty, a Pack inputs suite folder (inputs) — then read_suite registers it like any suite. Checks
    first, writes nothing on a refusal (SuiteError): one node · store as the Pack's · no empty gold · case ids the same in dataset and
    expectations · human-sample cards among them · test cards only in a confirm part. cards (ids) · part (work | confirm) take those
    cards only: the lines kept byte for byte, human-sample rewritten by one rule, the name with _work · _confirm, no human_sample line
    when none is left. Returns (folder name, gold record {name, expectations_sha256, human_sample_sha256, cards_sha256, part})."""
    gold = Path(gold)
    meta_text = (gold / "suite.yaml").read_text(encoding="utf-8")
    try:
        meta, _ = strict_yaml.load(meta_text)
    except strict_yaml.StrictYAMLError as e:
        raise SuiteError(f"정답 suite.yaml {e}") from None
    if not isinstance(meta, dict) or not meta.get("expectations"):
        raise SuiteError("정답 폴더의 suite.yaml에 expectations가 없습니다")
    ds_name = _plain(meta.get("dataset", "dataset.jsonl"), "dataset")
    exp_path = gold / _plain(meta["expectations"], "expectations")
    hs_name = _plain(meta["human_sample"], "human_sample") if meta.get("human_sample") is not None else None
    data = _jsonl_lines(gold / ds_name)
    if inputs is not None:   # the hand-off left the inputs empty: the Pack's own inputs suite fills them
        inputs = Path(inputs)
        try:
            imeta, _ = strict_yaml.load((inputs / "suite.yaml").read_text(encoding="utf-8"))
        except strict_yaml.StrictYAMLError as e:
            raise SuiteError(f"입력 suite.yaml {e}") from None
        if not isinstance(imeta, dict):
            raise SuiteError("입력 suite.yaml은 mapping이어야 합니다")
        if any(imeta.get(k) is not None for k in ("expectations", "human_sample", "review_fixtures")):
            raise SuiteError("입력 suite에 expectations · human_sample · review_fixtures가 있습니다 — 입력만 있는 suite여야 합니다")
        if str(imeta.get("dataset", "dataset.jsonl")) != ds_name:
            raise SuiteError(f"입력 suite의 dataset 이름이 정답 쪽({ds_name})과 다릅니다")
        data = _jsonl_lines(inputs / ds_name)
    try:
        rows = [json.loads(ln) for ln in data]
        exp_rows = [json.loads(ln) for ln in _jsonl_lines(exp_path)]
    except ValueError as e:
        raise SuiteError(f"JSON 줄을 읽을 수 없습니다 — {e}") from None
    if not all(isinstance(r, dict) for r in rows + exp_rows) or not exp_rows:
        raise SuiteError("dataset · expectations 줄은 객체여야 하고 expectations가 비어 있으면 안 됩니다")
    if any(not r.get("input") for r in rows):
        raise SuiteError("dataset에 입력이 빈 case가 있습니다 — 넘김 v2의 입력이나 --inputs가 필요합니다")
    ids = [str(r.get("case_id")) for r in rows]
    exp_lines = _jsonl_lines(exp_path)
    exps = {str(r.get("case_id")): ln for r, ln in zip(exp_rows, exp_lines)}
    if set(ids) != set(exps) or len(ids) != len(set(ids)):
        raise SuiteError(f"dataset과 expectations의 case가 다릅니다(빠짐 {sorted(set(exps) - set(ids))[:5]} · 남음 {sorted(set(ids) - set(exps))[:5]})")
    empty = []
    for cid, ln in exps.items():
        nodes = (json.loads(ln).get("nodes") or {})
        if not isinstance(nodes, dict) or not all(isinstance(c, dict) for c in nodes.get(node) or []):
            raise SuiteError(f"{cid}: expectations nodes 모양이 다릅니다")
        if set(nodes) != {node}:
            raise SuiteError(f"{cid}: 정답 node가 {node} 하나가 아닙니다({sorted(nodes)})")
        ev = [e for chk in nodes[node] for e in (chk.get("evidence") or [])]
        if not ev:
            empty.append(cid)
        if any(e.get("store") != store for e in ev):
            raise SuiteError(f"{cid}: 정답 store가 Pack의 store '{store}'가 아닙니다({sorted({e.get('store') for e in ev})})")
    if empty:
        raise SuiteError(f"정답이 빈 카드 {len(empty)}장 — R-5를 멈추고 리드 판단(D-387 (6)): {empty[:5]}")
    hs = json.loads((gold / str(hs_name)).read_text(encoding="utf-8")) if hs_name else None
    card_of = (lambda r: str(r.get("card_id"))) if isinstance(hs, list) else None
    hs_ids = {card_of(r) for r in hs} if isinstance(hs, list) else set(hs or {})
    if hs is not None and not hs_ids <= set(ids):
        raise SuiteError(f"human-sample에 dataset 밖 카드가 있습니다: {sorted(hs_ids - set(ids))[:5]}")
    for r in (hs if isinstance(hs, list) else (hs or {}).values()):   # the person-checked gold too: the node's store only (review N-R5-2)
        for side in ("human", "auto"):
            if any(isinstance(e, dict) and e.get("store") != store for e in (r.get(side) or [])):
                raise SuiteError(f"human-sample 정답 store가 Pack의 store '{store}'가 아닙니다")
    keep = set(ids) if cards is None else set(cards)
    if cards is not None and not keep <= set(ids):
        raise SuiteError(f"--cards에 dataset 밖 카드가 있습니다: {sorted(keep - set(ids))[:5]}")
    tests = sorted(i for i in keep if TEST_CARD.fullmatch(i))
    if tests and part != "confirm":
        raise SuiteError(f"test 카드는 LLMOps 쪽 확인 suite(--part confirm)로만 씁니다: {tests[:5]}")
    name = gold.name + (f"_{part}" if part else "")
    folder = Path(dest_root) / "evals" / name
    folder.mkdir(parents=True)
    (folder / ds_name).write_text("".join(ln + "\n" for ln, r in zip(data, rows) if str(r.get("case_id")) in keep), encoding="utf-8")
    (folder / str(meta["expectations"])).write_text("".join(ln + "\n" for ln in exp_lines if str(json.loads(ln)["case_id"]) in keep), encoding="utf-8")
    kept_hs = [r for r in hs if card_of(r) in keep] if isinstance(hs, list) else {k: v for k, v in (hs or {}).items() if k in keep}
    yaml_lines = meta_text.splitlines(keepends=True)
    if part:
        yaml_lines = [re.sub(r"^suite:\s*(\S+)", lambda m: f"suite: {m.group(1)}_{part}", ln) if ln.startswith("suite:") else ln for ln in yaml_lines]
    if hs_name and kept_hs:
        if cards is None:
            (folder / str(hs_name)).write_bytes((gold / str(hs_name)).read_bytes())
        else:
            (folder / str(hs_name)).write_text(json.dumps(kept_hs, ensure_ascii=False, separators=(", ", ": ")) + "\n", encoding="utf-8")
    elif hs_name:
        yaml_lines = [ln for ln in yaml_lines if not ln.startswith("human_sample:")]
    (folder / "suite.yaml").write_text("".join(yaml_lines), encoding="utf-8")
    sha = lambda b: hashlib.sha256(b).hexdigest()
    rec = {"name": gold.name, "expectations_sha256": sha(exp_path.read_bytes()),
           "human_sample_sha256": sha((gold / str(hs_name)).read_bytes()) if hs_name else None,
           "cards_sha256": sha("\n".join(sorted(keep)).encode()) if cards is not None else None, "part": part,
           "human_sample_note": "자동 확정 — 독립 사람 확인 아님(D-384)" if hs_name else None}
    return name, rec


def register_copy(store, data_dir, pid, su, src):
    """The registration rule of the app's register_suite (same content once per Project · data-dir copy · hash check · a failed copy
    keeps nothing), for the admin command. Returns the record, or raises SuiteError with the same-content suite's name."""
    rec, same = store.add_suite_unique(pid, su)
    if same:
        raise SuiteError(f"이미 같은 내용의 평가 suite가 등록돼 있습니다 — {same['name']}(같은 Pack · suite_revision · dataset_hash)")
    try:
        dest = copy_suite(src, data_dir, rec["id"])
        if "sha256:" + dir_hash(dest, exclude=()) != su["suite_revision"]:
            raise OSError("사본이 읽은 내용과 다릅니다(등록 중 바뀌었을 수 있음)")
    except Exception as e:  # noqa: BLE001
        store.delete_suite(rec["id"])
        d = copy_dir(data_dir, rec["id"])
        if d:
            shutil.rmtree(d, ignore_errors=True)
        raise SuiteError(f"평가 suite를 등록할 수 없습니다 — {e}") from None
    return store.set_suite_copy(rec["id"], dest)


def copy_suite(src, data_dir, sid):
    dest = Path(data_dir) / "suites" / sid
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))   # the same files suite_revision covers
    return dest


def with_cases(src, dest_root, folder, rows):
    """EV-025 OPS-3 CHG-126: a copy of a registered suite's data-dir copy (src, only read) as <dest_root>/evals/<folder>/ with rows
    ({"case_id", "input"}, no expected value — Q-2 A) added at the end of its dataset. The registration then reads it like any suite."""
    dest = Path(dest_root) / "evals" / folder
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    meta, _ = strict_yaml.load((dest / "suite.yaml").read_text(encoding="utf-8"))
    ds = dest / str((meta or {}).get("dataset", "dataset.jsonl"))
    text = ds.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        text += "\n"
    ds.write_text(text + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return dest


def build_subset(src, dest_root, folder, keep, rows=()):
    """EV-035 CHG-180: a copy of a registered suite's data-dir copy (src, only read) as <dest_root>/evals/<folder>/ keeping only the
    cases whose id is in `keep` (dataset lines, and the lines of its expectations · review_fixtures files with those case ids), then
    `rows` ({"case_id", "input"} and, for a case a person checked, its inline field-truth "expected" — EV-065 CHG-266a) at the end —
    the wheel's work suite and hidden suite. Read like any suite (read_suite takes an inline expected as the case's own)."""
    dest = Path(dest_root) / "evals" / folder
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    meta, _ = strict_yaml.load((dest / "suite.yaml").read_text(encoding="utf-8"))
    meta = meta or {}
    ds = dest / str(meta.get("dataset", "dataset.jsonl"))
    keep, kept, order = set(map(str, keep)), [], []
    for n, line in enumerate(ds.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        cid = str(row["case_id"] if row.get("case_id") not in (None, "") else row.get("id", n))
        order.append(cid)
        if cid in keep:
            kept.append(json.dumps(dict(row, case_id=cid), ensure_ascii=False))   # the id stays the same when lines move
    ds.write_text("".join(x + "\n" for x in kept) + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    # every per-case file goes with its cases (a hidden case's answers never stay in the work suite — critic ML1-N3): a JSONL file by
    # its case_id, else by line position when it has one line per dataset line (evaluation_truth.jsonl); node_gold.json's "cases"
    ds_name = ds.name
    for f in sorted(dest.iterdir()):
        if f.name == ds_name or not f.is_file():
            continue
        if f.suffix == ".jsonl":
            rows = [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
            objs = [json.loads(x) for x in rows]
            if all(isinstance(o, dict) and o.get("case_id") not in (None, "") for o in objs):
                rows = [x for x, o in zip(rows, objs) if str(o["case_id"]) in keep]
            elif len(rows) == len(order):
                rows = [x for x, cid in zip(rows, order) if cid in keep]
            else:
                raise SuiteError(f"{f.name}의 줄을 case에 맞출 수 없습니다(case_id 없음 · 줄 수 {len(rows)} ≠ dataset {len(order)})")
            f.write_text("".join(x + "\n" for x in rows), encoding="utf-8")
        elif f.suffix == ".json":
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("cases"), dict):
                d["cases"] = {k: v for k, v in d["cases"].items() if str(k) in keep}
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return dest


def copy_dir(data_dir, sid):
    d = (Path(data_dir) / "suites" / sid).resolve()
    return d if d.parent == (Path(data_dir) / "suites").resolve() else None


def case_review(suite, i):
    """What the runtime does at a human_review node for case i (CHG-11)."""
    rp = suite["review_policy"]
    if rp["default"] == "fixture":
        return {"policy": "fixture", "decision": suite["cases"][i].get("review"), "missing_fixture": rp["missing_fixture"]}
    return {"policy": rp["default"]}


# ---------------------------------------------------------------------------------------------- scorecard (CHG-12)
def _lookup(value, path):
    if path in ("", "$", None):
        return value
    cur = value
    for name, idx in STEP.findall(path):
        try:
            cur = cur[int(idx)] if idx else cur[name]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def check(value, chk):
    actual = _lookup(value, chk.get("path", ""))
    if "equals" in chk:
        return actual == chk["equals"], chk["equals"], actual
    if "contains" in chk:
        hay = actual if isinstance(actual, (str, list)) else json.dumps(actual, ensure_ascii=False)
        return chk["contains"] in hay, chk["contains"], actual
    if "contains_all" in chk:   # CHG-39: every given value is in the list (or every given text in the text)
        want = chk["contains_all"]
        hay = actual if isinstance(actual, (str, list)) else json.dumps(actual, ensure_ascii=False)
        return isinstance(want, list) and all((w in hay) if isinstance(hay, list) or isinstance(w, str) else False for w in want), want, actual
    if "in" in chk:
        return actual in chk["in"], chk["in"], actual
    # D-470 ① (c1a15-eval report v4): the text was reworked · none of these is there · any one of the sub-checks holds
    if "not_equals" in chk:
        return actual != chk["not_equals"], chk["not_equals"], actual
    if "not_contains_any" in chk:
        want = chk["not_contains_any"]
        hay = actual if isinstance(actual, (str, list)) else json.dumps(actual, ensure_ascii=False)
        return isinstance(want, list) and not any(w in hay for w in want), want, actual
    if "any_of" in chk:
        subs = [check(value, sub) for sub in chk["any_of"] if isinstance(sub, dict)]
        return any(x[0] is True for x in subs), [x[1] for x in subs], [x[2] for x in subs]
    if "max_key" in chk:   # EV-079 (D-463 덧 5): the key of the largest value; ties by the check's own tie_order (a stored dict keeps no order)
        if not isinstance(actual, dict) or not actual or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in actual.values()):
            return False, chk["max_key"], actual
        tie = [str(k) for k in chk.get("tie_order") or []]
        rank = lambda k: tie.index(k) if k in tie else len(tie)
        top = max(actual.values())
        best = sorted([k for k, v in actual.items() if v == top], key=rank)[0]
        return best == chk["max_key"], chk["max_key"], best
    return None, None, actual


def _final_value(case):
    f = case.get("final")
    try:
        return json.loads(f) if isinstance(f, str) and f[:1] in "{[" else f
    except ValueError:
        return f


def _ratio(ok, n, none):
    return {"v": ok / n, "n": n, "ok": ok} if n else {"none": none}


def scorecard(run, suite_cases, graph, judge=None, judge_settings=False, sections=None):
    cases = run["cases"]
    nodes = {n["name"]: n for n in graph["nodes"]}
    reasons, mech, fx_final, fx_node, branch, judged, by_case = [], {}, [0, 0], [0, 0], {}, {}, {}
    per_node = {n: {"reached": 0, "expected": [0, 0]} for n in nodes}
    final_skipped = 0
    for c, sc in zip(cases, suite_cases):
        visits = c.get("visits") or []
        last = {v["node"]: v for v in visits}
        for n in {v["node"] for v in visits if v.get("status") in ("done", "stopped")}:
            if n in per_node:
                per_node[n]["reached"] += 1
        for v in visits:
            for chk in (v.get("checks") or []) + [{"rule": "expect." + x["check"], "passed": x["passed"], "detail": x.get("detail")} for x in v.get("expect_checks") or []]:
                if chk.get("passed") is None:
                    continue
                m = mech.setdefault(v["node"], {}).setdefault(chk["rule"], [0, 0])
                m[1] += 1; m[0] += bool(chk["passed"])
                if not chk["passed"]:
                    reasons.append({"case": c["i"] + 1, "node": v["node"], "rule": chk["rule"], "expected": "통과", "actual": chk.get("detail")})
            if v.get("route"):
                b = branch.setdefault(v["node"], {})
                b[v["route"]["target"]] = b.get(v["route"]["target"], 0) + 1
        exp = sc.get("expected") or {}
        if exp.get("final"):
            if c["state"] == STOPPED_STATE:
                final_skipped += 1   # final expectations are about the final product; a run stopped before review has none (C-1)
            elif c["state"] == "완료":
                fv = _final_value(c)
                for chk in exp["final"]:
                    ok, want, got = check(fv, chk)
                    fx_final[1] += 1; fx_final[0] += bool(ok)
                    if not ok:
                        reasons.append({"case": c["i"] + 1, "node": "(최종)", "path": chk.get("path", ""), "expected": want, "actual": got})
            else:
                fx_final[1] += len(exp["final"])
        for nid, chks in (exp.get("nodes") or {}).items():
            v = last.get(nid)
            for chk in chks:
                if "evidence" in chk:
                    continue   # EV-043 CHG-210: a gold position is scored in retrieval_metrics, never as an expectation match
                if chk.get("reached") is False and not any(k in chk for k in ("equals", "contains", "contains_all", "in", "not_equals", "not_contains_any", "any_of")):   # CHG-39: passes when the case has no visit of the node at all
                    if v is None and c["state"] in UNFINISHED:
                        continue   # CHG-53: a case that failed may have gone there had it not failed — not evaluated
                    ok, want, got = v is None, "도달 안 함", None if v is None else v.get("status")
                elif v is None or v.get("status") not in ("done",):
                    continue   # node not reached: not evaluated
                else:
                    ok, want, got = check(v.get("output"), chk)
                fx_node[1] += 1; fx_node[0] += bool(ok)
                if nid in per_node:
                    per_node[nid]["expected"][1] += 1; per_node[nid]["expected"][0] += bool(ok)
                if not ok:
                    reasons.append({"case": c["i"] + 1, "node": nid, "path": chk.get("path", ""), "expected": want, "actual": got})
        if judge is not None:
            for v in visits:
                text = ((nodes.get(v["node"]) or {}).get("expect") or {}).get("text")
                if text and v.get("status") == "done":
                    r = judge(text, v.get("input"), v.get("output"))
                    judged.setdefault(v["node"], []).append(r)
                    if isinstance(r, dict):
                        by_case.setdefault(c["i"], []).append(r["score"])
    for n, node in nodes.items():   # declared checks on nodes no case reached
        declared = node["kind"].startswith("validate") or ((node.get("expect") or {}).get("checks"))
        if declared and n not in mech:
            mech[n] = None
    for n, node in nodes.items():   # a router no case reached is "not evaluated", never 0% (C-1 missing-data policy)
        if node["kind"].startswith("router") and n not in branch:
            branch[n] = None
    judge_sum = _judge_summary(judge, judged)
    if judge_sum.get("v") is not None:   # EV-035 CHG-182 ③: each case's lowest node judge score (a row case with a judge failure is not '고침')
        judge_sum["cases"] = {str(i): min(s) for i, s in by_case.items()}
    if judge_settings and judge_sum.get("v") is not None:
        judge_sum["setting"] = JUDGE_SETTING_NOTE
    node_table = []
    for n, node in nodes.items():
        m = mech.get(n)
        jn = (judge_sum.get("nodes") or {}).get(n)
        node_table.append({"node": n, "kind": node["kind"], "reached": per_node[n]["reached"], "cases": len(cases),
                           "checks": {} if n not in mech else (None if m is None else {r: {"v": ok / tot, "ok": ok, "n": tot} for r, (ok, tot) in m.items()}),
                           "expected": _ratio(per_node[n]["expected"][0], per_node[n]["expected"][1], "기대값 없음"),
                           "judge": jn if jn is not None else ({"none": "expect.text 없음"} if not (node.get("expect") or {}).get("text") else
                                                              {"none": judge_sum.get("none", "채점 안 됨")})})
    done = [c for c in cases if c["state"] in ("완료", STOPPED_STATE)]
    fails = sum(c["state"] in ("실패", "timeout") for c in cases)
    ran = [c for c in cases if c["state"] != "실행 안 됨"]
    out = {
        "mechanical_checks": {n: ({"none": "평가 안 됨(도달 안 함)"} if m is None else {r: {"v": ok / tot, "ok": ok, "n": tot} for r, (ok, tot) in m.items()}) for n, m in mech.items()},
        "fixture_agreement": {"final": _ratio(fx_final[0], fx_final[1], "기대값 없음"), "nodes": _ratio(fx_node[0], fx_node[1], "기대값 없음"),
                              "final_not_evaluated_stopped": final_skipped},
        "branch_ratio": {n: ({"none": "평가 안 됨(도달 안 함)"} if b is None else {t: {"n": k, "v": k / sum(b.values())} for t, k in b.items()}) for n, b in branch.items()},
        "model_judge": judge_sum,
        "nodes": node_table,
        "post_human_review_quality": {"none": NOT_MEASURED},
        "latency": {"v": sum(c.get("ms") or 0 for c in done) / len(done) / 1000} if done else {"none": "완료된 case 없음"},
        "tokens": {"none": "token 미측정(실제 모드)"} if any(c.get("tokens") is None for c in ran) else {"v": sum(c.get("tokens") or 0 for c in ran) / 1000},
        "failure_rate": {"v": fails / len(cases)} if cases else {"none": "case 없음"},
        "review_sources": _review_sources(cases),
        "review_auto_actions": _auto_actions(cases),   # EV-SUI-01 CHG-S04: what the automatic reviews did (approve · escalate · reject · edit)
        "failure_reasons": reasons[:200],
    }
    rm = retrieval_metrics(cases, suite_cases, sections)   # D-492 덧 15: the Run's corpus sections for fixed-size pieces
    if rm:   # only when a suite case has gold positions for a node (EV-043 CHG-210)
        out["retrieval_metrics"] = rm
    mm = model_metrics(cases, suite_cases, graph, run.get("manifest"))
    if mm:   # only when the graph has a predict node (EV-042 CHG-206); other suite Runs keep the same scorecard
        out["model_metrics"] = mm
    return out


MM_NO_FILE = "정상 class 못 읽음(모델 release 파일 없음)"
MM_CHANGED = "정상 class 못 읽음(모델 release 파일이 실행 때와 다름)"
MM_NO_NORMAL = "정상 class 정의 없음"
MM_NO_ALARM = "알람 없음(threshold_config 없음)"


def release_manifest(manifest, name):
    """(model release manifest.json, None) or (None, reason) — read only from the Run manifest's `location` (the original release
    folder; LLMOps keeps no copy of it) and only when its bytes are the ones the Run used: sha256 = that entry's manifest_sha256
    (packrt hashes the whole file, threshold_config included). EV-042 CHG-206, E3 critic B-2."""
    rel = ((manifest or {}).get("model_releases") or {}).get(name) or {}
    if not rel.get("location"):
        return None, MM_NO_FILE
    try:
        raw = (Path(rel["location"]) / "manifest.json").read_bytes()
    except OSError:
        return None, MM_NO_FILE
    if hashlib.sha256(raw).hexdigest() != str(rel.get("manifest_sha256") or "").removeprefix("sha256:"):
        return None, MM_CHANGED
    try:
        data = json.loads(raw)
    except ValueError:
        return None, MM_CHANGED
    return (data, None) if isinstance(data, dict) else (None, MM_CHANGED)


def model_metrics(cases, suite_cases, graph, manifest):
    """EV-042 CHG-206 (RX-L2): per predict@ node of the graph — the expected class (the suite expectation `equals` on
    `<writes[0]>.type`, D-3 common vocabulary; node_gold · evaluation_truth are not read) against the predicted class (the node's last
    done visit output) as a confusion matrix, accuracy, recall · precision per class and the normal-case false alarm rate (visit
    `alarm` on cases expected in the release's normal classes). Ratios carry ok/n, a zero denominator gives a reason — never 0."""
    out = {}
    for node in (graph or {}).get("nodes") or []:
        if not str(node.get("kind", "")).startswith("predict@") or not node.get("writes"):
            continue
        name, path = node["name"], f"{node['writes'][0]}.type"
        pairs, no_label, not_reached, alarms = [], 0, 0, []
        for c, sc in zip(cases, suite_cases):
            want = next((chk["equals"] for chk in ((sc.get("expected") or {}).get("nodes") or {}).get(name) or []
                         if chk.get("path") == path and "equals" in chk), None)
            if want is None:
                no_label += 1
                continue
            v = next((x for x in reversed(c.get("visits") or []) if x.get("node") == name and x.get("status") == "done"), None)
            got = _lookup(v.get("output"), path) if v else None
            if got is None:
                not_reached += 1
                continue
            pairs.append((str(want), str(got)))
            alarms.append(v.get("alarm"))
        mrel, why = release_manifest(manifest, (node.get("params") or {}).get("model"))
        seen = list(dict.fromkeys([w for w, _ in pairs] + [g for _, g in pairs]))
        order = [str(x) for x in (mrel or {}).get("classes") or []]
        classes = [x for x in order if x in seen] + [x for x in seen if x not in order] if order else seen
        matrix = {w: {g: sum(1 for p in pairs if p == (w, g)) for g in classes} for w in classes if any(p[0] == w for p in pairs)}
        recall = {k: _ratio(sum(1 for w, g in pairs if w == k and g == k), sum(1 for w, _ in pairs if w == k), "기대 case 없음") for k in classes}
        precision = {k: _ratio(sum(1 for w, g in pairs if w == k and g == k), sum(1 for _, g in pairs if g == k), "예측 case 없음") for k in classes}
        if any(a is None for a in alarms) or not alarms:
            fa = {"none": MM_NO_ALARM if alarms else "기대 case 없음"}
        elif mrel is None:
            fa = {"none": why}
        else:
            normal = ((mrel.get("threshold_config") or {}).get("values") or {}).get("normal_classes")
            if not normal:
                fa = {"none": MM_NO_NORMAL}
            else:
                hits = [a for (w, _), a in zip(pairs, alarms) if w in normal]
                fa = _ratio(sum(1 for a in hits if a), len(hits), "정상 기대 case 없음")
        out[name] = {"n": len(pairs), "no_label": no_label, "not_reached": not_reached, "classes": classes, "matrix": matrix,
                     "accuracy": _ratio(sum(1 for w, g in pairs if w == g), len(pairs), "기대 case 없음"),
                     "recall": recall, "precision": precision, "normal_false_alarm": fa}
    return out


def _judge_summary(judge, judged):
    if judge is None:
        return {"none": "꺼짐"}
    flat = [r for rs in judged.values() for r in rs]
    scores = [r["score"] for r in flat if isinstance(r, dict)]
    if not scores:
        if not flat:
            return {"none": "평가 안 됨(expect.text가 있는 node에 도달 안 함)"}
        return {"none": next(r for r in flat if isinstance(r, str))}   # 채점 안 됨(키 없음) / 채점 안 됨(호출 실패)
    nodes = {}
    for n, rs in judged.items():
        ok = [r for r in rs if isinstance(r, dict)]
        nodes[n] = {"v": sum(r["score"] for r in ok) / len(ok), "n": len(ok), "why": [r["why"] for r in ok][:3]} if ok else {"none": next((r for r in rs if isinstance(r, str)), "평가 안 됨")}
    ok = [r for r in flat if isinstance(r, dict)]
    served = sorted({r["served_model"] for r in ok if r.get("served_model")})
    return {"v": sum(scores) / len(scores), "nodes": nodes, "why": [r["why"] for r in ok][:3],
            "provider": next((r.get("provider") for r in ok), None),
            # EV-036 (answer 036-01): the judge provider · model asked for, and the model(s) the provider says answered
            "llm_provider": next((r.get("llm_provider") for r in ok if r.get("llm_provider")), None),
            "model": next((r.get("model") for r in ok if r.get("model")), None),
            "served_model": " · ".join(served) if served else None}


def _auto_actions(cases):
    """EV-SUI-01 CHG-S04: automatic reviews (actor_kind auto — no person reviewed) by their action."""
    out = {}
    for c in cases:
        for v in c.get("visits") or []:
            r = v.get("review") or {}
            if r.get("actor_kind") == "auto":
                out[r.get("action") or "기타"] = out.get(r.get("action") or "기타", 0) + 1
    return out


def _review_sources(cases):
    out = {"fixture": 0, "auto": 0, "model": 0, "사람": 0, "기타": 0}   # kept apart; nothing here becomes '사람' by default
    for c in cases:
        for v in c.get("visits") or []:
            if v.get("review"):
                k = v["review"].get("actor_kind")
                out[k if k in ("fixture", "auto", "model") else "기타"] += 1
    return out


# ---------------------------------------------------------------- EV-043 CHG-210: the retriever's gold positions (contract-locked F)
TOP = 10


def _spans(result, gold, sections=None):
    """(result [a, b), gold [a, b), the gold's section [a, b) or None) in one frame, False when they cannot meet (another history
    record · doc · section), None when it cannot be told: D-492 덧 15 (contract F 33) — a fixed-size piece has document positions and
    no section, so the gold moves to document positions by its section's start in the Run's corpus (`sections` {doc: {section: (start,
    end)}}); without that the pair is not known."""
    a, b = result.get("locator") or {}, gold.get("locator") or {}
    if gold.get("store") and result.get("store") and gold["store"] != result["store"]:
        return False
    if "record_id" in b:
        return ((0, 1), (0, 1), None) if a.get("record_id") is not None and str(a["record_id"]) == str(b["record_id"]) else False
    if a.get("doc") != b.get("doc") or (a.get("section") and b.get("section") and a["section"] != b["section"]):
        return False
    try:
        ra, ga = (a["char_start"], a["char_end"]), (b["char_start"], b["char_end"])
    except (KeyError, TypeError):
        return False
    if b.get("section") and not a.get("section"):
        span = ((sections or {}).get(b["doc"]) or {}).get(str(b["section"]))
        if span is None:
            return None
        return ra, (span[0] + ga[0], span[0] + ga[1]), tuple(span)
    return ra, ga, None   # both at section positions (or both at document positions): a section piece is inside its section


def _hit(result, gold, sections=None):
    """A result matches a gold position: the same history record, or the same doc (and section when both have one) overlapping by at
    least one character over [char_start, char_end) — a fixed-size piece against the gold at document positions (D-492 덧 15). None
    when it cannot be told (a fixed-size piece and no corpus sections)."""
    sp = _spans(result, gold, sections)
    if sp is None or sp is False:
        return sp if sp is None else False
    (r0, r1), (g0, g1), _ = sp
    return max(r0, g0) < min(r1, g1)


def _case_recall(results, golds, store, sections=None):
    """(hits, denominator): golds hit by the top 10 results (each gold once), over min(gold count, 10); None when a result's match
    cannot be told (D-492 덧 15: fixed-size pieces and no corpus sections) — the case is counted apart, never as a 0."""
    top = [dict(r, store=r.get("store") or store) for r in results[:TOP]]
    hits = 0
    for g in golds:
        got = [_hit(r, g, sections) for r in top]
        if None in got:
            return None
        hits += any(got)
    n = min(len(golds), TOP)
    return min(hits, n), n


RANK_MAX = 50   # D-492 덧 16: the ranked results a case keeps for the same-character-budget recall


def _ranked(results, golds, store, sections=None):
    """D-492 덧 16: {lens, first, n} — each ranked result's characters (locator end − start), each gold's first hitting rank (None =
    none in the kept ranks) and the denominator min(gold count, 10); the budget recall is counted from it for any budget later."""
    rs = [dict(r, store=r.get("store") or store) for r in results[:RANK_MAX]]
    lens = []
    for r in rs:
        loc = r.get("locator") or {}
        try:
            lens.append(int(loc["char_end"]) - int(loc["char_start"]))
        except (KeyError, TypeError, ValueError):
            return None
    first = [next((i for i, r in enumerate(rs) if _hit(r, g, sections)), None) for g in golds]
    return {"lens": lens, "first": first, "n": min(len(golds), TOP)}


def _pairs(results, golds, store, sections=None):
    """D-492 덧 16 (eval 101 · its script's definitions): for each (gold, hitting top-10 piece) pair, the share of the gold the piece
    covers and the share of the piece outside the gold's section."""
    out = []
    for g in golds:
        for r in results[:TOP]:
            sp = _spans(dict(r, store=r.get("store") or store), g, sections)
            if not sp:
                continue
            (r0, r1), (g0, g1), sec = sp
            if max(r0, g0) >= min(r1, g1) or r1 <= r0 or g1 <= g0:
                continue
            inside = (max(0, min(r1, sec[1]) - max(r0, sec[0])) if sec else r1 - r0)
            out.append(((min(r1, g1) - max(r0, g0)) / (g1 - g0), 1 - inside / (r1 - r0)))
    return out


def char_budget(m):
    """D-492 덧 16 (Q-1 (가)): B = the Run's top-10 results' characters a case, averaged over its counted cases; None without them."""
    rows = [r for r in (m or {}).get("ranked") or [] if r]
    return sum(sum(r["lens"][:TOP]) for r in rows) / len(rows) if rows else None


def budget_recall(m, budget):
    """D-492 덧 16 (contract F 37): recall with the results read in rank order while their characters fit `budget` (the first one
    always) — Σ golds first hit inside / Σ min(gold, 10), as recall@10 counts; None without a budget or ranked cases."""
    rows = [r for r in (m or {}).get("ranked") or [] if r]
    if budget is None or not rows:
        return None
    ok = n = 0
    for r in rows:
        k, used = 0, 0
        for ln in r["lens"]:
            if k and used + ln > budget:
                break
            k, used = k + 1, used + ln
        ok += min(sum(1 for f in r["first"] if f is not None and f < k), r["n"])
        n += r["n"]
    return _ratio(ok, n, "정답 위치를 셀 case 없음")


def retrieval_metrics(cases, suite_cases, sections=None):
    """Per retriever node with gold positions: recall@10 (Σ hits / Σ min(gold, 10), micro), hit@10, the person-checked sample
    (human-sample.json: its human gold, and the same cards' auto gold), cases the retriever did not run on (a session's next Run ·
    not reached) and cases whose results carry no position (retrieve@1). A zero denominator gives a reason, never 0. D-492 덧 15 · 16:
    `sections` (the Run's corpus) lets fixed-size pieces be counted, else such a case is 'no_sections'; each counted case keeps its
    ranked characters (the budget recall, counted later on a base Run's budget) and two reference cells — shown only."""
    nodes = {}
    for c, sc in zip(cases, suite_cases):
        for nid, chks in ((sc.get("expected") or {}).get("nodes") or {}).items():
            golds = [g for chk in chks if "evidence" in chk for g in chk["evidence"]]
            if golds:
                nodes.setdefault(nid, []).append((c, sc, golds))
    out = {}
    for nid, rows in nodes.items():
        acc = {"r": [0, 0], "h": [0, 0], "hu": [0, 0], "ha": [0, 0]}
        no_ret = no_loc = no_sec = 0
        ranked, pairs = [], []
        for c, sc, golds in rows:
            v = next((x for x in reversed(c.get("visits") or []) if x.get("node") == nid and x.get("status") == "done"), None)
            ret = (v or {}).get("retrievals") if v else None
            if not isinstance(ret, dict):
                no_ret += 1
                continue
            results = ret.get("results") or []
            kind = str(v.get("kind") or "")
            if (kind and not kind.startswith("retrieve@2")) or (not kind and results and not any(isinstance(r.get("locator"), dict) for r in results)):
                no_loc += 1   # retrieve@1 keeps no positions — counted apart even with no results, never as a measured 0 (slice critic R1-C1-B1)
                continue
            got = _case_recall(results, golds, ret.get("store"), sections)
            if got is None:   # D-492 덧 15: fixed-size pieces and no corpus sections — not counted, never a 0
                no_sec += 1
                continue
            hits, n = got
            ranked.append(_ranked(results, golds, ret.get("store"), sections))
            pairs += _pairs(results, golds, ret.get("store"), sections)
            acc["r"][0] += hits; acc["r"][1] += n
            acc["h"][0] += bool(hits); acc["h"][1] += 1
            hs = sc.get("human_sample")
            if hs:
                for key, side in (("hu", "human"), ("ha", "auto")):
                    hm = _case_recall(results, hs[side], ret.get("store"), sections) or (0, 0)
                    acc[key][0] += hm[0]; acc[key][1] += hm[1]
        ratio = lambda k, none: _ratio(acc[k][0], acc[k][1], none)
        out[nid] = {"recall@10": ratio("r", "정답 위치를 셀 case 없음"), "hit@10": ratio("h", "정답 위치를 셀 case 없음"),
                    "recall@10_human": ratio("hu", "사람 확인 표본 없음"), "recall@10_human_auto": ratio("ha", "사람 확인 표본 없음"),
                    "n": len(rows), "no_retrieval": no_ret, "no_locator": no_loc, "no_sections": no_sec, "ranked": ranked,
                    "partial_half": _ratio(sum(1 for cov, _ in pairs if cov < 0.5), len(pairs), "닿은 정답 없음"),
                    "outside_section": {"v": sum(o for _, o in pairs) / len(pairs)} if pairs else {"none": "닿은 정답 없음"}}
    return out

