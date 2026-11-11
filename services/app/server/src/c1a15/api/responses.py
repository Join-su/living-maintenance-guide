"""응답 본문(OpenAPI 정본의 출력 스키마, P-19: 화면 값 API). 앱 타입은 이 정본에서 생성한다(EP-C1-APITYPE-001).

서버가 만든 값은 정해진 필드만(extra = forbid). 정비 이력 원본을 그대로 넘기는 객체(점검 경로 · 결과)는 extra = allow.
"""

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Passthrough(BaseModel):
    model_config = ConfigDict(extra="allow")


class TrendPoint(_Out):
    at: str
    value: int | float


class AlarmValue(_Out):
    sensor: str
    value: int | float
    unit: str
    boundary: int | float
    direction: str
    trend_title: str | None = None
    origin: str | None = None  # EV-009: "lead" = 경계 안 후보를 파트장이 올림
    crossed: bool | None = None


class Progress(_Out):
    check_no: int
    check_max: int


# ---------------------------------------------------------------- 홈 · 종(T02 · T12)


class AlarmMl(_Out):
    """D-546 U5 화면 칸: 알람 행에 저장한 Pack ML 값 중 예측 유형(확률 1위) · 위험도."""

    type: str | None
    type_name: str | None
    risk: float | None


class AlarmRow(_Out):
    alarm_id: int
    equipment_id: str
    symptom_group: str
    group_name: str
    system: str | None = None
    alarm: AlarmValue
    sensor_snapshot: dict[str, int | float]
    trend: list[TrendPoint]
    occurred_at: str
    status: str
    change_seq: int
    session_id: int | None
    progress: Progress | None
    report_status: str | None
    report_session_id: int | None
    ml: AlarmMl | None = None
    call_sent: str | None = None  # G3: 보낸 호출 답 전(approval · handover)
    exception_progress: bool = False  # 약속 v9 [예외] 진행 세션(왼쪽 칸 '진행 중 · 예외 조치')


class RecentReport(_Out):
    report_id: int
    session_id: int
    equipment_id: str
    system: str | None = None
    cause: str
    line: str = ""  # D-642 R5: 한 줄(예외면 '예외상황 · 점검 → 조치', 아니면 원인 이름)
    confirmed_at: str | None
    status: str
    reject_reason: str | None = None
    rejected_by_name: str | None = None
    rejected_at: str | None = None


class InProgress(_Out):
    session_id: int
    alarm_id: int
    progress: Progress


class Shift(_Out):
    label: str
    start: str
    end: str
    start_text: str
    end_text: str
    done: int
    in_progress: int
    calls: int


class ToolCaution(_Out):
    """D-546 U6 공구 교체 주의(알람 아님): 설비마다 확인 전 최근 값 하나."""

    caution_id: int
    equipment_id: str
    tool_wear_min: float
    at: str


class ToolCautionAck(_Out):
    caution_id: int
    equipment_id: str


class PrecheckBasis(_Out):
    record_id: str
    equipment_id: str
    at: str
    result: str
    follow_days: int | None


class PrecheckMark(_Out):
    choice: str
    by: str
    at: str
    reason: str | None


class PrecheckCard(_Out):
    """D-619 사전 점검 후보(고장 판단 아님): 건수는 코드가 이력에서 셈."""

    pattern: str
    record_id: str
    equipment_id: str
    section: str
    section_title: str
    days_ago: int
    within_days: int
    follow: str
    look_at: str
    act_short: str  # D-670 카드 펼침 한 줄 짧은 앞 조치 이름
    follow_short: str  # D-670 카드 펼침 한 줄 짧은 뒤 사건 이름
    total: int
    hits: int
    clean: int
    basis: list[PrecheckBasis]
    mark: PrecheckMark | None
    assignee: str  # D-642 E 그 설비 담당자 id
    assignee_name: str  # D-642 E 담당자 표시 이름(정비자 홈 담당 표시 · 파트장 칸)


class PrecheckAck(_Out):
    pattern: str
    record_id: str


class Home(_Out):
    alarms: list[AlarmRow]
    new_since: list[AlarmRow]
    in_progress: list[InProgress]
    change_seq: int
    shift: Shift
    recent_reports: list[RecentReport]
    cautions: list[ToolCaution]
    prechecks: list[PrecheckCard]


