"""EV-035 ML-3 (approved E3, answer 045-01 · CHG-182 · 183): ⑤ of the improvement wheel — the rows, their runs and the winner.

A row is one tried change (one hypothesis, or hypotheses of the same node with different knobs put together — Q-4 A); it is not a
version (D-121). Its runs go through the ordinary executor with a record store kept in memory (ShadowStore), so a row's run is never a
Run: no runs row, no Run list, no comparison, no state (Q-7 A · PRES-185). Only the winner becomes a version, by the same registration
a person's '새 버전 생성' uses, and its three runs are then written once as that version's evaluation Runs (no new execution)."""
import copy as _copy
import hashlib
import json
import re
import threading
import time
import uuid


class ShadowStore:
    """The Executor's record calls kept in memory (the real store answers project and suite)."""
    def __init__(self, store):
        self.real, self.runs, self.lock = store, {}, threading.Lock()
        self.suites = {}

    def create_run(self, pid, release_id, meta, cases):
        uid = "cand-" + uuid.uuid4().hex[:10]
        with self.lock:
            self.runs[uid] = {"uid": uid, "project_id": pid, "release_id": release_id, "status": "실행 중", "created_at": time.time(),
                              "finished_at": None, **_copy.deepcopy(meta), "cases": [dict(c) for c in cases]}
        return uid

    def run(self, uid):
        with self.lock:
            r = self.runs.get(uid)
            return _copy.deepcopy(r) if r else None

    def release(self, rid):   # EV-061 (A1-B1): a row run on a copy release finds its origin's key file through the real store
        return self.real.release(rid)

    def update_case(self, uid, idx, data):
        with self.lock:
            self.runs[uid]["cases"][idx] = _copy.deepcopy(data)

    def add_event(self, uid, event):
        return 0

    def set_run_meta(self, uid, key, value):
        with self.lock:
            self.runs[uid][key] = _copy.deepcopy(value)

    def finish_run(self, uid, status, notice=None, summary=None, cases=()):
        with self.lock:
            r = self.runs[uid]
            r.update(status=status, finished_at=time.time(), summary=_copy.deepcopy(summary))
            if notice:
                r["notice"] = notice
            for c in cases:
                r["cases"][c["i"]] = _copy.deepcopy(c)

    def set_last_run_code(self, rid, code_hash):
        pass

    def add_evaluation_case(self, *a):
        pass   # a row's run has no trace link (the executor writes no trace here — U-2, ledger L0)

    def drop(self, uid):
        """The row's run is kept in its row record now (auto_candidate): the in-memory copy goes (critic ML-3 note)."""
        with self.lock:
            self.runs.pop(uid, None)

    def project(self, pid):
        return self.real.project(pid)

    def suite(self, sid):
        return self.suites.get(sid) or self.real.suite(sid)

    def put_suite(self, su):
        """A suite as the row's run sees it (the cheap test: the predicted cases only, judge off)."""
        sid = "shadow-" + uuid.uuid4().hex[:8]
        self.suites[sid] = {**su, "id": sid}
        return sid


def combine(rows):
    """Hypotheses of one node with different knobs put together (the first of each knob); prompt lines go in one after another."""
    seen, out = set(), []
    for h in rows:
        if h["knob"] not in seen:
            seen.add(h["knob"])
            out.append(h)
    return out if len(out) >= 2 else None


def merge_pick(rows):
    """D-470 ⓒ: of an efficiency batch's passing rows in rank order, those changing no node an earlier picked one changes — the rows
    of the merged row; [] when fewer than two."""
    taken, out = set(), []
    for r in rows:
        nodes = {l.get("node") for l in r.get("lines") or [] if l.get("node")}
        if nodes and not nodes & taken:
            out.append(r)
            taken |= nodes
    return out if len(out) >= 2 else []


JUDGE_PASS = 0.5   # a node judge score below this is the case's judge failure (the same line the responsible-node search uses)


SCORING = "v2"   # EV-089 (D-491): the count a wheel result was made with — v2 the final answer, none on a record = v1 (strict)
SCORING_WORDS = {"v2": "셈 v2 · 최종 답 기준", "v1": "옛 엄격 셈 v1"}
STRICT_ROW = "엄격 셈 · 다시 씀(참고)"   # EV-089 (라): the old strict count and the rewrites beside the criteria (never one)


def scoring_word(x):
    """The words for the count a recommendation, a ⑤ column set or a sealed result was made with — an old one (no 'scoring') is v1."""
    return SCORING_WORDS["v2" if (x or {}).get("scoring") == SCORING else "v1"]


def on_fail_of(graph):
    """{validate node: its params.on_fail node} of a Run's or release's graph — the rewrite branches a failed check may be fixed on."""
    return {n.get("name") or n.get("id"): (n.get("params") or {})["on_fail"] for n in (graph or {}).get("nodes") or []
            if isinstance(n.get("params"), dict) and n["params"].get("on_fail")}


def check_failures(case, on_fail=None):
    """EV-089 (D-491): [(node, rule, fixed)] of a case's failed checks in path order. A validate check that failed is fixed when the path
    took its on_fail branch (on_fail = on_fail_of(graph): the next visit is that node — a rewrite) and the next check visit (the rewrite
    checked again — C1-a15's check_report_2) passes in full; when that one fails too, or no check follows, it stands (the original text
    is put back — wrong). A failure that took no on_fail branch (task10's evidence_check → a router → another check) stands, and so does
    every failure without a graph to read (the old count). A node's expect check (packrt expect.checks) is never checked again."""
    out, waiting, visits = [], [], case.get("visits") or []
    for i, v in enumerate(visits):
        chks = v.get("checks") or []
        if chks:
            ok = not any(c.get("passed") is False for c in chks)
            out += [(n, r, ok) for n, r in waiting]
            failed = [(v.get("node"), c.get("rule")) for c in chks if c.get("passed") is False]
            took = bool(failed) and i + 1 < len(visits) and (on_fail or {}).get(v.get("node")) == visits[i + 1].get("node")
            waiting = failed if took else []
            out += [] if took else [(n, r, False) for n, r in failed]
        out += [(v.get("node"), "expect." + str(c.get("check")), False) for c in v.get("expect_checks") or [] if c.get("passed") is False]
    return out + [(n, r, False) for n, r in waiting]


def open_failures(case, on_fail=None):
    """The failed checks that stand (check_failures without the fixed ones), in path order."""
    return [(n, r) for n, r, fixed in check_failures(case, on_fail) if not fixed]


def rewritten(case, on_fail=None):
    """A check failed and its re-check on the on_fail branch passed — a rewrite that worked (the reference cell's '다시 씀')."""
    return any(fixed for _, _, fixed in check_failures(case, on_fail))


def _strict_failed(case):
    return any(chk.get("passed") is False for v in case.get("visits") or [] for chk in (v.get("checks") or []) + (v.get("expect_checks") or []))


def outcome(case, judge=None, final=None, strict=False, on_fail=None):
    """A row case's '고침' / '유지' (CHG-182 ③): finished · no failed check stands (EV-089 · D-491: a failure its re-check on the same
    path passed does not count; strict = the old count, any failure — the reference cell only) · and, in real mode, no judge failure of
    the case (judge = its lowest node judge score, None when not judged — the fake judge is never a criterion, EV-030 Q-9). final (EV-079,
    D-463 덧 6 — an accuracy wheel only): its suite expected.final checks pass too ("step": 1 reads a two-step case's first output)."""
    if case.get("state") != "완료":
        return False
    if judge is not None and judge < JUDGE_PASS:
        return False
    if _strict_failed(case) if strict else open_failures(case, on_fail):
        return False
    if final:   # True: the case's own expected; a dict: the suite case's expected (a Run row keeps no suite expectations)
        from . import suites
        exp = final if isinstance(final, dict) else case.get("expected")
        for chk in ((exp or {}).get("final") or []) if isinstance(exp, dict) else []:
            src = ((case.get("steps") or [{}])[0] if chk.get("step") == 1 else case)
            ok, _, _ = suites.check(suites._final_value(src), chk)
            if ok is not True:
                return False
    return True


