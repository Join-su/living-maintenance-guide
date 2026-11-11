"""사람 피드백 받기 POST /runs/{run_id}/feedback(VFE EV-005 지시 041, D-180 (A) · D-182, E3 F-1 ~ F-15 + 다시 켬 · 상수 대조 ·
실제 record_feedback). 기록기는 DB 없는 가짜(record_feedback이 있는 것 · 없는 것). tracewriter 0.1.3이 있으면(git archive 경로)
실제 TraceWriter.record_feedback을 가짜 연결로 한 번 더 본다. 기존 응답 · 기록은 그대로(더하기만)."""
import ast
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
import tracewriter
from tracewriter import TraceWriteError, TraceWriter

from deploy_server import feedback
from deploy_server.app import Deployment
from deploy_server.settings import load_settings
from deploy_server.stores import request_sha256
from fake_writer import FakeWriter, canonical

ROOT = Path(__file__).resolve().parents[1]
LLMOPS = ROOT.parent / "llmops-lite-v2" / "llmops"
SECRET = "sk-feedbacktest0123456789abcdefABCDEF"
FB_ID = re.compile(r"^fb_[0-9A-HJKMNP-TV-Z]{26}$")


class FeedbackWriter(FakeWriter):
    """가짜 기록기 + record_feedback(tracewriter 0.1.3 모양). fail: 부를 때마다 앞에서 하나씩 꺼내 그 code로 실패(None = 성공)."""

    def __init__(self, root, *, fail=(), **kw):
        super().__init__(root, **kw)
        self.fb_fail, self.calls, self.rows = list(fail), [], {}

    def record_feedback(self, **kw):
        self.calls.append(kw)
        code = self.fb_fail.pop(0) if self.fb_fail else None
        if code:
            raise TraceWriteError(code, "가짜 피드백 기록 실패")
        body = canonical(kw)
        sha = hashlib.sha256(body).hexdigest()
        ref = self.store_payload(kw["edited_value"])["ref"] if kw["action"] == "edit" else None
        old = self.rows.get(kw["feedback_id"])
        if old is not None:
            if old != body:
                raise TraceWriteError("trace_conflict", "같은 피드백 id에 다른 내용")
            return {"result": "duplicate", "edited_value_ref": ref, "body_sha256": sha}
        self.rows[kw["feedback_id"]] = body
        return {"result": "inserted", "edited_value_ref": ref, "body_sha256": sha}


def _secrets():
    return [v for v in (os.environ.get("OPENAI_API_KEY"),) if v]


@pytest.fixture
def fb_dep(make_deployment, tmp_path):
    def make(writer=None, **kw):
        w = writer if writer is not None else FeedbackWriter(tmp_path / "trace", secrets=_secrets, **kw)
        return make_deployment(trace_writer=w), w
    return make


def post_run(c, body, key):
    return c.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json", "Idempotency-Key": key})


