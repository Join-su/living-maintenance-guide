# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-10(시험 DB): 보고서 원인 · 조치 문장 = 초안 열쇠마다 한 번(report_written) · deploy 길 report Run · 실패 표 · 표지 칸.

VC-44(본 문장 = 확정 문장 · 쓰기 수 · 반려 · 정정으로 초안이 바뀜 · 다시 판정 없는 정정) · VC-45(실패 갈래) — 두 길.
"""

import pytest
from sqlalchemy import text

from fake_deploy import FakeDeploy, engine_answer
from test_api import LEAD_PW, TECH_PW, api, db, login, ok, register, to_instruct  # noqa: F401
from test_correction import fix, seq_of
from test_deploy_path import count, reset_ops

pytestmark = pytest.mark.db
SENT = ("cause", "action")


@pytest.fixture
def path(request, api, monkeypatch):
    """deploy 길(가짜 배포 서버) — 받은 report 본문을 셈. 앱 adapter 쓴 수(inproc 길)는 3단계에서 빠짐(EV-001 ES-16)."""
    from c1a15.deploy import runs
    from c1a15.llm.fixture import FixtureAdapter

    calls = []
    real = FixtureAdapter.write

    def counting(self, facts):
        if facts.kind == "report_sentence":
            calls.append(facts.values["field"])
        return real(self, facts)

    monkeypatch.setattr(FixtureAdapter, "write", counting)
    with FakeDeploy() as fake:
        fake.default = engine_answer
        monkeypatch.setenv("C1A15_JUDGE", "deploy")
        monkeypatch.setenv("C1A15_DEPLOY_URL", fake.url)
        runs.settings.cache_clear()
        runs.client.cache_clear()
        yield {
            "name": "deploy",
            "writes": lambda: sum(p["body"]["kind"] == "report" for p in fake.posts()),
            "fake": fake,
        }
    runs.settings.cache_clear()
    runs.client.cache_clear()


def to_report(api, db, equipment="M-01"):
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, equipment).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report"
    return tech, s


def written(db, sid):
    with db.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text(
                    "select payload from ops.session_events where session_id = :s and type = 'report_written' order by seq"
                ),
                {"s": sid},
            )
        ]


def sentences(s):
    return {f["key"]: (f["text"], f.get("fallback")) for f in s["report"]["fields"] if f["key"] in SENT}


def test_written_once_shown_equals_confirmed(api, db, path):
    """VC-44 · PR-17 · PR-20: 보고서 단계에서 한 번 쓰고, 다시 그리기 · 확정은 다시 쓰지 않음 — 본 문장 = 확정 문장 = 초안(fixture · 가짜 provider)."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    shown = sentences(s)
    assert path["writes"]() == 1 and len(written(db, sid)) == 1
    w = written(db, sid)[0]
    assert w["via"] == path["name"] and w["draft_key"].startswith("sha256:")
    assert {k: (v["text"], v["fallback"], v["attempts"], v["reason"]) for k, v in w["fields"].items()} == {
        k: (shown[k][0], False, 1, None) for k in SENT
    }
    if path["name"] == "deploy":
        assert w["run_id"] and w["deployment_id"] == "c1a15@v1-local"
        body = next(p["body"] for p in path["fake"].posts() if p["body"]["kind"] == "report")
        assert body["session_id"] == f"S-A{s['alarm_id']}" and set(body["report_draft"]) == {
            "equipment_alarm",
            "sensor_evidence",
            "check_path",
            "cause",
            "evidence_records",
            "action",
            "downtime",
            "unresolved",
        }
        assert body["report_draft"]["cause"] == shown["cause"][0] and "4.3.2" in body["allowed_sections"]
        with db.connect() as c:  # D-290 ②: similar · allowed_sections를 세션 값 빠짐없이
            sim = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
        assert [x["record_id"] for x in body["similar"]] == [x["record_id"] for x in sim] and len(sim) == 10
        assert {"1.2", "4.2", "4.3", "4.3.2", "6.1"} <= set(body["allowed_sections"])
    assert shown["cause"] == ("냉각 필터 막힘", False)  # 기준선 fixture 문장 그대로(PR-20)
    assert shown["action"][0] == "흡기 필터 교체(4.3.2, A) · 재가동(6.1, B, 확인 완료) 후 온도 차 9.0 K 경계 이내"
    for _ in range(3):
        assert sentences(tech.get(f"/api/tech/sessions/{sid}").json()) == shown
    s = ok(tech, s, type="report_confirm")
    assert sentences(s) == shown and path["writes"]() == 1 and len(written(db, sid)) == 1
    with db.connect() as c:
        fields = c.execute(text("select fields from ops.reports where session_id = :s"), {"s": sid}).scalar()
    assert {f["key"]: f["text"] for f in fields if f["key"] in SENT} == {k: v[0] for k, v in shown.items()}


