"""P-06 API 통합 시험(실제 PostgreSQL, marker db). 시험 DB는 매번 새로 만들고, 비밀값은 verify-db가 무작위로 만든다."""

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db
SERVER = Path(__file__).resolve().parents[1]
URL = os.environ.get("DATABASE_URL", "")
TECH_PW, LEAD_PW = os.environ.get("C1A15_TECH_PASSWORD", ""), os.environ.get("C1A15_LEAD_PASSWORD", "")
SENSOR = {
    "air_temperature_k": 302.5,
    "process_temperature_k": 310.4,
    "rotational_speed_rpm": 1500,
    "torque_nm": 40.0,
    "tool_wear_min": 100,
    "temp_diff_k": 7.9,
    "power_w": 6283,
}


# 출력 이탈(PW) 알람 — 원값 5칸과 계산 칸이 맞게(배포 서버가 원값에서 다시 셈)
# 57.3 Nm × 1500 rpm = 9,001 W · 온도 차 9.5 K
PW_SENSOR = {**SENSOR, "process_temperature_k": 312.0, "torque_nm": 57.3, "temp_diff_k": 9.5, "power_w": 9001}


@pytest.fixture(scope="module")
def db():
    if not URL:
        pytest.skip("DATABASE_URL 없음")
    env = {**os.environ, "DATA_DIR": str(SERVER.parent / "data"), "PYTHONPATH": str(SERVER / "src")}
    steps = (
        ["alembic", "downgrade", "base"],
        ["alembic", "upgrade", "head"],
        ["python", "-m", "c1a15.loader"],
    )
    for args in steps:
        subprocess.run(args, cwd=SERVER, env=env, check=True)
    return create_engine(URL)


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setenv("C1A15_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("APP_MODE", "fixture")
    monkeypatch.setenv("DATA_DIR", str(SERVER.parent / "data"))
    from c1a15.api import deps
    from c1a15.api.main import app

    deps.reset_caches()
    return app


def client(app, surface):
    return TestClient(app, headers={"X-App": surface})


def login(app, surface, user, pw):
    c = client(app, surface)
    r = c.post("/api/login", json={"user_id": user, "password": pw})
    return c, r


# 시험 알람의 출처 표시(deploy 길 Pack 알람 입력 필수 칸, EV-001 ES-08) — 기본 길은 쓰지 않는다
TEST_LINEAGE = {"source_release": "ai4i-2020@sha256:test", "row_id": 1, "split": "test"}


def register(app, equipment="M-01", sensor=None, headers=None):
    c = TestClient(app)
    return c.post(
        "/api/internal/alarms",
        json={"equipment_id": equipment, "sensor_snapshot": sensor or SENSOR, "data_lineage": TEST_LINEAGE},
        headers=headers if headers is not None else {"X-Internal-Token": "test-internal-token"},
    )


def reset_ops(db):
    with db.begin() as c:
        for t in ("audit_log", "calls", "reports", "session_events", "diag_sessions", "alarms", "login_sessions"):
            c.execute(text(f"delete from ops.{t}"))
        c.execute(
            text(
                "update ops.grade_table g set grade = s.initial_grade "
                "from knowledge.manual_sections s where s.section_id = g.section_id"
            )
        )


def test_role_login_and_session_boundary(api, db):
    """AC-12 · ADV-06(API 부분): 앱 × 역할, 위조 앱 표시, 다른 앱 쿠키, 쿠키 없음, 로그아웃 · 만료 세션 거부."""
    reset_ops(db)
    assert login(api, "tech", "lead.park", LEAD_PW)[1].status_code == 403  # AC-12-C1
    assert login(api, "lead", "tech.kim", TECH_PW)[1].status_code == 403  # AC-12-C2
    assert login(api, "tech", "tech.kim", "wrong")[1].status_code == 401
    tech, r = login(api, "tech", "tech.kim", TECH_PW)
    assert r.status_code == 200 and "tech_session" in tech.cookies  # AC-12-C3
    lead, r = login(api, "lead", "lead.park", LEAD_PW)
    assert r.status_code == 200 and "lead_session" in lead.cookies
    assert tech.get("/api/tech/home").status_code == 200
    stolen = TestClient(api, headers={"X-App": "tech"}, cookies={"tech_session": lead.cookies["lead_session"]})
    assert stolen.get("/api/tech/home").status_code == 403  # 파트장 세션을 정비자 쿠키 이름으로
    cross = TestClient(api, headers={"X-App": "lead"}, cookies={"tech_session": tech.cookies["tech_session"]})
    assert cross.get("/api/tech/home").status_code == 401  # 파트장 앱 경로에는 lead_session이 없다
    assert TestClient(api).get("/api/tech/home").status_code == 403  # X-App 없음
    assert client(api, "tech").get("/api/tech/home").status_code == 401  # 쿠키 없음
    with db.begin() as c:
        c.execute(
            text("update ops.login_sessions set expires_at = now() - interval '1 minute' where user_id = 'lead.park'")
        )
    assert lead.get("/api/records/H-0001").status_code == 401  # 만료
    assert tech.post("/api/logout").status_code == 200
    assert tech.get("/api/tech/home").status_code == 401  # 로그아웃 뒤 즉시 무효
    with db.connect() as c:
        stored = [h for (h,) in c.execute(text("select token_hash from ops.login_sessions"))]
    assert all(len(h) == 64 for h in stored)  # 원 세션 ID는 저장하지 않음


def test_alarm_registration_and_r9_assignment(api, db):
    """AC-28 · AC-34-C1: 내부 호출만 등록, 시각 = 지금, 담당자 · 최소 부하 · 미배정 → 다음 로그인."""
    reset_ops(db)
    assert register(api, headers={}).status_code == 403
    assert (
        register(api, headers={"X-Internal-Token": "test-internal-token", "X-App": "tech"}).status_code == 403
    )  # nginx 경유는 거부
    r = register(api, "M-01")
    assert r.status_code == 200 and r.json()["assigned_to"] is None  # 로그인한 정비자 없음 → 미배정(AC-28-C3)
    tech, _ = login(api, "tech", "tech.lee", TECH_PW)
    with db.connect() as c:
        assert (
            c.execute(
                text("select assigned_to from ops.alarms where alarm_id = :a"), {"a": r.json()["alarm_id"]}
            ).scalar()
            == "tech.lee"
        )
    owner = {"M-01": "tech.kim"}  # 담당표: 번호순으로 tech.kim · tech.lee · tech.choi · tech.jung
    assert (
        register(api, "M-01").json()["assigned_to"] == "tech.lee"
    )  # 담당자 tech.kim 미로그인 → 로그인한 사람 중 최소 부하(AC-28-C2)
    login(api, "tech", owner["M-01"], TECH_PW)
    assert register(api, "M-01").json()["assigned_to"] == "tech.kim"  # 담당자 로그인 → 담당자(AC-28-C1)
    home = tech.get("/api/tech/home").json()
    assert {a["equipment_id"] for a in home["alarms"]} == {"M-01"} and home["change_seq"] > 0  # AC-13(API 부분)
    assert tech.get(f"/api/tech/home?since={home['change_seq']}").json()["new_since"] == []


def test_commit_before_response_is_sent(api, db):
    """앞 slice 결함 수리(S-06 G5, 평가 실행에서 발견): 요청 transaction은 응답을 보내기 전에 commit한다.

    틀리면 실패: commit이 응답 시작 뒤에 오면(FastAPI 기본 scope='request')
    빠른 클라이언트의 다음 요청이 방금 쓴 기록을 못 봐 404 no_session ·
    409 stale_seq가 산발적으로 난다(평가 실행 EC-01 · EC-05 · EC-09)."""
    from sqlalchemy import event

    from c1a15.api import deps

    order: list[str] = []

    def on_commit(_conn):
        order.append("commit")

    async def wrapped(scope, receive, send):
        async def send2(msg):
            if msg["type"] == "http.response.start":
                order.append("response")
            await send(msg)

        await api(scope, receive, send2)

    reset_ops(db)
    event.listen(deps.engine(), "commit", on_commit)
    try:
        r = TestClient(wrapped).post(
            "/api/login", json={"user_id": "tech.kim", "password": TECH_PW}, headers={"X-App": "tech"}
        )
    finally:
        event.remove(deps.engine(), "commit", on_commit)
    assert r.status_code == 200
    assert "commit" in order and order.index("commit") < order.index("response"), order


def test_alarm_registration_eval_mode(api, db):
    """P-16 · 결정 017 ②: 내부 등록의 eval_mode=true만 alarms.eval_mode 참(기본 거짓), 감사 행에도 평가 모드 표시."""
    reset_ops(db)
    c = TestClient(api)
    h = {"X-Internal-Token": "test-internal-token"}
    base = {"equipment_id": "M-01", "sensor_snapshot": SENSOR, "data_lineage": TEST_LINEAGE}  # deploy 길 = 출처 칸 필요
    a = c.post("/api/internal/alarms", json={**base, "eval_mode": True}, headers=h)
    b = c.post("/api/internal/alarms", json=base, headers=h)
    assert a.status_code == 200 and b.status_code == 200
    with db.connect() as conn:
        flags = dict(conn.execute(text("select alarm_id, eval_mode from ops.alarms")).all())
        audit_eval = conn.execute(
            text("select count(*) from ops.audit_log where action = 'alarm' and detail->>'eval_mode' = 'true'")
        ).scalar()
    assert flags == {a.json()["alarm_id"]: True, b.json()["alarm_id"]: False} and audit_eval == 1
    assert (
        c.post(
            "/api/internal/alarms",
            json={"equipment_id": "M-01", "sensor_snapshot": SENSOR, "eval_mode": True},
            headers={**h, "X-App": "tech"},
        ).status_code
        == 403
    )  # nginx 경유(X-App)는 평가 모드도 거부


def start(api, db, user="tech.kim", equipment="M-01"):
    tech, _ = login(api, "tech", user, TECH_PW)
    alarm_id = register(api, equipment).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    return tech, s


def test_lock_precedes_instruction_and_results(api, db):
    """AC-01 · AC-35 · AC-14(API 부분) · 같은 업무 key 한 번만."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    assert s["step"]["kind"] == "lock" and "instruction" not in s  # AC-01-C1
    assert s["preview"]["kind"] in {"instruct", "instruct_after_checklist", "call"} and "text" not in s["preview"]
    assert tech.get(f"/api/tech/sessions/{sid}/manual/4.3").status_code == 409  # 잠금 전 원문 금지
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    assert (
        s["locked"] and s["lock_at"] and s["step"]["kind"] == "precheck" and s["instruction"]["section"] == "4.2"
    )  # AC-01-C2
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "스핀들 52 °C"},
    ).json()
    step = s["step"]
    assert step["kind"] == "instruct" and s["instruction"]["text"]
    other = "4.5" if step["section"] != "4.5" else "4.6"
    before = s["probabilities"]
    r = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": other, "value": "정상"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "not_instructed"  # AC-35-C1
    assert tech.get(f"/api/tech/sessions/{sid}").json()["probabilities"] == before
    body = {"seq": s["last_seq"] + 1, "type": "result", "section": step["section"], "value": "확인 불가", "memo": ""}
    s2 = tech.post(f"/api/tech/sessions/{sid}/events", json=body).json()
    again = tech.post(f"/api/tech/sessions/{sid}/events", json=body)
    assert again.status_code == 200 and again.json()["last_seq"] == s2["last_seq"]  # 같은 key 재전송 → 기록 1건
    assert tech.post(f"/api/tech/sessions/{sid}/events", json={**body, "value": "정상"}).status_code == 409
    assert sum(s2["probabilities"].values()) == 100 and s2["step"]["section"] != step["section"]  # AC-19-C1(API 부분)
    from c1a15.api import deps

    deps.reset_caches()  # API 프로세스 재시작 흉내
    tech2, _ = login(api, "tech", "tech.kim", TECH_PW)
    resumed = tech2.get(f"/api/tech/sessions/{sid}").json()
    assert (
        resumed["step"] == s2["step"] and resumed["lock_at"] == s2["lock_at"] and resumed["results"] == s2["results"]
    )  # AC-14(API 부분)
    s3 = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s2["last_seq"] + 1, "type": "equipment_change", "equipment_id": "M-02"},
    ).json()
    assert not s3["locked"] and s3["step"]["kind"] == "lock" and "instruction" not in s3  # AC-01-C3
    with db.connect() as c:
        via = [
            v
            for (v,) in c.execute(
                text("select payload->>'via' from ops.session_events where session_id = :s and type = 'step_opened'"),
                {"s": sid},
            )
        ]
    # 재시작 뒤에도 같은 단계 · 다음 단계 = 업무 기록에서 다시 판정한 Run(L3 약속 바뀜 — ES-16, checkpoint 안 씀)
    assert set(via) == {"deploy"}


def test_b_checklist_and_open_step_survives_grade_change(api, db):
    """AC-43(API 부분) · R13 · AC-25-C4(열린 지시는 그대로, 다음 판정부터 새 등급)."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'B' where section_id in ('4.3', '4.5', '4.6')"))
    tech, s = start(api, db)
    sid = s["session_id"]
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    ).json()
    step = s["step"]
    assert (
        step["kind"] == "instruct_after_checklist" and s["instruction"]["checklist"]
    )  # B 지시: 절차와 체크리스트가 함께(실행 해석 ④)
    assert s["instruction"]["text"]
    sec = step["section"]
    r = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": sec, "value": "정상"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "b_checklist_required"  # AC-43-C1
    bad = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={
            "seq": s["last_seq"] + 1,
            "type": "b_checklist_done",
            "section": sec,
            "checked": s["instruction"]["checklist"][:1],
        },
    )
    assert bad.status_code == 409
    expected_common = ["주변 공구·이물질을 치웠다", "커버가 모두 닫혔다", "주변 작업자가 없다"]
    assert s["instruction"]["checklist"] == expected_common  # 절에 목록이 없으면 공통 목록(AC-43-C2)
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "b_checklist_done", "section": sec, "checked": expected_common},
    ).json()
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'C' where section_id = :s"), {"s": sec})
    assert (
        tech.get(f"/api/tech/sessions/{sid}").json()["step"]["section"] == sec
    )  # 열린 지시는 등급 변경에도 그대로(R13)
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": sec, "value": "정상"},
    ).json()
    assert s["step"]["section"] != sec  # AC-43-C3: 확인 뒤 결과 입력이 받아짐


