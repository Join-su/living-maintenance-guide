# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""D-543 구현 2 예외 안내(승인 전 화면) · 약속 v8(D-545 덧 2): start 결과 exception_guide를 세션에 저장 →
judge 요청마다 exception_relation(relation_id)을 다시 담고 등급표에 EX 줄(처음 값 = 안내 등급) →
Pack이 잠금 · 사전 확인 뒤 예외 절(C)을 호출 단계로 내면 호출 화면 칸 call.exception_guide
= 안내 글 · 절차 · 판정 기준 · 근거 이력 · 출처(앱 원본의 승인 예외 기록).

승인 뒤 길(약속 v8 · engine/exception 사본 = c1a15-pack 50b2d74 · d05a64d): 파트장 승인 → 예외 절 지시(매뉴얼 밖 절 —
응답 글 그대로) → 결과 정상이면 원래 순서 다음 · 이상이면 조치 → 재가동 → 재가동 값 → 보고서. 분 표 EX 줄 · 원래 순서 다음.
"""

import json
import os

import pytest
from sqlalchemy import text

from fake_deploy import pack_answer
from test_api import ok, reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import api, db, deploy  # noqa: F401
from test_exception_record import TEXT as EX_TEXT
from test_exception_record import _rid, _to_report
from test_lead import lead

pytestmark = pytest.mark.db


def to_ex(tech, s, eg):
    """D-580(Pack ef9ec74): C 예외는 첫 추천이 아니라 후보 — 사람이 [점검 바꾸기]로 고름."""
    return ok(tech, to_instruct(tech, s), type="switch_check", section=eg["section"])


TEXT = "흡기 필터를 교체한 뒤 사흘 안에 같은 알람이 다시 울리면, 덮개 고정 나사 네 개가 다 있는지 먼저 본다."


def _approved_exception(api, db):
    tech, s = _to_report(api, db)
    s = ok(tech, s, type="exception_split", exception_text=EX_TEXT)
    s = ok(
        tech,
        s,
        type="report_confirm",
        exception_record={"text": EX_TEXT},
        exception_review={"decision": "not", "match": None},
    )
    rid = _rid(db, s["session_id"])
    return lead(api).post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).json()["history_record_id"]


def _guide(hid: str) -> dict:
    """약속 v8 결과 exception_guide(그래프 꼴 정본 4절 · c1a15-pack 주인)."""
    return {
        "section": f"EX-{hid}",
        "relation_id": f"EX-{hid}",
        "grade": "C",
        "title": "필터 덮개 고정 나사 확인",
        "text": TEXT,
        "steps": ["덮개 나사 확인", "빠졌으면 새로 끼움"],
        "criteria": "나사 네 개 모두 체결 = 정상",
        "basis": [hid],
    }


def _home_row(tech, s) -> dict:
    return next(a for a in tech.get("/api/tech/home").json()["alarms"] if a["alarm_id"] == s["alarm_id"])


def _bodies(deploy, kind):
    return [r["body"] for r in deploy.posts() if r["body"].get("kind") == kind]


def test_exception_guide_v8_start_relation_call_block_and_send(api, db, deploy):
    reset_ops(db)
    hid = _approved_exception(api, db)
    eg = _guide(hid)
    deploy.default = pack_answer(exception_guide=eg)
    n_judge = len(_bodies(deploy, "judge"))
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    assert s["step"]["kind"] == "call" and s["step"]["section"] == eg["section"]
    judges = _bodies(deploy, "judge")[n_judge:]
    assert judges and all(b["exception_relation"] == eg["relation_id"] for b in judges)  # judge마다 다시 담음
    assert all(b["grades"][eg["section"]] == "C" for b in judges)  # 등급표 EX 줄(처음 값 = 안내 등급)
    assert "exception_relation" not in _bodies(deploy, "start")[-1]  # start엔 없음(결과로 받음)
    call = s["call"]
    assert (call["section"], call["title"], call["grade"], call["minutes"]) == (
        eg["section"],
        "필터 덮개 고정 나사 확인",
        "C",
        None,
    )  # 매뉴얼 밖 절 — 이름은 Pack 답에서
    g = call["exception_guide"]
    assert {k: g[k] for k in eg} == eg  # v8 칸 그대로
    with db.connect() as c:
        at, sit = c.execute(
            text(
                "select occurred_at, record->'exception_record'->>'situation' from knowledge.history_records "
                "where record_id = :i"
            ),
            {"i": hid},
        ).one()
    assert sit.startswith("M-01 · ")  # D-556: 상황 = 자동 글
    assert g["source"] == {"record_id": hid, "equipment_id": "M-01", "occurred_at": at.isoformat(), "situation": sit}
    assert g["next"] is None  # 원래 순서 다음 = v8에 칸 없음(질문 001-zzz17)
    assert (
        "예외상황-필터 덮개 고정 나사 확인과 같은 조건" in call["summary"]["text"]
    )  # 고침A 26쪽 이름(주제 칸 전 = 이름)
    assert g["topic"] == "필터 덮개 고정 나사 확인"
    if os.environ.get("EXCEPTION_GUIDE_OUT"):  # D-503 2: 바뀐 응답 꼴 실제 한 건(시험 DB 값 그대로)
        with open(os.environ["EXCEPTION_GUIDE_OUT"], "w", encoding="utf-8") as fp:
            json.dump(call, fp, ensure_ascii=False, indent=1)
    # 「노하우 등록」은 호출과 따로 — 단계 · 호출 상태 그대로, 판정 Run을 다시 부르지 않음(판정 입력 아님)
    runs_before = len(deploy.posts())
    s = ok(tech, s, type="exception_draft", exception_record={"text": "덕트 이음매 빠짐 · 다시 끼움"})
    assert s["exception_draft"] == {"text": "덕트 이음매 빠짐 · 다시 끼움"} and s["call"]["status"] == "none"
    assert len(deploy.posts()) == runs_before
    s = ok(tech, s, type="call_wait")
    assert s["call"]["status"] == "waiting" and s["call"]["exception_guide"]["relation_id"] == eg["relation_id"]
    s = ok(tech, s, type="call_send")
    assert s["call"]["status"] == "sent" and s["call"]["exception_guide"]["text"] == TEXT
    assert _bodies(deploy, "judge")[-1]["exception_relation"] == eg["relation_id"]


def test_ordinary_call_has_no_exception_guide(api, db, deploy):
    reset_ops(db)
    tech, s = start(api, db, equipment="M-01")
    s = to_instruct(tech, s)
    for _ in range(8):
        if s["step"]["kind"] == "call":
            break
        s = ok(tech, s, type="result", section=s["instruction"]["section"], value="정상")
    assert s["step"]["kind"] == "call" and s["call"]["exception_guide"] is None
    assert all("exception_relation" not in b for b in _bodies(deploy, "judge"))  # 안내가 없으면 지금 바이트


def test_exception_guide_unknown_record_has_no_source(api, db, deploy):
    reset_ops(db)
    eg = {**_guide("R-9999"), "steps": [], "criteria": None}
    deploy.default = pack_answer(exception_guide=eg)
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    g = s["call"]["exception_guide"]
    assert g["source"] is None and g["steps"] == [] and g["criteria"] is None  # 원본에 없는 기록 — 지어내기 0


def _approve_call(api, eg):
    lc = lead(api)
    (row,) = lc.get("/api/lead/calls").json()["rows"]
    assert row["need"] == f"{eg['title']}({eg['section']}, C)"  # 파트장 표 '필요한 작업' — 예외 절 이름(절, 등급)
    assert lc.post(f"/api/lead/calls/{row['call_id']}/answer", json={"answer": "approved"}).status_code == 200
    return lc, row["call_id"]


def _to_ex_instruct(api, db, deploy, **extra):
    reset_ops(db)
    hid = _approved_exception(api, db)
    eg = {**_guide(hid), "minutes": 15, "next_section": "4.3", **extra}
    deploy.default = pack_answer(exception_guide=eg)
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    assert s["step"]["kind"] == "call" and s["call"]["exception_guide"]["next"] == {
        "section": "4.3",
        "title": "흡기 필터 육안 점검",
    }  # 원래 순서 다음(v8 next_section)
    s = ok(tech, s, type="call_send")
    lc, cid = _approve_call(api, eg)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    return tech, s, eg, lc, cid


def test_exception_after_approval_instruct_then_abnormal_action_restart_report(api, db, deploy):
    tech, s, eg, lc, cid = _to_ex_instruct(api, db, deploy)
    sec = eg["section"]
    assert (s["step"]["kind"], s["step"]["section"]) == ("instruct", sec)  # 승인 뒤 예외 절 지시
    ins = s["instruction"]
    assert (ins["section"], ins["title"]) == (sec, eg["title"])
    assert ins["steps"] == eg["steps"]  # 매뉴얼 밖 절 — 응답 글(절차)을 그대로
    assert ins["criterion"] == eg["criteria"]
    j = _bodies(deploy, "judge")[-1]
    assert (j["grades"][sec], j["minutes"][sec]) == ("C", 15)  # 등급표 · 분 표 EX 줄(D-545 덧 2)
    s = ok(tech, s, type="result", section=sec, value="이상", memo="나사 하나 빠짐")
    assert (s["step"]["kind"], s["step"]["section"]) == ("action", sec)
    assert s["action"]["steps"] == eg["steps"] and s["action"]["section"] == sec
    s = ok(tech, s, type="action_done", section=sec)
    assert s["step"]["kind"] == "restart_check"
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report" and s["step"]["outcome"] == "unknown"  # 관계 원인 없음 → 원인 미상
    assert eg["title"] in [p["title"] for p in s["path"]]  # 지금까지 점검 · 조치 — 예외 절 이름
    assert f"{eg['title']} · {sec}" in [f"{p['title']} · {p['detail']}" for p in s["path"]]  # 'EX-…절' 아님
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert f"{eg['title']}({sec}, C)" in f["action"]  # 조치 칸 등급 = 등급표 EX 줄(None 아님)
    assert f["check_path"].count(eg["title"]) == 1 and f"({sec}, 이상: 나사 하나 빠짐)" in f["check_path"]
    assert f["cause"] == "원인 미상"  # 관계 원인 없음
    assert eg["title"] in lc.get(f"/api/lead/calls/{cid}").json()["need"]  # 파트장 호출 답 화면
    s = ok(tech, s, type="report_confirm")
    rid = _rid(db, s["session_id"])
    assert eg["title"] in json.dumps(lc.get(f"/api/lead/reports/{rid}").json(), ensure_ascii=False)  # 파트장 보고서
    row = next(x for x in lc.get("/api/lead/overview").json()["rows"] if x["report_id"] == rid)
    assert row["line"] == f"예외상황 · {eg['title']}"  # D-642: 옛 v8 안내 = 점검 · 조치가 같은 절 이름 → 한 번만
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert r.status_code == 200
    with db.connect() as c:
        rec = c.execute(
            text("select record from knowledge.history_records where record_id = :i"),
            {"i": r.json()["history_record_id"]},
        ).scalar()
    assert eg["title"] in json.dumps(rec, ensure_ascii=False)  # 승인 → 정비 이력 한 건(예외 절 단계 포함)


def test_exception_after_approval_normal_returns_to_original_order(api, db, deploy):
    tech, s, eg, _, _ = _to_ex_instruct(api, db, deploy)
    sec = eg["section"]
    s = ok(tech, s, type="result", section=sec, value="정상")
    assert s["step"]["kind"] in ("instruct", "instruct_after_checklist", "call")
    assert s["step"]["section"] == "4.3"  # 원래 순서 다음 점검(= next_section)
    s2 = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s2["step"] == s["step"]  # 같은 화면 다시 열어도 그대로


def test_exception_call_sent_shows_on_home_row(api, db, deploy):
    """마지막 대조(final-audit 025): 예외 카드 길에서 호출을 보낸 뒤 왼쪽 알람 칸 '진행 중 · 파트장 호출' — 보통 호출 길과 같음."""
    reset_ops(db)
    eg = _guide(_approved_exception(api, db))
    deploy.default = pack_answer(exception_guide=eg)
    tech, s = start(api, db, equipment="M-01")
    s = to_ex(tech, s, eg)
    assert s["step"]["kind"] == "call" and s["call"]["exception_guide"]
    a = _home_row(tech, s)  # 야간 교대 중엔 앞 승인 보고서 알람도 완료 행으로 함께 보임
    assert a["call_sent"] is None  # 보내기 전
    s = ok(tech, s, type="call_send")
    assert s["call"]["status"] == "sent"
    a = _home_row(tech, s)
    assert a["call_sent"] == "approval"


@pytest.mark.parametrize(("minutes", "cause"), [(5, "CL-1"), (None, None)])  # Pack 예시 꼴 · 원인 없는 관계
def test_start_with_real_pack_exception_preview(api, db, deploy, minutes, cause):
    """F1 걷기 결함(D-546 덧 33): 실제 Pack start는 예외가 맞으면 preview = call EX-…(contracts v8 start-exception.result)
    — 앱 등급표 · 분 표 · 매뉴얼에 없는 절이어도 세션이 열리고 미리보기 카드 값은 예외 안내에서(분 없으면 None)."""
    reset_ops(db)
    eg = {**_guide(_approved_exception(api, db)), "minutes": minutes, "next_section": "4.3"}
    deploy.default = pack_answer(
        exception_guide=eg, preview=True, exception_cause=cause
    )  # Pack 꼴: preview = 예외 절 호출
    tech, s = start(api, db, equipment="M-01")
    assert "session_id" in s, s  # 고치기 전: 500(KeyError EX-…)
    start_body = _bodies(deploy, "start")[-1]
    pv = s["preview"]  # D-580: C 예외는 첫 추천 밖 — 미리보기 = 매뉴얼 · 예외는 후보 줄
    assert pv["kind"] != "call" and pv["section"] != eg["section"]
    assert eg["section"] in {c["section"] for c in pv["candidates"]}
    assert start_body["kind"] == "start"
    s = to_ex(tech, s, eg)
    assert s["step"]["kind"] == "call" and s["step"]["section"] == eg["section"]


def test_exception_counts_as_check_grade_use_and_approved_call_row(api, db, deploy):
    """F2 전 작은 셋(D-546 덧 34 · packrt-1 146 그림): 예외 절 = 정비자가 한 점검 — 지시 머리 '점검 n / 최대 m'에 셈 ·
    최종본 '4. 조치 결과' 권한 사용 C에 셈 · 승인된 호출은 '5. 미확인 · 주간 요청'이 아니라 4의 '파트장 승인' 줄."""
    tech, s, eg, lc, _ = _to_ex_instruct(api, db, deploy)
    sec = eg["section"]
    assert (s["instruction"]["check_no"], s["instruction"]["check_max"]) == (
        2,
        5,
    )  # 첫 추천 4.3을 연 뒤 바꾼 예외 절 = 둘째(D-637 F3: 매뉴얼 넷(C 포함) + 예외 하나)
    s = ok(tech, s, type="result", section=sec, value="정상")
    assert s["step"]["section"] == "4.3" and (s["instruction"]["check_no"], s["instruction"]["check_max"]) == (2, 5)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report"
    s = ok(tech, s, type="report_confirm")
    f = lc.get(f"/api/lead/reports/{_rid(db, s['session_id'])}/final").json()
    assert f["grade_use"]["C"] >= 1  # 승인받은 예외 절(C) 점검이 권한 사용에
    assert eg["title"] not in f["unresolved"]  # 승인된 호출은 미확인 · 주간 요청 아님
    assert f["approved_calls"] == [f"{eg['title']}({sec}, C등급) · 승인 후 진행"]
