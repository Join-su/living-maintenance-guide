"""EV-004(D-390): 메모 해석(R7)은 그 점검 절의 판정 기준 줄 낱말로.

절 글 전체의 앞 낱말(점검 이름 · 권한 · 등급)이 아님.

재현(2차 점검 가이드 H4): 4.5 냉각수 수위 확인에서 '정상' + 메모 '수위 MAX 근처'가 '이상'으로 읽혀 확인 질문이 났음.
"""

import pytest

from c1a15.engine import knowledge
from c1a15.loader import core


@pytest.fixture(scope="module")
def k(data_dir_module):
    return knowledge.from_sections(core.read_manual(data_dir_module))


def test_topic_word_memo_gives_no_signal(k, monkeypatch):
    from c1a15.api import sessions

    monkeypatch.setenv("APP_MODE", "fixture")
    assert sessions._memo_signal(k, "4.5", "수위 MAX 근처", "정상") == (None, False)


def test_criterion_word_memo_still_conflicts(k, monkeypatch):
    """시연 DEMO-04 꼴: 판정 기준 낱말(기름때)이 든 메모 + '정상' → 확인 질문 그대로."""
    from c1a15.api import sessions

    monkeypatch.setenv("APP_MODE", "fixture")
    sig, failed = sessions._memo_signal(k, "4.3", "기름때가 필터 표면 절반 넘게 덮음", "정상")
    assert not failed and sig["interpretation"] == "이상" and sig["conflicts_with_button"]
