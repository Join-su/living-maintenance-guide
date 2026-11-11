# ruff: noqa: E501 — 시험 설명 문장은 길이 제한 밖
"""D-663 S1(재시연 5 · 표시 · 근거 고르는 곳만 · Pack 셈 · 순위 손 0): 진단 근거 칸 = 1위 후보로 확정된 지난 같은 알람 기록.

1위 = Pack 순위(probabilities) 그대로(같은 값이면 매뉴얼 순 앞) · 범위 = Pack record_counts.scope 그대로
(equipment = 이 설비 · group = 같은 증상 전체) · 알람 앞 기록 · 늦은 순 · 같은 증상군. 다른 설비 센서 비슷한 카드는 화면에서 뺌(앱 몫).
M-01(저장소 이력): 온도 차 저하 원인 확정 5건 중 냉각 필터 막힘 2건 = H-0210 · H-0191 — 순위 줄 '5건 중 2건'과 같은 수. DB 없음.
"""

import json
from datetime import UTC, datetime

import pytest

from c1a15.api import screen
from test_api import db  # noqa: F401

M01_PROBS = {"CL-1": 34, "CL-2": 22, "CL-3": 22, "CL-4": 22}
M01_COUNTS = {
    "equipment_counts": {"CL-1": 2, "CL-2": 1, "CL-3": 1, "CL-4": 1},
    "equipment_total": 5,
    "count_scope": "equipment",
    "count_prior": 1,
}
AT = "2026-11-09T12:00:00+09:00"


