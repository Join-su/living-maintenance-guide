"""Comparison table (P-10): one row per release with the comparison Run's eight metrics, only from Runs on the chosen run
input. A pure function over stored Runs (their summaries were fixed when they ended, P-07). Read only."""

import hashlib
import json
import time

from packrt import trace as ptrace

from .store import suite_key

METRICS = ["정답 일치", "LLM 점수", "집계 점수", "성공률", "오류 수", "timeout 수", "latency", "token"]
FINISHED = ("완료", "일부 실패")  # default comparison Run: the latest finished one; a cancelled Run only when chosen


def metrics(summary):
    if not summary:
        return {k: {"none": "점수 없음"} for k in METRICS}
    m = summary
    return {"정답 일치": m["rule"], "LLM 점수": m["llm"], "집계 점수": m["agg"], "성공률": m["success"], "오류 수": {"v": m["fails"]},
            "timeout 수": {"v": m["tos"]}, "latency": m["latency"], "token": m["tokens"]}


def default_input(inputs, runs):
    """The input of the most recent Run (that still exists as an input), else the first input."""
    ids = [i["id"] for i in inputs]
    for r in sorted(runs, key=lambda r: r["created_at"], reverse=True):
        if r["input"]["id"] in ids:
            return r["input"]["id"]
    return ids[0] if ids else None


def suite_tag(suites, s):
    """EV-012 CHG-70: 'S-n · MM-DD HH:MM 등록' — n is the registration order in the Project (suites in created_at order)."""
    n = next((i for i, x in enumerate(suites, 1) if x["id"] == s["id"]), None)   # None: removed meanwhile (a failed copy of a concurrent request)
    return (f"S-{n} · " if n else "") + f"{time.strftime('%m-%d %H:%M', time.localtime(s['created_at']))} 등록"


def suite_groups(suites):
    """EV-012 CHG-71: suite id -> ids of the same-content registrations of the Project, earliest first (the first is the group's representative)."""
    by_key = {}
    for s in suites:
        by_key.setdefault(suite_key(s), []).append(s["id"])
    return {sid: ids for ids in by_key.values() for sid in ids}


def compare_rows(releases, runs, input_id, picks=None):
    """Rows for the chosen run input. input_id: one input id, or a set of ids that count as the same input (same-content
    evaluation suites, EV-012 CHG-71). picks: {release id: run uid} chosen on screen (any finished or cancelled Run of that
    release on this input). Returns (rows, any_finished).
    EV-064 CHG-CF-1 (D-406 ①): the anchor is the input's latest finished Run (created last; the same time: the later row) — the
    row holding it gets "anchor": True. A row without a pick defaults to its latest finished Run under the anchor's condition
    (`condition`, the same rule as the P09 head), else its latest finished Run as before; "same" says whether the row's Run is
    under the anchor's condition and "differs" names the fields that are not. No finished Run: no anchor, every "same" False."""
    picks = picks or {}
    ids = set(input_id) if isinstance(input_id, (set, frozenset, list, tuple)) else {input_id}
    per, any_finished = [], False
    for rel in releases:
        mine = sorted((r for r in runs if r["rel"] == rel["id"] and r["input"]["id"] in ids and r["status"] != "실행 중"), key=lambda r: r["created_at"])
        finished = [r for r in mine if r["status"] in FINISHED]
        any_finished |= bool(finished)
        per.append((rel, mine, finished))
    last = [(f[-1]["created_at"], i) for i, (_, _, f) in enumerate(per) if f]
    at = max(last)[1] if last else None
    anchor = per[at][2][-1] if at is not None else None
    want = condition(anchor) if anchor else None
    rows = []
    for i, (rel, mine, finished) in enumerate(per):
        chosen = next((r for r in mine if r["uid"] == picks.get(rel["id"])), None)
        if chosen is None and want is not None:
            chosen = next((r for r in reversed(finished) if condition(r) == want), None)
        chosen = chosen or (finished[-1] if finished else None)
        have = condition(chosen) if chosen and want is not None else None
        differs = [k for k in want if have[k] != want[k]] if have is not None else []
        rows.append({"rel": rel["id"], "name": rel["name"], "color": rel["color"],
                     "runs": [{"id": r["uid"], "label": r["label"], "status": r["status"]} for r in mine],
                     "run": chosen["uid"] if chosen else None, "label": chosen["label"] if chosen else None,
                     "metrics": metrics(chosen.get("summary")) if chosen else None,
                     "anchor": i == at, "same": have is not None and not differs, "differs": differs})
        if i == at:
            rows[-1]["anchor_run"] = anchor["uid"]
    return rows, any_finished