def test_records_manual_and_notifications(api, db):
    """AC-38 · AC-39 · AC-37(API 부분) · 감사 기록 일부(AC-26)."""
    reset_ops(db)
    idle, _ = login(api, "tech", "tech.choi", TECH_PW)
    assert idle.get("/api/tech/notifications").json() == {"items": [], "empty": True}  # AC-37-C3(API 부분)
    tech, s = start(api, db)
    sid = s["session_id"]
    rec = tech.get("/api/records/H-0001").json()
    assert rec["record_id"] == "H-0001" and {
        "equipment_id",
        "occurred_at",
        "symptom",
        "sensor_snapshot",
        "steps",
        "outcome",
        "downtime_min",
        "narrative",
    } <= set(rec)
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    assert tech.get(f"/api/tech/sessions/{sid}/manual/4.3").status_code == 409  # 아직 지시 전(사전 확인 4.2 차례)
    tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    )
    man = tech.get(f"/api/tech/sessions/{sid}/manual/4.3").json()
    assert man["section"] == "4.3" and "판정 기준" in man["text"] and man["grade"] == "A" and man["minutes"] == 3
    items = tech.get("/api/tech/notifications").json()["items"]
    assert any(
        i.items() >= {"kind": "in_progress", "session_id": sid, "alarm_id": s["alarm_id"]}.items() for i in items
    )
    with db.connect() as c:
        actions = {a for (a,) in c.execute(text("select action from ops.audit_log"))}
    assert {"login", "alarm", "session_start", "lock", "instruct"} <= actions


