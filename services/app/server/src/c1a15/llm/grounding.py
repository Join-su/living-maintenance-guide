"""R8 근거 검사(코드): 문장의 절 번호 · 수치 · 근거 사례 ID가 원천에 있는가. 실패 → 1회 다시 작성 → 또 실패 → 원문 대체.

같은 세션에서 문장 생성 · 검색 실패가 3회 연속이면 숙련자 인계를 권한다(AC-31-C2).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from c1a15.llm.base import AdapterError, Draft

FALLBACK_LABEL = "원문으로 대체됨"
REPEAT_FAILURE_LIMIT = 3


@dataclass(frozen=True)
class Grounding:
    allowed_sections: frozenset[str]
    source_numbers: frozenset[str]
    top_record_ids: frozenset[str]


@dataclass(frozen=True)
class Checked:
    text: str
    fallback: bool
    label: str | None
    attempts: int


_NUM = re.compile(r"\d+(?:\.\d+)?")
_SECTION = re.compile(r"(?<![\d.])\d\.\d(?:\.\d)?(?![\d.])")  # "4.3절"처럼 한글이 붙어도 절 번호
_RECORD = re.compile(r"\b[HR]-\d{4}\b")
_EXCEPTION = re.compile(
    r"\bEX-[HR]-\d+\b"
)  # 예외 절 번호(약속 v8 EX-<기록>) — 안의 기록 번호는 근거 기록이 아니라 절(덧 37)


def is_section_like(token: str) -> bool:
    """'7.8'처럼 절 번호 꼴인 수치인가(센서 값이 절 번호로 읽히는 경우를 호출자가 구분할 때)."""
    return bool(_SECTION.fullmatch(token))


def numbers_in(*texts: str) -> frozenset[str]:
    return frozenset(n for t in texts for n in _NUM.findall(t or ""))


def is_grounded(draft: Draft, g: Grounding) -> bool:
    text = _EXCEPTION.sub(" ", draft.text)
    sections = set(_SECTION.findall(text)) | set(_EXCEPTION.findall(draft.text)) | set(draft.sections)
    if not sections <= g.allowed_sections:
        return False
    records = set(_RECORD.findall(text)) | set(draft.record_ids)
    if not records <= g.top_record_ids:
        return False
    text_wo = _RECORD.sub(" ", _SECTION.sub(" ", text))
    return set(_NUM.findall(text_wo)) <= g.source_numbers


def checked(generate: Callable[[], Draft], g: Grounding, fallback_text: str) -> Checked:
    """생성 → 검사 → 1회 다시 작성 → 원문 대체. provider 오류도 원문 대체(판정은 계속)."""
    for attempt in (1, 2):
        try:
            draft = generate()
        except AdapterError:
            return Checked(fallback_text, True, FALLBACK_LABEL, attempt)
        if is_grounded(draft, g):
            return Checked(draft.text, False, None, attempt)
    return Checked(fallback_text, True, FALLBACK_LABEL, 2)


def should_recommend_handover(fallback_history: list[bool]) -> bool:
    """같은 세션에서 연속 3회 원문 대체면 숙련자 인계 권함."""
    run = 0
    for f in fallback_history:
        run = run + 1 if f else 0
    return run >= REPEAT_FAILURE_LIMIT
