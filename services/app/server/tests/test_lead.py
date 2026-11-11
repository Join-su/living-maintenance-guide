# ruff: noqa: F811, E501 — test_api의 fixture(api · db · night)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""P-11 파트장 API 통합 시험(시험 DB): 승인 대기 · 검토 결정 · 이력 변환(R12) · 호출 답 · 권한 등급(R13) · 감사 조회.

값 정의는 사용자 결정 018(권한 위반 · 등급 표 범위 · 직접 확인 · 숙련자 인계 답 · 이력 변환).
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from test_api import (  # noqa: F401 — fixture(api · db · night)는 이름으로 쓴다
    LEAD_PW,
    api,
    db,
    ev,
    login,
    night,
    ok,
    register,
    reset_ops,
    run_until,
    start,
    to_instruct,
)

pytestmark = pytest.mark.db


def lead(api):
    c, r = login(api, "lead", "lead.park", LEAD_PW)
    assert r.status_code == 200
    return c


def confirmed_report(api, db, equipment="M-01", memo="기름때가 필터 표면 절반 넘게 덮음"):
    """정비자: 4.3 이상 → 조치 → 재가동 → 경계 이내 → 보고서 확정(승인 대기)."""
    tech, s = start(api, db, equipment=equipment)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo=memo)
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    with db.connect() as c:
        rid = c.execute(
            text("select report_id from ops.reports where session_id = :s"), {"s": s["session_id"]}
        ).scalar()
    return tech, s, rid


def test_lead_role_boundary_and_empty_overview(api, db):
    """AC-12(파트장 API 부분) · AC-27-C3: 파트장 역할만, 비면 타일 0 · 빈 행."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", __import__("test_api").TECH_PW)
    assert tech.get("/api/lead/overview").status_code in {401, 403}
    lc = lead(api)
    o = lc.get("/api/lead/overview").json()
    assert o["tiles"] == {"awaiting": 0, "calls_open": 0, "violations": 0} and o["rows"] == []
    assert lc.get("/api/tech/home").status_code in {401, 403}


def test_report_line_is_cause_name_not_cause_sentence(api, db):
    """D-642 R5 · R2: 홈 '최근 내 보고서' · 파트장 '확정된 보고서' 한 줄 = 업무 기록의 확정 원인 이름 — 원인 칸이 긴 문장이어도."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    lc = lead(api)
    # D-642 R5 · R2: 한 줄은 원인 문장이 아니라 업무 기록의 확정 원인 이름 — 원인 칸이 긴 문장이어도 그대로
    long = "흡기 필터 표면의 기름때가 절반 이상 덮여 냉각 필터가 막힘(4.3, H-0197, H-0232)."
    with db.begin() as c:
        c.execute(
            text(
                "update ops.reports set fields = (select jsonb_agg(case when f->>'key' = 'cause'"
                " then jsonb_set(f, '{text}', to_jsonb(cast(:t as text))) else f end) from jsonb_array_elements(fields) f)"
                " where report_id = :r"
            ),
            {"t": long, "r": rid},
        )
    row = lc.get("/api/lead/overview").json()["rows"][0]
    assert (row["cause"], row["line"]) == (long, "냉각 필터 막힘")
    rr = tech.get("/api/tech/home").json()["recent_reports"][0]
    assert (rr["cause"], rr["line"]) == (long, "냉각 필터 막힘")


