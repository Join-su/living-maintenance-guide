# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""P-14 정정 API 통합 시험(시험 DB): 정정 덧붙임 · 감사 · 다시 판정 · 정정 호출 · 보낸 호출에 붙임 · 거부 · 메모 덧붙이기.

값 정의는 사용자 결정 019(sources/intent/019.md). 시험마다 '틀리면 실패하는 것'을 주석으로 적는다(점검표 C7).
"""

import pytest
from sqlalchemy import text

from test_api import (  # noqa: F401 — fixture(api · db)는 이름으로 쓴다
    LEAD_PW,
    api,
    db,
    login,
    ok,
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


def fix(tech, s, **body):
    return tech.post(f"/api/tech/sessions/{s['session_id']}/corrections", json=body)


def note(tech, s, **body):
    return tech.post(f"/api/tech/sessions/{s['session_id']}/notes", json=body)


def seq_of(db, sid, etype, section=None):
    q = "select seq from ops.session_events where session_id = :s and type = :t"
    q += " and payload->>'section' = :sec" if section else ""
    with db.connect() as c:
        return c.execute(text(q + " order by seq desc limit 1"), {"s": sid, "t": etype, "sec": section}).scalar()


def count(db, sql, **kw):
    with db.connect() as c:
        return c.execute(text(sql), kw).scalar()


def expected_step(api_obj, events):
    """R4 · R5를 처음부터 다시 적용한 다음 단계(정정 결과로 같은 기록을 새로 만든 것과 같아야 함 — AC-46-C1)."""
    from c1a15.api import deps
    from c1a15.engine import rules, session

    k = deps.manual()
    with deps.engine().connect() as c:
        g, minutes = deps.grades(c)
        sim = [rules.Similar(x["record_id"], x["cause"], x["score"]) for x in api_obj]
    return session.decide(session.fold(k, "CL", sim, events), k, g, minutes)


def similar(db, sid):
    with db.connect() as c:
        return c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()


def test_correction_rejudges_with_confirm_and_voids_later(api, db):
    """AC-46-C1 ~ C5 · AC-45-C3 · C4: 결과 정정 → 열린 지시가 바뀌면 확인 필요 → 확인 뒤 R4 · R5 다시 적용 · 뒤 기록 무효 · 정정 n회."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    assert s["instruction"]["section"] == "4.3"
    s = ok(tech, s, type="result", section="4.3", value="정상", memo="필터 깨끗함")
    opened = s["instruction"]["section"]  # 지금 열린 지시(보내지 않은 입력이 있을 수 있음)
    sid, target = s["session_id"], seq_of(db, s["session_id"], "result", "4.3")
    before_events = count(db, "select count(*) from ops.session_events where session_id = :s", s=sid)
    r = fix(tech, s, target_seq=target, value="이상", memo="필터 뒷면 기름때", reason="뒷면을 못 봄")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "confirm_required"  # AC-46-C3: 확인 없이 저장 안 됨
    d = r.json()["detail"]
    assert (
        d["current"]["section"] == opened and d["next"]["kind"] == "action" and d["target"]["after"]["value"] == "이상"
    )
    assert (
        count(db, "select count(*) from ops.session_events where session_id = :s", s=sid) == before_events
    )  # AC-46-C4: 그대로
    r = fix(tech, s, target_seq=target, value="이상", memo="필터 뒷면 기름때", reason="뒷면을 못 봄", confirmed=True)
    assert r.status_code == 200, r.text
    s = r.json()
    # AC-46-C1: 정정한 결과로 처음부터 다시 적용한 판정과 같다(다시 판정하지 않으면 지시가 opened 그대로라 실패)
    exp = expected_step(
        similar(db, sid),
        [{"type": "lock"}, {"type": "precheck_done"}, {"type": "result", "section": "4.3", "value": "이상"}],
    )
    assert (s["step"]["kind"], s["step"]["section"]) == (exp["kind"], exp["section"]) == ("action", "4.3.2")
    entry = {p["title"]: p for p in s["path"]}["흡기 필터 육안 점검"]
    assert "정정 1회" in entry["detail"] and "정상 → 이상" in entry["detail"] and entry["status"] == "bad"
    assert s["correction"]["before"] == "정상" and s["correction"]["after"] == "이상"  # T08 / corrected_path 배너
    with db.connect() as c:
        raw = c.execute(
            text("select payload from ops.session_events where session_id = :s and seq = :q"), {"s": sid, "q": target}
        ).scalar()
        au = c.execute(text("select detail from ops.audit_log where action = 'correction'")).scalar()
    assert raw["value"] == "정상" and raw["memo"] == "필터 깨끗함"  # 원래 값은 업무 기록에 그대로(덧붙임)
    assert (
        au["before"] == {"value": "정상", "memo": "필터 깨끗함"}
        and au["after"]["value"] == "이상"
        and au["reason"] == "뒷면을 못 봄"
        and au["by"] == "tech.kim"
    )  # AC-45-C3
    r = fix(
        tech, s, target_seq=target, memo="필터 뒷면 절반 넘게 기름때", reason="메모 보충"
    )  # AC-45-C4: 다시 정정 · 메모만
    assert r.status_code == 200 and r.json()["step"] == s["step"]  # AC-46-C5: 메모만 정정하면 다음 단계 그대로
    assert "정정 2회" in {p["title"]: p for p in r.json()["path"]}["흡기 필터 육안 점검"]["detail"]
    # 뒤에 보낸 결과가 있으면 그 기록은 지우지 않고 '정정으로 무효'(AC-46-C2) — 지금 단계가 지시가 아니면 확인 창 없음
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="정상")
    nxt = s["instruction"]["section"]
    s = ok(tech, s, type="result", section=nxt, value="정상")
    r = fix(tech, s, target_seq=seq_of(db, s["session_id"], "result", "4.3"), value="이상", reason="뒷면")
    assert r.status_code == 200, r.text
    later = {p["title"]: p for p in r.json()["path"]}
    nxt_title = next(
        t
        for t in later
        if t not in {"잠금 확인", "흡기 필터 육안 점검"} and later[t]["at"] and later[t]["detail"] == "정상"
    )
    assert later[nxt_title]["status"] == "void" and r.json()["step"]["kind"] == "action"


