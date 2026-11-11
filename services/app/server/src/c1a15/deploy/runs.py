"""앱 → Pack Run 입력 만들기(EV-001 ES-07 · 08 · 10): 정본 = c1a15-pack 약속 v2(+ README 읽는 법) · v3(kind report).

- session_id = `S-A<alarm_id>`(알람 하나 = 세션 하나 — 실패로 되돌린 뒤 다시 보내도 같은 값, R6-1)
- start.event_id = 세션을 연 알람의 event_id, 기본 길로 등록돼 없으면 `C1A-A<alarm_id>`(R6-2)
- 업무 기록은 판정이 쓰는 칸만(사용자 ID · 표시 이름 · 메모 원문 · 정정 사유 0, VC-35)
부르기는 deploy.client 한 곳(키 · 재시도 · 감사).
"""

import secrets
import time
from datetime import UTC
from functools import lru_cache

from c1a15 import config, redact
from c1a15.deploy.client import Client, RunResult
from c1a15.deploy.ledger import DbLedger

SENSORS5 = ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min")
EVENT_KEYS = ("seq", "type", "void", "section", "value", "judged_value", "yes", "outcome", "corrected")
EVENT_KEYS += ("kind", "answer", "mode", "choice", "from_section")  # from_section = 약속 v9 switch_check
EVENT_KEYS += ("step",)  # 약속 v10 5절 예외 안내 걸음(result · action_done만 · 없으면 칸 0)
SIGNAL_KEYS = ("out_of_scope_finding", "conflicts_with_button", "interpretation", "quote")
HANDOVER_REASONS = {"llm_failure", "out_of_scope_finding"}  # 정본 event.reason enum — 자유 문장 사유는 넣지 않음


@lru_cache
def settings() -> dict:
    return config.pack_settings()


@lru_cache
def client() -> Client:
    return Client(settings()["deploy_url"], DbLedger(), host_name=settings()["deploy_host"])


_REVISION = {"at": None, "value": None}
REVISION_KEEP = 5.0  # 홈이 5초마다 불림 — /info는 그동안 한 번(D-619)


def revision_now(fresh: bool = False, clock=time.monotonic) -> str | None:
    """배포 서버 지금 revision · 못 읽으면 마지막 값(전환 중 끊김 2초에 카드가 깜빡이지 않게)."""
    t = clock()
    if fresh or _REVISION["at"] is None or t - _REVISION["at"] >= REVISION_KEEP:
        _REVISION["at"] = t
        got = client().info()
        if fresh:
            return got
        _REVISION["value"] = got or _REVISION["value"]
    return _REVISION["value"]


def session_ident(alarm_id: int) -> str:
    return f"S-A{alarm_id}"


def start_event_id(alarm) -> str:
    return alarm.event_id or f"C1A-A{alarm.alarm_id}"


def sensors5(snapshot: dict) -> dict:
    return {k: snapshot[k] for k in SENSORS5}


def similar_body(similar: list[dict]) -> list[dict]:
    return [{"record_id": s["record_id"], "cause": s.get("cause"), "score": s["score"]} for s in similar]


def _signal_fields(sig: dict) -> dict:
    return {k: sig[k] for k in SIGNAL_KEYS if k in sig}


def judge_events(inputs: list[dict]) -> list[dict]:
    """정정 · 무효가 반영된 논리 기록(INPUT_TYPES만)에서 판정이 쓰는 칸만."""
    out = []
    for e in inputs:
        ev = {k: e[k] for k in EVENT_KEYS if k in e}
        if isinstance(e.get("signal"), dict):
            ev["signal"] = redact.redact_obj(_signal_fields(e["signal"]))[0]  # 메모 인용 속 이름(001-63)
        if e["type"] == "handover_request" and e.get("reason") in HANDOVER_REASONS:
            ev["reason"] = e["reason"]
        out.append(ev)
    return out


@lru_cache
def source_release() -> str:
    """시연 · 평가 행의 출처 = AI4I 원본 sha256(data/manifest.json)."""
    import json
    import os
    from pathlib import Path

    manifest = json.loads((Path(os.environ["DATA_DIR"]) / "manifest.json").read_text(encoding="utf-8"))
    return f"ai4i-2020@sha256:{manifest['source']['sha256']}"


