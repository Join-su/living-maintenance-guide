# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-528 · D-530 4 조각 2: 정비자 홈의 계통 이름 한 칸(내 알람 한 줄 · 왼쪽 「오늘 밤 알람」 칸 — 부록 4 '계통 이름은 group_systems 값을 한 칸 더함').

HOME_FIXTURE_OUT을 주면 실제 응답 한 건을 그 파일로 남긴다(정비자 화면 시험 fixture — 실제 출력 꼴, D-503 2).
"""

import json
import os

import pytest

from test_api import api, db, night, register, reset_ops  # noqa: F401
from test_lead import confirmed_report

pytestmark = pytest.mark.db


def test_home_alarm_rows_and_recent_reports_carry_system(api, db, night):
    reset_ops(db)
    tech, _, _ = confirmed_report(api, db)  # 완료 1(보고서 승인 대기)
    assert register(api, "M-02").status_code == 200  # 새 알람 1
    h = tech.get("/api/tech/home").json()
    if os.environ.get("HOME_FIXTURE_OUT"):
        with open(os.environ["HOME_FIXTURE_OUT"], "w", encoding="utf-8") as fp:
            json.dump(h, fp, ensure_ascii=False, indent=1)
    assert {a["status"] for a in h["alarms"]} == {"new", "done"}
    assert all(a["system"] == "냉각 계통" for a in h["alarms"])  # 온도 차 저하(CL) = 냉각 계통(매뉴얼 장 제목)
    new = next(a for a in h["alarms"] if a["status"] == "new")
    assert (new["alarm"]["sensor"], new["alarm"]["unit"], new["alarm"]["direction"]) == ("온도 차", "K", "경계 아래")
    assert h["recent_reports"][0]["system"] == "냉각 계통" and h["recent_reports"][0]["status"] == "awaiting_approval"