def test_rejected_then_correction_changes_draft_writes_new_sentence(api, db, path):
    """ISS-701 ① · A7 / AC-11 · A13 / AC-45 · VC-44 ②: 반려 → 같은 키(항목만) 다시 쓰지 않음 → 조치 뒤 정정 → 정정 호출 → 주간 처리 답 →
    원인 미상 보고서 → 초안 열쇠가 바뀌어 새 문장 한 번 → 확정 문장 = 새 원인 · 승인 이력 cause_code 없음. similar는 그대로(PR-10, A12-N3)."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    with db.connect() as c:
        sim0 = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
    s = ok(tech, s, type="report_confirm")
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=sid)
    assert (
        lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시 확인"}).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert (
        s["step"]["kind"] == "report" and len(written(db, sid)) == 1 and path["writes"]() == 1
    )  # 반려로 다시 열림 = 같은 열쇠
    s = fix(tech, s, target_seq=seq_of(db, sid, "result", "4.3"), value="정상", reason="다시 보니 깨끗함").json()
    assert s["step"]["kind"] == "call" and s["step"]["corrected"]
    s = ok(tech, s, type="call_send")
    row = next(x for x in lc.get("/api/lead/calls").json()["rows"] if x["equipment_id"] == "M-01")
    assert (
        lc.post(f"/api/lead/calls/{row['call_id']}/answer", json={"answer": "daytime", "note": "주간 확인"}).status_code
        == 200
    )
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["step"]["kind"] == "report"
    ws = written(db, sid)
    assert len(ws) == 2 and path["writes"]() == 2 and ws[0]["draft_key"] != ws[1]["draft_key"]
    shown = sentences(s)
    assert shown["cause"] == ("원인 미상", False) and ws[1]["fields"]["cause"]["text"] == "원인 미상"
    s = ok(tech, s, type="report_confirm")
    assert sentences(s) == shown
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"}).status_code == 200
    with db.connect() as c:
        rec = c.execute(
            text("select record from knowledge.history_records where record_id = :i"), {"i": f"R-{rid:04d}"}
        ).scalar()
        sim1 = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
    assert rec["outcome"]["cause_code"] is None and "원인 미상" in rec["narrative"]
    assert sim1 == sim0  # 열쇠에 similar를 넣지 않는 근거: 세션 동안 그대로(PR-10)


def test_correction_without_rejudge_keeps_sentence(api, db, path):
    """A12-N4 · VC-44 ②: 보고서 단계에서 다시 판정 없는 정정(사전 확인 측정값) → 다음 단계 그대로 · 문장 그대로 · 쓰기 0."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    shown = sentences(s)
    path0 = {f["key"]: f["text"] for f in s["report"]["fields"]}["check_path"]
    r = fix(tech, s, target_seq=seq_of(db, sid, "precheck_done"), values="하우징 53 °C", reason="값 잘못 읽음")
    assert r.status_code == 200 and r.json()["step"]["kind"] == "report"
    assert sentences(r.json()) == shown and len(written(db, sid)) == 1 and path["writes"]() == 1
    path1 = {f["key"]: f["text"] for f in r.json()["report"]["fields"]}["check_path"]
    assert path1 == path0  # 사전 확인 측정값은 보고서 8칸에 없음(기준선 그대로) — 정정 표시는 결과 정정에서(다음 시험)