def test_adv07_rejections_and_broken_decide_is_caught(api, db, monkeypatch):
    """ADV-07: 잠금 전 지시 · 지시 밖 결과 · 승인 없는 C 지시 요청은 거부.

    판정 함수 결함(등급 검사 제거)은 시험이 잡는다."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    r = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": "4.3", "value": "정상"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "not_locked"
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    ).json()
    r = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": "4.4", "value": "정상"},
    )
    assert r.status_code == 409  # 4.4(C)는 지시가 아니므로 결과를 받지 않는다(AC-02 · AC-35)
    from c1a15.engine import rules

    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'C' where section_id in ('4.3', '4.5', '4.6')"))
    tech2, s2 = locked_session(api, "tech.lee", "M-02")
    assert_call_without_instruction(tech2, s2)  # 정상 함수: C만 남으면 지시 없이 호출, 그 절 결과는 409
    monkeypatch.setattr(rules, "INSTRUCTABLE", {"A", "B", "C", "D"})  # 판정 함수 결함 사본(등급 검사 제거)
    tech3, s3 = locked_session(api, "tech.choi", "M-03")
    with pytest.raises(AssertionError, match="C 차례가 호출이 아님"):  # S02-G6-4: 실패 지점 고정
        assert_call_without_instruction(tech3, s3)  # 같은 음성 시험이 결함을 잡아 실패한다


def locked_session(api, user, equipment):
    tech, _ = login(api, "tech", user, TECH_PW)
    alarm_id = register(api, equipment).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{s['session_id']}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    return tech, s


def assert_call_without_instruction(tech, s):
    """ADV-07 API 음성 시험: 승인 없는 C 차례 = 호출 단계, 지시 내용 없음, 그 절 결과 · 원문 거부."""
    sid, sec = s["session_id"], s["step"]["section"]
    assert s["step"]["kind"] == "call" and "instruction" not in s, "C 차례가 호출이 아님"
    r = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": sec, "value": "정상"},
    )
    assert r.status_code == 409
    assert tech.get(f"/api/tech/sessions/{sid}/manual/{sec}").status_code == 409


def test_alarm_generator_cli_registers_scenarios(api, db, monkeypatch, capsys):
    """AC-34-C1 · C2 · AC-28 · AC-13(API 부분): 발생기 CLI → 등록 API.

    시각 = 실행 시각, 센서 값 = 각본 행, 배정 = R9.
    """
    from datetime import UTC, datetime

    from c1a15.alarms import __main__ as cli

    reset_ops(db)
    internal = TestClient(api)
    monkeypatch.setenv("C1A15_SCENARIOS", str(SERVER.parent / "data" / "demo" / "scenarios.json"))
    monkeypatch.setattr(
        cli,
        "send",
        lambda base, token, body: internal.post(
            "/api/internal/alarms", json=body, headers={"X-Internal-Token": token}
        ).json(),
    )
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    before = datetime.now(UTC)
    assert cli.main(["--all"]) == 0
    after = datetime.now(UTC)
    items = cli.load_scenarios(cli.scenarios_path())
    with db.connect() as c:
        rows = {
            r.scenario_id: r
            for r in c.execute(
                text(
                    "select scenario_id, equipment_id, sensor_snapshot, trend, occurred_at, assigned_to from ops.alarms"
                )
            )
        }
    assert set(rows) == {s["scenario_id"] for s in items}
    for s in items:
        r = rows[s["scenario_id"]]
        assert before <= r.occurred_at <= after  # AC-34-C1
        assert r.sensor_snapshot == s["sensor_snapshot"] and r.trend == s["trend"]  # AC-34-C2
        assert r.assigned_to == "tech.kim"  # 로그인한 정비자 한 명 → 모두 그에게(AC-28-C1 · C2)
    home = tech.get("/api/tech/home").json()
    assert len(home["alarms"]) == 10 and all(
        a["alarm"]["trend_title"].endswith("최근 12회 측정") for a in home["alarms"]
    )
    assert register(api, "M-99").status_code == 422  # 담당표에 없는 설비
    quiet = {**SENSOR, "process_temperature_k": 312.0, "temp_diff_k": 9.5}  # 원값과 맞는 계산 칸(배포 서버가 다시 셈)
    assert register(api, "M-01", quiet).status_code == 422  # 경계를 넘지 않으면 알람 아님
    out = capsys.readouterr().out
    assert out.count("등록 · 배정 tech.kim") == 10


def test_fixture_full_flow_makes_no_external_call(api, db, monkeypatch):
    """P-05 verifier · AC-16-C2(API 부분): fixture 전체 흐름의 외부 호출 0.

    로그인 → 알람 → 진단(검색 · 문장) → 잠금 → 결과 → 원문 · 기록 · 종 목록.

    Python 소켓 연결은 배포 서버(127.0.0.1 한 곳) 말고 모두 막고 센다.
    DB 연결은 libpq(C)라 세지 않는다(시험 DB 127.0.0.1만).
    """
    import socket

    reset_ops(db)
    calls = []

    from urllib.parse import urlsplit

    from c1a15.deploy import runs

    deploy = urlsplit(runs.settings()["deploy_url"])
    real = socket.socket.connect

    def blocked(sock, address):  # 배포 서버(127.0.0.1 한 곳, PR-08)만 지나감 — 그 밖은 막고 셈
        if isinstance(address, tuple) and address[:2] == (deploy.hostname, deploy.port):
            return real(sock, address)
        calls.append(address)
        raise OSError("network blocked in test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    tech, s = start(api, db)
    sid = s["session_id"]
    assert s["preview"]["kind"] == "instruct" and s["preview"]["reason"]  # fixture 문장
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    ).json()
    sec = s["step"]["section"]
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "result", "section": sec, "value": "정상", "memo": "깨끗함"},
    ).json()
    assert tech.get(f"/api/tech/sessions/{sid}/manual/{sec}").status_code == 200
    assert tech.get("/api/records/H-0001").status_code == 200
    assert tech.get("/api/tech/notifications").status_code == 200
    assert calls == []


def test_server_only_fields_rejected(api, db):
    """요청 스키마(OpenAPI 정본): 서버만 쓰는 event 필드(void · signal · at)와 서버가 정하는 알람 필드는 422.

    void를 받으면 지난 기록을 무효로 만들 수 있고, signal을 받으면 메모 해석(R7)을 건너뛸 수 있다.
    """
    reset_ops(db)
    tech, s = start(api, db)
    sid, nxt = s["session_id"], s["last_seq"] + 1
    for extra in ({"void": True}, {"signal": {"out_of_scope_finding": True}}, {"at": "2020-01-01T00:00:00Z"}):
        r = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": nxt, "type": "lock", **extra})
        assert r.status_code == 422, extra
    assert tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": nxt, "type": "approval"}).status_code == 422
    with db.connect() as c:
        assert c.execute(text("select count(*) from ops.session_events where type = 'lock'")).scalar() == 0
    forged = TestClient(api).post(
        "/api/internal/alarms",
        json={"equipment_id": "M-01", "sensor_snapshot": SENSOR, "symptom_group": "PW", "occurred_at": "2020-01-01"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert forged.status_code == 422


def test_manual_only_for_instructed_sections_and_new_alarm_notification(api, db):
    """B5 · AC-39 · R2: 잠금 뒤에도 지시되지 않은 절(승인 없는 C 4.4 등) 원문은 거부. AC-37-C1(API): 새 알람 항목."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    items = tech.get("/api/tech/notifications").json()["items"]
    new = {"kind": "new_alarm", "alarm_id": alarm_id, "equipment_id": "M-01"}
    assert any(i.items() >= new.items() for i in items)  # 시작 전 = 새 알람(P-19 필드가 더해져도 같은 항목)
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    sid = s["session_id"]
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{sid}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    assert s["step"]["section"] == "4.3"
    assert tech.get(f"/api/tech/sessions/{sid}/manual/4.3").status_code == 200
    assert tech.get(f"/api/tech/sessions/{sid}/manual/4.2").status_code == 200  # 이전에 지시된 사전 확인
    for other in ("4.4", "4.5", "7.2"):
        r = tech.get(f"/api/tech/sessions/{sid}/manual/{other}")
        assert r.status_code == 409 and r.json()["detail"] == "not_instructed"
    with db.connect() as c:
        n = c.execute(
            text('select jsonb_array_length("similar") from ops.diag_sessions where session_id = :s'), {"s": sid}
        )
        assert n.scalar() == 10  # AC-40: 순위 근거 = 상위 10건


def test_reason_grounding_and_llm_failure_continue(api, db, monkeypatch):
    """B2 · R5-6 · R8 · AC-06 · AC-31(API): 근거 없는 '고른 이유'는 템플릿 + '원문으로 대체됨',
    메모 해석 provider 오류는 진행을 막지 않고, 같은 세션 3회 연속이면 숙련자 인계 권함."""
    from c1a15.api import sessions as svc
    from c1a15.llm.base import AdapterError, Draft

    reset_ops(db)
    tech, s = start(api, db)
    assert s["preview"]["reason_fallback"] is False and s["preview"]["reason_label"] is None  # fixture 문장은 통과

    class Broken:
        mode = "broken"

        def write(self, facts):
            return Draft("4.8절 기준 77% 확률입니다")  # 근거 없는 절 · 수치

        def memo_signal(self, *args):
            raise AdapterError("provider down")

    monkeypatch.setattr(svc, "adapter_for_mode", lambda: Broken())
    from c1a15.api import screen as scr

    monkeypatch.setattr(scr, "adapter_for_mode", lambda: Broken())  # 같은 provider: 지시문 · AI 해석도 실패(P-19)
    sid = s["session_id"]
    p = tech.get(f"/api/tech/sessions/{sid}").json()["preview"]
    assert p["reason_fallback"] and p["reason_label"] == "원문으로 대체됨" and "4.8" not in p["reason"]
    # 실패 수(P-19부터 지시문 포함): 사전 확인 지시문 1 → 첫 점검 지시문 2 → 메모 해석 3
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{sid}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
        assert not s["recommend_handover"]  # 아직 3회 연속이 아님
    assert s["instruction"]["fallback"]  # 실패한 지시문 자리 = 절 원문 + '원문으로 대체됨'(AC-31-C1)
    body = {
        "seq": s["last_seq"] + 1,
        "type": "result",
        "section": s["step"]["section"],
        "value": "확인 불가",
        "memo": "메모",
    }
    r = tech.post(f"/api/tech/sessions/{sid}/events", json=body)
    assert r.status_code == 200  # provider 오류에도 진단 계속(AC-31-C1)
    s = r.json()
    assert s["recommend_handover"] and "instruction" in s  # 3회 연속 실패(AC-31-C2), 다음 지시는 그대로