def test_correction_rejects_reason_closed_and_non_correctable(api, db):
    """AC-45-C2 · AC-48: 사유 없음 · 정정하지 않는 기록 · 확정 세션(승인 대기 · 승인) 거부 + 감사, 반려로 다시 열리면 다시 정정."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    sid = s["session_id"]
    seqs = {
        t: seq_of(db, sid, t)
        for t in ("lock", "precheck_done", "result", "action_done", "restart", "restart_result", "step_opened")
    }
    r = fix(tech, s, target_seq=seqs["result"], value="정상", reason="  ")
    assert r.status_code == 409 and r.json()["detail"] == "reason_required"  # AC-45-C2
    for t in ("lock", "action_done", "restart", "restart_result", "step_opened"):  # AC-48-C1: 정정하지 않는 기록
        r = fix(tech, s, target_seq=seqs[t], value="정상", reason="x")
        assert r.status_code == 409 and r.json()["detail"] == "not_correctable", t
    assert (
        note(tech, s, target_seq=seqs["result"], memo="x").json()["detail"] == "not_note_target"
    )  # 결과는 정정 대상(메모 덧붙이기 아님)
    assert note(tech, s, target_seq=seqs["lock"], memo="  ").json()["detail"] == "memo_required"
    s = ok(tech, s, type="report_confirm")
    lc = lead(api)
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=sid)

    def closed(tech_c, sess, seq_map, label):  # AC-48-C2: 확정 세션은 정정 · 메모 덧붙이기 모두 거부(생성자 해석 ③)
        for t2 in ("result", "precheck_done", "lock"):
            assert (
                fix(tech_c, sess, target_seq=seq_map[t2], value="정상", values="x", reason="x").json()["detail"]
                == "session_closed"
            ), (label, t2)
            assert note(tech_c, sess, target_seq=seq_map[t2], memo="x").json()["detail"] == "session_closed", (
                label,
                t2,
            )

    closed(tech, s, seqs, "승인 대기")
    # 승인한 세션(다른 설비)
    tech2, s2 = start(api, db, equipment="M-03")
    s2 = to_instruct(tech2, s2)
    s2 = ok(tech2, s2, type="result", section="4.3", value="이상", memo="기름때")
    s2 = ok(tech2, s2, type="action_done", section="4.3.2")
    s2 = ok(tech2, s2, type="restart", checked=s2["restart"]["checklist"])
    s2 = ok(tech2, s2, type="restart_result", outcome="within")
    s2 = ok(tech2, s2, type="report_confirm")
    rid2 = count(db, "select report_id from ops.reports where session_id = :s", s=s2["session_id"])
    assert lc.post(f"/api/lead/reports/{rid2}/decision", json={"decision": "approve"}).status_code == 200
    closed(tech2, s2, {t2: seq_of(db, s2["session_id"], t2) for t2 in ("result", "precheck_done", "lock")}, "승인")
    assert (
        lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시 확인"}).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{sid}").json()  # AC-48-C3: 반려로 다시 열린 세션은 다시 정정 · 덧붙이기
    assert note(tech, s, target_seq=seqs["lock"], memo="잠금 표지 2번 패널에 추가로 걺").status_code == 200
    r = fix(tech, s, target_seq=seqs["precheck_done"], values="하우징 53 °C", reason="값 잘못 읽음")
    assert r.status_code == 200 and r.json()["step"]["kind"] == "report"  # 측정값만 → 다음 단계 그대로
    reasons = {
        x for (x,) in db.connect().execute(text("select detail->>'reason' from ops.audit_log where action = 'reject'"))
    }
    assert {
        "reason_required",
        "not_correctable",
        "session_closed",
        "not_note_target",
        "memo_required",
    } <= reasons  # 거부 감사(별도 transaction)


def test_non_correctable_kinds_by_session_state(api, db):
    """P-14 verifier 기록 종류 7 × 세션 상태 · AC-48-C1 ~ C3(서버 부분): 잠금 · 조치 · 재가동(경계 확인 포함) · 파트장 호출 ·
    확인 질문 답 · B 시작 전 체크 · 숙련자 인계 기록은 진행 · 반려로 다시 열림에서 정정 거부(not_correctable) · 메모 덧붙이기는 됨,
    승인 대기 · 승인에서는 둘 다 거부(session_closed). 틀리면 실패: 어느 종류든 정정이 저장되거나, 열린 세션에서 메모가 거부되거나,
    확정 세션에서 메모가 저장되면."""
    reset_ops(db)
    lc = lead(api)
    # 파트장 호출 세션(지시가 C 점검에 이르러 호출 → 주간 처리 답 → 보고서)
    tc, sc = start(api, db, user="tech.lee", equipment="M-02")
    sc = ok(tc, run_until(tc, to_instruct(tc, sc), {"call"}, lambda sec: "정상"), type="call_send")
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "daytime"}).status_code == 200
    # 숙련자 인계 세션(4.4 A — 확인 불가가 이어져 인계 → 주간 숙련자 배정 답 → 보고서)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    th, sh = start(api, db, user="tech.choi", equipment="M-03")
    sh = run_until(th, to_instruct(th, sh), {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상")
    sh = ok(th, sh, type="call_send")
    hid = next(x["call_id"] for x in lc.get("/api/lead/calls").json()["rows"] if x["kind"] == "handover")
    assert lc.post(f"/api/lead/calls/{hid}/answer", json={"answer": "assign"}).status_code == 200
    # 확인 질문 답 · B 시작 전 체크 · 조치 · 재가동 세션(4.3.2 B: '정상' + 이상 뜻 메모 → 확인 질문 '네' → B 조치)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'B' where section_id = '4.3.2'"))
    ta, sa = start(api, db)
    sa = ok(ta, to_instruct(ta, sa), type="result", section="4.3", value="정상", memo="기름때가 표면 절반 넘게 덮음")
    assert sa["step"]["kind"] == "confirm_question"
    sa = ok(ta, sa, type="confirm_answer", yes=True)
    assert sa["step"]["kind"] == "action_after_checklist"
    sa = ok(ta, sa, type="b_checklist_done", section="4.3.2", checked=sa["action"]["checklist"])
    sa = ok(ta, sa, type="action_done", section="4.3.2")
    sa = ok(ta, sa, type="restart", checked=sa["restart"]["checklist"])
    sa = ok(ta, sa, type="restart_result", outcome="within")
    sessions = {"A": (ta, sa), "C": (tc, sc), "H": (th, sh)}
    for key, (t_, s_) in sessions.items():
        sessions[key] = (t_, t_.get(f"/api/tech/sessions/{s_['session_id']}").json())
        assert sessions[key][1]["step"]["kind"] == "report", key
    kinds = {  # 기록 종류 7(재가동은 체크리스트 · 경계 확인 두 기록)
        "잠금": ("A", "lock", None),
        "확인 질문 답": ("A", "confirm_answer", None),
        "B 시작 전 체크": ("A", "b_checklist_done", None),
        "조치": ("A", "action_done", None),
        "재가동": ("A", "restart", None),
        "재가동 경계 확인": ("A", "restart_result", None),
        "파트장 호출": ("C", "call_sent", "approval"),
        "숙련자 인계": ("H", "call_sent", "handover"),
    }
    seqs = {}
    for name, (key, etype, call_kind) in kinds.items():
        q = "select seq from ops.session_events where session_id = :s and type = :t"
        q += " and payload->>'kind' = :k" if call_kind else ""
        with db.connect() as c:
            rows = c.execute(text(q), {"s": sessions[key][1]["session_id"], "t": etype, "k": call_kind}).all()
        assert len(rows) == 1, name
        seqs[name] = rows[0][0]

    def check(state, open_):
        for name, (key, _, _) in kinds.items():
            t_, s_ = sessions[key]
            r = fix(t_, s_, target_seq=seqs[name], value="이상", values="x", memo="x", reason="다시 봄")
            assert r.status_code == 409 and r.json()["detail"] == ("not_correctable" if open_ else "session_closed"), (
                state,
                name,
            )
            r = note(t_, s_, target_seq=seqs[name], memo=f"{state} 메모")
            if open_:
                assert r.status_code == 200, (state, name, r.text[:200])
                sessions[key] = (t_, r.json())
            else:
                assert r.status_code == 409 and r.json()["detail"] == "session_closed", (state, name)

    def history_flags(key):
        t_, s_ = sessions[key]
        h = t_.get(f"/api/tech/sessions/{s_['session_id']}/history").json()
        return [(r["kind"], r["correctable"], r["notable"]) for st in h["steps"] for r in st["records"]], h

    check("진행", True)
    flags, h = history_flags("A")
    assert ("lock", False, True) in flags and ("action", False, True) in flags  # 지난 화면: 메모 덧붙이기만
    c43 = next(r for st in h["steps"] for r in st["records"] if r["kind"] == "check")
    assert c43["correctable"] and c43["value"] == "정상"  # 결과는 정정 대상(확인 질문 답과 별개)
    for key in ("C", "H"):
        assert any(k == "call" and not cor and nt for k, cor, nt in history_flags(key)[0]), key
    rids = {}
    for key, (t_, s_) in sessions.items():
        sessions[key] = (t_, ok(t_, s_, type="report_confirm"))
        rids[key] = count(db, "select report_id from ops.reports where session_id = :s", s=s_["session_id"])
    check("승인 대기", False)
    for key, rid in rids.items():
        assert (
            lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시 확인"}).status_code
            == 200
        )
        t_, s_ = sessions[key]
        sessions[key] = (t_, t_.get(f"/api/tech/sessions/{s_['session_id']}").json())
    check("반려", True)  # AC-48-C3: 다시 열린 세션은 메모 덧붙이기가 되고, 정정하지 않는 기록은 그대로 정정 불가
    for key, (t_, s_) in sessions.items():
        sessions[key] = (t_, ok(t_, s_, type="report_confirm"))
        assert lc.post(f"/api/lead/reports/{rids[key]}/decision", json={"decision": "approve"}).status_code == 200
    check("승인", False)
    assert all(not cor and not nt for _, cor, nt in history_flags("A")[0])  # 확정 세션 지난 화면: 정정 · 메모 수단 없음


def original_kept(tech, db, s, target_seq, before):
    """'원래 값 남음' 검사(ADV-08): 업무 기록의 원래 행이 그대로이고, 지난 화면 정정 이력의 처음 값이 원래 값이다."""
    with db.connect() as c:
        raw = c.execute(
            text("select payload from ops.session_events where session_id = :s and seq = :q"),
            {"s": s["session_id"], "q": target_seq},
        ).scalar()
    h = tech.get(f"/api/tech/sessions/{s['session_id']}/history").json()
    rec = next(r for st in h["steps"] for r in st["records"] if r["seq"] == target_seq)
    return (
        all(raw.get(k) == v for k, v in before.items())
        and bool(rec["corrections"])
        and rec["corrections"][0]["before"] == before
    )


def test_adv08_overwrite_defect_is_caught(api, db, monkeypatch):
    """ADV-08: 정정 불가 기록 · 확정 세션 요청 거부는 test_non_correctable_kinds_by_session_state(기록 종류 7 × 세션 상태)와
    test_correction_rejects_reason_closed_and_non_correctable. 여기서는 원래 값 덮어쓰기: 지금 저장 계층에서는 '원래 값 남음' 검사가
    통과하고, 정정을 덧붙이지 않고 원래 기록을 고쳐 쓰는 결함 사본에서는 같은 검사가 실패해야 한다."""
    import json

    from c1a15.api import sessions

    before = {"value": "정상", "memo": "필터 깨끗함"}

    def corrected_session(equipment, user):
        tech, s = start(api, db, user=user, equipment=equipment)
        s = ok(tech, to_instruct(tech, s), type="result", section="4.3", **before)
        target = seq_of(db, s["session_id"], "result", "4.3")
        r = fix(tech, s, target_seq=target, memo="필터 뒷면 기름때", reason="뒷면을 못 봄")  # 메모만 → 다음 단계 그대로
        assert r.status_code == 200, r.text[:200]
        return tech, r.json(), target

    reset_ops(db)
    tech, s, target = corrected_session("M-01", "tech.kim")
    assert original_kept(tech, db, s, target, before)  # 지금 저장 계층: 원래 값 남음
    real = sessions._store

    def overwrite(c, sid, seq, etype, payload, actor):  # 결함 사본: 정정을 덧붙이지 않고 원래 기록을 고쳐 씀
        if etype != "correction":
            return real(c, sid, seq, etype, payload, actor)
        c.execute(
            text(
                "update ops.session_events set payload = payload || cast(:p as jsonb) where session_id = :s and seq = :q"
            ),
            {"p": json.dumps(payload["after"], ensure_ascii=False), "s": sid, "q": payload["target_seq"]},
        )

    monkeypatch.setattr(sessions, "_store", overwrite)
    tech2, s2, target2 = corrected_session("M-03", "tech.lee")
    assert not original_kept(tech2, db, s2, target2, before)  # 결함 사본은 검사가 잡는다(fail closed)


def test_correction_after_action_opens_correction_call(api, db):
    """AC-47-C1 · 결정 012 ② · 019 ① ⑧: 조치 뒤 결과 정정 → 다시 판정 대신 정정 호출 → 승인이면 하던 단계로, 주간 처리 · 직접 확인이면 원인 미상 · 조치 그대로."""
    reset_ops(db)
    lc = lead(api)
    where = {"approved": ("tech.kim", "M-01"), "daytime": ("tech.kim", "M-03"), "direct": ("tech.lee", "M-02")}
    label = {"daytime": "주간 처리 요청", "direct": "파트장 직접 확인"}
    for answer, (user, equipment) in where.items():
        tech, s = start(api, db, user=user, equipment=equipment)
        s = to_instruct(tech, s)
        s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
        s = ok(tech, s, type="action_done", section="4.3.2")
        assert s["step"]["kind"] == "restart_check"
        sid, target = s["session_id"], seq_of(db, s["session_id"], "result", "4.3")
        s = fix(tech, s, target_seq=target, value="정상", reason="다시 보니 깨끗함").json()
        # 다시 판정했다면 4.3 정상 → 다른 점검 지시가 나와 실패한다
        assert s["step"]["kind"] == "call" and s["step"]["corrected"] and s["call"]["correction"]["before"] == "이상"
        assert s["call"]["actions_done"][0]["section"] == "4.3.2" and s["call"]["candidates"] == []
        assert {d["section"]: d.get("note") for d in s["call"]["checks_done"]}["4.3"] == "정정 1회, 원래 이상"
        assert "정정" in s["call"]["summary"]["text"] and "4.3.2" in s["call"]["summary"]["text"]
        s = ok(tech, s, type="call_send")
        assert s["call"]["status"] == "sent"
        row = next(x for x in lc.get("/api/lead/calls").json()["rows"] if x["equipment_id"] == equipment)
        assert row["kind"] == "approval" and row["need"] == "정정 확인 · 4.3 흡기 필터 육안 점검(이상 → 정상)"  # 019 ⑧
        d = lc.get(f"/api/lead/calls/{row['call_id']}").json()
        assert {o["value"]: o["hint"] for o in d["options"]}["approved"] == "정비자가 정정 때 하던 단계로 이어서 합니다"
        assert (
            lc.post(
                f"/api/lead/calls/{row['call_id']}/answer", json={"answer": answer, "note": "필터 재확인"}
            ).status_code
            == 200
        )
        s = tech.get(f"/api/tech/sessions/{sid}").json()
        if answer == "approved":  # 정정 때 하던 단계(재가동 확인)로 이어서 — 새 작업 없음
            assert s["step"]["kind"] == "restart_check"
        else:  # 019 ①(주간 처리 · 직접 확인): 원인 미상 · 이미 한 조치는 그대로 · 정정 내용과 답 · 메모는 미확인 · 요청
            f = {x["key"]: x["text"] for x in s["report"]["fields"]}
            assert s["step"]["kind"] == "report" and f["cause"] == "원인 미상" and "흡기 필터 교체(4.3.2" in f["action"]
            assert (
                f"정정 확인 · 4.3 흡기 필터 육안 점검(이상 → 정상) · {label[answer]} · 필터 재확인" in f["unresolved"]
            )
            assert s["call"]["correction"]["before"] == "이상"  # 답 화면(T10 / corrected_ans_*)도 정정 값


def test_correction_attaches_to_sent_handover_and_answered_calls(api, db):
    """AC-47-C2 · 결정 019 ② ③: 보낸 호출(숙련자 인계 포함) 뒤 정정 → 새 호출 · 다시 판정 없이 정정 상자, 답한 호출 뒤 → 기록만."""
    reset_ops(db)
    lc = lead(api)
    # 숙련자 인계 답 대기 중(019 ②)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech, s = start(api, db)
    s = run_until(
        tech, to_instruct(tech, s), {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상"
    )
    s = ok(tech, s, type="call_send")
    sid = s["session_id"]
    target = seq_of(db, sid, "result", "4.6")
    calls_before = count(db, "select count(*) from ops.calls where session_id = :s", s=sid)
    s2 = fix(tech, s, target_seq=target, value="이상", reason="설정값 다시 읽음").json()
    assert (s2["step"]["kind"], s2["step"]["call_status"]) == (
        "handover",
        "sent",
    )  # 다시 판정하지 않음(했다면 원인 확정 → 조치)
    assert count(db, "select count(*) from ops.calls where session_id = :s", s=sid) == calls_before  # 새 호출 없음
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    box = lc.get(f"/api/lead/calls/{cid}").json()["corrections"]
    assert (
        len(box) == 1
        and box[0]["section"] == "4.6"
        and box[0]["before"]["value"] == "정상"
        and box[0]["after"]["value"] == "이상"
        and box[0]["reason"] == "설정값 다시 읽음"
    )
    # 답한 호출 뒤(019 ③): 승인 답으로 C 점검(4.4)을 하던 중 그보다 앞선 4.3 정정
    reset_ops(db)
    lc = lead(api)  # 초기화가 로그인 세션도 지움
    tech, s = start(api, db)
    s = run_until(tech, to_instruct(tech, s), {"call"}, lambda sec: "정상")
    sec = s["call"]["section"]
    s = ok(tech, s, type="call_send")
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "approved"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["instruction"]["section"] == sec
    target = seq_of(db, s["session_id"], "result", "4.3")
    s2 = fix(tech, s, target_seq=target, value="이상", reason="뒷면 기름때").json()
    assert s2["step"] == s["step"] and s2["instruction"]["section"] == sec  # 다시 판정 · 다시 답 받기 없음
    with db.connect() as c:
        cors = c.execute(text("select corrections from ops.calls where call_id = :c"), {"c": cid}).scalar()
        answered = c.execute(text("select answer is not null from ops.calls where call_id = :c"), {"c": cid}).scalar()
    assert len(cors) == 1 and cors[0]["answered"] and answered  # 그 호출 기록에 붙음 · 답은 그대로
    assert any(
        r["action"].startswith("정정 · 4.3 결과 정상 → 이상") for r in lc.get("/api/lead/audit").json()["rows"]
    )  # L06 / correction


def test_correction_reaches_report_audit_and_history(api, db):
    """AC-45-C5 · 결정 019 ④ ⑤ ⑥ · L06 / correction: 보고서 점검 경로 정정 표기 · 무효 기록, 감사 문구, 승인 이력은 최신 값 · 무효 빼고 · 정정 n회."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="정상", memo="깨끗")
    other = s["instruction"]["section"]
    s = ok(tech, s, type="result", section=other, value="정상")
    sid = s["session_id"]
    lock_seq = seq_of(db, sid, "lock")
    s = fix(
        tech,
        s,
        target_seq=seq_of(db, sid, "result", "4.3"),
        value="이상",
        memo="뒷면 기름때",
        reason="뒷면을 못 봄",
        confirmed=True,
    ).json()
    r = note(tech, s, target_seq=lock_seq, memo="잠금 표지 2번 패널")
    assert r.status_code == 200
    s = r.json()
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert "정정 1회" in f["check_path"] and "정정으로 무효" in f["check_path"]  # 019 ⑤ · AC-45-C5
    assert "잠금 표지 2번 패널" not in " ".join(f.values())  # 019 ⑥: 덧붙인 메모는 보고서에 넣지 않음
    s = ok(tech, s, type="report_confirm")
    lc = lead(api)
    acts = [r["action"] for r in lc.get("/api/lead/audit").json()["rows"]]
    assert "정정 · 4.3 결과 정상 → 이상 · 사유 '뒷면을 못 봄'" in acts and "메모 덧붙이기 · 잠금 완료 1.2" in acts
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=sid)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    with db.connect() as c:
        rec = c.execute(
            text("select record from knowledge.history_records where record_id = :i"), {"i": f"R-{rid:04d}"}
        ).scalar()
    checks = [x for x in rec["steps"] if x["kind"] == "check"]
    assert [(x["section"], x["result"]) for x in checks] == [("4.3", "이상")]  # 019 ④: 최신 값만 · 무효(other) 뺌
    assert (
        checks[0]["corrections"] == 1 and checks[0]["note"] == "뒷면 기름때" and rec["outcome"]["cause_code"] == "CL-1"
    )


