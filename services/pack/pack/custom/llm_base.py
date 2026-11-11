from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Draft:
    """LLM(또는 템플릿)이 쓴 문장과 그 문장이 붙인 근거(R8 검사 대상)."""

    text: str
    sections: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoSignal:
    """R7 구조화 출력: {interpretation: 정상|이상|불명, conflicts_with_button, out_of_scope_finding, quote}."""

    interpretation: str
    conflicts_with_button: bool
    out_of_scope_finding: bool
    quote: str = ""

    def as_event_signal(self) -> dict:
        return {
            "interpretation": self.interpretation,
            "conflicts_with_button": self.conflicts_with_button,
            "out_of_scope_finding": self.out_of_scope_finding,
            "quote": self.quote,
        }


@dataclass
class Facts:
    """코드가 만든 사실(R5-6 '고른 이유' · 호출 요약 · 보고서 문장의 재료)."""

    kind: str
    section: str = ""
    values: dict = field(default_factory=dict)


class AdapterError(RuntimeError):
    """provider · 설정 오류. 화면에는 구분된 오류(원문 대체)로 보이고 판정은 계속된다."""


class Adapter(Protocol):
    mode: str

    def write(self, facts: Facts) -> Draft: ...

    def memo_signal(self, check_section: str, criteria: str, memo: str, button: str | None) -> MemoSignal | None: ...