def rule_outcomes(case, judge=None, final=None, strict=False, on_fail=None):
    """D-463 덧 28: an accuracy case's outcome per final-check rule {rule: bool} — the agreed unit (eval · 덧 9: each (case, rule) apart);
    the case's own state · node checks · judge count against every rule; no final checks (or the switch off) is one rule 'case'."""
    base = outcome(case, judge, strict=strict, on_fail=on_fail)
    exp = final if isinstance(final, dict) else (case.get("expected") if final else None)
    checks = ((exp or {}).get("final") or []) if isinstance(exp, dict) else []
    if not checks:
        return {"case": base}
    from . import suites
    out = {}
    for chk in checks:
        src = ((case.get("steps") or [{}])[0] or {}) if chk.get("step") == 1 else case
        ok = base and suites.check(suites._final_value(src), chk)[0] is True
        r = str(chk.get("rule") or "final")
        out[r] = out.get(r, True) and ok
    return out


def case_judges(run):
    """{case index: lowest node judge score} from the Run's scorecard (suites.scorecard keeps it since ML-3 r2)."""
    mj = ((run.get("summary") or {}).get("scorecard") or {}).get("model_judge") or {}
    return {int(k): v for k, v in (mj.get("cases") or {}).items()}


def summarize(run, ids, sets, base_ok, real=False, final=None):
    """The row's columns over the work suite: fixed / fix cases, kept / keep cases (broken), errors (flaky cases left out), tokens,
    latency, judge mean. In real mode a case's judge failure counts against it (CHG-182 ③)."""
    js = case_judges(run) if real else {}
    cases, of = run.get("cases") or [], on_fail_of(run.get("graph"))   # EV-089: the rewrite branches of the Run's graph
    # final (EV-079, an accuracy wheel): True = each case's own expected, {case id: expected} = the suite's expectations by case
    by = {cid: outcome(c, js.get(c.get("i", k)), final.get(cid) or None if isinstance(final, dict) else final, on_fail=of) for k, (cid, c) in enumerate(zip(ids, cases))}
    fix = [c for c in ids if sets.get(c) == "fix"]
    keep = [c for c in ids if sets.get(c) == "keep"]
    fixed = [c for c in fix if by.get(c)]
    broken = [c for c in keep if base_ok.get(c) and not by.get(c)]
    errors = sum(1 for cid, c in zip(ids, cases) if c.get("state") in ("실패", "timeout") and sets.get(cid) != "flaky")
    tokens = sum((c.get("tokens") or 0) for c in cases)
    ms = [c.get("ms") or 0 for c in cases if c.get("state") == "완료"]
    mj = ((run.get("summary") or {}).get("scorecard") or {}).get("model_judge") or {}
    out = {"fixed": len(fixed), "fix": len(fix), "kept": len(keep) - len(broken), "keep": len(keep), "broken": broken, "errors": errors,
           "tokens": tokens, "latency": (sum(ms) / len(ms) / 1000) if ms else None, "judge": mj.get("v"), "by_case": by, "scoring": SCORING}
    # EV-089 (라): the old strict count (cells right) and the cases a re-check fixed — a reference cell, never a criterion
    strict = [rule_outcomes(c, js.get(c.get("i", k)), final.get(cid) or None if isinstance(final, dict) else final, strict=True)
              for k, (cid, c) in enumerate(zip(ids, cases))]
    out.update(strict={"right": sum(sum(map(bool, x.values())) for x in strict), "cells": sum(len(x) for x in strict)},
               rewritten=sum(1 for c in cases if rewritten(c, of)))
    if final:   # D-463 덧 28: the accuracy wheel's (case, rule) outcomes; with a base read the same way the columns count pairs
        by_rule = {cid: rule_outcomes(c, js.get(c.get("i", k)), final.get(cid) or None if isinstance(final, dict) else final, on_fail=of) for k, (cid, c) in enumerate(zip(ids, cases))}
        out["by_rule"] = by_rule
        if base_ok and all(isinstance(v, dict) for v in base_ok.values()):
            now = lambda cid, r: (by_rule.get(cid) or {}).get(r)
            fix_p = [(cid, r) for cid in fix for r, ok in (base_ok.get(cid) or {}).items() if not ok]
            keep_p = [(cid, r) for cid in keep for r, ok in (base_ok.get(cid) or {}).items() if ok]
            err = {cid for cid, c in zip(ids, cases) if c.get("state") in ("실패", "timeout")}   # D-463 덧 29: a run error is an error, not broken
            broken_p = [(cid, r) for cid in fix + keep if cid not in err for r, ok in (base_ok.get(cid) or {}).items() if ok and not now(cid, r)]
            out.update(fixed=sum(1 for cid, r in fix_p if now(cid, r)), fix=len(fix_p), keep=len(keep_p),
                       kept=len(keep_p) - sum(1 for cid, _ in broken_p if cid in keep), broken=[f"{cid}·{r}" for cid, r in broken_p])
    return out


def ref_text(col):
    """A ⑤ row's reference cell: '엄격 right / cells · 다시 씀 n'; a row measured before EV-089 reads '옛 엄격 셈 v1' (its numbers are strict)."""
    if (col or {}).get("scoring") != SCORING:
        return SCORING_WORDS["v1"]
    st = col.get("strict") or {}
    return f"엄격 {st.get('right', 0)} / {st.get('cells', 0)} · 다시 씀 {col.get('rewritten', 0)}"


def compact(run, ids=None):
    """A row run's case record without the whole run (CHG-188): per case its path, checks, state, tokens, ms and output sha; with the
    suite's case ids, an op case (a 운영 Run put up, 'op-…') keeps its final value too (D-502 (3) · packrt-1 091: a row run lives in
    memory only, so after the wheel this is where its op cases' outputs can still be read)."""
    out = []
    ops = {i for i, k in enumerate(ids or []) if str(k).startswith("op-")} if ids and len(ids) == len(run.get("cases") or []) else set()
    for n, c in enumerate(run.get("cases") or []):
        visits = c.get("visits") or []
        final = visits[-1].get("output") if visits else None
        out.append({"i": c.get("i"), "state": c.get("state"), "tokens": c.get("tokens"), "ms": c.get("ms"), "path": [v.get("node") for v in visits],
                    "checks": [[v.get("node"), chk.get("rule"), chk.get("passed")] for v in visits for chk in (v.get("checks") or [])],
                    "output_sha": hashlib.sha256(json.dumps(final, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16],
                    **({"error": error_code(c["error"])} if c.get("error") else {}), **({"final": c.get("final")} if n in ops else {})})
    return {"cases": out}


def error_code(err):
    """A case error as node · kind · HTTP status only (S-4, D-438 덧 7) — the text is never kept (it may carry provider words)."""
    text = str((err or {}).get("summary") or "")
    kind = re.search(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Refused))\b", text)
    http = re.search(r"\b([45]\d\d)\b", text)
    return {"node": (err or {}).get("node"), "kind": kind.group(1) if kind else None, "http": http.group(1) if http else None}


def error_tag(run):
    """The most common case error of a Run as 'node · kind status' (the ⑤ table's reason), or None."""
    tags = [error_code(c["error"]) for c in run.get("cases") or [] if c.get("error")]
    if not tags:
        return None
    t = max(({json.dumps(x, sort_keys=True): x for x in tags}).values(), key=lambda x: sum(1 for y in tags if y == x))
    return " · ".join(p for p in (t["node"], " ".join(q for q in (t["kind"], t["http"]) if q)) if p)