def fb(c, run_id, body, key):
    return c.post(f"/runs/{run_id}/feedback", content=json.dumps(body, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json", "Idempotency-Key": key})


def err(r):
    return r.status_code, r.json()["error"]["code"], r.json()["error"]["where"]


EDIT = {"action": "edit", "label": "LLM 출력 틀림", "actor": "owner-01", "reason": "원인 1순위가 HDF가 맞음",
        "edits": {"answer.cause_candidates.0.type": "HDF"}}
APPROVE = {"action": "approve", "actor": "owner-01", "reason": "맞는 답"}


@pytest.fixture
def ran(fb_dep, client_for, cases):
    """검토 node 있는 실행(C01, review) 하나와 검토 node 없는 실행(C10, no_issue) 하나."""
    def make(**kw):
        dep, w = fb_dep(**kw)
        c = client_for(dep)
        r1 = post_run(c, cases["C01"], "ran-c01").json()
        r10 = post_run(c, cases["C10"], "ran-c10").json()
        assert r1["outcome"] == "completed" and r10["outcome"] == "no_issue"
        return dep, w, c, r1["run_id"], r10["run_id"]
    return make


def test_f1_edit_writes_one_feedback(ran):
    dep, w, c, run, _ = ran()
    r = fb(c, run, EDIT, "f1")
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    b = r.json()
    assert set(b) == {"feedback_id", "run_id", "at", "edited_value_ref"}
    assert FB_ID.match(b["feedback_id"]) and b["run_id"] == run and b["at"].endswith("Z") and b["edited_value_ref"].startswith("sha256:")
    (call,) = w.calls
    assert call == {"feedback_id": b["feedback_id"], "run_id": run, "at": b["at"], "action": "edit", "actor": "owner-01",
                    "reason": EDIT["reason"], "retention_class": "dev", "label": "LLM 출력 틀림", "review_node": "review", "node": None,
                    "edited_value": EDIT["edits"]}


def test_f2_approve_label_rule(ran):
    _, w, c, run, _ = ran()
    assert fb(c, run, APPROVE, "f2-a").status_code == 201
    assert fb(c, run, {**APPROVE, "label": "정상 동작(escalation 맞음)"}, "f2-b").status_code == 201
    assert err(fb(c, run, {**APPROVE, "label": "근거 부족"}, "f2-c")) == (422, "invalid_input", "$.label")
    assert [x["label"] for x in w.calls] == [None, "정상 동작(escalation 맞음)"]


def test_f3_other_actions_need_a_failure_label(ran):
    _, _, c, run, _ = ran()
    for action in ("escalate", "reject"):
        assert err(fb(c, run, {**APPROVE, "action": action}, f"f3-{action}")) == (422, "invalid_input", "$.label")
        assert err(fb(c, run, {**APPROVE, "action": action, "label": "정상 동작(escalation 맞음)"}, f"f3-ok-{action}"))[2] == "$.label"
        assert fb(c, run, {**APPROVE, "action": action, "label": "Pack 밖 원인"}, f"f3-good-{action}").status_code == 201
    assert err(fb(c, run, {**EDIT, "label": None}, "f3-edit"))[2] == "$.label"
    assert err(fb(c, run, {**EDIT, "label": "없는 라벨"}, "f3-unknown"))[2] == "$.label"


def test_f4_review_failed_has_no_approve(ran):
    dep, w, c, run, _ = ran()
    meta_p = dep.reports.data_dir / "reports" / c.get(f"/runs/{run}").json()["report"]["report_id"] / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta_p.write_text(json.dumps({**meta, "review": {**meta["review"], "node_path": "respond.review_failed"}}), encoding="utf-8")
    r = fb(c, run, APPROVE, "f4-a")
    assert err(r) == (422, "invalid_input", "$.action") and "edit · escalate · reject" in r.json()["error"]["message"]
    assert fb(c, run, {**APPROVE, "action": "escalate", "label": "규칙 실패"}, "f4-b").status_code == 201
    assert w.calls[-1]["review_node"] == "review_failed"


def test_f5_actor_and_reason(ran, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "owner-secret-99")   # 별명 모양인 비밀 값 = 키 모양으로 막음(RX-F1+)
    _, w, c, run, _ = ran()
    for i, actor in enumerate(("김철수", "kim chulsoo", "12345", "a", "a" * 33, "Owner", "owner-secret-99")):
        assert err(fb(c, run, {**APPROVE, "actor": actor}, f"f5-{i}"))[2] == "$.actor", actor
    for i, reason in enumerate((None, "", "   ", "가" * 501, 3)):
        body = {**APPROVE, "reason": reason} if reason is not None else {k: v for k, v in APPROVE.items() if k != "reason"}
        assert err(fb(c, run, body, f"f5-r-{i}"))[2] == "$.reason", reason
    assert err(fb(c, run, {k: v for k, v in APPROVE.items() if k != "actor"}, "f5-none"))[2] == "$.actor"
    assert fb(c, run, {**APPROVE, "reason": "가" * 500}, "f5-500").status_code == 201
    assert len(w.calls) == 1


def test_f6_edits_rules(ran):
    _, _, c, run, _ = ran()
    assert err(fb(c, run, {k: v for k, v in EDIT.items() if k != "edits"}, "f6-a"))[2] == "$.edits"
    assert err(fb(c, run, {**APPROVE, "edits": {"answer.x": 1}}, "f6-b"))[2] == "$.edits"
    assert err(fb(c, run, {**EDIT, "edits": {}}, "f6-c"))[2] == "$.edits"
    assert err(fb(c, run, {**EDIT, "edits": {f"answer.k{i}": i for i in range(51)}}, "f6-d"))[2] == "$.edits"
    assert err(fb(c, run, {**EDIT, "edits": {"event.alarm": 1}}, "f6-e"))[2] == '$.edits["event.alarm"]'
    assert err(fb(c, run, {**EDIT, "edits": {"answer..x": 1}}, "f6-f"))[2] == '$.edits["answer..x"]'
    assert err(fb(c, run, {**EDIT, "edits": "answer"}, "f6-g"))[2] == "$.edits"
    assert fb(c, run, {**EDIT, "edits": {"answer": {"summary": "새 글"}}}, "f6-ok").status_code == 201


def test_f7_unknown_fields_role_and_node(ran):
    _, w, c, run, _ = ran()
    r = fb(c, run, {**APPROVE, "x": 1}, "f7-a")
    assert err(r) == (422, "invalid_input", "$.x")
    r = fb(c, run, {**APPROVE, "role": "정비 담당"}, "f7-b")
    assert err(r)[2] == "$.role" and "role" in r.json()["error"]["message"]
    assert err(fb(c, run, {**APPROVE, "node": "nope"}, "f7-c"))[2] == "$.node"
    assert err(fb(c, run, {**APPROVE, "node": 3}, "f7-d"))[2] == "$.node"
    assert err(fb(c, run, [APPROVE], "f7-e"))[2] == "$"
    r = fb(c, run, {**EDIT, "node": "draft"}, "f7-ok")
    assert r.status_code == 201 and w.calls[-1]["node"] == "draft"
    # 문제가 여럿이면 details.problems에 모두(첫 것이 where)
    r = fb(c, run, {"action": "edit", "actor": "X", "x": 1}, "f7-many")
    wheres = [p["where"] for p in r.json()["error"]["details"]["problems"]]
    assert wheres[0] == r.json()["error"]["where"] and {"$.label", "$.actor", "$.reason", "$.edits", "$.x"} <= set(wheres)


def test_f8_run_must_exist_and_have_succeeded(ran):
    dep, w, c, run, _ = ran()
    assert err(fb(c, "run_01NOPE0000000000000000000A", APPROVE, "f8-a")) == (404, "run_not_found", "path:run_id")
    assert fb(c, "run_bad-id", APPROVE, "f8-b").status_code == 404
    rec = c.get(f"/runs/{run}").json()
    for i, status in enumerate(("failed", "running")):
        rid = f"run_01ZZZZZZZZZZZZZZZZZZZZZZZ{i}"
        dep.runs.save({**rec, "run_id": rid, "status": status, "result": None})
        r = fb(c, rid, APPROVE, f"f8-{status}")
        assert err(r) == (409, "run_not_succeeded", "path:run_id") and r.json()["error"]["run_id"] == rid
    assert w.calls == []


def test_f9_same_key(ran):
    _, w, c, run, run10 = ran()
    first = fb(c, run, EDIT, "f9")
    again = fb(c, run, EDIT, "f9")
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true" and again.json() == first.json()
    assert len(w.calls) == 1
    assert err(fb(c, run, {**EDIT, "reason": "다른 이유"}, "f9")) == (422, "idempotency_key_reused", "header:Idempotency-Key")
    assert err(fb(c, run10, EDIT, "f9"))[1] == "idempotency_key_reused"
    assert err(fb(c, run, EDIT, ""))[1] == "idempotency_key_missing"
    assert err(fb(c, run, EDIT, "a b"))[1] == "idempotency_key_invalid"
    r = c.post(f"/runs/{run}/feedback", content=b"{", headers={"Idempotency-Key": "f9-json"})
    assert err(r) == (400, "invalid_json", "$")
    # 실행 키와 피드백 키는 따로: 실행에 쓴 키를 피드백에 써도 실행 키 기록과 섞이지 않음
    assert fb(c, run, APPROVE, "ran-c01").status_code == 201


def test_f10_trace_unavailable_then_same_key_same_id(ran):
    _, w, c, run, _ = ran(fail=["trace_unavailable"])
    r = fb(c, run, EDIT, "f10")
    e = r.json()["error"]
    assert r.status_code == 503 and e["code"] == "trace_unavailable" and e["retryable"] is True and e["run_id"] == run
    r2 = fb(c, run, EDIT, "f10")
    assert r2.status_code == 201 and "Idempotent-Replayed" not in r2.headers
    a, b = w.calls
    assert a == b and r2.json()["feedback_id"] == a["feedback_id"] and r2.json()["at"] == a["at"]
    r3 = fb(c, run, EDIT, "f10")
    assert r3.json() == r2.json() and r3.headers["Idempotent-Replayed"] == "true" and len(w.calls) == 2
    assert err(fb(c, run, {**EDIT, "reason": "다름"}, "f10"))[1] == "idempotency_key_reused"


def test_f11_other_write_errors_are_stored(ran):
    _, w, c, run, _ = ran(fail=["trace_schema", "payload_failed"])
    r = fb(c, run, EDIT, "f11")
    assert err(r) == (500, "trace_schema", "trace") and r.json()["error"]["retryable"] is False
    again = fb(c, run, EDIT, "f11")
    assert again.status_code == 500 and again.json() == r.json() and again.headers["Idempotent-Replayed"] == "true"
    assert err(fb(c, run, EDIT, "f11-b"))[:2] == (500, "payload_failed")
    assert len(w.calls) == 2


def test_f12_writer_without_record_feedback(fb_dep, client_for, cases, tmp_path):
    dep, _ = fb_dep(writer=FakeWriter(tmp_path / "plain"))
    c = client_for(dep)
    run = post_run(c, cases["C01"], "f12-run").json()["run_id"]
    r = fb(c, run, APPROVE, "f12")
    assert err(r) == (501, "feedback_unsupported", "trace")
    assert fb(c, run, APPROVE, "f12").json() == r.json()


def test_f13_secrets_stay_out_of_responses_and_key_files(ran, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    dep, w, c, run, _ = ran()
    r = fb(c, run, {**EDIT, "reason": f"키 {SECRET} 가 보임", "edits": {"answer.summary": f"값 {SECRET}"}}, "f13")
    assert r.status_code == 201 and SECRET not in r.text
    bad = fb(c, run, {**EDIT, "edits": {f"answer.{SECRET}": 1}}, "f13-path")
    assert bad.status_code == 422 and SECRET not in bad.text and bad.json()["error"]["where"].startswith('$.edits["answer.')
    assert SECRET not in fb(c, run, {**APPROVE, "actor": "sk-" + "a" * 30}, "f13-actor").text
    for p in (dep.settings.data_dir / "feedback-idempotency").glob("*.json"):
        assert SECRET not in p.read_text(encoding="utf-8")
    # 가짜 기록기의 원문 파일은 가림을 거침(실제 가림은 tracewriter 몫, F-18)
    assert all(SECRET not in p.read_text(encoding="utf-8") for p in (w.root / "payloads").rglob("*") if p.is_file())


def test_f14_no_issue_run_has_no_review_node(ran):
    _, w, c, _, run10 = ran()
    assert fb(c, run10, APPROVE, "f14-a").status_code == 201
    assert w.calls[-1]["review_node"] is None
    assert err(fb(c, run10, EDIT, "f14-b"))[2] == "$.action"
    assert fb(c, run10, {**APPROVE, "action": "escalate", "label": "검색 못 함", "node": "check_alarm"}, "f14-c").status_code == 201


def test_f15_existing_responses_unchanged(ran):
    dep, _, c, run, _ = ran()
    before = (c.get(f"/runs/{run}").content, c.get("/runs").content)
    rid = c.get(f"/runs/{run}").json()["report"]["report_id"]
    report_before = c.get(f"/reports/{rid}").content
    assert fb(c, run, EDIT, "f15").status_code == 201 and fb(c, run, APPROVE, "f15-b").status_code == 201
    assert (c.get(f"/runs/{run}").content, c.get("/runs").content) == before
    assert c.get(f"/reports/{rid}").content == report_before
    assert not (dep.settings.data_dir / "feedback").exists()   # 배포단 자기 피드백 기록 파일은 없음(같은 키 파일만)


def test_f16_restart_reopens_a_running_feedback_key(ran, make_deployment, client_for, tmp_path):
    dep, w, c, run, _ = ran()
    sha = request_sha256({"run_id": run, "body": APPROVE})
    assert dep.feedback_idem.claim("f16", sha, run, extra={"feedback_id": "fb_01ARZ3NDEKTSV4RRFFQ69G5FAV", "at": "2026-10-20T00:00:00.000Z"})
    assert err(fb(c, run, APPROVE, "f16"))[1] == "idempotency_in_progress"   # 켜져 있는 동안은 처리 중
    dep2 = make_deployment(data_dir=dep.settings.data_dir, trace_writer=w)
    r = fb(client_for(dep2), run, APPROVE, "f16")
    assert r.status_code == 201 and r.json()["feedback_id"] == "fb_01ARZ3NDEKTSV4RRFFQ69G5FAV" and r.json()["at"] == "2026-10-20T00:00:00.000Z"
    assert list(dep2.idem.running()) == []   # 실행 키는 그대로(run_interrupted 처리와 따로)


def test_f17_rule_constants_match_tracewriter_and_llmops():
    if hasattr(tracewriter, "LABELS"):   # tracewriter 0.1.3~
        from tracewriter import feedback as twf
        assert (feedback.LABELS, feedback.OK_LABELS, feedback.LABEL_LIST_VERSION) == (twf.LABELS, twf.OK_LABELS, twf.LABEL_LIST_VERSION)
        assert (feedback.ALIAS_RE.pattern, feedback.PATH_RE.pattern, feedback.NODE_RE.pattern) == (twf.ALIAS_RE.pattern, twf.PATH_RE.pattern,
                                                                                                    twf.NODE_RE.pattern)
        assert (feedback.MAX_REASON, feedback.MAX_EDITS, feedback.MAX_PATH, feedback.ACTIONS) == (twf.MAX_REASON, twf.MAX_EDITS, twf.MAX_PATH,
                                                                                                  twf.ACTIONS)
    if not (LLMOPS / "autodiag.py").exists():
        pytest.skip("llmops-lite-v2가 없어 labels-v1 원본 대조를 건너뜀")
    found = {}
    for name, f in (("LABELS", "autodiag.py"), ("OK_LABELS", "autodiag.py"), ("LABEL_LIST_VERSION", "autodiag.py"), ("ALIAS_RE", "deploying.py")):
        for node in ast.parse((LLMOPS / f).read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
                found[name] = ast.literal_eval(node.value.args[0]) if name == "ALIAS_RE" else ast.literal_eval(node.value)
    assert found == {"LABELS": feedback.LABELS, "OK_LABELS": feedback.OK_LABELS, "LABEL_LIST_VERSION": feedback.LABEL_LIST_VERSION,
                     "ALIAS_RE": feedback.ALIAS_RE.pattern}


class _Db:
    def __init__(self):
        self.sql, self.rows = [], {}


class _Cur:
    def __init__(self, db):
        self.db, self._one = db, None

    def execute(self, sql, params=()):
        self.db.sql.append((sql, list(params)))
        self._one = None
        if sql.startswith("INSERT INTO trace.feedback"):
            if params[0] not in self.db.rows:
                self.db.rows[params[0]] = list(params)
                self._one = (params[0],)
        elif sql.startswith("SELECT body_sha256 FROM trace.feedback"):
            self._one = (self.db.rows[params[0]][-1],)

    def fetchone(self):
        return self._one


class _Conn:
    closed = False

    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return _Cur(self.db)

    def close(self):
        self.closed = True


@pytest.mark.skipif(not hasattr(TraceWriter, "record_feedback"), reason="tracewriter 0.1.3 record_feedback 없음(0.1.1 고정본) — git archive 경로로 돌림")
def test_f18_real_record_feedback_with_fake_connection(fb_dep, client_for, cases, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    db = _Db()
    real = TraceWriter(payload_root=tmp_path / "payloads", secrets=_secrets, connect=lambda: _Conn(db))

    class Combo(FakeWriter):   # 실행 기록은 가짜, 피드백은 실제 tracewriter
        record_feedback = staticmethod(real.record_feedback)

    dep, _ = fb_dep(writer=Combo(tmp_path / "trace", secrets=_secrets))
    c = client_for(dep)
    run = post_run(c, cases["C01"], "f18-run").json()["run_id"]
    r = fb(c, run, {**EDIT, "reason": f"이유 {SECRET}", "edits": {"answer.summary": f"값 {SECRET}"}}, "f18")
    assert r.status_code == 201, r.text
    b = r.json()
    (row,) = db.rows.values()
    ins = next(s for s, _ in db.sql if s.startswith("INSERT INTO trace.feedback"))
    cols = ins[ins.index("(") + 1:ins.index(")")].split(", ")
    got = dict(zip(cols, row))
    assert got["feedback_id"] == b["feedback_id"] and got["run_id"] == run and got["review_node"] == "review" and got["source"] == "deploy_api"
    assert got["edited_value_ref"] == b["edited_value_ref"] and got["actor_kind"] == "human" and got["role"] is None and got["contract"] == 1
    assert SECRET not in got["reason"] and got["label_list_version"] == "labels-v1"
    ref = next(p for s, p in db.sql if s.startswith("INSERT INTO trace.payload_ref"))
    assert ref[0] == b["edited_value_ref"][7:] and ref[2] == "feedback_edited_value" and ref[6] == "dev"
    data = (tmp_path / "payloads" / ref[0][:2] / ref[0]).read_bytes()
    assert SECRET.encode() not in data and hashlib.sha256(data).hexdigest() == ref[0]
    # 같은 id · 같은 내용을 기록기에 다시 보내면 duplicate(503 뒤 같은 키 재요청 길)
    again = real.record_feedback(feedback_id=b["feedback_id"], run_id=run, at=b["at"], action="edit", actor="owner-01",
                                 reason=f"이유 {SECRET}", retention_class="dev", label="LLM 출력 틀림", review_node="review",
                                 edited_value={"answer.summary": f"값 {SECRET}"})
    assert again["result"] == "duplicate"
    # tracewriter check_feedback 경로 → API 경로(RX-F1): 비밀 값이 든 별명은 배포단이 먼저 막고 문구에 값이 없음
    probs = feedback.check_shared(tracewriter.check_feedback, {"feedback_id": b["feedback_id"], "run_id": run, "source": "deploy_api",
                                  "action": "edit", "actor": "owner-01", "reason": "x", "retention_class": "dev", "at": b["at"],
                                  "label": "LLM 출력 틀림", "edited_value": {"answer.a..b": 1}}, _secrets())
    assert probs == [('$.edits["answer.a..b"]', "칸 경로 모양이 아닙니다")]
