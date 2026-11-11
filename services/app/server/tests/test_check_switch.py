# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""약속 v9 [점검 바꾸기] · [예외](c1a15-pack d94f18e · contract-v9.md 1 · 2 · 3절): 점검 안내 중 지금 추천 후보 하나를 고르면
judge 사건 switch_check{section, from_section, choice: candidate} → Pack이 그 점검을 엶(등급 그대로 · switched{from, to}) ·
[예외] = exception_start{mode} → 절 EX-NEW(C): call_first = 호출 → 승인 → 조치 · act_now = 바로 조치(lead_notice
acted_without_call → 파트장 '호출 없이 조치함') → 조치 완료 → 재가동 → 보고서(원인 미상). 후보 밖 · 점검 안내 밖은 409.
기록 줄(3절) = 내부 목록 check-records: 헛걸음 wasted_step · 놓침 missed · 새 예외 new_exception + human_check.
가짜 배포 서버 judge = 앱 engine 사본 + engine/switch.py(Pack custom/switch.py 사본) — Pack judge.py 차례 그대로."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_api import ev, ok, reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import api, db, deploy  # noqa: F401
from test_lead import lead

pytestmark = pytest.mark.db
H = {"X-Internal-Token": "test-internal-token"}


def _post(tech, s, **body):
    return tech.post(f"/api/tech/sessions/{s['session_id']}/events", json={"seq": s["last_seq"] + 1, **body})


def _events(db, sid, etype):
    with db.connect() as c:
        return [
            r.payload
            for r in c.execute(
                text("select payload from ops.session_events where session_id = :s and type = :t order by seq"),
                {"s": sid, "t": etype},
            )
        ]


def _seq(db, sid, etype):
    with db.connect() as c:
        return c.execute(
            text("select seq from ops.session_events where session_id = :s and type = :t"), {"s": sid, "t": etype}
        ).scalar_one()


def _records(api, sid):
    items = TestClient(api).get("/api/internal/check-records", headers=H).json()["items"]
    return [x for x in items if x["session_id"] == sid]


def test_switch_to_candidate_is_a_judge_event_pack_opens_it_then_continues(api, db, deploy):
    reset_ops(db)
    tech, s = start(api, db)
    assert s.get("switch_candidates", []) == []  # 잠금 전 0
    s = to_instruct(tech, s)
    assert (s["step"]["kind"], s["step"]["section"]) == ("instruct", "4.3")
    cands = s["switch_candidates"]
    assert cands and all(set(x) == {"section", "title", "grade", "summary", "exception"} for x in cands)
    assert all(x["summary"] for x in cands)  # 고침A 13쪽 제목 아래 설명(임시 = 매뉴얼 점검 방법 첫 줄)
    assert "4.3" not in {x["section"] for x in cands}  # 지금 점검은 후보 밖
    ab = next(x for x in cands if x["grade"] in {"A", "B"})
    runs_before = len(deploy.posts())
    assert (_post(tech, s, type="switch_check", section="4.3").status_code) == 409  # 지금 점검 = 후보 아님
    assert _post(tech, s, type="switch_check", section="9.9").status_code == 409
    assert _post(tech, s, type="switch_check").status_code == 422  # 절 칸 없으면 422
    assert _post(tech, s, type="check_switch", section=ab["section"]).status_code == 422  # 1단계 임시 이름은 없음
    s = ok(tech, s, type="switch_check", section=ab["section"])
    posts = deploy.posts()[runs_before:]
    assert [p["body"]["kind"] for p in posts] == ["judge"]  # Pack이 판정(앱이 열지 않음)
    want = {"section": ab["section"], "from_section": "4.3", "choice": "candidate"}
    assert {k: posts[0]["body"]["events"][-1].get(k) for k in ("type", *want)} == {"type": "switch_check", **want}
    assert s["step"]["section"] == ab["section"]
    assert s["step"]["kind"] == ("instruct" if ab["grade"] == "A" else "instruct_after_checklist")
    assert s["instruction"]["section"] == ab["section"] and s["instruction"]["title"] == ab["title"]
    assert _events(db, s["session_id"], "switch_check") == [want]
    opened = _events(db, s["session_id"], "step_opened")[-1]["decision"]
    assert opened["switched"] == {"from": "4.3", "to": ab["section"]}  # Pack 결정 칸
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()  # 같은 화면 다시 읽어도 고른 점검 그대로
    assert s["step"]["section"] == ab["section"]
    # 정답지 14쪽 '다시 바꾸기': 바꾼 점검에선 앞 판정 후보를 이어 씀(지금 고른 것 빼고)
    assert [x["section"] for x in s["switch_candidates"]] == [
        x["section"] for x in cands if x["section"] != ab["section"]
    ]
    if s["step"]["kind"] == "instruct_after_checklist":
        s = ok(tech, s, type="b_checklist_done", section=ab["section"], checked=s["instruction"]["checklist"])
    s = ok(tech, s, type="result", section=ab["section"], value="정상")  # 고른 점검 결과 → 이어 감
    assert s["step"]["section"] != ab["section"]
    assert _records(api, s["session_id"]) == [
        {
            "kind": "wasted_step",
            "session_id": s["session_id"],
            "human_check": {"section": ab["section"], "from_section": "4.3"},
            "relation_id": None,
            "at_seq": _seq(db, s["session_id"], "switch_check"),
            "how": None,  # D-633 안 고름 줄 몫 칸(헛걸음 줄은 null)
            "run_id": None,
        }
    ]


