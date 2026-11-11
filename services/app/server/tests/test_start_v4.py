# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-15(시험 DB): 세션 시작 상위 10 = Pack 리트리버(약속 v4) — start 본문에 similar 0 · 출력 그대로 저장.

VC-53(start 길 · 저장 · 뒤 judge 본문 · 없는 기록 · 결과 0건) · VC-54(가짜 = 승인 40행: 시연 10 + 카드 val 30, 첫 판정 = 승인 행).
"""

import pytest
from sqlalchemy import text

import fake_deploy
from c1a15.api import deps
from c1a15.engine import session
from test_api import TECH_PW, TEST_LINEAGE, api, db, login  # noqa: F401
from test_deploy_path import count, deploy, reset_ops  # noqa: F401

pytestmark = pytest.mark.db


def start_case(api, db, deploy, row):
    """승인 행의 입력(센서 5칸)으로 알람을 등록하고 세션을 연다 → (세션 응답, start 본문, 저장 행)."""
    from fastapi.testclient import TestClient

    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    reg = TestClient(api).post(
        "/api/internal/alarms",
        json={
            "equipment_id": "M-01",
            "sensor_snapshot": row["sensor5"],
            "data_lineage": TEST_LINEAGE,
            "eval_mode": True,
        },
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert reg.status_code == 200, reg.text
    s = tech.post(f"/api/tech/alarms/{reg.json()['alarm_id']}/session")
    body = [p["body"] for p in deploy.posts() if p["body"]["kind"] == "start"][-1]
    with db.connect() as c:
        stored = c.execute(
            text('select "similar", retrieval from ops.diag_sessions where alarm_id = :a'),
            {"a": reg.json()["alarm_id"]},
        ).one_or_none()
    return tech, s, body, stored


@pytest.mark.parametrize("case", [r["case"] for r in fake_deploy.start_v4_rows()])
def test_vc54_start_stores_approved_row(api, db, deploy, case):
    """VC-54 · PR-26 ②: 승인 40행마다 — 본문 similar 0 · 저장 similar · retrieval = 그 행 · 첫 판정(앱 engine) = 승인 행 decision."""
    reset_ops(db)
    row = next(r for r in fake_deploy.start_v4_rows() if r["case"] == case)
    tech, s, body, stored = start_case(api, db, deploy, row)
    assert s.status_code == 200
    assert "similar" not in body and body["query"]["sensor_snapshot"] == row["sensor5"]
    before = len(fake_deploy.STAND_INS)
    assert fake_deploy.start_v4_row(body).get("stand_in") is None  # 제 행(대역 아님)
    assert len(fake_deploy.STAND_INS) == before
    assert stored.similar == row["similar"] and stored.retrieval == row["retrieval"]
    with db.connect() as c:
        first = c.execute(
            text(
                "select payload from ops.session_events where session_id = :s and type = 'step_opened' order by seq limit 1"
            ),
            {"s": s.json()["session_id"]},
        ).scalar()
    # A16-N4: 가짜 서버의 첫 판정은 앱 engine 계산 — 승인 행 decision과 다르면 L1(c1a15-pack)
    assert first["decision"] == row["decision"], (case, first["decision"], row["decision"])


def test_vc53_judge_uses_stored_similar_and_one_start(api, db, deploy):
    """VC-53 · PR-23: 세션당 start 1(잠금 · 결과 · 메모 · 정정 · 다시 읽기 뒤에도) · 뒤 judge 본문 similar = 저장 값.

    ES-16 B-1: 지운 test_vc12_one_search_per_session(결과 · 정정 뒤 상위 10 계산 1회)의 몫을 start Run 수로 옮김.
    """
    reset_ops(db)
    row = fake_deploy.start_v4_rows()[0]
    tech, s, body, stored = start_case(api, db, deploy, row)
    sj = s.json()
    sid = sj["session_id"]
    url = f"/api/tech/sessions/{sid}/events"
    for ev in ({"type": "alarm_ack"}, {"type": "lock"}, {"type": "precheck_done", "values": "하우징 52 °C"}):
        r = tech.post(url, json={"seq": sj["last_seq"] + 1, **ev})
        assert r.status_code == 200, r.text
        sj = r.json()
    sec = sj["step"]["section"]
    r = tech.post(
        url, json={"seq": sj["last_seq"] + 1, "type": "result", "section": sec, "value": "정상", "memo": "깨끗함"}
    )
    assert r.status_code == 200, r.text
    target = next(x["seq"] for x in r.json()["results"] if x["section"] == sec)
    before = len([p for p in deploy.posts() if p["body"]["kind"] == "judge"])
    r = tech.post(
        f"/api/tech/sessions/{sid}/corrections",
        json={"target_seq": target, "value": "이상", "reason": "뒷면", "confirmed": True},
    )
    assert r.status_code == 200, r.text
    assert tech.get(f"/api/tech/sessions/{sid}").status_code == 200
    judges = [p["body"] for p in deploy.posts() if p["body"]["kind"] == "judge"]
    assert len(judges) > before >= 3 and all(j["similar"] == stored.similar for j in judges)  # 정정 = 다시 판정 Run
    assert len([p for p in deploy.posts() if p["body"]["kind"] == "start"]) == 1


def test_vc53_unknown_record_stops_session(api, db, deploy):
    """VC-53 · PR-24: Pack이 앱 이력에 없는 기록을 고르면 세션을 만들지 않음(카드를 빼고 보이지 않음) · 감사."""
    reset_ops(db)
    row = fake_deploy.start_v4_rows()[0]
    bad = [{**row["similar"][0], "record_id": "H-9999"}] + row["similar"][1:]
    deploy.default = lambda b: (
        {"output": {**fake_deploy.engine_answer(b)["output"], "similar": bad}}
        if b["kind"] == "start"
        else fake_deploy.engine_answer(b)
    )
    tech, s, body, stored = start_case(api, db, deploy, row)
    assert s.status_code == 503 and s.json() == {"detail": "pack_unavailable", "code": "unknown_record"}
    assert stored is None
    with db.connect() as c:
        d = c.execute(
            text("select detail from ops.audit_log where action = 'pack_call_failed' order by audit_id desc limit 1")
        ).scalar_one()
    assert d["code"] == "unknown_record" and d["missing"] == ["H-9999"] and d["kind"] == "start"
    assert count(db, "select count(*) from ops.alarms where status = 'new'") == 1  # 알람 그대로(요청 되돌림)


def test_vc53_empty_similar_opens_session(api, db, deploy):
    """VC-53 · 약속 v4 4절: 결과 0건(similar [] · retrieval 셋 null)도 정상 — 세션이 열리고 첫 판정은 근거 0."""
    reset_ops(db)
    row = fake_deploy.start_v4_rows()[0]
    empty = {"source": "packrt", "vector_db_version": None, "query_from": None, "fallback_used": None}

    def answer(b):
        if b["kind"] != "start":
            return fake_deploy.engine_answer(b)
        k = deps.manual()
        st = session.fold(k, b["group"], [], b["events"])
        return {
            "output": {"decision": session.decide(st, k, b["grades"], b["minutes"]), "similar": [], "retrieval": empty}
        }

    deploy.default = answer
    tech, s, body, stored = start_case(api, db, deploy, row)
    assert s.status_code == 200 and stored.similar == [] and stored.retrieval == empty
    with db.connect() as c:
        first = c.execute(
            text(
                "select payload from ops.session_events where session_id = :s and type = 'step_opened' order by seq limit 1"
            ),
            {"s": s.json()["session_id"]},
        ).scalar()
    assert first["decision"]["kind"] == "lock" and set(first["decision"]["evidence"].values()) == {0}