class NotifyItem(_Out):
    kind: str
    alarm_id: int
    session_id: int | None = None
    equipment_id: str | None = None
    group_name: str | None = None
    occurred_at: str | None = None
    progress: Progress | None = None
    call_section: str | None = None
    call_title: str | None = None
    call_grade: str | None = None
    answer: str | None = None
    answered_by: str | None = None
    answered_by_name: str | None = None
    answered_at: str | None = None
    report_cause: str | None = None
    reject_reason: str | None = None
    rejected_by_name: str | None = None
    rejected_at: str | None = None


class Notifications(_Out):
    items: list[NotifyItem]
    empty: bool


# ---------------------------------------------------------------- 세션(T03 ~ T07)


class SensorRow(_Out):
    key: str
    name: str
    value: int | float
    unit: str
    value_text: str
    range_text: str
    status: str
    is_alarm_sensor: bool
    out_of_range: bool


class Reading(_Out):
    text: str
    fallback: bool
    label: str | None


class AlarmBlock(_Out):
    alarm_id: int
    equipment_id: str
    equipment_type: str
    occurred_at: str
    symptom_group: str
    group_name: str
    system: str
    alarm: AlarmValue
    boundary_text: str
    out_of_range_count: int
    trend: list[TrendPoint]
    trend_title: str | None
    sensors: list[SensorRow]
    ai_reading: Reading
    similar_count: int
    ml: AlarmMl | None = None


class RecordCard(_Out):
    record_id: str
    equipment_id: str
    month: int
    similarity_pct: int | None  # D-663: 근거 카드(1위 후보 확정 기록)는 없음
    outcome_type: str
    cause_code: str | None
    outcome_label: str
    action_name: str
    downtime_min: int | float | None  # D-663 덧1: 시연 정비 줄(알람 없음) 근거 카드는 없음


class ResultRow(_Out):
    seq: int | None
    section: str | None
    value: str | None
    memo: str | None


class Step(_Out):
    kind: str | None
    section: str | None
    grade: str | None
    cause: str | None
    reason: str | None
    unable_before: int | None
    interpretation: str | None
    quote: str | None
    outcome: str | None
    call_status: str | None
    corrected: bool | None = None  # S-05: 조치 뒤 정정 호출(T10 / summary_corrected)
    step: str | None = None  # 약속 v10 5절 예외 안내 걸음(c1 · a1 …) — 걸음 결정만
    step_index: int | None = None
    steps_total: int | None = None


class PathEntry(_Out):
    at: str | None
    title: str
    detail: str
    status: str


class ActionBlock(_Out):
    cause_code: str
    cause_name: str
    evidence_record_ids: list[str]
    section: str
    title: str
    verb: str
    grade: str | None
    minutes: int | None
    done_at: str | None
    checklist: list[str]
    checklist_done: bool
    steps: list[str]  # 조치 절의 절차(매뉴얼 번호 줄 그대로 · D-530 답장 001-zzy)


class RestartValue(_Out):
    sensor: str
    value: int | float
    value_text: str
    boundary_text: str
    rule_text: str
    basis: str


class RestartBlock(_Out):
    section: str
    title: str
    grade: str | None
    checklist: list[str]
    restarted_at: str | None
    outcome: str | None
    value: RestartValue | None = None


class MonitorBlock(_Out):
    section: str
    title: str
    grade: str | None
    closed: bool


class CheckDone(_Out):
    section: str
    title: str
    value: str
    note: str | None = None  # 정정된 결과: '정정 n회, 원래 …'(T10 / summary_corrected)


class CallCorrection(_Out):
    """정정 호출을 연 정정(조치 뒤 결과 정정, AC-47-C1)."""

    section: str | None
    title: str | None
    before: str | None
    after: str | None
    reason: str | None
    at: str | None


class ActionDone(_Out):
    section: str
    title: str
    at: str | None


class Candidate(_Out):
    code: str
    name: str
    pct: int


class CallAnswer(_Out):
    answer: str | None
    by: str | None
    by_name: str | None
    at: str | None
    note: str | None


