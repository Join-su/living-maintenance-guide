"""사람 체크 기록(EV-006 · D-408 · D-412): 파트장 마지막 결정 뒤 그 세션의 문서 한 장(`c1a15.check-record.v1`).

- 앱 DB만 읽어 만든다(정정 · 메모 덧붙임을 적용한 업무 기록 — sessions.events_of). 새 입력 · 화면 · 표 0.
- 확정 원인은 승인 이력과 같은 셈(lead.history_record). 평가 모드 세션은 만들지 않는다.
- 대상 Run(`steps[].opened_by_run` · `signals[].runs`): 앱은 입력마다 judge Run을 다시 돌려 같은 단계를 다시 연다
  → **이어 열린 묶음의 첫 Run**. 묶음 열쇠 = 점검 · 조치 · 호출 결정은 그 절, 그 밖은 kind.
  무효(void) step_opened는 뺀다.
- 신호 다섯(`check-signals-v1`)은 힌트 — 트리거 결정은 LLMOps. 신호 0 = 해결됨.
- 보내는 문서(`for_send`)는 메모 원문을 빼고 `has_memo`만(D-412 결정 3). 원문은 앱 DB · CLI 찍기에만.
- 보내기(`C1A15_CHECKS=deploy`): 결정 commit 뒤 한 번 → 배포 서버 `POST /runs/{report Run}/feedback`
  `{"kind": "check_record", "record": <문서>}` · 같은 키 = record_key · 실패해도 승인 그대로(감사만).
"""

from sqlalchemy import select
from sqlalchemy.engine import Connection

from c1a15 import config
from c1a15.api import deps, flow, sessions
from c1a15.db import models as m
from c1a15.deploy import runs as deploy_runs

from .model import CheckRecordV1

RULE = "check-signals-v1"
SEND_TIMEOUT = 10.0  # 보내기 상한(초) — 승인 응답은 commit 뒤라 기다리지 않음
MEMO_MAX = 500
WORK_KINDS = {"instruct", "instruct_after_checklist", "action", "action_after_checklist", "call"}
FIRST_INSTRUCTION_KINDS = {"instruct", "instruct_after_checklist", "call"}
ANSWER = {"approved": "승인 후 진행", "daytime": "주간 처리", "direct": "직접 확인", "assign": "주간 숙련자 배정"}


def record_key(report_id: int) -> str:
    return f"CR-{report_id:06d}"


def _key(dec: dict) -> tuple:
    """묶음 열쇠: 점검 · 조치 · 호출 = 절(C등급 '호출 → 승인 → 점검'이 한 묶음), 그 밖 = kind(+절)."""
    if dec.get("kind") in WORK_KINDS:
        return ("work", dec.get("section"))
    return (dec.get("kind"), dec.get("section"))


def _top(probs: dict | None) -> list[dict]:
    items = sorted((probs or {}).items(), key=lambda x: (-x[1], x[0]))
    return [{"cause": k, "p": int(v)} for k, v in items[:3]]


def _step(e: dict, dec: dict) -> tuple[str, str | None, str] | None:
    """업무 기록 한 줄 → (steps kind, 절, 결과). 체크와 무관한 줄은 None."""
    t = e["type"]
    if t == "lock":
        return "lock", "1.2", "완료"
    if t == "precheck_done":
        return "precheck", dec.get("section"), "완료"
    if t == "result":
        return "check", e.get("section"), e.get("value")
    if t == "confirm_answer":
        return "confirm", dec.get("section"), "예" if e.get("yes") else "아니오"
    if t == "out_of_scope_choice":
        return (
            "out_of_scope",
            dec.get("section"),
            {"handover": "인계", "continue": "계속"}.get(e.get("choice"), e.get("choice")),
        )
    if t == "call_sent":
        return ("handover" if e.get("kind") == "handover" else "call"), e.get("section"), "보냄"
    if t == "call_wait":
        return "call", e.get("section"), "대기"
    if t == "call_answer":
        return (
            ("handover" if dec.get("kind") == "handover" else "call"),
            e.get("section"),
            ANSWER.get(e.get("answer"), e.get("answer")),
        )
    if t == "action_done":
        return "action", e.get("section"), "완료"
    if t == "restart":
        return "restart", "6.1", "재가동"
    if t == "restart_result":
        return "restart", "6.1", "경계 이내" if e.get("outcome") == "within" else "여전히 밖"
    if t == "monitor_close":
        return "monitor", "6.2", "완료"
    if t == "handover_request":
        return "handover", dec.get("section"), "요청"
    return None


