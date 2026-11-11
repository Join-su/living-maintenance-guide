# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-07(시험 DB): 판정 길 deploy — start · judge Run 본문 · 저장 · 실패 되돌림 · 같은 seq 다시 · 세션 시작 실패.

VC-35(본문 칸 · session_id S-A<alarm_id> · 되돌림 뒤 같은 기본 키 · 알람 하나 = 세션 하나) · VC-36(이어) · PR-10(start Run 세션당 1회).
가짜 배포 서버가 받은 본문 · 돌려준 응답은 증거 파일로 남겨 c1a15-pack .venv로 정본 v2 schema 검사(VC-35 도구).
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from fake_deploy import FakeDeploy, engine_answer, start_v4_row, start_v4_rows
from test_api import TECH_PW, api, db, login, ok, register  # noqa: F401
from test_api import reset_ops as _reset_ops

pytestmark = pytest.mark.db
BANNED = {
    "user_id",
    "actor",
    "by",
    "display_name",
    "memo",
    "notes",
    "corrections",
    "corrected_from",
    "before",
    "after",
    "_at",
    "reason_text",
}
EVIDENCE = os.environ.get("C1A15_DEPLOY_EVIDENCE")  # 있으면 받은 본문 · 응답을 여기에 jsonl로


@pytest.fixture
def deploy(api, monkeypatch):
    from c1a15.deploy import runs

    with FakeDeploy() as fake:
        fake.default = engine_answer
        monkeypatch.setenv("C1A15_JUDGE", "deploy")
        monkeypatch.setenv("C1A15_DEPLOY_URL", fake.url)
        runs.settings.cache_clear()
        runs.client.cache_clear()
        yield fake
        if EVIDENCE:
            with Path(EVIDENCE).open("a", encoding="utf-8") as f:
                for r in fake.posts():
                    f.write(json.dumps({"request": r["body"]}, ensure_ascii=False) + "\n")
                for rec in fake.keys.values():
                    if rec["status"] in (200, 201):
                        f.write(json.dumps({"response": rec["payload"]["result"]["output"]}, ensure_ascii=False) + "\n")
    runs.settings.cache_clear()
    runs.client.cache_clear()


def reset_ops(db):
    _reset_ops(db)
    with db.begin() as c:
        c.execute(text("delete from ops.alarm_candidates"))


def banned_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BANNED:
                yield f"{path}.{k}"
            yield from banned_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from banned_keys(v, f"{path}[{i}]")


def count(db, sql, **kw):
    with db.connect() as c:
        return c.execute(text(sql), kw).scalar_one()


def test_start_and_judge_runs_bodies_and_storage(api, db, deploy):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    sid = s["session_id"]
    start_post = next(p for p in deploy.posts() if p["body"]["kind"] == "start")
    start, start_run = start_post["body"], deploy.keys[start_post["headers"]["Idempotency-Key"]]["run_id"]
    alarm_event = count(db, "select event_id from ops.alarms where alarm_id = :a", a=alarm_id)
    assert alarm_event.startswith("C1A-E")  # 알람도 deploy 길(alarm Run)이 만든 사건 id
    assert start["kind"] == "start" and start["session_id"] == f"S-A{alarm_id}" and start["event_id"] == alarm_event
    assert set(start["query"]["sensor_snapshot"]) == {
        "air_temperature_k",
        "process_temperature_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
    }
    assert start["events"] == [] and "similar" not in start  # 약속 v4: 상위 10은 Pack 리트리버(EV-001 CH-29)
    with db.connect() as c:
        row = c.execute(
            text(
                'select "similar", retrieval, start_run_id, deployment_id from ops.diag_sessions where session_id = :s'
            ),
            {"s": sid},
        ).one()
        first = c.execute(
            text("select payload from ops.session_events where session_id = :s and type = 'step_opened' order by seq"),
            {"s": sid},
        ).scalar()
    want = start_v4_row(start)  # 가짜 배포 서버가 답한 행(승인 자료 · 이 알람은 대역)
    assert want.get("stand_in") is True and want["case"] == next(
        r["case"]
        for r in start_v4_rows()
        if (r["group"], r["direction"]) == (start["group"], start["query"]["direction"])
    )  # 대역 = 같은 group · direction의 첫 행(CH-35)
    assert row.similar == want["similar"] and row.start_run_id == start_run and row.deployment_id == "c1a15@v1-local"
    assert row.retrieval == want["retrieval"] and "app_release_id" not in row.retrieval  # retrieval 넷 그대로(v4)
    assert (
        first["via"] == "deploy"
        and first["run_id"] == start_run
        and first["decision"]["kind"] == s["step"]["kind"] == "lock"
    )
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    judge = deploy.posts()[-1]["body"]
    assert (
        judge["kind"] == "judge" and judge["session_id"] == f"S-A{alarm_id}" and judge["similar"] == row.similar
    )  # 저장 값 그대로
    assert [e["type"] for e in judge["events"]] == ["lock"] and "event_id" not in judge and "query" not in judge
    assert [b for p in deploy.posts() for b in banned_keys(p["body"])] == []
    with db.connect() as c:
        vias = [
            p["via"]
            for (p,) in c.execute(
                text("select payload from ops.session_events where session_id = :s and type = 'step_opened'"),
                {"s": sid},
            )
        ]
    assert (
        set(vias) == {"deploy"}
        and count(db, "select count(*) from ops.audit_log where action = 'pack_call_failed'") == 0
    )
    starts = [p for p in deploy.posts() if p["body"]["kind"] == "start"]
    tech.get(f"/api/tech/sessions/{sid}")
    assert tech.post(f"/api/tech/alarms/{alarm_id}/session").json()["session_id"] == sid  # 알람 하나 = 세션 하나(A9-N7)
    assert (
        len(starts) == len([p for p in deploy.posts() if p["body"]["kind"] == "start"]) == 1
    )  # PR-10: start Run 세션당 1회


