# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-11(시험 DB): 승인 보고서 → R12 받은 편지 파일(CH-23 · VC-47 · PR-01 · PR-16 · PR-21).

모양 = deploy 102 · metadata 칸 = c1a15-pack 012-02(센서 7칸 = 원값 5 + 알람 Run sensor_derived 2, 012-04).
"""

import json

import pytest
from sqlalchemy import text

from test_api import LEAD_PW, TECH_PW, api, db, login, ok, register, to_instruct  # noqa: F401
from test_deploy_path import count, deploy, reset_ops  # noqa: F401

pytestmark = pytest.mark.db
META = {
    "evidence_type",
    "source",
    "symptom_group",
    "cause",
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
    "temp_diff_k",
    "power_w",
}


def confirmed_report(api, db):
    """4.3 이상 → 교체 → 재가동 → 경계 이내 → 보고서 확정(승인 대기) → (report_id, 파트장 client)."""
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    return count(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"]), lc, alarm_id


def approve(lc, rid):
    r = lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert r.status_code == 200, r.text[:200]
    return r.json()


def audits(db, action):
    with db.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text("select detail from ops.audit_log where action = :a order by audit_id"), {"a": action}
            )
        ]


def test_approval_writes_one_inbox_file(api, db, tmp_path, monkeypatch):
    """VC-47: 승인 commit 뒤 파일 하나 — deploy 102 모양 · 012-02 칸 · text = 이력 narrative · 사람 이름 0 · .tmp 남지 않음."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    assert list(tmp_path.iterdir()) == []  # 확정(승인 대기)만으로는 0
    assert approve(lc, rid)["history_record_id"] == f"R-{rid:04d}"
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"RPT-{rid:06d}.json"]  # .tmp 남지 않음
    raw = (tmp_path / files[0]).read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert set(doc) == {"report_id", "approved_at", "document"} and doc["report_id"] == f"RPT-{rid:06d}"
    d = doc["document"]
    assert set(d) == {"doc", "record_id", "text", "metadata"} and d["doc"] == d["record_id"] == f"R-{rid:04d}"
    with db.connect() as c:
        rec = c.execute(
            text("select record, narrative from knowledge.history_records where record_id = :i"), {"i": d["record_id"]}
        ).one()
    assert d["text"] == rec.narrative
    md = d["metadata"]
    assert set(md) == META and md["evidence_type"] == "history" and md["source"] == "approved_report"
    assert md["symptom_group"] == "CL" and md["cause"] == "CL-1" == rec.record["outcome"]["cause_code"]
    assert all(isinstance(md[k], int | float) for k in META - {"evidence_type", "source", "symptom_group", "cause"})
    with db.connect() as c:
        names = [n for (n,) in c.execute(text("select display_name from ops.accounts"))]
    assert not any(n and n in raw for n in names) and "tech.kim" not in raw and "lead.park" not in raw  # PR-16
    assert audits(db, "r12_inbox_skipped") == [] and audits(db, "r12_inbox_failed") == []


def test_no_setting_no_file_no_audit(api, db, tmp_path, monkeypatch):
    """CH-23: 설정 없음 = 지금 동작(파일 0 · 감사 0)."""
    monkeypatch.delenv("C1A15_R12_INBOX", raising=False)
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    approve(lc, rid)
    assert audits(db, "r12_inbox_skipped") == [] and audits(db, "r12_inbox_failed") == []  # 설정 없음 = 감사도 0


def test_reject_writes_nothing(api, db, tmp_path, monkeypatch):
    """PR-01: 반려 · 확정 대기 보고서는 0."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시"}).status_code == 200
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("case", ["eval_mode", "exists", "missing_sensor"])
def test_skip_cases_audited_and_approval_kept(api, db, tmp_path, monkeypatch, case):
    """VC-47 건너뜀 갈래: 평가 모드 · 파일 있음(덮지 않음) · 센서 7칸 중 하나 빠짐 → 파일 그대로 · r12_inbox_skipped{reason} 한 줄 · 승인 200."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    rid, lc, alarm_id = confirmed_report(api, db)
    if case == "eval_mode":
        with db.begin() as c:
            c.execute(text("update ops.alarms set eval_mode = true where alarm_id = :a"), {"a": alarm_id})
    if case == "exists":
        (tmp_path / f"RPT-{rid:06d}.json").write_text("앞서 쓴 파일", encoding="utf-8")
    if case == "missing_sensor":
        with db.begin() as c:
            c.execute(
                text("update ops.alarms set sensor_snapshot = sensor_snapshot - 'power_w' where alarm_id = :a"),
                {"a": alarm_id},
            )
    approve(lc, rid)
    got = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.iterdir()}
    assert got == ({f"RPT-{rid:06d}.json": "앞서 쓴 파일"} if case == "exists" else {})
    assert [a["reason"] for a in audits(db, "r12_inbox_skipped")] == [case]