def test_event_required_fields_are_422(api, db):
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    for body in ({"type": "confirm_answer"}, {"type": "out_of_scope_choice"}, {"type": "result", "value": "정상"}):
        assert tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, **body}).status_code == 422


def test_p18_first_call_preview_values(api, db, monkeypatch):
    """P-18 · AC-02(API): 첫 판정이 호출이면 진단 미리보기가 이름 · 소요 분 · 근거 검사한 이유를 낸다(절차 없음)."""
    import json as _json

    from c1a15.api import sessions as svc
    from c1a15.llm.base import Draft

    reset_ops(db)
    with db.begin() as c:  # 등급 경로(실제 검색 결과: 1순위 CL-1 냉각 필터 막힘 83%): 4.3 · 4.5 · 4.6 = C
        c.execute(text("update ops.grade_table set grade = 'C' where section_id in ('4.3', '4.5', '4.6')"))
    tech, s = start(api, db)
    p = s["preview"]
    assert (
        p["kind"] == "call" and p["section"] == "4.3" and p["grade"] == "C" and p["call_reason"] == "top_cause_cd_only"
    )
    assert p["title"] and p["minutes"] and "text" not in p and "instruction" not in s
    assert p["reason"].startswith("1순위 ") and "4.3절, C등급" in p["reason"] and p["reason_fallback"] is False
    sid = s["session_id"]
    flat = [
        {"record_id": f"H-{9000 + i:04d}", "cause": c_, "score": 0.9}
        for i, c_ in enumerate(["CL-1"] * 3 + ["CL-2"] * 3 + ["CL-3"] * 2 + ["CL-4"] * 2)
    ]
    with db.begin() as c:  # 1순위가 50% 미만 + A · B 없음 → no_ab_left
        c.execute(
            text('update ops.diag_sessions set "similar" = cast(:s as jsonb) where session_id = :i'),
            {"s": _json.dumps(flat), "i": sid},
        )
    p = tech.get(f"/api/tech/sessions/{sid}").json()["preview"]
    assert p["kind"] == "call" and p["call_reason"] == "no_ab_left" and p["grade"] == "C"
    assert "혼자 할 수 있는 점검(A · B)이 없습니다" in p["reason"] and "text" not in p

    class Ungrounded:
        mode = "x"

        def write(self, facts):
            return Draft("4.8절 기준 77% 확률")

    monkeypatch.setattr(svc, "adapter_for_mode", lambda: Ungrounded())
    p = tech.get(f"/api/tech/sessions/{sid}").json()["preview"]
    assert p["reason_fallback"] and p["reason_label"] == "원문으로 대체됨" and "4.8" not in p["reason"]
    with db.connect() as c:
        kinds = [r[0] for r in c.execute(text("select type from ops.session_events where session_id = :s"), {"s": sid})]
    assert "preview_shown" in kinds  # 처음 보인 미리보기 문장의 대체 여부를 세션 실패 수에


def test_p18_input_validation_and_replay(api, db):
    """S02-G6-3: 빈 결과 · 필수 필드 없는 체크 · 서버 전용 필드는 422, 일부 필드만 다시 보낸 재전송은 409."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{sid}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    sec, nxt = s["step"]["section"], s["last_seq"] + 1
    url = f"/api/tech/sessions/{sid}/events"
    assert tech.post(url, json={"seq": nxt, "type": "result", "section": sec}).status_code == 422
    assert tech.post(url, json={"seq": nxt, "type": "b_checklist_done", "section": sec}).status_code == 422
    assert tech.post(url, json={"seq": nxt, "type": "lock", "llm_fallback": True}).status_code == 422
    body = {"seq": nxt, "type": "result", "section": sec, "value": "확인 불가", "memo": "덮개가 안 열림"}
    assert tech.post(url, json=body).status_code == 200
    assert tech.post(url, json=body).status_code == 200  # 같은 내용 재전송 → 기록 1건
    partial = {k: v for k, v in body.items() if k != "memo"}
    assert tech.post(url, json=partial).status_code == 409  # 일부 필드만 → 같은 업무 key 아님


def test_p19_screen_values_from_api(api, db, monkeypatch):
    """P-19 · AC-01 · AC-13 · AC-37 · AC-38 · AC-40(API): 화면 값은 서버가 낸다(앱은 받은 값만)."""
    import json as _json

    from c1a15.api import screen
    from c1a15.llm.base import Draft

    reset_ops(db)
    rules_json = _json.loads((SERVER.parent / "data/alarm_rules.json").read_text())
    tech, s = start(api, db)
    sid = s["session_id"]
    a = s["alarm"]  # T03 알람 블록
    assert (a["equipment_type"], a["group_name"], a["system"]) == ("회전 가공 설비", "온도 차 저하", "냉각 계통")
    assert a["boundary_text"] == "하위 경계 8.2 K" and a["trend_title"] == "온도 차 추이 · 최근 12회 측정"
    rows = a["sensors"]
    assert [r["key"] for r in rows] == [
        "temp_diff_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
        "air_temperature_k",
    ]
    assert rows[0]["range_text"] == "8.2 K 이상" and rows[0]["status"] == "경계 아래"
    for r in rows[1:]:  # 평소 범위 = data/alarm_rules.json normal_ranges(결정 014 ①)
        nr = rules_json["normal_ranges"][r["key"]]
        assert r["range_text"] == f"{screen.fmt(r['key'], nr['low'])} ~ {screen.fmt(r['key'], nr['high'])}"
    assert a["out_of_range_count"] == 1 and a["similar_count"] == 10
    assert not a["ai_reading"]["fallback"] and "냉각 계통" in a["ai_reading"]["text"]
    # T04: 원인 이름(A-04) · 기록 카드 3장 = 이력 기록 값
    assert set(s["probabilities"]) <= set(s["cause_names"]) and s["cause_names"]["CL-1"] == "냉각 필터 막힘"
    with db.connect() as c:
        sim = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
        recs = {
            r: rec
            for r, rec in c.execute(
                text("select record_id, record from knowledge.history_records where record_id = any(:ids)"),
                {"ids": [x["record_id"] for x in sim[:3]]},
            )
        }
    cards = s["similar_cards"]
    assert [x["record_id"] for x in cards] == [x["record_id"] for x in sim[:3] if x["score"] >= 0.5]
    for card, sm in zip(cards, sim[:3], strict=True):
        rec = recs[card["record_id"]]
        assert card["equipment_id"] == rec["equipment_id"] and card["downtime_min"] == rec["downtime_min"]
        assert card["month"] == int(rec["occurred_at"][5:7]) and card["similarity_pct"] == round(sm["score"] * 100)
        if card["cause_code"]:  # 같은 원인 코드면 순위 이름 = 카드 이름(PBC4-A9 실행 해석 ②)
            assert card["outcome_label"] == s["cause_names"][card["cause_code"]] == rec["outcome"]["cause_name"]
    # T05 잠금 전: 1.2절 절차만(R1), 다른 절 원문 409
    li = s["lock_instruction"]
    assert li["section"] == "1.2" and len(li["steps"]) == 4 and "instruction" not in s
    assert tech.get(f"/api/tech/sessions/{sid}/manual/4.3").status_code == 409
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    assert "lock_instruction" not in s and s["instruction"]["section"] == "4.2"
    assert len(s["instruction"]["steps"]) == 2 and s["instruction"]["check_no"] == 0
    assert (s["instruction"]["grade"], s["instruction"]["minutes"]) == ("A", 3)  # 사전 확인 4.2도 등급 · 분
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    ).json()
    ins = s["instruction"]  # T06: 점검 n / 최대 m · 근거(결정 014 ② · ③)
    assert ins["section"] == "4.3" and (ins["grade"], ins["minutes"]) == ("A", 3) and len(ins["steps"]) == 4
    assert ins["criterion"].startswith("기름때") and (ins["check_no"], ins["check_max"]) == (
        1,
        4,
    )  # D-637 F3: C 4.4도 셈
    want = [x["record_id"] for x in sorted((x for x in sim if x["cause"] == "CL-1"), key=lambda x: -x["score"])][:2]
    assert ins["evidence_record_ids"] == want and ins["unable"] == []
    # T02 · T12: 진행 n / m · 교대(결정 014 ④) · 증상 이름
    home = tech.get("/api/tech/home").json()
    row = next(x for x in home["alarms"] if x["alarm_id"] == s["alarm_id"])
    assert row["progress"] == {"check_no": 1, "check_max": 4} and row["session_id"] == sid
    assert row["group_name"] == "온도 차 저하" and home["recent_reports"] == []
    assert home["shift"]["in_progress"] == 1 and (home["shift"]["done"], home["shift"]["calls"]) == (0, 0)
    item = next(i for i in tech.get("/api/tech/notifications").json()["items"] if i["kind"] == "in_progress")
    assert item["progress"] == {"check_no": 1, "check_max": 4} and item["group_name"] == "온도 차 저하"
    # 확인 불가 → 다음 지시에 그 점검 이름 · 횟수
    body = {"seq": s["last_seq"] + 1, "type": "result", "section": "4.3", "value": "확인 불가"}
    s = tech.post(f"/api/tech/sessions/{sid}/events", json=body).json()
    assert s["instruction"]["unable"] == [{"section": "4.3", "title": "흡기 필터 육안 점검", "count": 1}]
    assert s["instruction"]["check_no"] == 2
    # T13: 승인 · 출처
    rec = tech.get(f"/api/records/{cards[0]['record_id']}").json()
    assert rec["approval"]["status"] == "승인" and rec["approval"]["by"] and rec["source"] == "generated"
    assert rec["equipment_type"] == "회전 가공 설비"

    class Ungrounded:
        mode = "x"

        def write(self, facts):
            return Draft("4.8절 기준 77% 확률")

    monkeypatch.setattr(screen, "adapter_for_mode", lambda: Ungrounded())
    ai = tech.get(f"/api/tech/sessions/{sid}").json()["alarm"]["ai_reading"]
    assert ai["fallback"] and ai["label"] == "원문으로 대체됨" and "4.8" not in ai["text"]


def test_p19_pw_alarm_sensor_table(api, db):
    """PBC4-A8 실행 해석 ①(API): PW 알람 센서 표 = 알람 센서 + 4개(온도 차 빠짐)."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01", PW_SENSOR).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    a = s["alarm"]
    assert a["symptom_group"] == "PW" and a["system"] == "구동·전원 계통"
    assert [r["name"] for r in a["sensors"]] == ["기계 출력", "회전 속도", "회전력", "공구 마모", "공기 온도"]
    assert a["sensors"][0]["range_text"] == "8,403 W 이하" and a["boundary_text"] == "상위 경계 8,403 W"