# ---------------------------------------------------------------- ⑥ the grading agent (CHG-184, B-REC; ML-4)
def _rules_of(exp):
    """The rule names of a suite case's final checks, in order ('final' for a check without one); [] when it has none."""
    out = []
    for chk in ((exp or {}).get("final") or []) if isinstance(exp, dict) else []:
        r = str(chk.get("rule") or "final")
        if r not in out:
            out.append(r)
    return out


def hidden_finals(suite_cases):
    """{case id: suite expected} for the hidden cases that have final checks (D-476 덧 5) — the rest are read case by case."""
    return {c["id"]: c["expected"] for c in suite_cases if _rules_of(c.get("expected"))}


def hidden_basis(suite_cases, base_cases, case_ids, graph=None):
    """What each hidden case is held against (CHG-184 · D-476 덧 5): one put up from an operational Run (op-) was failing — False;
    a suite case has its outcome in the base version's Run of the base suite — {rule: bool} when it has final checks, else one bool;
    None without one."""
    seen = {}
    for c, x in zip(suite_cases, base_cases or []):
        exp = c.get("expected")
        seen[c["id"]] = rule_outcomes(x, final=exp, on_fail=on_fail_of(graph)) if _rules_of(exp) else outcome(x, final=exp or None, on_fail=on_fail_of(graph))
    return {c: False if str(c).startswith("op-") else seen.get(c) for c in case_ids}


def hidden_result(runs, basis, finals=None):
    """The hidden suite (new version only, D-122) over its three Runs, read by (case, rule) like the work suite (D-476 덧 5): a case
    with suite final checks (finals = {case id: expected}) has one cell per rule (rule_outcomes), any other case one cell 'case'
    (its outcome, as before). A cell passes when it passes in most of the Runs. basis = {case id: bool | None | {rule: bool | None}} —
    the result it had on the base version. Worse = a cell that passed there and no longer does, or every cell of a case that ends in
    an error ('C · rule', a one-cell case by its id). Returns None when there is no hidden suite or no finished Run."""
    runs = [r for r in runs or [] if r]
    if not runs:
        return None
    finals, ids = finals or {}, list(basis)
    votes = {(c, r): 0 for c in ids for r in (_rules_of(finals.get(c)) or ["case"])}
    errors = set()
    for run in runs:
        for cid, c in zip(ids, run.get("cases") or []):
            of = on_fail_of(run.get("graph"))
            outs = rule_outcomes(c, final=finals[cid], on_fail=of) if _rules_of(finals.get(cid)) else {"case": outcome(c, on_fail=of)}
            for r, ok in outs.items():
                votes[(cid, r)] = votes.get((cid, r), 0) + bool(ok)
            if c.get("state") in ("실패", "timeout"):
                errors.add(cid)
    passed = {x for x in votes if votes[x] * 2 > len(runs)}
    held = lambda c, r: (basis.get(c) or {}).get(r) if isinstance(basis.get(c), dict) else basis.get(c)
    name = lambda c, r: c if r == "case" else f"{c} · {r}"
    worse = sorted({name(c, r) for (c, r) in votes if (held(c, r) is True and (c, r) not in passed) or c in errors})
    return {"n": len(votes), "passed": len(passed), "worse": worse, "runs": len(runs)}


FIELD_ROW = "현장 정답 고침"   # EV-065 CHG-266a (D-412 decision 6 · 7): the case a person checked must be fixed


def field_fixed(run, ids, field):
    """EV-065 CHG-266a: the trigger cases (case id → its field-truth expected) this Run fixes. A case is fixed when every node check
    holds — an evidence check: ≥ 1 gold in that node's top 10 (suites._case_recall); another check: suites.check on that node's
    output — and its final checks hold on the case's final output. A case that stopped, or never reached a node, is not fixed.
    D-506 덧 2 (packrt-1 103): a final check reads the case's final (suites._final_value; step 1 its first step) as rule_outcomes —
    the table column — does, not the last visit's output (a C1-a15 judge visit keeps its answer under 'result')."""
    from . import suites
    out = []
    for cid, c in zip(ids, run.get("cases") or []):
        exp = (field or {}).get(cid)
        if not exp:
            continue
        visits = c.get("visits") or []
        ok = c.get("state") == "완료" or not exp.get("final")
        for nid, chks in (exp.get("nodes") or {}).items():
            ok = ok and node_holds(visits, nid, chks)
        for chk in exp.get("final") or []:
            src = ((c.get("steps") or [{}])[0] or {}) if chk.get("step") == 1 else c
            ok = ok and suites.check(suites._final_value(src), chk)[0] is True
        if ok:
            out.append(cid)
    return out


def node_holds(visits, nid, chks):
    """One node's field checks on a case's visits (its last finished visit): evidence ≥ 1 gold in the top 10, else suites.check."""
    from . import suites
    v = next((x for x in reversed(visits) if x.get("node") == nid and x.get("status") == "done"), None)
    if v is None:
        return False
    for chk in chks:
        if "evidence" in chk:
            ret = v.get("retrievals") if isinstance(v.get("retrievals"), dict) else {}
            if suites._case_recall(ret.get("results") or [], chk["evidence"], ret.get("store"))[0] < 1:
                return False
        elif suites.check(v.get("output"), chk)[0] is not True:
            return False
    return True


def field_row(runs, ids, field):
    """⑥ (CHG-266a): the '현장 정답 고침' row over the new version's Runs — each Run fixes ≥ 1 of the trigger cases; None without them."""
    if not field:
        return None
    k = min(len(field_fixed(r, ids, field)) for r in runs) if runs else 0
    return {"name": FIELD_ROW, "ok": k >= 1, "text": f"{k} / {len(field)}"}


FIELD_SCORE_ROW = "현장 점수 · {node}"   # D-444 #11 (가) (D-412 decision 7 · D-444 덧 #9): per node, over every field case of the wheel
FIELD_SCORE_MIN = 5                      # under this many cases of a node the row is a reference only


def field_score_rows(base_runs, new_runs, ids, field_all):
    """D-444 #11 (가): ⑥ one row per node the field cases check — the cases whose node checks hold (the lowest over the Runs), the
    운영 version's → the new one's; under FIELD_SCORE_MIN cases a reference (ok None), from it a criterion (not below the 운영's)."""
    rows = []
    nodes = sorted({n for e in (field_all or {}).values() for n in (e.get("nodes") or {})})
    for node in nodes:
        cases = [(i, field_all[cid]["nodes"][node]) for i, cid in enumerate(ids) if node in ((field_all.get(cid) or {}).get("nodes") or {})]
        if not cases:
            continue
        score = lambda runs: min(sum(node_holds(((r.get("cases") or [])[i] if i < len(r.get("cases") or []) else {}).get("visits") or [], node, chks)
                                     for i, chks in cases) for r in runs) if runs else 0
        nb, nn, m = score(base_runs), score(new_runs), len(cases)
        name = FIELD_SCORE_ROW.format(node=node)
        if m < FIELD_SCORE_MIN:
            rows.append({"name": name, "ok": None, "text": f"{nb} → {nn} / {m} (참고 — {FIELD_SCORE_MIN}건 전)"})
        else:
            rows.append({"name": name, "ok": nn >= nb, "text": f"{nb} → {nn} / {m}"})
    return rows


# ---------------------------------------------------------------- D-477 (1) 운영 기록 다시 돌리기 · (3) 증거 세기
REPLAY_ROW, EVIDENCE_ROW = "운영 기록 다시 돌리기", "증거 세기"
REPLAY_SAME, REPLAY_BETTER, REPLAY_WORSE, REPLAY_SAME_TRUTH = "같음", "나아짐", "나빠짐", "같은 판정"
REPLAY_NO_TRUTH, REPLAY_FLAKY, REPLAY_NONE = "정답 없음", "흔들림", "비교 불가"
HOLD_WORSE = "보류 · 운영 기록 나빠짐 — 사람 판단"
HOLD_THIN = "보류 · 표본 부족 — 사람 판단 필요"
HOLD_SEALED = "보류 · 봉인 시험 — 사람 판단"   # D-477 덧 6 · 8: the sealed test failed or could not run
LIMIT_ALL, LIMIT_GROUP = 0.10, 0.30   # D-477 덧 5 (eval 056 (나)): the bound over every input · over the weakest group


