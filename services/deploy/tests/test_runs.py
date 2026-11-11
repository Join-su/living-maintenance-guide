"""골격 시험(지시 005, 006 1단계): 가짜 provider + 파일 backend로 EventCard 한 건 → 보고서, 같은 Idempotency-Key 재요청, 오류 공통 형식,
기록 실패 = 실행 오류, 배포 등록 대조. 네트워크·실제 provider 없음."""
import hashlib
import json
import os
import sqlite3

import packrt
import pytest

from conftest import V0_REVISION
from deploy_server.fake_provider import FakeProvider
from fake_writer import FakeWriter
from tracewriter import TraceWriteError, sqlite_registration_check


def post(client, body, key="key-1", **headers):
    h = {"Idempotency-Key": key} if key is not None else {}
    h.update(headers)
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=h)


def err(resp):
    return resp.json()["error"]


# ------------------------------------------------------------------ EventCard 한 건 → 보고서
def test_event_card_to_report(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    r = post(c, cases["C01"])
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "succeeded" and b["outcome"] == "completed"
    assert b["deployment_id"] == "task10@dev-local"
    assert b["pack"]["pack_revision"] == V0_REVISION and b["pack"]["commit"].startswith("93ca0a1")
    assert b["data_release_id"].startswith("drs:") and len(b["data_release_id"]) == 20
    assert b["runtime_version"] == packrt.__version__   # 불러온 packrt 판(이번 주는 llmops .venv의 설치본)
    assert b["review"] == {"actor_kind": "auto", "action": "approve", "note": "자동 승인(사람 검토 아님)"}
    assert r.headers["Location"] == b["href"] == f"/runs/{b['run_id']}"
    assert "Idempotent-Replayed" not in r.headers

    # 보고서 파일과 GET /reports
    rep = c.get(b["report"]["href"]).json()
    f = dep.settings.data_dir / rep["location"]
    data = f.read_bytes()
    assert hashlib.sha256(data).hexdigest() == rep["sha256"] == b["report"]["sha256"] and rep["size"] == len(data)
    assert data.decode().startswith("# 정비 대응 보고서 — 사건 EVT-03530")
    assert rep["format"] == "md" and rep["run_id"] == b["run_id"] and rep["node_path"] == "respond.report"
    assert not os.access(f, os.W_OK)

    # GET /runs: 근거가 붙은 결과
    run = c.get(b["href"]).json()
    assert run["status"] == "succeeded" and run["result"]["output"] == data.decode()
    g = run["result"]["grounding"]
    evidence_ids = {e["id"] for e in g["evidence"]}
    cited = {i for cand in g["answer"]["cause_candidates"] for i in cand["evidence_ids"]} | {i for p in g["answer"]["procedures"] for i in p["evidence_ids"]}
    assert cited and cited <= evidence_ids
    assert g["review"]["actor_kind"] == "auto"


def test_trace_events_follow_contract_2(make_deployment, client_for, cases):
    dep = make_deployment()
    b = post(client_for(dep), cases["C01"]).json()
    ev = dep.trace.events_of(b["run_id"])
    assert [e["seq"] for e in ev] == list(range(len(ev)))
    assert {e["trace_id"] for e in ev} == {b["trace"]["trace_id"]}
    first, last = ev[0], ev[-1]
    assert first["type"] == "run_started" and last["type"] == "run_ended"
    sb = first["body"]
    assert (sb["origin"], sb["source_kind"], sb["deployment_id"], sb["retention_class"]) == ("deployment", "agent_pack", "task10@dev-local", "dev")
    assert (sb["pack_id"], sb["pack_revision"], sb["data_release_id"]) == ("task10", V0_REVISION, b["data_release_id"])
    assert set(sb["release_bindings"]) == {"history", "manuals"} and sb["release_bindings"]["manuals"]["release_id"] == "ir-manuals-v2"
    assert last["body"]["status"] == "succeeded" and last["body"]["outcome"] == "completed"

    started = [e for e in ev if e["type"] == "node_started"]
    ended = [e for e in ev if e["type"] == "node_ended"]
    assert sorted(e["node_id"] for e in started) == sorted(e["node_id"] for e in ended)
    assert {"find_history", "find_manuals", "draft", "review", "report"} <= {e["node_id"] for e in ended}
    assert len({e["span_id"] for e in started}) == len(started)            # node 실행마다 span 하나
    assert all(e["parent_span_id"] == first["span_id"] for e in started)  # 부모는 run span
    for s in started:
        e = next(x for x in ended if x["span_id"] == s["span_id"])
        assert e["body"]["status"] == "done" and e["body"]["output_ref"]["ref"].startswith("sha256:")

    draft = next(e for e in ended if e["node_id"] == "draft")["body"]["meta"]
    assert (draft["provider"], draft["model"]) == ("openai", "gpt-4.1-mini") and draft["prompt_ref"]["ref"].startswith("sha256:")
    manuals = next(e for e in ended if e["node_id"] == "find_manuals")["body"]["meta"]
    assert manuals["release_id"] == "ir-manuals-v2" and manuals["results"] and manuals["query_sha256"]

    rec = next(e for e in ev if e["type"] == "review_recorded")["body"]
    assert (rec["actor_kind"], rec["action"]) == ("auto", "approve")
    req = next(e for e in ev if e["type"] == "review_requested")["body"]
    assert req["review_id"] == rec["review_id"] and req["allowed_actions"] == ["approve", "edit", "escalate", "reject"]

    art = next(e for e in ev if e["type"] == "artifact_written")["body"]
    rep = dep.get_report(b["report"]["report_id"])
    assert (art["location"], art["sha256"], art["size"]) == (rep["location"], rep["sha256"], rep["size"])

    # 이벤트의 모든 참조가 원문 파일로 있다(가림 뒤 바이트의 sha256)
    refs = []

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get("ref"), str) and v["ref"].startswith("sha256:"):
                refs.append(v["ref"][7:])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk([e["body"] for e in ev])
    assert refs
    for sha in refs:
        assert hashlib.sha256(dep.trace.payload_path(sha).read_bytes()).hexdigest() == sha


