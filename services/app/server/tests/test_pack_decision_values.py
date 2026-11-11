# ruff: noqa: F811, E501 — test_api · test_deploy_path · test_engine_rules의 fixture를 이름으로 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-011(D-476 · 001-65 (가)): 판정 매개변수(기본 = 지금) · 화면 숫자 = Pack 판정 출력.

V-02 기본 = 지금 · V-03 안전 규칙은 매개변수 밖 · V-13 셈 꼴 · V-09 요청 본문 · V-17 호출 요약 %(단위) ·
V-11 · V-12 · V-14 · V-15 · V-16 · V-16b(DB 길 — verify-db).
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from c1a15.api import flow, screen
from c1a15.deploy import runs
from c1a15.engine import rules, session
from fake_deploy import pack_answer
from test_api import TECH_PW, api, db, login, ok, to_instruct  # noqa: F401
from test_checkrecord import alarm
from test_deploy_path import count, deploy, reset_ops  # noqa: F401
from test_engine_rules import R5_SIMILAR, grades, k, r5_session  # noqa: F401

CL = ["CL-1", "CL-2", "CL-3", "CL-4"]
SKEW = session.Scoring(rule="rank_weighted", alpha=0.05) if hasattr(session, "Scoring") else None


def old_probabilities(candidates, similar):
    """지금(969ae59) 식 그대로: α 0.5 + Σ score → 합 100 반올림."""
    scores = {c: 0.5 for c in candidates}
    for r in similar:
        if r.cause in scores:
            scores[r.cause] += r.score
    return rules.round_to_100(scores)


# ---------------------------------------------------------------- V-13 셈 꼴(Preview 숫자)


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        ({}, [50, 28, 11, 11]),
        ({"rule": "near_only", "near_min": 0.9}, [61, 13, 13, 13]),
        ({"rule": "rank_weighted"}, [52, 22, 13, 13]),
        ({"manual": {"CL-2": 1.0}, "manual_weight": 0.3}, [47, 33, 10, 10]),
    ],
)
def test_v13_score_rules(kw, want):
    assert list(rules.round_to_100(rules.cause_scores(CL, R5_SIMILAR, **kw)).values()) == want


def test_v13_unknown_rule_is_error():
    with pytest.raises(ValueError):
        rules.cause_scores(CL, R5_SIMILAR, rule="max")


# ---------------------------------------------------------------- V-02 기본 = 지금

SIM = st.lists(
    st.builds(
        rules.Similar,
        st.from_regex(r"H-\d{4}", fullmatch=True),
        st.sampled_from([*CL, "PW-1", None]),
        st.floats(0, 1, allow_nan=False),
    ),
    max_size=10,
)


@settings(max_examples=200, deadline=None)
@given(SIM)
def test_v02_default_equals_current(k, similar):
    state = session.fold(k, "CL", similar, [])
    now = old_probabilities(state.candidates, state.similar)
    assert session.probabilities(state) == now == session.probabilities(state, session.Scoring())


def test_v02_decide_default_equals_no_args(k, grades):
    g, minutes = grades
    for events in ([], [{"type": "lock"}], [{"type": "lock"}, {"type": "precheck_done"}]):
        state = r5_session(k) if events else session.fold(k, "CL", R5_SIMILAR, [])
        assert session.decide(state, k, g, minutes) == session.decide(
            state, k, g, minutes, rules.CALL_THRESHOLD, session.Scoring()
        )


# ---------------------------------------------------------------- V-03 안전 규칙은 매개변수 밖

EXTREMES = (
    [
        session.Scoring(rule=rule, alpha=alpha, near_min=0.9, manual=manual, manual_weight=1.0)
        for rule in rules.SCORE_RULES
        for alpha in (0.05, 2.0)
        for manual in (None, {"CL-3": 1.0})
    ]
    if hasattr(session, "Scoring")
    else []
)


