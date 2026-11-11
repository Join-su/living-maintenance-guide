"""체크 기록 v1 모양(EV-006 CH-1) — 이 모델이 schema 정본이다.

같은 폴더 check-record-v1.schema.json은 여기서 만든 것(python -m c1a15.checkrecord --schema).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Session(_M):
    pack_session_id: str
    alarm_id: int
    equipment_id: str
    symptom_group: str
    alarm_source: Literal["boundary", "ml_predicted"]
    scenario_id: str | None
    occurred_at: str
    closed_at: str | None


class Runs(_M):
    deployment_id: str | None
    start: str | None
    judge: list[str]
    alarm: str | None
    report: str | None


class Cause(_M):
    cause: str
    p: int


class FirstDecision(_M):
    run_id: str | None
    top: list[Cause]
    pack_top: list[Cause]


class FirstInstruction(_M):
    section: str
    kind: str
    run_id: str | None


class Step(_M):
    seq: int
    at: str
    kind: Literal[
        "lock", "precheck", "check", "confirm", "out_of_scope", "call", "action", "restart", "monitor", "handover"
    ]
    section: str | None
    grade: str | None
    opened_by_run: str | None
    recommended: bool
    result: str
    judged: str | None
    memo: str | None
    has_memo: bool
    corrected: int
    void: bool


class Outcome(_M):
    type: Literal["원인 확정", "이상 없음·감시", "원인 미상·숙련자 인계"]
    cause_code: str | None
    cause_text: str | None
    action_section: str | None
    restart: Literal["within", "outside"] | None


class Edit(_M):
    field: str
    before: str
    after: str


class Reject(_M):
    at: str | None
    reason: str | None


class Lead(_M):
    decision: Literal["approve", "edit"]
    edits: list[Edit]
    cause_text_changed: bool
    rejects: list[Reject]
    at: str


class Signal(_M):
    id: Literal["S1", "S2", "S3", "S4", "S5"]
    rule: Literal["check-signals-v1"]
    runs: list[str | None]
    sections: list[str] | None = None
    first: str | None = None
    final: str | None = None
    before: str | None = None
    after: str | None = None
    reasons: list[str] | None = None
    basis: list[str] | None = None


class Query(_M):
    symptom_group: str
    sensor_snapshot: dict[str, float]


class FieldTruth(_M):
    query: Query
    confirmed_sections: list[str]
    confirmed_cause: str


class ExceptionOffer(_M):
    """D-633 예외 추천이 보인 세션 — 사람이 그 예외를 골랐나(선택 칸 · 예외가 안 보인 세션은 null)."""

    relation_id: str | None
    run_id: str | None
    adopted: bool
    how: Literal["switched", "followed"] | None
    picked_section: str | None


class CheckRecordV1(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["c1a15.check-record.v1"] = Field(alias="schema")
    record_key: str
    made_at: str
    session: Session
    runs: Runs
    first_decision: FirstDecision
    first_instruction: FirstInstruction | None
    steps: list[Step]
    outcome: Outcome
    lead: Lead
    signals: list[Signal]
    field_truth: FieldTruth | None
    exception_offer: ExceptionOffer | None = None


def schema_text() -> str:
    """정본 JSON schema 글(정렬 · 끝 줄바꿈) — 파일과 바이트 같아야 함(VP-1)."""
    import json

    return (
        json.dumps(CheckRecordV1.model_json_schema(by_alias=True), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    )
