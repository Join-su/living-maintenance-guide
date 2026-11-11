"""D-536 ⑥: an approved know-how text (a technician's free text, sent with the report and approved by the part lead) → one relation row
→ the structure check → a test graph version → the same cases on the version in use and the test version in three groups → added as
the graph version in use, or not added with one notice. Design: agents/llmops-2/reports/126-d536-knowhow-graph-design.md.

The row is the relation node's rule-table shape (D-533 exceptions.csv): rel (a relation the graph has: self · same_line ·
machine_work) · where ('field op value' joined by ';', the fields the Pack allows for that rel) · at_least · then (a cause of the graph,
or a check that checks exactly one cause). Evidence = the past approved records where the condition held (counted by the Pack):
  enough (≥ min_support) and mostly the row's cause → weight normal (the node may put that cause first)
  enough but mostly another cause → basis '이력과 다름' (the node does not use a row its evidence does not back: the test fails it)
  too little (처음 보는 상황 included) → weight low · basis '근거 부족': the order is unchanged, only its check is added at the end
The three groups (the Pack says where the condition holds; the cases' answers stay here and are never sent to the Pack):
  (가) holds and the actual cause is the row's → the first check right more often or fewer wasted checks, at least once
  (나) does not hold → nothing changes
  (다) holds and the actual cause is another → the checks it wastes there are no more than (가) saved
and no new permission breach (a step done '단독' below grade A). A low row's (가) is unknown (None), the other checks decide.

Extraction now is fake only — fake_extractor answers by the text's sha256; a model call comes after the user's approval. The Pack is
called through pack(op, payload): 'support' {row, knowhow} → {n, top} · 'run' {cases: [{id, alarm}], knowhow} → {id: {steps, used, hits}}."""
import hashlib
import json
from pathlib import Path

from . import graphver

OPS = ("<=", ">=", "<", ">", "=")   # the Pack's rule-table operators, longest first
GROUPS = ("가", "나", "다")
NOTHING = "관계를 뽑지 못함"
DISAGREES = "이력과 다름 — 조건이 참이던 지난 기록의 1등 원인이 {top}(근거 {n}건) · 관리자 확인"


def fake_extractor(answers):
    """text → [row]: the fixed answer for the text's sha256, else nothing (no model call)."""
    return lambda text: [dict(answers[k])] if (k := hashlib.sha256(str(text).encode("utf-8")).hexdigest()) in answers else []


def _parts(where):
    out = []
    for part in [p.strip() for p in str(where or "").split(";") if p.strip()]:
        op = next((o for o in OPS if o in part), None)
        if op is None:
            return None
        f, v = (x.strip() for x in part.split(op, 1))
        if not f or not v:
            return None
        if op != "=" and v != "@":
            try:
                float(v)
            except ValueError:
                return None
        out.append((f, op, v))
    return out or None


def _cause(then, decl):
    if then in decl["causes"]:
        return then
    got = decl["checks"].get(then) or []
    return got[0] if len(got) == 1 else None


def _key(row, decl):
    return row.get("rel"), tuple(sorted(_parts(row.get("where")) or [])), int(row.get("at_least") or 0), _cause(row.get("then"), decl) or row.get("then")


def check_row(row, decl, rules):
    """None when the row can sit on the graph, else why not — decl: {"rels": {rel: [fields]}, "causes": [codes], "checks": {section:
    [causes]}} as the Pack and the graph version declare them; rules: the rows in use (the Pack's table and the know-how before)."""
    rel = row.get("rel")
    if rel not in decl["rels"]:
        return f"구조 — 모르는 관계({rel})"
    parts = _parts(row.get("where"))
    if parts is None:
        return f"구조 — 조건 꼴이 아님({row.get('where')})"
    bad = [f for f, _, _ in parts if f not in decl["rels"][rel]]
    if bad:
        return f"구조 — {rel}에서 쓸 수 없는 칸({', '.join(bad)})"
    if not isinstance(row.get("at_least"), int) or isinstance(row.get("at_least"), bool) or row["at_least"] < 1:
        return "구조 — at_least는 1 이상 정수"
    then = row.get("then")
    if then not in decl["causes"] and then not in decl["checks"]:
        return f"구조 — 그래프에 없는 원인 · 점검({then})"
    if _cause(then, decl) is None:
        return f"구조 — 점검 {then}이 원인 여럿을 확인함(원인으로 적어야 함)"
    same = next((r for r in rules if _key(r, decl) == _key(row, decl)), None)
    if same:
        return f"구조 — 이미 있는 규칙과 같음({same.get('id')})"
    return None