class ExceptionCheckFields(_Out):
    """D-554 ④ 한 점검(c1a15-pack 꼴 정본 fields-gate 1절)."""

    what: str
    how: str
    criteria: str
    minutes: int | None
    instruction: str | None = Field(
        default=None, exclude_if=lambda v: v is None
    )  # D-642 C 지시 문장 · 없으면 칸 안 나감


class ExceptionActionFields(_Out):
    """D-554 ④ 한 조치(c1a15-pack 꼴 정본 fields-gate 1절)."""

    what: str
    parts: list[str]
    minutes: int | None
    instruction: str | None = Field(
        default=None, exclude_if=lambda v: v is None
    )  # D-642 C 지시 문장 · 없으면 칸 안 나감


class ExceptionGuideNext(_Out):
    section: str
    title: str


class ExceptionGuideSource(_Out):
    """출처 한 줄 = 앱 원본(승인 이력)의 예외 상황 기록."""

    record_id: str
    equipment_id: str
    occurred_at: str
    situation: str


class ExceptionGuide(_Out):
    """D-543 예외 안내(호출 단계) = 약속 v8 결과 exception_guide 칸 그대로 + 출처(앱 원본) · 원래 순서 다음(null)."""

    section: str
    relation_id: str
    grade: str | None
    title: str | None
    text: str | None
    steps: list[str]
    criteria: str | None
    basis: list[str]
    source: ExceptionGuideSource | None
    next: ExceptionGuideNext | None
    topic: str | None = None  # 고침A '예외상황-{주제}'의 주제(Pack 결과 칸 임시 이름 · 없으면 title)
    checks: list[ExceptionCheckFields] = []  # 약속 v10 4절: 그 절 점검 목록(걸음 길 · 없으면 옛 길)
    actions: list[ExceptionActionFields] = []  # 약속 v10 4절: 그 절 조치 목록


class ExceptionWalkStep(_Out):
    """정답지 26쪽 '안내 차례' 한 줄(약속 v10 5절 걸음): c1 … 점검 → a1 … 조치."""

    step: str
    side: str  # check | action
    what: str
    say: str  # D-642 C 지시 문장(승인된 instruction · 없으면 무엇 · 어떻게 · 판정 기준) — '지금'은 now로 따로
    done: bool
    now: bool


class CallBlock(_Out):
    kind: str
    reason: str | None
    head: str | None
    section: str | None
    title: str | None
    grade: str | None
    minutes: int | None
    checks_done: list[CheckDone]
    candidates: list[Candidate]
    summary: Reading
    status: str
    waiting_at: str | None
    sent_at: str | None
    answer: CallAnswer | None
    correction: CallCorrection | None = None
    actions_done: list[ActionDone] = []
    exception_guide: ExceptionGuide | None = None


class ReportField(_Out):
    key: str
    label: str
    source: str
    text: str
    edited: bool
    ok: bool
    unsupported_numbers: list[str]
    fallback: bool | None = None  # 원인 · 조치만: 원문으로 대체됨(EV-001 CH-21)


class ReportCheck(_Out):
    key: str
    label: str
    ok: bool


class Rejection(_Out):
    reason: str | None
    by: str | None
    by_name: str | None
    at: str | None


class ExceptionPrev(_Out):
    record_id: str
    occurred_at: str
    outcome: str | None  # 확정 원인, 없으면 결과 종류
    action: str | None


class ExceptionCheck(_Out):
    section: str
    title: str


class ExceptionRecent(_Out):
    """D-570 최근 N일 정비 이력 한 건(recent-condition.md: did · checked = 앱 번호 · 적은 차례)."""

    record_id: str
    occurred_at: str
    days_before: int
    did: list[str]
    checked: list[str]
    outcome: str | None


class ExceptionContext(_Out):
    """D-538 자동 정보 여덟 — 데이터에 없는 칸은 null(지어내기 0)."""

    equipment_id: str
    symptom_group: dict[str, str]
    occurred_at: str
    sensor_values: dict
    previous_maintenance: ExceptionPrev | None
    recent_maintenance: list[ExceptionRecent] = []  # D-570: 같은 설비 최근 N일 정비 이력 전부(전 기록 = 빈 목록)
    ai_first_check: ExceptionCheck | None
    confirmed_cause: str
    report_id: int | None