def test_lead_overview_rows_and_approve_adds_history(api, db):
    """AC-27-C1 · C2 · AC-29-C3 · R12(결정 018 ⑤): 승인 대기 행 → 그대로 승인 → R-번호 이력(다음 진단 후보는 배포 서버 R12)."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    lc = lead(api)
    o = lc.get("/api/lead/overview").json()
    row = o["rows"][0]
    assert o["tiles"]["awaiting"] == 1 and o["tiles"]["violations"] == 0
    assert (row["equipment_id"], row["group_name"], row["cause"]) == ("M-01", "온도 차 저하", "냉각 필터 막힘")
    assert row["author_name"] and row["confirmed_at"] and row["evidence_count"] == 3 and row["violations"] == 0
    d = lc.get(f"/api/lead/reports/{rid}").json()
    assert [f["label"] for f in d["fields"]] == ["확정 원인", "점검 경로", "조치", "근거 사례", "미확인"]
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["history_record_id"] == f"R-{rid:04d}"
    hid = r.json()["history_record_id"]
    with db.connect() as c:
        h = c.execute(
            text("select source, symptom_group, record from knowledge.history_records where record_id = :i"), {"i": hid}
        ).one()
        st = c.execute(text("select status, approval from ops.reports where report_id = :r"), {"r": rid}).one()
    assert h.source == "approved_report" and h.symptom_group == "CL"
    # VC-58(EV-001 CH-32): 앱은 벡터 · 새 release를 쓰지 않음 — 그 표는 0009로 없어서(test_db VC-56) 쓰기 코드가 되살아나면
    # 승인이 SQL 오류로 200이 아니게 됨(위 단언). 다음 진단 후보는 배포 서버 R12
    rec = h.record
    assert (
        rec["source"] == {"report_id": rid}
        and rec["outcome"]["cause_code"] == "CL-1"
        and rec["approval"]["status"] == "승인"
    )
    assert [x["kind"] for x in rec["steps"]][:2] == ["lock", "check"] and rec["narrative"].endswith("분.")
    assert st.status == "approved" and st.approval["mode"] == "approve"
    home = tech.get("/api/tech/home").json()
    assert home["recent_reports"][0]["status"] == "approved"
    # 결정 018 ⑥(AC-09-C1) '다음 같은 증상군 알람의 후보에 듦'은 배포 서버 R12 몫(deploy KS-5 끝-끝, PR-25)
    rec_view = tech.get(f"/api/records/{hid}").json()  # 기록 창에서 R-번호로 열림
    assert rec_view["source"] == "approved_report" and rec_view["outcome"]["cause_name"] == "냉각 필터 막힘"
    assert (
        lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 409
    )  # 두 번 승인 없음


def test_lead_violation_tile_counts_reports(api, db):
    """AC-27-C1 · 결정 018 ①: 지시로 열리지 않은 절의 결과가 든 보고서는 행 위반 수 · 타일(위반 1건 이상 보고서 수)에 셈."""
    reset_ops(db)
    _, s, rid = confirmed_report(api, db)
    _, s2, rid2 = confirmed_report(api, db, equipment="M-03")
    with db.begin() as c:  # 시험 DB: 결과 하나의 절을 지시받지 않은 절로 바꿔 위반 기록을 만든다
        c.execute(
            text(
                "update ops.session_events set payload = jsonb_set(payload, '{section}', '\"4.5\"') "
                "where session_id = :s and type = 'result'"
            ),
            {"s": s["session_id"]},
        )
    o = lead(api).get("/api/lead/overview").json()
    rows = {r["report_id"]: r["violations"] for r in o["rows"]}
    assert o["tiles"]["violations"] == 1 and rows[rid] >= 1 and rows[rid2] == 0


def test_approve_index_only_after_commit(api, db, monkeypatch):
    """R12 · G6-S04-2 비차단 ③: 승인 요청이 commit 전에 실패하면 이력 행이 남지 않고, commit 뒤 할 일(받은 편지)은 성공 때만 돈다."""
    from c1a15.api import deps
    from c1a15.api import lead as lead_api

    called: list[str] = []
    g = deps.conn()
    next(g).info["after_commit"].append(lambda: called.append("x"))
    with pytest.raises(RuntimeError):
        g.throw(RuntimeError("요청 실패"))
    g = deps.conn()
    next(g).info["after_commit"].append(lambda: called.append("ok"))
    with pytest.raises(StopIteration):
        next(g)
    assert called == ["ok"]
    reset_ops(db)
    _, s, rid = confirmed_report(api, db)
    hid = f"R-{rid:04d}"
    real = lead_api.sessions.audit

    def boom(c, user_id, action, *a, **k):
        if action == "report_approve":
            raise RuntimeError("승인 감사 실패")
        return real(c, user_id, action, *a, **k)

    monkeypatch.setattr(lead_api.sessions, "audit", boom)
    with pytest.raises(RuntimeError):
        lead(api).post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    with db.connect() as c:
        assert not c.execute(
            text("select count(*) from knowledge.history_records where record_id = :i"), {"i": hid}
        ).scalar()
        assert (
            c.execute(text("select status from ops.reports where report_id = :r"), {"r": rid}).scalar()
            == "awaiting_approval"
        )


def test_lead_eval_mode_report_not_indexed(api, db):
    """AC-09-C2(결정 017 ②: 평가 모드 표시는 시험 DB로): 평가 모드 알람의 보고서는 승인해도 이력에 들어가지 않는다."""
    reset_ops(db)
    _, s, rid = confirmed_report(api, db)
    with db.begin() as c:
        c.execute(text("update ops.alarms set eval_mode = true where alarm_id = :a"), {"a": s["alarm_id"]})
    lc = lead(api)
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert r.status_code == 200 and r.json().get("history_record_id") is None
    with db.connect() as c:
        n = c.execute(
            text("select count(*) from knowledge.history_records where record_id = :i"), {"i": f"R-{rid:04d}"}
        ).scalar()
    assert n == 0


def test_lead_edit_approve_r8_and_audit(api, db):
    """AC-06(P-11) · AC-10 · L03 / edit · edit_failed: 근거 없는 수치는 승인 안 됨, 고친 항목 전 → 후 · 메모가 감사에."""
    reset_ops(db)
    _, s, rid = confirmed_report(api, db)
    lc = lead(api)
    bad = {"unresolved": "냉각 팬 상태(4.4, C) · 팬 회전수 1,850 rpm으로 정상 확인"}
    chk = lc.post(f"/api/lead/reports/{rid}/check", json={"edits": bad}).json()
    assert (
        not chk["ok"]
        and chk["fields"][0]["unsupported_numbers"]
        and "세션 입력·매뉴얼 원문에 없습니다" in chk["messages"][0]
    )
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "edit", "edits": bad, "memo": "팬 상태 확인됨"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "edit_ungrounded"
    good = {"unresolved": "냉각 팬 상태(4.4, C) · 주간 조 확인 배정"}
    assert lc.post(f"/api/lead/reports/{rid}/check", json={"edits": good}).json()["ok"]
    r = lc.post(
        f"/api/lead/reports/{rid}/decision", json={"decision": "edit", "edits": good, "memo": "주간 확인 담당을 지정함"}
    )
    assert r.status_code == 200
    with db.connect() as c:
        ap = c.execute(text("select approval, fields from ops.reports where report_id = :r"), {"r": rid}).one()
        au = c.execute(text("select detail from ops.audit_log where action = 'report_edit_approve'")).scalar()
        rej = [d for (d,) in c.execute(text("select detail from ops.audit_log where action = 'reject'"))]
    e = ap.approval["edits"][0]
    assert (
        ap.approval["mode"] == "edit"
        and e["field"] == "unresolved"
        and e["after"] == good["unresolved"]
        and e["before"]
    )
    assert au["edits"][0]["after"] == good["unresolved"] and au["memo"] == "주간 확인 담당을 지정함"  # AC-10-C1
    assert {f["key"]: f["text"] for f in ap.fields}["unresolved"] == good["unresolved"]
    assert any(d.get("reason") == "edit_ungrounded" for d in rej)


def test_lead_reject_requires_reason_and_reaches_tech(api, db, night):
    """AC-24 · AC-11-C1(API) · AC-37(반려 항목): 사유 없으면 거부(감사), 사유 있으면 정비자 최근 보고서 · 종 목록 · 세션 재개."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    lc = lead(api)
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "  "})
    assert r.status_code == 409 and r.json()["detail"] == "reason_required"
    with db.connect() as c:
        assert (
            c.execute(text("select status from ops.reports where report_id = :r"), {"r": rid}).scalar()
            == "awaiting_approval"
        )
        assert (
            c.execute(
                text(
                    "select count(*) from ops.audit_log where action = 'reject' and detail->>'reason' = 'reason_required'"
                )
            ).scalar()
            == 1
        )
    reason = "필터 표면 상태를 메모로 다시 적어 주세요"
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": reason}).status_code == 200
    rr = tech.get("/api/tech/home").json()["recent_reports"][0]
    assert rr["status"] == "rejected" and rr["reject_reason"] == reason and rr["rejected_by_name"]
    items = tech.get("/api/tech/notifications").json()["items"]
    assert any(i["kind"] == "rejected_report" and i["reject_reason"] == reason for i in items)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert (
        s["status"] == "in_progress" and s["step"]["kind"] == "report" and s["report"]["rejection"]["reason"] == reason
    )
    s = ok(tech, s, type="report_confirm")  # AC-11-C2: 다시 확정 → 승인 대기
    assert lc.get("/api/lead/overview").json()["rows"][0]["report_id"] == rid


