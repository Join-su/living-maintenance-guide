"""피드백 판 2 — 사람 체크 기록 문서 한 장(D-408 · D-412, 보고 202 · 206): POST /runs/{run_id}/feedback 본문 {"kind": "check_record", "record": …}.
배포단은 문서 겉만 본다(이름 · 키 · 배포 id · 붙는 Run · 신호 id · 메모 글 0 · 크기). 기록기는 DB 없는 가짜(record_check_record가 있는 것 · 없는 것).
판 1 피드백(tests/test_feedback_api.py)은 그대로."""
import copy
import hashlib
import json
import re

import pytest
from tracewriter import TraceWriteError

from fake_writer import canonical
from test_feedback_api import APPROVE, FB_ID, FeedbackWriter, _secrets, err, fb, post_run


class CheckWriter(FeedbackWriter):
    """가짜 기록기 + record_check_record(기록기 판 2 명세 모양 — 보고 206 C-5). 문서 원문은 payload 파일 하나, 같은 id · 같은 내용 = duplicate."""

    def __init__(self, root, **kw):
        super().__init__(root, **kw)
        self.cr_calls, self.cr_rows = [], {}

    def record_check_record(self, **kw):
        self.cr_calls.append(kw)
        code = self.fb_fail.pop(0) if self.fb_fail else None
        if code:
            raise TraceWriteError(code, "가짜 체크 기록 실패")
        body = canonical(kw)
        ref = self.store_payload(kw["record"])["ref"]
        old = self.cr_rows.get(kw["feedback_id"])
        if old is not None:
            if old != body:
                raise TraceWriteError("trace_conflict", "같은 피드백 id에 다른 내용")
            return {"result": "duplicate", "record_ref": ref, "body_sha256": hashlib.sha256(body).hexdigest()}
        self.cr_rows[kw["feedback_id"]] = body
        return {"result": "inserted", "record_ref": ref, "body_sha256": hashlib.sha256(body).hexdigest()}


def record(dep, run_id, **over):
    """앱 EV-006 change-contract v4 꼴의 최소 문서(칸 뜻은 앱 몫 — 여기선 겉 칸만 채움)."""
    doc = {
        "schema": "c1a15.check-record.v1",
        "record_key": "CR-000001",
        "made_at": "2026-10-27T01:00:00Z",
        "session": {"pack_session_id": "S-A12", "alarm_id": 12, "equipment_id": "M-01", "symptom_group": "CL"},
        "runs": {"deployment_id": dep.settings.deployment_id, "start": run_id, "judge": [], "report": None},
        "first_decision": {"run_id": run_id, "top": [{"cause": "CL-2", "p": 0.57}]},
        "steps": [{"seq": 3, "kind": "check", "section": "4.5", "opened_by_run": run_id, "recommended": True, "result": "정상",
                   "memo": None, "has_memo": True, "corrected": 0, "void": False}],
        "outcome": {"type": "원인 확정", "cause_code": "CL-1"},
        "lead": {"decision": "approve", "edits": [], "rejects": []},
        "signals": [{"id": "S1", "rule": "check-signals-v1", "runs": [run_id]}, {"id": "S3", "rule": "check-signals-v1", "runs": [run_id]},
                    {"id": "S1", "rule": "check-signals-v1", "runs": [run_id]}],
        "field_truth": {"confirmed_sections": ["4.3"], "confirmed_cause": "CL-1"},
    }
    for k, v in over.items():
        doc[k] = v
    return {"kind": "check_record", "record": doc}


@pytest.fixture
def cr(make_deployment, client_for, cases, tmp_path):
    """체크 기록을 아는 기록기(기본) 또는 판 1만 아는 기록기 + 실행 하나(C01)."""
    def make(writer_cls=CheckWriter, **kw):
        w = writer_cls(tmp_path / "trace", secrets=_secrets, **kw)
        dep = make_deployment(trace_writer=w)
        c = client_for(dep)
        run = post_run(c, cases["C01"], "cr-c01").json()["run_id"]
        return dep, w, c, run
    return make