SMALL = "표본 {n}건 · 최소 {need}건 · 위쪽 한계 {bound:.1f}%"   # D-478 덧 17: a small problem holds even with nothing broken — said with its numbers


def need_n(limit, conf=0.95):
    """D-478 덧 17: the fewest inputs whose bound with none broken is within `limit` (전체 10% → 29 · 묶음 30% → 9)."""
    n = 1
    while cp_upper(0, n, conf) > limit + 1e-9:
        n += 1
    return n


def cp_upper(m, n, conf=0.95):
    """The one-sided Clopper-Pearson upper bound of a rate with m of n (eval 056): the p where P(X ≤ m | n, p) = 1 − conf; 1.0 when n = 0."""
    if n <= 0:
        return 1.0
    if m >= n:
        return 1.0
    from math import comb
    cdf = lambda p: sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(m + 1))
    lo, hi = m / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > 1 - conf else (lo, mid)
    return hi


def _canon(v):
    from . import suites
    v = suites._final_value({"final": v}) if isinstance(v, str) else v
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def _last_outputs(case):
    out = {}
    for v in (case or {}).get("visits") or []:
        if v.get("status", "done") == "done":
            out[v.get("node")] = v.get("output")
    return out


def replay_case(trace, base_cases, new_case, kinds, gold=None):
    """D-477 (1): one operational input — trace = the 운영 output of that time {"final", "nodes": {node: output}} (None: no input),
    base_cases = the 운영 version's two Runs of it now, new_case = the new version's. 흔들림 when the 운영 side's three finals differ;
    else the final and every deterministic node (kind not llm) both ran are compared as they are (an llm node's text counts by the
    final only); a change with the Run's field truth (gold) is 나아짐 · 나빠짐 · 같은 판정 by the node checks (node_holds)."""
    from . import suites
    if trace is None or new_case is None:
        return {"cls": REPLAY_NONE, "node": None}
    finals = [_canon(trace.get("final"))] + [_canon(suites._final_value(c)) for c in base_cases or []]
    if len(set(finals)) > 1:
        return {"cls": REPLAY_FLAKY, "node": None}
    new_nodes, node = _last_outputs(new_case), None
    for n, o in (trace.get("nodes") or {}).items():
        if str(kinds.get(n, "")).split("@")[0] != "llm" and n in new_nodes and _canon(o) != _canon(new_nodes[n]):
            node = n
            break
    if node is None and _canon(trace.get("final")) != _canon(suites._final_value(new_case)):
        node = "(최종 출력)"
    if node is None:
        return {"cls": REPLAY_SAME, "node": None}
    if not gold:
        return {"cls": REPLAY_NO_TRUTH, "node": node}
    holds = lambda c: all(node_holds((c or {}).get("visits") or [], n, chks) for n, chks in (gold.get("nodes") or {}).items())
    old_ok, new_ok = holds((base_cases or [None])[0]), holds(new_case)
    return {"cls": REPLAY_BETTER if new_ok and not old_ok else REPLAY_WORSE if old_ok and not new_ok else REPLAY_SAME_TRUTH, "node": node}


def replay_row(results):
    """⑥ '운영 기록 다시 돌리기': the counts by class; any 나빠짐 fails it (D-477: not adopted — the person judges); none → no criterion."""
    if not results:
        return {"name": REPLAY_ROW, "ok": None, "text": "운영 Run 없음"}
    n = {k: sum(1 for r in results if r["cls"] == k) for k in (REPLAY_SAME, REPLAY_BETTER, REPLAY_WORSE, REPLAY_SAME_TRUTH, REPLAY_NO_TRUTH, REPLAY_FLAKY, REPLAY_NONE)}
    moved = n[REPLAY_BETTER] + n[REPLAY_WORSE] + n[REPLAY_SAME_TRUTH] + n[REPLAY_NO_TRUTH]
    text = (f"같음 {n[REPLAY_SAME]} · 바뀜 {moved}(나아짐 {n[REPLAY_BETTER]} · 나빠짐 {n[REPLAY_WORSE]} · 같은 판정 {n[REPLAY_SAME_TRUTH]} · "
            f"정답 없음 {n[REPLAY_NO_TRUTH]}) · 흔들림 {n[REPLAY_FLAKY]} · 비교 불가 {n[REPLAY_NONE]} / {len(results)}")
    hover = " · ".join(f"{r['cls']} {r.get('run', '')} {r.get('node') or ''}".strip() for r in results
                       if r["cls"] in (REPLAY_BETTER, REPLAY_WORSE, REPLAY_SAME_TRUTH, REPLAY_NO_TRUTH))
    return {"name": REPLAY_ROW, "ok": not n[REPLAY_WORSE], "text": text, "hover": hover}


def broken_case(broken, cid):
    """D-476 덧 22: is the case broken — in a case-level list ('K1') or by any of its rules ('K1·헛걸음 없음', summarize with final)."""
    return any(b == cid or b.startswith(f"{cid}·") for b in broken or [])


N_ALL, N_GROUP = need_n(LIMIT_ALL), need_n(LIMIT_GROUP)   # 29 · 9


def evidence_row(items, fixed):
    """⑥ '증거 세기' (D-477 (3) · eval 056): items = one per input {"group", "ok"} (ok False = broken); the bound over all and per
    group — 전체 ≤ LIMIT_ALL and the weakest group ≤ LIMIT_GROUP, else it fails (a hold — the person)."""
    n, m = len(items), sum(1 for x in items if not x["ok"])
    u = cp_upper(m, n)
    groups = {}
    for x in items:
        groups.setdefault(x["group"], []).append(x)
    ug = {g: cp_upper(sum(1 for x in xs if not x["ok"]), len(xs)) for g, xs in groups.items()}
    ok = u <= LIMIT_ALL + 1e-9 and all(v <= LIMIT_GROUP + 1e-9 for v in ug.values())
    per = " · ".join(f"{g} {len(groups[g])}건" for g in sorted(groups, key=lambda g: (-len(groups[g]), g)))
    small = SMALL.format(n=n, need=N_ALL, bound=cp_upper(0, n) * 100) if n < N_ALL else ""   # D-478 덧 17: said before anyone wonders
    return {"name": EVIDENCE_ROW, "ok": ok, "text": f"고침 {fixed} · 망가짐 {m} — 망가짐 비율 {u * 100:.1f}% 넘음은 배제(95%) · 기준 전체 {LIMIT_ALL * 100:.0f}% · 묶음 {LIMIT_GROUP * 100:.0f}%"
            + (f" · {per}" if per else "") + (f" · {small}" if small else ""), "bound": u, "groups": {g: round(v, 4) for g, v in ug.items()},
            "n": n, "need": N_ALL, **({"small": small} if small else {})}


def with_replay(rec, rrow, erow):
    """⑥ (D-477): the two rows go at the table's end; an adopt with a failed replay or thin evidence becomes a hold (the person, ⑦) —
    a reject stays a reject."""
    rec["criteria"] = list(rec.get("criteria") or []) + [x for x in (rrow, erow) if x]
    why = [HOLD_WORSE] if rrow and rrow["ok"] is False else []
    why += [HOLD_THIN] + ([erow["small"]] if erow.get("small") else []) if erow and erow["ok"] is False else []   # D-478 덧 17: the numbers in the box
    if why and rec.get("decision") == "adopt":
        rec["decision"] = "hold"
        rec["reasons"] = why + list(rec.get("reasons") or [])
    return rec