def test_lead_call_answers(api, db):
    """AC-08 · AC-23-C1 · C3 · AC-42(API) · L04: 호출 목록 · 상세 선택지, D는 승인 없음(서버 거부), 승인 → 그 C 지시."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    sec = s["call"]["section"]
    s = ok(tech, s, type="call_send")
    lc = lead(api)
    rows = lc.get("/api/lead/calls").json()["rows"]
    assert (
        rows[0]["kind"] == "approval" and rows[0]["need"] == f"냉각 팬 분해 점검({sec}, C)" and rows[0]["sender_name"]
    )
    cid = rows[0]["call_id"]
    d = lc.get(f"/api/lead/calls/{cid}").json()
    assert [o["value"] for o in d["options"]] == ["approved", "daytime", "direct"] and d["summary"]
    assert lc.get("/api/lead/overview").json()["tiles"]["calls_open"] == 1
    r = lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "assign"})
    assert r.status_code == 409  # 파트장 호출에 인계 답 없음
    assert (
        lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved", "note": "팬 점검 승인"}).status_code == 200
    )
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "daytime"}).status_code == 409  # 한 번만
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["step"]["kind"] == "instruct" and s["instruction"]["section"] == sec  # AC-08-C2
    assert s["call"]["answer"]["by"] == "lead.park" and s["call"]["answer"]["at"]
    s = ok(tech, s, type="result", section=sec, value="이상", memo="팬 축 유격 큼")  # AC-42-C1: 확정형 C '이상'
    assert s["step"]["kind"] in {"call", "action", "action_after_checklist"} and s["action"]["cause_code"] == "CL-2"
    assert (
        lc.get("/api/lead/calls").json()["rows"][0]["call_id"] != cid
        if lc.get("/api/lead/calls").json()["rows"]
        else True
    )
    # D 등급 호출(초기화하면 로그인도 지워지므로 다시 로그인)
    reset_ops(db)
    with db.begin() as c:
        c.execute(text(f"update ops.grade_table set grade = 'D' where section_id = '{sec}'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda x: "정상")
    s = ok(tech, s, type="call_send")
    lc = lead(api)
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert [o["value"] for o in lc.get(f"/api/lead/calls/{cid}").json()["options"]] == ["daytime", "direct"]  # AC-23-C3
    r = lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved"})
    assert r.status_code == 409 and r.json()["detail"] == "answer_not_allowed"
    assert (
        lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "daytime", "note": "주간 정비 배정"}).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert s["step"]["outcome"] == "daytime" and "주간 처리 요청 · 주간 정비 배정" in f["unresolved"]  # AC-23-C1


def test_lead_handover_answer(api, db):
    """AC-23-C4 · 결정 018 ④: 숙련자 인계 → 답 대기 → 주간 숙련자 배정 + 처리 메모 → 보고서에 기록."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상")
    s = ok(tech, s, type="call_send")
    lc = lead(api)
    row = lc.get("/api/lead/calls").json()["rows"][0]
    assert row["kind"] == "handover" and row["need"] == "점검 소진 · 원인 미상"
    d = lc.get(f"/api/lead/calls/{row['call_id']}").json()
    assert [o["label"] for o in d["options"]] == ["주간 숙련자 배정", "직접 확인"]
    assert lc.post(f"/api/lead/calls/{row['call_id']}/answer", json={"answer": "approved"}).status_code == 409
    assert (
        lc.post(
            f"/api/lead/calls/{row['call_id']}/answer", json={"answer": "assign", "note": "주간 조 숙련자"}
        ).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert (
        s["step"]["kind"] == "report"
        and f["cause"] == "원인 미상"
        and "주간 숙련자 배정 · 주간 조 숙련자" in f["unresolved"]
    )


def test_lead_handover_direct_answer(api, db):
    """AC-23-C4 · 결정 018 ③ ④: 숙련자 인계 → 파트장 '직접 확인' + 처리 메모 → 보고서에 처리 결과."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상")
    s = ok(tech, s, type="call_send")
    assert (s["step"]["kind"], s["step"]["call_status"]) == ("handover", "sent")  # 답 대기
    lc = lead(api)
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    note = "팬 축 직접 확인, 베어링 교체 필요"
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "direct", "note": note}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert (
        s["step"]["kind"] == "report" and f["cause"] == "원인 미상" and f"파트장 직접 확인 · {note}" in f["unresolved"]
    )


def test_lead_grade_change(api, db):
    """AC-25 · R13 · 결정 018 ②: 등급표 전부 · 경고 확인해야 저장 · 기록 · 다음 판정부터 · 열린 지시는 그대로."""
    reset_ops(db)
    lc = lead(api)
    g = lc.get("/api/lead/grades").json()
    with db.connect() as c:
        n = c.execute(text("select count(*) from ops.grade_table")).scalar()
    assert g["total"] == n == len(g["rows"]) and g["changes"] == []
    secs = [r["section"] for r in g["rows"]]
    assert secs == sorted(secs, key=lambda s: [int(x) for x in s.split(".")])
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    assert s["instruction"]["section"] == "4.3"
    w = lc.get("/api/lead/grades/4.3/warning", params={"grade": "C"}).json()
    assert w["direction"] == "raise" and w["head"] == "등급을 올리려 합니다 · 4.3 흡기 필터 육안 점검 A → C"
    assert lc.get("/api/lead/grades/4.6/warning", params={"grade": "A"}).status_code == 422  # 같은 등급
    r = lc.post("/api/lead/grades/4.3", json={"grade": "C"})
    assert r.status_code == 409 and r.json()["detail"] == "warning_not_confirmed"  # AC-25-C3
    assert lc.post("/api/lead/grades/4.3", json={"grade": "C", "confirmed": True}).status_code == 200
    assert lc.post("/api/lead/grades/4.6", json={"grade": "C", "confirmed": True}).status_code == 200
    ch = lc.get("/api/lead/grades").json()["changes"]
    assert {(x["section"], x["before"], x["after"]) for x in ch} == {("4.3", "A", "C"), ("4.6", "A", "C")} and ch[0][
        "by_name"
    ]
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["step"]["kind"] == "instruct" and s["instruction"]["section"] == "4.3"  # AC-25-C4: 열린 지시 그대로
    s = ok(tech, s, type="result", section="4.3", value="정상")
    assert s["step"].get("section") != "4.6" and (s.get("instruction") or {}).get("section") != "4.6"  # AC-25-C1
    w = lc.get("/api/lead/grades/4.3/warning", params={"grade": "A"}).json()
    assert w["direction"] == "lower" and "C → A" in w["head"]
    w = lc.get("/api/lead/grades/4.6/warning", params={"grade": "D"}).json()  # AC-25-C3: D도 경고 · 확인
    assert w["direction"] == "raise" and "C → D" in w["head"] and w["body"]
    assert lc.post("/api/lead/grades/4.6", json={"grade": "D"}).status_code == 409
    assert lc.post("/api/lead/grades/4.6", json={"grade": "D", "confirmed": True}).status_code == 200


def test_lead_grade_change_keeps_open_call(api, db):
    """AC-25-C4: 이미 열린 호출 화면(C 점검 호출 요약)은 그 절 등급을 낮춰도 바뀌지 않는다."""
    reset_ops(db)
    tech, s = start(api, db)
    s = run_until(tech, to_instruct(tech, s), {"call"}, lambda sec: "정상")
    before = (s["step"], s["call"])
    lc = lead(api)
    sec = s["call"]["section"]
    assert lc.post(f"/api/lead/grades/{sec}", json={"grade": "A", "confirmed": True}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert (s["step"], s["call"]) == before


def test_lead_audit_and_filters(api, db):
    """AC-26-C1 · C2 · L06: 시간 역순 · 행동 문구 · 시스템 지시 행 · 설비 · 사용자 · 기간 필터."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    tech2, s2 = start(api, db, equipment="M-03")
    lc = lead(api)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    a = lc.get("/api/lead/audit").json()
    acts = [r["action"] for r in a["rows"]]
    assert acts[0] == "보고서 승인" and "보고서 확정" in acts and "조치 완료 · 4.3.2" in acts
    assert "재가동 확인 · 경계 이내" in acts and "결과 입력 · 4.3 이상" in acts and "잠금 완료 · 1.2" in acts
    sysrow = next(r for r in a["rows"] if r["action"].startswith("지시 · 4.3"))
    assert sysrow["user_name"] == "시스템" and sysrow["app"] == "system" and sysrow["target"] == "M-01 세션"
    assert [r["at"] for r in a["rows"]] == sorted((r["at"] for r in a["rows"]), reverse=True)
    f = lc.get("/api/lead/audit", params={"equipment": "M-03"}).json()["rows"]
    assert f and all(r["equipment_id"] == "M-03" for r in f)
    u = lc.get("/api/lead/audit", params={"user_id": "lead.park"}).json()["rows"]
    assert u and all(r["user_id"] == "lead.park" and r["app"] == "파트장" for r in u)
    assert lc.get("/api/lead/audit", params={"date": "2020-01-01"}).json()["rows"] == []
    assert "M-01" in a["options"]["equipment"] and any(x["user_id"] == "tech.kim" for x in a["options"]["users"])
    reg = next(r for r in a["rows"] if r["action"].startswith("알람 등록"))  # 시스템 행: 알람 등록 · 배정
    assert (
        reg["user_name"] == "시스템"
        and reg["app"] == "system"
        and reg["target"].endswith(" 알람")
        and "배정 " in reg["action"]
    )
    assert all(r["target"].startswith(f"{r['equipment_id']} ") for r in f)


def test_call_required_audit_once_per_call_step(api, db):
    """결정 020 ④(앞 slice 결함 수리): '호출 필요' 감사 행은 호출 단계를 처음 열 때만 — 보냄 · 대기 뒤 다시 열린 단계는 새 행이 아님.

    틀리면 실패: 호출을 보낸 뒤 '호출 필요'가 한 번 더 남거나(S-03 · S-04 동작), 두 번째 호출(다른 절)의 행이 빠지면."""
    reset_ops(db)
    lc = lead(api)
    tech, s = start(api, db)
    s = run_until(tech, to_instruct(tech, s), {"call"}, lambda sec: "정상")
    first = s["step"]["section"]

    def call_rows():
        with db.connect() as c:
            return [
                d["section"]
                for (d,) in c.execute(
                    text(
                        "select detail from ops.audit_log where action = 'call_required' and target = :t order by audit_id"
                    ),
                    {"t": f"session:{s['session_id']}"},
                )
            ]

    assert call_rows() == [first]
    s = ok(tech, s, type="call_send")
    assert s["step"]["call_status"] == "sent" and call_rows() == [first]  # 보낸 뒤 다시 열린 호출 단계는 새 행 아님
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    s = ok(tech, s, type="result", section=first, value="이상")  # 승인된 C 점검 이상 → 조치도 C면 두 번째 호출
    assert s["step"]["kind"] == "call" and s["step"]["section"] != first
    second = s["step"]["section"]
    assert call_rows() == [first, second]
    s = ok(tech, s, type="call_wait")  # 호출하지 않고 대기도 새 행 아님
    assert call_rows() == [first, second]
    acts = [r["action"] for r in lc.get("/api/lead/audit").json()["rows"] if r["action"].startswith("호출 필요")]
    assert len(acts) == 2  # L06 화면 행도 같음


def test_second_call_view_is_not_previous_answer(api, db):
    """앞 slice 결함 수리(S-06 G5 시연 E2E에서 발견): 승인 답 뒤 같은 세션에 새 호출 단계(다른 절)가 열리면
    호출 화면 값은 그 새 호출 — 보냄 전(status none · 답 없음 · 새 요약), 보낸 뒤 답도 그 호출 것만.

    틀리면 실패: 새 호출 단계에 앞 호출의 '승인 후 진행' 답 · 보냄 상태가 붙어 정비자 화면에 '파트장 호출' 버튼이 없음."""
    reset_ops(db)
    lc = lead(api)
    tech, s = start(api, db)
    s = run_until(tech, to_instruct(tech, s), {"call"}, lambda sec: "정상")
    first = s["step"]["section"]
    s = ok(tech, s, type="call_send")
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    s = ok(tech, s, type="result", section=first, value="이상")
    second = s["step"]["section"]
    assert s["step"]["kind"] == "call" and second != first
    c = s["call"]
    assert (c["section"], c["status"], c["answer"]) == (second, "none", None)  # 새 호출 — 앞 답 아님
    assert second in c["summary"]["text"]  # 요약도 새 호출(그 절) 것
    s = ok(tech, s, type="call_send")
    assert (s["call"]["section"], s["call"]["status"], s["call"]["answer"]) == (second, "sent", None)
    cid2 = next(x["call_id"] for x in lc.get("/api/lead/calls").json()["rows"])
    assert cid2 != cid and lc.post(f"/api/lead/calls/{cid2}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["step"]["kind"] in {"action", "action_after_checklist"} and s["call"]["answer"]["answer"] == "approved"


def test_lead_audit_records_review_call_and_grade(api, db):
    """AC-26-C1: 반려 · 호출 · 호출 답 · 등급 변경도 감사 행으로 남고 L06 문구로 읽힌다."""
    reset_ops(db)
    _, s, rid = confirmed_report(api, db)
    lc = lead(api)
    assert (
        lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시 확인"}).status_code
        == 200
    )
    tech2, s2 = start(api, db, equipment="M-03")
    s2 = run_until(tech2, to_instruct(tech2, s2), {"call"}, lambda sec: "정상")
    s2 = ok(tech2, s2, type="call_send")
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "daytime"}).status_code == 200
    assert lc.post("/api/lead/grades/4.6", json={"grade": "C", "confirmed": True}).status_code == 200
    rows = lc.get("/api/lead/audit").json()["rows"]
    acts = [r["action"] for r in rows]
    assert "보고서 반려" in acts and "호출 답 · 주간 처리" in acts
    assert any(x.startswith("파트장 호출 · ") for x in acts) and any(x.startswith("지시 · ") for x in acts)
    g = next(r for r in rows if r["action"].startswith("등급 변경 · 4.6"))
    assert g["action"].endswith("A → C") and g["user_id"] == "lead.park" and g["target"] == "권한 등급"
    # 확인 질문 답(AC-05): '정상' 버튼 + 이상 뜻 메모 → 확인 질문 → '아니요'
    tech3, s3 = start(api, db, equipment="M-02")
    s3 = to_instruct(tech3, s3)
    sec = s3["instruction"]["section"]
    s3 = ok(tech3, s3, type="result", section=sec, value="정상", memo="기름때가 표면 절반 넘게 덮음")
    assert s3["step"]["kind"] == "confirm_question"
    ok(tech3, s3, type="confirm_answer", yes=False)
    acts = [r["action"] for r in lc.get("/api/lead/audit").json()["rows"]]
    assert "확인 질문 답 · 아니요" in acts
    # 기간 필터: 고른 날짜(KST)의 행만, 기본값은 오늘
    from c1a15.api import screen

    today = datetime.now(screen.KST).date()
    d = lc.get("/api/lead/audit", params={"date": today.isoformat()}).json()
    assert d["date"] == today.isoformat() and [r["audit_id"] for r in d["rows"]] == [
        r["audit_id"] for r in lc.get("/api/lead/audit").json()["rows"]
    ]
    assert d["rows"] and all(datetime.fromisoformat(r["at"]).astimezone(screen.KST).date() == today for r in d["rows"])
    prev = (today - timedelta(days=1)).isoformat()
    assert all(
        datetime.fromisoformat(r["at"]).astimezone(screen.KST).date().isoformat() == prev
        for r in lc.get("/api/lead/audit", params={"date": prev}).json()["rows"]
    )


