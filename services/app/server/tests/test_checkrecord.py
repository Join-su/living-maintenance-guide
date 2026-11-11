# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-006(시험 DB): 사람 체크 기록 한 장 — 만들기 · 신호 · 대상 Run 규칙 · 보내기(D-412 시연 네 장면 몫).

장면 ② = DEMO-01(실제 이력 벡터 DB ir-history-16d297d8 첫 Run 값 — 시험 안에서만 START_V4_DATA를 바꿔 끼움):
진단 1위 냉각수 부족 → 4.5 정상(헛걸음) → 4.3 이상 → 냉각 필터 막힘 확정 → S1 · S3.
장면 ④ 흉내 = 같은 각본 · 같은 기록 10건에서 냉각 필터 막힘(CL-1) 점수가 위 → 4.3 먼저 → 신호 0(해결됨).
"""

import json

import pytest
from sqlalchemy import text

import fake_deploy
from test_api import LEAD_PW, SERVER, TECH_PW, api, db, login, ok, to_instruct  # noqa: F401
from test_deploy_path import deploy, reset_ops  # noqa: F401
from test_r12_inbox import audits

pytestmark = pytest.mark.db
REAL = fake_deploy.START_V4_DATA.parent / "start_real_16d297d8.jsonl"
SCENARIOS = {
    s["scenario_id"]: s for s in json.loads((SERVER.parent / "data/demo/scenarios.json").read_text())["scenarios"]
}


def scene4_rows(tmp_path):
    """장면 ④ 흉내 자료: S01 상위 10건은 그대로(앱 이력에 있는 기록 id만) · CL-1 넷의 점수를 위로 · CL-3 여섯을 아래로."""
    rows = [json.loads(x) for x in REAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    s01 = next(r for r in rows if r["case"] == "S01")
    cl1 = [s for s in s01["similar"] if s["cause"] == "CL-1"]
    other = [s for s in s01["similar"] if s["cause"] != "CL-1"]
    s01["similar"] = [{**s, "score": 0.95 - i * 0.02} for i, s in enumerate(cl1)] + [
        {**s, "score": 0.40 - i * 0.02} for i, s in enumerate(other)
    ]
    path = tmp_path / "start_scene4.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def alarm(api, scenario, eval_mode=False):
    from fastapi.testclient import TestClient

    s = SCENARIOS[scenario]
    body = {
        "equipment_id": s["equipment_id"],
        "sensor_snapshot": s["sensor_snapshot"],
        "data_lineage": {"source_release": "ai4i-2020@sha256:test", "row_id": s["udi"], "split": "test"},
    }
    if eval_mode:
        body["eval_mode"] = True
    r = TestClient(api).post("/api/internal/alarms", json=body, headers={"X-Internal-Token": "test-internal-token"})
    assert r.status_code == 200, r.text
    return r.json()["alarm_id"]


def demo01(api, db, eval_mode=False):
    """DEMO-01 화면 길을 API로: 지시마다 그 절의 정답(4.3만 이상) → 조치 → 경계 이내 → 보고서 확정 → 파트장 그대로 승인."""
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01', eval_mode)}/session").json()
    s = to_instruct(tech, s)
    first = s["instruction"]["section"]
    for _ in range(4):
        sec = s["instruction"]["section"]
        if sec == "4.3":
            s = ok(tech, s, type="result", section=sec, value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
            break
        s = ok(tech, s, type="result", section=sec, value="정상")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    rid = one(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    return rid, s["session_id"], first


def one(db, sql, **kw):
    with db.connect() as c:
        return c.execute(text(sql), kw).scalar()


def opened(db, sid):
    with db.connect() as c:
        rows = c.execute(
            text("select payload from ops.session_events where session_id = :s and type = 'step_opened' order by seq"),
            {"s": sid},
        ).all()
    return [(r[0]["decision"]["kind"], r[0]["decision"].get("section"), r[0].get("run_id")) for r in rows]


def build(db, rid):
    from c1a15 import checkrecord

    with db.connect() as c:
        return checkrecord.build(c, rid)


def test_scene2_demo01_signals_target_runs_and_truth(api, db, deploy, monkeypatch):
    """VP-2 · VP-7 장면 ② · VP-1 · VP-4: S1 4.5(그 점검을 연 judge Run) · S3 CL-3 → CL-1(start Run) · 현장 정답 4.3 · CL-1."""
    from c1a15 import checkrecord
    from c1a15.checkrecord.model import CheckRecordV1

    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    rid, sid, first = demo01(api, db)
    assert first == "4.5"  # 실제 이력 DB: 진단 1위 냉각수 부족 → 4.5 먼저(헛걸음)
    doc = build(db, rid)
    CheckRecordV1.model_validate(doc)
    seq = opened(db, sid)
    start = one(db, "select start_run_id from ops.diag_sessions where session_id = :s", s=sid)
    assert seq[0][:2] == ("lock", "1.2") and seq[0][2] == start  # 첫 단계는 잠금(start Run)
    run_45 = next(r for k, sec, r in seq if k == "instruct" and sec == "4.5")
    assert doc["first_decision"]["run_id"] == start and doc["first_decision"]["top"][0] == {"cause": "CL-3", "p": 57}
    assert doc["first_instruction"] == {"section": "4.5", "kind": "instruct", "run_id": run_45}
    sig = {s["id"]: s for s in doc["signals"]}
    assert set(sig) == {"S1", "S3"}
    assert sig["S1"]["sections"] == ["4.5"] and sig["S1"]["runs"] == [run_45]
    assert (sig["S3"]["first"], sig["S3"]["final"], sig["S3"]["runs"]) == ("CL-3", "CL-1", [start])
    assert doc["field_truth"]["confirmed_sections"] == ["4.3"] and doc["field_truth"]["confirmed_cause"] == "CL-1"
    assert doc["outcome"]["type"] == "원인 확정" and doc["outcome"]["restart"] == "within"
    assert doc["record_key"] == f"CR-{rid:06d}" and doc["session"]["pack_session_id"].startswith("S-A")
    check43 = next(s for s in doc["steps"] if s["kind"] == "check" and s["section"] == "4.3")
    assert check43["memo"] and check43["has_memo"]
    sent = checkrecord.for_send(doc)  # 보내는 문서 = 메모 원문 0(D-412 결정 3)
    assert (
        all(s["memo"] is None for s in sent["steps"])
        and next(s for s in sent["steps"] if s["section"] == "4.3" and s["kind"] == "check")["has_memo"]
    )
    assert "tech.kim" not in json.dumps(doc, ensure_ascii=False) and "lead.park" not in json.dumps(
        doc, ensure_ascii=False
    )
    assert build(db, rid) == doc  # 결정성(VP-4)


def test_scene4_same_fault_first_43_no_signals(api, db, deploy, monkeypatch, tmp_path):
    """VP-7 장면 ④ 흉내: 같은 고장 · CL-1 위 → 첫 지시 4.3 · 신호 0(= 해결됨)."""
    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", scene4_rows(tmp_path))
    rid, _, first = demo01(api, db)
    doc = build(db, rid)
    assert first == "4.3" and doc["first_instruction"]["section"] == "4.3"
    assert doc["first_decision"]["top"][0]["cause"] == "CL-1" and doc["signals"] == []
    assert doc["outcome"]["cause_code"] == "CL-1" and doc["outcome"]["restart"] == "within"


def test_reopened_steps_target_first_run_s2_s5(api, db, deploy, monkeypatch):
    """대상 Run 규칙: DEMO-05 — 조치 뒤 여전히 밖(S2) → 숙련자 인계를 보내면 handover가 다시 열림 → S5 대상 = 처음 연 Run."""
    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-05')}/session").json()
    s = to_instruct(tech, s)
    for _ in range(4):
        sec = s["instruction"]["section"]
        if sec == "4.3":
            s = ok(tech, s, type="result", section=sec, value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
            break
        s = ok(tech, s, type="result", section=sec, value="정상")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="outside")
    assert s["step"]["kind"] == "handover"
    s = ok(tech, s, type="call_send")
    assert (s["step"]["kind"], s["step"].get("call_status")) == ("handover", "sent")  # 같은 단계를 다시 엶
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    cid = lc.get("/api/lead/calls").json()["rows"][0]["call_id"]
    assert lc.post(f"/api/lead/calls/{cid}/answer", json={"answer": "assign", "note": "주간 조"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    s = ok(tech, s, type="report_confirm")
    rid = one(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    doc = build(db, rid)
    handovers = [r for k, _sec, r in opened(db, s["session_id"]) if k == "handover"]
    assert len(handovers) >= 2  # 처음 연 Run + 인계를 보낸 뒤 다시 연 Run
    action_run = next(r for k, sec, r in opened(db, s["session_id"]) if k == "action" and sec == "4.3.2")
    sig = {x["id"]: x for x in doc["signals"]}
    assert sig["S2"]["runs"] == [action_run] and sig["S2"]["sections"] == ["4.3.2"]
    assert sig["S5"]["runs"] == [handovers[0]] and sig["S5"]["basis"] == ["handover:restart_outside"]
    assert handovers[1] in doc["runs"]["judge"]  # 다시 연 Run은 기록용 목록에만
    assert doc["outcome"]["type"] == "원인 미상·숙련자 인계" and doc["outcome"]["cause_code"] is None
    assert "S1" not in sig and "S3" not in sig and doc["field_truth"] is None  # 원인 없는 세션


def test_send_one_document_after_decision(api, db, deploy, monkeypatch):
    """CH-4 · VP-6: C1A15_CHECKS=deploy → 승인 commit 뒤 report Run에 문서 한 장(메모 0) · 같은 키 다시 = 같은 응답."""
    from c1a15 import checkrecord

    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    monkeypatch.setenv("C1A15_CHECKS", "deploy")
    rid, _, _ = demo01(api, db)
    assert len(deploy.feedback) == 1
    got = deploy.feedback[0]
    doc = build(db, rid)
    assert got["run_id"] == doc["runs"]["report"] and got["key"] == doc["record_key"]
    assert got["body"]["kind"] == "check_record" and got["body"]["record"] == checkrecord.for_send(doc)
    assert all(s["memo"] is None for s in got["body"]["record"]["steps"])
    assert [a["record_key"] for a in audits(db, "check_record_sent")] == [doc["record_key"]]
    again = checkrecord.send(doc)  # 다시 보내기(CLI --send와 같은 길) — 같은 키 · 같은 본문
    assert again["status"] == 201 and len(deploy.feedback) == 1


def test_send_failure_keeps_approval(api, db, deploy, monkeypatch):
    """PR-3 · VP-5: 배포 서버가 500이어도 승인 · 이력 그대로 · 감사 failed 1."""
    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    monkeypatch.setenv("C1A15_CHECKS", "deploy")
    deploy.feedback_fail = 500
    rid, _, _ = demo01(api, db)
    assert one(db, "select status from ops.reports where report_id = :r", r=rid) == "approved"
    assert one(db, "select count(*) from knowledge.history_records where record_id = :i", i=f"R-{rid:04d}") == 1
    failed = audits(db, "check_record_failed")
    assert len(failed) == 1 and failed[0]["status"] == 500 and not audits(db, "check_record_sent")


def test_off_by_default_sends_nothing(api, db, deploy, monkeypatch):
    """PR-4: C1A15_CHECKS 없음 = off → 보내기 · 감사 0."""
    reset_ops(db)
    monkeypatch.delenv("C1A15_CHECKS", raising=False)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    demo01(api, db)
    assert deploy.feedback == [] and not audits(db, "check_record_sent") and not audits(db, "check_record_failed")


def test_eval_mode_makes_no_record_cli_too(api, db, deploy, monkeypatch, capsys):
    """PR-5 · VP-9: 평가 모드 알람 세션 → 만들기 · CLI 모두 0건(보내기도 0)."""
    from c1a15.checkrecord.__main__ import main

    reset_ops(db)
    monkeypatch.setattr(fake_deploy, "START_V4_DATA", REAL)
    monkeypatch.setenv("C1A15_CHECKS", "deploy")
    rid, _, _ = demo01(api, db, eval_mode=True)
    assert build(db, rid) is None and deploy.feedback == []
    assert main(["--report", str(rid)]) == 0 and "평가 모드" in capsys.readouterr().out


def test_schema_file_is_model_schema():
    """VP-1: 저장소 schema 파일 = 모델에서 만든 글(바이트 같음)."""
    from c1a15.checkrecord.model import schema_text

    path = SERVER / "src/c1a15/checkrecord/check-record-v1.schema.json"
    assert path.read_text(encoding="utf-8") == schema_text()


def _finish(api, db, tech, s):
    """지시마다 4.3만 이상 → 조치 → 경계 이내 → 보고서 확정 → 파트장 그대로 승인 → 체크 기록."""
    for _ in range(6):
        if s["step"]["kind"] not in {"instruct", "instruct_after_checklist"}:
            break
        sec = s["instruction"]["section"]
        s = ok(tech, s, type="result", section=sec, value="이상" if sec == "4.3" else "정상")
    s = ok(tech, s, type="action_done", section=s["action"]["section"])
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    rid = one(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    return build(db, rid), s


def test_exception_shown_not_picked_followed_and_switched(api, db, deploy):
    """D-633: 예외 추천이 보였는데 끝까지 안 고름 — 추천 4.3대로(followed) · [점검 바꾸기]로 A · B 후보(switched).
    칸 = 관계 id · start Run(안내가 처음 실린 Run) · 사람이 한 점검. 예외가 안 보인 세션은 null. 내부 기록 줄도 하나씩."""
    from fastapi.testclient import TestClient

    from fake_deploy import pack_answer
    from test_api import start
    from test_exception_guide import _approved_exception, _guide

    reset_ops(db)
    hid = _approved_exception(api, db)
    plain = one(db, "select max(report_id) from ops.reports")
    assert build(db, plain)["exception_offer"] is None  # 예외가 안 보인 세션
    eg = _guide(hid)
    deploy.default = pack_answer(exception_guide=eg, preview=True)
    tech, s = start(api, db, equipment="M-01")
    assert eg["section"] in {c["section"] for c in s["preview"]["candidates"]}  # 보임
    sid = s["session_id"]
    srun = one(db, "select start_run_id from ops.diag_sessions where session_id = :s", s=sid)
    doc, s = _finish(api, db, tech, to_instruct(tech, s))
    assert doc["exception_offer"] == {
        "relation_id": eg["relation_id"],
        "run_id": srun,
        "adopted": False,
        "how": "followed",
        "picked_section": "4.3",
    }
    tech, s = start(api, db, equipment="M-01")
    s = to_instruct(tech, s)
    assert eg["section"] in {c["section"] for c in s["switch_candidates"]}
    pick = next(c["section"] for c in s["switch_candidates"] if not c["exception"] and c["grade"] in {"A", "B"})
    s = ok(tech, s, type="switch_check", section=pick)
    doc2, s2 = _finish(api, db, tech, s)
    o = doc2["exception_offer"]
    assert (o["relation_id"], o["adopted"], o["how"], o["picked_section"]) == (
        eg["relation_id"],
        False,
        "switched",
        pick,
    )
    items = TestClient(api).get("/api/internal/check-records", headers={"X-Internal-Token": "test-internal-token"})
    lines = [x for x in items.json()["items"] if x["kind"] == "exception_not_picked"]
    hc = lambda sec: {"section": sec, "from_section": None}  # noqa: E731
    assert [(x["session_id"], x["human_check"], x["relation_id"], x["how"], x["run_id"]) for x in lines] == [
        (sid, hc("4.3"), eg["relation_id"], "followed", srun),
        (s2["session_id"], hc(pick), eg["relation_id"], "switched", o["run_id"]),
    ]
