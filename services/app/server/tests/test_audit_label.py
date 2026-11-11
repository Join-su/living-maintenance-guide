"""D-529: 감사 기록 '행동' 이름표 — ML 길 알람 없음 · 옛 후보 행도 영어 이름 0(DB 없음)."""

from types import SimpleNamespace

import pytest

from c1a15.api import lead

K = SimpleNamespace(sections={})


@pytest.mark.parametrize(
    ("action", "detail", "want"),
    [
        ("alarm_rejected", {"reason": "low_confidence"}, "알람 없음 · 유형 확신 낮음"),
        ("alarm_rejected", {"reason": "rnf"}, "알람 없음 · 유형 RNF"),
        ("alarm_rejected", {"reason": "model_failure"}, "알람 없음 · 예측 실패"),
        ("alarm_rejected", {"reason": "ml_type_alarm_unsupported"}, "알람 없음 · ML 유형 · 경계 표시 없음"),
        ("alarm_rejected", {"reason": "new_reason"}, "알람 없음 · 그 밖 사유"),  # 실패 쪽: 모르는 사유도 영어 0
        ("alarm_rejected", {}, "알람 없음 · 그 밖 사유"),
        ("alarm_candidate", {"reason": "rnf"}, "후보 기록 · 유형 RNF"),  # D-529 전 옛 행
        ("candidate_promote", {"reason": "low_confidence"}, "후보 올림"),
        ("candidate_dismiss", {"reason": "rnf"}, "후보 버림"),
    ],
)
def test_alarm_rejected_and_old_candidate_rows_have_korean_labels(action, detail, want):
    assert lead.audit_label(SimpleNamespace(action=action, detail=detail), K) == want