@pytest.mark.parametrize("threshold", [30, 90])
def test_v03_safety_rules_hold_for_any_scoring(k, grades, threshold):
    g, minutes = grades
    assert EXTREMES
    for sc in EXTREMES:
        unlocked = session.decide(session.fold(k, "CL", R5_SIMILAR, []), k, g, minutes, threshold, sc)
        assert unlocked["kind"] == "lock"  # AC-01 잠금 먼저
        state = r5_session(k)
        d = session.decide(state, k, g, minutes, threshold, sc)
        if d["kind"] in {"instruct", "instruct_after_checklist"}:
            assert g[d["section"]] in rules.INSTRUCTABLE  # AC-02 · AC-08: C · D는 지시 아님
            twice = r5_session(k, [{"type": "result", "section": d["section"], "value": "확인 불가"} for _ in range(2)])
            d2 = session.decide(twice, k, g, minutes, threshold, sc)
            assert d2.get("section") != d["section"] or d2["kind"] not in {
                "instruct",
                "instruct_after_checklist",
            }  # AC-19-C2


# ---------------------------------------------------------------- V-09 요청 본문(manual_support)


def test_v09_judge_body_manual_support_only_when_given():
    base = runs.judge_body(7, "CL", [{"record_id": "H-1", "cause": "CL-1", "score": 0.9}], [], {}, {})
    assert "manual_support" not in base and set(base) == {
        "kind",
        "session_id",
        "group",
        "similar",
        "events",
        "grades",
        "minutes",
    }
    with_ms = runs.judge_body(7, "CL", [], [], {}, {}, manual_support={"CL-1": 0.6})
    assert with_ms["manual_support"] == {"CL-1": 0.6} and {**with_ms, "similar": base["similar"]} != base


# ---------------------------------------------------------------- V-17 호출 요약 원인 %(단위) · 숫자 읽기 도우미


def test_v17_candidates_use_step_decision_values(k):
    state = r5_session(k)
    step = {"kind": "call", "probabilities": {"CL-2": 13, "CL-1": 61, "CL-3": 13, "CL-4": 13}, "evidence": {"CL-1": 2}}
    pct = {c["code"]: c["pct"] for c in flow._candidates(state, k, step)}
    assert pct == {"CL-1": 61, "CL-2": 13, "CL-3": 13, "CL-4": 13}
    assert [c["pct"] for c in flow._candidates(state, k, {})] == sorted(
        old_probabilities(CL, R5_SIMILAR).values(), reverse=True
    )


def test_decided_numbers_order_and_fallback(k):
    order = [c.code for c in rules.ordered_causes(k, "CL")]
    step = {
        "probabilities": {"CL-4": 13, "CL-1": 61, "CL-3": 13, "CL-2": 13},
        "evidence": {"CL-4": 0, "CL-1": 2, "CL-3": 0, "CL-2": 1},
    }
    probs, ev = screen.decided_numbers(step, order)
    assert (
        list(probs) == order and list(ev) == order and probs["CL-1"] == 61
    )  # 원인 순서 = 매뉴얼 개요 순(JSONB 순서와 무관)
    assert screen.decided_numbers({"kind": "lock"}, order) is None and screen.decided_numbers(None, order) is None


# ---------------------------------------------------------------- DB 길(verify-db)


def events(db, sid):
    with db.connect() as c:
        rows = c.execute(
            text("select seq, type, payload from ops.session_events where session_id = :s order by seq"), {"s": sid}
        ).all()
    return [{"seq": r.seq, "type": r.type, **r.payload} for r in rows]


def app_probabilities(db, sid, group="CL"):
    """앱 셈(기본 매개변수) — 업무 기록으로 지금 다시 셈."""
    from c1a15.api import deps, sessions

    with db.connect() as c:
        row = sessions._row(c, sid)
        evs = sessions.events_of(c, sid)
    state = session.fold(
        deps.manual(),
        row.symptom_group,
        sessions.similar_of(row),
        [e for e in evs if e["type"] in sessions.INPUT_TYPES],
    )
    return session.probabilities(state), rules.evidence_counts(state.candidates, state.similar)


def last_decision(db, sid):
    return [e["decision"] for e in events(db, sid) if e["type"] == "step_opened"][-1]


def same(db, sid, label):
    d = last_decision(db, sid)
    assert (d["probabilities"], d["evidence"]) == app_probabilities(db, sid), (label, d.get("kind"))