# EV-050 CHG-W2-01 · 02 (D-250 (7)): packrt 0.12.0 records how a vector DB version was made — corpus_release · index_recipe ·
# builder beside embedding in the run manifest's evidence_index_releases.<store>. Two versions made from the same corpus,
# embedding and builder whose recipes differ only in chunk size · overlap are the same condition; a store without all four
# (made by hand, or an older runtime) is compared by its hashes as before.
CHUNK_KNOBS = ("split", "chunk_size", "overlap")   # D-492 덧 15 (contract F 36): the same condition = a difference in these only


def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _recipe_key(entry):
    """('recipe', corpus sha, embedding, builder, sha of the recipe without the cut · chunk_size · overlap), or None."""
    e = entry or {}
    corpus, recipe, builder, emb = (e.get("corpus_release") or {}).get("sha256"), e.get("index_recipe"), e.get("builder"), e.get("embedding")
    if not corpus or not isinstance(recipe, dict) or not builder or not emb:
        return None
    rest = {k: v for k, v in recipe.items() if k not in CHUNK_KNOBS}
    return ("recipe", corpus, _canon(emb), _canon(builder), hashlib.sha256(_canon(rest).encode()).hexdigest())


def _index_item(k, v):
    rk = _recipe_key(v)
    return (k, *rk) if rk else (k, v.get("manifest_sha256"), v.get("index_sha256"))


def _condition(man):
    """What must be the same for two suite Runs to be compared (spec 3.2/3.3, EV-002 CHG-12)."""
    man = man or {}
    ev = man.get("evaluation") or {}
    return {"평가 suite": (ev.get("suite_revision"), ev.get("dataset_hash")),
            "모델 release": tuple(sorted((k, v.get("manifest_sha256"), v.get("artifact_sha256")) for k, v in (man.get("model_releases") or {}).items())),
            "벡터 DB release": tuple(sorted(_index_item(k, v) for k, v in (man.get("evidence_index_releases") or {}).items())),
            "표 파일": tuple(sorted((k, v.get("sha256")) for k, v in (man.get("table_snapshots") or {}).items())),
            "runtime": (man.get("runtime") or {}).get("version")}


def no_expectation(graph, ref_graph, suite_cases):
    """Error #18 (manual p.32): nodes of this Run's graph that the reference Run's graph did not have and that no case of the
    evaluation suite (its registered copy in LLMOps) has an expectation for. Shown as '기대값 없음' apart from the node
    agreement, never counted as a pass or a failure."""
    have = {n for c in suite_cases or () for n in ((c.get("expected") or {}).get("nodes") or {})}
    ref = {n["name"] for n in (ref_graph or {}).get("nodes") or []}
    return [n["name"] for n in (graph or {}).get("nodes") or [] if n["name"] not in ref and n["name"] not in have]


def expect_removed(graph, suite_cases):
    """Instruction 019f-4: nodes the evaluation suite has expectations for that this Run's graph no longer has (removed by a
    structural edit) — '기대값 node 없음(지움)', shown in the same cell as '기대값 없음'."""
    have = {n["name"] for n in (graph or {}).get("nodes") or []}
    want = sorted({n for c in suite_cases or () for n in ((c.get("expected") or {}).get("nodes") or {})})
    return [n for n in want if n not in have] if have else []