def test_draft_changed_when_no_written_matches():
    """A12-N4 · CH-20: 열쇠가 맞는 report_written이 없으면 두 칸 원문 · draft_changed(화면 조회는 쓰지 않음)."""
    from types import SimpleNamespace

    from c1a15.api import flow

    row = SimpleNamespace(similar=[{"record_id": "H-0001", "cause": "CL-1", "score": 0.9}])
    draft = {"cause": "냉각 필터 막힘", "action": "흡기 필터 교체(4.3.2, A)", "check_path": "잠금 확인(1.2)"}
    other = {"type": "report_written", "draft_key": "sha256:other", "fields": {k: {"text": "옛 문장"} for k in SENT}}
    key = flow.draft_key(row, [], draft)
    other |= {"seq": 7}
    got = flow.report_sentences(row, [other], draft, key)
    assert got == (
        {k: {"text": draft[k], "fallback": True, "attempts": None, "reason": "draft_changed"} for k in SENT},
        7,
    )
    hit = {"type": "report_written", "seq": 9, "draft_key": key, "fields": {k: {"text": f"새 {k}"} for k in SENT}}
    assert flow.report_sentences(row, [other, hit], draft, key) == (hit["fields"], 9)
    assert flow.draft_key(row, [], {**draft, "cause": "원인 미상"}) != key  # 원인 원문이 바뀌면 열쇠가 바뀜


# ---------------------------------------------------------------- VC-45 실패 표(deploy 길, preview v12 J 갈래마다)


@pytest.fixture
def deploy_fast(api, monkeypatch):
    """가짜 배포 서버 + 기다림 0 · report 시간 초과 0.5초 클라이언트(같은 키 3번 · 새 키 한 번 규칙은 그대로)."""
    from c1a15.deploy import runs
    from c1a15.deploy.client import Client
    from c1a15.deploy.ledger import DbLedger

    with FakeDeploy() as fake:
        fake.default = engine_answer
        monkeypatch.setenv("C1A15_JUDGE", "deploy")
        monkeypatch.setenv("C1A15_DEPLOY_URL", fake.url)
        runs.settings.cache_clear()
        cl = Client(
            fake.url, DbLedger(), sleep=lambda _s: None, timeouts={"report": 0.5}, poll_every=0.1, poll_limit=0.5
        )
        monkeypatch.setattr(runs, "client", lambda: cl)
        yield fake
    runs.settings.cache_clear()


def fail(code, status=500, **kw):
    return {"status": status, "code": code, **kw}


CASES = {  # 이름: (report Run 계획, 기대 reason, 기대 report 시도 수)
    "write_report": ([fail("run_failed", where="run.write_report", node_code="provider_error")], "provider_failure", 1),
    "rewrite_report_retryable": (
        [fail("run_failed", where="run.rewrite_report", node_retryable=True)] * 2,
        "provider_failure",
        2,
    ),  # node_error.retryable → 새 키 한 번 뒤 끝
    "trace_unavailable": ([fail("trace_unavailable", status=503, retryable=True)] * 2, "pack_unavailable", 2),
    "deployment_not_registered": ([fail("deployment_not_registered")], "pack_unavailable", 1),
    "internal_error": ([fail("internal_error")], "pack_unavailable", 1),
    "invalid_input": ([fail("invalid_input", status=422)], "pack_unavailable", 1),
    "bad_llm_output": (  # D-290 ①: LLM 출력이 JSON 아님 · 모양 틀림 = Run 실패(다시 시도 없음)
        [fail("run_failed", where="run.write_report", node_code="output_invalid")],
        "provider_failure",
        1,
    ),
    "other_where": ([fail("run_failed", where="run.validate_report")], "pack_unavailable", 1),
    "unknown_code": ([fail("something_new")], "pack_unavailable", 1),
    "timeout": (
        [{"use_default": True, "delay": 2.0}],  # 시간 초과 → 같은 키 = 실행 중 409 → 폴링 한도 뒤 끝
        "pack_unavailable",
        1,
    ),  # 같은 키 3번 다시 = 그 키 실행 중(409 폴링 끝까지는 X)
}