def test_switch_only_while_instructing_and_c_grade_becomes_call(api, db, deploy):
    reset_ops(db)
    tech, s = start(api, db)
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    assert s["step"]["kind"] == "precheck" and s["switch_candidates"] == []  # 사전 확인 중엔 후보 0
    for body in ({"type": "switch_check", "section": "4.4"}, {"type": "exception_start", "mode": "act_now"}):
        r = _post(tech, s, **body)
        assert (r.status_code, r.json()["detail"]) == (409, "not_switchable")  # 안전 단계 먼저
    s = ok(tech, s, type="precheck_done", values="하우징 52 °C")
    cd = [x for x in s["switch_candidates"] if x["grade"] in {"C", "D"}]
    if not cd:
        pytest.skip("이 각본 후보에 C · D 점검 없음")
    s = ok(tech, s, type="switch_check", section=cd[0]["section"])
    assert (s["step"]["kind"], s["step"]["section"], s["step"]["reason"]) == (
        "call",
        cd[0]["section"],
        "switched_check",
    )
    assert s["switch_candidates"] == []  # 호출 단계에선 후보 0


def test_exception_call_first_call_approve_action_restart_report_unknown(api, db, deploy):
    reset_ops(db)
    lc = lead(api)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    assert _post(tech, s, type="exception_start").status_code == 422  # mode 없으면 422
    assert _post(tech, s, type="exception_start", mode="later").status_code == 422
    s = ok(tech, s, type="exception_start", mode="call_first")
    assert _events(db, s["session_id"], "exception_start") == [{"mode": "call_first"}]
    step = s["step"]
    assert (step["kind"], step["section"], step["grade"], step["reason"]) == (
        "call",
        "EX-NEW",
        "C",
        "exception_progress",
    )
    assert (s["call"]["section"], s["call"]["title"], s["call"]["grade"]) == ("EX-NEW", "예외 조치", "C")
    assert s["call"]["status"] == "sent" and s["step"]["call_status"] == "sent"  # 정답지 16쪽: 호출은 자동으로 나감
    assert s["call"]["summary"]["text"].endswith(
        " 매뉴얼 후보 밖 — 예외 조치(C등급) 승인 요청. 설비는 정지 상태로 대기 중."
    )
    assert [e["section"] for e in _events(db, s["session_id"], "call_sent")] == ["EX-NEW"]
    assert "action" not in s  # 승인 전 조치 지시 0
    r = _post(tech, s, type="exception_start", mode="act_now")
    assert (r.status_code, r.json()["detail"]) == (409, "not_switchable")  # 한 번만
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert (s["step"]["kind"], s["step"]["section"]) == ("action", "EX-NEW")
    act = s["action"]
    assert (act["section"], act["title"], act["cause_name"], act["grade"], act["steps"]) == (
        "EX-NEW",
        "예외 조치",
        "예외",
        "C",
        [],
    )
    opened = _events(db, s["session_id"], "step_opened")[-1]["decision"]
    assert opened["stage"] == "exception_progress" and "lead_notice" not in opened  # 승인 뒤 = 호출한 조치
    s = ok(tech, s, type="action_done", section="EX-NEW")
    assert s["step"]["kind"] == "restart_check" and s["action"]["done_at"]
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert (s["step"]["kind"], s["step"]["outcome"]) == ("report", "unknown")
    fields = {f["key"]: f["text"] for f in s["report"]["fields"]}
    assert fields["cause"] == "원인 미상" and fields["action"].startswith("예외 조치(예외, C)")  # 화면 글 EX-NEW 0
    acts = [r["action"] for r in lc.get("/api/lead/audit").json()["rows"]]
    assert "지시 · 예외" in acts and not any("EX-NEW" in a for a in acts)  # 파트장 오늘 기록
    assert all(r["session_id"] != s["session_id"] for r in lc.get("/api/lead/overview").json()["acted"])
    assert _records(api, s["session_id"]) == []  # 확정 전 · 바꾼 점검 0 = 기록 줄 0