def _recs(data_dir):
    with (data_dir / "history" / "maintenance_history.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_m01_top_cause_records_are_its_own_two_filter_records(data_dir):
    got = screen.top_records(_recs(data_dir), M01_PROBS, M01_COUNTS, "M-01", "CL", AT)
    assert [r["record_id"] for r in got] == ["H-0210", "H-0191"]  # 늦은 순
    assert len(got) == M01_COUNTS["equipment_counts"]["CL-1"]  # 순위 줄 k와 같은 수
    assert {r["outcome"]["cause_code"] for r in got} == {"CL-1"} and {r["equipment_id"] for r in got} == {"M-01"}


# 시연 첫 상태 줄(demo_history add · D-570): 알람 · 정지 시간 없는 정비 줄 — 시연 앱 DB 실제 H-0301 꼴 그대로
H0301 = {
    "demo": "D-570",
    "steps": [
        {
            "at": "2026-11-05T21:26:26+09:00",
            "kind": "action",
            "name": "흡기 필터 교체",
            "result": "완료",
            "section": "4.3.2",
        }
    ],
    "source": "generated",
    "outcome": {"type": "원인 확정", "cause_code": "CL-1", "cause_name": "냉각 필터 막힘"},
    "approval": {"at": "2026-11-05T21:34:26+09:00", "by": "시연", "status": "승인"},
    "record_id": "H-0301",
    "occurred_at": "2026-11-05T21:26:26+09:00",
    "equipment_id": "M-01",
    "symptom_group": {"code": "CL", "name": "온도 차 저하"},
}


def test_m01_first_layer_k2_drops_the_newest_demo_line_the_graph_does_not_hold_yet(data_dir):
    # D-663 덧1: 카드 수 = k. 첫 층 그래프는 시연 줄 H-0301 · H-0302를 안 실음(첫 승인 반영 때 들어옴 · deploy first-build)
    # → 앱 DB 후보 3(H-0301 · H-0210 · H-0191) > Pack k 2 = 그래프가 아직 모르는 가장 새 줄을 뺌. 리허설 13:38 500(KeyError downtime_min)도 막음.
    got = screen.top_records([*_recs(data_dir), H0301], M01_PROBS, M01_COUNTS, "M-01", "CL", AT)
    assert [r["record_id"] for r in got] == ["H-0210", "H-0191"]
    assert [screen.top_card(r)["record_id"] for r in got] == ["H-0210", "H-0191"]


def test_after_reflection_k3_the_demo_work_line_is_a_card_without_downtime(data_dir):
    # 승인 반영 뒤 그래프가 H-0301을 세면(k 3) 시연 정비 줄도 카드(알람 없음 · 정지 시간 칸 비움) — M-03(6) · M-13(4)과 같은 꼴
    counts = {**M01_COUNTS, "equipment_counts": {**M01_COUNTS["equipment_counts"], "CL-1": 3}, "equipment_total": 6}
    got = screen.top_records([*_recs(data_dir), H0301], M01_PROBS, counts, "M-01", "CL", AT)
    assert [r["record_id"] for r in got] == ["H-0301", "H-0210", "H-0191"]
    card = screen.top_card(got[0])
    assert (
        card["downtime_min"] is None
        and card["action_name"] == "흡기 필터 교체"
        and card["outcome_label"] == "냉각 필터 막힘"
    )


def test_the_card_is_the_record_card_without_a_similarity():
    card = screen.top_card(
        {
            "record_id": "H-0191",
            "equipment_id": "M-01",
            "occurred_at": "2026-09-27T09:22:00+09:00",
            "downtime_min": 30,
            "outcome": {"type": "원인 확정", "cause_code": "CL-1", "cause_name": "냉각 필터 막힘"},
            "steps": [{"kind": "check", "name": "흡기 필터 육안 점검"}, {"kind": "action", "name": "흡기 필터 교체"}],
        }
    )
    assert card == {
        "record_id": "H-0191",
        "equipment_id": "M-01",
        "month": 9,
        "similarity_pct": None,
        "outcome_type": "원인 확정",
        "cause_code": "CL-1",
        "outcome_label": "냉각 필터 막힘",
        "action_name": "흡기 필터 교체",
        "downtime_min": 30,
    }


def test_top_is_the_pack_order_and_ties_go_to_the_manual_order(data_dir):
    recs = _recs(data_dir)
    tie = {"CL-1": 25, "CL-2": 25, "CL-3": 25, "CL-4": 25}
    assert {r["outcome"]["cause_code"] for r in screen.top_records(recs, tie, M01_COUNTS, "M-01", "CL", AT)} == {"CL-1"}
    cl4 = {"CL-1": 10, "CL-2": 10, "CL-3": 10, "CL-4": 70}
    assert [r["record_id"] for r in screen.top_records(recs, cl4, M01_COUNTS, "M-01", "CL", AT)] == ["H-0154"]


def test_no_counts_no_cards_and_later_records_are_left_out(data_dir):
    recs = _recs(data_dir)
    assert screen.top_records(recs, M01_PROBS, None, "M-01", "CL", AT) == []  # 옛 세션: 근거 칸 0(지어낸 근거 0)
    assert screen.top_records(recs, {}, M01_COUNTS, "M-01", "CL", AT) == []
    assert [
        r["record_id"]
        for r in screen.top_records(recs, M01_PROBS, M01_COUNTS, "M-01", "CL", "2026-09-28T00:00:00+09:00")
    ] == ["H-0191"]


def test_group_scope_reads_the_same_symptom_records_latest_few(data_dir):
    recs = _recs(data_dir)
    group = {**M01_COUNTS, "count_scope": "group"}
    got = screen.top_records(recs, M01_PROBS, group, "M-09", "CL", AT)
    every = [
        r
        for r in recs
        if r["symptom_group"]["code"] == "CL" and (r["outcome"].get("cause_code") == "CL-1") and r["occurred_at"] < AT
    ]
    assert 0 < len(got) == min(screen.TOP_CARDS_MAX, len(every))
    assert [r["occurred_at"] for r in got] == sorted((r["occurred_at"] for r in got), reverse=True)
    assert {r["outcome"]["cause_code"] for r in got} == {"CL-1"}


@pytest.mark.db
def test_the_db_query_reads_the_loaded_history(db):  # noqa: F811
    """적재된 이력(knowledge.history_records)에서 같은 두 장 · 카드 꼴(유사 % 없음)."""
    with db.connect() as c:
        got = screen.top_cards(c, "M-01", "CL", datetime(2026, 11, 9, 3, 0, tzinfo=UTC), M01_PROBS, M01_COUNTS)
        assert [x["record_id"] for x in got] == ["H-0210", "H-0191"]
        assert {(x["outcome_label"], x["similarity_pct"], x["equipment_id"]) for x in got} == {
            ("냉각 필터 막힘", None, "M-01")
        }
        assert screen.top_cards(c, "M-01", "CL", datetime(2026, 11, 9, 3, 0, tzinfo=UTC), M01_PROBS, None) == []