@pytest.mark.parametrize("case", list(CASES))
def test_report_run_failure_falls_back_and_confirm_works(api, db, deploy_fast, case):
    """VC-45 · CH-22 · PR-19: 끝내 실패 → 두 칸 원문 · 표지 · reason · attempts null · 확정 200 · 503 0 · 감사 · 3회 셈 0."""
    plan, reason, tries = CASES[case]
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    from c1a15.api import sessions as svc

    with db.connect() as c:
        fails0 = svc.llm_failures(svc.events_of(c, s["session_id"]))
    deploy_fast.plan = [{"use_default": True}] + plan  # 다음 실행 = 보고서 단계를 여는 judge, 그 뒤 report
    r = tech.post(
        f"/api/tech/sessions/{s['session_id']}/events",
        json={"seq": s["last_seq"] + 1, "type": "restart_result", "outcome": "within"},
    )
    assert r.status_code == 200, r.text[:300]  # 503 안내 없음
    s = r.json()
    w = written(db, s["session_id"])[-1]
    assert {k: (v["fallback"], v["attempts"], v["reason"]) for k, v in w["fields"].items()} == {
        k: (True, None, reason) for k in SENT
    }
    assert w["fields"]["cause"]["text"] == "냉각 필터 막힘" and w["fields"]["action"]["text"].startswith(
        "흡기 필터 교체(4.3.2, A)"
    )
    assert sentences(s) == {k: (w["fields"][k]["text"], True) for k in SENT}  # 원문 = 앱 초안 · 표지
    keys = [p["headers"]["Idempotency-Key"] for p in deploy_fast.posts() if p["body"]["kind"] == "report"]
    assert len(set(keys)) == tries  # 기본 키(+ 새 키 한 번)
    with db.connect() as c:
        aud = c.execute(
            text(
                "select detail from ops.audit_log where action = 'pack_call_failed' and detail->>'kind' = 'report' order by audit_id desc limit 1"
            )
        ).scalar()
    want = plan[-1]
    if "code" in want:  # 감사 = CH-15 그대로: code · where · node_code(사람에게 알림 갈래는 code 값으로 구분)
        assert aud["code"] == want["code"] and aud["where"] == want.get("where", "$")
        assert aud["node_code"] == want.get("node_code")
    else:
        assert aud["code"] in {"timeout", "poll_timeout", "idempotency_in_progress"}
    with db.connect() as c:
        assert svc.llm_failures(svc.events_of(c, s["session_id"])) == fails0  # H-4: 보고서 원문 대체는 3회 셈에 안 듦
    s = ok(tech, s, type="report_confirm")
    assert s["report"]["status"] == "awaiting_approval"


