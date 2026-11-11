"""D-477 (2) · 덧 6 ~ 9 (보고 073): the sealed test set — a problem set no wheel sees, used once right before a version's adoption.
Its folder (launch setting LLMOPS_SEALED_DIR) is read only after its MANIFEST's files add up to LLMOPS_SEALED_SHA (the sha the
orchestrator recorded — eval's rule: '<name>:<sha256>\\n' in the files' order); the new and the base version run its cases in memory
(main.ops.sealed_run — no Run record, no trace, no judge) and only counts leave this module: the (case, rule) cells the base got right,
broken (errors included), fixed, the evidence bound (autocand.evidence_row) and the groups. Every use is one wheel log line (counts only),
and those lines are the count: the set is used at most `limit` times (the MANIFEST's, 3), and a version made from one that failed on
it stops at once — both before anything runs ('봉인 시험지 갈아 끼울 때', eval swaps the set). The lead's ⑥ calls check() once, after
every criterion passed (D-477 덧 8)."""
import hashlib
import json
from pathlib import Path

from . import autocand, opsruns

ROW, LIMIT = "봉인 시험", 3
WHY_NONE, WHY_BROKEN, WHY_PACK, WHY_SWAP = "설정 없음", "봉인 깨짐", "이 Pack의 봉인 시험지 아님", "봉인 시험지 갈아 끼울 때"
WHY_ONCE = "이 revision은 이 봉인 시험지로 이미 시험함"   # D-477 덧 13 · D-486 덧 14: once per revision (a new release of it too)
PASSED, FAILED = "통과", "떨어짐"
LOG = "봉인 시험 벌 {set} · {k}/{limit}번째 · {result} · 후보 {rid} · 기준 맞은 칸 {n} · 망가짐 {broken} · 고침 {fixed}"
# D-486 덧 8 · 9: a release a person picked outside any wheel — its reason on the record, one sealed_use row (migration 0013) per use,
# and one call's spend cap (real mode): a Run crossing it is cut there, the use still counts and the row keeps nothing but that
CUT, CAP_USD = "상한 멈춤", 0.1
WHY_CAP = "이번 상한에 닿아 멈춤(사용 1번으로 셈)"
WHY_TABLE = "봉인 기록 표 없음(migration 0013 전)"
LOG_CUT = "봉인 시험 벌 {set} · {k}/{limit}번째 · {result} · 후보 {rid} · 상한 ${cap:g} · 쓴 돈 어림 ${usd:.4f}"
PICKED = " · 기준 {base} · 부른 이 {by} · 사람이 고른 후보 · {reason}"
# D-486 덧 13 (packrt-1 079): a Run whose cases all ended in an error before anything was scored (no key reached the Pack …) measured
# nothing — recorded apart (a wheel log line with another head, a sealed_use row '설정 실패'), never counted as a use
SETUP = "설정 실패"
WHY_SETUP = "설정 실패 · 결과 0(case가 모두 채점 전 오류 · 사용으로 안 셈)"
LOG_SETUP = "봉인 시험 설정 실패 · 결과 0 · 벌 {set} · {which} case 모두 채점 전 오류 · 후보 {rid} · revision {rev}"
REV = " · revision {rev}"   # 덧 14: the candidate's revision on every use line — a version once is once per revision
SPENT = " · 실측 ${m:.4f} · 어림 ${e:.4f}"
SCORE = " · 셈 {v}"   # EV-089 (D-491): the count a use was made with (autocand.SCORING); a line or row without it was the old strict v1
# D-486 덧 12 (packrt-1 078): the call's cap counts the Run's own token usage × the price list — a model outside the wheels' list
# (autocand.CHAT_USD_PER_1M, which also says which models a wheel may switch to) gets a price here, for this count only:
# gpt-6-luna at the c1a15 real runs' careful price (the gpt-5.6-luna list price; gpt-6-luna measured ≈ 0.10 · 0.50 per 1M).
# D-492 덧 22: the wheel's spend count (automain.visit_spend) reads the same count-only list — still no wheel behaviour
SEALED_EXTRA_USD_PER_1M = {"gpt-6-luna": (0.20, 1.20)}