def scorecard_rows(rows, runs, suite_cases=()):
    """Scorecard comparison for a suite input: one row per release with a chosen Run; a row whose conditions differ from the
    reference row — the anchor's row (EV-064 CHG-CF-1), else the first compared row — is shown but not compared. A different
    Pack environment is only a warning."""
    by_uid = {r["uid"]: r for r in runs}
    out, ref = [], None
    first = next((row for row in rows if row.get("anchor") and by_uid.get(row.get("run"))), None)   # EV-064 CHG-CF-1: the anchor's row is the reference
    if first:
        r0 = by_uid[first["run"]]
        ref = (_condition(r0.get("manifest") or {}), r0.get("manifest") or {}, r0.get("graph"), r0)
    for row in rows:
        run = by_uid.get(row.get("run"))
        if not run:
            continue
        man = run.get("manifest") or {}
        cond = _condition(man)
        item = {"rel": row["rel"], "name": row["name"], "color": row["color"], "run": row["label"], "revision": man.get("pack_revision"),
                "scorecard": (run.get("summary") or {}).get("scorecard"), "same": True, "differs": [], "warning": None, "no_expect": [], "expect_removed": [],
                "run_id": run["uid"]}
        item["cells"] = columns(item["scorecard"], budgets_of((((ref[3] if ref else run) or {}).get("summary") or {}).get("scorecard")))   # D-492 덧 16: the reference's budget   # EV-071 (D-443 ②): every chosen row shows its own cells, not only the first two
        if ref is None or row is first:
            ref = ref or (cond, man, run.get("graph"), run)
            item["reference"] = True
        else:
            item["differs"] = [k for k in cond if cond[k] != ref[0][k]]
            item["same"] = not item["differs"]
            if item["differs"]:
                item["differs_detail"] = differs_detail(ref[3], run, item["differs"])   # EV-042 CHG-207
            elif notes := condition_notes(ref[3], run):
                item["notes"] = notes   # EV-050 CHG-W2-03
            if (man.get("pack_env") or {}) != (ref[1].get("pack_env") or {}):
                item["warning"] = "Pack 환경이 다름(Python·설치 목록)"
            item["no_expect"] = no_expectation(run.get("graph"), ref[2], suite_cases)
            item["expect_removed"] = expect_removed(run.get("graph"), suite_cases)
        out.append(item)
    # EV-064 (PRES-CF-3, #18): the reference is the anchor's row — often the newer version, the one with the new node — so the
    # reference row also names its nodes the compared row's graph does not have and the suite has no expectation for
    top = next((x for x in out if x.get("reference")), None)
    other = next((x for x in out if not x.get("reference") and x["same"]), None) or next((x for x in out if not x.get("reference")), None)
    if top is not None and other is not None:
        top["no_expect"] = no_expectation(by_uid[top["run_id"]].get("graph"), by_uid[other["run_id"]].get("graph"), suite_cases)
    return out

# ---------------------------------------------------------------- P09 details (EV-022 CHG-110, moved from the removed comparison.py)
# Case-by-case path differences, the scorecard as named columns and the same-condition check with the data bundle, for the
# '▸ 세부 사항' toggle of the comparison page (manual p.24). Pure functions over two stored Runs; nothing is stored.
TIMING = ("latency", "tokens")                      # differ on every Run: shown, never counted as a change


def condition(run):
    c = _condition(run.get("manifest"))
    c["자료 묶음"] = _bundle_key(run)
    return c


def _bundle_key(run):
    """The data bundle to compare (CHG-W2-02): the stored data_release_id, unless a vector store has a recipe — then the same
    rule as runs._data_release_id (every store of the execution config, its release id) with that store's release id replaced
    by its recipe key, so a chunk size · overlap change alone keeps the bundle. The stored value is never changed."""
    man = run.get("manifest") or {}
    keys = {k: _recipe_key(v) for k, v in (man.get("evidence_index_releases") or {}).items()}
    if not any(keys.values()):
        return run.get("data_release_id")
    stores = {k: None for k in ((man.get("execution_config") or {}).get("backends") or {})}
    for part in ("evidence_index_releases", "table_snapshots"):
        stores.update({k: (v or {}).get("release_id") for k, v in (man.get(part) or {}).items()})
    stores.update({k: list(rk) for k, rk in keys.items() if rk})
    return ptrace.data_release_id(stores)


def _chunks(recipe):
    size, over = recipe.get("chunk_size"), recipe.get("overlap")
    return f"{'섹션 그대로' if size is None else size} · {over or 0}"


def condition_notes(rb, ro):
    """CHG-W2-03: for two Runs under the same condition, the recipe stores whose chunk size · overlap differ —
    {'벡터 DB <store>': '청크 · 오버랩 다름(800 · 100 → 600 · 50)'}; {} when there is none."""
    a, b = ((r.get("manifest") or {}).get("evidence_index_releases") or {} for r in (rb, ro))
    out = {}
    for k in sorted(set(a) & set(b)):
        x, y = a[k] or {}, b[k] or {}
        if _recipe_key(x) and _recipe_key(x) == _recipe_key(y) and (x["index_recipe"].get("split") != y["index_recipe"].get("split")):   # D-492 덧 15
            cut = lambda r: f"{r.get('split')} · {_chunks(r)}"   # noqa: E731
            out[f"벡터 DB {k}"] = f"나누는 방식 · 청크 · 오버랩 다름({cut(x['index_recipe'])} → {cut(y['index_recipe'])})"
        elif _recipe_key(x) and _recipe_key(x) == _recipe_key(y) and _chunks(x["index_recipe"]) != _chunks(y["index_recipe"]):
            out[f"벡터 DB {k}"] = f"청크 · 오버랩 다름({_chunks(x['index_recipe'])} → {_chunks(y['index_recipe'])})"
    return out