class ExceptionDraft(_Out):
    """D-543 「노하우 등록」 초안(D-556: 글 하나) — 보고서 '예외 상황 기록' 칸에 채워짐."""

    text: str


class ExceptionSimilar(_Out):
    """약속 v10 3절 비슷한 것 하나(exception_similar matches 줄): 등록 예외 절 · 매뉴얼 점검 · 조치 절."""

    kind: str  # 예외 | 매뉴얼
    no: str
    name: str
    score: float
    checks: list[ExceptionCheckFields] | None = None  # 예외 줄 지금 목록(선택 · 같음 + 고침 바탕 · D-560 덧 10)
    actions: list[ExceptionActionFields] | None = None


class ExceptionMatchRef(_Out):
    kind: str
    no: str


class ExceptionEditOut(_Out):
    """same-edit.md 1절 edit(정답지 30쪽 같음 + 고침): 고친 뒤 대상 예외의 점검 목록 · 조치 목록 전체."""

    checks: list[ExceptionCheckFields]
    actions: list[ExceptionActionFields]


class ExceptionReviewOut(_Out):
    """약속 v10 6절 exception_record.exception_review(한 번 · 확정 기록 · 승인 기록 파일에 그대로)."""

    decision: str  # same | not
    match: ExceptionMatchRef | None
    edit: ExceptionEditOut | None = None  # same-edit.md 1절(같음 + 고침일 때만)


class ExceptionOne(_Out):
    """예외 하나(약속 v10 1절) = 주제 + 점검 목록 + 조치 목록 + 비슷한 것(3절) + 확정 · 적용 뒤 같음 / 아님."""

    topic: str  # 15자 안 · 이름 = '예외상황-{topic}'
    checks: list[ExceptionCheckFields]
    actions: list[ExceptionActionFields]
    matches: list[ExceptionSimilar] = []
    edit: ExceptionEditOut | None = None  # 2단계 같음 + 고침(정답지 30쪽 · 같음일 때만)
    decision: str | None = None  # 적용 뒤: same | not
    match: ExceptionMatchRef | None = None  # 적용 뒤: 같다고 한 것
    kind: str | None = None  # 적용 뒤 기록 이름(약속 v9 3절): missed | new_exception


class ExceptionSplit(_Out):
    """「AI 나눔」 마지막 결과(확정 전) — text가 지금 글과 다르면 다시 눌러야 함 · 나누지 못함이면 exception null."""

    text: str
    exception: ExceptionOne | None
    error: str | None
    similar_error: str | None = None  # 비슷한 것 찾기 실패(아님만 고름)


class ExceptionAuto(_Out):
    """D-554 ④ 자동 두 칸(입력 0): 상황 = 설비 · 알람 + 센서 근거, 결과 = 재가동 판정."""

    situation: str
    result: str


class SwitchCandidate(_Out):
    """D-556 [점검 바꾸기] 후보 한 줄(판정 reason_facts.alternatives)."""

    section: str
    title: str
    grade: str | None
    summary: str | None = None  # 고침A 13쪽 제목 아래 설명(임시 = 매뉴얼 점검 방법 첫 줄 · Pack 출력 오면 그 칸)
    exception: bool = False  # D-580 예외 후보(같은 줄 · 표시만 작게)


class ExceptionApplied(_Out):
    """정답지 18 · 19쪽: 팝업에서 「보고서에 적용」한 예외 상황(확정 전)."""

    text: str
    exception: ExceptionOne | None
    exception_review: ExceptionReviewOut | None
    split_error: str | None = None


class ExceptionNote(_Out):
    """D-543 참고 알림: 같은 설비 · 같은 증상군의 승인된 예외 상황 기록."""

    record_id: str
    occurred_at: str
    situation: str
    action: str
    text: str  # 알림에 보일 한 줄(D-554 ④ 칸 기록 = 칸을 이은 글 · 전 기록 = 상황 글)
    topic: str | None = None  # 고침A 12쪽 '이전 예외 기록 · 예외상황-{주제}'(예외 주제 · 없으면 None)