MINUTES_ROW = "현장 분(첫 걸음)"   # D-478 #8: a reference row beside the criteria (never one)


def first_step(case, case_input, expected, spec):
    """D-478 #8: one case's first step — {"chosen", "minutes", "waste"} (waste = its minutes when the chosen check's cause is not the
    case's true cause: the expected final check's max_key that is one of check_cause's causes); None when it cannot be counted."""
    from . import opsruns, suites
    if not spec:
        return None
    causes = set(spec["cause"].values())
    truth = next((c["max_key"] for c in (expected or {}).get("final") or [] if isinstance(c, dict) and c.get("max_key") in causes), None)
    final = suites._final_value(case or {})
    st, path = spec["chosen"].split(".", 1)
    chosen = opsruns._path(final, st, path) if isinstance(final, dict) else None
    ist, ipath = spec["minutes"].split(".", 1)
    table = opsruns._path(case_input, ist, ipath) if isinstance(case_input, dict) else None
    if truth is None or not isinstance(chosen, str) or chosen not in spec["cause"] or not isinstance(table, dict) or not isinstance(table.get(chosen), (int, float)):
        return None
    m = table[chosen]
    return {"chosen": chosen, "minutes": m, "waste": 0 if spec["cause"][chosen] == truth else m}


def minutes_row(base_run, new_run, ids, inputs, expects, spec):
    """D-478 #8: ⑥'s reference row — the cases both versions can be counted on: 첫 걸음 헛걸음 분 a case on average 운영 → 후보, the
    cases whose first step was wasted, and the cases whose wasted minutes went up (by name) or down. None without the keys or a case."""
    if not spec:
        return None
    at = lambda run, i: ((run or {}).get("cases") or [])[i] if i < len((run or {}).get("cases") or []) else None
    pairs = []
    for i, cid in enumerate(ids):
        b = first_step(at(base_run, i), inputs.get(cid), expects.get(cid), spec)
        n = first_step(at(new_run, i), inputs.get(cid), expects.get(cid), spec)
        if b and n:
            pairs.append((cid, b, n))
    if not pairs:
        return None
    k = len(pairs)
    avg = lambda side: sum(p[side]["waste"] for p in pairs) / k
    cnt = lambda side: sum(1 for p in pairs if p[side]["waste"])
    slower = [cid for cid, b, n in pairs if n["waste"] > b["waste"]]
    faster = sum(1 for cid, b, n in pairs if n["waste"] < b["waste"])
    text = (f"알람 하나당 첫 걸음 헛걸음 분 {avg(1):.1f} → {avg(2):.1f} · 첫 걸음 헛걸음 {cnt(1)} → {cnt(2)}건 / {k} · "
            f"분 늘어남 {len(slower)}건" + (f"({' · '.join(slower)})" if slower else "") + f" · 줄어듦 {faster}건")
    return {"name": MINUTES_ROW, "ok": None, "text": text, "slower": slower}


EFFICIENCY_ROW = "효율(보기만)"   # EV-065 CHG-267 (D-412): tokens · latency shown, never a criterion (EV-059's fake-mode-only rule widened)


def recommend(base_runs, new_runs, hidden, ids, sets, real, tolerance, spend, field=None, field_all=None, final=None):
    """The grading agent (Q-6 A): the criteria table over the two versions' evaluation Runs and their scorecards — nothing else.
    It never sees a hypothesis, a change line or the wheel's log (D-98), and makes no LLM call. base_runs = the 운영 version's two
    baseline Runs on the work suite, new_runs = the new version's Runs on the same suite (R-n), hidden = hidden_result(...) or None,
    spend(run) -> (calls, usd), final = an accuracy wheel's work-suite expectations by case: the columns count (case, rule) as ⑤ does
    (D-476 덧 22 — without it an order suite read '17 → 17' by case). Returns the recommendation stored on the row (a recommendation, never
    a verdict — PRES-182)."""
    base = [summarize(r, ids, sets, {}, real=real, final=final) for r in base_runs]
    base_ok = base[0].get("by_rule") or base[0]["by_case"]
    if final:   # by (case, rule): the base column counts the same pairs (its own failing pairs are the fix pairs — ⑤'s denominator)
        base = [summarize(r, ids, sets, base_ok, real=real, final=final) for r in base_runs]
    new = [summarize(r, ids, sets, base_ok, real=real, final=final) for r in new_runs]
    flaky = [c for c in ids if sets.get(c) == "flaky"]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    per_case = lambda runs, f: mean([f(r) / max(1, len(r.get("cases") or [])) for r in runs])

    def column(sums, runs, broken):
        lat = [s["latency"] for s in sums if s["latency"] is not None]
        judges = [s["judge"] for s in sums if s["judge"] is not None]
        return {"fixed": min(s["fixed"] for s in sums), "fix": sums[0]["fix"], "kept": sums[0]["keep"] - len(broken), "keep": sums[0]["keep"],
                "broken": broken, "tokens": per_case(runs, lambda r: sum((c.get("tokens") or 0) for c in r.get("cases") or [])),
                "latency": mean(lat), "calls": per_case(runs, lambda r: spend(r)[0]), "judge": min(judges) if judges else None,
                "errors": sum(s["errors"] for s in sums), "scoring": SCORING, "rewritten": max(s["rewritten"] for s in sums),
                "strict": {"right": min(s["strict"]["right"] for s in sums), "cells": sums[0]["strict"]["cells"]}}
    b = column(base, base_runs, [])
    n = column(new, new_runs, sorted({c for s in new for c in s["broken"]}))
    tol = 1 + tolerance
    crit = [{"name": "고칠 세트 고친 수 ↑", "ok": n["fixed"] > b["fixed"], "text": f"{b['fixed']} → {n['fixed']} / {n['fix']}"},
            {"name": "지킬 세트 망가짐 0", "ok": not n["broken"], "text": f"망가짐 {len(n['broken'])}"},
            {"name": "오류 0", "ok": not n["errors"], "text": f"오류 {n['errors']}"},
            {"name": EFFICIENCY_ROW, "ok": None,
             "text": f"토큰 {fmt_pct(n['tokens'], b['tokens'])} · latency {fmt_pct(n['latency'], b['latency'])} (허용 +{tolerance * 100:.0f}%)"
                     + (" · 허용 넘음" if (b["tokens"] and (n["tokens"] or 0) > b["tokens"] * tol) or (b["latency"] and n["latency"] and n["latency"] > b["latency"] * tol) else "")},
            {"name": STRICT_ROW, "ok": None, "text": f"엄격 셈 맞은 칸 {b['strict']['right']} → {n['strict']['right']} / {n['strict']['cells']}"
                                                     f" · 다시 쓴 case {b['rewritten']} → {n['rewritten']}"}]
    if real and b["judge"] is not None and n["judge"] is not None:   # the fake judge is never a criterion (EV-030 Q-9)
        crit.append({"name": "채점 평균", "ok": n["judge"] >= b["judge"], "text": f"{n['judge']:.3f} / 기준 최저 {b['judge']:.3f}"})
    fr = field_row(new_runs, ids, field)
    if fr:
        crit.insert(0, fr)
    crit[1 if fr else 0:1 if fr else 0] = field_score_rows(base_runs, new_runs, ids, field_all)   # D-444 #11 (가)
    if hidden is None:
        crit.append({"name": "숨긴 case", "ok": None, "text": "검증 없음"})
    else:
        crit.append({"name": "숨긴 case", "ok": not hidden["worse"], "text": f"{hidden['passed']} / {hidden['n']} 통과"
                     + (f" · 나빠짐 {len(hidden['worse'])}" if hidden["worse"] else "")})
    failed = [c for c in crit if c["ok"] is False]
    reasons = [f"고칠 {n['fixed']} / {n['fix']} 고침", f"지킬 망가짐 {len(n['broken'])}",
               f"토큰 {fmt_pct(n['tokens'], b['tokens'])}", f"latency {fmt_delta(n['latency'], b['latency'])}",
               "숨긴 case 검증 없음" if hidden is None else f"숨긴 case {hidden['passed']} / {hidden['n']}"]
    return {"decision": "reject" if failed else "adopt", "criteria": crit, "failed": [c["name"] for c in failed], "reasons": reasons,
            "hidden": hidden, "flaky": len(flaky), "columns": {"base": {k: v for k, v in b.items()}, "new": n}, "scoring": SCORING}