def test_tech_cannot_send_lead_records_and_rejections_audited(api, db):
    """S-03 G6 2차 의견: 정비자가 call_answer · report_rejected를 보내면 422, 확정 뒤 입력 · 체크리스트 부족 거부도 감사."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    assert ev(tech, s, type="call_answer").status_code == 422
    assert ev(tech, s, type="report_rejected").status_code == 422
    assert ev(tech, s, type="report_edit", field="cause", text="x").status_code == 409  # session_closed
    tech2, s2 = start(api, db, equipment="M-03")
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'B' where section_id = '4.3'"))
    s2 = ok(tech2, s2, type="alarm_ack")
    s2 = ok(tech2, s2, type="lock")
    s2 = ok(tech2, s2, type="precheck_done", values="하우징 52 °C")
    assert s2["step"]["kind"] == "instruct_after_checklist"
    assert ev(tech2, s2, type="b_checklist_done", section="4.3", checked=["x"]).status_code == 409
    with db.connect() as c:
        reasons = {r for (r,) in c.execute(text("select detail->>'reason' from ops.audit_log where action = 'reject'"))}
    assert {"session_closed", "checklist_incomplete"} <= reasons
    with db.begin() as c:
        c.execute(
            text(
                "update ops.grade_table g set grade = s.initial_grade from knowledge.manual_sections s where s.section_id = g.section_id"
            )
        )
    _ = register


def test_ac06_report_sentences_never_ungrounded(api, db, monkeypatch):
    """AC-06 L2(P-11 완료): 가짜 provider가 근거 없는 보고서 문장을 내도 초안 · 확정 · 승인 보고서에 남지 않는다(템플릿 대체),
    파트장 수정 문장도 다시 검사해 근거 없으면 승인되지 않는다."""
    from c1a15.api import flow
    from c1a15.llm.base import Draft

    class Ungrounded:
        mode = "x"

        def write(self, facts):
            if facts.kind == "report_sentence":
                return Draft("교체 후 온도 차 12.3 K로 회복, 5.9절 기준 통과")
            return Draft(facts.values.get("template", ""))

    reset_ops(db)
    monkeypatch.setattr(flow, "adapter_for_mode", lambda: Ungrounded())
    _, s, rid = confirmed_report(api, db)
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert "12.3" not in f["action"] + f["cause"] and "5.9" not in f["action"]  # AC-06-C2: 템플릿 문장으로 대체
    assert f["cause"] == "냉각 필터 막힘" and f["action"].startswith("흡기 필터 교체(4.3.2, A)")
    lc = lead(api)
    bad = {"action": f["action"] + " · 교체 후 온도 차 12.3 K로 회복"}
    assert not lc.post(f"/api/lead/reports/{rid}/check", json={"edits": bad}).json()["ok"]
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "edit", "edits": bad}).status_code == 409
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    with db.connect() as c:
        rec = c.execute(
            text("select record from knowledge.history_records where record_id = :i"), {"i": f"R-{rid:04d}"}
        ).scalar()
    assert "12.3" not in rec["narrative"] and "12.3" not in rec["action"]


# ---------------------------------------------------------------- 진행 중 건(EV-002 ES-01: VC-01 ~ VC-07)


def _at(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def _rows(lc):
    r = lc.get("/api/lead/sessions")
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _row(lc, sid):
    return next(x for x in _rows(lc)["rows"] if x["session_id"] == sid)


def _event_at(db, sid, etype):
    with db.connect() as c:
        return c.execute(
            text(
                "select created_at from ops.session_events where session_id = :s and type = :t order by seq desc limit 1"
            ),
            {"s": sid, "t": etype},
        ).scalar()


def _open_call(db, sid):
    with db.connect() as c:
        return c.execute(
            text(
                "select call_id, sent_at from ops.calls where session_id = :s and answer is null order by sent_at desc"
            ),
            {"s": sid},
        ).first()


def test_lead_sessions_role_boundary_and_empty(api, db):
    """VC-01 · VC-02(EV-002 CH-01 · CH-02): 파트장만 읽음(정비자 · 로그인 없음은 거부), 진행 중 없으면 빈 목록 · 0."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", __import__("test_api").TECH_PW)
    assert tech.get("/api/lead/sessions").status_code in {401, 403}
    assert __import__("test_api").client(api, "lead").get("/api/lead/sessions").status_code in {401, 403}
    o = _rows(lead(api))
    assert o["rows"] == [] and o["counts"] == {"in_progress": 0, "on_hold": 0} and _at(o["now"])


