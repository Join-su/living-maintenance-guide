"""Version state of Agent Pack releases (EV-022 CHG-107 · 109 · 112; S-3 · S-6 · S-16 revised · S-17 · S-18 · S-19; manual p.2 ·
4 · 22 · 27 · 29 · 43). Nothing is stored for the state: it is computed from what is already recorded — the deployment rows,
the deploy-approval decisions with their rollout stage, and the person's verdicts on the release's Runs at its current
revision. LangGraph code releases have no version state (S-17). Pure functions over store rows; the rollout stage of a
deploy approval comes in as a function (the deploy session's status file, read by the caller — E-S-04).

  운영    the release that started running last (EV-SUI-01 CHG-S01, D-155): among the tagged deployments that have started
          running (status file `switched`; a row with no status file — an early bootstrap row such as V0's — counts when it
          carries a tag, S-16 revised) and the switched rollbacks, the one with the latest switch time; the same time for two
          versions, or a switch time that cannot be read, keeps the earlier answer and says why (the deploy side's restore-check)
  이전    a release that was 운영 before and no longer is (S-16)
  승인됨  the latest verdict at the current revision is adopt; a deploy approval in progress or failed keeps it here (S-6)
  거절    the latest verdict at the current revision is reject
  평가 중 otherwise (a confirmed version waiting for its evaluation and verdict, or a hold)
  편집 중 is not a release state: the Project's one open edit is shown next to the release it started from (S-1)."""
import re

from .compare import FINISHED, case_rows, condition

NAME_RE = re.compile(r"^V(\d+)$")
STAGES = ("requested", "applied", "tagged", "registered", "switched")
LOCK = {"운영": "운영(배포) 버전", "이전": "이전 배포 버전", "deploying": "배포 진행 중"}   # S-3 · S-6 wording


def next_name(releases):
    """S-19: the Project's largest V-number + 1 (V1 when no release is named V<n>). A rejected version keeps its number."""
    nums = [int(m.group(1)) for r in releases if (m := NAME_RE.match(r.get("name", "")))]
    return f"V{max(nums) + 1}" if nums else "V1"


AUTO_STAGE = "data_release_auto"   # EV-044 CHG-225: stage_of's answer for an R12 row — it changes the data, not the version


def release_mrs(rel):
    """EV-044: the packrt `mrs:` id of a release's execution-config models (None without models)."""
    from .deploying import model_line
    return model_line((((rel or {}).get("pack") or {}).get("bindings") or {}).get("models"))[0]


def release_drs(rel):
    """D-484 덧 4: the packrt `drs:` id of a release's execution-config stores (the ledger row's way, admin.bootstrap_values) — None
    without stores."""
    stores = ((((rel or {}).get("pack") or {}).get("bindings") or {}).get("stores")) or {}
    if not stores:
        return None
    from packrt.trace import data_release_id
    return data_release_id({k: (v or {}).get("release_id") for k, v in stores.items()})


def row_versions(releases, row):
    """EV-044 CHG-221: the versions a ledger row belongs to — the Agent Pack releases of its Pack id and revision; when the row
    carries a model release and some of them were registered with that model, only those. A row without a model (before EV-044,
    or a Pack without models) and a row whose model no version has keep the old answer: every version of the revision. D-484 덧 4:
    then, the same way, the ones registered with the row's data bundle (drs)."""
    same = [r for r in releases if r.get("pack") and r["pack"].get("revision") == row.get("pack_revision") and r["pack"].get("id") == row.get("pack_id")]
    m = row.get("model_release_id")
    mine = ([r for r in same if release_mrs(r) == m] or same) if m else same
    d = row.get("data_release_id")   # D-484 덧 4: then the data bundle — a version that differs only in its stores (a v3 vector DB)
    return ([r for r in mine if release_drs(r) == d] or mine) if d else mine


UNREADABLE = "운영 판단 불가(상태 파일 시각을 읽을 수 없음)"
SAME_TIME = "운영 판단 불가(같은 시각 운영 전환 둘)"