def new_event_id() -> str:
    """알람 Run의 사건 id — 알람 행이 생기기 전에 만든다(ML 길은 Run 결과를 보고 알람을 만들므로)."""
    return "C1A-E" + secrets.token_hex(6)


def alarm_body(
    event_id: str,
    equipment_id: str,
    snapshot: dict,
    source_mode: str,
    lineage: dict,
    product_type: str | None,
    threshold: float | None,
) -> dict:
    card = {
        "event_id": event_id,
        "source_mode": source_mode,
        "equipment_id": equipment_id,
        "sensor_snapshot": sensors5(snapshot),  # 계산 칸(출력 · 온도 차)은 Pack 한 곳에서 — 7칸이 와도 5칸만
        "alarm": {"source": "none"},
        "data_lineage": lineage,
    }
    if product_type:
        card["product_type"] = product_type
    body = {"kind": "alarm", "event_card": card}
    if source_mode == "ml_predicted":
        body["settings"] = {"ml_alarm_threshold": threshold}
    return body


def alarm(event_id: str, body: dict) -> RunResult:
    return client().run("alarm", event_id, 0, body)


def alarm_context(alarm) -> dict:
    """약속 v7(D-546 덧 27): 알람 난 설비 · 알람 시각(초까지 · 시간대) — Pack 그래프 '같은 설비' 범위 · 설비 조건 예외 ·
    앞선 기록만. 값이 없으면 칸을 빼고(v6과 같은 판정), line_id는 앱에 값이 없어 싣지 않음."""
    out = {}
    if alarm.equipment_id:
        out["equipment_id"] = alarm.equipment_id
    if alarm.occurred_at:
        at = alarm.occurred_at if alarm.occurred_at.tzinfo else alarm.occurred_at.replace(tzinfo=UTC)
        out["occurred_at"] = at.replace(microsecond=0).isoformat()
    return out


def start_body(alarm, grades: dict, minutes: dict, recent_work: list[dict] | None = None) -> dict:
    """약속 v4: 상위 10건은 Pack 리트리버(retrieve_history)가 고름 — similar 칸은 보내지 않음(EV-001 CH-29).
    recent_work = 약속 v10 덧(D-570 · Pack 5e76c66): 그 설비 30일 안 정비(앱 DB) — 없으면 칸 0(지금 바이트)."""
    return {
        **alarm_context(alarm),
        **({"recent_work": recent_work} if recent_work else {}),
        "kind": "start",
        "session_id": session_ident(alarm.alarm_id),
        "event_id": start_event_id(alarm),
        "group": alarm.symptom_group,
        "query": {
            "direction": None
            if alarm.alarm.get("origin") == "lead"
            else alarm.alarm.get("direction"),  # EV-009: 넘지 않은 경계
            "sensor_snapshot": sensors5(alarm.sensor_snapshot),
        },  # 없으면 null(정본 enum)
        "events": [],
        "grades": grades,
        "minutes": minutes,
    }


def judge_body(
    alarm_id: int,
    group: str,
    similar: list[dict],
    inputs: list[dict],
    grades: dict,
    minutes: dict,
    manual_support: dict | None = None,
    exception_relation: str | None = None,
    context: dict | None = None,
) -> dict:
    body = {
        **(context or {}),  # 약속 v7 equipment_id · occurred_at(alarm_context) — 없으면 지금 바이트
        "kind": "judge",
        "session_id": session_ident(alarm_id),
        "group": group,
        "similar": similar_body(similar),
        "events": judge_events(inputs),
        "grades": grades,
        "minutes": minutes,
    }
    if (
        manual_support is not None
    ):  # 약속 v5(EV-011 CH-07): start가 준 매뉴얼 몫을 그대로 다시 담음 — 없으면 지금 바이트
        body["manual_support"] = manual_support
    if exception_relation is not None:  # 약속 v8(D-545 덧 2): start가 준 예외 관계 id — 없으면 지금 바이트
        body["exception_relation"] = exception_relation
    return body


def start(alarm, grades: dict, minutes: dict, recent_work: list[dict] | None = None) -> RunResult:
    return client().run("start", session_ident(alarm.alarm_id), 0, start_body(alarm, grades, minutes, recent_work))