def test_lead_sessions_demo02_path_stage_and_hold(api, db):
    """VC-03 · VC-05 · VC-06: DEMO-02 경로(4.3 · 4.6 정상 → 호출 승인 → 4.4 이상 → 조치 호출 승인 → 조치 → 재가동 → 보고서)에서
    한 행의 stage · step · hold 순서(Preview v2 표), 확정하면 빠지고 반려되면 다시. GET은 쓰기 0, 시각은 운영 기록."""
    reset_ops(db)
    lc = lead(api)
    t0 = datetime.now(__import__("datetime").UTC)
    tech, s = start(api, db, equipment="M-03")
    t1 = datetime.now(__import__("datetime").UTC)
    sid = s["session_id"]

    def counts():
        with db.connect() as c:
            return [
                c.execute(text(q)).scalar()
                for q in (
                    "select count(*) from ops.session_events",
                    "select coalesce(max(seq), 0) from ops.session_events",
                    "select count(*) from ops.diag_sessions",
                    "select count(*) from ops.calls",
                    "select count(*) from ops.audit_log",
                    "select coalesce(max(change_seq), 0) from ops.alarms",
                )
            ]

    before = counts()
    row = _row(lc, sid)
    assert counts() == before  # VC-05: 읽기만
    with db.connect() as c:
        created, occurred = c.execute(
            text(
                "select d.created_at, a.occurred_at from ops.diag_sessions d join ops.alarms a using (alarm_id) where d.session_id = :s"
            ),
            {"s": sid},
        ).one()
    # VC-06: 알람 시각 = 등록 API가 넣은 지금 시각(각본 · AI4I 행 시각 아님), 시작 · 마지막 입력 = 운영 기록
    assert t0 <= _at(row["alarm_at"]) <= t1 and _at(row["alarm_at"]) == occurred
    assert _at(row["started_at"]) == created and _at(row["last_input_at"]) == _event_at(db, sid, "preview_shown")
    o = _rows(lc)
    r0 = next(x for x in o["rows"] if x["session_id"] == sid)
    assert r0["elapsed_min"] == int((_at(o["now"]) - created).total_seconds() // 60)
    assert r0["hold"]["minutes"] == int((_at(o["now"]) - _at(r0["hold"]["since"])).total_seconds() // 60)
    # 잠금 전(PR-04): 단계 1.2만, 다음 점검 절 · 본문 없음
    assert (row["stage"], row["stage_label"], row["step_no"], row["step_kind"], row["section"]) == (
        "before_lock",
        "잠금 전",
        None,
        "lock",
        "1.2",
    )
    assert row["hold"]["kind"] == "lock" and _at(row["hold"]["since"]) == created and row["hold"]["call_id"] is None
    assert (
        row["equipment_id"] == "M-03" and row["group_code"] == "CL" and row["technician_name"] and row["check_no"] == 0
    )
    assert not {"text", "instruction", "lock_instruction"} & set(row)
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    row = _row(lc, sid)
    assert (row["stage"], row["step_kind"], row["hold"]) == ("check", "precheck", None)
    s = ok(tech, s, type="precheck_done", values="하우징 52 °C")
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    first = s["step"]["section"]
    row = _row(lc, sid)
    assert (row["stage"], row["step_no"], row["step_kind"], row["section"], row["grade"]) == (
        "check",
        3,
        "call",
        first,
        "C",
    )
    assert row["hold"]["kind"] == "call_unsent" and _at(row["hold"]["since"]) == _event_at(db, sid, "step_opened")
    s = ok(tech, s, type="call_send")
    before = counts()
    row, call = _row(lc, sid), _open_call(db, sid)
    assert counts() == before  # VC-05: ops.calls를 읽는 상태에서도 쓰기 0
    assert row["hold"]["kind"] == "call_sent" and row["hold"]["call_id"] == call.call_id
    assert _at(row["hold"]["since"]) == call.sent_at and row["hold"]["label"] == "파트장 답 대기"
    assert lc.post(f"/api/lead/calls/{call.call_id}/answer", json={"answer": "approved"}).status_code == 200
    row = _row(lc, sid)
    assert (row["stage"], row["step_kind"], row["section"], row["hold"]) == ("check", "instruct", first, None)
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    s = ok(tech, s, type="result", section=first, value="이상")
    second = s["step"]["section"]
    row = _row(lc, sid)
    assert (row["stage"], row["step_kind"], row["section"], row["hold"]["kind"]) == (
        "check",
        "call",
        second,
        "call_unsent",
    )
    s = ok(tech, s, type="call_send")
    call = _open_call(db, sid)
    assert _row(lc, sid)["hold"]["kind"] == "call_sent"
    assert lc.post(f"/api/lead/calls/{call.call_id}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    row = _row(lc, sid)
    assert row["stage"] == "action" and row["step_no"] == 4 and row["step_kind"] in {"action", "action_after_checklist"}
    if s["step"]["kind"] == "action_after_checklist":
        s = ok(tech, s, type="b_checklist_done", section=second, checked=s["instruction"]["checklist"])
    s = ok(tech, s, type="action_done", section=second)
    assert (_row(lc, sid)["stage"], _row(lc, sid)["step_kind"]) == ("action", "restart_check")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    assert (_row(lc, sid)["stage"], _row(lc, sid)["step_kind"]) == ("action", "restart_value")
    s = ok(tech, s, type="restart_result", outcome="within")
    row = _row(lc, sid)
    assert (row["stage"], row["step_no"], row["step_kind"], row["hold"], row["report_rejected"]) == (
        "report",
        5,
        "report",
        None,
        False,
    )
    s = ok(tech, s, type="report_confirm")
    assert all(x["session_id"] != sid for x in _rows(lc)["rows"])  # 확정 → 승인 대기(L02)로
    rid = lc.get("/api/lead/overview").json()["rows"][0]["report_id"]
    assert (
        lc.post(
            f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "점검 경로 메모를 더 적어 주세요"}
        ).status_code
        == 200
    )
    row = _row(lc, sid)
    assert (row["stage"], row["report_rejected"]) == ("report", True)


def test_lead_sessions_order_hold_first(api, db):
    """VC-04: 대기 있는 행 먼저(since 오래된 순), 그다음 시작이 이른 순."""
    reset_ops(db)
    lc = lead(api)
    _, a = start(api, db, equipment="M-01")  # 잠금 전(hold lock)
    tech_b, b = start(api, db, equipment="M-02")
    to_instruct(tech_b, b)  # 대기 없음
    _, c3 = start(api, db, equipment="M-03")  # 잠금 전(hold lock, a보다 늦음)
    o = _rows(lc)
    assert [x["session_id"] for x in o["rows"]] == [a["session_id"], c3["session_id"], b["session_id"]]
    assert o["counts"] == {"in_progress": 3, "on_hold": 2}
    # 먼저 시작한 b가 나중에 호출을 보내면(since = sent_at) 나중에 시작했지만 일찍부터 기다린 a · c3 뒤에 선다
    b = run_until(tech_b, tech_b.get(f"/api/tech/sessions/{b['session_id']}").json(), {"call"}, lambda sec: "정상")
    ok(tech_b, b, type="call_send")
    o = _rows(lc)
    assert [x["session_id"] for x in o["rows"]] == [a["session_id"], c3["session_id"], b["session_id"]]
    _, d = start(api, db, equipment="M-04")  # 가장 늦게 시작 · 가장 늦게 기다림(잠금 전)
    o = _rows(lc)
    assert [x["session_id"] for x in o["rows"]] == [a["session_id"], c3["session_id"], b["session_id"], d["session_id"]]
    assert [x["hold"]["kind"] for x in o["rows"]] == ["lock", "lock", "call_sent", "lock"]


def test_lead_sessions_other_holds(api, db):
    """VC-07: 호출하지 않고 대기 · 숙련자 인계 보냄 · 확인 질문 대기 · 재가동 뒤 인계 — hold 종류 · since 출처 · 단계 번호(앱 규칙 3)."""
    reset_ops(db)
    lc = lead(api)
    # ⓐ 호출 단계에서 '호출하지 않고 대기'
    tech, s = start(api, db, equipment="M-01")
    s = run_until(tech, to_instruct(tech, s), {"call"}, lambda sec: "정상")
    s = ok(tech, s, type="call_wait")
    row = _row(lc, s["session_id"])
    assert (row["stage"], row["step_no"], row["hold"]["kind"]) == ("check", 3, "call_waiting")
    assert _at(row["hold"]["since"]) == _event_at(db, s["session_id"], "call_wait") and row["hold"]["call_id"] is None
    # ⓑ 확인 불가 반복 → 숙련자 인계 보냄(test_lead_handover_answer와 같은 길)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech2, s2 = start(api, db, equipment="M-02")
    s2 = run_until(
        tech2, to_instruct(tech2, s2), {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상"
    )
    assert _row(lc, s2["session_id"])["hold"]["kind"] == "call_unsent"
    s2 = ok(tech2, s2, type="call_send")
    row, call = _row(lc, s2["session_id"]), _open_call(db, s2["session_id"])
    assert (row["stage"], row["step_no"], row["step_kind"], row["hold"]["kind"]) == (
        "check",
        3,
        "handover",
        "handover_sent",
    )
    assert _at(row["hold"]["since"]) == call.sent_at and row["hold"]["call_id"] == call.call_id
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'C' where section_id = '4.4'"))
    # ⓒ 버튼과 어긋난 메모 → 확인 질문(정비자 답 대기)
    tech3, s3 = start(api, db, equipment="M-03")
    s3 = to_instruct(tech3, s3)
    s3 = ok(
        tech3,
        s3,
        type="result",
        section=s3["instruction"]["section"],
        value="정상",
        memo="기름때가 표면 절반 넘게 덮음",
    )
    assert s3["step"]["kind"] == "confirm_question"
    row = _row(lc, s3["session_id"])
    assert (row["stage"], row["step_no"], row["hold"]["kind"]) == ("check", 3, "confirm_pending")
    assert _at(row["hold"]["since"]) == _event_at(db, s3["session_id"], "result")
    # ⓓ 조치 · 재가동 뒤 '여전히 밖' → 숙련자 인계: 앱 규칙대로 점검(3)
    tech4, s4 = start(api, db, equipment="M-04")
    s4 = to_instruct(tech4, s4)
    s4 = ok(tech4, s4, type="result", section="4.3", value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
    s4 = ok(tech4, s4, type="action_done", section="4.3.2")
    s4 = ok(tech4, s4, type="restart", checked=s4["restart"]["checklist"])
    s4 = ok(tech4, s4, type="restart_result", outcome="outside")
    assert s4["step"]["kind"] == "handover"
    row = _row(lc, s4["session_id"])
    assert (row["stage"], row["step_no"], row["hold"]["kind"]) == ("check", 3, "call_unsent")
    s4 = ok(tech4, s4, type="call_send")
    assert _row(lc, s4["session_id"])["hold"]["kind"] == "handover_sent"


def test_d529_audit_screen_no_english_for_ml_no_alarm_and_old_candidate_rows(api, db):
    """D-529: 감사 기록 화면 — 알람 없음(alarm_rejected) · 옛 후보 행(기록 · 올림 · 버림)의 행동 · 대상 칸에 영어 이름 0."""
    reset_ops(db)
    rows = [
        ("system", "alarm_rejected", "event:C1A-Eabc", {"reason": "low_confidence", "event_id": "C1A-Eabc"}),
        ("system", "alarm_candidate", "candidate:7", {"reason": "rnf", "event_id": "C1A-Edef"}),
        ("lead.park", "candidate_promote", "candidate:7", {"alarm_id": 1, "group": "CL", "reason": "rnf"}),
        ("lead.park", "candidate_dismiss", "candidate:8", {"reason": "model_failure"}),
    ]
    with db.begin() as c:
        for actor, action, target, detail in rows:
            c.execute(
                text(
                    "insert into ops.audit_log (actor, action, equipment_id, target, detail) "
                    "values (:a, :b, 'M-02', :t, cast(:d as jsonb))"
                ),
                {"a": actor, "b": action, "t": target, "d": json.dumps(detail)},
            )
    got = {(r["action"], r["target"]) for r in lead(api).get("/api/lead/audit").json()["rows"]}
    assert got == {
        ("알람 없음 · 유형 확신 낮음", "M-02 알람 판정"),
        ("후보 기록 · 유형 RNF", "M-02 후보"),
        ("후보 올림", "M-02 후보"),
        ("후보 버림", "M-02 후보"),
    }
