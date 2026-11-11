"""live 모드(OpenAI) — 사용자 승인 뒤에만 쓴다. fixture 모드에서는 불러오지 않는다.

외부 호출 정적 검사(scripts/external_call_check.py)의 유일한 허용 위치다. 모델 ID는 설정으로(EP-MODEL-001),
timeout과 transient 오류의 bounded 재시도만 둔다(EP-OPS-001). 비밀값은 환경 변수로만 받는다.
"""

import os

from c1a15 import redact
from c1a15.llm.base import AdapterError, Draft, Facts, MemoSignal

DEFAULT_TIMEOUT_S = 20
MAX_RETRIES = 2


class LiveAdapter:
    mode = "live"

    def __init__(self, client, model: str):
        self.client, self.model = client, model

    @classmethod
    def from_env(cls) -> "LiveAdapter":
        key, model = os.environ.get("OPENAI_API_KEY"), os.environ.get("C1A15_LLM_MODEL")
        if not key or not model:
            raise AdapterError("live 모드에는 OPENAI_API_KEY와 C1A15_LLM_MODEL이 필요합니다(사용자 승인 뒤 설정)")
        from openai import OpenAI

        return cls(OpenAI(api_key=key, timeout=DEFAULT_TIMEOUT_S, max_retries=MAX_RETRIES), model)

    def write(self, facts: Facts) -> Draft:
        prompt = (
            "다음 사실만으로 한두 문장을 쓰세요. 절 번호와 수치는 사실에 있는 것만 씁니다.\n"
            f"{facts.kind} {facts.section} {redact.redact_obj(facts.values)[0]}"  # 사실 속 메모 이름(001-63)
        )
        try:
            resp = self.client.responses.create(model=self.model, input=prompt)
        except Exception as exc:  # provider 오류는 구분된 오류로(화면은 원문 대체)
            raise AdapterError(str(exc)) from exc
        return Draft(resp.output_text.strip(), sections=(facts.section,) if facts.section else ())

    def memo_signal(self, check_section: str, criteria: str, memo: str, button: str | None) -> MemoSignal | None:
        raise AdapterError("live 메모 해석은 live 승인 slice에서 구현한다")
