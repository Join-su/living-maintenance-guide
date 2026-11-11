# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-566 약속 v10 5절 다음 같은 조건 알람의 예외 안내 — 걸음마다(Pack 3502e29 engine/exception 사본):
C 승인 예외 전체에 한 번 → 점검 c1 → c2 → 조치 a1 → a2(적은 순서 · 결과로 건너뛰지 않음 · 사건 step 칸) → 재가동 → 재가동 값
→ 안(정상)이면 보고서 · 밖이면 원래 점검 순서(D-566 덧 2). 정답지 26쪽 '안내 차례 · 적은 순서 그대로'."""

import json
import os

import pytest

from test_api import api, db, ok, reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import deploy  # noqa: F401
from test_exception_guide import _approve_call, _approved_exception, _bodies, _guide, to_ex

pytestmark = pytest.mark.db

CHECKS = [
    {"what": "필터 덮개 고정 나사 확인", "how": "손으로 돌려 봄", "criteria": "모두 체결 = 정상", "minutes": 5},
    {"what": "흡기 덕트 이음매", "how": "", "criteria": "", "minutes": None},
]
ACTIONS = [
    {"what": "나사를 새로 끼움", "parts": ["M6 나사"], "minutes": 5},
    {"what": "밴드를 다시 조임", "parts": [], "minutes": None},
]


def _walk(s):
    return [(w["step"], w["what"], w["done"], w["now"]) for w in s["exception_walk"]]


def _to_walk(api, db, deploy):
    reset_ops(db)
    hid = _approved_exception(api, db)
    eg = {
        **_guide(hid),
        "minutes": 15,
        "next_section": "4.3",
        "topic": "필터 덮개 나사",
        "checks": CHECKS,
        "actions": ACTIONS,
    }
    deploy.default = pack_answer_of(eg)
    tech, s = start(api, db, equipment="M-01")
    return tech, to_ex(tech, s, eg), eg


def pack_answer_of(eg):
    from fake_deploy import pack_answer

    return pack_answer(exception_guide=eg, preview=True)


def test_walk_call_once_then_each_step_in_order_restart_within_report(api, db, deploy):
    tech, s, eg = _to_walk(api, db, deploy)
    sec = eg["section"]
    # 승인 전 호출(예외 전체에 한 번): 제목 = 첫 걸음 무엇 · 안내 차례 넷 · 지금 = c1(정답지 26쪽)
    assert (s["step"]["kind"], s["step"]["section"]) == ("call", sec) and "step" not in s["step"]
    assert s["call"]["title"] == CHECKS[0]["what"]
    g = s["call"]["exception_guide"]
    assert (g["checks"], g["actions"], g["topic"]) == (CHECKS, ACTIONS, "필터 덮개 나사")
    assert g["next"] is None  # 걸음 길 = '원래 순서 다음' 줄 0(정답지 26쪽)
    assert _walk(s) == [
        ("c1", CHECKS[0]["what"], False, True),
        ("c2", CHECKS[1]["what"], False, False),
        ("a1", ACTIONS[0]["what"], False, False),
        ("a2", ACTIONS[1]["what"], False, False),
    ]
    s = ok(tech, s, type="call_send")
    _approve_call(api, eg)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    # c1: 점검 칸 그대로(무엇 = 제목 · 방법 = 절차 · 기준 · 분)
    assert (s["step"]["kind"], s["step"]["step"], s["step"]["step_index"], s["step"]["steps_total"]) == (
        "instruct",
        "c1",
        1,
        4,
    )
    ins = s["instruction"]
    assert (ins["title"], ins["steps"], ins["criterion"], ins["minutes"]) == (
        CHECKS[0]["what"],
        ["손으로 돌려 봄"],
        "모두 체결 = 정상",
        5,
    )
    n_judge = len(_bodies(deploy, "judge"))
    s = ok(tech, s, type="result", section=sec, value="정상")  # 정상이어도 다음 걸음(건너뛰지 않음)
    ev = [e for e in _bodies(deploy, "judge")[n_judge]["events"] if e.get("section") == sec and e["type"] == "result"]
    assert ev[-1]["step"] == "c1"  # 사건 step 칸(약속 v10 5절)
    assert (s["step"]["kind"], s["step"]["step"]) == ("instruct", "c2")
    assert (s["instruction"]["title"], s["instruction"]["steps"], s["instruction"]["criterion"]) == (
        CHECKS[1]["what"],
        [],
        None,
    )
    assert [w[2:] for w in _walk(s)] == [(True, False), (False, True), (False, False), (False, False)]
    s = ok(tech, s, type="result", section=sec, value="이상", memo="이음매 벌어짐")
    assert (s["step"]["kind"], s["step"]["step"]) == ("action", "a1")
    act = s["action"]
    assert (act["title"], act["steps"], act["minutes"], act["done_at"]) == (
        ACTIONS[0]["what"],
        ["부품 M6 나사"],
        5,
        None,
    )
    assert act["cause_name"] == "예외상황-필터 덮개 나사"
    s = ok(tech, s, type="action_done", section=sec)
    assert (s["step"]["kind"], s["step"]["step"]) == ("action", "a2") and s["action"]["title"] == ACTIONS[1]["what"]
    r = tech.post(
        f"/api/tech/sessions/{s['session_id']}/events",
        json={"seq": s["last_seq"] + 1, "type": "action_done", "section": "4.3.2"},
    )
    assert r.status_code == 409  # 다른 절 조치는 받지 않음
    s = ok(tech, s, type="action_done", section=sec)
    if os.environ.get("WALK_JUDGE_OUT"):  # D-503 2: 걸음 사건이 실린 judge 요청 실제 한 건(Pack v10 schema 대조용)
        with open(os.environ["WALK_JUDGE_OUT"], "w", encoding="utf-8") as fp:
            json.dump(_bodies(deploy, "judge")[-1], fp, ensure_ascii=False, indent=1)
    ad = [e for e in _bodies(deploy, "judge")[-1]["events"] if e["type"] == "action_done"]
    assert [e.get("step") for e in ad] == ["a1", "a2"]
    assert (
        s["step"]["kind"] == "restart_check" and s["action"]["title"] == ACTIONS[1]["what"] and s["action"]["done_at"]
    )
    assert all(w[2] for w in _walk(s)) and not any(w[3] for w in _walk(s))
    titles = [p["title"] for p in s["path"]]
    assert [t for t in titles if t in {x["what"] for x in CHECKS + ACTIONS}] == [
        x["what"] for x in CHECKS + ACTIONS
    ]  # 경로 = 걸음마다
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report"  # 안(정상) → 보고서
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}  # 보고서 = 걸음마다 그 점검 · 조치 무엇
    assert f"{CHECKS[0]['what']}({sec}, 정상) → {CHECKS[1]['what']}({sec}, 이상: 이음매 벌어짐)" in f["check_path"]
    assert f["action"].startswith(f"{ACTIONS[0]['what']}({sec}, C) · {ACTIONS[1]['what']}({sec}, C) · 재가동")
    s2 = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s2["step"] == s["step"]  # 같은 화면 다시 열어도 그대로
    # D-642 R5 · R2: 보고서 한 줄 = '예외상황 · 문제를 찾은 점검 → 조치'(원인 문장 '원인 미상…'을 자르지 않음)
    s = ok(tech, s, type="report_confirm")
    want = f"예외상황 · {CHECKS[1]['what']} → {ACTIONS[0]['what']}, {ACTIONS[1]['what']}"
    rr = next(r for r in tech.get("/api/tech/home").json()["recent_reports"] if r["session_id"] == s["session_id"])
    assert rr["line"] == want and rr["cause"] != want
    from test_lead import lead

    ov = lead(api).get("/api/lead/overview").json()
    assert next(r for r in ov["rows"] if r["session_id"] == s["session_id"])["line"] == want


def test_walk_restart_outside_goes_back_to_original_order(api, db, deploy):
    """D-566 덧 2: 안내를 마친 뒤 재가동 값이 밖이면 원래 점검 순서(4.3 …) — 그 재가동 둘은 원래 순서 접기에서 뺌."""
    tech, s, eg = _to_walk(api, db, deploy)
    sec = eg["section"]
    s = ok(tech, s, type="call_send")
    _approve_call(api, eg)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    for _ in range(2):
        s = ok(tech, s, type="result", section=sec, value="이상", memo="풀림")
    for _ in range(2):
        s = ok(tech, s, type="action_done", section=sec)
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="outside")
    assert s["step"]["section"] == "4.3" and s["step"]["kind"] in ("instruct", "instruct_after_checklist")
    assert "step" not in s["step"] and s["instruction"]["title"] == "흡기 필터 육안 점검"  # 보통 지시(걸음 칸 0)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    assert s["step"]["kind"] == "restart_check"  # 원래 순서의 재가동은 처음부터(예외 몫 재가동은 뺌)
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report"


def test_old_guide_without_lists_keeps_old_path(api, db, deploy):
    """목록 없는 옛 예외 절(v8 · v9 꼴)은 옛 길: 점검 하나 → 정상이면 원래 순서 · 안내 차례 없음."""
    reset_ops(db)
    eg = {**_guide(_approved_exception(api, db)), "next_section": "4.3"}
    deploy.default = pack_answer_of(eg)
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    assert s["exception_walk"] is None and s["call"]["title"] == eg["title"]
    s = ok(tech, s, type="call_send")
    _approve_call(api, eg)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert "step" not in s["step"] and s["instruction"]["steps"] == eg["steps"]
    s = ok(tech, s, type="result", section=eg["section"], value="정상")
    assert s["step"]["section"] == "4.3"


# 연습 여섯(28907 trace)에서 받은 진짜 Pack exception_guide — 절 이름 칸 title = '예외상황-{주제}'(점검 이름 아님)
REAL_GUIDE = {
    "actions": [{"minutes": 5, "parts": [], "what": "덮개 볼트 다시 조임"}],
    "checks": [{"criteria": "", "how": "손으로 돌려 본다", "minutes": 5, "what": "흡기 필터 덮개 볼트"}],
    "criteria": None,
    "grade": "C",
    "minutes": 5,
    "next_section": "4.3",
    "steps": ["손으로 돌려 본다"],
    "text": "손으로 돌려 본다",
    "title": "예외상황-덮개 볼트 조임",
    "topic": "덮개 볼트 조임",
}


def test_real_pack_guide_preview_and_summary_name_the_first_check(api, db, deploy):
    """정답지 11쪽 · D-570 덧 7: 셋째 칩 = 첫 걸음 점검 이름 · 이유 · 호출 요약에 예외 이름 한 번 · 조사 받침 따라."""
    from fake_deploy import pack_answer

    reset_ops(db)
    hid = _approved_exception(api, db)
    eg = {**REAL_GUIDE, "section": f"EX-{hid}", "relation_id": f"EX-{hid}", "basis": [hid]}
    deploy.default = pack_answer(exception_guide=eg, preview=True)
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    text = s["call"]["summary"]["text"]
    assert "예외상황-덮개 볼트 조임과 같은 조건 — 흡기 필터 덮개 볼트(C등급) 확인 필요" in text
    assert text.count("덮개 볼트 조임") == 1


def test_exception_is_one_candidate_switch_in_and_out(api, db, deploy):
    """D-580(사용자): C 예외는 첫 추천 밖 · 후보 줄 — 바꾸기로 고름 → 호출 → 다른 점검으로 다시 바꿈 · 예외는 후보에 남음."""
    reset_ops(db)
    eg = {**_guide(_approved_exception(api, db)), "topic": "필터 덮개 나사", "checks": CHECKS, "actions": ACTIONS}
    sec = eg["section"]
    deploy.default = pack_answer_of(eg)
    tech, s = start(api, db, equipment="M-01")
    assert {"4.4", sec} <= {c["section"] for c in s["preview"]["candidates"]}  # 진단 카드 다른 후보
    s = to_instruct(tech, s)
    assert s["step"]["section"] == "4.3"  # 첫 추천 = 매뉴얼
    row = next(c for c in s["switch_candidates"] if c["section"] == sec)
    assert (row["title"], row["grade"], row["exception"]) == (CHECKS[0]["what"], "C", True)
    s = ok(tech, s, type="switch_check", section=sec)
    assert (s["step"]["kind"], s["step"]["section"]) == ("call", sec)
    cands = {c["section"]: c for c in s["switch_candidates"]}
    assert "4.3" in cands and sec not in cands
    s = ok(tech, s, type="switch_check", section="4.3")
    assert (s["step"]["kind"], s["step"]["section"]) == ("instruct", "4.3")
    assert sec in {c["section"] for c in s["switch_candidates"]}


def test_normal_results_go_to_next_candidate_not_the_action(api, db, deploy):
    """D-580(사용자): 예외 점검이 모두 정상이면 조치로 안 감 · 다음 후보 · 그 예외는 후보에서 빠짐."""
    tech, s, eg = _to_walk(api, db, deploy)
    sec = eg["section"]
    s = ok(tech, s, type="call_send")
    _approve_call(api, eg)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    for name in ("c1", "c2"):
        assert (s["step"]["section"], s["step"]["step"]) == (sec, name)
        s = ok(tech, s, type="result", section=sec, value="정상")
    assert s["step"]["kind"] in ("instruct", "instruct_after_checklist") and s["step"]["section"] != sec
    assert sec not in {c["section"] for c in s["switch_candidates"]}


def test_why_line_says_what_matched(api, db, deploy):
    """D-580 ④: 어떤 조건이 맞았나 한 줄 = Pack matched(증상군 · 설비 · 며칠 전 · 절 · 근거 예외) · 없으면 칸 0."""
    reset_ops(db)
    hid = _approved_exception(api, db)
    eg = {
        **_guide(hid),
        "topic": "필터 덮개 나사",
        "checks": CHECKS,
        "actions": ACTIONS,
        "matched": {
            "group": "온도 차 저하",
            "equipment_id": "M-03",
            "source_equipment": "M-01",
            "sensors": [],
            "exception": f"EX-{hid}",
            "records": [hid],
            "recent": {
                "section": "4.3.2",
                "days": 7,
                "record_id": "H-0301",
                "at": "2026-11-03T10:00:00+09:00",
                "days_ago": 3.0,
            },
        },
    }
    deploy.default = pack_answer_of(eg)
    tech, s = start(api, db, equipment="M-01")
    line = "M-01에서 배운 규칙 · 이 기기도 3일 전 같은 정비(4.3.2 · H-0301)"
    assert next(c for c in s["preview"]["candidates"] if c["exception"])["summary"] == line
    s = to_ex(tech, s, eg)
    assert s["exception_why"] == line


def test_pick_exception_at_diagnosis_opens_call_after_precheck(api, db, deploy):
    """D-585: 진단에서 예외를 고르면 잠금 · 사전 확인 뒤 그 예외 호출(승인 한 번)."""
    reset_ops(db)
    eg = {**_guide(_approved_exception(api, db)), "topic": "필터 덮개 나사", "checks": CHECKS, "actions": ACTIONS}
    deploy.default = pack_answer_of(eg)
    tech, s = start(api, db, equipment="M-01")
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="switch_check", section=eg["section"])
    pv = tech.get(f"/api/tech/sessions/{s['session_id']}").json()["preview"]
    assert s["step"]["kind"] == "lock" and pv["picked"]["title"] == CHECKS[0]["what"]
    s = ok(tech, s, type="lock")
    s = ok(tech, s, type="precheck_done", values="하우징 52 °C")
    assert (s["step"]["kind"], s["step"]["section"]) == ("call", eg["section"])


def test_reason_names_exception_by_first_check_even_with_numbers(api, db, deploy):
    """D-585(new-pack 013-251): 이유 글에 내부 번호 대신 첫 점검 이름 · 이름의 숫자도 근거 검사 통과(원문 대체 0)."""
    reset_ops(db)
    checks = [{**CHECKS[0], "what": "덮개 볼트 4개 확인"}]
    eg = {**_guide(_approved_exception(api, db)), "topic": "덮개 볼트", "checks": checks, "actions": ACTIONS}
    deploy.default = pack_answer_of(eg)
    _, s = start(api, db, equipment="M-01")
    pv = s["preview"]
    assert "덮개 볼트 4개 확인(C)" in pv["reason"] and eg["section"] not in pv["reason"]
    assert pv["reason_fallback"] is False


def test_d642_sentence_flows_split_apply_approve_history_then_walk_and_card(api, db, deploy):
    """D-642 C: AI 나눔 지시 문장(instruction) → 보고서 적용 · 확정 → 파트장 승인 → 이력 줄에 그대로 →
    다음 기계 start exception_guide 문장 → 안내 차례 say = 승인된 문장(지어낸 값 0)."""
    from sqlalchemy import text as sql

    from test_exception_record import _rid, _to_report
    from test_lead import lead

    reset_ops(db)
    txt = "점검 필터 덮개 고정 나사를 손으로 확인. 조치 나사를 새로 끼움 [시험:문장]"
    tech, s = _to_report(api, db)
    s = ok(tech, s, type="exception_split", exception_text=txt)
    ex = s["report"]["exception_split"]["exception"]
    say_c, say_a = ex["checks"][0]["instruction"], ex["actions"][0]["instruction"]
    assert say_c.endswith("정상 범위면 정상") and say_a.endswith("합니다")
    s = ok(
        tech,
        s,
        type="report_confirm",
        exception_record={"text": txt},
        exception_review={"decision": "not", "match": None},
    )
    assert s["report"]["exception_record"]["exception"]["checks"][0]["instruction"] == say_c  # 보고서 칸
    rid = _rid(db, s["session_id"])
    lc = lead(api)
    final = lc.get(f"/api/lead/reports/{rid}/final").json()["exception_record"]
    assert final["exception"]["checks"][0]["instruction"] == say_c  # 파트장 승인 화면 값
    hid = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).json()["history_record_id"]
    with db.connect() as c:
        rec = c.execute(sql("select record from knowledge.history_records where record_id = :i"), {"i": hid}).scalar()
    er = rec["exception_record"]["exception"]
    assert (er["checks"][0]["instruction"], er["actions"][0]["instruction"]) == (
        say_c,
        say_a,
    )  # 승인된 문장만 다른 기계로
    # 다음 기계: 그래프가 돌려준 예외 절(승인된 문장 그대로) → 안내 차례 · 지시 카드
    eg = {
        **_guide(hid),
        "minutes": 15,
        "next_section": "4.3",
        "topic": "필터 덮개 나사",
        **{k: er[k] for k in ("checks", "actions")},
    }
    deploy.default = pack_answer_of(eg)
    tech2, s2 = start(api, db, equipment="M-03")
    s2 = to_ex(tech2, s2, eg)
    assert [(w["step"], w["say"], w["now"]) for w in s2["exception_walk"]] == [
        ("c1", say_c, True),
        ("a1", say_a, False),
    ]
    s2 = ok(tech2, s2, type="call_send")
    _approve_call(api, eg)
    s2 = tech2.get(f"/api/tech/sessions/{s2['session_id']}").json()
    assert s2["step"]["step"] == "c1" and s2["exception_walk"][0]["say"] == say_c