def test_p19_instruction_guide_grounding_and_repeat(api, db, monkeypatch):
    """P-19 · AC-31 · R8(API): 지시문은 열 때 한 번 쓰고 근거 검사, 실패면 원문 블록 + '원문으로 대체됨'."""
    from c1a15.api import screen
    from c1a15.llm.base import Draft

    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"}).json()
    s = tech.post(
        f"/api/tech/sessions/{sid}/events",
        json={"seq": s["last_seq"] + 1, "type": "precheck_done", "values": "하우징 52 °C"},
    ).json()
    ins = s["instruction"]  # fixture 지시문 = 절 원문 절차(근거 검사 통과)
    assert ins["fallback"] is False and ins["original"] is None and len(ins["steps"]) == 4
    calls = []

    class Ungrounded:
        mode = "x"

        def write(self, facts):
            calls.append(facts.kind)
            return Draft("4.8절 기준으로 77분 동안 점검한다.")

    monkeypatch.setattr(screen, "adapter_for_mode", lambda: Ungrounded())
    body = {"seq": s["last_seq"] + 1, "type": "result", "section": ins["section"], "value": "확인 불가"}
    s = tech.post(f"/api/tech/sessions/{sid}/events", json=body).json()  # 다른 점검이 열리며 지시문 생성 실패
    ins = s["instruction"]
    assert ins["fallback"] and ins["fallback_label"] == "원문으로 대체됨" and ins["steps"] == []
    assert ins["original"].startswith(ins["section"]) and "4.8" not in ins["original"] and "**" not in ins["original"]
    assert "판정 기준" in ins["original"] and ins["criterion"]
    n = calls.count("instruction")
    assert n == 2  # 1회 다시 작성 → 또 실패 → 원문 대체(R8)
    again = tech.get(f"/api/tech/sessions/{sid}").json()
    assert calls.count("instruction") == n and again["instruction"] == ins  # 주기 조회는 다시 쓰지 않는다
    q = "select payload->>'llm_fallback' from ops.session_events where session_id = :s and type = 'instruction_shown'"
    with db.connect() as c:
        flags = [r[0] for r in c.execute(text(q + " order by seq"), {"s": sid})]
    assert flags == ["false", "false", "true"]  # 사전 확인 4.2 · 첫 점검 · 실패한 다음 점검(AC-31-C2 실패 수에 들어감)


def test_p19_low_evidence_cards_and_common_checklist(api, db):
    """P-19 · AC-29(API): 근거 적음이면 기록 카드 3건 미만(score ≥ 0.5만), B 점검 체크리스트가 공통이면 표시."""
    import json as _json

    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    with db.begin() as c:
        sim = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
        low = [dict(x, score=0.9 if i < 2 else 0.3) for i, x in enumerate(sim)]
        c.execute(
            text('update ops.diag_sessions set "similar" = cast(:v as jsonb) where session_id = :s'),
            {"v": _json.dumps(low), "s": sid},
        )
        c.execute(text("update ops.grade_table set grade = 'B' where section_id = '4.3'"))
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["low_evidence"] and [x["record_id"] for x in s["similar_cards"]] == [x["record_id"] for x in low[:2]]
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{sid}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    ins = s["instruction"]
    assert s["step"]["kind"] == "instruct_after_checklist" and ins["section"] == "4.3"
    assert ins["checklist_common"] is True and ins["checklist"] and not ins["checklist_done"]


def test_ac05_confirm_question_no_keeps_rank_yes_applies(api, db):
    """AC-05(L2): 메모가 버튼과 반대 뜻이면 확인 질문.

    '아니요' → 순위 · 단계 그대로 · 답 기록 저장, '네' 뒤에만 해석대로 반영.
    """
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            f"/api/tech/sessions/{sid}/events",
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    sec, before = s["instruction"]["section"], s["probabilities"]
    url = f"/api/tech/sessions/{sid}/events"

    def ask():  # '정상' 버튼 + 이상 뜻 메모(R7 (a)) → 확인 질문, 순위는 아직 그대로
        nonlocal s
        body = {
            "seq": s["last_seq"] + 1,
            "type": "result",
            "section": sec,
            "value": "정상",
            "memo": "기름때가 표면 절반 넘게 덮음",
        }
        s = tech.post(url, json=body).json()
        assert s["step"]["kind"] == "confirm_question" and s["step"]["interpretation"] == "이상"
        assert s["probabilities"] == before and "instruction" not in s

    ask()
    s = tech.post(url, json={"seq": s["last_seq"] + 1, "type": "confirm_answer", "yes": False}).json()
    assert s["probabilities"] == before and s["step"]["kind"] in {"instruct", "instruct_after_checklist"}  # AC-05-C1
    assert s["instruction"]["section"] == sec  # 반영 없이 같은 점검을 다시 입력
    with db.connect() as c:
        stored = c.execute(
            text("select payload from ops.session_events where session_id = :s and type = 'confirm_answer'"), {"s": sid}
        ).all()
    assert [r[0] for r in stored] == [{"yes": False}]  # 답 기록 저장
    ask()
    s = tech.post(url, json={"seq": s["last_seq"] + 1, "type": "confirm_answer", "yes": True}).json()
    # AC-05-C2: '네' 뒤에만 해석('이상')대로 — 4.3은 확정형 점검이라 연결 원인 CL-1 확정 → 조치(또는 호출) 단계
    assert s["step"]["kind"] in {"action", "action_after_checklist", "call"} and s["step"]["cause"] == "CL-1"
    with db.connect() as c:
        n = c.execute(
            text("select count(*) from ops.session_events where session_id = :s and type = 'confirm_answer'"),
            {"s": sid},
        ).scalar()
    assert n == 2