class ExceptionRecord(_Out):
    situation: str  # 상황(D-556 = 자동 · 전 기록 = 정비자 글)
    context: ExceptionContext
    action: str | None = None  # 전 기록 = 무엇을 했나 글(D-556 기록은 없음)
    text: str | None = None  # D-556 정비자 글 그대로
    exception: ExceptionOne | None = None  # 약속 v10 6절 예외 하나 + 같음 / 아님(v9 짝 기록은 하나로 읽음)
    exception_review: ExceptionReviewOut | None = None  # 약속 v10 6절 · 승인 기록 파일 칸(한 번)
    split_error: str | None = None  # 나누지 못함(pack_unavailable | not_split | invalid_output) → 글 그대로 새 예외
    result: str | None = None  # D-556 자동(재가동 판정)


class ReportBlock(_Out):
    status: str
    report_id: int | None = None
    rejection: Rejection | None = None
    fields: list[ReportField]
    checks: list[ReportCheck]
    messages: list[str]
    can_confirm: bool
    confirmed_at: str | None
    exception_record: ExceptionRecord | None = None  # D-538: 마지막 확정 때 함께 보낸 예외 상황 기록
    exception_draft: ExceptionDraft | None = None  # D-543: 「노하우 등록」 초안(마지막 확정 뒤)
    exception_auto: ExceptionAuto | None = None  # 확정 전 칸에 보일 상황 · 결과
    exception_split: ExceptionSplit | None = None  # D-556 「AI 나눔」 마지막 결과(확정 전)
    exception_session: bool = False  # 고침A 18쪽: 예외 진행을 거친 세션만 팝업 · 예외 구역
    exception_applied: ExceptionApplied | None = None  # 고침A 18 · 19쪽: 「보고서에 적용」한 것(확정 전)
    low_evidence: bool


class Correction(_Out):
    section: str
    title: str
    at: str | None
    before: str | None
    after: str | None


class LockInstruction(_Out):
    section: str
    title: str
    grade: str | None
    minutes: int | None
    steps: list[str]


class Unable(_Out):
    section: str
    title: str
    count: int


class Instruction(_Out):
    section: str
    title: str
    text: str
    checklist: list[str]
    checklist_done: bool
    checklist_common: bool
    grade: str | None
    minutes: int | None
    steps: list[str]
    fallback: bool
    fallback_label: str | None
    original: str | None
    criterion: str | None
    check_no: int
    check_max: int
    evidence_record_ids: list[str]
    unable: list[Unable]


class PreviewPick(_Out):
    section: str
    title: str | None
    grade: str | None


class PickedBy(_Out):
    """D-652 12-f Pack reason_facts.picked_by — 옵션 check_first_<증상군>이 첫 점검을 바꾼 판정."""

    option: str
    section: str
    title: str
    instead_of: str
    instead_title: str


class Preview(_Out):
    kind: str
    section: str | None
    grade: str | None
    cause: str | None
    title: str | None = None
    minutes: int | None = None
    call_reason: str | None = None
    reason: str | None = None
    reason_fallback: bool | None = None
    reason_label: str | None = None
    candidates: list[SwitchCandidate] = []  # D-585 진단 카드 [점검 바꾸기] 창 후보
    picked: PreviewPick | None = None  # D-585 진단에서 고른 점검
    picked_by: PickedBy | None = Field(default=None, exclude_if=lambda v: v is None)  # D-652 12-f 옵션이 바꾼 첫 점검


