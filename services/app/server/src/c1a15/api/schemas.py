"""요청 본문(OpenAPI 정본의 입력 스키마). 정해진 필드만 받는다(extra = forbid).

업무 기록 event에는 서버만 쓰는 필드(void · signal · at)가 있다. 클라이언트가 그 필드를 보내면 422로 거부한다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Strict):
    user_id: str
    password: str


class TrendPoint(_Strict):
    at: str
    value: int | float


class AlarmBody(_Strict):
    """시연 알람 발생기 → 알람 등록. 증상군 · 경계 판정 · 시각은 서버가 정한다."""

    equipment_id: str
    sensor_snapshot: dict[str, int | float]
    trend: list[TrendPoint] = []
    scenario_id: str | None = None
    eval_mode: bool = False  # P-16 평가 실행기 알람: 승인해도 이력 · 받은 편지에 넣지 않음(R12 · 결정 017 ②)
    # deploy 길(EV-001 ES-08)에서 Pack 알람 입력에 담는 것 — 없으면 각본(scenario_id)의 UDI · Type으로 채운다
    data_lineage: dict[str, int | str | None] | None = None
    product_type: Literal["L", "M", "H"] | None = None


class ExceptionMatch(_Strict):
    """약속 v10 3절: 비슷한 것 하나(exception_similar 결과 matches 줄의 kind · no)."""

    kind: Literal["예외", "매뉴얼"]
    no: str = Field(max_length=40)


class ExceptionCheckIn(_Strict):
    """약속 v10 1절 점검 칸(what만 필수)."""

    what: str = Field(min_length=1, max_length=200)
    how: str = Field(default="", max_length=500)
    criteria: str = Field(default="", max_length=200)
    minutes: int | None = Field(default=None, ge=1)
    instruction: str = Field(default="", max_length=500)  # D-642 C 지시 문장(선택 · 빈 글 = 칸 없음)


class ExceptionActionIn(_Strict):
    """약속 v10 1절 조치 칸(what만 필수)."""

    what: str = Field(min_length=1, max_length=200)
    parts: list[str] = Field(default=[], max_length=10)
    minutes: int | None = Field(default=None, ge=1)
    instruction: str = Field(default="", max_length=500)  # D-642 C 지시 문장(선택 · 빈 글 = 칸 없음)


class ExceptionEdit(_Strict):
    """same-edit.md 1절 exception_review.edit(정답지 30쪽 같음 + 고침/보탬):
    고친 뒤 대상 예외의 점검 목록 · 조치 목록 전체."""

    checks: list[ExceptionCheckIn] = Field(min_length=1, max_length=10)
    actions: list[ExceptionActionIn] = Field(min_length=1, max_length=10)


class ExceptionReview(_Strict):
    """약속 v10 3 · 6절 exception_review: 예외 전체에 한 번 같음(그 match = 놓침) / 아님(새 예외).
    edit = 2단계 같음 + 고침(same-edit.md 1절 · 기존 예외 match만 · 고친 뒤 목록 전체)."""

    decision: Literal["same", "not"]
    match: ExceptionMatch | None = None
    edit: ExceptionEdit | None = None


class ExceptionInput(_Strict):
    """D-538 · D-556 예외 상황 기록: 정비자가 예외라고 판단할 때만 적는 글 하나.
    확정 · 적용 때는 그 글의 「AI 나눔」 예외 전체에 한 번 같음 / 아님(exception_review)
    — 상황 · 결과 · 자동 정보는 서버가 붙임."""

    text: str = Field(default="", max_length=2000)


class EventBody(_Strict):
    """정비자 입력 하나. seq = 마지막으로 본 seq + 1(idempotency key = 세션 ID + seq)."""

    seq: int
    type: Literal[
        "alarm_ack",
        "lock",
        "equipment_change",
        "precheck_done",
        "b_checklist_done",
        "result",
        "confirm_answer",
        "out_of_scope_choice",
        "action_done",
        "restart",
        "restart_result",
        "monitor_close",
        "call_send",
        "call_wait",
        "report_edit",
        "report_confirm",
        "handover_request",
        "exception_draft",
        "exception_split",
        "switch_check",
        "exception_start",
        "exception_apply",
    ]
    section: str | None = None
    value: Literal["정상", "이상", "확인 불가"] | None = None
    memo: str | None = None
    values: str | dict[str, str] | None = None
    checked: list[str] | None = None
    equipment_id: str | None = None
    yes: bool | None = None
    choice: Literal["handover", "continue"] | None = None
    outcome: Literal["within", "outside"] | None = None  # 재가동 후 값: 정비자가 고름
    summary: str | None = None  # 호출 · 인계 요약(고친 문장)
    field: str | None = None  # 보고서 항목 키(R10)
    text: str | None = None  # 보고서 항목 문장(고친 문장, R8 검사)
    exception_record: ExceptionInput | None = None  # D-538: 예외 상황 기록(선택) — 보고서 확정과 함께
    exception_text: str | None = Field(default=None, max_length=2000)  # D-556 「AI 나눔」할 예외 글
    exception_review: ExceptionReview | None = None  # 약속 v10 3절(한 번)
    mode: Literal["call_first", "act_now"] | None = None  # 약속 v9 exception_start: 호출 후 조치 / 바로 조치

    @model_validator(mode="after")
    def _required_by_type(self):
        """type별 필수 필드가 없으면 422(판정 함수의 KeyError 500 방지)."""
        need = {
            "result": ("section",),
            "b_checklist_done": ("section", "checked"),
            "confirm_answer": ("yes",),
            "out_of_scope_choice": ("choice",),
            "action_done": ("section",),
            "restart": ("checked",),
            "restart_result": ("outcome",),
            "report_edit": ("field", "text"),
            "exception_draft": ("exception_record",),
            "exception_split": ("exception_text",),
            "switch_check": ("section",),
            "exception_start": ("mode",),
            "exception_apply": ("exception_record",),
        }.get(self.type, ())
        missing = [f for f in need if getattr(self, f) is None]
        if self.type == "result" and self.value is None and not self.memo:
            missing.append("value 또는 memo")  # 빈 결과는 받지 않는다(S02-G6-3)
        if missing:
            raise ValueError(f"{self.type}에 필요한 필드 없음: {', '.join(missing)}")
        return self


# ---------------------------------------------------------------- 파트장(P-11)


class EditsBody(_Strict):
    """수정 후 승인 문장 검사(R8): 고친 보고서 항목 키 → 문장."""

    edits: dict[str, str]


class DecisionBody(_Strict):
    """L03 결정 저장: 그대로 승인 · 수정 후 승인(고친 문장) · 반려(memo = 사유, 필수)."""

    decision: Literal["approve", "edit", "reject"]
    edits: dict[str, str] = {}
    memo: str | None = None


class AnswerBody(_Strict):
    """L04 답 저장: 파트장 호출 approved · daytime · direct, 숙련자 인계 assign · direct + 처리 메모."""

    answer: Literal["approved", "daytime", "direct", "assign"]
    note: str | None = None


class GradeBody(_Strict):
    """L05 등급 변경: 경고를 확인했을 때만(confirmed = true) 저장."""

    grade: Literal["A", "B", "C", "D"]
    confirmed: bool = False


class CorrectionBody(_Strict):
    """T15 '이 기록 정정'(AC-45): 대상 기록 seq · 정정 값(결과 · 메모 또는 사전 확인 측정값) · 사유(필수) · 확인."""

    target_seq: int
    value: Literal["정상", "이상", "확인 불가"] | None = None
    memo: str | None = None
    values: str | None = None
    reason: str
    confirmed: bool = False  # AC-46-C3: 다시 판정으로 열린 지시가 바뀌는 확인 창을 거쳤음


class PrecheckBody(_Strict):
    """D-619 사전 점검 후보: 살펴봄 · 이번엔 제외(이유) — 기록만."""

    choice: Literal["look", "skip"]
    reason: str | None = Field(default=None, max_length=200)


class NoteBody(_Strict):
    """T15 '메모 덧붙이기'(AC-48-C1): 정정하지 않는 기록에 덧붙일 메모."""

    target_seq: int
    memo: str
