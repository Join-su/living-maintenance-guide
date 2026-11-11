# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-538 · D-556 · D-566 예외 상황 기록: 정비자가 예외라고 판단할 때만 적는 글 하나 → 「AI 나눔」(배포 Run kind
exception_split — 약속 v10 2절: 예외 하나 = 주제 + 점검 목록 + 조치 목록 · 한쪽 0 = 나누지 못함) → Pack 비슷한 것 찾기
(v10 3절 kind exception_similar · 예외 통째로 한 번 · 상위 3 · 예외 | 매뉴얼) → 예외 전체에 한 번 같음(missed · 놓침) /
아님(new_exception · 새 예외) = report_confirm.exception_review → 보고서 확정 기록(+ 서버가 붙이는 상황 · 결과 · 자동 정보
여덟) → 파트장 승인 때 정비 이력 원본 층(승인 기록 파일 칸 exception_record.exception · .exception_review) → 승인된 기록 목록.

표 · 열 더함 0. 글이 비면 기록 없음. 자동 정보에 데이터가 없는 칸은 null(지어내기 0).
가짜 배포 서버: 나눔 = fake_deploy.split_answer(글 줄 '점검 …' → 점검 목록 · '조치 …' → 조치 목록) · 비슷한 것 =
similar_answer(Pack similar.py와 같은 셈 · 그 증상군 매뉴얼 점검 · 조치 절 + 가짜 등록 예외 EX-R-0001).
"""

import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_api import api, db, ok, reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import deploy  # noqa: F401
from test_lead import lead

pytestmark = pytest.mark.db

TEXT = "점검 필터 덮개 고정 나사를 손으로 확인. 점검 흡기 덕트 이음매. 조치 나사를 새로 끼움. 조치 밴드를 다시 조임"
SIM0 = {"kind": "예외", "no": "EX-R-0001"}  # 가짜 등록 예외(필터 덮개 나사)
SAME = {"decision": "same", "match": SIM0}
NOT = {"decision": "not", "match": None}
CHECKS = [
    {
        "what": "필터 덮개 고정 나사를 손으로 확인",
        "how": "필터 덮개 고정 나사를 손으로 확인",
        "criteria": "정상 범위",
        "minutes": None,
    },
    {"what": "흡기 덕트 이음매", "how": "흡기 덕트 이음매", "criteria": "정상 범위", "minutes": None},
]
ACTIONS = [
    {"what": "나사를 새로 끼움", "parts": [], "minutes": None},
    {"what": "밴드를 다시 조임", "parts": [], "minutes": None},
]
NOTE_TEXT = (
    "점검 필터 덮개 고정 나사를 손으로 확인 · 점검 흡기 덕트 이음매 · 조치 나사를 새로 끼움 · 조치 밴드를 다시 조임"
)
H = {"X-Internal-Token": "test-internal-token"}


def _to_report(api, db, equipment="M-01"):
    tech, s = start(api, db, equipment=equipment)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    return tech, s


def _split(tech, s, txt=TEXT):
    return ok(tech, s, type="exception_split", exception_text=txt)


def _confirm(tech, s, txt=TEXT, review=SAME):
    return ok(tech, s, type="report_confirm", exception_record={"text": txt}, exception_review=review)


def _mine(api, rid):
    """승인된 예외 기록 목록 중 이 보고서 것만(앞 시험이 남긴 승인 이력 — 지식 schema — 은 뺌)."""
    items = TestClient(api).get("/api/internal/exception-records", headers=H).json()["items"]
    return [x for x in items if x["report_id"] == rid]


def _set(ex):
    """None 칸을 뺀 값(응답은 빈 선택 칸도 None으로 보임 · 안쪽까지)."""
    if isinstance(ex, dict):
        return {k: _set(v) for k, v in ex.items() if v is not None}
    return [_set(x) for x in ex] if isinstance(ex, list) else ex


def _rid(db, sid):
    with db.connect() as c:
        return c.execute(text("select report_id from ops.reports where session_id = :s"), {"s": sid}).scalar()


def _post(tech, s, **body):
    return tech.post(f"/api/tech/sessions/{s['session_id']}/events", json={"seq": s["last_seq"] + 1, **body})


def test_split_similar_review_confirm_context_reject_reconfirm_approve_history_and_list(api, db, deploy):
    reset_ops(db)
    tech, s = _to_report(api, db)
    assert s["report"]["exception_record"] is None and s["report"]["exception_split"] is None
    auto = s["report"]["exception_auto"]  # 확정 전 칸에 보일 자동 두 칸(입력 0)
    assert auto["situation"].startswith("M-01 · 온도 차 저하 · ") and auto["result"] == "재가동 정상(경계 이내)"
    runs_before = len(deploy.posts())
    step = s["step"]
    s = _split(tech, s, f" {TEXT} ")
    sp = s["report"]["exception_split"]
    ex0 = sp["exception"]  # 예외 하나 = 주제 + 점검 목록 + 조치 목록(적은 순서 · 짝 없음 · 약속 v10 1절)
    assert sp["text"] == TEXT and sp["error"] is None and "items" not in sp
    assert ex0["checks"] == CHECKS and ex0["actions"] == ACTIONS
    assert ex0["topic"] and len(ex0["topic"]) <= 15 and not ex0["topic"].startswith("예외상황")
    m0 = ex0["matches"]  # 비슷한 것: 예외 통째로 한 번 · 상위 3 · 높은 점수 먼저 · 최소 점수 0.15 이상 · 예외 | 매뉴얼
    assert 0 < len(m0) <= 3 and all(m["kind"] in {"예외", "매뉴얼"} and m["score"] >= 0.15 for m in m0)
    assert [m["score"] for m in m0] == sorted((m["score"] for m in m0), reverse=True)
    assert {k: m0[0][k] for k in ("kind", "no")} == SIM0  # 등록 예외가 맨 위
    posts = deploy.posts()[runs_before:]
    assert [p["body"]["kind"] for p in posts] == ["exception_split", "exception_similar"]  # 판정 Run 다시 0
    assert posts[0]["body"]["text"] == TEXT  # 약속 v10 2절 요청 꼴 그대로(설비 · 시각 칸 0)
    assert set(posts[0]["body"]) == {"kind", "session_id", "group", "text"}
    assert posts[1]["body"] == {  # 약속 v10 3절 요청 꼴 그대로(v9 items 대신 exception 하나)
        "kind": "exception_similar",
        "session_id": posts[0]["body"]["session_id"],
        "group": "CL",
        "exception": {k: ex0[k] for k in ("topic", "checks", "actions")},
    }
    assert s["step"] == step  # 단계 그대로
    s = _split(tech, s)  # 같은 화면 한 번 더 = 새 Run 둘(seq가 다름) · 같은 결과
    assert len(deploy.posts()) == runs_before + 4 and s["report"]["exception_split"]["exception"] == ex0
    # 확정: 예외 전체에 한 번 같음(비슷한 것 하나) / 아님(match null) — 없음 · 남의 항목 · 짝 어긋남 · 글 바뀜은 409
    for txt, review, reason in (
        (TEXT, None, "exception_review_needed"),
        (TEXT, {**SAME, "match": {"kind": "예외", "no": "EX-R-9999"}}, "exception_review_invalid"),
        (TEXT, {**SAME, "match": None}, "exception_review_invalid"),
        (TEXT, {**NOT, "match": SIM0}, "exception_review_invalid"),
        (TEXT + " 더", SAME, "exception_split_needed"),
    ):
        r = _post(tech, s, type="report_confirm", exception_record={"text": txt}, exception_review=review)
        assert (r.status_code, r.json()["detail"]["reason"]) == (409, reason)
    r = _post(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=[{"item": 0, **SAME}])
    assert r.status_code == 422  # v9 줄마다 꼴(목록 · item 칸)은 받지 않음
    assert _rid(db, s["session_id"]) is None  # 확정되지 않음
    s = _confirm(tech, s)
    ex = s["report"]["exception_record"]
    rid = _rid(db, s["session_id"])
    assert ex["text"] == TEXT and "items" not in ex
    got = ex["exception"]
    assert (got["decision"], got["match"], got["kind"]) == ("same", SIM0, "missed")  # 같음 = 놓침(약속 v9 3절)
    assert {k: got[k] for k in ("topic", "checks", "actions", "matches")} == ex0  # 그때 나눈 것 · 보인 비슷한 것
    assert ex["exception_review"] == SAME  # 승인 기록 파일 칸(약속 v10 6절): exception · exception_review 한 번
    fields = {f["key"]: f["text"] for f in s["report"]["fields"]}
    assert ex["situation"] == f"{fields['equipment_alarm']} · {fields['sensor_evidence']}"  # 확정 때 값
    assert ex["result"] == "재가동 정상(경계 이내)" and "action" not in ex
    assert s["report"]["exception_split"] is None  # 확정 뒤 나눔 결과는 기록 안으로
    ctx = ex["context"]  # 자동 정보 여덟(정비자 입력 0)
    assert ctx["equipment_id"] == "M-01" and ctx["symptom_group"] == {"code": "CL", "name": "온도 차 저하"}
    assert (
        ctx["occurred_at"] and ctx["sensor_values"]["alarm"]["sensor"] == "온도 차" and ctx["sensor_values"]["snapshot"]
    )
    assert ctx["ai_first_check"] == {"section": "4.3", "title": "흡기 필터 육안 점검"}  # 세션 시작 때 첫 안내
    assert ctx["confirmed_cause"] == "냉각 필터 막힘" and ctx["report_id"] == rid
    prev = ctx["previous_maintenance"]  # 같은 설비 정비 이력 중 이 알람 앞 마지막 한 건
    with db.connect() as c:
        want = c.execute(
            text(
                "select record_id from knowledge.history_records where equipment_id = 'M-01' and occurred_at < :at "
                "order by occurred_at desc, record_id desc limit 1"
            ),
            {"at": ctx["occurred_at"]},
        ).scalar()
    assert prev["record_id"] == want and prev["occurred_at"] and prev["outcome"]
    lc = lead(api)
    final = lc.get(f"/api/lead/reports/{rid}/final").json()["exception_record"]
    assert _set(final) == _set(ex)  # 최종 보기 · 내부 목록은 빈 칸도 None으로 보임(action 등)
    assert _mine(api, rid) == []  # 승인 전엔 목록 0
    # 반려 → 다시 나눔 · 다시 확정: 새 확정의 기록이 이김
    assert (
        lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "조치 글 다시"}).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["report"]["exception_record"]["text"] == TEXT  # 다시 열린 보고서 칸에 앞 기록
    one = "점검 흡기 덕트 이음매. 조치 밴드를 다시 조임"
    r = _post(tech, s, type="report_confirm", exception_record={"text": one}, exception_review=NOT)
    assert r.json()["detail"]["reason"] == "exception_split_needed"  # 반려 전 나눔은 쓰지 않음
    s = _split(tech, s, one)
    s = _confirm(tech, s, one, NOT)
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert r.status_code == 200
    hid = r.json()["history_record_id"]
    with db.connect() as c:
        rec = c.execute(text("select record from knowledge.history_records where record_id = :i"), {"i": hid}).scalar()
    er = rec["exception_record"]  # 승인 기록 파일 칸(약속 v10 6절): exception = 1절 꼴 그대로 · exception_review 한 번
    assert er["text"] == one and "items" not in er and er["exception_review"] == NOT
    assert er["exception"] == {"topic": er["exception"]["topic"], "checks": [CHECKS[1]], "actions": [ACTIONS[1]]}
    assert isinstance(er["exception_matches"], list) and er["context"]["report_id"] == rid  # 그때 보인 비슷한 것(앱 칸)
    items = _mine(api, rid)
    assert [(x["record_id"], x["report_id"]) for x in items] == [(hid, rid)]
    from c1a15.api import flow

    assert items[0]["approved_at"] and _set(items[0]["exception_record"]) == _set(flow.record_out(er))
    assert items[0]["exception_record"]["exception"]["kind"] == "new_exception"  # 목록 = 화면 꼴(이름 붙임)
    if os.environ.get("EXCEPTION_LIST_OUT"):  # D-545: 내부 목록 응답 한 건 그대로(가상 기록 · 토큰 · 접속 정보 0)
        with open(os.environ["EXCEPTION_LIST_OUT"], "w", encoding="utf-8") as fp:
            json.dump(items[0], fp, ensure_ascii=False, indent=1)
    if os.environ.get("KG_RECORD_OUT"):  # 승인 기록 파일 꼴 예시 한 건(시험 DB 값 그대로 · 실제 꼴)
        with open(os.environ["KG_RECORD_OUT"], "w", encoding="utf-8") as fp:
            json.dump({"approved_at": items[0]["approved_at"], "record": rec}, fp, ensure_ascii=False, indent=1)


def test_split_run_failure_is_not_split_and_text_confirms_as_new_exception(api, db, deploy):
    """배포 Run이 끝내 실패하면 '나누지 못함'(error · 예외 null · 비슷한 것 Run 0) — 고를 것 없이 글 그대로 새 예외로
    확정(split_error) · 고름을 보내면 409 · 글을 바꾸면 다시 나눠야 함 · 글을 지우면 기록 없이 확정."""
    reset_ops(db)
    tech, s = _to_report(api, db)
    runs_before = len(deploy.posts())
    deploy.plan.extend([{"status": 500, "code": "internal", "retryable": False}] * 3)
    s = _split(tech, s)
    deploy.plan.clear()
    assert s["report"]["exception_split"] == {
        "text": TEXT,
        "exception": None,
        "error": "pack_unavailable",
        "similar_error": None,
    }
    assert {p["body"]["kind"] for p in deploy.posts()[runs_before:]} == {"exception_split"}  # 비슷한 것 Run 0
    r = _post(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=NOT)
    assert r.json()["detail"]["reason"] == "exception_review_invalid"
    r = _post(tech, s, type="report_confirm", exception_record={"text": TEXT + " 더"})
    assert r.json()["detail"]["reason"] == "exception_split_needed"
    s = _confirm(tech, s, review=None)
    ex = s["report"]["exception_record"]
    assert (ex["text"], ex["exception"], ex["exception_review"], ex["split_error"]) == (
        TEXT,
        None,
        None,
        "pack_unavailable",
    )


@pytest.mark.parametrize(
    ("txt", "reason"),
    [
        (f"{TEXT} [시험:나눔없음]", "not_split"),
        (f"{TEXT} [시험:꼴깨짐]", "invalid_output"),
        ("점검 필터 덮개 고정 나사를 손으로 확인. 점검 흡기 덕트 이음매", "not_split"),  # 점검만(조치 0)
        ("조치 나사를 새로 끼움. 조치 밴드를 다시 조임", "not_split"),  # 조치만(점검 0)
    ],
)
def test_split_one_side_empty_is_not_split_like_run_failure(api, db, deploy, txt, reason):
    """약속 v10 2절(D-566 덧 1 Q3): 점검 0 또는 조치 0 · 꼴 밖이면 예외 null + reason = Run 실패와 같이 '나누지 못함'
    — 비슷한 것 Run 0 · 글 그대로 새 예외."""
    reset_ops(db)
    tech, s = _to_report(api, db)
    runs_before = len(deploy.posts())
    s = _split(tech, s, txt)
    assert s["report"]["exception_split"] == {"text": txt, "exception": None, "error": reason, "similar_error": None}
    assert [p["body"]["kind"] for p in deploy.posts()[runs_before:]] == ["exception_split"]
    s = _confirm(tech, s, txt, review=None)
    ex = s["report"]["exception_record"]
    assert (ex["split_error"], ex["exception"], ex["exception_review"]) == (reason, None, None)


def test_split_failure_then_clear_text_confirms_without_record(api, db, deploy):
    """나누지 못한 뒤 글을 지우면 예외 기록 없이 보고서만 확정(선택 칸)."""
    reset_ops(db)
    tech, s = _to_report(api, db)
    s = _split(tech, s, f"{TEXT} [시험:나눔없음]")
    s = ok(tech, s, type="report_confirm")
    assert s["report"]["status"] == "awaiting_approval" and s["report"]["exception_record"] is None


def test_similar_failure_keeps_exception_and_allows_only_not(api, db, deploy):
    """비슷한 것 찾기가 끝내 실패하면 나눈 예외는 그대로 · 비슷한 것 0 · similar_error — 같음은 409, 아님이면 확정."""
    reset_ops(db)
    tech, s = _to_report(api, db)
    deploy.plan.extend([{"use_default": True}, *[{"status": 500, "code": "internal", "retryable": False}] * 3])
    s = _split(tech, s)
    sp = s["report"]["exception_split"]
    assert sp["error"] is None and sp["similar_error"] == "pack_unavailable"
    assert sp["exception"]["checks"] == CHECKS and sp["exception"]["matches"] == []
    deploy.plan.clear()
    r = _post(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=SAME)
    assert r.json()["detail"]["reason"] == "exception_review_invalid"
    s = _confirm(tech, s, review=NOT)
    assert s["report"]["exception_record"]["exception"]["kind"] == "new_exception"


def test_exception_record_empty_is_none_and_list_needs_internal_token(api, db):
    reset_ops(db)
    tech, s = _to_report(api, db)
    s = ok(tech, s, type="report_confirm", exception_record={"text": "  "})
    assert s["report"]["exception_record"] is None
    rid = _rid(db, s["session_id"])
    lc = lead(api)
    hid = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).json()["history_record_id"]
    with db.connect() as c:
        rec = c.execute(text("select record from knowledge.history_records where record_id = :i"), {"i": hid}).scalar()
    assert "exception_record" not in rec  # 빈 기록은 이력에 칸을 만들지 않음
    items = TestClient(api).get("/api/internal/exception-records", headers=H).json()["items"]
    assert [x for x in items if x["report_id"] == rid] == []  # 이 보고서는 목록에 없음(앞 시험이 남긴 승인 이력은 뺌)
    assert TestClient(api).get("/api/internal/exception-records").status_code in {401, 403}  # 내부 토큰 없으면 거부
    assert lc.get("/api/internal/exception-records").status_code in {401, 403}  # 파트장 로그인으로도 못 읽음


def test_exception_text_too_long_and_split_only_on_report_step(api, db):
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    r = _post(tech, s, type="exception_split", exception_text=TEXT)
    assert (r.status_code, r.json()["detail"]) == (409, "no_report_step")  # 보고서 단계에서만
    tech, s = _to_report(api, db)
    assert _post(tech, s, type="exception_split", exception_text="가" * 2001).status_code == 422  # 2000자까지
    assert _post(tech, s, type="exception_split").status_code == 422  # 글 칸 없으면 422
    assert _post(tech, s, type="exception_split", exception_text="  ").json()["detail"] == "exception_text_required"
    for bad in ({"text": "가" * 2001}, {"situation": "가"}, {"check": {"what": "가"}}, {"text": TEXT, "choices": []}):
        # 옛 꼴(두 글 · 나눈 칸 · D-556 1단계 choices)은 받지 않음
        assert _post(tech, s, type="report_confirm", exception_record=bad).status_code == 422
    assert _rid(db, s["session_id"]) is None  # 확정되지 않음


def test_exception_draft_prefills_report_and_reference_notes(api, db):
    """D-543 「노하우 등록」: 잠금 뒤 글 초안 → 보고서 칸에 채워짐 → 나눔 · 보고서와 함께 확정 · 승인 →
    같은 설비 · 같은 증상군의 다음 세션에 참고 알림(앱 원본 조회 — 답장 001-zzz9 다 = A)."""
    reset_ops(db)
    tech, s = start(api, db)
    body = {"seq": s["last_seq"] + 1, "type": "exception_draft", "exception_record": {"text": TEXT}}
    assert tech.post(f"/api/tech/sessions/{s['session_id']}/events", json=body).status_code == 409  # 잠금 전 0
    s = to_instruct(tech, s)
    assert s["exception_draft"] is None  # 아직 초안 없음(앞 시험이 남긴 승인 이력이 있으면 참고 알림은 있을 수 있음)
    s = ok(tech, s, type="exception_draft", exception_record={"text": f" {TEXT} "})
    assert s["exception_draft"] == {"text": TEXT}
    assert s["step"]["kind"] == "instruct" and s["instruction"]["section"] == "4.3"  # 판정 · 단계 그대로
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["report"]["exception_draft"] == {"text": TEXT}  # 보고서 칸에 채울 값
    s = _split(tech, s)
    s = _confirm(tech, s)
    assert s["report"]["exception_draft"] is None and s["report"]["exception_record"]["text"] == TEXT
    rid = _rid(db, s["session_id"])
    hid = lead(api).post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).json()["history_record_id"]
    tech2, s2 = start(api, db, equipment="M-01")
    s2 = to_instruct(tech2, s2)
    note = next(n for n in s2["exception_notes"] if n["record_id"] == hid)
    assert note["situation"].startswith("M-01 · ") and note["action"] == NOTE_TEXT and note["occurred_at"]
    assert note["text"] == NOTE_TEXT  # 알림 한 줄 = 나눈 줄을 이은 글(상황은 자동이라 머리와 겹침)
    tech3, s3 = start(api, db, equipment="M-03")  # 다른 설비는 이 기록을 보지 않음
    s3 = to_instruct(tech3, s3)
    assert all(n["record_id"] != hid for n in s3["exception_notes"])


def test_exception_draft_empty_clears(api, db):
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="exception_draft", exception_record={"text": TEXT})
    assert s["exception_draft"] == {"text": TEXT}
    s = ok(tech, s, type="exception_draft", exception_record={"text": " "})
    assert s["exception_draft"] is None  # 비우면 초안 없음


def test_old_drafts_read_as_one_text():
    """D-556 전 업무 기록 초안(두 글 situation · action)은 글 하나로 읽음(읽기만)."""
    from c1a15.api import flow

    events = [{"seq": 1, "type": "exception_draft", "exception_record": {"situation": "가 ", "action": "나"}}]
    assert flow.exception_draft_of(events) == {"text": "가\n나"}
    assert flow.exception_draft_of([{**events[0], "exception_record": {"situation": " ", "action": ""}}]) is None
    old = {"situation": "상황 글", "action": "조치 글", "context": {}}
    assert flow.exception_text(old) == "조치 글"  # 전 기록 알림 = 조치 글 그대로


def test_split_exception_drops_malformed_lines_and_one_side_empty_is_none():
    """배포 출력 줄 꼴이 어긋나면 그 줄만 뺌(지어내기 0) · 남은 점검 0 또는 조치 0이면 None(나누지 못함)."""
    from c1a15.api import flow

    ck = {"what": "가", "how": "나", "criteria": "다", "minutes": 3}

    def out(**ex):
        return {
            "exception_split": {"exception": {"topic": "덮개 나사", "checks": [ck], "actions": [{"what": "라"}]} | ex}
        }

    got = flow.split_exception(out(checks=[ck, "글", {"what": " "}, {"how": "나"}], actions=[{"what": "라"}, {}, 3]))
    assert got == {
        "topic": "덮개 나사",
        "checks": [ck],
        "actions": [{"what": "라", "parts": [], "minutes": None}],
    }
    assert flow.split_exception(out(checks=[{"what": "가"}]))["checks"] == [  # 방법 · 기준 빈 글은 그대로
        {"what": "가", "how": "", "criteria": "", "minutes": None}
    ]
    assert flow.split_exception(out(checks=[])) is None and flow.split_exception(out(actions=[{"what": " "}])) is None
    assert flow.split_exception(out(checks="글")) is None and flow.split_exception({}) is None
    assert flow.split_exception({"exception_split": {"exception": None, "reason": "not_split"}}) is None
    # 주제: 머리 '예외상황-' 떼기 · 15자 · 비면 첫 조치 what 앞 15자(Pack 다듬기와 같음)
    assert flow.split_exception(out(topic=" 예외상황-덮개 나사 "))["topic"] == "덮개 나사"
    assert flow.split_exception(out(topic="가" * 20))["topic"] == "가" * 15
    assert flow.split_exception(out(topic=None, actions=[{"what": "나" * 20}]))["topic"] == "나" * 15
    many = flow.split_exception(out(checks=[ck] * 12, actions=[{"what": "라"}] * 12))
    assert len(many["checks"]) == len(many["actions"]) == 10
    assert [flow.split_reason(o) for o in ({}, {"exception_split": {"reason": "invalid_output"}})] == [
        "not_split",
        "invalid_output",
    ]


def test_similar_query_and_matches_shape():
    """약속 v10 3절: 요청 = 예외 하나(비슷한 것 칸 뺌) · 결과 = 통째 matches 상위 3 · 모르는 kind · 꼴 어긋남은 뺌."""
    from c1a15.api import flow

    ex = {"topic": "가", "checks": [{"what": "나"}], "actions": [{"what": "다"}], "matches": [{"kind": "예외"}]}
    assert flow.similar_query(ex) == {"topic": "가", "checks": [{"what": "나"}], "actions": [{"what": "다"}]}
    m = lambda k, no, sc: {"kind": k, "no": no, "name": "이름", "score": sc}  # noqa: E731
    rows = [m("매뉴얼", "4.5", 0.6), m("그래프", "X", 0.5), m("예외", "EX-R-1", 0.4), {"kind": "예외"}]
    assert flow.similar_matches({"exception_similar": {"matches": rows}}) == [
        m("매뉴얼", "4.5", 0.6),
        m("예외", "EX-R-1", 0.4),
    ]
    assert len(flow.similar_matches({"exception_similar": {"matches": [m("매뉴얼", "4.4", 0.3)] * 4}})) == 3
    assert flow.similar_matches({"exception_similar": [{"item": 0, "matches": rows}]}) == []  # v9 줄마다 꼴은 읽지 않음
    assert flow.similar_matches({}) == []


def test_v9_pair_records_read_as_one_exception():
    """v9 짝 기록(items · 줄마다 review)은 읽기만: 예외 하나로(점검 · 조치 목록을 이음) — 짝이 하나면 그 고름 그대로,
    여럿이면 고름 칸 없이(지어내기 0) · 기록 줄은 짝마다 그대로 · 알림 글 · 주제도 읽힘."""
    from c1a15.api import flow, sessions

    def pair(i, kind):
        return {
            "topic": f"주제{i}",
            "check": {"what": f"점검{i}", "how": "", "criteria": "", "minutes": None},
            "action": {"what": f"조치{i}", "parts": [], "minutes": None},
            "matches": [],
            "decision": "same" if kind == "missed" else "not",
            "match": {"kind": "예외", "no": "EX-R-1"} if kind == "missed" else None,
            "kind": kind,
        }

    two = {"text": "글", "items": [pair(1, "missed"), pair(2, "new_exception")], "exception_review": [{"item": 0}]}
    out = flow.record_out(two)
    assert "items" not in out and out["exception_review"] is None and out["text"] == "글"
    assert out["exception"] == {
        "topic": "주제1",
        "checks": [two["items"][0]["check"], two["items"][1]["check"]],
        "actions": [two["items"][0]["action"], two["items"][1]["action"]],
        "matches": [],
    }
    one = flow.record_out({"text": "글", "items": [pair(1, "missed")], "exception_review": [{"item": 0}]})
    assert (one["exception"]["kind"], one["exception_review"]) == (
        "missed",
        {"decision": "same", "match": {"kind": "예외", "no": "EX-R-1"}},
    )
    assert flow.record_out({"text": "글", "items": [], "split_error": "not_split"})["exception"] is None
    assert flow.exception_text(two) == "점검 점검1 · 조치 조치1. 점검 점검2 · 조치 조치2"
    events = [{"seq": 1, "type": "report_confirm", "exception_record": two}]
    assert [r["kind"] for r in sessions.check_records(7, events)] == ["missed", "new_exception"]
    m = {"kind": "예외", "no": "EX-R-1"}
    v10 = {
        "text": "글",
        "exception": {"topic": "가", "checks": [], "actions": []},
        "exception_review": {"decision": "same", "match": m},
    }
    (row,) = sessions.check_records(7, [{**events[0], "exception_record": v10}])
    assert (row["kind"], row["relation_id"]) == ("missed", "EX-R-1")
    view = flow.record_out({**v10, "exception_matches": [{**m, "name": "가", "score": 0.5}]})
    assert "exception_matches" not in view and view["exception_review"] == v10["exception_review"]
    assert {k: view["exception"][k] for k in ("decision", "match", "kind")} == {
        "decision": "same",
        "match": m,
        "kind": "missed",
    }
    assert view["exception"]["matches"] == [{**m, "name": "가", "score": 0.5}]
    nv = flow.record_out({**v10, "exception_review": {"decision": "not", "match": None}})["exception"]
    assert (nv["kind"], nv["match"], nv["matches"]) == ("new_exception", None, [])


def test_same_with_edit_stage2(api, db, deploy):
    """정답지 30쪽 2단계 '같음 + 고침'(same-edit.md 1절 · D-560 덧 10): 비슷한 것 예외 줄에 대상 예외 지금 목록(가짜 = Pack 흉내)
    → 같음 + edit {checks, actions} = 고친 뒤 대상 목록 전체 → 기록 exception_review.edit(exception 꼴 그대로) · 화면 응답
    exception.edit · 보고서 · 최종본 · 승인 이력에 같이. 아님 · 매뉴얼 · 목록 없는 줄 · 바뀐 것 0 · 빈 what은 409 / 422."""
    from c1a15.api import flow

    reset_ops(db)
    tech, s = _to_report(api, db)
    s = _split(tech, s)
    ms = s["report"]["exception_split"]["exception"]["matches"]
    target = next(m for m in ms if m["kind"] == "예외")
    manual = next(m for m in ms if m["kind"] == "매뉴얼")
    assert target["checks"] and target["actions"] and manual.get("checks") is None  # 예외 줄만 지금 목록
    checks = [
        {**target["checks"][0], "how": "덮개 고정 나사 네 개가 다 있는지 보고, 풀린 나사가 있으면 손으로 돌려 본다"}
    ]
    actions = [*target["actions"], {"what": "남은 나사도 다시 조인다", "parts": [], "minutes": None}]
    edit = {"checks": checks, "actions": actions}
    for review in (
        {**NOT, "edit": edit},  # 아님엔 고침 0
        {"decision": "same", "match": {"kind": "매뉴얼", "no": manual["no"]}, "edit": edit},  # 매뉴얼 절은 고침 0
        {**SAME, "edit": {"checks": target["checks"], "actions": target["actions"]}},  # 바뀐 것 0 = 같음만
    ):
        r = _post(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=review)
        assert (r.status_code, r.json()["detail"]["reason"]) == (409, "exception_review_invalid")
    bad = {**SAME, "edit": {"checks": [{"what": ""}], "actions": actions}}
    assert (
        _post(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=bad).status_code == 422
    )
    s = _confirm(tech, s, review={**SAME, "edit": edit})
    ex = s["report"]["exception_record"]
    assert ex["exception"]["edit"] == edit and ex["exception"]["kind"] == "missed"
    assert ex["exception_review"] == {**SAME, "edit": edit}  # same-edit.md 1절 칸
    rid = _rid(db, s["session_id"])
    lc = lead(api)
    assert lc.get(f"/api/lead/reports/{rid}/final").json()["exception_record"]["exception"]["edit"] == edit
    hid = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).json()["history_record_id"]
    with db.connect() as c:
        er = c.execute(
            text("select record->'exception_record' from knowledge.history_records where record_id = :i"), {"i": hid}
        ).scalar()
    assert er["exception_review"] == {**SAME, "edit": edit} and set(er["exception"]) == {"topic", "checks", "actions"}
    before = next(m for m in er["exception_matches"] if m["no"] == SIM0["no"])  # 바뀌기 전(그때 받은 대상 목록)
    assert (before["checks"], before["actions"]) == (target["checks"], target["actions"])
    assert flow.record_out(er)["exception"]["edit"] == edit