def audit_names(where: str, target: str, n: int) -> None:
    """거른 이름 수만 감사(원문 · 거른 낱말 0, 001-63) — 요청 트랜잭션과 따로 커밋."""
    if not n:
        return
    from c1a15.api import deps
    from c1a15.db import models as m

    with deps.engine().begin() as c:
        c.execute(
            m.AuditEntry.__table__.insert().values(
                actor="system", action="names_redacted", target=target, detail={"where": where, "count": n}
            )
        )


def judge(
    alarm_id: int,
    group: str,
    similar: list[dict],
    inputs: list[dict],
    grades: dict,
    minutes: dict,
    manual_support: dict | None = None,
    purpose: str | None = None,
    exception_relation: str | None = None,
    context: dict | None = None,
) -> RunResult:
    """purpose = 'trial'이면 정정 확인 창의 시험 판정(EV-011 CH-06) — 머리글 X-Run-Purpose · 키 따로(본문 같음)."""
    body = judge_body(alarm_id, group, similar, inputs, grades, minutes, manual_support, exception_relation, context)
    seq = inputs[-1]["seq"] if inputs else 0  # 그 판정을 부른 마지막 입력 기록
    signals = [_signal_fields(e["signal"]) for e in inputs if isinstance(e.get("signal"), dict)]
    audit_names("judge", session_ident(alarm_id), redact.redact_obj(signals)[1])
    return client().run("judge", session_ident(alarm_id), seq, body, purpose)


def report_body(alarm_id: int, group: str, similar: list[dict], draft: dict, sections: list[str]) -> dict:
    """약속 v3 kind report: 앱 코드 초안 8항목(원인 · 조치 = 원문 대체 글) · 세션에서 열린 · 기록된 절."""
    return {
        "kind": "report",
        "session_id": session_ident(alarm_id),
        "group": group,
        "similar": similar_body(similar),
        "report_draft": redact.redact_obj(dict(draft))[0],  # 소견 줄 속 이름(001-63) — 기록 ID · 절 · 수치는 그대로
        "allowed_sections": sorted(sections),
    }


def report(alarm_id: int, group: str, similar: list[dict], draft: dict, sections: list[str], seq: int) -> RunResult:
    """seq = 보고서 단계를 연 기록 — 초안이 바뀌면 본문 hash가 달라 기본 키도 달라짐(CH-20 초안 열쇠)."""
    audit_names("report", session_ident(alarm_id), redact.redact_obj(dict(draft))[1])
    return client().run("report", session_ident(alarm_id), seq, report_body(alarm_id, group, similar, draft, sections))


def split_body(alarm, group: str, text: str) -> dict:
    """약속 v10 2절 Pack run kind exception_split: 정비자 예외 글 하나 →
    예외 하나(주제 + 점검 목록 + 조치 목록).
    요청 = {kind, session_id, group, text}만(설비 · 시각 칸은 Pack이 거절). 글 속 이름은 거름."""
    return {
        "kind": "exception_split",
        "session_id": session_ident(alarm.alarm_id),
        "group": group,
        "text": redact.redact(text)[0],
    }


def split(alarm, group: str, text: str, seq: int) -> RunResult:
    """seq = 「AI 나눔」을 누른 기록 — 같은 글을 다시 눌러도 새 Run(seq가 다름)."""
    audit_names("exception_split", session_ident(alarm.alarm_id), redact.redact(text)[1])
    return client().run("exception_split", session_ident(alarm.alarm_id), seq, split_body(alarm, group, text))


def exception_similar_body(alarm, group: str, exception: dict) -> dict:
    """약속 v10 3절 kind exception_similar: AI 나눔 예외 하나(주제 · 점검 · 조치) → 통째로 비슷한 것 상위 3."""
    return {
        "kind": "exception_similar",
        "session_id": session_ident(alarm.alarm_id),
        "group": group,
        "exception": exception,
    }


def exception_similar(alarm, group: str, exception: dict, seq: int) -> RunResult:
    """seq = 「AI 나눔」을 누른 기록(나눔 Run과 같은 seq · kind가 달라 키가 다름)."""
    return client().run(
        "exception_similar", session_ident(alarm.alarm_id), seq, exception_similar_body(alarm, group, exception)
    )