def test_c1_writes_one_check_record(cr):
    dep, w, c, run = cr()
    body = record(dep, run)
    r = fb(c, run, body, "CR-000001")
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    b = r.json()
    assert set(b) == {"feedback_id", "run_id", "at", "kind", "record_key", "record_ref"}
    assert FB_ID.match(b["feedback_id"]) and b["run_id"] == run and b["kind"] == "check_record" and b["record_key"] == "CR-000001"
    assert b["record_ref"].startswith("sha256:")
    (call,) = w.cr_calls
    assert call == {"feedback_id": b["feedback_id"], "run_id": run, "at": b["at"], "actor": "lead", "retention_class": "dev",
                    "record_key": "CR-000001", "session_id": "S-A12", "signals": ["S1", "S3"], "record": body["record"]}
    assert w.calls == []                                            # 판 1 함수는 부르지 않음


def test_c2_same_key_same_body_replays_other_body_refused(cr):
    dep, w, c, run = cr()
    body = record(dep, run)
    first = fb(c, run, body, "CR-000001").json()
    again = fb(c, run, body, "CR-000001")
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true" and again.json() == first
    assert len(w.cr_calls) == 1
    other = copy.deepcopy(body)
    other["record"]["outcome"]["cause_code"] = "CL-2"
    assert err(fb(c, run, other, "CR-000001"))[:2] == (422, "idempotency_key_reused")


def test_c3_key_must_be_record_key(cr):
    dep, _, c, run = cr()
    assert err(fb(c, run, record(dep, run), "other-key")) == (422, "invalid_input", "header:Idempotency-Key")


def test_c4_memo_text_never_goes_to_the_shared_record(cr):
    dep, w, c, run = cr()
    steps = [{"seq": 3, "kind": "check", "section": "4.5", "memo": "필터에 기름때", "has_memo": True}]
    r = fb(c, run, record(dep, run, steps=steps), "CR-000001")
    assert err(r) == (422, "invalid_input", "$.record.steps[0].memo")
    assert "기름때" not in r.text and w.cr_calls == []
    assert fb(c, run, record(dep, run, steps=[{**steps[0], "memo": None}]), "CR-000001").status_code == 201


def test_c5_deployment_and_anchor_run(cr):
    dep, _, c, run = cr()
    other_dep = record(dep, run, runs={"deployment_id": "task10@other", "start": run})
    assert err(fb(c, run, other_dep, "CR-000001")) == (422, "invalid_input", "$.record.runs.deployment_id")
    elsewhere = record(dep, run, runs={"deployment_id": dep.settings.deployment_id, "start": run, "report": "run_01M5VK6HCJ1ZPN1PG3K0J0FWNZ"})
    assert err(fb(c, run, elsewhere, "CR-000001")) == (422, "invalid_input", "path:run_id")
    assert err(fb(c, run, record(dep, run, runs={"deployment_id": dep.settings.deployment_id}), "CR-000001")) == (422, "invalid_input", "$.record.runs")


def test_c6_run_must_exist_here(cr):
    dep, _, c, run = cr()
    missing = "run_01M5VK6HCJ1ZPN1PG3K0J0FWNZ"
    body = record(dep, missing, runs={"deployment_id": dep.settings.deployment_id, "start": missing})
    assert err(fb(c, missing, body, "CR-000001"))[:2] == (404, "run_not_found")


def test_c7_envelope_fields(cr):
    dep, _, c, run = cr()
    good = record(dep, run)
    assert err(fb(c, run, {**good, "extra": 1}, "CR-000001")) == (422, "invalid_input", "$.extra")
    assert err(fb(c, run, {"kind": "check_record", "record": []}, "CR-000001")) == (422, "invalid_input", "$.record")
    assert err(fb(c, run, record(dep, run, schema="check-record"), "CR-000001")) == (422, "invalid_input", "$.record.schema")
    assert err(fb(c, run, record(dep, run, record_key="1 2"), "1-2")) == (422, "invalid_input", "$.record.record_key")
    assert err(fb(c, run, record(dep, run, signals=[{"id": "S 1"}]), "CR-000001")) == (422, "invalid_input", "$.record.signals[0].id")
    assert err(fb(c, run, record(dep, run, signals="S1"), "CR-000001")) == (422, "invalid_input", "$.record.signals")
    assert err(fb(c, run, record(dep, run, session={"pack_session_id": "S A"}), "CR-000001")) == (422, "invalid_input", "$.record.session.pack_session_id")
    big = record(dep, run, field_truth={"pad": "x" * (1024 * 1024)})
    assert err(fb(c, run, big, "CR-000001")) == (422, "invalid_input", "$.record")