def test_ac05_yes_on_screen_check_changes_ranking(api, db):
    """AC-05-C2(L2, 선별형): '네, 맞습니다' 뒤에만 순위가 해석대로 바뀐다 — 선별형 점검 '이상' = 그 원인만 남음."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01", PW_SENSOR).json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    url = f"/api/tech/sessions/{s['session_id']}/events"
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = tech.post(
            url,
            json={
                "seq": s["last_seq"] + 1,
                "type": etype,
                **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}),
            },
        ).json()
    for _ in range(
        6
    ):  # 선별형 3.5(인버터 경고 이력, PW-2)가 지시될 때까지: 3.3 정상(PW-1 제외), 3.4 확인 불가 2회(그 점검만 제외)
        sec = s["instruction"]["section"]
        if sec == "3.5":
            break
        value = "정상" if sec == "3.3" else "확인 불가"
        s = tech.post(url, json={"seq": s["last_seq"] + 1, "type": "result", "section": sec, "value": value}).json()
    assert s["instruction"]["section"] == "3.5", (s["step"], s["probabilities"])
    before = s["probabilities"]
    assert len(before) >= 2  # 남은 후보가 둘 이상이어야 '그 원인만'이 순위 변화로 보인다
    body = {
        "seq": s["last_seq"] + 1,
        "type": "result",
        "section": "3.5",
        "value": "정상",
        "memo": "경고 이력에 손상 기록",
    }
    s = tech.post(url, json=body).json()
    assert s["step"]["kind"] == "confirm_question" and s["probabilities"] == before
    s = tech.post(url, json={"seq": s["last_seq"] + 1, "type": "confirm_answer", "yes": True}).json()
    from c1a15.api import deps

    cause = deps.manual().check("3.5").cause
    assert s["probabilities"] != before and s["probabilities"] == {cause: 100}  # 그 원인만(R4 선별형 '이상')


# ---------------------------------------------------------------- S-03(P-09): 조치 · 재가동 · 호출 · 보고서


def ev(tech, s, **body):
    return tech.post(f"/api/tech/sessions/{s['session_id']}/events", json={"seq": s["last_seq"] + 1, **body})


def ok(tech, s, **body):
    r = ev(tech, s, **body)
    assert r.status_code == 200, (body, r.status_code, r.text[:300])
    return r.json()


def to_instruct(tech, s):
    for etype in ("alarm_ack", "lock", "precheck_done"):
        s = ok(tech, s, type=etype, **({"values": "하우징 52 °C"} if etype == "precheck_done" else {}))
    return s


def run_until(tech, s, kinds, choose):
    """지시가 이어지는 동안 choose(section)의 결과를 보내다 step.kind가 kinds에 들면 멈춘다."""
    for _ in range(12):
        if s["step"]["kind"] in kinds:
            return s
        sec = s["instruction"]["section"]
        s = ok(tech, s, type="result", section=sec, value=choose(sec))
    raise AssertionError(s["step"])


def sim_answer(db, sid, answer, section, by="lead.park", note=None):
    """파트장 답(S-04 P-11이 쓰는 기록)을 시험 DB에 넣는다 — PBC-4 v10 실행 해석 ③."""
    from datetime import UTC, datetime

    from c1a15.api import sessions as svc

    with db.begin() as c:
        call_id = c.execute(
            text("select call_id from ops.calls where session_id = :s order by call_id desc limit 1"), {"s": sid}
        ).scalar()
        at = datetime.now(UTC).isoformat()
        payload = {"answer": answer, "section": section, "by": by, "at": at, "note": note, "call_id": call_id}
        c.execute(
            text("update ops.calls set answer = cast(:a as jsonb), answered_at = now() where call_id = :c"),
            {"a": __import__("json").dumps(payload), "c": call_id},
        )
        evs = svc.events_of(c, sid)
        svc._store(c, sid, evs[-1]["seq"] + 1, "call_answer", payload, by)
        svc._open_step(c, sid, svc._row(c, sid), svc.events_of(c, sid), by)


@pytest.fixture
def night(monkeypatch):
    """교대 수(결정 014 ④: 야간 22:00 ~ 06:00)를 시험 시각과 무관하게 보려고 '지금이 야간 교대 안'으로 둔다."""
    from datetime import datetime, timedelta

    from c1a15.api import screen

    now = datetime.now(screen.KST)
    monkeypatch.setattr(screen, "shift_window", lambda at=None: (now - timedelta(hours=1), now + timedelta(hours=7)))


def test_s03_action_restart_report_confirm(api, db, night):
    """AC-32-C1 · AC-21-C1 · C2 · AC-07 · AC-33 · R10 · 결정 015(L2): A 조치 → 재가동 → 재가동 후 값 → 보고서 → 확정."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    assert s["instruction"]["section"] == "4.3"
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때가 필터 표면 절반 넘게 덮음")
    a = s["action"]
    assert s["step"]["kind"] == "action" and a["section"] == "4.3.2" and a["grade"] == "A" and a["done_at"] is None
    assert a["cause_name"] == "냉각 필터 막힘" and a["title"] == "흡기 필터 교체"
    from c1a15.api import deps  # D-530 답장 001-zzy: 조치 절차 = 매뉴얼 4.3.2 번호 줄 그대로
    from c1a15.api import screen as scr

    assert a["steps"] == scr.procedure_steps(deps.manual(), "4.3.2") and len(a["steps"]) == 3
    assert ev(tech, s, type="restart", checked=[]).status_code == 409  # 조치 전 재가동 없음
    s = ok(tech, s, type="action_done", section="4.3.2")  # AC-32-C1: A 조치는 바로 · 완료 시각
    assert s["action"]["done_at"] and s["step"]["kind"] == "restart_check" and s["step"]["section"] == "6.1"
    rs = s["restart"]
    assert rs["grade"] == "B" and len(rs["checklist"]) == 3 and "value" not in rs
    r = ev(tech, s, type="restart", checked=rs["checklist"][:2])
    assert r.status_code == 409 and r.json()["detail"] == "checklist_incomplete"  # AC-21-C1
    s = ok(tech, s, type="restart", checked=rs["checklist"])
    v = s["restart"]["value"]  # AC-21-C2: 재가동 뒤 현재 값과 경계(R11: 경계 + 여유 10%)
    assert s["step"]["kind"] == "restart_value" and v["value_text"] == "9.0 K"
    assert v["rule_text"] == "경계 8.2 K 이상이면 경계 이내"
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "report" and s["step"]["outcome"] == "confirmed"
    rep = s["report"]
    f = {x["key"]: x for x in rep["fields"]}
    assert [x["label"] for x in rep["fields"]] == [
        "설비 · 알람",
        "센서 근거",
        "점검 경로",
        "확정 원인",
        "근거 사례",
        "조치",
        "정지 시간",
        "미확인 · 요청",
    ]
    assert [x["source"] for x in rep["fields"]] == ["자동", "센서", "입력", "입력", "기록", "매뉴얼", "자동", "입력"]
    assert f["equipment_alarm"]["text"].startswith("M-01 · 온도 차 저하 · ")
    assert f["sensor_evidence"]["text"].startswith("온도 차 7.9 K (경계 8.2 K 아래) · ") and f["sensor_evidence"][
        "text"
    ].endswith("→ 냉각 계통 쪽")
    assert f["check_path"]["text"].startswith(
        "잠금 확인(1.2) → 고온부 확인과 온도 기록(4.2) → 흡기 필터 육안 점검(4.3, 이상: 기름때가"
    )
    assert f["cause"]["text"] == "냉각 필터 막힘"
    with db.connect() as c:
        sim = c.execute(text('select "similar" from ops.diag_sessions where session_id = :s'), {"s": sid}).scalar()
    assert f["evidence_records"]["text"] == " · ".join(x["record_id"] for x in sim[:3])
    assert f["action"]["text"] == "흡기 필터 교체(4.3.2, A) · 재가동(6.1, B, 확인 완료) 후 온도 차 9.0 K 경계 이내"
    assert f["downtime"]["text"].endswith("분")
    assert "냉각 팬 분해 점검(4.4, C등급) · 주간 확인 요청" in f["unresolved"]["text"]  # 결정 015 ④(CL-2가 후보로 남음)
    assert all(x["ok"] for x in rep["checks"]) and rep["can_confirm"] and rep["status"] == "draft"
    assert [p["status"] for p in s["path"]] == ["ok", "ok", "bad", "ok", "ok", "ok"]
    # AC-07-C1: 근거 없는 수치로 고치면 확정 전 확인이 실패하고 확정이 거부된다
    bad = f["action"]["text"] + " · 교체 후 온도 차 9.4 K로 회복"
    s = ok(tech, s, type="report_edit", field="action", text=bad)
    rep = s["report"]
    assert (
        not {x["key"]: x for x in rep["fields"]}["action"]["ok"] and rep["checks"][0]["label"] == "근거 없는 문장 1건"
    )
    assert rep["messages"][0].startswith('"교체 후 온도 차 9.4 K로 회복"의 수치 9.4') and not rep["can_confirm"]
    r = ev(tech, s, type="report_confirm")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "precheck_failed"
    s = ok(tech, s, type="report_edit", field="action", text=f["action"]["text"])
    s = ok(tech, s, type="report_confirm")  # AC-07-C2: 모두 통과 → 승인 대기
    assert (
        s["report"]["status"] == "awaiting_approval" and s["report"]["confirmed_at"] and s["step"]["kind"] == "closed"
    )
    r = ev(tech, s, type="report_edit", field="cause", text="다른 원인")
    assert r.status_code == 409 and r.json()["detail"] == "session_closed"  # AC-33-C1
    again = tech.get(f"/api/tech/sessions/{sid}").json()
    assert again["report"]["status"] == "awaiting_approval" and again["report"]["fields"] == s["report"]["fields"]
    home = tech.get("/api/tech/home").json()  # PBC-4 v8 실행 해석 ③: 홈 값이 0이 아님
    assert home["shift"]["done"] == 1 and home["recent_reports"][0]["cause"] == "냉각 필터 막힘"
    assert home["recent_reports"][0]["status"] == "awaiting_approval"
    row = next(x for x in home["alarms"] if x["alarm_id"] == s["alarm_id"])
    assert row["status"] == "done" and row["report_status"] == "awaiting_approval"
    with db.connect() as c:
        st = c.execute(text("select status from ops.diag_sessions where session_id = :s"), {"s": sid}).scalar()
        acts = [x for (x,) in c.execute(text("select action from ops.audit_log order by audit_id"))]
        rejected = c.execute(text("select detail from ops.audit_log where action = 'reject'")).all()
    assert st == "reported" and {"action_done", "restart", "restart_result", "report_edit", "report_confirm"} <= set(
        acts
    )
    assert any("precheck_failed" in str(r_[0]) for r_ in rejected)  # S02-G6-1: 거부 감사가 rollback 뒤에도 남음