def test_report_run_ungrounded_twice_only_that_field(api, db, deploy_fast):
    """VC-45 마지막 갈래: Run 성공 · 조치 칸만 근거 검사 두 번 떨어짐 → 그 칸만 표지(Pack 출력 그대로)."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    out = {
        "report": {
            "cause": {
                "text": "냉각 필터 막힘으로 온도 차가 낮아졌습니다.",
                "fallback": False,
                "attempts": 1,
                "reason": None,
            },
            "action": {"text": "ACTION_DRAFT", "fallback": True, "attempts": 2, "reason": "ungrounded_number"},
        }
    }
    deploy_fast.plan = [{"use_default": True}, {"output": out}]
    s = ok(tech, s, type="restart_result", outcome="within")
    got = sentences(s)
    assert got["cause"] == ("냉각 필터 막힘으로 온도 차가 낮아졌습니다.", False) and got["action"] == (
        "ACTION_DRAFT",
        True,
    )
    w = written(db, s["session_id"])[-1]
    assert w["fields"]["action"] == out["report"]["action"]


def test_report_run_unreachable_falls_back(api, db, deploy_fast, monkeypatch):
    """VC-45 '못 닿음': report만 닫힌 포트로 — 같은 키 3번 뒤 두 칸 원문 · pack_unavailable · 확정 가능."""
    import socket

    from c1a15.deploy import runs
    from c1a15.deploy.client import Client
    from c1a15.deploy.ledger import DbLedger

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead = Client(f"http://127.0.0.1:{sock.getsockname()[1]}", DbLedger(), sleep=lambda _s: None)
    sock.close()  # 아무도 듣지 않는 포트
    live = runs.client()

    class Split:
        def run(self, kind, *a):
            return (dead if kind == "report" else live).run(kind, *a)

    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    monkeypatch.setattr(runs, "client", lambda: Split())
    s = ok(tech, s, type="restart_result", outcome="within")
    w = written(db, s["session_id"])[-1]
    assert {k: (v["fallback"], v["reason"]) for k, v in w["fields"].items()} == {
        k: (True, "pack_unavailable") for k in SENT
    }
    rows = [
        r[0]
        for r in db.connect().execute(
            text(
                "select detail->>'code' from ops.audit_log where action = 'pack_call_failed' and detail->>'kind' = 'report'"
            )
        )
    ]
    assert rows == ["unreachable"]  # 같은 키 3번 다시(2 · 5 · 10초 — 시험은 기다림 0) 뒤 끝 — 감사는 끝난 실패 한 줄
    assert ok(tech, s, type="report_confirm")["report"]["status"] == "awaiting_approval"


def correction_to_daytime_report(api, db, tech, s, lc):
    """조치 뒤 4.3 결과 정정 → 정정 호출 → 파트장 '주간 처리' 답 → 원인 미상 보고서(초안 원인 · 조치가 바뀜)."""
    sid = s["session_id"]
    s = fix(tech, s, target_seq=seq_of(db, sid, "result", "4.3"), value="정상", reason="다시 보니 깨끗함").json()
    assert s["step"]["kind"] == "call" and s["step"]["corrected"]
    s = ok(tech, s, type="call_send")
    row = next(x for x in lc.get("/api/lead/calls").json()["rows"] if x["equipment_id"] == "M-01")
    assert (
        lc.post(f"/api/lead/calls/{row['call_id']}/answer", json={"answer": "daytime", "note": "주간 확인"}).status_code
        == 200
    )
    return tech.get(f"/api/tech/sessions/{sid}").json()


def test_rejudge_correction_before_report_writes_new_and_old_edit_does_not_override(api, db, path):
    """VC-44 ② · B1: 보고서 단계에서 원인을 고친 뒤 확정 전 정정(다시 판정 · 정정 호출) → 새 초안 → 새 문장 +1 —
    앞서 고친 원인(report_edit)은 새 문장을 덮지 않음(그 문장 기록 뒤 수정만), 정정 표시 그대로."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    s = ok(tech, s, type="report_edit", field="cause", text="냉각 필터 막힘 — 필터 표면 기름때")
    assert {f["key"]: f for f in s["report"]["fields"]}["cause"]["edited"]
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    s = correction_to_daytime_report(api, db, tech, s, lc)
    assert s["step"]["kind"] == "report" and len(written(db, sid)) == 2 and path["writes"]() == 2
    f = {x["key"]: x for x in s["report"]["fields"]}
    assert f["cause"]["text"] == "원인 미상" and not f["cause"]["edited"]
    assert "정정" in f["check_path"]["text"]
    s = ok(tech, s, type="report_confirm")
    assert {x["key"]: x["text"] for x in s["report"]["fields"]}["cause"] == "원인 미상"