def chat_price_sealed(model):
    """(input, output) USD per 1M tokens for the sealed count: the wheels' list first, then SEALED_EXTRA_USD_PER_1M; None for neither."""
    from .autocand import chat_price
    hits = [k for k in SEALED_EXTRA_USD_PER_1M if isinstance(model, str) and (model == k or model.startswith(k + "-2"))]
    return chat_price(model) or (SEALED_EXTRA_USD_PER_1M[max(hits, key=len)] if hits else None)


def _graph(rel):
    return (rel or {}).get("graph") or {}


def sealed_spend(rel, cases):
    """{measured, estimated, usd} of a sealed Run's cases so far: the wheels' own count (automain.visit_spend, D-492 ②) with the sealed
    price list — an llm visit with its provider usage and a priced model measured; an llm visit without either and a retriever visit
    (its query embedding, no usage recorded) estimated at the wheels' careful units."""
    from .automain import visit_spend
    _, measured, estimated = visit_spend(_graph(rel), cases, chat_price_sealed)
    return {"measured": measured, "estimated": estimated, "usd": measured + estimated}


def budget_cut(rel, budget, cancel):
    """The sealed Run's per-case hook (MemoryRuns.on_case): cancel(uid) once the cases so far cost more than `budget`."""
    return lambda uid, run: sealed_spend(rel, run.get("cases")).get("usd") > budget and cancel(uid)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_set(folder, want):
    """(True, manifest, None) when every file the MANIFEST names is there with its sha256, no other file is, and the files add up to
    `want`; else (False, None, WHY_BROKEN). Nothing of a file's content is read beyond its bytes' hash."""
    folder = Path(folder or "")
    try:
        man = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
        files = dict(man["files"])
        got = {name: _sha(folder / name) for name in files}
    except (OSError, ValueError, KeyError, TypeError):
        return False, None, WHY_BROKEN
    present = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()} - {"MANIFEST.json"}
    total = hashlib.sha256("".join(f"{n}:{got[n]}\n" for n in files).encode()).hexdigest()
    if got != files or present != set(files) or total != want:
        return False, None, WHY_BROKEN
    return True, man, None


def read_cases(folder, man):
    """The set's cases [{id, input, expected}] from its suite.yaml · dataset · expectations (in memory only)."""
    import yaml
    d = Path(folder) / man["suite"]
    doc = yaml.safe_load((d / "suite.yaml").read_text(encoding="utf-8")) or {}
    rows = lambda name: [json.loads(x) for x in (d / name).read_text(encoding="utf-8").splitlines() if x.strip()]
    exp = {r["case_id"]: {k: v for k, v in r.items() if k != "case_id"} for r in rows(doc.get("expectations") or "expectations.jsonl")}
    return [{**{k: v for k, v in r.items() if k != "case_id"}, "id": r["case_id"], "expected": exp.get(r["case_id"]) or {}}   # a two-step case keeps its 'then'
            for r in rows(doc.get("dataset") or "dataset.jsonl")]


def _cells(case, exp, on_fail=None):
    has = bool((exp or {}).get("final"))
    return autocand.rule_outcomes(case or {}, final=exp, on_fail=on_fail) if has else {"case": autocand.outcome(case or {}, on_fail=on_fail)}


def tally(ids, expected, base, new, groups, on_fail=(None, None)):
    """The (case, rule) cells: n = right on the base · broken = of those, wrong (or an error) on the new · fixed = wrong on the base and
    right on the new; one evidence item per input with a right cell ({group, ok}); groups = those inputs per group."""
    n = broken = fixed = 0
    items, per = [], {}
    for i, cid in enumerate(ids):
        b = _cells(base[i] if i < len(base) else None, expected.get(cid), on_fail[0])   # EV-089: (base, new) rewrite branches
        nc = new[i] if i < len(new) else None
        err = (nc or {}).get("state") in ("실패", "timeout") or nc is None
        nw = _cells(nc, expected.get(cid), on_fail[1])
        right = [r for r, v in b.items() if v]
        bad = [r for r in right if err or not nw.get(r)]
        n, broken = n + len(right), broken + len(bad)
        fixed += sum(1 for r, v in b.items() if not v and not err and nw.get(r))
        if right:
            items.append({"group": groups.get(cid, opsruns.GROUP_ALL), "ok": not bad})
            per[groups.get(cid, opsruns.GROUP_ALL)] = per.get(groups.get(cid, opsruns.GROUP_ALL), 0) + 1
    return {"n": n, "broken": broken, "fixed": fixed, "items": items, "groups": per}