def _threshold(run, model):
    """The threshold a Run's predict visits recorded for a model (EV-041 visit meta), or None."""
    names = {n["name"] for n in (run.get("graph") or {}).get("nodes") or [] if (n.get("params") or {}).get("model") == model}
    for c in run.get("cases") or []:
        for v in c.get("visits") or []:
            if v.get("node") in names and v.get("threshold") is not None:
                return v["threshold"]
    return None


def model_release_detail(rb, ro):
    """EV-042 CHG-207 (#21): which part of '모델 release' differs between two Runs — from the two Run manifests only (the names are
    the model_releases keys = predict params.model; release_id is not used). The condition itself is unchanged: packrt's
    manifest_sha256 hashes the whole manifest.json, threshold_config included, so a threshold-only release already differs."""
    a, b = ((r.get("manifest") or {}).get("model_releases") or {} for r in (rb, ro))
    if set(a) != set(b):
        return "모델 이름 다름"
    for k in sorted(a):
        x, y = a[k] or {}, b[k] or {}
        if x.get("artifact_sha256") != y.get("artifact_sha256"):
            return "모델 파일 다름"
    for k in sorted(a):
        x, y = a[k] or {}, b[k] or {}
        if x.get("threshold_config_sha256") != y.get("threshold_config_sha256"):
            ta, tb = _threshold(rb, k), _threshold(ro, k)
            return "임계값 다름" + (f" {float(ta):.2f} → {float(tb):.2f}" if ta is not None and tb is not None else "")
    return "manifest 다름"


def differs_detail(rb, ro, differs):
    """{item: wording} for the differing condition items that have one (only '모델 release' now); {} when there is none."""
    return {"모델 release": model_release_detail(rb, ro)} if "모델 release" in differs else {}


def branches(graph):
    """Parallel fan-outs from the graph definition (instructions 019d · 019d-1): for a node — START included, from the graph's
    'start' list — with plain parallel ('par') edges,
    each branch is the nodes reachable from its first node that not every branch reaches (the part before the join), so a
    branch may be a chain of nodes (a node inserted on the edge, manual p.13). A fan-out whose branches never meet again is
    not grouped; router branches are exclusive and never 'par'. Returns [[branch, …], …], each branch = [first node, …] in
    definition order. Both graph readers (Pack and LangGraph) mark plain fan-out edges as 'par'."""
    edges = [(e["source"], e["target"]) for e in (graph or {}).get("edges") or [] if e.get("kind") != "loop"]
    nxt = {}
    for s, t in edges:
        nxt.setdefault(s, []).append(t)
    heads = {}
    for e in (graph or {}).get("edges") or []:
        if e.get("kind") == "par":
            heads.setdefault(e["source"], []).append(e["target"])
    if len((graph or {}).get("start") or []) > 1:   # a fan-out from START is one too (task10-pack EV-019, answer 013-03 A)
        heads["START"] = list(graph["start"])

    def reach(n):
        seen, todo = [], [n]
        while todo:
            x = todo.pop(0)
            if x not in seen:
                seen.append(x)
                todo += nxt.get(x, [])
        return seen
    out = []
    for src, firsts in heads.items():
        if len(firsts) < 2:
            continue
        reached = [reach(f) for f in firsts]
        common = set(reached[0]).intersection(*map(set, reached[1:]))
        if not common:
            continue                                        # the branches never meet: not one parallel group
        grp = [br for br in ([n for n in r if n not in common and n != src] for r in reached) if br]   # a branch going straight to the join has no node
        if len(grp) >= 2:
            out.append(grp)
    return out


def path(case, groups=(), rank=None):
    """The nodes a case went through. With the graph's parallel fan-outs, a stretch of nodes that all belong to the branches of
    one fan-out keeps each branch's own order and puts the branches one after another (rank of their first node, else the
    definition order): branches run at the same time and interleave differently on every Run, which is not a path difference
    (019d · 019d-1). An order change inside a branch, a node more or less, or an order change outside the branches still
    counts. Only the graph decides what is parallel, never the observed order."""
    out = []
    for v in case.get("visits") or []:
        if not out or out[-1] != v["node"]:
            out.append(v["node"])
    member = {}
    for gi, grp in enumerate(groups):
        for bi, br in enumerate(grp):
            for n in br:
                member.setdefault(n, (gi, br[0]))
    if not member:
        return out
    rank = rank or {br[0]: k for grp in groups for k, br in enumerate(grp)}
    canon, i = [], 0
    while i < len(out):
        g = member.get(out[i])
        j = i + 1
        while g is not None and j < len(out) and (member.get(out[j]) or (None,))[0] == g[0]:
            j += 1
        seg = out[i:j]
        canon += sorted(seg, key=lambda n: (rank.get(member[n][1], len(rank)), member[n][1])) if j - i > 1 else seg   # stable: a branch keeps its order
        i = j
    return canon