def test_legacy_session_rejected_keeps_flow06(api, db, path):
    """B2 · Q-18: 이 판 전에 확정된 세션(report_written 없음)을 반려 → 반려 때 첫 기록이 써져도 확정했던 문장에서 다시 고침(FLOW-06)."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    s = ok(tech, s, type="report_edit", field="check_path", text=s["report"]["fields"][2]["text"] + " · 메모")
    s = ok(tech, s, type="report_confirm")
    confirmed = {x["key"]: x["text"] for x in s["report"]["fields"]}
    with db.begin() as c:  # 이 판 전 세션 흉내: 문장 기록을 지움(시험 DB만)
        c.execute(text("delete from ops.session_events where session_id = :s and type = 'report_written'"), {"s": sid})
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    rid = count(db, "select report_id from ops.reports where session_id = :s", s=sid)
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "다시"}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["step"]["kind"] == "report" and len(written(db, sid)) == 1  # 반려로 다시 열 때 첫 기록
    assert {
        x["key"]: x["text"] for x in s["report"]["fields"]
    } == confirmed  # 확정했던 문장(고친 점검 경로 포함) 그대로


def test_grade_change_in_report_step_keeps_sentence(api, db, path):
    """B2 · R13: 보고서 단계가 열린 뒤 파트장 등급표 변경(_open_step 안 거침) → 열쇠 그대로 · 표지 없음 · 쓴 문장 그대로 확정."""
    reset_ops(db)
    tech, s = to_report(api, db)
    sid = s["session_id"]
    shown = sentences(s)
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    assert lc.post("/api/lead/grades/4.3.2", json={"grade": "B", "confirmed": True}).status_code == 200
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert sentences(s) == shown and "(4.3.2, A)" in shown["action"][0]
    s = ok(tech, s, type="report_confirm")
    assert sentences(s) == shown and len(written(db, sid)) == 1


def test_report_run_malformed_201_falls_back(api, db, deploy_fast):
    """201인데 report 모양이 아님 → 500으로 막히지 않고 두 칸 원문 · pack_unavailable."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    deploy_fast.plan = [{"use_default": True}, {"output": {"report": {"cause": {"text": "x"}}}}]
    s = ok(tech, s, type="restart_result", outcome="within")
    w = written(db, s["session_id"])[-1]
    assert {k: (v["fallback"], v["reason"]) for k, v in w["fields"].items()} == {
        k: (True, "pack_unavailable") for k in SENT
    }


def test_pack_body_has_no_lead_answer_note(api, db, path):
    """D-294 · PR-16 · VC-35: Pack report 본문 미확인 칸에서 파트장 답 메모만 뺌 — 화면 · 확정 보고서는 그대로(메모 포함)."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    lc, _ = login(api, "lead", "lead.park", LEAD_PW)
    s = correction_to_daytime_report(api, db, tech, s, lc)  # 답 메모 '주간 확인'
    screen_unresolved = {f["key"]: f["text"] for f in s["report"]["fields"]}["unresolved"]
    note = " · 주간 확인"  # 파트장 답 메모(호출 답 note)
    assert screen_unresolved.endswith("주간 처리 요청" + note)
    if path["name"] == "deploy":
        body = [p["body"] for p in path["fake"].posts() if p["body"]["kind"] == "report"][-1]
        assert body["session_id"] == f"S-A{alarm_id}"
        assert (
            body["report_draft"]["unresolved"] == screen_unresolved[: -len(note)]
        )  # 메모만 빠지고 답 표시 · 정정 내용 그대로