def _rev(rel):
    return ((rel or {}).get("pack") or {}).get("revision") or ""


def _setup_failed(cases):
    """D-486 덧 13: every case ended in an error before anything was scored."""
    return bool(cases) and all((c or {}).get("state") in ("실패", "timeout") for c in cases)


def _uses(store, set12):
    """[(result, candidate release id, its revision)] of every use of this set: the wheel log lines of every Project and (덧 9) the
    sealed_use rows — a setup failure (덧 13) is neither. The revision is the line's or row's own (덧 14), else the release's if it is
    still there."""
    head = f"봉인 시험 벌 {set12} · "
    out = []
    for pid in store.project_ids():
        for w in store.wheels(pid):
            for lg in store.wheel_logs(w["wheel_id"]):
                t = lg.get("text") or ""
                if t.startswith(head):
                    rid = t.split(" · 후보 ", 1)[1].split(" · ", 1)[0] if " · 후보 " in t else None
                    rev = t.split(" · revision ", 1)[1].split(" · ", 1)[0] if " · revision " in t else _rev(store.release(rid) if rid else None)
                    out.append((PASSED if f" · {PASSED} · " in t else FAILED, rid, rev))
    if getattr(store, "sealed_ready", False):   # D-486 덧 9: the uses outside a wheel count on the same set
        out += [(r["result"], r["release_id"], r.get("revision") or _rev(store.release(r["release_id"]))) for r in store.sealed_uses(set12)
                if r["result"] != SETUP]
    return out


def _once(used, rel):
    """덧 14: this release, or another one of the same revision, has used the set."""
    return rel["id"] in {rid for _, rid, _ in used} or bool(_rev(rel)) and _rev(rel) in {rv for _, _, rv in used}


def _ancestors(store, rel):
    seen, cur = set(), rel
    while cur and ((cur.get("pack") or {}).get("from") or {}).get("rid") and cur["id"] not in seen:
        seen.add(cur["id"])
        cur = store.release(cur["pack"]["from"]["rid"])
        if cur:
            seen.add(cur["id"])
    return seen - {rel["id"]} if rel else set()


def _after_failed(store, rel, used):
    """A version made from one that failed on this set: a release on its pack.from chain, or (D-502 · eval 104) a failed candidate of
    the same Pack whose git HEAD is on this version's git line (packs.git_line) — a fix registered on its own from a commit built on the
    one that failed (luna 후보 2 8f019af on fa534ed). A release without a line (an archive, no git) is followed by pack.from only."""
    failed = {rid for res, rid, _ in used if res == FAILED and rid}
    if _ancestors(store, rel) & failed:
        return True
    pack = (rel or {}).get("pack") or {}
    line = set((pack.get("git") or {}).get("line") or ())
    if not line:
        return False
    for rid in failed - {rel["id"]}:
        f = (store.release(rid) or {}).get("pack") or {}
        if f.get("id") == pack.get("id") and (f.get("git") or {}).get("head") in line:
            return True
    return False


def _work_groups(main, w, spec):
    state = main.store.wheel_state(w["wheel_id"]) or {}
    sid = (state.get("suites") or {}).get("work")
    su = main.store.suite(sid) if sid else None
    return {opsruns.group_of(c.get("input"), spec) for c in (su or {}).get("cases") or []}