def test_write_failure_keeps_approval(api, db, tmp_path, monkeypatch):
    """PR-21: 폴더가 없어 쓰기 실패 → 승인 200 · 이력 그대로 · r12_inbox_failed 감사 1."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path / "없는-폴더"))
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    assert approve(lc, rid)["history_record_id"] == f"R-{rid:04d}"
    assert count(db, "select status from ops.reports where report_id = :r", r=rid) == "approved"
    assert count(db, "select count(*) from knowledge.history_records where record_id = :i", i=f"R-{rid:04d}") == 1
    assert [a["error"] for a in audits(db, "r12_inbox_failed")] == ["FileNotFoundError"]


def test_deploy_path_sensors_are_raw5_plus_pack_derived(api, db, deploy, tmp_path, monkeypatch):
    """Q-20 정정(012-04): deploy 길 알람 행 센서 = 원값 5칸 + 알람 Run sensor_derived 2칸 → 받은 편지 7칸이 그 값."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    rid, lc, alarm_id = confirmed_report(api, db)
    approve(lc, rid)
    md = json.loads((tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8"))["document"]["metadata"]
    alarm_post = next(p for p in deploy.posts() if p["body"]["kind"] == "alarm")
    derived = deploy.keys[alarm_post["headers"]["Idempotency-Key"]]["payload"]["result"]["output"]["alarm"][
        "sensor_derived"
    ]
    raw5 = alarm_post["body"]["event_card"]["sensor_snapshot"]
    assert (
        {k: md[k] for k in raw5} == raw5
        and {k: md[k] for k in derived} == derived
        and set(raw5) | set(derived) == META - {"evidence_type", "source", "symptom_group", "cause"}
    )


def test_relative_inbox_path_refused():
    from c1a15 import config

    assert config.r12_inbox({}) is None
    assert config.r12_inbox({"C1A15_R12_INBOX": "/tmp/x"}) == "/tmp/x"
    with pytest.raises(config.ConfigError):
        config.r12_inbox({"C1A15_R12_INBOX": "inbox"})


def test_unknown_cause_has_null_cause(api, db, tmp_path, monkeypatch):
    """012-02 · Q-20: 원인 미상(정정 호출 → 주간 처리) 보고서 → metadata.cause = null · 이력 cause_code 없음."""
    from test_report_sentence import correction_to_daytime_report

    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    s = correction_to_daytime_report(api, db, tech, s, lc)
    s = ok(tech, s, type="report_confirm")
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    approve(lc, rid)
    md = json.loads((tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8"))["document"]["metadata"]
    assert md["cause"] is None and md["symptom_group"] == "CL" and set(md) == META


def test_edit_approval_writes_edited_text(api, db, tmp_path, monkeypatch):
    """'수정 후 승인'(edit)도 같은 길 — 파트장이 고친 칸이 text(이력 narrative)에 들어감."""
    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    r = lc.post(
        f"/api/lead/reports/{rid}/decision",
        json={"decision": "edit", "edits": {"cause": "냉각 필터 막힘(기름때 심함)"}},
    )
    assert r.status_code == 200, r.text[:200]
    d = json.loads((tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8"))["document"]
    assert "냉각 필터 막힘(기름때 심함)로 판단" in d["text"]


@pytest.mark.parametrize("bad", ["7.8", True, None])
def test_document_skips_non_number_sensor(bad):
    """A11-N7: 센서 값이 문자 · bool · null이면 받지 않을 파일이므로 쓰지 않음(None)."""
    from c1a15.deploy import inbox

    snap = {k: 1.0 for k in inbox.SENSORS7}
    rec = {
        "sensor_snapshot": snap,
        "symptom_group": {"code": "CL"},
        "outcome": {"cause_code": "CL-1"},
        "source": {"report_id": 3},
        "record_id": "R-0003",
        "narrative": "x",
    }
    assert inbox.document(rec, "t")["document"]["metadata"]["power_w"] == 1.0
    assert inbox.document({**rec, "sensor_snapshot": {**snap, "power_w": bad}}, "t") is None


def test_deploy_path_uses_pack_derived_not_request_values(api, db, deploy, tmp_path, monkeypatch):
    """012-04: deploy 길은 원값 5칸만 보내고 계산 칸은 Pack sensor_derived — 요청에 틀린 계산 칸을 줘도 받은 편지는 Pack 값."""
    from test_api import SENSOR

    monkeypatch.setenv("C1A15_R12_INBOX", str(tmp_path))
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01", sensor={**SENSOR, "temp_diff_k": 1.0, "power_w": 1}).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    approve(lc, rid)
    md = json.loads((tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8"))["document"]["metadata"]
    assert (md["temp_diff_k"], md["power_w"]) == (7.9, 6283)  # Pack(가짜 = 같은 식)이 5칸에서 다시 계산한 값