class SessionView(_Out):
    session_id: int
    alarm_id: int
    status: str
    locked: bool
    lock_at: str | None
    alarm_ack_at: str | None = None  # EV-007: 알람 화면 확인 시각(없으면 잠금 거절)
    probabilities: dict[str, int]
    evidence: dict[str, int]
    equipment_counts: dict[str, int] | None = None  # 리허설 2 R3 · R6: 확률 셈의 이 설비 원인별 k건(Pack 출력)
    equipment_total: int | None = None  # 같은 셈의 이 설비 기록 n건
    count_scope: str | None = None  # equipment · group(이 설비 기록이 적거나 같은 증상 전체 셈)
    count_prior: int | None = None  # 원인마다 기본 건수(확률 = (k + prior) ÷ (n + prior × 원인 수))
    low_evidence: bool
    top_records: list[str]
    similar_basis: str
    cause_names: dict[str, str]
    similar_cards: list[RecordCard]
    top_cards: list[RecordCard] = []  # D-663 S1: 진단 근거 칸 = 1위 후보로 확정된 지난 같은 알람 기록
    alarm: AlarmBlock
    results: list[ResultRow]
    step: Step
    last_seq: int
    recommend_handover: bool
    lock_instruction: LockInstruction | None = None
    instruction: Instruction | None = None
    path: list[PathEntry] = []
    action: ActionBlock | None = None
    restart: RestartBlock | None = None
    monitor: MonitorBlock | None = None
    call: CallBlock | None = None
    report: ReportBlock | None = None
    correction: Correction | None = None
    exception_draft: ExceptionDraft | None = None  # D-543 「노하우 등록」 초안
    exception_why: str | None = None  # D-580 '왜 이 예외' 한 줄(Pack matched)
    exception_miss: str | None = None  # D-637 F11 '왜 예외가 아닌가' 한 줄(등록 예외의 최근 정비 절을 이 기기는 안 함)
    exception_notes: list[ExceptionNote] = []  # D-543 참고 알림(같은 설비 · 같은 증상군 승인 예외 기록)
    switch_candidates: list[SwitchCandidate] = []  # D-556 [점검 바꾸기] 지금 추천 후보(점검 안내 중만)
    exception_topic: str | None = None  # 고침A '예외상황-{주제}' — 이 세션 예외 안내(v8)의 주제(없으면 None)
    exception_walk: list[ExceptionWalkStep] | None = None  # 약속 v10 걸음 길 안내 차례(없으면 None)


class SessionWithPreview(SessionView):
    preview: Preview


# ---------------------------------------------------------------- 과거 기록(T13)


class Approval(_Passthrough):
    status: str
    by: str | None = None
    at: str | None = None


class RecordStepOut(_Passthrough):
    kind: str
    name: str | None = None
    section: str | None = None
    result: str | None = None


class Record(_Out):
    record_id: str
    equipment_id: str
    equipment_type: str | None
    approval: Approval | None
    source: str
    provenance: str
    occurred_at: str
    symptom: dict
    alarm: dict
    sensor_snapshot: dict[str, int | float]
    steps: list[RecordStepOut]
    outcome: dict
    downtime_min: int | float
    narrative: str


# ---------------------------------------------------------------- 파트장 앱(L02 ~ L06, P-11)


class LeadTiles(_Out):
    awaiting: int
    calls_open: int
    violations: int


class LeadReportRow(_Out):
    report_id: int
    session_id: int
    equipment_id: str
    group_name: str
    cause: str
    line: str = ""  # D-642 R2: 한 줄(예외면 '예외상황 · 점검 → 조치', 아니면 원인 이름)
    author_name: str
    confirmed_at: str | None
    evidence_count: int
    low_evidence: bool
    violations: int


class LeadActedRow(_Out):
    """약속 v9 lead_notice acted_without_call: 정비자가 [예외] 바로 조치를 고른 세션 한 줄."""

    session_id: int
    equipment_id: str
    group_name: str
    tech_name: str | None
    at: str


class LeadOverview(_Out):
    now: str
    tiles: LeadTiles
    rows: list[LeadReportRow]
    acted: list[LeadActedRow] = []  # 약속 v9 '호출 없이 조치함'(최근 순)
    prechecks: list[PrecheckCard] = []  # D-642 E '사전 점검 후보' 칸(모든 설비 · 담당자 · 10초 조회마다 다시 셈)


class LeadField(_Out):
    key: str
    label: str
    text: str


class LeadReport(_Out):
    report_id: int
    session_id: int
    status: str
    equipment_id: str
    group_name: str
    occurred_at: str | None
    author_name: str
    low_evidence: bool
    fields: list[LeadField]
    violations: int


class FinalDecision(_Out):
    kind: str
    by_name: str
    at: str | None
    memo: str | None = None


class FinalSensor(_Out):
    name: str
    value_text: str
    range_text: str
    status: str
    out_of_range: bool


class FinalCause(_Out):
    name: str
    probability: int
    evidence: int
    result: str
    confirmed: bool


class FinalCauseBasis(_Out):
    section: str
    title: str
    value: str
    memo: str | None


