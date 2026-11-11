# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-570(recent-condition.md · 줄인 꼴): 예외 기록 자동 정보 = 같은 설비 최근 N일 정비 이력 전부(이력 번호 · 시각 · 며칠 전 ·
한 조치 절 did · 한 점검 절 checked · 결과) · 시연 이력 줄 명령(넣기 · 보기 · 빼기 — 시연 줄만)."""

import json
import os
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from fake_deploy import pack_answer
from test_api import api, db, ok, reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import deploy  # noqa: F401
from test_exception_guide import _approved_exception, _bodies, _guide
from test_exception_record import NOT, TEXT, _split, _to_report

pytestmark = pytest.mark.db

FAR = datetime(2027, 6, 1, 10, 0, tzinfo=UTC)  # 이력 자료(2026년) 뒤 — 창 안엔 넣은 줄만


def _add(*args):
    from c1a15 import demo_history

    return demo_history.main(["add", *args])


def _drop(*records):
    from c1a15 import demo_history

    for r in records:
        demo_history.main(["remove", "--record", r])


def test_recent_window_equipment_order_and_fields(api, db, capsys):
    from c1a15.api import flow
    from c1a15.db import models as m

    at = FAR.isoformat()
    try:
        _add(
            "--record",
            "H-9001",
            "--equipment",
            "M-01",
            "--days-ago",
            "3",
            "--action",
            "4.3.2",
            "--check",
            "4.3",
            "--at",
            at,
        )
        _add("--record", "H-9002", "--equipment", "M-01", "--days-ago", "1", "--action", "4.5.2", "--at", at)
        _add("--record", "H-9003", "--equipment", "M-01", "--days-ago", "8", "--action", "4.3.2", "--at", at)  # 7일 밖
        _add(
            "--record", "H-9004", "--equipment", "M-02", "--days-ago", "2", "--action", "4.3.2", "--at", at
        )  # 다른 설비
        _add(
            "--record", "H-9005", "--equipment", "M-01", "--days-ago", "-1", "--action", "4.3.2", "--at", at
        )  # 알람 뒤
        line = json.loads(
            capsys.readouterr().out.strip().splitlines()[0]
        )  # add = 이력 원본 꼴 한 줄(배포단 적재기 입력)
        assert line["record_id"] == "H-9001" and line["demo"] == "D-570" and line["equipment_id"] == "M-01"
        assert [(s["kind"], s["section"]) for s in line["steps"]] == [("check", "4.3"), ("action", "4.3.2")]
        assert line["outcome"]["cause_name"]  # 그 조치 절 원인 이름(매뉴얼 표)
        alarm = m.Alarm(equipment_id="M-01", occurred_at=FAR, symptom_group="CL")
        with db.connect() as c:
            got = flow.recent_maintenance(c, alarm)
            assert [r["record_id"] for r in got] == ["H-9002", "H-9001"]  # 최근 것 먼저 · 7일 안 · 같은 설비 · 알람 앞
            h1 = got[1]
            assert (h1["days_before"], h1["did"], h1["checked"]) == (3, ["4.3.2"], ["4.3"])
            assert h1["occurred_at"] == (FAR - timedelta(days=3)).isoformat() and h1["outcome"]
            assert [r["record_id"] for r in flow.recent_maintenance(c, alarm, days=30)] == [
                "H-9002",
                "H-9001",
                "H-9003",
            ]
    finally:
        _drop("H-9001", "H-9002", "H-9003", "H-9004", "H-9005")
    with db.connect() as c:
        left = c.execute(text("select count(*) from knowledge.history_records where record_id like 'H-900%'")).scalar()
    assert left == 0


def test_add_line_carries_what_the_graph_loader_reads(api, db, capsys):
    """리허설 RPT-000501: 적재기(build_v3 graph_rows)는 이력 줄마다 approval.status · 센서 일곱을 읽음 — 빠지면 KeyError로 새 그래프 0.
    센서값은 같은 설비 실제 이력 값 그대로(지어내기 0) · 이력 없는 설비는 거절."""
    sensors = {
        "air_temperature_k",
        "process_temperature_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
        "temp_diff_k",
        "power_w",
    }
    try:
        _add("--record", "H-9031", "--equipment", "M-03", "--days-ago", "2", "--action", "4.3.2")
        line = json.loads(capsys.readouterr().out.strip().splitlines()[0])
        assert line["approval"] == {"status": "승인", "by": "시연", "at": line["closed_at"]}
        assert "." not in line["occurred_at"] and all(
            "." not in st["at"] for st in line["steps"]
        )  # 초까지(engine _secs · 이력 자료와 같음)
        assert sensors <= set(line["sensor_snapshot"])
        with db.connect() as c:
            real = c.execute(
                text(
                    "select sensor_snapshot from knowledge.history_records where equipment_id = 'M-03' "
                    "and source = 'generated' and record->>'demo' is null order by occurred_at desc limit 1"
                )
            ).scalar()
            kept = c.execute(
                text("select sensor_snapshot from knowledge.history_records where record_id = 'H-9031'")
            ).scalar()
        assert line["sensor_snapshot"] == real == kept
    finally:
        _drop("H-9031")
    with pytest.raises(ValueError):  # 값을 가져올 이력이 없는 설비
        _add("--record", "H-9032", "--equipment", "M-99", "--days-ago", "2", "--action", "4.3.2")


def test_demo_command_only_touches_demo_rows(api, db):
    from c1a15 import demo_history

    demo_history.main(["add"])  # 인자 없음 = 시연 기본 한 줄(M-01 · 3일 전 · 4.3.2 · H-0301)
    with db.connect() as c:
        row = c.execute(
            text("select equipment_id, record from knowledge.history_records where record_id = 'H-0301'")
        ).one()
    assert row.equipment_id == "M-01" and [s["section"] for s in row.record["steps"]] == ["4.3.2"]
    demo_history.main(["remove"])  # 짝 = 그 줄만 뺌
    with db.connect() as c:
        assert (
            c.execute(text("select count(*) from knowledge.history_records where record_id = 'H-0301'")).scalar() == 0
        )

    with db.connect() as c:
        real = c.execute(
            text(
                "select record_id from knowledge.history_records where source = 'generated' and record->>'demo' is null limit 1"
            )
        ).scalar()
    with pytest.raises(ValueError):  # 시연 줄 아닌 이력과 번호가 겹치면 거절
        _add("--record", real, "--equipment", "M-01", "--days-ago", "3", "--action", "4.3.2")
    demo_history.main(["remove", "--record", real])  # 시연 표시 없는 이력은 빼지 않음
    with db.connect() as c:
        assert (
            c.execute(text("select count(*) from knowledge.history_records where record_id = :r"), {"r": real}).scalar()
            == 1
        )
    with pytest.raises(ValueError):  # 매뉴얼에 없는 절
        _add("--record", "H-9010", "--equipment", "M-01", "--days-ago", "3", "--action", "9.9.9")


def test_exception_record_context_carries_recent_list(api, db, deploy):
    """예외 기록 확정 → 자동 정보 recent_maintenance에 3일 전 시연 줄(승인 기록 파일 칸 그대로)."""
    reset_ops(db)
    tech, s = _to_report(api, db)
    with db.connect() as c:
        at = c.execute(text("select occurred_at from ops.alarms where alarm_id = :a"), {"a": s["alarm_id"]}).scalar()
    try:
        _add(
            "--record", "H-9011", "--equipment", "M-01", "--days-ago", "3", "--action", "4.3.2", "--at", at.isoformat()
        )
        s = _split(tech, s)
        s = ok(tech, s, type="report_confirm", exception_record={"text": TEXT}, exception_review=NOT)
        ctx = s["report"]["exception_record"]["context"]
        demo = [r for r in ctx["recent_maintenance"] if r["record_id"] == "H-9011"]
        assert len(demo) == 1 and (demo[0]["days_before"], demo[0]["did"]) == (3, ["4.3.2"])
        assert all(0 <= r["days_before"] <= 7 for r in ctx["recent_maintenance"])  # 7일 창
        assert ctx["previous_maintenance"]  # 옛 칸 그대로(파트장 '직전 정비' 줄)
        if os.environ.get("RECENT_OUT"):  # D-503 2: 승인 기록 파일 context 실제 한 건(시험 DB 값)
            with open(os.environ["RECENT_OUT"], "w", encoding="utf-8") as fp:
                json.dump(ctx, fp, ensure_ascii=False, indent=1)
    finally:
        _drop("H-9011")


AT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$"
)  # Pack 약속 v10 덧 at 칸


def test_start_request_carries_recent_work_only_on_start(api, db, deploy):
    """Pack 5e76c66: start 요청 recent_work = 그 설비 30일 안 정비 [{record_id, at, sections}] · judge엔 없음 · 없으면 칸 0."""
    reset_ops(db)
    try:
        _add("--days-ago", "3", "--check", "4.3")  # 기본 = H-0301 · M-01 · 4.3.2
        _add("--record", "H-9021", "--days-ago", "20", "--action", "4.5.2")  # 7일 밖 · 30일 안
        _add("--record", "H-9022", "--days-ago", "31", "--action", "4.5.2")  # 30일 밖
        _add("--record", "H-9023", "--equipment", "M-02", "--days-ago", "2")  # 다른 설비
        tech, s = start(api, db, equipment="M-01")
        s = to_instruct(tech, s)
        body = _bodies(deploy, "start")[-1]
        if os.environ.get("RECENT_START_OUT"):  # D-503 2: start 요청 실제 한 건(Pack v10 덧 schema 대조용)
            with open(os.environ["RECENT_START_OUT"], "w", encoding="utf-8") as fp:
                json.dump(body, fp, ensure_ascii=False, indent=1)
        rows = {r["record_id"]: r for r in body["recent_work"]}
        assert body["equipment_id"] == "M-01" and AT.match(body["occurred_at"])
        assert rows["H-0301"]["sections"] == ["4.3.2", "4.3"] and "H-9021" in rows
        assert not {"H-9022", "H-9023"} & set(rows)
        assert all(
            AT.match(r["at"]) and set(r) == {"record_id", "at", "sections"} and r["sections"] for r in rows.values()
        )
        assert all("recent_work" not in b for b in _bodies(deploy, "judge"))  # 다른 종류는 Pack이 거절
    finally:
        _drop("H-0301", "H-9021", "H-9022", "H-9023")


def test_recent_work_shape_filters_and_caps():
    """Pack 칸 조건: 절 앱 번호만(EX-… · 빈 절 줄 뺌) · 시각 초까지 · 60줄 상한."""
    from c1a15.api import flow

    row = {
        "record_id": "H-1",
        "occurred_at": "2026-11-03T01:02:03.456789+00:00",
        "did": ["4.3.2", "EX-R-1"],
        "checked": ["4.3"],
    }
    empty = {"record_id": "H-2", "occurred_at": "2026-11-03T01:02:03+00:00", "did": [], "checked": []}
    assert flow.recent_work_of([row, empty]) == [
        {"record_id": "H-1", "at": "2026-11-03T01:02:03+00:00", "sections": ["4.3.2", "4.3"]}
    ]
    assert len(flow.recent_work_of([row] * 70)) == 60


@pytest.mark.parametrize(("days_ago", "shown"), [(3, True), (8, False)])
def test_recent_exception_recommended_only_with_recent_work(api, db, deploy, days_ago, shown):
    """D-570 시연 길: 3일 전 그 절 정비 있는 설비 알람 = 예외 추천 · 7일 밖이면 0 · 줄을 빼면 같은 알람 추천 0."""
    reset_ops(db)
    eg = {**_guide(_approved_exception(api, db)), "next_section": "4.3"}
    # 절 = 4.5.2: 준비로 승인한 보고서(4.3.2 조치)도 '최근 정비'라 그 절로는 늘 맞음 — 시연 줄만 가리는 절로 시험
    deploy.default = pack_answer(exception_guide=eg, exception_recent={"section": "4.5.2", "days": 7})
    try:
        _add("--days-ago", str(days_ago), "--action", "4.5.2")
        tech, s = start(api, db, equipment="M-01")
        s = to_instruct(tech, s)
        assert (eg["section"] in {c["section"] for c in s["switch_candidates"]}) is shown  # D-580 후보 줄
    finally:
        _drop("H-0301")
    reset_ops(db)
    tech, s = start(api, db, equipment="M-01")  # 뺀 뒤 같은 알람 = 추천 0
    s = to_instruct(tech, s)
    assert eg["section"] not in {c["section"] for c in s["switch_candidates"]}
