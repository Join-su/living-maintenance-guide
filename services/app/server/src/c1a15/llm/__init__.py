"""LLM Adapter(P-05, B-07): 문장 · 메모 신호만 만든다. 판정은 engine이 한다(Authority boundary).

APP_MODE=fixture(기본)는 템플릿 · 단어 규칙만 쓰고 외부 호출이 0이다. live는 사용자 승인 뒤에만(EP-C1-LLM-001).
"""

import os

from c1a15.llm.base import Adapter


def adapter_for_mode(mode: str | None = None) -> Adapter:
    mode = mode or os.environ.get("APP_MODE", "fixture")
    if mode == "fixture":
        from c1a15.llm.fixture import FixtureAdapter

        return FixtureAdapter()
    if mode == "live":
        from c1a15.llm.live import LiveAdapter  # 승인 뒤에만 쓰는 경로. fixture에서는 불러오지 않는다

        return LiveAdapter.from_env()
    raise ValueError(f"알 수 없는 APP_MODE: {mode}")