def _final(case):
    return case.get("final") if case.get("final") is not None else (case.get("sim") or {}).get("final")


def case_rows(base, other, suite_cases=()):
    """Case-by-case differences of two Runs. Each Run's path reads its own graph (the release graph it ran, kept on the Run);
    siblings of one parallel fan-out share one order across both Runs (019d)."""
    rows, transitions, path_changed = [], {}, 0
    gb, gc = branches(base.get("graph")), branches(other.get("graph"))
    rank = {}
    for n in (br[0] for g in gb + gc for br in g):          # one order of branches for both Runs (by their first node)
        rank.setdefault(n, len(rank))
    for i, (b, c) in enumerate(zip(base["cases"], other["cases"])):
        bp, cp = path(b, gb, rank), path(c, gc, rank)
        only_b, only_c = [n for n in bp if n not in cp], [n for n in cp if n not in bp]
        final_differs = _final(b) != _final(c)
        changed = bp != cp or b["state"] != c["state"] or final_differs
        if bp != cp:
            path_changed += 1
            key = f"{'·'.join(only_b) or '—'} → {'·'.join(only_c) or '—'}" if only_b or only_c else "같은 node · 순서 바뀜"
            transitions[key] = transitions.get(key, 0) + 1
        rows.append({"index": i, "case_id": (suite_cases[i].get("id") if i < len(suite_cases) else None),
                     "base": {"state": b["state"], "path": bp, "final": _final(b)}, "other": {"state": c["state"], "path": cp, "final": _final(c)},
                     "changed": changed, "final_differs": final_differs, "path_diff": {"only_base": only_b, "only_other": only_c}})
        if b.get("fake_limits") or c.get("fake_limits"):   # EV-SUI-01 CHG-S06: a case with fake limits keeps its row even when unchanged
            rows[-1].update(fake_limits={"base": b.get("fake_limits") or [], "other": c.get("fake_limits") or []}, keep=True)
    summary = {"total": len(rows), "changed": sum(r["changed"] for r in rows), "path_changed": path_changed, "transitions": transitions}
    return rows, summary


def _cell(x):
    if not isinstance(x, dict):
        return x
    if "none" in x:
        return x["none"]
    if "ok" in x and "n" in x:
        return f"{x['ok']}/{x['n']}"
    if "v" in x:
        return round(x["v"], 4) if isinstance(x["v"], float) else x["v"]
    return x


def columns(sc, budgets=None):
    """The scorecard as named columns (never one number)."""
    sc = sc or {}
    out = {}
    for n, rules in (sc.get("mechanical_checks") or {}).items():
        if isinstance(rules, dict) and "none" not in rules:
            for r, x in rules.items():
                out[f"기계 검사 {n} · {r}"] = _cell(x)
        else:
            out[f"기계 검사 {n}"] = _cell(rules)
    fx = sc.get("fixture_agreement") or {}
    out["기대값 일치(최종)"] = _cell(fx.get("final"))
    out["기대값 일치(node)"] = _cell(fx.get("nodes"))
    for n, b in (sc.get("branch_ratio") or {}).items():
        if isinstance(b, dict) and "none" not in b:
            for t, x in b.items():
                out[f"분기 {n} → {t}"] = x.get("n")
        else:
            out[f"분기 {n}"] = _cell(b)
    for row in sc.get("nodes") or []:
        out[f"도달 {row['node']}"] = f"{row['reached']}/{row['cases']}"
    out["모델 검토자"] = _cell(sc.get("model_judge"))
    out["실패율"] = _cell(sc.get("failure_rate"))
    out["latency"] = _cell(sc.get("latency"))
    out["tokens"] = _cell(sc.get("tokens"))
    out.update(model_columns(sc))
    out.update(retrieval_columns(sc, budgets))
    return out