def test_exception_act_now_notices_lead_and_records_human_check(api, db, deploy):
    reset_ops(db)
    lc = lead(api)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="exception_start", mode="act_now")
    assert (s["step"]["kind"], s["step"]["section"]) == ("action", "EX-NEW")  # 호출 없이 바로 조치
    assert _events(db, s["session_id"], "call_sent") == []
    assert (s["path"][-1]["title"], s["path"][-1]["detail"], s["path"][-1]["status"]) == ("예외 조치", "예외", "now")
    opened = _events(db, s["session_id"], "step_opened")[-1]["decision"]
    assert opened["lead_notice"] == "acted_without_call" and opened["exception_progress"] == {"mode": "act_now"}
    (row,) = [a for a in tech.get("/api/tech/home").json()["alarms"] if a["alarm_id"] == s["alarm_id"]]
    assert row["exception_progress"] is True and row["call_sent"] is None  # 왼쪽 칸 '진행 중 · 예외 조치'(17쪽)
    acted = [r for r in lc.get("/api/lead/overview").json()["acted"] if r["session_id"] == s["session_id"]]
    assert len(acted) == 1 and acted[0]["equipment_id"] == "M-01" and acted[0]["tech_name"] and acted[0]["at"]
    assert acted[0]["group_name"] == "온도 차 저하"
    s = ok(tech, s, type="action_done", section="EX-NEW")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report"
    assert s["report"]["exception_session"] is True and s["report"]["exception_applied"] is None  # 고침A 18쪽 팝업
    r = _post(tech, s, type="report_confirm")
    assert r.json()["detail"]["reason"] == "exception_apply_needed"  # 예외 진행 세션은 적용해야 확정
    r = _post(tech, s, type="exception_apply", exception_record={"text": " "})
    assert (r.status_code, r.json()["detail"]) == (409, "exception_text_required")  # 빈 글 = 적용 0
    txt = "점검 필터 덮개 고정 나사를 손으로 확인. 점검 흡기 덕트 이음매. 조치 나사를 새로 끼움. 조치 밴드를 다시 조임"
    s = ok(tech, s, type="exception_split", exception_text=txt)
    ex0 = s["report"]["exception_split"]["exception"]
    m0 = ex0["matches"][0]
    assert ex0["topic"] == "필터 덮개 나사"  # '예외상황-{주제}'(가짜 = 점검 낱말 1 · 2 · 4 · Pack 몫)
    assert [len(ex0["checks"]), len(ex0["actions"])] == [2, 2]  # 예외 하나 = 점검 목록 + 조치 목록(약속 v10)
    review = {"decision": "same", "match": {"kind": m0["kind"], "no": m0["no"]}}
    runs_before = len(deploy.posts())
    s = ok(
        tech,
        s,
        type="exception_apply",
        exception_record={"text": txt},
        exception_review={"decision": "not", "match": None},
    )
    assert s["report"]["exception_applied"]["exception"]["kind"] == "new_exception"
    s = ok(
        tech, s, type="exception_apply", exception_record={"text": txt}, exception_review=review
    )  # [예외 상황 수정] → 다시 적용
    ap = s["report"]["exception_applied"]
    assert (ap["text"], ap["exception"]["kind"], ap["exception_review"]) == (txt, "missed", review)
    assert len(deploy.posts()) == runs_before and s["report"]["status"] == "draft"  # 적용 = 판정 Run 0 · 확정 전
    s = ok(tech, s, type="report_confirm")  # 적용한 것을 함께 확정
    assert s["report"]["exception_record"]["exception_review"] == review
    (rec,) = _records(api, s["session_id"])
    assert rec["kind"] == "missed" and rec["relation_id"] == m0["no"]
    assert rec["human_check"] == {"section": "EX-NEW", "from_section": "4.3"}  # 예외 진행 = EX-NEW · 그때 점검
    assert TestClient(api).get("/api/internal/check-records").status_code in {401, 403}  # 내부 토큰 없으면 거부
    # 정답지 20쪽: 반려되면 보고서 다시 열림(Pack d9b562b 덧층 사본) — 앞 확정 기록이 '예외 상황' 구역에 다시(= 적용한 것)
    with db.connect() as c:
        rid = c.execute(
            text("select report_id from ops.reports where session_id = :s"), {"s": s["session_id"]}
        ).scalar()
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert (s["step"]["kind"], s["report"]["status"]) == ("report", "draft")
    ap = s["report"]["exception_applied"]
    assert ap["text"] == txt and ap["exception_review"] == review
    s = ok(tech, s, type="report_confirm")  # 다시 확정 → 닫힘
    assert s["report"]["exception_record"]["exception_review"] == review and s["step"]["kind"] == "closed"