def normalize(row, decl):
    """The row as the graph keeps it: then = the cause, where in a fixed order."""
    return {**row, "then": _cause(row["then"], decl), "where": ";".join(f"{f}{op}{v}" for f, op, v in sorted(_parts(row["where"])))}


def measure(out, gold):
    """first (1 when the first step is the right first check) · wasted (the checks loaded before it; all loaded checks when it is not
    loaded) · steps · violations (a step to do '단독' below grade A)."""
    steps = [s["section"] for s in out.get("steps") or []]
    checks = [s["section"] for s in out.get("steps") or [] if s.get("kind") == "check"]
    return {"first": int(bool(steps) and steps[0] == gold["first"]),
            "wasted": checks.index(gold["first"]) if gold["first"] in checks else len(checks), "steps": steps,
            "violations": sum(1 for s in out.get("steps") or [] if s.get("mode") == "단독" and s.get("grade") != "A")}


def run_both(pack, cases, before_rows, after_rows, tags=(None, None)):
    """The same cases on the version in use and on the test version (tags: theirs, for a Pack that reads the rows from the graph); the
    Pack gets the alarms only."""
    sent = [{"id": c["id"], "alarm": c["alarm"]} for c in cases]
    return (pack("run", {"cases": sent, "knowhow": before_rows, "tag": tags[0]}),
            pack("run", {"cases": sent, "knowhow": after_rows, "tag": tags[1]}))


def judge(rows, low):
    """rows: [{group, before, after (measure()), violations}] → (one summary per group, {가 · 나 · 다 · 권한: True · False · None})."""
    groups = []
    for g in GROUPS:
        rs = [r for r in rows if r["group"] == g]
        groups.append({"group": g, "n": len(rs),
                       "first_before": sum(r["before"]["first"] for r in rs), "first_after": sum(r["after"]["first"] for r in rs),
                       "improved": sum(1 for r in rs if r["after"]["first"] > r["before"]["first"] or r["after"]["wasted"] < r["before"]["wasted"]),
                       "changed": sum(1 for r in rs if r["after"]["steps"] != r["before"]["steps"]),
                       "wasted_before": sum(r["before"]["wasted"] for r in rs), "wasted_after": sum(r["after"]["wasted"] for r in rs),
                       "wasted_added": sum(max(0, r["after"]["wasted"] - r["before"]["wasted"]) for r in rs),
                       "wasted_saved": sum(max(0, r["before"]["wasted"] - r["after"]["wasted"]) for r in rs)})
    by = {x["group"]: x for x in groups}
    checks = {"가": None if low else (by["가"]["n"] > 0 and by["가"]["improved"] >= 1), "나": by["나"]["changed"] == 0,
              "다": by["다"]["wasted_added"] <= by["가"]["wasted_saved"], "권한": sum(r["violations"] for r in rows) == 0}
    return groups, checks


def _why(groups, checks):
    by = {x["group"]: x for x in groups}
    out = []
    if checks["가"] is False:
        g = by["가"]
        out.append("가 사례 없음" if not g["n"] else f"가 나아짐 0(이미 맞음 {g['n']}/{g['n']})"
                   if g["first_before"] == g["n"] and not g["wasted_before"] else "가 나아짐 0")
    if checks["나"] is False:
        out.append(f"나 바뀜 {by['나']['changed']}")
    if checks["다"] is False:
        out.append(f"다 헛걸음 +{by['다']['wasted_added']} > 가에서 아낀 {by['가']['wasted_saved']}")
    if checks["권한"] is False:
        out.append("권한 위반")
    return " · ".join(out)


def notices(folder):
    p = Path(folder) / "notices.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []


def _save(folder, rec, notice):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{rec['knowhow']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    if notice:
        with (folder / "notices.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"knowhow": rec["knowhow"], "tag": rec.get("tag"), "why": rec["why"]}, ensure_ascii=False) + "\n")
    return rec


def apply(kh, *, extract, decl, rules, graph, versions, pack, cases, records):
    """One approved know-how {id, text, report_id} all the way: → the record (knowhow · status 추가됨 · 넣지 않음 · row · support · tag ·
    groups · checks · why), saved under records/<id>.json; a 넣지 않음 also appends one line to records/notices.jsonl."""
    rec = {"knowhow": kh["id"], "report_id": kh.get("report_id"), "text_sha256": hashlib.sha256(str(kh["text"]).encode("utf-8")).hexdigest()}
    got = extract(kh["text"])
    if not got:
        return _save(records, {**rec, "status": "넣지 않음", "why": NOTHING}, True)
    row = got[0]
    before = versions.rows()
    why = check_row(row, decl, list(rules) + before)
    if why:
        return _save(records, {**rec, "status": "넣지 않음", "row": row, "why": why}, True)
    row = {**normalize(row, decl), "id": kh["id"], "text_sha256": rec["text_sha256"], **({"report_id": kh["report_id"]} if kh.get("report_id") else {})}
    sup = pack("support", {"row": row, "knowhow": before})
    rec["support"] = {k: sup[k] for k in ("n", "top", "share") if k in sup}
    low = sup["n"] < decl["min_support"]
    row = {**row, "weight": "low" if low else "normal", "basis": "근거 부족" if low else "근거" if sup["top"] == row["then"] else "이력과 다름"}
    try:
        tag, _ = versions.make(graph, before + [row])
    except graphver.GraphError as e:
        return _save(records, {**rec, "status": "넣지 않음", "row": row, "why": f"시험용 그래프 버전 못 만듦 — {e}"}, True)
    base_out, kh_out = run_both(pack, cases, before, before + [row], (versions.current(), tag))
    rows = []
    for c in cases:
        hit = bool((kh_out.get(c["id"]) or {}).get("hits", {}).get(row["id"]))
        b, a = measure(base_out.get(c["id"]) or {}, c["gold"]), measure(kh_out.get(c["id"]) or {}, c["gold"])
        group = "나" if not hit else "가" if c["gold"]["cause"] == row["then"] else "다"
        rows.append({"group": group, "before": b, "after": a, "violations": a["violations"]})
    groups, checks = judge(rows, low)
    checks["구조"] = True
    ok = all(v is not False for v in checks.values())
    why = None if ok else _why(groups, checks) + (" · " + DISAGREES.format(top=sup["top"], n=sup["n"]) if row["basis"] == "이력과 다름" else "")
    rec.update(row=row, tag=tag, groups=groups, checks=checks, status="추가됨" if ok else "넣지 않음", why=why)
    versions.note(tag, test={"knowhow": kh["id"], "groups": groups, "checks": checks})
    if ok:
        versions.adopt(tag)
    else:
        versions.reject(tag, rec["why"])
    return _save(records, rec, not ok)


def pack_runner(python, kg, layer, script=Path(__file__).resolve().parent.parent / "scripts" / "knowhow_pack_run.py"):
    """pack(op, payload) over the Pack's own Python (its graph venv) and scripts/knowhow_pack_run.py — one process per call, JSON in
    and out; a refusal says the exit code and its last line only."""
    import subprocess

    def call(op, payload):
        r = subprocess.run([str(python), "-B", str(script)], input=json.dumps({"kg": str(kg), "layer": layer, "op": op, "payload": payload}, ensure_ascii=False),
                           capture_output=True, text=True, timeout=1800)
        if r.returncode:
            tail = (r.stderr.strip().splitlines() or ["-"])[-1]
            raise RuntimeError(f"Pack 실행 실패(rc {r.returncode}) — {tail[:200]}")
        return json.loads(r.stdout)
    return call


def cases_of(path):
    """The kg-test case file (one JSON a line: record_id · ts · machine · line · group · sensors · gold · category) → [{id, alarm, gold}];
    the gold stays here."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            out.append({"id": c["record_id"], "alarm": {x: c[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}, "gold": c["gold"]})
    return out