def test_c8_no_signals_is_resolved_and_allowed(cr):
    """신호 0 = '해결됨'(앱 v4 · llmops 156) — 그대로 받음."""
    dep, w, c, run = cr()
    assert fb(c, run, record(dep, run, signals=[]), "CR-000001").status_code == 201
    assert w.cr_calls[0]["signals"] == []


def test_c9_writer_without_check_records_is_501_and_v1_still_works(cr):
    dep, w, c, run = cr(writer_cls=FeedbackWriter)
    r = fb(c, run, record(dep, run), "CR-000001")
    assert err(r) == (501, "feedback_unsupported", "trace") and "체크 기록" in r.json()["error"]["message"]
    assert fb(c, run, APPROVE, "v1-approve").status_code == 201 and len(w.calls) == 1


def test_c10_trace_unavailable_then_same_key_same_ids(cr):
    dep, w, c, run = cr(fail=["trace_unavailable"])
    body = record(dep, run)
    r = fb(c, run, body, "CR-000001")
    assert err(r)[:2] == (503, "trace_unavailable") and r.json()["error"]["retryable"] is True
    ok = fb(c, run, body, "CR-000001")
    assert ok.status_code == 201
    first, second = w.cr_calls
    assert (first["feedback_id"], first["at"]) == (second["feedback_id"], second["at"]) == (ok.json()["feedback_id"], ok.json()["at"])


def test_c11_secret_text_in_record_is_masked_in_the_payload(cr, monkeypatch):
    secret = "sk-checkrecord0123456789abcdefABCDEF"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    dep, w, c, run = cr()
    r = fb(c, run, record(dep, run, outcome={"type": "원인 확정", "cause_code": "CL-1", "note": secret}), "CR-000001")
    assert r.status_code == 201 and secret not in r.text
    stored = [p.read_text(encoding="utf-8") for p in (w.root / "payloads").rglob("*") if p.is_file()] if (w.root / "payloads").exists() else []
    assert all(secret not in s for s in stored)


def test_c12_key_like_text_cannot_be_the_record_key_or_session(cr, monkeypatch):
    secret = "sk-checkrecordkey0123456789abcdefABCD"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    dep, w, c, run = cr()
    r = fb(c, run, record(dep, run, record_key=secret), secret)
    assert err(r) == (422, "invalid_input", "$.record.record_key") and secret not in r.text
    r = fb(c, run, record(dep, run, session={"pack_session_id": secret}), "CR-000001")
    assert err(r) == (422, "invalid_input", "$.record.session.pack_session_id") and secret not in r.text and w.cr_calls == []


def test_c13_bounds_match_the_writer_draft(cr):
    """001-p(앱 대조): 기록기 판 2 초안과 같은 경계 — 신호 0 ~ 20 · session.pack_session_id 필수."""
    dep, w, c, run = cr()
    sig = lambda n: [{"id": f"S{i}", "rule": "check-signals-v1", "runs": [run]} for i in range(n)]
    assert fb(c, run, record(dep, run, signals=sig(20)), "CR-000001").status_code == 201
    assert err(fb(c, run, record(dep, run, record_key="CR-000002", signals=sig(21)), "CR-000002")) == (422, "invalid_input", "$.record.signals")
    no_session = record(dep, run, record_key="CR-000003")
    del no_session["record"]["session"]
    assert err(fb(c, run, no_session, "CR-000003")) == (422, "invalid_input", "$.record.session.pack_session_id")
    assert err(fb(c, run, record(dep, run, record_key="CR-000004", session={"alarm_id": 1}), "CR-000004")) == (422, "invalid_input", "$.record.session.pack_session_id")


def test_c14_duplicate_by_record_key_answers_the_stored_id(cr):
    """packrt O-2: 기록기는 record_key 하나에 줄 하나 — 배포단 키 기록을 잃고 다시 와도 duplicate면 저장된 feedback_id · at을 돌려줌."""
    dep, w, c, run = cr()
    stored = {"feedback_id": "fb_01M5V0000000000000000000ZZ", "at": "2026-10-27T00:00:00.000Z"}
    w.record_check_record = lambda **kw: {"result": "duplicate", "record_ref": "sha256:" + "d" * 64, "body_sha256": "x", **stored}
    b = fb(c, run, record(dep, run), "CR-000001").json()
    assert (b["feedback_id"], b["at"]) == (stored["feedback_id"], stored["at"])