def check(main, w, row, by=None):
    """D-477 덧 8 · 9: once, after ⑥'s criteria all passed. {state: passed | failed | stopped | none, why, set, use, limit, n, fixed,
    broken, upper, groups, missing} — counts only. none (no setting) leaves the recommendation as it is (덧 9); failed · stopped hold.
    덧 13: `by` = a person called it on the candidate they picked (POST …/sealed-check) — a version once on this set, the alias on the log line."""
    st, cfg = main.store, main.settings
    folder, want = getattr(cfg, "sealed_dir", None), getattr(cfg, "sealed_sha", None)
    if not folder or not want:
        return {"state": "none", "why": WHY_NONE}
    ok, man, why = verify_set(folder, want)
    if not ok:
        return {"state": "stopped", "why": why}
    new, base = st.release(row.get("release_id")), st.release(w["base_release_id"])
    if not new or not base or man.get("pack_id") != (new.get("pack") or {}).get("id"):
        return {"state": "stopped", "why": WHY_PACK}
    set12, limit = want[:12], int(man.get("limit") or LIMIT)
    used = _uses(st, set12)
    if by and _once(used, new):
        return {"state": "stopped", "why": WHY_ONCE, "set": set12, "use": len(used), "limit": limit}
    if len(used) >= limit or _after_failed(st, new, used):
        return {"state": "stopped", "why": WHY_SWAP, "set": set12, "use": len(used), "limit": limit}
    cases = read_cases(folder, man)
    ids, exp = [c["id"] for c in cases], {c["id"]: c.get("expected") for c in cases}
    real = getattr(main, "real", False)
    if real and main.over_cap(main.case_unit(w, st.wheel_state(w["wheel_id"])) * 2 * len(cases)):   # two versions × the set, a paid step
        return {"state": "stopped", "why": main.cap_hold(), "set": set12, "use": len(used), "limit": limit}
    got_new = main.ops.sealed_run(new, cases)
    got_base = None if _setup_failed(got_new) else main.ops.sealed_run(base, cases)   # 덧 13: nothing more after a setup failure
    if real:   # the careful estimate of what the two Runs cost (automain.run_spend), as the wheel's other Runs are counted
        from .automain import run_spend
        spent = [run_spend({"graph": r.get("graph"), "cases": got}) for r, got in ((new, got_new), (base, got_base)) if got is not None]
        main.record(w, f"⑥ {ROW}", sum(x[0] for x in spent), sum(x[1] for x in spent), "추정")
    if got_base is None or _setup_failed(got_base):
        st.wheel_log(w["wheel_id"], 6, LOG_SETUP.format(set=set12, which="후보" if got_base is None else "기준", rid=new["id"], rev=_rev(new))
                     + (f" · 부른 이 {by}" if by else ""))
        return {"state": "stopped", "why": WHY_SETUP, "set": set12, "use": len(used), "limit": limit}
    spec = getattr(main.ops, "group_spec", lambda rid: None)(base["id"])
    t = tally(ids, exp, got_base, got_new, {c["id"]: opsruns.group_of(c.get("input"), spec) for c in cases},
              on_fail=(autocand.on_fail_of(base.get("graph")), autocand.on_fail_of(new.get("graph"))))
    erow = autocand.evidence_row(t["items"], t["fixed"])
    missing = sorted(g for g in _work_groups(main, w, spec) - set(t["groups"]) if g != opsruns.GROUP_ALL)
    k, result = len(used) + 1, PASSED if t["broken"] == 0 else FAILED
    st.wheel_log(w["wheel_id"], 6, LOG.format(set=set12, k=k, limit=limit, result=result, rid=new["id"], n=t["n"], broken=t["broken"], fixed=t["fixed"])
                 + REV.format(rev=_rev(new)) + SCORE.format(v=autocand.SCORING) + (f" · 부른 이 {by}" if by else ""))
    return {"state": "passed" if result == PASSED else "failed", "why": None, "set": set12, "use": k, "limit": limit, "n": t["n"],
            "fixed": t["fixed"], "broken": t["broken"], "upper": erow["bound"], "groups": t["groups"], "missing": missing, "scoring": autocand.SCORING}