def _walk(events: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """업무 기록을 차례로 읽어 (steps, 무효 아닌 step_opened, 단계별 결정 이유)를 낸다 — 대상 Run 규칙 한 곳."""
    steps, opened, reasons = [], [], {}
    dec, key, first_run = {}, None, None
    for e in events:
        if e["type"] == "step_opened":
            if e.get("void"):
                continue
            dec = e.get("decision") or {}
            opened.append(e)
            if _key(dec) != key:
                key, first_run = _key(dec), e.get("run_id")
            if dec.get("reason"):
                reasons[first_run] = dec["reason"]
            continue
        s = _step(e, dec)
        if s is None:
            continue
        kind, section, result = s
        memo = (e.get("memo") or None) if kind == "check" else None
        steps.append(
            {
                "seq": e["seq"],
                "at": e["_at"],
                "kind": kind,
                "section": section,
                "grade": dec.get("grade"),
                "opened_by_run": first_run,
                "recommended": True,
                "result": result,
                "judged": e.get("judged_value", e.get("value")) if kind == "check" else None,
                "memo": memo[:MEMO_MAX] if memo else None,
                "has_memo": bool(memo or e.get("notes")),
                "corrected": (e.get("corrected_from") or {}).get("count", 0),
                "void": bool(e.get("void")),
            }
        )
    return steps, opened, reasons


def _signals(
    steps: list[dict], outcome: dict, first_top: list[dict], runs: dict, lead: dict, reasons: dict
) -> list[dict]:
    live = [s for s in steps if not s["void"]]
    out = []
    if outcome["type"] == "원인 확정":  # S1 헛걸음 — 원인이 확정된 세션만(감시 · 인계에서 정상 점검은 헛걸음 아님)
        confirmed_yes = {s["section"] for s in live if s["kind"] == "confirm" and s["result"] == "예"}
        wasted = [
            s for s in live if s["kind"] == "check" and s["judged"] == "정상" and s["section"] not in confirmed_yes
        ]
        if wasted:
            out.append(
                {
                    "id": "S1",
                    "rule": RULE,
                    "sections": [s["section"] for s in wasted],
                    "runs": [s["opened_by_run"] for s in wasted],
                }
            )
    for i, s in enumerate(live):  # S2 조치 미해결 — 조치 뒤 재가동 '여전히 밖'
        if s["kind"] == "action" and any(x["kind"] == "restart" and x["result"] == "여전히 밖" for x in live[i + 1 :]):
            out.append({"id": "S2", "rule": RULE, "sections": [s["section"]], "runs": [s["opened_by_run"]]})
            break
    if outcome["cause_code"] and first_top and first_top[0]["cause"] != outcome["cause_code"]:
        out.append(
            {
                "id": "S3",
                "rule": RULE,
                "first": first_top[0]["cause"],
                "final": outcome["cause_code"],
                "runs": [runs["start"]],
            }
        )
    cause_edit = next((x for x in lead["edits"] if x["field"] == "cause"), None)
    if cause_edit or lead["rejects"]:
        out.append(
            {
                "id": "S4",
                "rule": RULE,
                "runs": [runs["report"]],
                "before": cause_edit["before"] if cause_edit else None,
                "after": cause_edit["after"] if cause_edit else None,
                "reasons": [x["reason"] for x in lead["rejects"] if x["reason"]],
            }
        )
    s5 = [
        s for s in live if s["kind"] == "out_of_scope" or (s["kind"] == "handover" and s["result"] in {"보냄", "요청"})
    ]
    if s5:
        basis = [
            "out_of_scope" if s["kind"] == "out_of_scope" else f"handover:{reasons.get(s['opened_by_run'], 'unknown')}"
            for s in s5
        ]
        out.append({"id": "S5", "rule": RULE, "basis": basis, "runs": [s["opened_by_run"] for s in s5]})
    return out


def build(c: Connection, report_id: int) -> dict | None:
    """승인된 보고서 하나 → 체크 기록 v1(dict). 평가 모드면 None. 승인 전이면 ValueError."""
    from c1a15.api import lead as lead_api  # lead가 이 모듈을 부르므로 늦게 가져옴

    r = lead_api._report(c, report_id)
    if r.eval_mode:
        return None
    if r.status != "approved" or not r.approval:
        raise ValueError(f"승인된 보고서가 아님: {report_id} ({r.status})")
    events = sessions.events_of(c, r.session_id)
    row = sessions._row(c, r.session_id)
    alarm = c.execute(select(m.Alarm.__table__).where(m.Alarm.alarm_id == r.alarm_id)).one()
    approval = r.approval
    rec = lead_api.history_record(c, r, [dict(f) for f in r.fields], approval)
    fields = {f["key"]: f["text"] for f in r.fields}
    steps, opened, reasons = _walk(events)
    live = [s for s in steps if not s["void"]]
    report_runs = [e.get("run_id") for e in events if e["type"] == "report_written" and e.get("run_id")]
    runs = {
        "deployment_id": row.deployment_id,
        "start": row.start_run_id,
        "judge": list(
            dict.fromkeys(e.get("run_id") for e in opened if e.get("run_id") and e.get("run_id") != row.start_run_id)
        ),
        "alarm": alarm.run_id,
        "report": report_runs[-1] if report_runs else None,
    }
    preview = next((e for e in events if e["type"] == "preview_shown"), {})
    first_opened = opened[0] if opened else {}
    first_top = _top(preview.get("probabilities"))
    instr = next((e for e in opened if (e.get("decision") or {}).get("kind") in FIRST_INSTRUCTION_KINDS), None)
    restarts = [s["result"] for s in live if s["kind"] == "restart" and s["result"] != "재가동"]
    actions = [s["section"] for s in live if s["kind"] == "action"]
    outcome = {
        "type": rec["outcome"]["type"],
        "cause_code": rec["outcome"]["cause_code"],
        "cause_text": fields.get("cause"),
        "action_section": actions[-1] if actions else None,
        "restart": ({"경계 이내": "within", "여전히 밖": "outside"}[restarts[-1]]) if restarts else None,
    }
    rejects = [
        {"at": e.get("at"), "reason": e.get("reason")}
        for e in events
        if e["type"] == "report_rejected" and not e.get("void")
    ]
    edits = [{"field": x["field"], "before": x["before"], "after": x["after"]} for x in approval.get("edits") or []]
    lead = {
        "decision": approval["mode"],
        "edits": edits,
        "cause_text_changed": any(x["field"] == "cause" for x in edits),
        "rejects": rejects,
        "at": approval["at"],
    }
    field_truth = None
    if outcome["cause_code"]:
        k = deps.manual()
        cause_of = {ch.section_id: ch.cause for ch in k.checks.get(r.symptom_group, [])}
        yes = {s["section"] for s in live if s["kind"] == "confirm" and s["result"] == "예"}
        found = [
            s["section"]
            for s in live
            if s["kind"] == "check"
            and (s["judged"] == "이상" or s["section"] in yes)
            and cause_of.get(s["section"]) == outcome["cause_code"]
        ]
        field_truth = {
            "query": {"symptom_group": r.symptom_group, "sensor_snapshot": deploy_runs.sensors5(alarm.sensor_snapshot)},
            "confirmed_sections": list(dict.fromkeys(found)),
            "confirmed_cause": outcome["cause_code"],
        }
    doc = {
        "schema": "c1a15.check-record.v1",
        "record_key": record_key(report_id),
        "made_at": approval["at"],
        "session": {
            "pack_session_id": deploy_runs.session_ident(r.alarm_id),
            "alarm_id": r.alarm_id,
            "equipment_id": r.equipment_id,
            "symptom_group": r.symptom_group,
            "alarm_source": "ml_predicted" if alarm.ml else "boundary",
            "scenario_id": alarm.scenario_id,
            "occurred_at": alarm.occurred_at.isoformat(),
            "closed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        },
        "runs": runs,
        "first_decision": {
            "run_id": row.start_run_id,
            "top": first_top,
            "pack_top": _top((first_opened.get("decision") or {}).get("probabilities")),
        },
        "first_instruction": (
            {
                "section": instr["decision"].get("section"),
                "kind": instr["decision"]["kind"],
                "run_id": instr.get("run_id"),
            }
            if instr
            else None
        ),
        "steps": steps,
        "outcome": outcome,
        "lead": lead,
        "signals": _signals(steps, outcome, first_top, runs, lead, reasons),
        "field_truth": field_truth,
        "exception_offer": flow.exception_offer(events),  # D-633 예외가 보였는데 골랐나(안 보였으면 null)
    }
    return CheckRecordV1.model_validate(doc).model_dump(by_alias=True)


def for_send(doc: dict) -> dict:
    """보내는 문서: 메모 원문을 빼고 has_memo만(D-412 결정 3)."""
    return {**doc, "steps": [{**s, "memo": None} for s in doc["steps"]]}


def send(doc: dict) -> dict:
    """배포 서버로 한 번(다시 보내기 없음 · 상한 10초). 결과 = {status, transport, run_id}."""
    run_id = doc["runs"]["report"] or doc["runs"]["start"]
    if not run_id:
        return {"status": None, "transport": "no_run", "run_id": None}
    body = {"kind": "check_record", "record": for_send(doc)}
    reply = deploy_runs.client().feedback(run_id, body, doc["record_key"], SEND_TIMEOUT)
    return {"status": reply.status, "transport": reply.transport, "run_id": run_id}


def queue_after_decision(c: Connection, report_id: int) -> None:
    """파트장 마지막 결정(승인) 뒤: C1A15_CHECKS=deploy면 commit 뒤 한 번 보냄 — 실패는 감사만(승인 그대로)."""
    if config.checks() != "deploy":
        return
    target = f"report:{report_id}"
    try:
        doc = build(c, report_id)
    except Exception as e:  # noqa: BLE001 — 체크 기록 실패는 승인과 따로(PR-3)
        err = type(e).__name__
        c.info["after_commit"].append(
            lambda: sessions.audit_rejection("system", "check_record_failed", target, {"error": err})
        )
        return
    if doc is None:  # 평가 모드
        return

    def deliver() -> None:
        try:
            res = send(doc)
        except Exception as e:  # noqa: BLE001
            res = {"status": None, "transport": type(e).__name__, "run_id": None}
        ok = res["status"] in (200, 201)
        detail = {"record_key": doc["record_key"], **res, "signals": [s["id"] for s in doc["signals"]]}
        sessions.audit_rejection("system", "check_record_sent" if ok else "check_record_failed", target, detail)

    c.info["after_commit"].append(deliver)