class FinalStep(_Out):
    at: str
    who: str
    text: str
    section: str | None
    grade: str | None


class FinalReport(_Out):
    """보고서 최종본(D-528 · 부록 5): 구역 1 ~ 5 값 — 두 앱이 같은 문서 화면(FinalReport.tsx)으로 그린다."""

    report_id: int
    session_id: int
    status: str
    equipment_id: str
    equipment_type: str
    group_name: str
    occurred_at: str
    confirmed_at: str | None
    author_name: str
    author_role: str  # 작성 근무(가이드 20쪽 '(야간 정비)')
    lock_at: str | None
    rejected_before: bool
    decision: FinalDecision | None
    sensors: list[FinalSensor]
    sensor_evidence: str
    causes: list[FinalCause]
    cause: str
    cause_basis: FinalCauseBasis | None  # 확정 근거(가이드 20쪽) — 확정 원인 없으면 없음
    check_path: str
    evidence_records: str
    timeline: list[FinalStep]
    action: str
    restart: str | None
    downtime: str
    grade_use: dict[str, int]
    approved_calls: list[str] = []  # 4 조치 결과 '파트장 승인'(승인 후 진행 호출 · D-546 덧 34)
    unresolved: str
    exception_record: ExceptionRecord | None  # D-538: 6. 예외 상황 기록(보기만)


class ExceptionRecordItem(_Out):
    record_id: str
    report_id: int
    approved_at: str | None
    exception_record: ExceptionRecord


class HumanCheck(_Out):
    section: str
    from_section: str | None


class CheckRecord(_Out):
    """약속 v9 3절 기록 줄(앱 → LLMOps): 헛걸음 · 놓침 · 새 예외 + 사람이 고른 점검."""

    kind: str  # wasted_step | missed | new_exception | exception_not_picked(D-633)
    session_id: int
    human_check: HumanCheck | None
    relation_id: str | None = None  # 놓침이면 같다고 한 항목(예외 · 매뉴얼 절 번호) · 안 고름이면 보인 예외 관계 id
    at_seq: int
    how: str | None = None  # D-633 안 고름의 길: switched | followed
    run_id: str | None = None  # D-633 예외 안내가 처음 실린 Run(start)


class CheckRecordList(_Out):
    items: list[CheckRecord]


class ExceptionRecordList(_Out):
    """D-538: 승인된 예외 상황 기록(정비 이력 원본 층) — LLMOps ⑥이 읽음."""

    items: list[ExceptionRecordItem]


class EditCheckField(_Out):
    key: str
    ok: bool
    unsupported_numbers: list[str]


class EditCheck(_Out):
    fields: list[EditCheckField]
    ok: bool
    messages: list[str]


class DecisionResult(_Out):
    status: str
    history_record_id: str | None = None


class LeadCallRow(_Out):
    call_id: int
    kind: str
    equipment_id: str
    group_name: str
    sent_at: str | None
    need: str
    sender_name: str


class LeadCalls(_Out):
    now: str
    rows: list[LeadCallRow]


class LeadSessionHold(_Out):
    kind: str
    label: str
    since: str | None
    minutes: int | None
    call_id: int | None


class LeadSessionRow(_Out):
    session_id: int
    alarm_id: int
    equipment_id: str
    group_code: str
    group_name: str
    technician_name: str
    stage: str
    stage_label: str
    step_no: int | None
    step_kind: str | None
    section: str | None
    section_title: str | None
    grade: str | None
    check_no: int
    check_max: int
    hold: LeadSessionHold | None
    alarm_at: str | None
    started_at: str | None
    last_input_at: str | None
    elapsed_min: int
    report_rejected: bool


class LeadSessions(_Out):
    now: str
    counts: dict[str, int]
    rows: list[LeadSessionRow]


class AnswerOption(_Out):
    value: str
    label: str
    hint: str


class LeadCall(_Out):
    call_id: int
    kind: str
    equipment_id: str
    group_name: str
    sent_at: str | None
    section: str | None
    grade: str | None
    summary: str
    sender_name: str | None = None
    options: list[AnswerOption]
    answered: bool
    answer: dict | None
    need: str
    corrections: list["CallCorrectionBox"] = []