class MemoryRuns:
    """The store a sealed Run is written to (runs.Executor's calls) — in memory only, gone with the call: no Run, case or event row and
    no evaluation_case. Releases are read from the real store (read only)."""
    def __init__(self, store, project):
        self.real, self.proj, self.runs = store, project, {}

    def create_run(self, pid, rid, meta, rows):
        import time
        import uuid
        uid = "sealed-" + uuid.uuid4().hex[:10]
        self.runs[uid] = {"uid": uid, "project_id": pid, "rel": rid, "status": "실행 중", "created_at": time.time(), "finished_at": None, **meta,
                          "label": "봉인", "cases": [json.loads(json.dumps(r)) for r in rows], "verdicts": []}
        return uid

    def run(self, uid):
        return json.loads(json.dumps(self.runs[uid], default=str))

    on_case = None   # D-486 덧 9: called after every case with (uid, the Run) — the app's sealed_run cuts a Run at its budget there

    def update_case(self, uid, idx, data):
        self.runs[uid]["cases"][idx] = json.loads(json.dumps(data, default=str))
        if self.on_case:
            self.on_case(uid, self.runs[uid])

    def set_run_meta(self, uid, key, value):
        self.runs[uid][key] = value

    def finish_run(self, uid, status, notice=None, summary=None, cases=()):
        self.runs[uid].update(status=status, notice=notice, summary=summary)
        for c in cases:
            self.update_case(uid, c["i"], c)

    def add_event(self, uid, event):
        return 0

    def set_last_run_code(self, rid, code_hash):
        pass

    def add_evaluation_case(self, *a, **k):
        pass

    def project(self, pid):
        return self.proj

    def suite(self, sid):
        return None   # no scorecard: the sealed cells are counted by tally, the expectations stay in this module

    def release(self, rid):
        return self.real.release(rid)