@pytest.mark.db
def test_v12_default_pack_decision_values_equal_app(api, db, deploy):
    """GAP-001: 기본 Pack이면 매 단계 step decision 값 = 앱 셈(판정 없이 순위를 바꾸는 입력이 없음)."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    for scenario, value in (("DEMO-01", "정상"), ("DEMO-06", "확인 불가")):
        s = tech.post(f"/api/tech/alarms/{alarm(api, scenario)}/session").json()
        sid, checked = s["session_id"], 0

        same(db, sid, scenario)
        s = to_instruct(tech, s)
        same(db, sid, scenario)
        while s["step"]["kind"] in {"instruct", "instruct_after_checklist"} and checked < 8:
            s = ok(tech, s, type="result", section=s["instruction"]["section"], value=value)
            same(db, sid, scenario)
            checked += 1
        assert checked >= 1, scenario


def skewed(deploy, **extra):
    deploy.default = pack_answer(scoring=SKEW, threshold=70, **extra)


@pytest.mark.db
def test_v14_v16_screen_numbers_follow_pack(api, db, deploy):
    """옵션 바꾼 Pack: 진단 % · 근거 n = decision 값(앱 셈과 다름) · 지난 화면 그때 순위 = start decision 값."""
    reset_ops(db)
    skewed(deploy)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json()
    sid = s["session_id"]
    start_d = last_decision(db, sid)
    assert s["probabilities"] == start_d["probabilities"] and s["evidence"] == start_d["evidence"]
    assert s["probabilities"] != app_probabilities(db, sid)[0]  # 시험이 뜻이 있음(옵션이 숫자를 바꿈)
    s = to_instruct(tech, s)
    d = last_decision(db, sid)
    assert s["probabilities"] == d["probabilities"] and s["evidence"] == d["evidence"]
    h = tech.get(f"/api/tech/sessions/{sid}/history").json()
    assert h["steps"][1]["records"][0]["probabilities"] == start_d["probabilities"]  # V-16


@pytest.mark.db
def test_v16b_old_session_without_preview_shown_uses_app(api, db, deploy):
    reset_ops(db)
    skewed(deploy)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    sid = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json()["session_id"]
    with db.begin() as c:
        c.execute(text("delete from ops.session_events where session_id = :s and type = 'preview_shown'"), {"s": sid})
    h = tech.get(f"/api/tech/sessions/{sid}/history").json()
    assert h["steps"][1]["records"][0]["probabilities"] == app_probabilities(db, sid)[0]  # 옛 세션 = 앱 셈 그대로


@pytest.mark.db
def test_v11_no_numbers_in_pack_output_uses_app(api, db, deploy):
    reset_ops(db)
    deploy.default = pack_answer(strip=True)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json()
    s = to_instruct(tech, s)
    assert (s["probabilities"], s["evidence"]) == app_probabilities(db, s["session_id"])


@pytest.mark.db
def test_v15_preview_from_pack_and_manual_support_bridge(api, db, deploy):
    reset_ops(db)
    ms = {"CL-2": 1.0}
    deploy.default = pack_answer(
        scoring=session.Scoring(manual_weight=0.3), use_manual=True, preview=True, manual_support=ms
    )
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json()
    start_req = [p["body"] for p in deploy.posts() if p["body"]["kind"] == "start"][-1]
    want = deploy.default(start_req)["output"]["preview"]
    assert s["preview"]["section"] == want["section"] and s["preview"]["kind"] == want["kind"]
    if want["kind"] in {"instruct", "instruct_after_checklist"}:
        assert f"{want['reason_facts']['probability']}%" in s["preview"]["reason"]
    to_instruct(tech, s)
    judges = [p["body"] for p in deploy.posts() if p["body"]["kind"] == "judge"]
    assert judges and all(b.get("manual_support") == ms for b in judges)  # 약속 v5 다리
    assert (
        count(db, "select count(*) from ops.session_events where type = 'preview_shown' and payload ? 'decision'") == 1
    )


# ---------------------------------------------------------------- V-18 · V-19 정정 확인 = Pack 시험 판정(D-476 덧 12 머리글)


def head(post, name):
    return {k.lower(): v for k, v in post["headers"].items()}.get(name.lower())


def test_trial_purpose_header_and_separate_key():
    """시험 판정 = 머리글 X-Run-Purpose: trial · 키 c1a15-judge-trial-… · 본문 같음 / 실제 Run = 머리글 없음 · 기본 키."""
    from fake_deploy import FakeDeploy
    from test_deploy_client import BODY, OUT, client

    with FakeDeploy() as f:
        f.plan = [{"output": OUT}, {"output": OUT}]
        c = client(f)
        c.run("judge", "S-A57", 12, BODY, purpose="trial")
        c.run("judge", "S-A57", 12, BODY)
    trial, real = f.posts()
    assert head(trial, "X-Run-Purpose") == "trial" and head(real, "X-Run-Purpose") is None
    assert head(trial, "Idempotency-Key").startswith("c1a15-judge-trial-S-A57-12-")
    assert head(real, "Idempotency-Key").startswith("c1a15-judge-S-A57-12-") and trial["body"] == real["body"]
    assert f.executions == 2  # 같은 본문이어도 키가 달라 재생 아님


def judges(deploy, trial=None):
    posts = [p for p in deploy.posts() if p["body"]["kind"] == "judge"]
    if trial is not None:
        posts = [p for p in posts if (head(p, "X-Run-Purpose") == "trial") is trial]
    return [p["body"] for p in posts]


def opened_correction(api, db):
    from test_correction import fix, seq_of

    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = to_instruct(tech, tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json())
    first = s["instruction"]["section"]
    s = ok(tech, s, type="result", section=first, value="정상")
    target = seq_of(db, s["session_id"], "result", first)
    return tech, s, fix, target


@pytest.mark.db
def test_v18_confirm_dialog_next_is_pack_trial_and_save_is_real_run(api, db, deploy):
    reset_ops(db)
    tech, s, fix, target = opened_correction(api, db)
    before, real_before = len(judges(deploy, trial=True)), len(judges(deploy, trial=False))
    r = fix(tech, s, target_seq=target, value="이상", reason="다시 봄")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "confirm_required"
    trial = judges(deploy, trial=True)[before:]
    assert len(trial) == 1 and len(judges(deploy, trial=False)) == real_before  # 시험 판정 하나 · 실제 Run 0
    want = deploy.default(trial[0])["output"]["decision"]
    assert r.json()["detail"]["next"]["section"] == want.get("section")  # 확인 창 '다음' = Pack 시험 판정
    r2 = fix(tech, s, target_seq=target, value="이상", reason="다시 봄", confirmed=True)
    assert r2.status_code == 200
    assert len(judges(deploy, trial=False)) == real_before + 1  # 확인 뒤 저장 = 표시 없는 실제 Run 하나
    assert len(judges(deploy, trial=True)) == before + 1
    assert r2.json()["step"]["section"] == want.get("section")


@pytest.mark.db
def test_v19_cancel_then_other_correction_and_failure(api, db, deploy):
    reset_ops(db)
    tech, s, fix, target = opened_correction(api, db)
    sid = s["session_id"]
    assert (
        fix(tech, s, target_seq=target, value="이상", reason="다시 봄").status_code == 409
    )  # 시험 A → 취소(보내지 않음)
    n = count(db, "select count(*) from ops.session_events where session_id = :s", s=sid)
    deploy.plan = [
        {"status": 503, "code": "trace_unavailable", "retryable": True}
    ] * 2  # VC-36 꼴(저장된 retryable 2번 → 503)
    assert fix(tech, s, target_seq=target, value="확인 불가", reason="다시 봄").status_code == 503  # 시험 판정 실패
    assert count(db, "select count(*) from ops.session_events where session_id = :s", s=sid) == n  # 저장 0
    r = fix(tech, s, target_seq=target, value="확인 불가", reason="다시 봄")
    if r.status_code == 409:
        r = fix(tech, s, target_seq=target, value="확인 불가", reason="다시 봄", confirmed=True)
    assert r.status_code == 200, r.text  # 다른 정정 B 저장 성공(키 재사용 충돌 0)
    assert all(rec["status"] != 422 for rec in deploy.keys.values())
    assert (
        count(db, "select count(*) from ops.session_events where session_id = :s and type = 'correction'", s=sid) == 1
    )
    assert r.json()["step"]["section"] == last_decision(db, sid).get("section")