def operational(releases, deployments, stage_of=None, when_of=None, rollbacks=(), info=None, redeploys=()):
    """(operational release id or None, earlier operational release ids). deployments: the store rows, oldest first.
    stage_of(row) → the deploy status-file stage for a row made by a deploy approval, or None when the row has no status file.
    when_of(row) → the epoch time the row started running (switched updated_at; created_at for a row without status file), None
    when it cannot be read. rollbacks: switched rollbacks [{"revision", "pack_id", "at", "recorded"}] (EV-SUI-01). Without
    when_of and rollbacks the row order decides (the rule before EV-SUI-01, the same answer for rows whose switch order is the row
    order). A switch whose time cannot be read is left out (UNREADABLE); two versions at the same latest time: the one recorded
    last (ledger row created_at, rollback decision time) is left out until one version is at the top (SAME_TIME) — the answer
    stays what it was before that record (D-155 '같은 시각이면 그대로'). info (dict, optional) receives "order" (owners by switch
    time), "current_at", "current_kind" (deployment | rollback), "switch_at" (the current owner's own switch time) and "note"."""
    stage_of = stage_of or (lambda row: None)
    packs = [r for r in releases if r.get("pack")]

    def owner(revision, pack_id, mrs=None, drs=None):
        match = row_versions(packs, {"pack_revision": revision, "pack_id": pack_id, "model_release_id": mrs, "data_release_id": drs})
        return match[0]["id"] if match else None   # the first one, as before (EV-044: among the ones with the row's model)
    counted = []
    for row in deployments:
        if not row.get("pack_tag"):
            continue                                        # an untagged (dev) deployment never makes a state (S-16)
        stage = stage_of(row)
        if stage == AUTO_STAGE:
            continue                                        # EV-044 CHG-225: an R12 data change is not a switch of version
        if stage is None or stage == "switched":
            counted.append(row)
    owners = [o for o in (owner(row["pack_revision"], row["pack_id"], row.get("model_release_id"), row.get("data_release_id")) for row in counted) if o]
    old = (owners[-1], {rid for rid in owners if rid != owners[-1]}) if owners else (None, set())
    if when_of is None and not rollbacks and not redeploys:
        return old
    # (time, owner, kind, recorded): recorded = when LLMOps recorded the event (row created_at, rollback decision time)
    events = [(when_of(row) if when_of else None, o, "deployment", row.get("created_at", 0)) for row in counted if (o := owner(row["pack_revision"], row["pack_id"], row.get("model_release_id"), row.get("data_release_id")))]
    events += [(rb.get("at"), o, "rollback", rb.get("recorded", 0)) for rb in rollbacks if (o := owner(rb.get("revision"), rb.get("pack_id")))]
    # D-457 ③-8: a deploy approval switched on a key that already had a tagged row (the same revision deployed again — no ledger row
    # of its own) is its own version's switch: {"release", "at", "recorded"}
    ids = {r["id"] for r in packs}
    events += [(rd.get("at"), rd["release"], "deployment", rd.get("recorded", 0)) for rd in redeploys if rd.get("release") in ids]
    if not events:
        return None, set()
    note, unreadable = None, set()
    if any(e[0] is None for e in events):
        note = UNREADABLE
        unreadable = {e[1] for e in events if e[0] is None}   # it did run once: still an earlier version (not '배포 진행 중')
        events = [e for e in events if e[0] is not None]    # only the unreadable switch is left out
        if not events:
            if info is not None:
                info.update(note=note)
            return old
    events.sort(key=lambda e: (e[0], e[3]))
    while len({e[1] for e in events if e[0] == events[-1][0]}) > 1:   # two versions at the same latest time
        note = note or SAME_TIME
        tied = [e for e in events if e[0] == events[-1][0]]
        events.remove(max(tied, key=lambda e: e[3]))        # the one recorded last waits: the answer stays what it was before it
    last = events[-1]
    current = last[1]
    if info is not None:
        info.update(order=[e[1] for e in events], current_at=last[0], current_kind=last[2])
        if note:
            info.update(note=note)
    return current, ({e[1] for e in events} | unreadable) - {current}


