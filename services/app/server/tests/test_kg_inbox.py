# ruff: noqa: F811, E501 — test_api · test_deploy_path · test_r12_inbox의 fixture · 도우미를 이름으로 쓴다
"""D-543 할 일 1 받은 편지함(그래프 적재기): 승인 commit 뒤 C1A15_KG_INBOX에 파일 하나 — 꼴 = 앱 초안 그대로
(c1a15-pack inbox-fit: 바꿀 칸 0) {kind kg_record, schema 1, report_id, approved_at, record}.
record = 승인 이력 한 건에서 승인 · 작성 칸(approval · narrative · provenance · source)과 단계 by를 뺀 것 · 글 칸 이름 거름.
일반 · 예외 기록 같은 꼴(예외면 record.exception_record). 실패해도 승인 그대로 · 평가 모드 0 · R12 폴더와 따로.
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from test_api import LEAD_PW, login, ok, start, to_instruct  # noqa: F401
from test_deploy_path import api, count, db, reset_ops  # noqa: F401
from test_exception_record import SAME, TEXT
from test_r12_inbox import approve, audits, confirmed_report

pytestmark = pytest.mark.db
RECORD_KEYS = {  # handoff d543 kg-inbox-example.json record 칸(예외 기록이 있으면 exception_record 더)
    "action",
    "alarm",
    "closed_at",
    "downtime_min",
    "equipment_id",
    "equipment_type",
    "occurred_at",
    "outcome",
    "record_id",
    "sensor_snapshot",
    "steps",
    "symptom_group",
    "unconfirmed",
}


def _history(db, record_id):
    with db.connect() as c:
        return c.execute(
            text("select record from knowledge.history_records where record_id = :i"), {"i": record_id}
        ).scalar()


def _names(db):
    with db.connect() as c:
        return [r[0] for r in c.execute(text("select display_name from ops.accounts"))] + ["tech.kim", "lead.park"]


def test_approval_writes_one_kg_file_without_names(api, db, tmp_path, monkeypatch):
    monkeypatch.setenv("C1A15_KG_INBOX", str(tmp_path))
    monkeypatch.delenv("C1A15_R12_INBOX", raising=False)
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    assert list(tmp_path.iterdir()) == []  # 확정(승인 대기)만으로는 0
    hid = approve(lc, rid)["history_record_id"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"RPT-{rid:06d}.json"]  # .tmp 남지 않음
    raw = (tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert set(doc) == {"kind", "schema", "report_id", "approved_at", "record"}
    assert (doc["kind"], doc["schema"], doc["report_id"]) == ("kg_record", 1, f"RPT-{rid:06d}")
    rec = doc["record"]
    assert set(rec) == RECORD_KEYS and rec["record_id"] == hid
    assert all("by" not in s for s in rec["steps"])
    assert [s["kind"] for s in rec["steps"]] == ["lock", "check", "action", "restart"]  # 예시 꼴 그대로
    full = _history(db, hid)  # 원본 이력과 같은 값(뺀 칸만 다름)
    assert {k: v for k, v in full.items() if k in RECORD_KEYS - {"steps"}} == {
        k: v for k, v in rec.items() if k != "steps"
    }
    assert not any(n in raw for n in _names(db))  # 사람 이름 · 계정 ID 0
    assert len(rec["sensor_snapshot"]) == 7
    if os.environ.get("KG_INBOX_OUT"):  # D-503 2: 실제 꼴 한 건(시험 DB 값 그대로)
        Path(os.environ["KG_INBOX_OUT"]).write_text(raw, encoding="utf-8")
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 409  # 두 번째 0
    assert len(list(tmp_path.iterdir())) == 1


def test_exception_record_same_shape_with_exception_record(api, db, tmp_path, monkeypatch):
    monkeypatch.setenv("C1A15_KG_INBOX", str(tmp_path))
    reset_ops(db)
    tech, s = start(api, db, equipment="M-01")
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="김 반장이 본 기름때")  # 이름 낀 메모
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="exception_split", exception_text=TEXT)  # D-556 AI 나눔(가짜 배포 서버)
    s = ok(
        tech,
        s,
        type="report_confirm",
        exception_record={"text": TEXT},
        exception_review=SAME,
    )
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=s["session_id"])
    approve(lc, rid)
    doc = json.loads((tmp_path / f"RPT-{rid:06d}.json").read_text(encoding="utf-8"))
    rec = doc["record"]
    assert set(rec) == RECORD_KEYS | {"exception_record"}
    ex = rec[
        "exception_record"
    ]  # 약속 v10 6절: 글 · 예외 하나(주제 · 점검 목록 · 조치 목록) · 같음/아님 한 번 · 자동 칸
    assert ex["text"] == TEXT and "items" not in ex
    assert set(ex["exception"]) == {"topic", "checks", "actions"}  # 1절 꼴 그대로(llmops-2 적재기는 그 밖 칸을 거절)
    assert set(ex["exception"]["checks"][0]) == {"what", "how", "criteria", "minutes"}
    assert set(ex["exception"]["actions"][0]) == {"what", "parts", "minutes"}
    assert ex["exception_review"] == SAME  # 승인 기록 파일 칸 이름(약속 v10 6절) · 같음 = 놓침
    assert [m["no"] for m in ex["exception_matches"]][:1] == ["EX-R-0001"]  # 그때 보인 비슷한 것(앱 칸)
    assert ex["situation"].startswith("M-01 · ") and ex["result"] == "재가동 정상(경계 이내)"
    assert set(rec["exception_record"]["context"]) >= {"equipment_id", "sensor_values", "symptom_group"}
    note = next(x["note"] for x in rec["steps"] if x.get("section") == "4.3")
    assert note == "[이름]이 본 기름때"  # 글 칸 이름 거름(R12와 같은 규칙)
    assert [a["count"] for a in audits(db, "names_redacted") if a["where"] == "kg_inbox"] == [1]


def test_no_setting_no_file_and_eval_mode_skipped(api, db, tmp_path, monkeypatch):
    monkeypatch.delenv("C1A15_KG_INBOX", raising=False)
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    approve(lc, rid)
    assert audits(db, "kg_inbox_skipped") == [] and audits(db, "kg_inbox_failed") == []  # 설정 없음 = 지금 동작
    monkeypatch.setenv("C1A15_KG_INBOX", str(tmp_path))
    rid, lc, alarm_id = confirmed_report(api, db)
    with db.begin() as c:
        c.execute(text("update ops.alarms set eval_mode = true where alarm_id = :a"), {"a": alarm_id})
    approve(lc, rid)
    assert list(tmp_path.iterdir()) == [] and [a["reason"] for a in audits(db, "kg_inbox_skipped")] == ["eval_mode"]


def test_write_failure_keeps_approval(api, db, tmp_path, monkeypatch):
    monkeypatch.setenv("C1A15_KG_INBOX", str(tmp_path / "없는-폴더"))
    reset_ops(db)
    rid, lc, _ = confirmed_report(api, db)
    assert approve(lc, rid)["history_record_id"] == f"R-{rid:04d}"
    assert count(db, "select status from ops.reports where report_id = :r", r=rid) == "approved"
    assert [a["error"] for a in audits(db, "kg_inbox_failed")] == ["FileNotFoundError"]


def test_relative_kg_inbox_path_refused():
    from c1a15 import config

    with pytest.raises(config.ConfigError):
        config.kg_inbox({"C1A15_KG_INBOX": "inbox"})
    assert config.kg_inbox({}) is None