def test_rejected_report_reuses_last_confirmed_exception_as_applied():
    """정답지 20쪽: 반려되면 앞 확정 기록이 '예외 상황' 구역에 다시(= 적용한 것) — 앱 몫(Pack 반려 길은 따로)."""
    from c1a15.api import flow

    one = {"topic": "가", "checks": [], "actions": [], "decision": "same", "kind": "missed"}
    rec = {"text": "글", "exception": one, "exception_review": {"decision": "same", "match": None}}
    events = [
        {
            "seq": 1,
            "type": "exception_apply",
            "text": "옛 글",
            "exception": one,
            "exception_review": None,
            "split_error": None,
        },
        {"seq": 2, "type": "report_confirm", "exception_record": rec},
    ]
    assert flow.exception_applied_of(events) is None  # 확정 뒤 = 적용 0
    rejected = [*events, {"seq": 3, "type": "report_rejected"}]
    assert flow.exception_applied_of(rejected) == {**rec, "split_error": None}
    reapplied = [
        *rejected,
        {
            "seq": 4,
            "type": "exception_apply",
            "text": "새 글",
            "exception": one,
            "exception_review": None,
            "split_error": None,
        },
    ]
    assert flow.exception_applied_of(reapplied)["text"] == "새 글"  # 반려 뒤 다시 적용하면 그것


def test_check_records_order_and_names():
    """기록 줄 셈(순수 함수): 바꾼 점검마다 헛걸음 · 마지막 확정 예외 하나 = 놓침 / 새 예외 · 무효 사건은 뺌."""
    from c1a15.api.sessions import check_records

    events = [
        {"seq": 5, "type": "step_opened", "decision": {"kind": "instruct", "section": "4.3"}},
        {"seq": 6, "type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"},
        {
            "seq": 7,
            "type": "switch_check",
            "section": "4.6",
            "from_section": "4.5",
            "choice": "candidate",
            "void": True,
        },
        {"seq": 8, "type": "step_opened", "decision": {"kind": "instruct", "section": "4.5"}},
        {"seq": 8.5, "type": "result", "section": "4.5", "value": "이상"},  # 고른 점검 결과 = 헛걸음 채택
        {
            "seq": 9,
            "type": "report_confirm",
            "exception_record": {
                "exception": {"topic": "가", "checks": [], "actions": []},
                "exception_review": {"decision": "same", "match": {"kind": "매뉴얼", "no": "4.5.2"}},
            },
        },
    ]
    hc = {"section": "4.5", "from_section": "4.3"}
    assert check_records(3, events) == [  # 예외 하나 = 줄 하나(약속 v10 · 예외 전체에 한 번)
        {"kind": "wasted_step", "session_id": 3, "human_check": hc, "at_seq": 6},
        {"kind": "missed", "session_id": 3, "human_check": hc, "relation_id": "4.5.2", "at_seq": 9},
    ]
    nr = {
        "exception": {"topic": "가", "checks": [], "actions": []},
        "exception_review": {"decision": "not", "match": None},
    }
    new_ex = {**events[-1], "exception_record": nr}
    assert check_records(3, [*events[:-1], new_ex])[-1]["kind"] == "new_exception"
    assert [
        r["kind"] for r in check_records(3, [*events[:-1], {**new_ex, "exception_record": {"split_error": "x"}}])
    ] == ["wasted_step"]  # 나누지 못함(예외 null) = 기록 줄 0 — 앞과 같음
    assert check_records(3, [*events[:1], {"seq": 6, "type": "exception_start", "mode": "act_now"}]) == []
    # 고침A 14쪽: 여러 번 바꾸면 마지막에 결과를 낸 점검만(중간 고름 기록 0) · from = 처음 추천
    chain = [
        events[0],
        {"seq": 6, "type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"},
        {"seq": 7, "type": "switch_check", "section": "4.6", "from_section": "4.5", "choice": "candidate"},
        {"seq": 8, "type": "result", "section": "4.6", "value": "정상"},
    ]
    assert check_records(3, chain) == [
        {"kind": "wasted_step", "session_id": 3, "human_check": {"section": "4.6", "from_section": "4.3"}, "at_seq": 7}
    ]
    assert check_records(3, chain[:3]) == []  # 결과 전 = 기록 0