def rollback_ok(prev_name, cur_name):
    """EV-068 (D-428 덧 2, D-140 narrowed): a rollback goes back to an earlier version — by number only when both names are V<n>
    (V1 → V0, never '다시 V1로'); with a hand-named version (C1-a15 v7) the ledger's previous operational version is earlier."""
    a, b = NAME_RE.match(prev_name or ""), NAME_RE.match(cur_name or "")
    return not (a and b) or int(a.group(1)) < int(b.group(1))


def moved_out(releases, views, rid):
    """D-652 12-e · D-687: the version that carries this measured version's change onto the running one (D-620), in progress or done; a failed
    rollout does not count. views: {release id: version view}."""
    return next((x for x in releases if (((x.get("pack") or {}).get("from") or {}).get("auto") or {}).get("rebased_from") == rid
                 and (views.get(x["id"]) or {}).get("rollout") and not views[x["id"]]["rollout"].get("failed")), None)


def previous_operational(info, op):
    """The version that ran before the current one (the latest other owner by switch time), or None."""
    order = [o for o in (info or {}).get("order") or [] if o != op]
    return order[-1] if order else None


def latest_verdict(rel, runs):
    """(verdict, Run) of the latest verdict among the release's Runs made at its current revision (older revisions are
    earlier records, S-2); (None, None) when there is none."""
    mine = [r for r in runs if r.get("rel") == rel["id"] and r.get("code_hash") == rel["code_hash"] and r.get("verdicts")]
    if not mine:
        return None, None
    last = max(mine, key=lambda r: (r["verdicts"][-1].get("at", ""), r.get("created_at", 0)))
    return last["verdicts"][-1].get("v"), last


def verdict_at_revision(rel, runs):
    return latest_verdict(rel, runs)[0]


def _evaluated(r):
    return r.get("status") in FINISHED and bool((r.get("manifest") or {}).get("evaluation"))


def judge_gate(rel, *, runs, releases, op):
    """S-7 · S-18 (manual p.26): a version made by confirming an edit gets adopt · reject only once it has a finished
    evaluation Run, at its current revision, under the same conditions (suite · models · index · tables · runtime · data
    bundle) as a finished evaluation Run of the version it started from or of the 운영 version. Other releases are not gated.
    Returns {"needed", "ok", "pairs": {run uid: {"rel", "name", "run", "label"}}, "reason"}; pairs name each same-condition
    evaluation with the Run it lines up with (the starting version first, then 운영)."""
    start = (rel.get("pack") or {}).get("from")
    if not start:
        return {"needed": False, "ok": True, "pairs": {}, "reason": None}
    names = {r["id"]: r for r in releases}
    against = [x for x in (start.get("rid"), op) if x and x != rel["id"] and x in names]
    # the starting version's Runs at the revision the edit started from (or its revision now), the 운영 version's at its revision
    ok_rev = {}
    ok_rev.setdefault(start.get("rid"), set()).update({start.get("revision"), (names.get(start.get("rid")) or {}).get("pack", {}).get("revision")})
    ok_rev.setdefault(op, set()).add((names.get(op) or {}).get("pack", {}).get("revision"))   # the same release as both: both revisions (R2-01)
    theirs = [(rid, r) for rid in dict.fromkeys(against) for r in sorted(runs, key=lambda r: r.get("created_at", 0), reverse=True)
              if r.get("rel") == rid and _evaluated(r) and (r.get("manifest") or {}).get("pack_revision") in ok_rev.get(rid, set())]
    pairs = {}
    for r in runs:
        if r.get("rel") != rel["id"] or r.get("code_hash") != rel["code_hash"] or not _evaluated(r):
            continue
        c = condition(r)
        hit = next(((rid, x) for rid, x in theirs if condition(x) == c), None)
        if hit:
            pairs[r["uid"]] = {"rel": hit[0], "name": names[hit[0]]["name"], "run": hit[1]["uid"], "label": hit[1].get("label"),
                               "path_changed": case_rows(hit[1], r)[1]["path_changed"] if "cases" in hit[1] and "cases" in r else 0}   # S-22: the same count as P09
    who = " · ".join(names[x]["name"] for x in dict.fromkeys(against)) or "출발 버전"
    reason = None if pairs else f"같은 조건 평가가 아직 없습니다 — {who}와 같은 평가 suite · 조건으로 실행한 뒤 adopt · reject를 남길 수 있습니다"
    return {"needed": True, "ok": bool(pairs), "pairs": pairs, "reason": reason}