class CallCorrectionBox(_Out):
    """L04 / call_corrected '보낸 뒤 정비자 정정' 상자 한 줄."""

    at: str | None
    section: str | None
    title: str | None
    before: dict | None
    after: dict | None
    reason: str | None


class AnswerResult(_Out):
    status: str
    answer: str


class GradeRow(_Out):
    section: str
    title: str
    grade: str
    label: str


class GradeChangeRow(_Out):
    section: str
    title: str
    before: str
    after: str
    by_name: str
    at: str | None


class LeadGrades(_Out):
    now: str
    total: int
    rows: list[GradeRow]
    changes: list[GradeChangeRow]


class GradeWarning(_Out):
    direction: str
    head: str
    body: str


class GradeResult(_Out):
    section: str
    before: str
    after: str


class AuditRow(_Out):
    audit_id: int
    at: str
    user_id: str | None
    user_name: str
    app: str
    action: str
    target: str
    equipment_id: str | None
    report_id: int | None = None  # D-528 승인 대기 '오늘 기록' 「보고서 보기」(보고서 확정 · 결정 행)


class AuditUser(_Out):
    user_id: str
    name: str


class AuditOptions(_Out):
    equipment: list[str]
    users: list[AuditUser]


class AuditFilters(_Out):
    equipment: str | None
    user_id: str | None


class LeadAudit(_Out):
    now: str
    date: str
    filters: AuditFilters
    options: AuditOptions
    rows: list[AuditRow]


# ---------------------------------------------------------------- S-05(P-14): 세션 단계 기록(T15 지난 화면)


class PastCorrection(_Out):
    at: str | None
    before: dict | None
    after: dict | None
    reason: str | None


class PastNote(_Out):
    at: str | None
    memo: str | None


class PastBox(_Out):
    """보낸 뒤 정비자 정정(호출 기록에 붙은 것)."""

    at: str | None
    section: str | None
    title: str | None
    before: dict | None
    after: dict | None
    reason: str | None


class PastRestart(_Out):
    section: str
    title: str
    grade: str | None
    at: str | None
    checklist: list[str]


class PastRecord(_Out):
    """지난 화면 한 장: 기록 종류와 그때 화면 값.

    종류 = alarm · diagnosis · lock · precheck · check · call · action · restart · monitor_close.
    정정 · 메모 덧붙이기 가능 여부 · 정정 이력 · 덧붙인 메모 · 무효는 서버가 정한다.
    """

    seq: int
    kind: str
    at: str | None
    band: str
    position: str
    void: bool
    correctable: bool
    notable: bool
    corrections: list[PastCorrection]
    notes: list[PastNote]
    section: str | None = None
    title: str | None = None
    grade: str | None = None
    minutes: int | None = None
    steps: list[str] | None = None
    record: str | None = None
    values: str | None = None
    lock_at: str | None = None
    criterion: str | None = None
    evidence_record_ids: list[str] | None = None
    check_no: int | None = None
    check_max: int | None = None
    value: str | None = None
    memo: str | None = None
    b_checklist: list[str] | None = None
    b_checklist_common: bool | None = None
    call_kind: str | None = None
    summary: str | None = None
    box: list[PastBox] | None = None
    cause_name: str | None = None
    restart: PastRestart | None = None
    checklist: list[str] | None = None
    alarm: AlarmBlock | None = None
    probabilities: dict[str, int] | None = None
    evidence: dict[str, int] | None = None
    equipment_counts: dict[str, int] | None = None  # 리허설 2 R3 · R6: 그때 순위 줄 n건 중 k건(없으면 숨김)
    equipment_total: int | None = None
    count_scope: str | None = None
    count_prior: int | None = None
    cause_names: dict[str, str] | None = None
    similar_cards: list[RecordCard] | None = None
    top_cards: list[RecordCard] | None = None  # D-663 S1: 그때 1위 후보로 확정된 기록
    low_evidence: bool | None = None
    next: Preview | None = None


class PastStep(_Out):
    no: int
    name: str
    records: list[PastRecord]


class SessionPast(_Out):
    session_id: int
    closed: bool
    closed_at: str | None
    closed_note: str | None
    steps: list[PastStep]