def test_pick_at_diagnosis_keeps_lock_and_precheck_then_opens_it(api, db, deploy):
    """D-585(사용자): 진단 카드 [점검 바꾸기] — 예외가 없는 보통 세션도 같음. 고른 뒤에도 잠금 · 사전 확인이 먼저 · 그 뒤 고른 점검."""
    reset_ops(db)
    tech, s = start(api, db, equipment="M-02")
    first = s["preview"]["section"]
    cands = s["preview"]["candidates"]
    assert cands and all(not c["exception"] for c in cands) and first not in {c["section"] for c in cands}
    pick = next(c for c in cands if c["grade"] in {"A", "B"})
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="switch_check", section=pick["section"])
    assert s["step"]["kind"] == "lock"  # 안전 순서 그대로
    pv = tech.get(f"/api/tech/sessions/{s['session_id']}").json()["preview"]
    assert pv["picked"] == {"section": pick["section"], "title": pick["title"], "grade": pick["grade"]}
    assert ev(tech, s, type="precheck_done", values="x").status_code == 409  # 잠금 전 사전 확인 0
    s = ok(tech, s, type="lock")
    assert s["step"]["kind"] == "precheck"
    s = ok(tech, s, type="precheck_done", values="하우징 표면 52 °C")
    assert (s["step"]["kind"], s["step"]["section"]) == ("instruct", pick["section"])


def test_pick_outside_candidates_at_diagnosis_is_refused(api, db, deploy):
    reset_ops(db)
    tech, s = start(api, db, equipment="M-02")
    s = ok(tech, s, type="alarm_ack")
    assert ev(tech, s, type="switch_check", section="9.9").status_code == 409


def test_precheck_call_up_goes_the_call_first_path(api, db, deploy):
    """피드백 F9: 사전 확인 [상급자 호출] = precheck_done{values, mode: call_first} 한 번 → 사전 확인 끝 ·
    정비자 예외 '호출 후 조치'와 같은 길(exception_start → EX-NEW 호출 자동). [확인]은 값 '확인함'으로 그대로."""
    reset_ops(db)
    lc = lead(api)
    tech, s = start(api, db)
    for etype in ("alarm_ack", "lock"):
        s = ok(tech, s, type=etype)
    s = ok(tech, s, type="precheck_done", values="이상 있음 · 상급자 호출", mode="call_first")
    sid = s["session_id"]
    assert _events(db, sid, "precheck_done") == [{"values": "이상 있음 · 상급자 호출"}]  # 사전 확인 사건에 mode 안 남김
    assert _events(db, sid, "exception_start") == [{"mode": "call_first"}]
    assert (s["step"]["kind"], s["step"]["section"], s["step"]["call_status"]) == ("call", "EX-NEW", "sent")
    assert [e["section"] for e in _events(db, sid, "call_sent")] == ["EX-NEW"]
    assert lc.get("/api/lead/calls").json()["rows"]  # 파트장 쪽 호출 한 줄
    r = _post(tech, s, type="precheck_done", values="확인함")
    assert (r.status_code, r.json()["detail"]) == (409, "not_precheck_step")


def test_precheck_confirm_only_is_plain_precheck(api, db, deploy):
    reset_ops(db)
    tech, s = start(api, db)
    for etype in ("alarm_ack", "lock"):
        s = ok(tech, s, type=etype)
    s = ok(tech, s, type="precheck_done", values="확인함")
    assert s["step"]["kind"] == "instruct" and _events(db, s["session_id"], "exception_start") == []