def test_s03_b_action_checklist(api, db):
    """AC-32-C2 · AC-43-C4(L2): B 조치는 시작 전 체크리스트(절에 없으면 공통 목록) 뒤에만 진행."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'B' where section_id = '4.3.2'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때 많음")
    assert s["step"]["kind"] == "action_after_checklist" and s["action"]["grade"] == "B"
    r = ev(tech, s, type="action_done", section="4.3.2")
    assert r.status_code == 409 and r.json()["detail"] == "b_checklist_required"
    with db.connect() as c:
        common = [t for (t,) in c.execute(text("select text from ops.common_precheck_items order by seq"))]
    assert s["action"]["checklist"] == common  # 4.3.2에는 목록이 없어 공통 시작 전 확인
    s = ok(tech, s, type="b_checklist_done", section="4.3.2", checked=common)
    s = ok(tech, s, type="action_done", section="4.3.2")
    assert s["step"]["kind"] == "restart_check"


def test_s03_restart_outside_goes_to_handover(api, db):
    """AC-21-C3(L2): 재가동 뒤 '여전히 밖' → 숙련자 인계 → 보고서 확정 원인 '원인 미상'."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="outside")
    assert s["step"]["kind"] == "handover" and s["step"]["reason"] == "restart_outside"
    assert s["call"]["kind"] == "handover" and s["call"]["head"] == "재가동 뒤에도 값이 경계 밖"
    s = ok(tech, s, type="call_send")
    assert s["step"]["kind"] == "handover" and s["step"]["call_status"] == "sent" and "report" not in s  # 결정 018 ④
    assert ev(tech, s, type="call_send").status_code == 409  # 같은 인계를 두 번 보내지 않는다
    sim_answer(db, s["session_id"], "assign", None, note="주간 조 배정")
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert s["step"]["kind"] == "report" and f["cause"] == "원인 미상"  # AC-21-C3
    assert "숙련자 인계 답 · 주간 숙련자 배정 · 주간 조 배정" in f["unresolved"]  # AC-23-C4(답 · 처리 메모)
    with db.connect() as c:
        kinds = [
            k for (k,) in c.execute(text("select kind from ops.calls where session_id = :s"), {"s": s["session_id"]})
        ]
    assert kinds == ["handover"]


def test_s03_monitor_close(api, db):
    """AC-22-C1(L2): 후보가 모두 빠지고 재가동 확인이 경계 이내 → 6.2 감시 마감 → 확정 원인 '이상 없음 · 감시'."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"restart_check"}, lambda sec: "정상")
    assert s["step"]["outcome"] == "monitor" and "action" not in s
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    assert s["step"]["kind"] == "monitor_close" and s["monitor"]["section"] == "6.2" and not s["monitor"]["closed"]
    s = ok(tech, s, type="monitor_close")
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert s["step"]["kind"] == "report" and f["cause"] == "이상 없음 · 감시"
    assert f["action"].startswith("조치 없음 · 재가동(6.1, B, 확인 완료) 후 온도 차") and f["action"].endswith(
        "감시 대상 등록(6.2)"
    )
    assert f["check_path"].endswith("이상 없음·감시 마감(6.2)") and f["unresolved"] == "없음"


def test_s03_call_wait_send_and_answers(api, db, night):
    """AC-30 · 1.4 · AC-37(P-09 L2) · AC-23(부분, 답은 시험 DB): 호출 요약 → 대기 → 보내기 → 주간 처리 답 → 보고서."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    cl = s["call"]
    assert cl["status"] == "none" and cl["kind"] == "approval" and cl["grade"] in {"C", "D"}
    assert [d["value"] for d in cl["checks_done"]] and cl["candidates"] and "instruction" not in s
    assert f"확인에 {cl['title']}({cl['section']}절, {cl['grade']}등급) 필요" in cl["summary"]["text"]
    s = ok(tech, s, type="call_wait")
    with db.connect() as c:
        n = c.execute(text("select count(*) from ops.calls where session_id = :s"), {"s": sid}).scalar()
    assert n == 0 and s["call"]["status"] == "waiting" and s["step"]["call_status"] == "waiting"  # AC-30-C1 · C2
    edited = cl["summary"]["text"] + " 정비자 메모: 팬 소음 있음."
    s = ok(tech, s, type="call_send", summary=edited)
    with db.connect() as c:
        rows = c.execute(text("select kind, summary from ops.calls where session_id = :s"), {"s": sid}).all()
    assert rows == [("approval", edited)] and s["call"]["status"] == "sent" and s["call"]["summary"]["text"] == edited
    assert ev(tech, s, type="call_send").status_code == 409  # 같은 호출을 두 번 보내지 않는다
    assert tech.get("/api/tech/home").json()["shift"]["calls"] == 1
    sim_answer(db, sid, "daytime", cl["section"], note="주간 정비 배정")
    items = tech.get("/api/tech/notifications").json()["items"]
    ans = next(i for i in items if i["kind"] == "answered_call")  # AC-37: 파트장 답이 온 호출 항목
    assert ans["session_id"] == sid and ans["answer"] == "daytime" and ans["call_section"] == cl["section"]
    assert ans["answered_by"] == "lead.park" and ans["answered_by_name"] and ans["answered_at"]  # 답한 사람 · 시각
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["step"]["kind"] == "report" and s["step"]["outcome"] == "daytime"
    assert s["call"]["status"] == "answered" and s["call"]["answer"]["answer"] == "daytime"
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert "주간 처리 요청" in f["unresolved"] and f["cause"] == "원인 미상"


