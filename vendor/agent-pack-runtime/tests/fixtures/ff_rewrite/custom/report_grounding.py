"""시험용(D-462): C1-a15 custom/report_grounding.py 꼴 축소 — 문장 낱말이 모두 초안에 있으면 근거 있음."""
import re
from dataclasses import dataclass

from custom.llm_base import Draft

_WORD = re.compile(r"[가-힣A-Za-z0-9.]+")


@dataclass(frozen=True)
class Grounding:
    words: frozenset


def words_in(*texts):
    return {w for t in texts for w in _WORD.findall(t or "")}


def is_grounded(draft: Draft, g: Grounding) -> bool:
    return set(_WORD.findall(draft.text)) <= g.words


def unused_helper(x):
    return x * 2