def test_no_alarm_case_records_no_issue(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    b = post(c, cases["C10"]).json()
    assert b["status"] == "succeeded" and b["outcome"] == "no_issue" and b["review"] is None
    assert b["report"]["format"] == "json"
    nodes = [e["node_id"] for e in dep.trace.events_of(b["run_id"]) if e["type"] == "node_ended"]
    assert nodes == ["check_alarm", "record_no_issue"]


def test_traceparent_trace_id_is_kept(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    b = post(c, cases["C01"], traceparent=f"00-{tid}-00f067aa0ba902b7-01").json()
    assert b["trace"]["trace_id"] == tid
    b2 = post(c, cases["C02"], key="key-2", traceparent="garbage").json()
    assert len(b2["trace"]["trace_id"]) == 32 and b2["trace"]["trace_id"] != tid


# ------------------------------------------------------------------ 반복 요청 키
def test_same_key_returns_same_result_without_rerun(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    r1 = post(c, cases["C01"], key="same")
    calls = len(dep.provider.calls)
    reordered = json.loads(json.dumps(cases["C01"], sort_keys=True))   # 키 순서가 달라도 같은 본문
    r2 = c.post("/runs", content=json.dumps(reordered, indent=2).encode(), headers={"Idempotency-Key": "same"})
    assert r2.status_code == r1.status_code == 201
    assert r2.json() == r1.json()
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert len(dep.provider.calls) == calls and dep.runs.count() == 1
    assert len(dep.trace.events) == 1   # 기록된 실행도 하나


def test_same_key_other_body_is_refused(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    first = post(c, cases["C01"], key="k").json()
    r = post(c, cases["C02"], key="k")
    assert r.status_code == 422
    e = err(r)
    assert e["code"] == "idempotency_key_reused" and e["where"] == "header:Idempotency-Key" and e["run_id"] == first["run_id"]
    assert dep.runs.count() == 1


def test_same_key_while_running_is_409(make_deployment, client_for, cases):
    from deploy_server.stores import request_sha256
    dep = make_deployment()
    assert dep.idem.claim("busy", request_sha256(cases["C01"]), "run_01JAB3K6Q8Z5V1X4N2M7P9R0ST")
    r = post(client_for(dep), cases["C01"], key="busy")
    assert r.status_code == 409 and err(r)["code"] == "idempotency_in_progress" and err(r)["retryable"] is True


@pytest.mark.parametrize("key,code", [(None, "idempotency_key_missing"), ("has space", "idempotency_key_invalid"), ("x" * 256, "idempotency_key_invalid")])
def test_key_required(make_deployment, client_for, cases, key, code):
    dep = make_deployment()
    r = post(client_for(dep), cases["C01"], key=key)
    assert r.status_code == 400 and err(r)["code"] == code and err(r)["run_id"] is None
    assert dep.runs.count() == 0


def test_invalid_input_makes_no_run_and_keeps_key(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    bad = {**cases["C01"], "event_id": "EVENT-1"}
    r = post(c, bad, key="fixme")
    assert r.status_code == 422
    e = err(r)
    assert e["code"] == "invalid_input" and e["where"] == "$.event_id" and e["run_id"] is None
    assert dep.runs.count() == 0
    assert post(c, cases["C01"], key="fixme").status_code == 201   # 키를 쓰지 않았으므로 고친 본문을 같은 키로


def test_invalid_json(make_deployment, client_for):
    r = client_for(make_deployment()).post("/runs", content=b"{not json", headers={"Idempotency-Key": "j"})
    assert r.status_code == 400 and err(r)["code"] == "invalid_json"


def test_unknown_ids_are_404(make_deployment, client_for):
    c = client_for(make_deployment())
    assert err(c.get("/runs/run_01JAB3K6Q8Z5V1X4N2M7P9R0ST"))["code"] == "run_not_found"
    assert err(c.get("/reports/rpt_nope"))["code"] == "report_not_found"
    assert c.get("/runs/..%2Fetc").status_code == 404


def test_restart_starts_a_new_key_scope_and_closes_interrupted(tmp_path, make_deployment, client_for, cases):
    """D-296: 실행 키 저장 범위 = 배포 id + Pack revision + 서버 시작. 같은 서버 안 같은 키 = 재생, 다시 띄운 서버에서 같은 키 = 새 실행.
    꺼질 때 실행 중이던 키의 실행은 그대로 run_interrupted(실패)로 닫음."""
    from deploy_server.stores import request_sha256
    data = tmp_path / "shared"
    dep = make_deployment(data_dir=data)
    c1 = client_for(dep)
    first = post(c1, cases["C01"], key="done").json()
    same = post(c1, cases["C01"], key="done")   # 같은 서버 안 재시도(2 · 5 · 10초) = 재생
    assert same.json() == first and same.headers["Idempotent-Replayed"] == "true"
    dep.idem.claim("crashed", request_sha256(cases["C02"]), "run_01JAB3K6Q8Z5V1X4N2M7P9R0SV")   # 실행 중에 꺼진 것처럼
    dep2 = make_deployment(data_dir=data)
    c2 = client_for(dep2)
    assert c2.get("/runs/run_01JAB3K6Q8Z5V1X4N2M7P9R0SV").json()["status"] == "failed"
    assert c2.get("/runs/run_01JAB3K6Q8Z5V1X4N2M7P9R0SV").json()["error"]["code"] == "run_interrupted"
    again = post(c2, cases["C01"], key="done")   # 다시 띄운 서버: 같은 키 = 새 실행(옛 결과 재생 안 함)
    assert again.status_code == 201 and again.json()["run_id"] != first["run_id"] and again.headers.get("Idempotent-Replayed") != "true"
    r = post(c2, cases["C02"], key="crashed")
    assert r.status_code == 201 and r.json()["run_id"] != "run_01JAB3K6Q8Z5V1X4N2M7P9R0SV"


def test_key_scope_includes_deployment_and_pack_revision(tmp_path):
    """D-296 필수 칸: 같은 서버 시작 값이어도 배포 id나 Pack revision이 다르면 다른 범위(같은 키 = 새 실행)."""
    from deploy_server.stores import IdempotencyStore, idem_scope
    base = IdempotencyStore(tmp_path, scope=idem_scope("c1a15@v1-local", "sha256:aaa", "boot1"))
    assert base.claim("k", "req", "run_1")
    base.complete("k", 201, {"run_id": "run_1"})
    assert IdempotencyStore(tmp_path, scope=idem_scope("c1a15@v1-local", "sha256:aaa", "boot1")).lookup("k", "req")["run_id"] == "run_1"
    for other in (idem_scope("c1a15@v1-local", "sha256:bbb", "boot1"), idem_scope("c1a15@dev-local", "sha256:aaa", "boot1"),
                  idem_scope("c1a15@v1-local", "sha256:aaa", "boot2")):
        assert IdempotencyStore(tmp_path, scope=other).lookup("k", "req") is None


def test_deployment_key_scope_uses_its_own_revision(make_deployment):
    from deploy_server.stores import idem_scope
    dep = make_deployment()
    assert dep.idem.scope == idem_scope(dep.settings.deployment_id, dep.runner.rt.revision, dep.boot_id)
    assert dep.feedback_idem.scope is None   # 피드백 키는 범위 없음(다시 띄운 뒤 같은 키로 다시 씀 — 지시 041)


# ------------------------------------------------------------------ 기록 실패 = 실행 오류, 실행 실패
def failing_at(node, code):
    """지정한 node의 node_ended를 기록할 때 기록기 오류를 낸다."""
    return lambda ev: code if ev["type"] == "node_ended" and ev["node_id"] == node else None


@pytest.mark.parametrize("code,status", [("trace_unavailable", 503), ("trace_conflict", 500)])
def test_trace_failure_ends_run_with_error(tmp_path, make_deployment, client_for, cases, code, status):
    dep = make_deployment(trace_writer=FakeWriter(tmp_path / "t", fail=failing_at("draft", code)))
    c = client_for(dep)
    r = post(c, cases["C01"], key="tf")
    assert r.status_code == status
    e = err(r)
    assert e["code"] == code and e["where"] == "trace" and e["run_id"] and e["retryable"] is (code == "trace_unavailable")
    assert "report" not in r.json()                               # 기록 없는 결과는 없음
    run = c.get(f"/runs/{e['run_id']}").json()
    assert run["status"] == "failed" and run["result"] is None and run["report"] is None
    ev = dep.trace.events_of(e["run_id"])
    assert ev[-1]["type"] == "run_ended" and ev[-1]["body"]["status"] == "failed" and ev[-1]["body"]["error"]["code"] == code
    assert not [x for x in ev if x["type"] == "artifact_written"]
    again = post(c, cases["C01"], key="tf")                      # 실패도 저장한 그대로
    assert again.status_code == status and again.json() == r.json()


class BrokenModel(FakeProvider):
    def chat(self, *a, **kw):
        return "not json", {}


def test_node_failure_is_run_failed(make_deployment, client_for, cases):
    dep = make_deployment(provider=BrokenModel())
    r = post(client_for(dep), cases["C01"])
    assert r.status_code == 500
    e = err(r)
    assert e["code"] == "run_failed" and e["where"] == "respond.draft" and e["details"]["node_error"]["code"] == "output_not_json"
    ev = dep.trace.events_of(e["run_id"])
    draft = [x for x in ev if x["type"] == "node_ended" and x["node_id"] == "draft"]
    assert draft and draft[-1]["body"]["status"] == "error"
    assert ev[-1]["type"] == "run_ended" and ev[-1]["body"]["status"] == "failed"


def test_report_write_failure_is_artifact_failed(make_deployment, client_for, cases):
    """보고서 파일을 못 쓰면 packrt가 실행 오류 artifact_failed로 끝낸다(015a). 보고서 없는 성공은 없다."""
    dep = make_deployment()

    def broken(*a, **kw):
        raise OSError("디스크 가득 참(가짜)")
    dep.reports.write = broken
    c = client_for(dep)
    r = post(c, cases["C01"], key="af")
    assert r.status_code == 500
    e = err(r)
    assert e["code"] == "artifact_failed" and e["run_id"] and e["retryable"] is False
    ev = dep.trace.events_of(e["run_id"])
    assert not [x for x in ev if x["type"] == "artifact_written"]
    assert ev[-1]["type"] == "run_ended" and ev[-1]["body"]["status"] == "failed" and ev[-1]["body"]["error"]["code"] == "artifact_failed"
    assert c.get(f"/runs/{e['run_id']}").json()["status"] == "failed"


# ------------------------------------------------------------------ 지시 006 1단계: 태그 배포, 자동 escalate
def test_v0_local_loads_tag(tmp_path, make_deployment, client_for, cases):
    dep = make_deployment(config="v0-local")
    assert (dep.copy.ref, dep.copy.tag) == ("v0", "v0") and dep.copy.commit.startswith("93ca0a1")
    r = post(client_for(dep), cases["C01"])
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["deployment_id"] == "task10@v0-local"
    assert b["pack"] == {"pack_id": "task10", "pack_revision": V0_REVISION, "commit": dep.copy.commit, "tag": "v0"}
    sb = dep.trace.events_of(b["run_id"])[0]["body"]
    assert (sb["deployment_id"], sb["retention_class"]) == ("task10@v0-local", "standard")


class OverclaimingModel(FakeProvider):
    """답 검사(unsafe_assertion)에 걸리는 초안: 근거 없는 확정 표현."""
    def _draft(self, prompt):
        out = super()._draft(prompt)
        out["uncertainty"] = "센서 값으로 보아 원인이 확정되었습니다"
        return out


ESCALATE_NOTE = "자동 escalate(답 검사 실패 초안, 사람 검토 아님)"


def test_answer_check_failure_is_auto_escalated_with_report(make_deployment, client_for, cases):
    dep = make_deployment(provider=OverclaimingModel())
    c = client_for(dep)
    r = post(c, cases["C01"])
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["status"], b["outcome"]) == ("succeeded", "completed")
    assert b["review"] == {"actor_kind": "auto", "action": "escalate", "note": ESCALATE_NOTE}
    rep = c.get(b["report"]["href"]).json()
    assert rep["review"]["action"] == "escalate" and rep["review"]["node_path"] == "respond.review_failed"
    assert (dep.settings.data_dir / rep["location"]).is_file()
    run = c.get(b["href"]).json()
    assert run["result"]["grounding"]["review"]["action"] == "escalate"

    ev = dep.trace.events_of(b["run_id"])
    ended = {e["node_id"]: e for e in ev if e["type"] == "node_ended"}
    assert "review_failed" in ended and "review" not in ended
    checks = ended["answer_check"]["body"]["meta"]["checks"]
    assert any(not x.get("passed", True) for x in (checks if isinstance(checks, list) else checks.values()))
    req = next(e for e in ev if e["type"] == "review_requested")
    rec = next(e for e in ev if e["type"] == "review_recorded")
    assert req["node_id"] == rec["node_id"] == "review_failed"
    assert req["body"]["allowed_actions"] == ["edit", "escalate", "reject"]
    assert rec["body"]["review_id"] == req["body"]["review_id"] == f"{b['run_id']}:review_failed:1"   # 실행 횟수는 1부터(packrt _iter)
    assert (rec["body"]["action"], rec["body"]["actor_kind"], rec["body"]["reason"]) == ("escalate", "auto", ESCALATE_NOTE)
    types = [e["type"] for e in ev]
    assert types.index("review_recorded") < types.index("artifact_written") < types.index("run_ended")
    assert ev[-1]["body"]["outcome"] == "completed"


# ------------------------------------------------------------------ 배포 등록 대조(1주차 방식)
def registry(tmp_path, rows):
    """llmops.db의 deployment 표 모양(1주차 등록 대조가 읽는 칸만)."""
    f = tmp_path / "llmops.db"
    con = sqlite3.connect(f)
    con.execute("CREATE TABLE deployment (deployment_id TEXT, pack_revision TEXT, data_release_id TEXT)")
    con.executemany("INSERT INTO deployment VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return f


def test_unregistered_deployment_runs_no_node(tmp_path, make_deployment, client_for, cases):
    reg = registry(tmp_path, [("task10@dev-local", "sha256:other", "drs:x")])
    dep = make_deployment(trace_writer=FakeWriter(tmp_path / "t", registration_check=sqlite_registration_check(reg)))
    r = post(client_for(dep), cases["C01"])
    assert r.status_code == 500
    e = err(r)
    assert e["code"] == "deployment_not_registered" and e["where"] == "trace" and e["retryable"] is False
    assert e["details"] == {"deployment_id": "task10@dev-local", "pack_revision": V0_REVISION, "data_release_id": dep.runner.data_release_id}
    assert dep.trace.events_of(e["run_id"]) == []                 # run_started부터 거부, node 실행 없음
    assert dep.provider.calls == []


def test_registered_deployment_runs(tmp_path, make_deployment, client_for, cases):
    """등록 줄은 settings registration(mode llmops_db, path)로 서버가 직접 만든 대조 함수로 본다."""
    reg = registry(tmp_path, [("task10@dev-local", V0_REVISION, "drs:b7eec8736389157c")])   # deploy-bootstrap이 낸 값(015 보고서와 같음)
    probe = make_deployment(registration={"mode": "llmops_db", "path": str(reg)})
    assert probe.runner.data_release_id == "drs:b7eec8736389157c"
    check = probe._registration_check()
    dep = make_deployment(data_dir=tmp_path / "d2", trace_writer=FakeWriter(tmp_path / "t", registration_check=check))
    assert post(client_for(dep), cases["C01"]).status_code == 201
    other = make_deployment(data_dir=tmp_path / "d3", config="v0-local", trace_writer=FakeWriter(tmp_path / "t3", registration_check=check))
    assert err(post(client_for(other), cases["C01"]))["code"] == "deployment_not_registered"   # 다른 deployment_id는 거부


def test_registration_db_missing_is_refusal(tmp_path, make_deployment, client_for, cases):
    dep = make_deployment(trace_writer=FakeWriter(tmp_path / "t", registration_check=sqlite_registration_check(tmp_path / "missing.db")))
    assert err(post(client_for(dep), cases["C01"]))["code"] == "deployment_not_registered"


# ------------------------------------------------------------------ 가림·결합·읽기 전용
class LeakyModel(FakeProvider):
    def _draft(self, prompt):
        out = super()._draft(prompt)
        out["uncertainty"] = "키 fake-secret-value-0001 와 sk-fakeABCDEFGHIJKLMNOPQRST 가 섞인 답"
        return out


def test_secrets_never_reach_trace(tmp_path, make_deployment, client_for, cases, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret-value-0001")
    dep = make_deployment(provider=LeakyModel())
    c = client_for(dep)
    r = post(c, cases["C01"])
    assert r.status_code == 201
    leaked = lambda data: b"fake-secret-value-0001" in data or b"sk-fakeABCDEFGHIJKLMNOPQRST" in data
    files = [p for p in dep.settings.data_dir.rglob("*") if p.is_file()]   # 원문 파일·보고서·실행 요약·반복 요청 키 모두
    assert files and not [p for p in files if leaked(p.read_bytes())]
    assert not leaked(json.dumps(dep.trace.events_of(r.json()["run_id"]), ensure_ascii=False).encode())
    assert not leaked(c.get(r.json()["href"]).content) and "[가림]" in c.get(r.json()["href"]).text


def test_pack_copy_is_read_only_and_pinned(make_deployment):
    dep = make_deployment()
    assert dep.runner.rt.revision == V0_REVISION
    assert dep.copy.commit.startswith("93ca0a1") and dep.copy.tag == "v0"   # dev-local은 commit으로 고정하지만 그 commit에 태그 v0이 붙어 있음
    files = [p for p in dep.copy.root.rglob("*") if p.is_file()]
    assert files and not [p for p in files if os.access(p, os.W_OK)]
    assert not list(dep.copy.root.rglob("__pycache__"))