def test_s03_approved_c_opens_and_d_never(api, db):
    """AC-08(부분) · S02-G6-4(L2): '승인 후 진행'이면 그 C 점검이 지시로 열리고, D등급은 승인 답에도 열리지 않는다."""
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    sec = s["call"]["section"]
    s = ok(tech, s, type="call_send")
    sim_answer(db, s["session_id"], "approved", sec)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["step"]["kind"] == "instruct" and s["instruction"]["section"] == sec  # 승인된 그 C 점검이 바로 열림
    assert s["call"]["answer"]["by"] == "lead.park" and s["call"]["answer"]["at"]  # AC-08-C2: 승인자 · 시각
    assert s["call"]["status"] == "answered" and s["call"]["grade"] == "C" and s["instruction"]["grade"] == "C"
    reset_ops(db)
    with db.begin() as c:
        c.execute(text(f"update ops.grade_table set grade = 'D' where section_id = '{sec}'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda x: "정상")
    assert s["call"]["section"] == sec and s["call"]["grade"] == "D"
    s = ok(tech, s, type="call_send")
    sim_answer(
        db, s["session_id"], "approved", sec
    )  # D에 '승인' 답이 와도(파트장 앱은 선택지를 주지 않는다 — AC-23-C3)
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    assert s["step"]["kind"] != "instruct" and "instruction" not in s
    r = ev(tech, s, type="result", section=sec, value="정상")
    assert r.status_code == 409  # D 점검 결과는 받지 않는다


def test_s03_first_call_summary(api, db):
    """P-09 verifier: 첫 판정 호출 세션 — 지금까지 점검 '점검 전' · 남은 후보 = 진단 때 순위 · 필요한 작업."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'C' where section_id in ('4.3', '4.5', '4.6')"))
    tech, s = start(api, db)
    diag = s["probabilities"]
    s = to_instruct(tech, s)
    cl = s["call"]
    assert s["step"]["kind"] == "call" and cl["checks_done"] == [] and "점검 전" in cl["summary"]["text"]
    assert {x["code"]: x["pct"] for x in cl["candidates"]} == diag
    assert f"확인에 {cl['title']}({cl['section']}절, C등급) 필요" in cl["summary"]["text"]


def test_s03_unable_twice_handover_and_out_of_scope(api, db):
    """AC-19-C3 · AC-20(L2): 확인 불가 반복 · 대안 없음 → 숙련자 인계 요약, 맞지 않는 소견 → 인계 선택 → 인계 요약."""
    reset_ops(db)
    with db.begin() as c:
        c.execute(text("update ops.grade_table set grade = 'A' where section_id = '4.4'"))
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"handover"}, lambda sec: "확인 불가" if sec in {"4.3", "4.4"} else "정상")
    assert s["step"]["reason"] == "no_alternative" and "확인 불가 2회, 남은 대안 없음" in s["call"]["head"]
    assert "할 수 있는 A · B 점검 없음" in s["call"]["summary"]["text"]
    done = {d["section"]: d["value"] for d in s["call"]["checks_done"]}  # 점검마다 한 줄(T11 / unable_repeat)
    assert done["4.3"] == "확인 불가 2회" and len(done) == len(s["call"]["checks_done"])
    assert "확인 불가 2회" in s["call"]["summary"]["text"]
    assert any(p["detail"] == "확인 불가 2회 · 제외" for p in s["path"])
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="정상", memo="바닥에서 타는 냄새")
    assert s["step"]["kind"] == "out_of_scope"
    before = s["probabilities"]
    s = ok(tech, s, type="out_of_scope_choice", choice="handover")
    assert s["step"]["kind"] == "handover" and s["call"]["head"] == "후보 원인과 맞지 않는 소견"
    assert s["probabilities"] == before and "맞지 않음" in s["call"]["summary"]["text"]


def test_s03_corrected_path_view(api, db):
    """T08 / corrected_path: 다시 판정한 정정(S-05 P-14 정정 API) → 뒤 기록 무효 · 정정 표시 · 조치."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="정상")
    nxt = s["instruction"]["section"]
    s = ok(tech, s, type="result", section=nxt, value="정상")
    target = next(r["seq"] for r in s["results"] if r["section"] == "4.3")
    r = tech.post(
        f"/api/tech/sessions/{sid}/corrections", json={"target_seq": target, "value": "이상", "reason": "뒷면"}
    )
    assert r.status_code == 200, r.text
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["step"]["kind"] == "action" and s["action"]["section"] == "4.3.2"
    c = s["correction"]
    assert (c["section"], c["before"], c["after"]) == ("4.3", "정상", "이상") and c["at"]
    p = {x["title"]: x for x in s["path"]}
    assert p["흡기 필터 육안 점검"]["status"] == "bad" and "정정 1회(" in p["흡기 필터 육안 점검"]["detail"]
    assert "정상 → 이상)" in p["흡기 필터 육안 점검"]["detail"]
    assert any(x["status"] == "void" for x in s["path"])  # 뒤 기록은 지우지 않고 무효
    assert s["path"][-1]["status"] == "now"


def test_s03_llm_repeat_handover(api, db, monkeypatch):
    """AC-31-C2 연결(사용자 결정 016 ④): 문장 생성 3회 연속 실패 → '숙련자 인계' → 인계 요약 → 보고서 '원인 미상'."""
    import runpy

    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    r = ev(tech, s, type="handover_request")
    assert r.status_code == 409 and r.json()["detail"] == "no_handover_recommendation"  # 권하지 않을 때는 거부
    monkeypatch.setattr("sys.argv", ["sim_llm_failure", str(sid), "all"])
    runpy.run_path(str(SERVER.parent / "e2e/sim_llm_failure.py"))
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["recommend_handover"]
    s = ok(tech, s, type="handover_request")
    assert s["step"]["kind"] == "handover" and s["step"]["reason"] == "llm_failure"
    assert s["call"]["head"] == "안내 문장 생성이 반복해서 실패함" and "3회 연속 실패" in s["call"]["summary"]["text"]
    s = ok(tech, s, type="call_send")
    assert s["step"]["kind"] == "handover" and s["step"]["call_status"] == "sent"  # 결정 018 ④: 답 대기
    sim_answer(db, sid, "direct", None, note="파트장이 원문으로 확인")
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert {x["key"]: x["text"] for x in s["report"]["fields"]}["cause"] == "원인 미상"


def test_s03_rejected_report_resume_and_reconfirm(api, db, night, monkeypatch):
    """FLOW-06 · AC-11 L2(P-09 부분, 반려 기록은 시험 DB — 사용자 결정 G6-S03): 반려 → 최근 보고서 반려 · 사유 ·
    종 '반려된 보고서' → 세션이 다시 열려 보고서 항목 수정 → 다시 확정 → 승인 대기."""
    import runpy

    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    s = ok(tech, s, type="result", section="4.3", value="이상", memo="기름때")
    s = ok(tech, s, type="action_done", section="4.3.2")
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    first = {x["key"]: x["text"] for x in s["report"]["fields"]}
    monkeypatch.setattr("sys.argv", ["sim_report_reject", str(sid), "필터 표면 상태를 메모로 다시 적어 주세요"])
    runpy.run_path(str(SERVER.parent / "e2e/sim_report_reject.py"))
    home = tech.get("/api/tech/home").json()
    rr = home["recent_reports"][0]  # AC-11-C1: 최근 보고서에 반려 · 사유
    assert rr["status"] == "rejected" and rr["reject_reason"] == "필터 표면 상태를 메모로 다시 적어 주세요"
    assert rr["rejected_by_name"] and rr["rejected_at"] and home["shift"]["done"] == 0
    items = tech.get("/api/tech/notifications").json()["items"]
    rej = next(i for i in items if i["kind"] == "rejected_report")  # AC-37: 반려된 보고서 항목
    assert rej["session_id"] == sid and rej["report_cause"] == "냉각 필터 막힘" and rej["reject_reason"]
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["status"] == "in_progress" and s["step"]["kind"] == "report"
    rep = s["report"]
    assert rep["status"] == "draft" and rep["rejection"]["reason"].startswith("필터 표면") and rep["can_confirm"]
    assert {x["key"]: x["text"] for x in rep["fields"]} == first  # 확정했던 문장에서 다시 고친다
    path = first["check_path"].replace("이상: 기름때", "이상: 기름때가 필터 표면 절반 넘게 덮음")
    s = ok(tech, s, type="report_edit", field="check_path", text=path)
    assert {x["key"]: x for x in s["report"]["fields"]}["check_path"]["ok"]
    bad = ok(tech, s, type="report_edit", field="action", text=first["action"] + " · 교체 후 9.7 K")
    assert not bad["report"]["can_confirm"]  # 반려 뒤 수정도 근거 검사(R8)
    s = ok(tech, bad, type="report_edit", field="action", text=first["action"])
    s = ok(tech, s, type="report_confirm")  # AC-11-C2: 다시 확정 → 승인 대기
    assert s["report"]["status"] == "awaiting_approval" and s["step"]["kind"] == "closed"
    with db.connect() as c:
        row = c.execute(
            text("select status, fields, rejection from ops.reports where session_id = :s"), {"s": sid}
        ).one()
        n = c.execute(text("select count(*) from ops.reports where session_id = :s"), {"s": sid}).scalar()
        st = c.execute(text("select status from ops.diag_sessions where session_id = :s"), {"s": sid}).scalar()
    assert row.status == "awaiting_approval" and n == 1 and st == "reported" and row.rejection["reason"]
    assert {f["key"]: f["text"] for f in row.fields}["check_path"] == path
    assert ev(tech, s, type="report_edit", field="cause", text="x").status_code == 409  # 다시 닫힘
    home = tech.get("/api/tech/home").json()
    assert home["recent_reports"][0]["status"] == "awaiting_approval" and home["shift"]["done"] == 1


def test_s03_business_rejections_are_audited(api, db):
    """G6-S03 비차단 의견: 업무 규칙 거부(확정 뒤 입력 · B 체크리스트 · 잠금 전 · 확인할 것 없음)도 감사 행이 남는다."""
    reset_ops(db)
    tech, s = start(api, db)
    assert ev(tech, s, type="precheck_done", values="하우징 52 °C").status_code == 409  # not_locked
    s = to_instruct(tech, s)
    assert ev(tech, s, type="confirm_answer", yes=True).status_code == 409  # nothing_pending
    assert ev(tech, s, type="b_checklist_done", section="4.3", checked=[]).status_code == 409
    with db.connect() as c:
        reasons = [r for (r,) in c.execute(text("select detail->>'reason' from ops.audit_log where action = 'reject'"))]
    assert {"not_locked", "nothing_pending", "no_b_checklist_step"} <= set(reasons)


def test_s03_direct_answer_goes_to_report(api, db):
    """AC-23-C2(L2, 답은 시험 DB) · 결정 018 ③: 직접 확인 + 처리 메모 → 보고서, 처리 결과는 미확인 · 요청에."""
    reset_ops(db)
    tech, s = start(api, db)
    sid = s["session_id"]
    s = to_instruct(tech, s)
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    sec = s["call"]["section"]
    s = ok(tech, s, type="call_send")
    sim_answer(db, sid, "direct", sec, note="팬 베어링 소음 확인, 주간 교체 예정")
    s = tech.get(f"/api/tech/sessions/{sid}").json()
    assert s["step"]["kind"] == "report" and s["step"]["outcome"] == "direct" and s["call"]["status"] == "answered"
    assert s["call"]["answer"]["answer"] == "direct" and s["call"]["answer"]["by_name"]
    f = {x["key"]: x["text"] for x in s["report"]["fields"]}
    assert "파트장 직접 확인 · 팬 베어링 소음 확인, 주간 교체 예정" in f["unresolved"] and f["cause"] == "원인 미상"
    assert "instruction" not in s and ev(tech, s, type="result", section=sec, value="정상").status_code == 409