def check_picked(main, new, base, by, reason, cap=CAP_USD):
    """D-486 덧 8 · 9: the sealed test on a release a person picked outside any wheel, against `base` (the named one or the 운영
    version — the caller picks). The same set checks as check() — setting, sha, Pack, a version once, the limit, a failed one's
    descendants — over the wheel logs and the sealed_use rows together; then, in real mode, the server and daily caps for this call's
    cap and each Run under what is left of it (`sealed_run(rel, cases, budget=…)` cuts a Run at it). A cut Run: the use counts (a row
    '상한 멈춤' with the spend), nothing else. Else one row with the counts; the result as check() gives it, plus 'record' (the row's words)."""
    st, cfg = main.store, main.settings
    folder, want = getattr(cfg, "sealed_dir", None), getattr(cfg, "sealed_sha", None)
    if not folder or not want:
        return {"state": "none", "why": WHY_NONE}
    ok, man, why = verify_set(folder, want)
    if not ok:
        return {"state": "stopped", "why": why}
    if man.get("pack_id") != (new.get("pack") or {}).get("id"):
        return {"state": "stopped", "why": WHY_PACK}
    set12, limit = want[:12], int(man.get("limit") or LIMIT)
    used = _uses(st, set12)
    if _once(used, new):
        return {"state": "stopped", "why": WHY_ONCE, "set": set12, "use": len(used), "limit": limit}
    if len(used) >= limit or _after_failed(st, new, used):
        return {"state": "stopped", "why": WHY_SWAP, "set": set12, "use": len(used), "limit": limit}
    if not st.sealed_ready:
        return {"state": "stopped", "why": WHY_TABLE, "set": set12, "use": len(used), "limit": limit}
    real = getattr(main, "real", False)
    if real and main.over_cap(cap):   # the most this call may spend, against the server and daily caps first
        return {"state": "stopped", "why": main.cap_hold(), "set": set12, "use": len(used), "limit": limit}
    cases = read_cases(folder, man)
    ids, exp = [c["id"] for c in cases], {c["id"]: c.get("expected") for c in cases}
    usd = m_usd = e_usd = 0.0
    got, setup = {}, None
    for rel in (new, base):
        left = cap - usd
        if real and left <= 0:
            got[rel["id"]] = None
            break
        got[rel["id"]] = main.ops.sealed_run(rel, cases, **({"budget": left} if real else {}))
        if real:   # D-486 덧 12: the Run's own tokens × prices, the careful unit only where no usage came back
            sp = sealed_spend(rel, got[rel["id"]])
            usd, m_usd, e_usd = usd + sp["usd"], m_usd + sp["measured"], e_usd + sp["estimated"]
        if _setup_failed(got[rel["id"]]):   # 덧 13: measured nothing — not a use, nothing more run
            setup = "후보" if rel is new else "기준"
            break
        if any(c.get("state") == "실행 안 됨" for c in got[rel["id"]]):
            got[rel["id"]] = None
            break
    k = len(used) + 1
    tail = SPENT.format(m=m_usd, e=e_usd) + PICKED.format(base=base.get("name") or base["id"], by=by, reason=reason)   # the reason last
    spent = {"usd": usd, "usd_measured": m_usd, "usd_estimated": e_usd, "revision": _rev(new)}
    if real:
        with main.spend_lock:
            main.spent_mem += usd
    if setup:
        text = LOG_SETUP.format(set=set12, which=setup, rid=new["id"], rev=_rev(new)) + tail
        st.add_sealed_use(set12=set12, project_id=new["project_id"], release_id=new["id"], base_release_id=base["id"], result=SETUP, called_by=by,
                          reason=reason, counts={}, line=text, **spent)
        return {"state": "stopped", "why": WHY_SETUP, "set": set12, "use": len(used), "limit": limit, "record": text}
    if got.get(new["id"]) is None or got.get(base["id"]) is None:
        text = LOG_CUT.format(set=set12, k=k, limit=limit, result=CUT, rid=new["id"], cap=cap, usd=usd) + REV.format(rev=_rev(new)) + tail
        st.add_sealed_use(set12=set12, project_id=new["project_id"], release_id=new["id"], base_release_id=base["id"], result=CUT, called_by=by,
                          reason=reason, counts={}, line=text, **spent)
        return {"state": "stopped", "why": WHY_CAP, "set": set12, "use": k, "limit": limit, "record": text}
    spec = getattr(main.ops, "group_spec", lambda rid: None)(base["id"])
    t = tally(ids, exp, got[base["id"]], got[new["id"]], {c["id"]: opsruns.group_of(c.get("input"), spec) for c in cases},
              on_fail=(autocand.on_fail_of(base.get("graph")), autocand.on_fail_of(new.get("graph"))))
    erow = autocand.evidence_row(t["items"], t["fixed"])
    result = PASSED if t["broken"] == 0 else FAILED
    text = (LOG.format(set=set12, k=k, limit=limit, result=result, rid=new["id"], n=t["n"], broken=t["broken"], fixed=t["fixed"]) + REV.format(rev=_rev(new))
            + SCORE.format(v=autocand.SCORING) + tail)
    counts = {"n": t["n"], "broken": t["broken"], "fixed": t["fixed"], "upper": erow["bound"], "groups": t["groups"], "scoring": autocand.SCORING}
    st.add_sealed_use(set12=set12, project_id=new["project_id"], release_id=new["id"], base_release_id=base["id"], result=result, called_by=by,
                      reason=reason, counts=counts, line=text, **spent)
    return {"state": "passed" if result == PASSED else "failed", "why": None, "set": set12, "use": k, "limit": limit, **counts,
            "missing": [], "record": text}


def line(d):
    """The ⑥ criteria row's words for the result of check()."""
    if d.get("state") == "none":
        return f"{ROW} — {WHY_NONE}"
    if d.get("state") == "stopped":
        return f"{ROW} 못 함 — {d.get('why')}"
    return (f"봉인 {d['set']} · {d['use']}/{d['limit']}번째 · 고침 {d['fixed']} · 망가짐 {d['broken']} · 위쪽 한계 {d['upper'] * 100:.1f}%"
            + "".join(f" · {g} 표본 없음" for g in d.get("missing") or [])
            + (SCORE.format(v=autocand.SCORING) if d.get("scoring") == autocand.SCORING else " · " + autocand.SCORING_WORDS["v1"]))   # EV-089: an old result is not counted again
