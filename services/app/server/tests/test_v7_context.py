# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""약속 v7(D-546 덧 27): start · judge 본문에 알람 난 설비(equipment_id) · 알람 시각(occurred_at, 초까지 · 시간대) —
Pack 그래프 '같은 설비' 범위 · 설비 조건 예외 관계 · 앞선 기록만. line_id는 앱에 값이 없어 싣지 않음.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from c1a15.api.sessions import GROUP_NAME
from c1a15.engine import exception
from fake_deploy import pack_answer
from test_api import reset_ops, start, to_instruct  # noqa: F401
from test_deploy_path import api, db, deploy  # noqa: F401

pytestmark = pytest.mark.db

OCCURRED = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$"  # contracts/v7 run_request.json
)


def _bodies(deploy, kind):
    return [r["body"] for r in deploy.posts() if r["body"].get("kind") == kind]


def _graph(group: str, equipment_id: str) -> dict:
    """그래프 꼴(d545 graph-shape 3 · 4절) 최소 — 설비 조건이 붙은 안내 관계 하나."""
    return {
        "groups": [{"code": k, "name": v} for k, v in GROUP_NAME.items()],
        "sections": [{"no": "EX-R-0005", "kind": "예외", "grade": "C", "name": "설비 조건 예외"}],
        "exception_relations": [
            {
                "id": "XR-1",
                "step": "EX-R-0005",
                "mode": "안내",
                "grade": "C",
                "when": {"group": GROUP_NAME[group], "sensors": [], "equipment_id": equipment_id},
            }
        ],
    }


def test_start_and_judge_carry_alarm_equipment_and_time(api, db, deploy):
    reset_ops(db)
    deploy.default = pack_answer()
    n_start, n_judge = len(_bodies(deploy, "start")), len(_bodies(deploy, "judge"))
    tech, s = start(api, db, equipment="M-02")
    to_instruct(tech, s)
    with db.connect() as c:
        eq, at = c.execute(text("select equipment_id, occurred_at from ops.alarms")).one()
    bodies = _bodies(deploy, "start")[n_start:] + _bodies(deploy, "judge")[n_judge:]
    assert [b["kind"] for b in bodies][:2] == ["start", "judge"]
    for b in bodies:
        assert b.get("equipment_id") == eq == "M-02"
        assert re.fullmatch(OCCURRED, b.get("occurred_at") or "")
        assert datetime.fromisoformat(b["occurred_at"]) == at.replace(microsecond=0)
        assert "line_id" not in b  # 값 없음 = 칸 없음
    if os.environ.get("V7_BODY_OUT"):  # D-503 2: 실제 꼴 한 건(start · 첫 judge 본문 그대로)
        Path(os.environ["V7_BODY_OUT"]).write_text(
            json.dumps(bodies[:2], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    b = bodies[0]  # 설비 조건 관계: 본문 칸으로 맞음 · 칸 없으면(v6 꼴) 같은 증상군이어도 안 맞음
    snap = b["query"]["sensor_snapshot"]
    assert exception.match(_graph(b["group"], "M-02"), b["group"], snap, b.get("equipment_id"))[0]["id"] == "XR-1"
    assert exception.match(_graph(b["group"], "M-02"), b["group"], snap, None) is None
    assert exception.match(_graph(b["group"], "M-09"), b["group"], snap, b.get("equipment_id")) is None