def test_voided_records_leave_llm_failure_count():
    """S02-G6-4: 정정으로 무효가 된 기록은 문장 생성 반복 실패 수(인계 권함)에서 뺀다."""
    from c1a15.api.sessions import llm_failures, logical

    raw = [
        {"seq": 1, "type": "result", "section": "4.3", "value": "정상", "memo": "m", "llm_fallback": False, "_at": "a"},
        {"seq": 2, "type": "instruction_shown", "section": "4.5", "llm_fallback": True, "_at": "b"},
        {"seq": 3, "type": "result", "section": "4.5", "value": "정상", "memo": "m", "llm_fallback": True, "_at": "c"},
    ]
    assert llm_failures(logical(raw)).count(True) == 2
    fixed = logical(
        raw
        + [
            {
                "seq": 4,
                "type": "correction",
                "target_seq": 1,
                "mode": "rejudge",
                "_at": "d",
                "before": {"value": "정상"},
                "after": {"value": "이상"},
            }
        ]
    )
    assert llm_failures(fixed).count(True) == 0  # 무효 기록을 세면 2로 남아 실패


def test_history_steps_records_and_read_only(api, db):
    """AC-41 · FLOW-14 다시 읽기: 진행 표시 5단계 기록(그때 값 · n / m · 정정 · 무효 · 덧붙인 메모 · 정정 가능 여부), 읽어도 단계 그대로(AC-41-C6)."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    s = ok(tech, s, type="precheck_done", values="하우징 표면 52 °C · 공기 302.7 K")
    s = ok(tech, s, type="result", section="4.3", value="정상", memo="필터 깨끗함")
    nxt = s["instruction"]["section"]
    s = ok(tech, s, type="result", section=nxt, value="정상")
    s = fix(tech, s, target_seq=seq_of(db, sid, "result", "4.3"), value="이상", reason="뒷면", confirmed=True).json()
    s = note(tech, s, target_seq=seq_of(db, sid, "lock"), memo="표지 추가").json()
    n_events = count(db, "select count(*) from ops.session_events where session_id = :s", s=sid)
    h = tech.get(f"/api/tech/sessions/{sid}/history").json()
    assert count(db, "select count(*) from ops.session_events where session_id = :s", s=sid) == n_events  # 읽기만
    assert tech.get(f"/api/tech/sessions/{sid}").json()["step"] == s["step"]  # AC-41-C6: 현재 단계 그대로
    names = [st["name"] for st in h["steps"]]
    assert (
        names == ["알람", "진단", "점검", "조치", "보고서"] and h["steps"][4]["records"] == []
    )  # 보고서는 기존 수정 경로(AC-41-C5)
    alarm_r = h["steps"][0]["records"][0]
    assert alarm_r["kind"] == "alarm" and alarm_r["alarm"]["sensors"] and "알람 · " in alarm_r["band"]
    diag = h["steps"][1]["records"][0]
    assert (
        diag["kind"] == "diagnosis" and diag["next"]["section"] == "4.3" and sum(diag["probabilities"].values()) == 100
    )  # 그때 첫 지시
    recs = h["steps"][2]["records"]
    kinds = [r["kind"] for r in recs]
    assert kinds == ["lock", "precheck", "check", "check"] and [r["position"] for r in recs] == [
        "1 / 4",
        "2 / 4",
        "3 / 4",
        "4 / 4",
    ]
    lock_r, pre, c43, cnx = recs
    assert (
        lock_r["notable"] and not lock_r["correctable"] and lock_r["notes"][0]["memo"] == "표지 추가"
    )  # AC-48-C1 · 019 ⑥
    assert pre["correctable"] and pre["values"].startswith("하우징 표면 52") and pre["section"] == "4.2"
    assert (
        c43["correctable"] and c43["value"] == "이상" and c43["corrections"][0]["before"]["value"] == "정상"
    )  # AC-45-C4: 이전 값
    assert c43["check_no"] == 1 and c43["steps"] and c43["criterion"] and "정정" in c43["band"]
    assert cnx["void"] and not cnx["correctable"] and cnx["check_no"] == 2  # 정정으로 무효는 정정 수단 없음
    # 조치 · 재가동 기록과 확정 세션
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    h = tech.get(f"/api/tech/sessions/{sid}/history").json()
    act = h["steps"][3]["records"][0]
    assert (
        act["kind"] == "action"
        and act["section"] == "4.3.2"
        and act["restart"]["checklist"]
        and "경계 이내" in act["record"]
    )
    assert (
        act["cause_name"] == "냉각 필터 막힘" and act["notable"] is False
    )  # 확정 세션은 메모 덧붙이기도 없음(생성자 해석 ③)
    assert (
        h["closed"]
        and h["closed_note"].startswith("보고서를 확정한 세션이라")
        and not any(r["correctable"] or r["notable"] for st in h["steps"] for r in st["records"])
    )
    assert (
        "보고서" in h["steps"][2]["records"][2]["band"] and "확정" in h["steps"][2]["records"][2]["band"]
    )  # T15 / closed 띠