def test_judge_failure_rolls_back_and_same_seq_resend_uses_attempt_key(api, db, deploy):
    """VC-36(이어): 저장된 retryable 실패 2번 → 503 pack_unavailable · 업무 기록 0 · 감사 2줄. 같은 seq 다시 → -r2 → 통과."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    s = tech.post(f"/api/tech/alarms/{alarm_id}/session").json()
    sid = s["session_id"]
    s = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "alarm_ack"}).json()
    before = count(db, "select count(*) from ops.session_events where session_id = :s", s=sid)
    deploy.plan = [{"status": 503, "code": "trace_unavailable", "retryable": True}] * 2
    r = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"})
    assert r.status_code == 503 and r.json() == {"detail": "pack_unavailable", "code": "trace_unavailable"}
    assert count(db, "select count(*) from ops.session_events where session_id = :s", s=sid) == before  # 되돌림
    assert count(db, "select count(*) from ops.audit_log where action = 'pack_call_failed'") == 2
    keys = [p["headers"]["Idempotency-Key"] for p in deploy.posts() if p["body"]["kind"] == "judge"]
    r = tech.post(f"/api/tech/sessions/{sid}/events", json={"seq": s["last_seq"] + 1, "type": "lock"})  # 같은 seq 다시
    assert r.status_code == 200 and r.json()["locked"]
    again = [p["headers"]["Idempotency-Key"] for p in deploy.posts() if p["body"]["kind"] == "judge"]
    assert again == keys + [keys[0] + "-r2"] and keys[1] == keys[0] + "-r1"  # 되돌린 뒤에도 같은 session_id · 기본 키


def test_start_failure_makes_no_session_and_alarm_stays_new(api, db, deploy):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    deploy.plan = [{"status": 500, "code": "deployment_not_registered"}]
    r = tech.post(f"/api/tech/alarms/{alarm_id}/session")
    assert r.status_code == 503 and r.json()["code"] == "deployment_not_registered"
    assert count(db, "select count(*) from ops.diag_sessions where alarm_id = :a", a=alarm_id) == 0
    assert count(db, "select status from ops.alarms where alarm_id = :a", a=alarm_id) == "new"
    assert count(db, "select count(*) from ops.audit_log where action = 'session_start'") == 0
    assert count(db, "select count(*) from ops.audit_log where action = 'pack_call_failed'") == 1
    deploy.plan = [{"output": None}]  # 다음 실행은 engine 계산(default) — plan에서 빼고 다시
    deploy.plan.clear()
    r = tech.post(f"/api/tech/alarms/{alarm_id}/session")
    # 저장된 retryable 아닌 실패(deployment_not_registered)는 같은 키 replay — 운영 쪽 일(등록)이 풀려야 함
    start_posts = [p for p in deploy.posts() if p["body"]["kind"] == "start"]
    assert (
        r.status_code == 503 and len({p["headers"]["Idempotency-Key"] for p in start_posts}) == 1
    )  # replay · 새 실행 0
    starts = {p["body"]["session_id"] for p in start_posts}
    assert starts == {f"S-A{alarm_id}"}  # 세션 번호가 없어도 같은 session_id(되돌림 뒤에도)


# ---------------- ES-08 알람 길(alarm Run)


def scenarios():
    return json.loads((Path(__file__).resolve().parents[2] / "data/demo/scenarios.json").read_text(encoding="utf-8"))[
        "scenarios"
    ]


def register_sc(api, sc, **extra):
    from fastapi.testclient import TestClient

    body = {k: sc[k] for k in ("scenario_id", "equipment_id", "sensor_snapshot", "trend")} | extra
    return TestClient(api).post("/api/internal/alarms", json=body, headers={"X-Internal-Token": "test-internal-token"})


def alarm_rows(db):
    with db.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "select scenario_id, symptom_group, alarm, sensor_snapshot, event_id, run_id, deployment_id, ml from ops.alarms order by alarm_id"
                )
            )
        ]


def test_vc37_demo_alarms_follow_app_boundary_rule_and_alarm_body(api, db, deploy):
    """VC-37: 시연 10행 — deploy 길 알람 · 증상군 · 경계 표시 · 센서 7칸 = 앱 경계 규칙 값(ES-16: inproc 대조 상대 대신 규칙 직접)."""
    from c1a15.api import deps
    from c1a15.deploy import runs
    from c1a15.engine import alarm_rules

    reset_ops(db)
    for sc in scenarios():
        assert register_sc(api, sc).status_code == 200
    via_deploy = alarm_rows(db)
    bodies = [p["body"] for p in deploy.posts() if p["body"]["kind"] == "alarm"]
    for sc, r in zip(scenarios(), via_deploy, strict=True):
        hit = alarm_rules.detect(sc["sensor_snapshot"], deps.alarm_table())
        assert (r["scenario_id"], r["symptom_group"], r["alarm"]) == (sc["scenario_id"], hit["group"], hit["alarm"])
        assert r["sensor_snapshot"] == sc["sensor_snapshot"]
    assert all(
        r["event_id"].startswith("C1A-E") and r["run_id"] and r["deployment_id"] == "c1a15@v1-local" for r in via_deploy
    )
    assert all(r["ml"] is None for r in via_deploy)  # 경계 길 = ml 칸 SQL NULL(review R-1)
    assert count(db, "select count(*) from ops.alarms where ml is not null") == 0
    sc0, b0 = scenarios()[0], bodies[0]
    card = b0["event_card"]
    assert set(card["sensor_snapshot"]) == {
        "air_temperature_k",
        "process_temperature_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
    }
    assert card["data_lineage"] == {"source_release": runs.source_release(), "row_id": int(sc0["udi"]), "split": "demo"}
    assert (
        card["product_type"] == sc0["product_type"]
        and card["source_mode"] == "sensor_boundary"
        and "settings" not in b0
    )
    assert [b for p in deploy.posts() for b in banned_keys(p["body"])] == []


def test_alarm_missing_lineage_and_product_type_are_422(api, db, deploy, monkeypatch):
    from fastapi.testclient import TestClient

    from c1a15.deploy import runs

    reset_ops(db)
    sc = scenarios()[0]
    c = TestClient(api)
    h = {"X-Internal-Token": "test-internal-token"}
    r = c.post(
        "/api/internal/alarms",
        json={"equipment_id": sc["equipment_id"], "sensor_snapshot": sc["sensor_snapshot"]},
        headers=h,
    )
    assert r.status_code == 422 and r.json()["detail"] == "missing_lineage" and deploy.posts() == []
    monkeypatch.setenv("C1A15_ALARM", "ml")
    runs.settings.cache_clear()
    lineage = {"source_release": runs.source_release(), "row_id": int(sc["udi"]), "split": "demo"}
    r = c.post(
        "/api/internal/alarms",
        json={"equipment_id": sc["equipment_id"], "sensor_snapshot": sc["sensor_snapshot"], "data_lineage": lineage},
        headers=h,
    )
    assert r.status_code == 422 and r.json()["detail"] == "missing_product_type" and deploy.posts() == []
    assert alarm_rows(db) == []


def test_alarm_other_failure_is_503_and_no_alarm(api, db, deploy):
    reset_ops(db)
    deploy.plan = [{"status": 503, "code": "trace_unavailable", "retryable": True}] * 2
    r = register_sc(api, scenarios()[0])
    assert r.status_code == 503 and r.json()["detail"] == "pack_unavailable"
    assert alarm_rows(db) == [] and count(db, "select count(*) from ops.alarm_candidates") == 0
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm'") == 0


def test_alarm_boundary_path_no_alarm_is_422(api, db, deploy):
    reset_ops(db)
    sc = scenarios()[0]
    normal = {
        **sc["sensor_snapshot"],
        "air_temperature_k": 300.0,
        "process_temperature_k": 310.0,
        "rotational_speed_rpm": 1500.0,
        "torque_nm": 40.0,
        "tool_wear_min": 10,
    }
    r = register_sc(api, {**sc, "sensor_snapshot": normal})
    assert r.status_code == 422 and r.json()["detail"] == "no_boundary_crossed" and alarm_rows(db) == []


def pack_candidate_plan(reason, shape="v6"):
    """Pack이 알람 없음 + 사유를 돌려주는 꼴 = Pack alarm_route 원본(alarms/ml.py route)이 실제로 내는 값 — 예측 실패는 Run 실패(D-275).

    shape v5 = 지금 떠 있는 Pack(약속 v5 · 옛 route 사본)의 `candidate` 칸 — D-530 덧 3: 앱은 두 꼴 다 읽는다."""
    from c1a15.alarms import ml as alarm_route

    if reason == "model_failure":
        return [
            {"status": 500, "code": "run_failed", "where": "run.predict_failure", "node_code": "feature_not_number"}
        ]
    first = "RNF" if reason == "rnf" else "HDF"  # 1위 RNF → rnf · 1위 확률 < 문턱 → low_confidence
    pred = {"model_release": "mr-x", "probabilities": {"NONE": 0.3, first: 0.45, "PWF": 0.25}}
    routed = alarm_route.route(pred, None, 0.5)
    assert routed["no_alarm_reason"] == reason  # 원본이 정말 그 사유의 알람 없음을 냄
    if shape == "v5":  # 약속 v5 꼴(옛 route): 사유가 candidate 칸 안에
        legacy = {
            "reason": reason,
            "risk": routed["ml"]["risk"],
            "top_k": routed["ml"]["top_k"],
            "boundary_crossed": False,
            "model_release": "mr-x",
        }
        routed = {**{k: v for k, v in routed.items() if k != "no_alarm_reason"}, "candidate": legacy}
    out = {"event_id": "x", "alarm_block": None, "sensor_derived": {"power_w": 1, "temp_diff_k": 1.0}, **routed}
    return [{"output": {"alarm": out}}]


@pytest.mark.parametrize(
    ("reason", "shape"),
    [("low_confidence", "v6"), ("rnf", "v6"), ("model_failure", "v6"), ("low_confidence", "v5"), ("rnf", "v5")],
)
def test_d529_pack_candidate_is_no_alarm_and_one_audit(api, db, deploy, reason, shape):
    """D-529 · D-530 덧 3: Pack 알람 없음 + 사유(확신 낮음 · RNF · 예측 실패)는 알람 없음(200) + 감사 alarm_rejected 한 줄.

    새 꼴(no_alarm_reason) · 옛 꼴(candidate.reason) 둘 다. 후보 표(alarm_candidates)에는 새 행 0 · 응답에 candidate_id 칸 없음."""
    reset_ops(db)
    cands = count(db, "select count(*) from ops.alarm_candidates")
    deploy.plan = pack_candidate_plan(reason, shape)
    r = register_sc(api, scenarios()[0])
    assert r.status_code == 200
    body = r.json()
    assert body["alarm_id"] is None and body["reason"] == reason and "candidate_id" not in body
    assert set(body) == {"alarm_id", "reason", "ml"}
    assert alarm_rows(db) == []
    assert count(db, "select count(*) from ops.alarm_candidates") == cands
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_candidate'") == 0
    with db.connect() as c:
        rows = c.execute(
            text("select actor, target, detail, equipment_id, at from ops.audit_log where action = 'alarm_rejected'")
        ).all()
    assert len(rows) == 1
    a = rows[0]
    assert a.actor == "system" and a.equipment_id == scenarios()[0]["equipment_id"] and a.at is not None
    assert a.detail["reason"] == reason and a.target == f"event:{a.detail['event_id']}"
    assert a.detail["event_id"].startswith("C1A-E") and a.detail["run_id"] == "run_0001"


def test_start_event_id_falls_back_to_c1a_a_for_alarm_registered_on_default_path(api, db, deploy):
    """R6-2: 예전 기본 길로 등록돼 event_id가 없는 알람이면 start.event_id = C1A-A<alarm_id>.

    운영 DB에 3단계 전 알람이 남을 수 있어 대체 규칙을 지킴(ES-16 A16-N5) — 그런 알람 행을 DB에서 직접 만듦.
    """
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    with db.begin() as c:
        c.execute(
            text("update ops.alarms set event_id = null, run_id = null, deployment_id = null where alarm_id = :a"),
            {"a": alarm_id},
        )
    assert tech.post(f"/api/tech/alarms/{alarm_id}/session").status_code == 200
    start = next(p["body"] for p in deploy.posts() if p["body"]["kind"] == "start")
    assert start["event_id"] == f"C1A-A{alarm_id}"


ML_OUT = {
    "model_release": "mr-c1a15-standin-v1",
    "risk": 0.71,
    "alarm": True,
    "confidence": 0.58,
    "threshold": 0.7,
    "confirmed": False,
    "top_k": [{"type": "HDF", "probability": 0.58, "group_suggestion": "CL"}],
}


def ml_alarm_output(card_id, group_source, block):
    return {
        "output": {
            "alarm": {
                "event_id": card_id,
                "alarm": True,
                "group": "CL",
                "group_source": group_source,
                "alarm_block": block,
                "sensor_derived": {"power_w": 6299, "temp_diff_k": 7.8},
                "ml": ML_OUT,
                "candidate": None,
            }
        }
    }


def test_ml_path_body_and_ml_stored(api, db, deploy, monkeypatch):
    """review R-3: C1A15_ALARM=ml — 본문 ml_predicted · settings 문턱 = 앱 설정 한 곳 · product_type 각본, 알람 행 ml = 받은 값."""
    from c1a15.deploy import runs

    reset_ops(db)
    monkeypatch.setenv("C1A15_ALARM", "ml")
    monkeypatch.setenv("C1A15_ML_ALARM_THRESHOLD", "0.7")
    runs.settings.cache_clear()
    sc = scenarios()[0]
    block = {
        "sensor": "온도 차",
        "value": 7.8,
        "unit": "K",
        "boundary": 8.2,
        "direction": "경계 아래",
        "trend_title": "온도 차 추이 · 최근 12회 측정",
    }
    deploy.plan = [ml_alarm_output("x", "sensor_boundary", block)]
    r = register_sc(api, sc)
    assert r.status_code == 200 and r.json()["alarm_id"]
    body = next(p["body"] for p in deploy.posts() if p["body"]["kind"] == "alarm")
    assert body["event_card"]["source_mode"] == "ml_predicted" and body["settings"] == {"ml_alarm_threshold": 0.7}
    assert body["event_card"]["product_type"] == sc["product_type"]
    row = alarm_rows(db)[0]
    assert row["ml"] == ML_OUT and row["alarm"] == block and row["symptom_group"] == "CL" and row["run_id"]


def test_ml_type_alarm_without_boundary_block_is_registered(api, db, deploy, monkeypatch):
    """D-546 U5(review R-2 · CH-25를 바꿈): ML 유형으로 정한 알람(alarm_block null)도 등록 — 알람 꼴은 앱이 그 증상군 센서로 채움(origin ml_type)."""
    from c1a15.deploy import runs

    reset_ops(db)
    monkeypatch.setenv("C1A15_ALARM", "ml")
    runs.settings.cache_clear()
    deploy.plan = [ml_alarm_output("x", "ml_type", None)]
    r = register_sc(api, scenarios()[0])
    assert (
        r.status_code == 200 and r.json()["alarm_id"] and [a["alarm"]["origin"] for a in alarm_rows(db)] == ["ml_type"]
    )
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") == 0