def budgets_of(sc):
    """D-492 덧 16 (Q-1 (가)): {node: B} — the reference Run's top-10 characters a case, used on both sides of a comparison."""
    from .suites import char_budget
    return {n: char_budget(m) for n, m in ((sc or {}).get("retrieval_metrics") or {}).items()}


def retrieval_columns(sc, budgets=None):
    """EV-043 CHG-210: the retriever metrics of a suite Run as named cells — '리트리버 <node> recall@10' · 'hit@10' · '사람 확인'.
    Empty for a Run without them (an older Run, or a suite without gold positions). D-492 덧 16: the same-character-budget recall on
    the reference Run's budget (`budgets`) and the two reference cells, where the Run has them."""
    from .suites import budget_recall
    out = {}
    for n, m in ((sc or {}).get("retrieval_metrics") or {}).items():
        out[f"리트리버 {n} recall@10"] = _cell(m.get("recall@10"))
        out[f"리트리버 {n} hit@10"] = _cell(m.get("hit@10"))
        out[f"리트리버 {n} 사람 확인"] = _cell(m.get("recall@10_human"))
        br = budget_recall(m, (budgets or {}).get(n))
        if br is not None:
            out[f"리트리버 {n} recall(같은 글자 예산)"] = _cell(br)
        if m.get("partial_half") is not None:
            out[f"리트리버 {n} 닿음 중 정답 절반 미만"] = _cell(m["partial_half"])
            out[f"리트리버 {n} 닿은 글 중 정답 절 밖"] = _cell(m.get("outside_section"))
    return out


MODEL_PREFIX = "모델 "


def model_columns(sc):
    """EV-042 CHG-206: the model metrics of a suite Run as named cells — '모델 <node> 정확도', '… 재현율 <class>' (classes with an
    expected case), '… 정상 오경보'. Empty for a Run without them (an older Run, or a graph without a predict node)."""
    out = {}
    for n, m in ((sc or {}).get("model_metrics") or {}).items():
        out[f"{MODEL_PREFIX}{n} 정확도"] = _cell(m.get("accuracy"))
        for k, x in (m.get("recall") or {}).items():
            if isinstance(x, dict) and "none" not in x:
                out[f"{MODEL_PREFIX}{n} 재현율 {k}"] = _cell(x)
        out[f"{MODEL_PREFIX}{n} 정상 오경보"] = _cell(m.get("normal_false_alarm"))
    return out


def scorecard_diff(base_sc, other_sc, models=True):
    """models=False (the two Runs' model releases differ — Q-1 (가), E3 critic B-1): no model cells at all. A model cell missing on
    the side of a Run without model metrics (a Run from before EV-042) shows '—' and is not a change; every other one-sided cell —
    a model cell of two new Runs included — stays a change."""
    bud = budgets_of(base_sc)   # D-492 덧 16: the base Run's budget on both sides
    a, b = columns(base_sc, bud), columns(other_sc, bud)
    mk = set(model_columns(base_sc)) | set(model_columns(other_sc))   # by name, not prefix: '모델 검토자' is an older cell
    if not models:
        a, b = ({k: v for k, v in x.items() if k not in mk} for x in (a, b))
    keys = list(a) + [k for k in b if k not in a]
    rk = set(retrieval_columns(base_sc, bud)) | set(retrieval_columns(other_sc, bud))   # EV-043: the same narrow rule for the retriever cells
    fam = lambda k: "model_metrics" if k in mk else "retrieval_metrics" if k in rk else None
    old = lambda side, k: not ((base_sc if side == "a" else other_sc) or {}).get(fam(k))   # that Run predates the family (EV-042 · EV-043)
    one_sided = lambda k: fam(k) is not None and ((k not in a and old("a", k)) or (k not in b and old("b", k)))   # slice critic M1-N2: only that case
    return {k: {"base": a.get(k), "other": b.get(k), "changed": k not in TIMING and not one_sided(k) and a.get(k) != b.get(k)} for k in keys}


def summary_line(label, summary):
    """'V1: 12 case 중 5개 경로 바뀜(draft → escalation_draft)' — the paths that moved, then any other changed cases."""
    t = summary["transitions"]
    line = f"{label}: {summary['total']} case 중 {summary['path_changed']}개 경로 바뀜"
    if t:
        line += "(" + ", ".join(k if v == summary["path_changed"] and len(t) == 1 else f"{k} {v}개" for k, v in t.items()) + ")"
    other = summary["changed"] - summary["path_changed"]
    if other:
        line += f", 경로는 같고 결과가 다른 case {other}개"
    return line