def fmt_pct(x, base):
    if x is None or not base:
        return "—"
    d = (x / base - 1) * 100
    return f"{'+' if d >= 0 else ''}{d:.0f}%"


def fmt_delta(x, base):
    if x is None or base is None:
        return "—"
    d = x - base
    return f"{'+' if d >= 0 else ''}{d:.1f}s"


# ---------------------------------------------------------------- EV-069 CHG-EF-5 · 6 (D-433): the efficiency wheel's rows and ⑥
# (input, output) USD per 1M tokens — the provider's public price list (E4 ledger names the source); a model outside it has no price
CHAT_USD_PER_1M = {"gpt-4.1-nano": (0.10, 0.40), "gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00)}
EFF_ROW, EFF_CONFIRM_ROW = "효율 나아짐", "확인 suite 나빠짐 0"
# D-463 덧 30 ② · 덧 31 (packrt-1 A-12: −1% recommended adopt): the least gain one chosen goal must reach, else the recommendation holds
EFF_GAIN, HOLD_SMALL = "이득 크기", "보류 · 이득 작음"
MIN_GAIN = {"cost": 0.10, "calls_llm": 0.10, "latency": 0.20}
MIN_LATENCY_S = 0.5
GOAL_KEYS = {"cost": "cost", "calls": "calls_llm", "latency": "latency"}
GOAL_NAMES = {"cost": "비용", "calls": "LLM 호출", "latency": "지연"}
GOAL_ORDER = ("cost", "calls", "latency")   # D-433 Q-EF-5


def chat_price(model):
    """(input, output) USD per 1M tokens of a model or its dated name (gpt-4.1-mini-2025-04-14 → gpt-4.1-mini); None outside the list."""
    hits = [k for k in CHAT_USD_PER_1M if isinstance(model, str) and (model == k or model.startswith(k + "-2"))]
    return CHAT_USD_PER_1M[max(hits, key=len)] if hits else None


def final_ok(case, suite_case):
    """EV-069 (E4 L0): the case's final expectations from its suite (suites.check, as the scorecard reads them) — True when none."""
    from . import suites
    exp = (suite_case or {}).get("expected") or {}
    if not exp.get("final"):
        return True
    if case.get("state") != "완료":
        return False
    first = suites._final_value((case.get("steps") or [{}])[0])   # EV-079: "step": 1 reads a two-step case's first output
    fv = suites._final_value(case)
    return all(suites.check(first if chk.get("step") == 1 else fv, chk)[0] for chk in exp["final"])


def eff_summarize(run, ids, sets, base_ok, suite_cases):
    """summarize without the judge, a case passing only when its final expectations pass too — the efficiency wheel's 'no kept case
    broken' reads the suite's expected values (contract CHG-EF-5; the accuracy wheel's outcome is unchanged)."""
    s = summarize(run, ids, sets, base_ok, real=False)
    by = {cid: ok and final_ok(c, sc) for (cid, ok), c, sc in zip(s["by_case"].items(), run.get("cases") or [], suite_cases or [])}
    keep = [c for c in ids if sets.get(c) == "keep" and base_ok.get(c)]   # 041 Q-S4-1: only a kept case the base passed can break
    broken = [c for c in keep if not by.get(c)]
    return {**s, "by_case": by, "broken": broken, "kept": len(keep) - len(broken), "keep": len(keep)}


def eff_base_ok(base_runs, ids, sets, suite_cases):
    """D-655: (a case the base passed in every one of its Runs — final expectations too, the base a kept case must stay at; the kept
    cases the base passed in some of its Runs but not all — its own shake, never counted as a row's break)."""
    bys = [eff_summarize(r, ids, sets, {}, suite_cases)["by_case"] for r in base_runs if r]
    ok = {c: bool(bys) and all(b.get(c) for b in bys) for c in ids}
    return ok, [c for c in ids if sets.get(c) == "keep" and not ok[c] and any(b.get(c) for b in bys)]


def noise_text(cases):
    """D-655 ③: the words the screen and the records show for the base's shake left out of the count."""
    return f"기준선 흔들림으로 뺀 case {len(cases)}건({', '.join(map(str, cases))})" if cases else ""


def eff_measures(run, prompt_chars=None):
    """Per finished case: LLM calls (the agent's llm node visits — no embedding, no judge), cost in USD (the visit's tokens_in ·
    tokens_out × its model's price; a visit with no tokens is estimated as (prompt file characters + output characters) ÷ 2, and
    the cost is marked '추정'), latency in s. CHG-EF-5 (A2-N7 · A3-N7)."""
    nodes = {n.get("name") or n.get("id"): n for n in ((run.get("graph") or {}).get("nodes") or [])}
    prompt_chars = prompt_chars or {}
    cases = [c for c in run.get("cases") or [] if c.get("state") == "완료"]
    calls = cost = 0.0
    estimated, ms = False, []
    for c in cases:
        ms.append((c.get("ms") or 0) / 1000)
        for v in c.get("visits") or []:
            n = nodes.get(v.get("node")) or {}
            if not str(n.get("kind") or "").startswith("llm"):
                continue
            calls += 1
            price = chat_price(v.get("model") or (n.get("params") or {}).get("model"))
            tin, tout = v.get("tokens_in"), v.get("tokens_out")
            if tin is None and tout is None:
                if v.get("tokens") is not None:   # a fake provider's usage: one total
                    tin, tout = v["tokens"], 0
                else:
                    estimated = True
                    out = len(json.dumps(v.get("output"), ensure_ascii=False, default=str)) if v.get("output") is not None else 0
                    tin, tout = prompt_chars.get(v.get("node"), 0) // 2, out // 2
            if price:
                cost += ((tin or 0) * price[0] + (tout or 0) * price[1]) / 1_000_000
    n = max(1, len(cases))
    return {"calls_llm": calls / n, "cost": cost / n, "cost_estimated": estimated, "latency": (sum(ms) / len(ms)) if ms else None}


def eff_by_node(runs):
    """D-462 A-1: per node over the base Runs' finished cases — visits, tokens in · out, cost (USD, price list; llm nodes only) and ms,
    each a mean per case (what the structure candidate agent reads)."""
    acc, n = {}, 0
    for r in runs or []:
        nodes = {x.get("name") or x.get("id"): x for x in (((r or {}).get("graph") or {}).get("nodes") or [])}
        for c in (r or {}).get("cases") or []:
            if c.get("state") != "완료":
                continue
            n += 1
            for v in c.get("visits") or []:
                a = acc.setdefault(v.get("node"), {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "ms": 0})
                tin, tout = v.get("tokens_in") or 0, v.get("tokens_out") or 0
                price = chat_price(v.get("model") or ((nodes.get(v.get("node")) or {}).get("params") or {}).get("model"))
                a["calls"] += 1
                a["tokens_in"] += tin
                a["tokens_out"] += tout
                a["cost"] += ((tin * price[0] + tout * price[1]) / 1_000_000) if price else 0.0
                a["ms"] += v.get("ms") or 0
    n = max(1, n)
    return {k: {"calls": round(a["calls"] / n, 3), "tokens_in": round(a["tokens_in"] / n), "tokens_out": round(a["tokens_out"] / n),
                "cost": round(a["cost"] / n, 8), "ms": round(a["ms"] / n)} for k, a in acc.items() if k}


def eff_mean(ms):
    keys = ("calls_llm", "cost", "latency")
    out = {k: (sum(m[k] for m in ms if m[k] is not None) / len([m for m in ms if m[k] is not None])) if any(m[k] is not None for m in ms) else None for k in keys}
    return {**out, "cost_estimated": any(m["cost_estimated"] for m in ms)}


def eff_noise(measures):
    """The spread of the base Runs per measure (max − min; 0 with one Run) — what a row must beat (S-4, D-438 덧 4)."""
    out = {}
    for k in ("cost", "calls_llm", "latency"):
        vals = [m.get(k) for m in measures if m.get(k) is not None]
        out[k] = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    return out


def eff_improved(new, base, goals, tolerance):
    """One chosen goal lower than the base by more than the base Runs' spread, none of the others above the base + tolerance (the
    '효율 나아짐' rule); latency alone never wins while LLM calls and cost stay the same (S-4, D-438 덧 4 · 7)."""
    noise = base.get("noise") or {}
    pairs = [(new.get(GOAL_KEYS[g]), base.get(GOAL_KEYS[g]), GOAL_KEYS[g]) for g in goals]
    pairs = [(x, y, k) for x, y, k in pairs if x is not None and y is not None]
    better = {k for x, y, k in pairs if y - x > noise.get(k, 0.0)}
    moved = any(new.get(k) is not None and base.get(k) is not None and abs(new[k] - base[k]) > noise.get(k, 0.0) for k in ("cost", "calls_llm"))
    if not better or (better <= {"latency"} and not moved):
        return False
    return not any(y and x > y * (1 + tolerance) for x, y, k in pairs)


def eff_gain_ok(new, base, goals):
    """One chosen goal down by at least its least gain (MIN_GAIN of the base; latency also by MIN_LATENCY_S seconds)."""
    for g in goals:
        k = GOAL_KEYS[g]
        x, y = new.get(k), base.get(k)
        if x is None or not y:
            continue
        if (y - x) / y >= MIN_GAIN[k] - 1e-9 and (k != "latency" or y - x >= MIN_LATENCY_S - 1e-9):
            return True
    return False


def eff_rank_key(measures, goals, n_ops):
    """1등 = the chosen goals in the Q-EF-5 order (cost → LLM calls → latency), lower first, then fewer change ops (D-121)."""
    return tuple(measures.get(GOAL_KEYS[g]) if measures.get(GOAL_KEYS[g]) is not None else float("inf") for g in GOAL_ORDER if g in goals) + (n_ops,)


def eff_text(new, base):
    return " · ".join([f"비용 {fmt_pct(new['cost'], base['cost'])}" + (" (추정)" if new.get("cost_estimated") or base.get("cost_estimated") else ""),
                       f"LLM 호출 {fmt_pct(new['calls_llm'], base['calls_llm'])}", f"지연 {fmt_delta(new['latency'], base['latency'])}"])


def confirm_counts(base_conf, new_conf, conf_cases):
    """The confirm suite case by case, one Run of each version (CHG-EF-6): passed on the new one, cases, worse (the 운영 one passed, the
    new one not) and better (D-659 ②: the other way)."""
    cids = list(range(len(base_conf.get("cases") or [])))
    cc = conf_cases or []
    bo, no = on_fail_of(base_conf.get("graph")), on_fail_of(new_conf.get("graph"))
    b = {i: outcome(c, on_fail=bo) and final_ok(c, cc[i] if i < len(cc) else None) for i, c in enumerate(base_conf.get("cases") or [])}
    n = {i: outcome(c, on_fail=no) and final_ok(c, cc[i] if i < len(cc) else None) for i, c in enumerate(new_conf.get("cases") or [])}
    return {"passed": sum(1 for i in cids if n.get(i)), "n": len(cids), "worse": [i for i in cids if b.get(i) and not n.get(i)],
            "better": [i for i in cids if n.get(i) and not b.get(i)]}


def eff_confirm_text(cnt):
    return f"{cnt['passed']} / {cnt['n']} 통과 · 나빠짐 {len(cnt['worse'])} · 나아짐 {len(cnt['better'])}"


def with_better(rec, base_conf, new_conf, conf_cases):
    """D-659 덧 (llmops-2 282): an efficiency recommendation kept before ② — its confirm criterion and reason read '나아짐 n', counted
    again from the two confirm Runs when the screen reads it. A copy; the kept record, the criterion and the decision are as they were."""
    crit = next((c for c in (rec or {}).get("criteria") or [] if c.get("name") == EFF_CONFIRM_ROW), None)
    if (rec or {}).get("kind") != "efficiency" or not crit or "나아짐" in str(crit.get("text")) or crit.get("ok") is None or not base_conf or not new_conf:
        return rec
    cnt = confirm_counts(base_conf, new_conf, conf_cases)
    extra = f" · 나아짐 {len(cnt['better'])}"
    reasons = [x + extra if str(x).startswith("확인 suite 나빠짐 ") and "나아짐" not in str(x) else x for x in rec.get("reasons") or []]
    return {**rec, "criteria": [{**c, "text": c["text"] + extra} if c is crit else c for c in rec["criteria"]], "reasons": reasons}


def recommend_eff(base_runs, new_runs, base_conf, new_conf, ids, sets, goals, tolerance, prompt_chars=None, work_cases=None, conf_cases=None, shake_runs=None):
    """⑥ of an efficiency wheel (CHG-EF-6): the work suite's case outcomes counted without the judge (A3-B2) — every case the base kept
    still passes, no case error, '효율 나아짐' over the chosen goals is a criterion here — and the confirm suite (test, one Run each of
    the 운영 and the new version) not worse case by case. No LLM call, no hypothesis or log read (PRES-EF-7)."""
    base_ok, shaky = eff_base_ok(list(base_runs) + list(shake_runs or []), ids, sets, work_cases)   # D-655: every base Run, its shake left out
    new = [eff_summarize(r, ids, sets, base_ok, work_cases) for r in new_runs]
    broken = sorted({c for s in new for c in s["broken"]})
    errors = sum(s["errors"] for s in new)
    bms = [eff_measures(r, prompt_chars) for r in base_runs]
    bm, nm = {**eff_mean(bms), "noise": eff_noise(bms)}, eff_mean([eff_measures(r, prompt_chars) for r in new_runs])
    crit = [{"name": "지킬 세트 망가짐 0", "ok": not broken, "text": " · ".join(x for x in (f"망가짐 {len(broken)}", noise_text(shaky)) if x)},
            {"name": "오류 0", "ok": not errors, "text": f"오류 {errors}"},
            {"name": EFF_ROW, "ok": eff_improved(nm, bm, goals, tolerance), "text": eff_text(nm, bm) + f" (허용 +{tolerance * 100:.0f}%)"},
            {"name": EFF_GAIN, "ok": eff_gain_ok(nm, bm, goals), "text": "최소 이득 — 비용 −10% · LLM 호출 −10% · 지연 −20%이면서 −0.5초 중 하나(고른 목표)"}]
    if base_conf is None or new_conf is None:
        crit.append({"name": EFF_CONFIRM_ROW, "ok": None, "text": "확인 검증 없음"})
        worse = better = None
    else:
        cnt = confirm_counts(base_conf, new_conf, conf_cases)
        worse, better = cnt["worse"], cnt["better"]   # D-659 ②: the better ones shown beside, never a criterion
        crit.append({"name": EFF_CONFIRM_ROW, "ok": not worse, "text": eff_confirm_text(cnt)})
    rw = lambda runs: max((sum(1 for c in r.get("cases") or [] if rewritten(c, on_fail_of(r.get("graph")))) for r in runs if r), default=0)   # EV-089 (라)
    crit.append({"name": STRICT_ROW, "ok": None, "text": f"다시 쓴 case {rw(base_runs)} → {rw(new_runs)} · 확인 suite {rw([base_conf])} → {rw([new_conf])}"})
    failed = [c for c in crit if c["ok"] is False and c["name"] != EFF_GAIN]   # a small gain holds, it does not reject
    small = not failed and not next(c["ok"] for c in crit if c["name"] == EFF_GAIN)
    reasons = ([HOLD_SMALL] if small else []) + [eff_text(nm, bm), f"지킬 망가짐 {len(broken)}"] + ([noise_text(shaky)] if shaky else []) + [f"오류 {errors}",
                                                  "확인 검증 없음" if worse is None else f"확인 suite 나빠짐 {len(worse)} · 나아짐 {len(better)}"]
    return {"decision": "reject" if failed else "hold" if small else "adopt", "criteria": crit, "failed": [c["name"] for c in failed], "reasons": reasons,
            "kind": "efficiency", "columns": {"base": bm, "new": nm}, "scoring": SCORING, "noise": shaky}


# ---------------------------------------------------------------- EV-043 CHG-211: a retriever row (contract-locked F)
CONFIRM_ROW = "확인 suite(test) 한 번"


# D-492 덧 16 (contract F 37): a row changing the manual DB's cut · chunk size · overlap is judged by the same-character-budget recall
BUDGET_KNOBS = ("split", "index.chunk_size", "index.overlap")


def budget_row(hyps):
    """True for a row whose hypotheses change the cut · chunk size · overlap (its candidates read pieces of another length)."""
    return any((h or {}).get("knob") in BUDGET_KNOBS for h in hyps or [])


def _rm(run, node):
    return ((((run or {}).get("summary") or {}).get("scorecard") or {}).get("retrieval_metrics") or {}).get(node) or {}


def ret_budget(runs, node):
    """D-492 덧 16 (Q-1 (가)): the budget B from the base Runs — their top-10 characters a case, the larger; None without them."""
    from .suites import char_budget
    bs = [b for b in (char_budget(_rm(r, node)) for r in runs) if b is not None]
    return max(bs) if bs else None


def ret_of(run, node, budget=None):
    """A Run's retriever measures from its scorecard (R-1): recall@10 and the person-checked recall@10 of the node, and expectation
    agreement final · nodes (gold positions never counted there); D-492 덧 16: the budget recall on `budget` (the base Runs' B).
    None where the scorecard has no value."""
    from .suites import budget_recall
    sc = ((run or {}).get("summary") or {}).get("scorecard") or {}
    rm = (sc.get("retrieval_metrics") or {}).get(node) or {}
    fa = sc.get("fixture_agreement") or {}
    part = lambda x: x if isinstance(x, dict) and "v" in x else None
    return {"recall": part(rm.get("recall@10")), "human": part(rm.get("recall@10_human")), "final": part(fa.get("final")), "nodes": part(fa.get("nodes")),
            "budget": part(budget_recall(rm, budget))}


def ret_base(runs, node, budget=None):
    """The baseline of a retriever row over the two baseline Runs: recall@10 · person-checked recall@10 · the budget recall the higher,
    agreement the lower."""
    ms = [ret_of(r, node, budget) for r in runs]
    pick = lambda k, f: f((m[k] for m in ms if m[k]), key=lambda x: x["v"], default=None)
    return {"recall": pick("recall", max), "human": pick("human", max), "final": pick("final", min), "nodes": pick("nodes", min),
            "budget": pick("budget", max), "budget_chars": budget}


BUDGET_UP = "예산 recall ↑"


def frac(x):
    return f"{x['ok']}/{x['n']}" if x else "—"


def ret_checks(ms, base, by_budget=False):
    """A retriever row's own conditions over its Runs, each Run on its own (⑤ and ⑥ alike): recall@10 above the baseline's higher,
    expectation agreement final · nodes not below the baseline's lower. A none baseline value skips its condition (ok None, '—')."""
    out = []
    first = ("budget", BUDGET_UP, True) if by_budget else ("recall", "recall@10 ↑", True)   # D-492 덧 16: a cut · chunk row by the budget
    for key, name, up in (first, ("final", "기대값 일치(최종)", False), ("nodes", "기대값 일치(node)", False)):
        b, vals = base.get(key), [m.get(key) for m in ms]
        if b is None:
            out.append({"name": name, "ok": None, "text": "—", "why": None})
            continue
        ok = bool(vals) and all(x is not None and (x["v"] > b["v"] if up else x["v"] >= b["v"]) for x in vals)
        worst = min(vals, key=lambda x: -1 if x is None else x["v"]) if vals else None
        why = f"{name[:-2]} 오르지 않음" if up else f"{name} 내려감 {b['v']:.2f} → {'—' if worst is None else format(worst['v'], '.2f')}"
        out.append({"name": name, "ok": ok, "text": f"{frac(b)} → {frac(worst)}", "why": None if ok else why})
    return out


def confirm_text(run, node):
    """⑥ · ⑦: the confirm suite's one Run — shown only, never a criterion."""
    if not run:
        return f"{CONFIRM_ROW} — 실행 기록 없음"
    m = ret_of(run, node)
    return f"{CONFIRM_ROW} — recall@10 {frac(m['recall'])} · 기대값 일치 {frac(m['final'])}"


def recommend_retriever(base_runs, new_runs, confirm_run, ids, sets, real, tolerance, spend, node, field=None, field_all=None, final=None,
                        by_budget=False):
    """⑥ for a retriever row: the same grading agent over the same Runs, with the retriever conditions in place of '고칠 세트 고친 수 ↑'
    (each new Run against the baseline), no hidden-case row, and the confirm suite (test) as a row that is shown only (ok None)."""
    rec = recommend(base_runs, new_runs, None, ids, sets, real, tolerance, spend, field=field, field_all=field_all, final=final)   # EV-065: the field row stays (in keep)
    budget = ret_budget(base_runs, node)   # D-492 덧 16: a cut · chunk row (by_budget) is judged by the budget recall on the base Runs' B
    base = ret_base(base_runs, node, budget=budget)
    news = [ret_of(r, node, budget) for r in new_runs]
    keep = [c for c in rec["criteria"] if c["name"] not in ("고칠 세트 고친 수 ↑", "숨긴 case")]
    rc = [{k: c[k] for k in ("name", "ok", "text")} for c in ret_checks(news, base, by_budget)]
    conf = confirm_text(confirm_run, node)
    crit = rc + keep + [{"name": CONFIRM_ROW, "ok": None, "text": conf[len(CONFIRM_ROW) + 3:] + "(보기만)"}]
    failed = [c for c in crit if c["ok"] is False]
    n = rec["columns"]["new"]
    worst = lambda k: min([m[k] for m in news], key=lambda x: -1 if x is None else x["v"]) if news else None
    reasons = [f"recall@10 {frac(base['recall'])} → {frac(worst('recall'))}", *([f"예산 recall {frac(base['budget'])} → {frac(worst('budget'))}"] if by_budget else []),
               f"기대값 일치(최종) {frac(base['final'])} → {frac(worst('final'))}",
               f"지킬 망가짐 {len(n['broken'])}", f"토큰 {fmt_pct(n['tokens'], rec['columns']['base']['tokens'])}",
               f"latency {fmt_delta(n['latency'], rec['columns']['base']['latency'])}", conf]
    rec["columns"]["base"]["retrieval"], rec["columns"]["new"]["retrieval"] = base, {k: worst(k) for k in ("recall", "human", "final", "nodes", "budget")}
    rec.update(decision="reject" if failed else "adopt", criteria=crit, failed=[c["name"] for c in failed], reasons=reasons,
               hidden=None, retriever={"node": node}, confirm={"text": conf})
    return rec