def run_gate(gate, run_uid, labels=None):
    """S-18 per Run (review E-S-03-R1-02): on a gated version adopt · reject are given only on a same-condition evaluation
    Run, so what an adopt is tied to always has its evaluation and comparison. None when allowed, else the reason."""
    if not gate or not gate.get("needed") or run_uid in (gate.get("pairs") or {}):
        return None
    if not gate.get("ok"):
        return gate["reason"]
    named = ", ".join((labels or {}).get(u, u) for u in gate["pairs"])
    return f"이 Run은 같은 조건 평가가 아닙니다 — 같은 조건 평가 {named}에서 채택 · 버림을 남깁니다"


def recommendation(run, gate):
    """The recommendation a verdict is compared with (PRES-118 rule, plus S-22 — user decision 10-14, manual p.26): on a
    same-condition evaluation Run of a confirmed version whose paths moved in at least one case against the Run it pairs with,
    'hold — 경로 n개 변경 · 사람이 비교를 보고 판단'. Otherwise the stored recommendation (a suite Run: none — scorecard)."""
    rec = ((run.get("summary") or {}).get("rec") or {})
    pair = ((gate or {}).get("pairs") or {}).get(run.get("uid") or run.get("id"))
    if pair and pair.get("path_changed", 0) >= 1:
        return {"v": "hold", "rule": f"경로 {pair['path_changed']}개 변경 · 사람이 비교를 보고 판단"}
    return rec


def approval(rel, runs, gate):
    """What an adopt is tied to (manual p.27): the revision, the data bundle, the judged Run and the Run it was compared with."""
    v, run = latest_verdict(rel, runs)
    if v != "adopt":
        return None
    last = run["verdicts"][-1]
    pair = (gate.get("pairs") or {}).get(run.get("uid"))
    return {"revision": rel["pack"].get("revision"), "data_release": run.get("data_release_id"), "run": run.get("uid"), "label": run.get("label"),
            "compare": pair, "at": last.get("at"), "reason": last.get("reason"), "by": last.get("by")}


def state(rel, *, runs, op, earlier, rollout=None, releases=()):
    """The version view of one release (API contract v1 'version' field). rollout: None, or the release's latest deploy approval
    as {"stage": <status-file stage or None>, "failed": bool, "reason": str} (S-6). releases: the Project's releases, for the
    judge gate (S-18) and what an adopt is tied to (p.27)."""
    if not rel.get("pack"):
        return None
    view = {"revision": rel["pack"].get("revision"), "from": rel["pack"].get("from"), "detail": None, "lock_reason": None}
    if rel["id"] == op:
        st, view["lock_reason"] = "운영", LOCK["운영"]
    elif rel["id"] in earlier:
        st, view["lock_reason"] = "이전", LOCK["이전"]
    elif rollout is not None:
        st, view["lock_reason"] = "승인됨", LOCK["deploying"]
        view["detail"] = f"배포 실패({rollout.get('fail_reason') or rollout.get('reason') or '이유 없음'})" if rollout.get("failed") else "배포 진행 중"
    else:
        v = verdict_at_revision(rel, runs)
        st = {"adopt": "승인됨", "reject": "거절"}.get(v, "평가 중")
    gate = judge_gate(rel, runs=runs, releases=releases or [rel], op=op)
    view.update(state=st, badge=st, editable_current=view["lock_reason"] is None, judge=gate,
                approval=approval(rel, runs, gate) if st in ("승인됨", "운영", "이전") else None)
    return view
