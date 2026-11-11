"""S-03(P-09, B-04): 조치 · 재가동 · 재가동 후 값 · 감시 마감 · 호출 · 숙련자 인계 · 보고서.

판정(다음 단계)은 engine.session.decide, 문장은 llm adapter(fixture 기본) + R8 근거 검사, 저장은 append-only 업무 기록 +
목록용 투영(ops.calls · ops.reports) + 감사. 화면은 여기서 만든 값만 보인다.
보고서 8항목 = 명세 R10, 계산 세부 = 사용자 결정 015(sources/intent/015.md).
"""

import dataclasses
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import Connection, select, text

from c1a15.api import deps, screen
from c1a15.db import models as m
from c1a15.deploy import runs
from c1a15.deploy.client import PackUnavailable
from c1a15.engine import exception, rules, session, switch
from c1a15.engine.knowledge import ManualKnowledge
from c1a15.llm import adapter_for_mode, grounding
from c1a15.llm.base import AdapterError, Draft, Facts, josa

# 정비자 앱이 보내는 S-03 입력(클라이언트 type) → 업무 기록 type
CLIENT_TYPES = {
    "action_done",
    "restart",
    "restart_result",
    "monitor_close",
    "call_send",
    "call_wait",
    "report_edit",
    "report_confirm",
    "handover_request",
    "exception_draft",  # D-543: 「노하우 등록」 — 판정에 쓰지 않음(접지 않음)
    "exception_split",  # D-556 1단계: 보고서 예외 글 「AI 나눔」 — 판정에 쓰지 않음(접지 않음)
    "exception_apply",  # 정답지 고침A 18쪽: 예외 상황 팝업 「보고서에 적용」 — 판정에 쓰지 않음(접지 않음)
}
NOT_JUDGED = {"exception_draft", "exception_split", "exception_apply"}  # 저장만 · 판정 Run을 다시 부르지 않음
# 판정 엔진이 접는 S-03 업무 기록(파트장 답 call_answer는 S-04 P-11이 쓴다)
ENGINE_TYPES = {
    "action_done",
    "restart",
    "restart_result",
    "monitor_close",
    "call_wait",
    "call_sent",
    "call_answer",
    "report_confirm",
    "handover_request",
    "report_rejected",
}

MEMO_SUMMARY_CHARS = 30  # 결정 015 ②
REPORT_FIELDS = [  # 명세 R10: (키, 항목, 출처 표시)
    ("equipment_alarm", "설비 · 알람", "자동"),
    ("sensor_evidence", "센서 근거", "센서"),
    ("check_path", "점검 경로", "입력"),
    ("cause", "확정 원인", "입력"),
    ("evidence_records", "근거 사례", "기록"),
    ("action", "조치", "매뉴얼"),
    ("downtime", "정지 시간", "자동"),
    ("unresolved", "미확인 · 요청", "입력"),
]
FIELD_KEYS = {k for k, _, _ in REPORT_FIELDS}
OUTCOME_CAUSE = {"monitor": "이상 없음 · 감시", "unknown": "원인 미상", "daytime": "원인 미상", "direct": "원인 미상"}
ANSWER_LABEL = {  # 파트장 답(1.4 · 결정 018): 파트장 호출 3종, 숙련자 인계 2종
    "approved": "승인 후 진행",
    "daytime": "주간 처리 요청",
    "direct": "파트장 직접 확인",
    "assign": "주간 숙련자 배정",
}


def _parse(at: str | None) -> datetime | None:
    return datetime.fromisoformat(at) if at else None


def memo_summary(memo: str | None) -> str:
    memo = (memo or "").strip()
    return memo if len(memo) <= MEMO_SUMMARY_CHARS else memo[:MEMO_SUMMARY_CHARS] + "…"


def checklist_of(k: ManualKnowledge, c: Connection, section: str) -> list[str]:
    """그 절의 시작 전 체크리스트(조치 절은 상위 절 안의 그 조치 블록), 없으면 공통 시작 전 확인(AC-43-C2 · C4)."""
    block, _ = screen._block(k, section)
    match = re.search(r"\*\*시작 전 체크리스트\*\*\s*\n((?:\s*- \[ \] .+\n?)+)", block)
    own = [line.split("] ", 1)[1].strip() for line in match.group(1).splitlines() if "] " in line] if match else []
    return own or [i for (i,) in c.execute(select(m.CommonPrecheckItem.text).order_by(m.CommonPrecheckItem.seq)).all()]


# ---------------------------------------------------------------- 재가동 후 값(R11)


def restart_value(alarm: m.Alarm, table: dict, scenarios: dict, after_fix: bool) -> float:
    """R11: 각본 값, 없으면 경계 안쪽 값(경계 + 여유 10%)."""
    sc = scenarios.get(alarm.scenario_id or "", {})
    v = sc.get("restart_value_after_fix" if after_fix else "restart_value_if_none")
    if v is not None:
        return v
    b, low = alarm.alarm["boundary"], alarm.alarm["direction"] == "경계 아래"
    return round(b * 1.1, 1) if low else round(b * 0.9, 1)


def restart_block(alarm: m.Alarm, after_fix: bool) -> dict:
    al = alarm.alarm
    key = screen.SNAPSHOT_KEY[screen._rule_for(deps.alarm_table(), alarm.symptom_group)["key"]]
    v = restart_value(alarm, deps.alarm_table(), deps.scenarios(), after_fix)
    unit = al["unit"]
    side = "이상" if al["direction"] == "경계 아래" else "이하"
    return {
        "sensor": al["sensor"],
        "value": v,
        "value_text": f"{screen.fmt(key, v)} {unit}",
        "boundary_text": f"{screen.fmt(key, al['boundary'])} {unit}",
        "rule_text": f"경계 {screen.fmt(key, al['boundary'])} {unit} {side}이면 경계 이내",
        "basis": "무부하 3분 운전 뒤",
    }


# ---------------------------------------------------------------- 점검 경로(R-PATH)


def path(events: list[dict], k: ManualKnowledge, step: dict, alarm: m.Alarm) -> list[dict]:
    """시각 · 점 색(ok · bad · void · now) · 단계 이름 · 결과. 정정으로 무효인 기록은 지우지 않고 void로 보인다."""
    out = []
    unable: dict[str, int] = {}
    eg = exception_guide_of(events)
    for e in events:
        at, void = e.get("_at"), bool(e.get("void"))
        t = e["type"]
        if t == "lock":
            out.append(
                {"at": e.get("at") or at, "title": k.sections["1.2"]["title"], "detail": "1.2절", "status": "ok"}
            )
        elif t == "precheck_done":
            sec = next(
                (
                    x["decision"]["section"]
                    for x in events
                    if x["type"] == "step_opened" and x["decision"].get("kind") == "precheck"
                ),
                None,
            )
            if sec:
                out.append({"at": at, "title": k.sections[sec]["title"], "detail": f"{sec}절", "status": "ok"})
        elif t == "result" and e.get("value"):
            sec, val = e["section"], e["value"]
            detail = val
            if val == "확인 불가":
                unable[sec] = unable.get(sec, 0) + 1
                if unable[sec] >= 2:
                    detail = "확인 불가 2회 · 제외"
            cf = e.get("corrected_from")
            if cf:
                when = screen.hhmm_kst(datetime.fromisoformat(cf["at"])) if cf.get("at") else ""
                detail += f" · 정정 {cf.get('count', 1)}회({when}, {cf.get('value')} → {val})"
            elif e.get("memo") and unable.get(sec, 0) < 2:
                detail += f" · {memo_summary(e['memo'])}"
            status = "void" if void else ("ok" if val == "정상" else "bad")
            out.append({"at": at, "title": step_title(e, k, eg), "detail": detail, "status": status})
        elif t == "action_done":
            out.append(
                {
                    "at": at,
                    "title": step_title(e, k, eg),
                    "detail": sec_text(e["section"]),
                    "status": "void" if void else "ok",
                }
            )
        elif t == "restart":
            out.append(
                {
                    "at": at,
                    "title": k.sections[session.RESTART_SECTION]["title"],
                    "detail": "6.1절 · 체크리스트 확인",
                    "status": "ok",
                }
            )
        elif t == "restart_result":
            rv = restart_block(alarm, True)
            label = "경계 이내" if e["outcome"] == "within" else "여전히 밖"
            out.append(
                {
                    "at": at,
                    "title": "재가동 후 값 확인",
                    "detail": f"{rv['value_text']} · {label}",
                    "status": "ok" if e["outcome"] == "within" else "bad",
                }
            )
        elif t == "monitor_close":
            out.append(
                {"at": at, "title": k.sections[session.MONITOR_SECTION]["title"], "detail": "6.2절", "status": "ok"}
            )
    now = _now_entry(step, k, eg)
    if now:
        out.append(now)
    return out


def sec_text(sec: str) -> str:
    """경로 글의 절 표시 — 매뉴얼 절은 '4.3절', 예외 절(약속 v8 EX-…)은 번호 그대로(매뉴얼 절이 아님),
    약속 v9 예외 진행 절(EX-NEW)은 '예외'(정답지 묶음 ① 17쪽)."""
    if sec == switch.NEW:
        return "예외"
    return sec if sec.startswith(exception.PREFIX) else f"{sec}절"


def _now_entry(step: dict, k: ManualKnowledge, eg: dict | None = None) -> dict | None:
    kind = step.get("kind")
    if kind in {"action", "action_after_checklist"}:
        return {
            "at": None,
            "title": step_title(step, k, eg),
            "detail": sec_text(step["section"]),
            "status": "now",
        }
    if kind == "restart_check":
        return {
            "at": None,
            "title": k.sections[session.RESTART_SECTION]["title"],
            "detail": "체크리스트 확인 중",
            "status": "now",
        }
    if kind == "restart_value":
        return {"at": None, "title": "재가동 후 값 확인", "detail": "", "status": "now"}
    return None


# ---------------------------------------------------------------- 호출 · 인계 요약(R-CALL · R-STOP, 1.4)


def checks_done(events: list[dict], k: ManualKnowledge) -> list[dict]:
    """지금까지 점검(결과가 있는 점검 · 정정으로 무효 제외).

    점검마다 한 줄 = 마지막 결과, 확인 불가 반복은 '확인 불가 n회'.
    """
    last: dict[tuple, str] = {}
    notes: dict[tuple, str | None] = {}
    unable: dict[tuple, int] = {}
    rows: dict[tuple, dict] = {}
    eg = exception_guide_of(events)
    for e in events:
        if e["type"] == "result" and e.get("value") and not e.get("void"):
            key = (e["section"], e.get("step"))  # 약속 v10 걸음마다 한 줄(같은 예외 절 c1 · c2)
            rows[key] = e
            last[key] = e["value"]
            cf = e.get("corrected_from") or {}
            changed = cf and cf.get("value") != e["value"]
            notes[key] = f"정정 {cf.get('count', 1)}회, 원래 {cf.get('value')}" if changed else None
            if e["value"] == "확인 불가":
                unable[key] = unable.get(key, 0) + 1
    return [
        {
            "section": key[0],
            "title": step_title(rows[key], k, eg),
            "value": f"확인 불가 {unable[key]}회" if v == "확인 불가" and unable.get(key, 0) >= 2 else v,
            "note": notes.get(key),
        }
        for key, v in last.items()
    ]


def _done_text(done: list[dict]) -> str:
    """'흡기 필터 육안 점검 · 냉각 팬 분해 점검 확인 불가 2회, 냉각 설정값 확인 정상'(같은 결과끼리 묶음)."""
    groups: dict[str, list[str]] = {}
    for d in done:
        groups.setdefault(d["value"], []).append(d["title"])
    return ", ".join(f"{' · '.join(titles)} {value}" for value, titles in groups.items())


def _candidates(state: session.SessionState, k: ManualKnowledge, step: dict | None = None) -> list[dict]:
    order = [c.code for c in rules.ordered_causes(k, state.group)]
    nums = screen.decided_numbers(step, order)  # EV-011 CH-05: 호출 요약 원인 % = Pack 판정 출력(칸 없으면 앱 셈)
    probs = nums[0] if nums else session.probabilities(state)
    names = screen.cause_names(k, state.group)
    return [{"code": c, "name": names.get(c, c), "pct": p} for c, p in sorted(probs.items(), key=lambda x: -x[1])]


def _write(facts: Facts, g: grounding.Grounding, template: str) -> grounding.Checked:
    return grounding.checked(lambda: adapter_for_mode().write(facts), g, template)


def call_summary(
    alarm: m.Alarm, state: session.SessionState, k: ManualKnowledge, step: dict, events: list[dict]
) -> dict:
    """1.4 호출 요약: 알람 · 지금까지 한 점검과 결과 · 남은 원인 후보 · 필요한 작업 · 설비 상태.

    자동 작성 · 고칠 수 있음(정비자가 고친 문장은 보낼 때 그대로 저장).
    """
    group = deps.group_names()[alarm.symptom_group]
    done = checks_done(events, k)
    cands = _candidates(state, k, step)
    head = f"{alarm.equipment_id} {group}({screen.hhmm_kst(alarm.occurred_at)})."
    if step.get("corrected"):  # T10 / summary_corrected: 조치 뒤 정정 → 다시 판정 대신 확인 요청(AC-47-C1)
        cor = correction_of(events, step) or {}
        acts = actions_of(events, k)
        act_text = " · ".join(f"{a['title']}({a['section']}) 완료 {screen.hhmm_kst(_parse(a['at']))}" for a in acts)
        when = screen.hhmm_kst(_parse(cor.get("at"))) if cor.get("at") else ""
        body = (
            f"{act_text}. {when} 정비자가 {cor.get('section')} {cor.get('title')} 결과를 {cor.get('before')} → "
            f"{cor.get('after')}으로 정정(사유: {cor.get('reason')}). "
            "조치 뒤 설비 상태가 바뀌어 다시 판정하지 않고 확인을 요청합니다."
        )
        template = f"{head} {body}"
        facts_vals = {"kind": "correction_call", "head": head, "body": body}
        sections = {cor.get("section")} | {a["section"] for a in acts} | {d["section"] for d in done}
        sections.discard(None)
    elif step["kind"] == "handover":
        reason = step.get("reason")
        path_text = _done_text(done) or "점검 전"
        if reason == "out_of_scope_finding":
            quote = next((r.get("memo") for r in reversed(state.results) if r.get("memo")), "")
            body = f"{path_text}. 소견: {memo_summary(quote)}. 매뉴얼 후보 원인과 맞지 않음."
        elif reason == "restart_outside":
            body = f"{path_text}. 재가동 뒤에도 값이 경계 밖."
        elif reason == "llm_failure":
            body = f"{path_text}. 안내 문장 생성 3회 연속 실패, 매뉴얼 원문으로만 진행 가능."
        else:
            body = f"{path_text}. 할 수 있는 A · B 점검 없음."
        template = f"{head} {body} 설비 정지 대기."
        facts_vals = {"kind": "handover", "head": head, "body": body}
        sections = {d["section"] for d in done}
    else:
        sec = step["section"]
        eg = exception_guide_of(events) or {}
        ex = eg if eg.get("section") == sec else {}  # D-543 예외 안내: 매뉴얼 밖 예외 절 — 이름은 Pack 답에서
        title = k.sections[sec]["title"] if sec in k.sections else ex.get("title") or sec
        top = cands[0] if cands else None
        done_text = (
            (" · ".join(d["title"] for d in done) + " 점검 " + "/".join(sorted({d["value"] for d in done})))
            if done
            else "점검 전"
        )
        topic = ex.get("topic") or title
        now = next((w for w in walk_of(events, step) or [] if w["now"]), None)  # 걸음 길 = 지금 걸음 점검 이름(11쪽)
        need = (  # 고침A 26쪽: 예외 이름 '예외상황-{주제}'
            f"예외상황-{topic}{josa(topic, '과', '와')} 같은 조건 — "
            f"{now['what'] if now else title}({step.get('grade')}등급) 확인 필요"
            if ex
            else f"확인에 {title}({sec}절, {step.get('grade')}등급) 필요"
        )
        cand_text = f"1순위 후보 {top['name']}({top['pct']}%), " if top else ""
        if sec == switch.NEW:  # 정답지 묶음 ① 16쪽: 예외 진행 호출 요약('매뉴얼 후보 밖 — 예외 조치(C등급) 승인 요청')
            done_text, cand_text, need = "매뉴얼 후보 밖", "", f"{title}({step.get('grade') or 'C'}등급) 승인 요청"
        sep = " — " if sec == switch.NEW else ". "
        template = f"{head} {done_text}{sep}{cand_text}{need}. 설비는 정지 상태로 대기 중."
        facts_vals = {"kind": "call", "head": head, "done": done_text, "candidate": cand_text, "need": need}
        sections = {sec} | {d["section"] for d in done}
    g = grounding.Grounding(
        allowed_sections=frozenset(
            sections | {n for n in grounding.numbers_in(template) if grounding.is_section_like(n)}
        ),
        source_numbers=grounding.numbers_in(template),
        top_record_ids=frozenset(),
    )
    r = _write(Facts("call_summary", step.get("section") or "", {**facts_vals, "template": template}), g, template)
    return {"text": r.text, "fallback": r.fallback, "label": r.label, "checks_done": done, "candidates": cands}


def correction_of(events: list[dict], step: dict) -> dict | None:
    """정정 호출(T10 / summary_corrected)을 연 정정: 절 · 점검 이름 · 전 → 후 · 사유 · 시각."""
    e = next(
        (e for e in reversed(events) if e["type"] == "correction" and e.get("mode") == "call_with_correction"), None
    )
    if e is None:
        return None
    k = knowledge_of(events)
    sec = e.get("section")
    return {
        "seq": e["seq"],
        "section": sec,
        "title": k.sections[sec]["title"] if sec in k.sections else None,
        "before": (e.get("before") or {}).get("value"),
        "after": (e.get("after") or {}).get("value"),
        "reason": e.get("reason"),
        "at": e.get("_at"),
    }


def actions_of(events: list[dict], k: ManualKnowledge) -> list[dict]:
    """마친 조치(정정으로 무효 제외): 절 · 이름(약속 v10 걸음이면 그 조치 무엇) · 완료 시각."""
    eg = exception_guide_of(events)
    return [
        {"section": e["section"], "title": step_title(e, k, eg), "at": e.get("_at")}
        for e in events
        if e["type"] == "action_done" and not e.get("void")
    ]


def handover_head(step: dict, state: session.SessionState, k: ManualKnowledge) -> str:
    reason = step.get("reason")
    if reason == "out_of_scope_finding":
        return "후보 원인과 맞지 않는 소견"
    if reason == "restart_outside":
        return "재가동 뒤에도 값이 경계 밖"
    if reason == "llm_failure":
        return "안내 문장 생성이 반복해서 실패함"
    twice = [s for s, n in state.unable.items() if n >= 2]
    if twice:
        return f"{k.sections[twice[0]]['title']} · 확인 불가 2회, 남은 대안 없음"
    return "할 수 있는 점검을 모두 했으나 원인이 나오지 않음"


# ---------------------------------------------------------------- 보고서(R10 · R8)


def _restart_events(events: list[dict]) -> tuple[dict | None, dict | None]:
    restart = next((e for e in events if e["type"] == "restart" and not e.get("void")), None)
    result = next((e for e in events if e["type"] == "restart_result" and not e.get("void")), None)
    return restart, result


def downtime_minutes(alarm: m.Alarm, events: list[dict], confirmed_at: datetime | None) -> int:
    """결정 015 ③: 알람 시각 ~ 재가동 확인 시각.

    주간 처리(감시 운전)로 끝나면 ~ 그 답 시각, 둘 다 없으면 ~ 보고서 확정 시각.
    """
    _, result = _restart_events(events)
    daytime = next((e for e in events if e["type"] == "call_answer" and e.get("answer") == "daytime"), None)
    end = (
        _parse((result or {}).get("_at"))
        or _parse((daytime or {}).get("at") or (daytime or {}).get("_at"))
        or confirmed_at
        or datetime.now(UTC)
    )
    return max(0, round((end - alarm.occurred_at).total_seconds() / 60))


def unresolved(
    state: session.SessionState, k: ManualKnowledge, events: list[dict], grades: dict, notes: bool = True
) -> str:
    """결정 015 ④: 후보로 남은 원인(확정 원인 제외)의 C · D 점검 가운데 하지 못한 것.

    여기에 주간 처리 요청 + 호출 처리 결과를 더한다. notes = False면 파트장 답 메모를 뺀다(Pack 본문 — D-294).
    """
    items = []
    for cause in state.candidates:
        if cause == state.confirmed:
            continue
        for chk in k.checks.get(state.group, []):
            if chk.cause == cause and chk.section_id not in state.done and grades.get(chk.section_id) in {"C", "D"}:
                items.append(f"{chk.title}({chk.section_id}, {grades[chk.section_id]}등급) · 주간 확인 요청")
    for e in events:
        if e["type"] == "call_answer" and not e.get("void") and e.get("corrected"):  # 결정 019 ①: 정정 내용 · 답 · 메모
            cor = correction_of([x for x in events if x["seq"] < e["seq"]], {}) or {}
            note = f" · {e['note']}" if e.get("note") and notes else ""
            items.append(
                f"정정 확인 · {cor.get('section')} {cor.get('title')}({cor.get('before')} → {cor.get('after')}) · "
                f"{ANSWER_LABEL.get(e['answer'], e['answer'])}{note}"
            )
            continue
        if e["type"] == "call_answer" and not e.get("void") and e.get("answer") == "approved":
            continue  # 승인된 호출은 미확인 · 요청이 아님 — 최종본 4 '파트장 승인' 줄(approved_calls · D-546 덧 34)
        if e["type"] == "call_answer" and not e.get("void"):  # 호출 처리 결과(결정 015 ④ · 018 ③ ④)
            sec = e.get("section")
            what = f"{k.sections[sec]['title']}({sec}) " if sec in k.sections else "숙련자 인계 답 · "
            note = f" · {e['note']}" if e.get("note") and notes else ""
            items.append(f"{what}{ANSWER_LABEL.get(e['answer'], e['answer'])}{note}")
        if e["type"] == "call_sent" and e.get("kind") == "handover" and not e.get("void"):
            items.append("숙련자 인계")
    return " / ".join(dict.fromkeys(items)) or "없음"


def approved_calls(k: ManualKnowledge, events: list[dict], grades: dict) -> list[str]:
    """최종본 4 조치 결과 '파트장 승인': 승인 후 진행 호출(정정 호출은 5 그대로) — '이름(절, 등급) · 승인 후 진행'."""
    out = []
    for e in events:
        if (
            e["type"] == "call_answer"
            and not e.get("void")
            and e.get("answer") == "approved"
            and not e.get("corrected")
        ):
            sec = e.get("section")
            title = k.sections[sec]["title"] if sec in k.sections else sec
            out.append(f"{title}({sec}, {grades.get(sec) or '?'}등급) · {ANSWER_LABEL['approved']}")
    return list(dict.fromkeys(out))


def report_draft(
    c: Connection,
    row: m.DiagSession,
    alarm: m.Alarm,
    state: session.SessionState,
    step: dict,
    events: list[dict],
    confirmed_at: datetime | None,
    grades: dict | None = None,
    for_pack: bool = False,
) -> dict[str, str]:
    """R10 8항목 초안(코드 사실). 원인 · 조치는 문장 생성 전 원문(실패면 이 글로 대체).

    grades = {}면 등급 글 없이(초안 열쇠용). for_pack = Pack report 본문용 — 미확인 칸에서 파트장 답 메모를 뺀다(D-294).
    """
    k = knowledge_of(events)
    g = grades_of(events, deps.grades(c)[0], {})[1] if grades is None else grades
    group = deps.group_names()[alarm.symptom_group]
    system = deps.group_systems()[alarm.symptom_group]
    end = confirmed_at or datetime.now(UTC)
    al = alarm.alarm
    table = deps.alarm_table()
    rows = screen.sensor_rows(al, alarm.symptom_group, alarm.sensor_snapshot, table)
    first = rows[0]
    others = [r for r in rows[1:] if r["out_of_range"]] or rows[1:2]  # 결정 015 ①
    side = "아래" if al["direction"] == "경계 아래" else "위"
    key = first["key"]
    sensor = f"{first['name']} {first['value_text']} (경계 {screen.fmt(key, al['boundary'])} {al['unit']} {side})"
    if al.get("origin") == "lead":  # EV-009: 경계 안 · 파트장이 올림
        sensor = f"{first['name']} {first['value_text']} ({screen.LEAD_PROMOTED})"
    sensor += "".join(f" · {r['value_text']}" for r in others[:1]) + f" → {system} 쪽"
    parts = ["잠금 확인(1.2)"]
    eg = exception_guide_of(events)
    walk_sec = {x["what"]: eg["section"] for x in [*eg.get("checks", []), *eg.get("actions", [])]} if walks(eg) else {}
    for p in path(events, k, {}, alarm):
        if p["title"] in {
            k.sections["1.2"]["title"],
            "재가동 후 값 확인",
            k.sections[session.RESTART_SECTION]["title"],
        }:
            continue
        if p["status"] == "void":  # 결정 019 ⑤: 정정으로 무효가 된 점검도 경로에 남긴다
            vsec = next((s for s in k.sections if k.sections[s]["title"] == p["title"]), "")
            if k.sections.get(vsec, {}).get("kind") != "action":
                parts.append(f"{p['title']}({vsec}, 정정으로 무효)")
            continue
        if p["title"] == k.sections[session.MONITOR_SECTION]["title"]:
            parts.append(f"{p['title']}(6.2)")
            continue
        sec = next((s for s in k.sections if k.sections[s]["title"] == p["title"]), "") or walk_sec.get(p["title"], "")
        if k.sections.get(sec, {}).get("kind") == "action":
            continue
        detail = p["detail"].replace(sec_text(sec), "").strip(" ·") if sec else p["detail"]
        if sec.startswith(exception.PREFIX) and not detail:  # 예외 절 조치 완료 줄 — 조치 칸에 따로 적음
            continue
        parts.append(f"{p['title']}({sec}{', ' + detail.replace(' · ', ': ', 1) if detail else ''})")
    outcome = step.get("outcome") or ("confirmed" if state.confirmed else "unknown")
    cause = (
        k.cause(state.confirmed).name
        if state.confirmed and outcome == "confirmed"
        else OUTCOME_CAUSE.get(outcome, "원인 미상")
    )
    restart, result = _restart_events(events)
    rv = restart_block(alarm, bool(state.confirmed))
    restart_text = ""
    if restart and result:
        label = "경계 이내" if result["outcome"] == "within" else "경계 밖"
        restart_text = f"재가동(6.1, {g.get('6.1')}, 확인 완료) 후 {al['sensor']} {rv['value_text']} {label}"
    if state.confirmed and outcome == "confirmed":
        ac = k.cause(state.confirmed).action_section
        action = f"{k.sections[ac]['title']}({ac}, {g.get(ac)})" + (f" · {restart_text}" if restart_text else "")
    elif outcome == "monitor":
        action = "조치 없음" + (f" · {restart_text}" if restart_text else "") + " · 감시 대상 등록(6.2)"
    elif actions_of(events, k):  # 결정 019 ①: 정정 호출에 주간 처리 · 직접 확인 — 이미 한 조치는 그대로
        action = " · ".join(  # 화면 글에 EX-NEW 0 → '예외'(zzz51 답 4)
            f"{a['title']}({'예외' if a['section'] == switch.NEW else a['section']}, {g.get(a['section'])})"
            for a in actions_of(events, k)
        )
        action += f" · {restart_text}" if restart_text else ""
    else:
        action = "조치 없음" + (f" · {restart_text}" if restart_text else "")
    facts = {
        "equipment_alarm": (
            f"{alarm.equipment_id} · {group} · {screen.hhmm_kst(alarm.occurred_at)} ~ {screen.hhmm_kst(end)}"
        ),
        "sensor_evidence": sensor,
        "check_path": " → ".join(parts),
        "cause": cause,
        "evidence_records": " · ".join(s["record_id"] for s in row.similar[:3]),  # R3 상위 최대 3개
        "action": action,
        "downtime": f"{downtime_minutes(alarm, events, confirmed_at)}분",
        "unresolved": unresolved(state, k, events, g, notes=not for_pack),
    }
    return facts


REPORT_SENTENCES = ("cause", "action")  # R8 대상: 보고서 원인 · 조치 문장(약속 v3 report 출력 칸)
REPORT_RUN_WHERE = {"run.write_report", "run.rewrite_report"}  # D-288: Pack 문장 node 실패 = provider_failure


def draft_key(row: m.DiagSession, events: list[dict], draft: dict[str, str]) -> str:
    """초안 열쇠(CH-20): 원인 · 조치 원문 · 근거 절 — 바뀌면 새로 한 번 쓴다(반려 뒤 추가 점검 · 확정 전 정정).

    draft는 등급 글 없는 초안(report_draft grades={}) — 파트장 등급표 변경은 _open_step을 거치지 않으므로
    열쇠에 넣지 않는다
    (R13: 열린 단계는 이후 등급 변경에도 그대로 — 쓴 문장도 그때 등급 그대로).
    """
    sections = sorted(_report_grounding(row, events, draft).allowed_sections)
    raw = json.dumps([draft["cause"], draft["action"], sections], ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def report_sentences(
    row: m.DiagSession, events: list[dict], draft: dict[str, str], key: str
) -> tuple[dict[str, dict], int]:
    """화면 · 확정이 읽는 원인 · 조치 문장과 그 기록 seq — 화면 조회는 쓰지 않는다(쓰기는 _open_step 한 곳)."""
    written = [e for e in events if e["type"] == "report_written"]
    if not written:  # Q-18: 이 판 전에 보고서 단계를 연 세션은 지금 동작(그릴 때 씀)
        gr = _report_grounding(row, events, draft)
        return {k: _sentence(k, draft, gr) for k in REPORT_SENTENCES}, 0
    hit = next((e for e in reversed(written) if e.get("draft_key") == key), None)
    if hit is not None:
        return hit["fields"], hit["seq"]
    fields = {
        k: {"text": draft[k], "fallback": True, "attempts": None, "reason": "draft_changed"} for k in REPORT_SENTENCES
    }
    return fields, written[-1]["seq"]


def report_written_payload(
    c: Connection, row: m.DiagSession, state: session.SessionState, step: dict, events: list[dict], seq: int
) -> dict | None:
    """보고서 단계를 열 때(CH-20): 초안 열쇠가 마지막 report_written과 같으면 None, 아니면 새로 쓴 문장."""
    alarm = sessions_alarm(c, row)
    draft = report_draft(c, row, alarm, state, step, events, None)
    key = draft_key(row, events, report_draft(c, row, alarm, state, step, events, None, grades={}))
    last = next((e for e in reversed(events) if e["type"] == "report_written"), None)
    if last is not None and last.get("draft_key") == key:
        return None
    body = report_draft(c, row, alarm, state, step, events, None, for_pack=True)  # PR-16: 파트장 답 메모 0(D-294)
    sections = sorted(_report_grounding(row, events, body).allowed_sections)
    try:  # CH-19: Pack report Run(약속 v3)
        res = runs.report(alarm.alarm_id, row.symptom_group, row.similar, body, sections, seq)
    except PackUnavailable as e:  # CH-22: 재시도 뒤 끝내 실패 — 503 대신 두 칸 원문(확정 가능 · 다시 부르지 않음)
        reason = "provider_failure" if e.code == "run_failed" and e.where in REPORT_RUN_WHERE else "pack_unavailable"
        fields = {k: {"text": draft[k], "fallback": True, "attempts": None, "reason": reason} for k in REPORT_SENTENCES}
        return {"draft_key": key, "fields": fields, "via": "deploy", "run_id": e.run_id}
    try:
        out = res.output["report"]
        fields = {k: {x: out[k][x] for x in ("text", "fallback", "attempts", "reason")} for k in REPORT_SENTENCES}
    except (KeyError, TypeError):  # 201인데 report 모양이 아님 — CH-22 '표 밖'과 같이 원문(세션이 막히지 않게)
        fields = {
            k: {"text": draft[k], "fallback": True, "attempts": None, "reason": "pack_unavailable"}
            for k in REPORT_SENTENCES
        }
    return {
        "draft_key": key,
        "fields": fields,
        "via": "deploy",
        "run_id": res.run_id,
        "deployment_id": res.deployment_id,
    }


def _sentence(fkey: str, draft: dict[str, str], gr: grounding.Grounding) -> dict:
    """앱 adapter(기본 길): 생성 → R8 → 1회 다시 → 원문. reason은 앱이 아는 만큼(preview v12 H)."""
    failed = []

    def generate() -> Draft:
        try:
            return adapter_for_mode().write(Facts("report_sentence", "", {"field": fkey, "template": draft[fkey]}))
        except AdapterError:
            failed.append(True)
            raise

    r = grounding.checked(generate, gr, draft[fkey])
    reason = None if not r.fallback else "provider_failure" if failed else "ungrounded"
    return {"text": r.text, "fallback": r.fallback, "attempts": r.attempts, "reason": reason}


def _report_grounding(row: m.DiagSession, events: list[dict], facts: dict[str, str]) -> grounding.Grounding:
    """R8 원천: 절 번호 = 이번 세션 기록, 수치 = 세션 입력 · 센서 값 · 초안 사실, 근거 사례 = R3 상위 10건."""
    nums = grounding.numbers_in(*facts.values())
    secs = {s for s in nums if grounding.is_section_like(s)} | {e.get("section") for e in events if e.get("section")}
    secs |= {e["decision"]["section"] for e in events if e["type"] == "step_opened" and e["decision"].get("section")}
    return grounding.Grounding(
        allowed_sections=frozenset(x for x in secs if x),
        source_numbers=nums,
        top_record_ids=frozenset(s["record_id"] for s in row.similar),
    )


def exception_of(events: list[dict]) -> dict | None:
    """D-538: 마지막 보고서 확정 때 함께 보낸 예외 상황 기록 — 반려 뒤 다시 확정하면 새 기록이 이김."""
    last = next((e for e in reversed(events) if e["type"] == "report_confirm"), None)
    return (last or {}).get("exception_record") or None


# 예외 기록 = 자유 글 → AI 나눔(약속 v10 2절: 주제 + 점검 목록 + 조치 목록) → 비슷한 것(3절 · 통째 한 번)
# → 예외 전체에 한 번 정비자 같음(missed · 놓침) / 아님(new_exception · 새 예외) = report_confirm.exception_review.
EX_TEXT_MAX = 2000
EX_LIST_MAX = 10


def _legacy_text(ex: dict) -> str:
    """D-556 전 기록 · 초안(두 글 situation · action, D-554 칸 check · action · note)을 글 하나로(읽기만)."""
    parts = [ex.get("situation"), ex.get("action") if isinstance(ex.get("action"), str) else None, ex.get("note")]
    return "\n".join(x.strip() for x in parts if isinstance(x, str) and x.strip())


def exception_draft_text(ex: dict | None) -> str:
    ex = ex or {}
    return (ex.get("text") or "").strip() if "text" in ex else _legacy_text(ex)


def exception_split_of(events: list[dict]) -> dict | None:
    """마지막 확정 뒤 마지막 「AI 나눔」 결과 {text, exception, error} — 없으면 None."""
    last_confirm = max((e["seq"] for e in events if e["type"] == "report_confirm"), default=0)
    hit = next((e for e in reversed(events) if e["type"] == "exception_split" and e["seq"] > last_confirm), None)
    return {k: hit.get(k) for k in ("text", "exception", "error", "similar_error")} if hit else None


def _sentence(row: dict) -> dict:
    """D-642 C: Pack이 AI 나눔 때 만든 지시 문장(선택 칸 instruction).
    빈 글 · 없음이면 칸을 싣지 않음(옛 꼴 그대로)."""
    say = str(row.get("instruction") or "").strip()
    return {"instruction": say} if say else {}


def _check_of(ck: dict) -> dict:
    return {
        **{
            k: ck.get(k) if k == "minutes" else str(ck.get(k) or "").strip()
            for k in ("what", "how", "criteria", "minutes")
        },
        **_sentence(ck),
    }


def _action_of(ac: dict) -> dict:
    return {
        "what": str(ac.get("what") or "").strip(),
        "parts": [str(x) for x in ac.get("parts") or []],
        "minutes": ac.get("minutes"),
        **_sentence(ac),
    }


def say_of(item: dict, side: str) -> str:
    """D-642 C 안내 차례 한 줄 = 승인된 지시 문장(instruction).
    없으면 무엇 · 어떻게 · 기준(조치는 무엇 · 부품)을 이어 씀. '지금' 표시는 문장에 넣지 않음(화면이 앞에 따로)."""
    say = str(item.get("instruction") or "").strip()
    if say:
        return say
    what = str(item.get("what") or "").strip()
    if side == "action":
        parts = [str(x) for x in item.get("parts") or []]
        return " · ".join(x for x in (what, f"부품 {', '.join(parts)}" if parts else "") if x)
    how, crit = str(item.get("how") or "").strip(), str(item.get("criteria") or "").strip()
    return " · ".join(x for x in (what, how if how != what else "", f"기준 {crit}" if crit else "") if x)


def split_exception(output: dict) -> dict | None:
    """배포 exception_split 출력(약속 v10 2절) → 예외 하나 {topic, checks, actions} — 꼴 어긋난 줄 · 빈 what은 뺌
    (지어내기 0) · 남은 점검 0 또는 조치 0이면 None(나누지 못함)."""
    ex = (output.get("exception_split") or {}).get("exception")
    if not isinstance(ex, dict):
        return None
    checks, actions = [], []
    for raw, conv, out in ((ex.get("checks"), _check_of, checks), (ex.get("actions"), _action_of, actions)):
        for x in raw if isinstance(raw, list) else []:
            try:
                row = conv(x)
            except (TypeError, AttributeError):
                continue
            if row["what"]:  # 방법 · 기준은 빈 글 허용
                out.append(row)
    if not checks or not actions:
        return None
    # 주제(정답지 18쪽 · '예외상황-{주제}') = Pack 칸 topic — 없으면 첫 조치 what 앞 15자(Pack 다듬기와 같음)
    topic = str(ex.get("topic") or "").strip().removeprefix("예외상황-").strip()[:15] or actions[0]["what"][:15]
    return {"topic": topic, "checks": checks[:EX_LIST_MAX], "actions": actions[:EX_LIST_MAX]}


def split_reason(output: dict) -> str:
    """나누지 못한 까닭(약속 v10 2절 reason not_split | invalid_output) — 없거나 모르는 값이면 not_split."""
    reason = (output.get("exception_split") or {}).get("reason")
    return reason if reason in {"not_split", "invalid_output"} else "not_split"


SIMILAR_KINDS = {"예외", "매뉴얼"}


def similar_query(ex: dict) -> dict:
    """비슷한 것 찾기에 보낼 예외 하나(약속 v10 3절 요청 꼴 = 1절 꼴 · 비슷한 것 칸은 뺌)."""
    return {k: ex[k] for k in ("topic", "checks", "actions")}


def similar_matches(output: dict) -> list[dict]:
    """배포 exception_similar 출력(약속 v10 3절) → 예외 통째의 비슷한 것(상위 3 · 꼴 어긋남은 뺌)."""
    rows = (
        (output.get("exception_similar") or {}).get("matches")
        if isinstance(output.get("exception_similar"), dict)
        else None
    )
    out = []
    for x in rows or []:
        try:
            if x.get("kind") in SIMILAR_KINDS and x.get("no"):
                row = {
                    "kind": x["kind"],
                    "no": str(x["no"]),
                    "name": str(x.get("name") or ""),
                    "score": float(x["score"]),
                }
                # 예외 줄 지금 목록(선택 칸 · D-560 덧 10 — 같음 + 고침의 바탕) — 꼴 어긋난 줄은 빼고 둘 다 있을 때만
                cks = [
                    _check_of(c)
                    for c in x.get("checks") or []
                    if isinstance(c, dict) and str(c.get("what") or "").strip()
                ]
                acs = [
                    _action_of(a)
                    for a in x.get("actions") or []
                    if isinstance(a, dict) and str(a.get("what") or "").strip()
                ]
                if x["kind"] == "예외" and cks and acs:
                    row |= {"checks": cks[:EX_LIST_MAX], "actions": acs[:EX_LIST_MAX]}
                out.append(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out[:3]


def exception_review(text: str, split: dict | None, review: dict | None) -> dict:
    """확정 · 적용 때(약속 v10 3절): 지금 글을 나눈 결과가 있고 예외 전체에 한 번 같음(비슷한 것 하나) /
    아님(match null) — 아니면 409. 지금 글을 나누지 못했으면(Run 실패 · 한쪽 0 · 꼴 밖) 고를 것 0 · 글 그대로 새 예외.
    → 기록 칸(약속 v10 6절): exception = 1절 꼴 그대로(주제 · 점검 · 조치 — 다른 칸 0 · llmops-2가 그 밖 칸 거절) ·
    exception_review {decision, match} 한 번 · exception_matches = 그때 보인 비슷한 것(앱 보기용 · 앱 칸)."""
    if split is None or (split.get("text") or "") != text:
        raise HTTPException(status_code=409, detail={"reason": "exception_split_needed"})
    if split.get("error"):
        if review:
            raise HTTPException(status_code=409, detail={"reason": "exception_review_invalid"})
        return {"exception": None, "exception_review": None}
    ex = split["exception"]
    if not review:
        raise HTTPException(status_code=409, detail={"reason": "exception_review_needed"})
    match = review.get("match")
    same = review["decision"] == "same"
    matches = ex.get("matches") or []
    if same != (match is not None) or (
        same and (match["kind"], match["no"]) not in {(x["kind"], x["no"]) for x in matches}
    ):
        raise HTTPException(status_code=409, detail={"reason": "exception_review_invalid"})
    edit = review.get("edit")
    if (
        edit is not None
    ):  # same-edit.md 1절: same · 기존 예외(지금 목록이 온 줄)에만 · 고친 뒤 목록 전체 · 바뀐 것 있어야
        target = next((x for x in matches if (x["kind"], x["no"]) == (match["kind"], match["no"])), {}) if same else {}
        edit = {"checks": [_check_of(c) for c in edit["checks"]], "actions": [_action_of(a) for a in edit["actions"]]}
        before = {"checks": target.get("checks"), "actions": target.get("actions")}
        if not same or match["kind"] != "예외" or not target.get("checks") or edit == before:
            raise HTTPException(status_code=409, detail={"reason": "exception_review_invalid"})
    return {
        "exception": {k: ex[k] for k in ("topic", "checks", "actions")},
        "exception_review": {
            "decision": review["decision"],
            "match": {"kind": match["kind"], "no": match["no"]} if same else None,
            **({"edit": edit} if edit else {}),  # same-edit.md 1절 새 칸(같음 + 고침일 때만)
        },
        "exception_matches": matches,
    }


def exception_view(rec: dict) -> dict | None:
    """v10 기록 칸 → 화면 예외 하나: 1절 꼴 + 비슷한 것 + 같음 / 아님 + 기록 이름(약속 v9 3절: 같음 = missed 놓침 ·
    아님 = new_exception 새 예외 · 관문)."""
    ex, rv = rec.get("exception"), rec.get("exception_review") or {}
    if not isinstance(ex, dict):
        return None
    same = rv.get("decision") == "same"
    return {
        **ex,
        "matches": rec.get("exception_matches") or [],
        "edit": rv.get("edit") if same else None,  # same-edit.md 1절 · 정답지 30쪽 같음 + 고침(고친 뒤 목록)
        "decision": rv.get("decision"),
        "match": rv.get("match") if same else None,
        "kind": ("missed" if same else "new_exception") if rv.get("decision") else None,
    }


def exception_entries(ex: dict) -> list[dict]:
    """기록 하나의 예외(읽기): v10 = exception 하나(화면 꼴) · v9 기록(items 짝) = 짝마다 예외 하나로 읽음."""
    if isinstance(ex.get("exception"), dict):
        return [exception_view(ex)]
    return [
        {
            **{k: v for k, v in it.items() if k not in {"check", "action"}},
            "checks": [it["check"]] if it.get("check") else [],
            "actions": [it["action"]] if it.get("action") else [],
        }
        for it in ex.get("items") or []
        if isinstance(it, dict)
    ]


def exception_text(ex: dict) -> str:
    """기록 한 글(참고 알림): 나눈 기록 = '점검 … · 조치 …', 나누지 못한 기록 = 적은 글, 전 기록 = 조치 글."""
    ents = exception_entries(ex)
    if not ents:
        if ex.get("text"):
            return ex["text"]
        return ex.get("action") if isinstance(ex.get("action"), str) else _legacy_text(ex)
    return ". ".join(
        " · ".join([*(f"점검 {c['what']}" for c in e["checks"]), *(f"조치 {a['what']}" for a in e["actions"])])
        for e in ents
    )


def record_out(ex: dict | None) -> dict | None:
    """응답 꼴(ExceptionRecord): v10 기록 = exception을 화면 꼴(+ 비슷한 것 · 고름 · 이름)로.
    v9 기록(items 짝 · 줄마다 review)은 예외 하나로 읽음 — 짝이 하나면 그 고름 그대로,
    여럿이면 고름 칸 없이(지어내기 0).
    저장된 기록은 그대로 둠(읽기만)."""
    if not ex:
        return ex
    if "items" not in ex or "exception" in ex:
        return {k: v for k, v in ex.items() if k != "exception_matches"} | {"exception": exception_view(ex)}
    ents = exception_entries(ex)
    out = {k: v for k, v in ex.items() if k != "items"} | {"exception": None, "exception_review": None}
    if ents:
        one = ents[0] if len(ents) == 1 else {}
        acts = [a for e in ents for a in e["actions"]]
        topic = next((e.get("topic") for e in ents if e.get("topic")), None)
        out["exception"] = {
            "topic": topic or (acts[0]["what"][:15] if acts else ""),
            "checks": [c for e in ents for c in e["checks"]][:EX_LIST_MAX],
            "actions": acts[:EX_LIST_MAX],
            "matches": one.get("matches") or [],
            **{k: one[k] for k in ("decision", "match", "kind") if one.get(k) is not None},
        }
        if one.get("decision"):
            out["exception_review"] = {"decision": one["decision"], "match": one.get("match")}
    return out


def exception_applied_of(events: list[dict]) -> dict | None:
    """정답지 18 · 19쪽: 마지막 확정 뒤 마지막 「보고서에 적용」(저장 꼴) — 없으면 None."""
    last_confirm = max((e["seq"] for e in events if e["type"] == "report_confirm"), default=0)
    hit = next((e for e in reversed(events) if e["type"] == "exception_apply" and e["seq"] > last_confirm), None)
    if hit is None and any(e["type"] == "report_rejected" and e["seq"] > last_confirm for e in events):
        hit = exception_of(events)  # 정답지 20쪽: 반려되면 앞 확정 기록이 구역에 다시 · [예외 상황 수정]
    if not hit or not hit.get("exception") and not hit.get("items") and not hit.get("split_error"):
        return None
    keep = ("text", "exception", "exception_review", "exception_matches", "items", "split_error")
    return {k: hit[k] for k in keep if k in hit} | {"split_error": hit.get("split_error")}


def exception_auto(fields: list[dict], events: list[dict]) -> dict:
    """자동 두 칸(D-554 · D-556): 상황 = 보고서 설비 · 알람 + 센서 근거, 결과 = 재가동 값 판정(입력 0)."""
    text_of = {f["key"]: f["text"] for f in fields}
    situation = " · ".join(t for t in (text_of.get("equipment_alarm"), text_of.get("sensor_evidence")) if t)
    _, result = _restart_events(events)
    if result is None:
        res = "재가동 전"
    else:
        res = "재가동 정상(경계 이내)" if result["outcome"] == "within" else "재가동 뒤 경계 밖"
    return {"situation": situation, "result": res}


def exception_draft_of(events: list[dict]) -> dict | None:
    """D-543 「노하우 등록」: 마지막 확정 뒤 마지막 초안(글 하나) — 보고서 칸에 채움(보고서와 함께 승인)."""
    last_confirm = max((e["seq"] for e in events if e["type"] == "report_confirm"), default=0)
    draft = next((e for e in reversed(events) if e["type"] == "exception_draft" and e["seq"] > last_confirm), None)
    text = exception_draft_text((draft or {}).get("exception_record"))
    return {"text": text} if text else None


def exception_notes(c: Connection, alarm: m.Alarm, limit: int = 3) -> list[dict]:
    """D-543 참고 알림(답장 001-zzz9 다 = A): 승인 이력 원본의 예외 상황 기록 중 같은 설비 · 같은 증상군, 최근 순."""
    rows = c.execute(
        text(
            "select record_id, occurred_at, record->'exception_record' as ex from knowledge.history_records "
            "where source = 'approved_report' and equipment_id = :eq and symptom_group = :g "
            "and record->'exception_record' is not null order by occurred_at desc, record_id desc limit :n"
        ),
        {"eq": alarm.equipment_id, "g": alarm.symptom_group, "n": limit},
    ).all()
    return [
        {
            "record_id": r.record_id,
            "occurred_at": r.occurred_at.isoformat(),
            "situation": r.ex.get("situation", ""),
            "action": exception_text(r.ex),
            # 알림에 보일 한 줄: D-556 나눈 기록은 줄을 이은 글(상황은 자동이라 머리와 겹침) · 전 기록은 상황 글
            "text": exception_text(r.ex) if exception_entries(r.ex) or r.ex.get("text") else r.ex.get("situation", ""),
            "topic": next((e.get("topic") for e in exception_entries(r.ex) if e.get("topic")), None),
        }
        for r in rows
    ]


def exception_guide_of(events: list[dict]) -> dict | None:
    """약속 v8(D-545 덧 2): start 결과 exception_guide(세션 시작 step_opened에 저장) — 없으면 None."""
    return next((e["exception_guide"] for e in events if e["type"] == "step_opened" and e.get("exception_guide")), None)


PICK_TYPES = {"switch_check", "diagnosis_pick"}  # [점검 바꾸기] 고름(잠금 뒤 판정 사건 · 잠금 전 진단 카드 사건)
OFFER_WORK = {"instruct", "instruct_after_checklist", "call"}


def exception_offer(events: list[dict]) -> dict | None:
    """D-633 예외 추천을 골랐나: start 결과 exception_guide가 실린 세션 = 예외가 보임
    (진단 카드 · [점검 바꾸기] 후보 줄). 고름 = 무효 아닌 사건 중 그 예외 절을 고른 줄(바꾸기 · 결과 · 호출 …) 하나라도.
    안 고름이면 how = switched([점검 바꾸기]로 다른 점검 · [예외] EX-NEW) |
    followed(추천 점검대로 — 창을 열고 취소한 것은 사건이 없어 여기).
    picked_section = 사람이 한 점검(바꾼 마지막 점검 | 첫 결과 절 | 첫 점검 안내 절). 예외 안내가 없으면 None."""
    first = next((e for e in events if e["type"] == "step_opened" and e.get("exception_guide")), None)
    if first is None:
        return None
    eg = first["exception_guide"]
    sec = eg.get("section")
    live = [e for e in events if not e.get("void") and e["type"] not in {"step_opened", "preview_shown"}]
    base = {"relation_id": eg.get("relation_id"), "run_id": first.get("run_id")}
    if any(e.get("section") == sec for e in live):
        return {**base, "adopted": True, "how": None, "picked_section": sec}
    moves = [
        e.get("section") if e["type"] in PICK_TYPES else switch.NEW
        for e in live
        if e["type"] in PICK_TYPES or e["type"] == "exception_start"
    ]
    if moves:
        return {**base, "adopted": False, "how": "switched", "picked_section": moves[-1]}
    done = next((e.get("section") for e in live if e["type"] == "result"), None)
    shown = next(
        (
            (e.get("decision") or {}).get("section")
            for e in events
            if e["type"] == "step_opened" and not e.get("void") and (e.get("decision") or {}).get("kind") in OFFER_WORK
        ),
        None,
    )
    return {**base, "adopted": False, "how": "followed", "picked_section": done or shown}


def why_line(eg: dict | None) -> str | None:
    """D-593 '왜 이 예외' 한 줄 = Pack matched — 규칙은 등록 기기에서, 최근 정비는 이 기기 것(지어내기 0)."""
    m_ = (eg or {}).get("matched")
    if not m_:
        return None
    src = m_.get("source_equipment")
    same = src == m_.get("equipment_id")
    head = "이 기기에서 배운 규칙" if same else f"{src}에서 배운 규칙" if src else "배운 규칙"
    # D-663 덧1: 화면에 보고서 번호(R-…) 0 — '… M-01에서 배운 규칙'까지만(근거 기록은 matched.records에 그대로)
    r = m_.get("recent")
    if r:
        n = int(r["days_ago"])
        when = f"{n}일 전" if n else "오늘"
        work = f"같은 정비({r['section']} · {r['record_id']})"  # 이 기기 것 — 절 · 이력 번호
        return f"{head} · {when} {work}" if same else f"{head} · 이 기기도 {when} {work}"
    conds = [f"{screen.SENSORS.get(f, (f,))[0]} {op} {v}" for f, op, v in m_.get("sensors") or []]
    return " · ".join([head, *conds])


def miss_line(source: dict | None, mine: list[dict], days: int) -> str | None:
    """D-637 F11 '왜 예외가 아닌가' 한 줄 — 등록 예외(최근 정비 조건)의 절을
    이 기기가 최근 days일 안에 안 했을 때만. source = {record_id, equipment_id, sections}
    · mine = 이 기기 recent_maintenance 줄. 같은 정비가 있으면 까닭을 모르므로 None."""
    secs = (source or {}).get("sections") or []
    if not secs:
        return None
    done = {x for r in mine if r["days_before"] <= days for x in [*r["did"], *r["checked"]]}
    if done & set(secs):
        return None
    where = f"{source['equipment_id']}에서 배운 규칙"  # D-663 덧1: 보고서 번호(R-…) 화면 0
    return f"예외 조건 안 맞음 · 최근 {days}일 같은 정비({', '.join(secs)}) 없음 · {where}"


def exception_source(c: Connection, alarm: m.Alarm) -> dict | None:
    """같은 증상군에서 가장 최근 승인된 예외 기록 — 그때 그 설비 최근 정비 절
    (조치 did · 앱 번호 · 보고서 자동 정보 그대로)."""
    r = c.execute(
        text(
            "select record_id, record->'exception_record'->'context' as ctx from knowledge.history_records "
            "where source = 'approved_report' and symptom_group = :g and record->'exception_record' is not null "
            "order by occurred_at desc, record_id desc limit 1"
        ),
        {"g": alarm.symptom_group},
    ).first()
    if r is None or not r.ctx:
        return None
    secs = sorted({x for w in r.ctx.get("recent_maintenance") or [] for x in w.get("did") or []})
    return {"record_id": r.record_id, "equipment_id": r.ctx.get("equipment_id"), "sections": secs}


def exception_miss(c: Connection, alarm: m.Alarm, eg: dict | None) -> str | None:
    """예외 안내가 없을 때만: 등록 예외가 이 알람에 왜 안 맞았나(최근 같은 정비 없음)
    — 진단 계산 · Pack 값 손 0(표시만)."""
    if eg:
        return None
    return miss_line(exception_source(c, alarm), recent_maintenance(c, alarm), RECENT_DAYS)


def walks(eg: dict | None) -> bool:
    """약속 v10 걸음 길: 예외 절에 점검 목록 · 조치 목록이 둘 다 있을 때만(없으면 v8 옛 길)."""
    return bool(eg and eg.get("checks") and eg.get("actions"))


def first_what(eg: dict | None) -> str | None:
    """걸음 길 첫 점검 이름(정답지 11쪽 셋째 칩 · 이유 글) — Pack 절 이름 title은 '예외상황-{주제}'라 칩 이름과 같음."""
    return eg["checks"][0]["what"] if walks(eg) else None


def guide_ex(eg: dict) -> tuple[dict, dict]:
    """engine 사본 함수(walk · base_events)에 넘길 (관계 줄, 절 줄) — exception_guide 칸에서."""
    return {"id": eg["relation_id"]}, {"no": eg["section"], **{f: eg[f] for f in ("checks", "actions") if eg.get(f)}}


def walk_item(eg: dict | None, name: str | None) -> dict | None:
    """걸음 이름(c1 · a2 …) → 그 점검 · 조치 칸(없으면 None)."""
    if not eg or not name or not re.fullmatch(r"[ca][0-9]{1,2}", name):
        return None
    rows = eg.get("checks" if name[0] == "c" else "actions") or []
    i = int(name[1:]) - 1
    return rows[i] if 0 <= i < len(rows) else None


def walk_of(events: list[dict], step: dict) -> list[dict] | None:
    """정답지 26쪽 '안내 차례 · 적은 순서 그대로': 점검 c1 … → 조치 a1 …(약속 v10 5절).
    한 걸음 = 이름 · 무엇 · 지시 문장(say · D-642 C) · 끝남 · 지금.
    지금 = 그 걸음 결정 · 승인 전 호출이면 첫 남은 걸음."""
    eg = exception_guide_of(events)
    if not walks(eg):
        return None
    sec = eg["section"]
    done = {
        e.get("step")
        for e in events
        if e.get("section") == sec and not e.get("void") and e["type"] in {"result", "action_done"} and e.get("step")
    }
    rows = exception.walk(guide_ex(eg)[1])
    now = step.get("step") if step.get("section") == sec else None
    if now is None and step.get("section") == sec and step.get("kind") == "call":
        now = next((n for n, _, _ in rows if n not in done), None)
    return [
        {"step": n, "side": side, "what": item["what"], "say": say_of(item, side), "done": n in done, "now": n == now}
        for n, side, item in rows
    ]


def step_title(e: dict, k: ManualKnowledge, eg: dict | None) -> str:
    """경로 · 지금까지 점검 줄 이름: 걸음 사건이면 그 점검 · 조치 무엇, 아니면 절 이름."""
    item = walk_item(eg, e.get("step")) if e.get("section") == (eg or {}).get("section") else None
    return item["what"] if item else k.sections[e["section"]]["title"]


def with_exception(eg: dict | None, grades: dict, minutes: dict) -> tuple[str | None, dict, dict]:
    """judge 요청 덧칸: 예외 관계 id + 등급표 · 분 표 EX 줄(표에 줄이 없으면 안내 값 = 처음 값 · 표 값이 이김 R13).
    분은 v8 exception_guide.minutes(없으면 줄 없음)."""
    if not eg:
        return None, grades, minutes
    sec = eg["section"]
    if eg.get("minutes") is not None:
        minutes = {**minutes, sec: minutes.get(sec, eg["minutes"])}
    return eg["relation_id"], {**grades, sec: grades.get(sec, eg["grade"])}, minutes


PROGRESS_TITLE = "예외 조치"  # 약속 v9 예외 진행 절 EX-NEW 이름(매뉴얼 · 그래프에 없는 절)


def progress_of(events: list[dict]) -> str | None:
    """약속 v9 [예외] 진행 방식(call_first | act_now) — exception_start가 없으면 None."""
    st = [e for e in events if e["type"] == "exception_start" and not e.get("void")]
    return st[-1].get("mode") if st else None


def grades_of(events: list[dict], g: dict, minutes: dict) -> tuple[str | None, dict, dict]:
    """화면용 등급표 · 분 표: v8 예외 절 줄 + 약속 v9 예외 진행 절 EX-NEW(등급 C)."""
    rel, g, minutes = with_exception(exception_guide_of(events), g, minutes)
    return rel, ({**g, switch.NEW: g.get(switch.NEW, "C")} if progress_of(events) else g), minutes


def knowledge_of(events: list[dict]) -> ManualKnowledge:
    """약속 v8: 세션에 예외 안내가 있으면 매뉴얼 밖 예외 절(EX-…)을 이 세션 지식에 덧붙임 — 지시 · 조치 · 경로 ·
    보고서 · 파트장 화면이 매뉴얼 절과 같은 길로 읽음(본문 = Pack 응답 글 그대로 · 매뉴얼 표는 손 0).
    약속 v9: [예외] 진행이면 절 EX-NEW(본문 없음 · 등급 C)도 덧붙임."""
    k = deps.manual()
    eg = exception_guide_of(events)
    extra = {}
    if progress_of(events):
        extra[switch.NEW] = {
            "id": switch.NEW,
            "title": PROGRESS_TITLE,
            "kind": "exception",
            "chapter": None,
            "group": None,
            "grade": "C",
            "minutes": None,
            "parent": None,
            "text": "",
        }
    if not eg:
        return dataclasses.replace(k, sections={**k.sections, **extra}) if extra else k
    sec = eg["section"]
    steps = "\n".join(f"{i}. {x}" for i, x in enumerate(eg.get("steps") or [], 1))
    crit = f"\n\n**판정 기준** {eg['criteria']}" if eg.get("criteria") else ""
    row = {
        "id": sec,
        "title": eg.get("title") or sec,
        "kind": "exception",
        "chapter": None,
        "group": None,
        "grade": eg.get("grade"),
        "minutes": eg.get("minutes"),
        "parent": None,
        "text": f"#### {sec} 점검 방법\n{eg.get('text') or ''}\n\n{steps}{crit}",
    }
    return dataclasses.replace(k, sections={**k.sections, sec: row, **extra})


def exception_action(c: Connection, events: list[dict], g: dict, minutes: dict) -> dict | None:
    """예외 절 조치 카드(약속 v8: 결과 이상 → 같은 예외 절 조치) — 원인 칩 = 근거 예외 기록, 절차 = 응답 절차 그대로."""
    eg = exception_guide_of(events)
    if not eg:
        return None
    if walks(eg):
        return _walk_action(events, eg, g, minutes)
    sec = eg["section"]
    mine = [e for e in events if e.get("section") == sec and not e.get("void")]
    results = [e.get("value") for e in mine if e["type"] == "result"]
    if not results or results[-1] != "이상":
        return None
    done_e = next((e for e in mine if e["type"] == "action_done"), None)
    basis = list(eg.get("basis") or [])
    return {
        "cause_code": "",
        "cause_name": f"예외 기록 {basis[0]}" if basis else "예외 기록",
        "evidence_record_ids": basis,
        "section": sec,
        "title": eg.get("title") or sec,
        "verb": "조치",
        "grade": g.get(sec, eg.get("grade")),
        "minutes": minutes.get(sec),
        "done_at": (done_e or {}).get("_at"),
        "checklist": [],
        "checklist_done": False,
        "steps": list(eg.get("steps") or []),
    }


def _walk_action(events: list[dict], eg: dict, g: dict, minutes: dict) -> dict | None:
    """약속 v10 걸음 조치 카드: 지금 열린 조치 걸음(a1 …) — 없으면 마지막 끝낸 조치 걸음(재가동 중 보기) ·
    조치 전이면 None.
    절차 = 부품 한 줄(있을 때만) · 분 = 그 조치 칸."""
    sec = eg["section"]
    step = next((e["decision"] for e in reversed(events) if e["type"] == "step_opened"), {})
    done = [
        e
        for e in events
        if e["type"] == "action_done" and e.get("section") == sec and e.get("step") and not e.get("void")
    ]
    name = step.get("step") if step.get("section") == sec and step.get("action") else None
    hit = None if name else (done[-1] if done else None)
    name = name or (hit or {}).get("step")
    item = walk_item(eg, name)
    if not item:
        return None
    return {
        "cause_code": "",
        "cause_name": ex_name_of(eg),
        "evidence_record_ids": list(eg.get("basis") or []),
        "section": sec,
        "title": item["what"],
        "verb": "조치",
        "grade": g.get(sec, eg.get("grade")),
        "minutes": item.get("minutes"),
        "done_at": (hit or {}).get("_at"),
        "checklist": [],
        "checklist_done": False,
        "steps": [f"부품 {', '.join(item['parts'])}"] if item.get("parts") else [],
    }


def ex_name_of(eg: dict) -> str:
    """예외 이름 '예외상황-{주제}'(주제 없으면 이름)."""
    return f"예외상황-{eg.get('topic') or eg.get('title') or ''}"


def progress_action(events: list[dict], step: dict) -> dict | None:
    """약속 v9 예외 진행 조치 카드(절 EX-NEW · 절차 없음 — 정비자가 판단해 조치 · 보고서 예외 칸에 적음)."""
    if not progress_of(events):
        return None
    done_e = next((e for e in events if e["type"] == "action_done" and e.get("section") == switch.NEW), None)
    if done_e is None and (step.get("kind"), step.get("section")) != ("action", switch.NEW):
        return None
    return {
        "cause_code": "",
        "cause_name": "예외",
        "evidence_record_ids": [],
        "section": switch.NEW,
        "title": PROGRESS_TITLE,
        "verb": "조치",
        "grade": "C",
        "minutes": None,
        "done_at": (done_e or {}).get("_at"),
        "checklist": [],
        "checklist_done": False,
        "steps": [],
    }


def exception_guide_view(c: Connection, eg: dict) -> dict:
    """D-543 예외 안내 카드: v8 칸 그대로 + 출처(근거 이력 중 앱 원본에 승인 예외 기록이 있는 첫 건).

    원래 순서 다음(next) = v8 next_section(c1a15-pack d05a64d) · 매뉴얼에 없는 절이면 None.
    원본에 없는 기록은 None(지어내기 0).
    """
    basis = list(eg.get("basis") or [])
    # 약속 v10 걸음 길은 정상이어도 다음 걸음 — '원래 순서 다음' 줄 0(정답지 26쪽 · 덧 2: 재가동 밖일 때만 원래 순서)
    nxt = None if walks(eg) else eg.get("next_section")
    titles = {s: v["title"] for s, v in deps.manual().sections.items()} if nxt else {}
    row = c.execute(
        text(
            "select record_id, equipment_id, occurred_at, record->'exception_record'->>'situation' as situation "
            "from knowledge.history_records where record_id = any(:ids) and record->'exception_record' is not null "
            "order by array_position(:ids, record_id) limit 1"
        ),
        {"ids": basis},
    ).first()
    return {
        **{f: eg.get(f) for f in ("section", "relation_id", "grade", "title", "text", "criteria")},
        "topic": eg.get("topic") or eg.get("title"),  # 고침A '예외상황-{주제}'(Pack 칸 오기 전 = 이름)
        "steps": list(eg.get("steps") or []),
        "checks": list(eg.get("checks") or []),  # 약속 v10 4절: 그 절 점검 목록 · 조치 목록 그대로
        "actions": list(eg.get("actions") or []),
        "basis": basis,
        "source": {
            "record_id": row.record_id,
            "equipment_id": row.equipment_id,
            "occurred_at": row.occurred_at.isoformat(),
            "situation": row.situation or "",
        }
        if row
        else None,
        "next": {"section": nxt, "title": titles[nxt]} if nxt in titles else None,  # 원래 순서 다음(v8 next_section)
    }


RECENT_DAYS = int(os.environ.get("C1A15_RECENT_DAYS", "7"))  # D-570 덧 1: 시연 N 기본 7일(약속 recent.days 1 ~ 30)


def recent_maintenance(c: Connection, alarm: m.Alarm, days: int | None = None) -> list[dict]:
    """D-570(recent-condition.md): 같은 설비 정비 이력 중 [알람 시각 − N일, 알람 시각) 안 전부, 최근 것 먼저 —
    이력 번호 · 시각 · 며칠 전(코드 셈) · 한 조치 절 did · 한 점검 절 checked(앱 번호 · 적은 차례 · 예외 절 뺌) · 결과.
    절 · 며칠 전은 이력 실제 값 그대로(추출기가 이 칸으로 recent{section, days}를 채움 · AI 판단 0)."""
    n = RECENT_DAYS if days is None else days
    rows = c.execute(
        text(
            "select record_id, occurred_at, record from knowledge.history_records where equipment_id = :eq "
            "and occurred_at < :at and occurred_at >= :since order by occurred_at desc, record_id desc"
        ),
        {"eq": alarm.equipment_id, "at": alarm.occurred_at, "since": alarm.occurred_at - timedelta(days=n)},
    ).all()

    def sections(rec: dict, kind: str) -> list[str]:
        secs = [st.get("section") for st in rec.get("steps") or [] if st.get("kind") == kind]
        return list(dict.fromkeys(x for x in secs if x and not str(x).startswith(exception.PREFIX)))

    return [
        {
            "record_id": r.record_id,
            "occurred_at": r.occurred_at.isoformat(),
            "days_before": (alarm.occurred_at - r.occurred_at).days,
            "did": sections(r.record, "action"),
            "checked": sections(r.record, "check"),
            "outcome": (r.record.get("outcome") or {}).get("cause_name") or (r.record.get("outcome") or {}).get("type"),
        }
        for r in rows
    ]


RECENT_WORK_DAYS, RECENT_WORK_MAX = 30, 60  # Pack 5e76c66 약속 v10 덧: 알람 시각 앞 30일 · 60줄 안
SECTION_NO = re.compile(r"^[0-9]\.[0-9](\.[0-9])?$")  # 같은 칸: 앱 절 번호만


def recent_work_of(rows: list[dict]) -> list[dict]:
    """recent_maintenance 줄 → start 요청 recent_work [{record_id, at(초까지), sections(조치 · 점검 앱 번호)}]
    — 절 없는 줄은 뺌."""
    out = []
    for r in rows:
        secs = [x for x in [*r["did"], *r["checked"]] if SECTION_NO.match(x)]
        if secs:
            at = datetime.fromisoformat(r["occurred_at"]).replace(microsecond=0).isoformat()
            out.append({"record_id": r["record_id"], "at": at, "sections": secs})
    return out[:RECENT_WORK_MAX]


def exception_context(c: Connection, alarm: m.Alarm, events: list[dict], cause: str, report_id: int) -> dict:
    """D-538 자동 정보 여덟(입력 0): 설비 · 증상군 · 시각 · 센서값 · 직전 정비 · AI 첫 안내 · 확정 원인 · 보고서 번호
    + D-570 최근 N일 정비 이력 전부.

    지금 데이터에 없는 칸은 None(지어내기 0). 직전 정비 = 같은 설비 정비 이력 중 이 알람 앞 마지막 한 건.
    """
    pv = next((e["preview"] for e in events if e["type"] == "preview_shown" and "preview" in e), None)
    prev = c.execute(
        text(
            "select record_id, occurred_at, record from knowledge.history_records "
            "where equipment_id = :eq and occurred_at < :at order by occurred_at desc, record_id desc limit 1"
        ),
        {"eq": alarm.equipment_id, "at": alarm.occurred_at},
    ).first()
    previous = None
    if prev is not None:
        act = next((s for s in prev.record.get("steps", []) if s.get("kind") == "action"), None)
        oc = prev.record.get("outcome") or {}
        previous = {
            "record_id": prev.record_id,
            "occurred_at": prev.occurred_at.isoformat(),
            "outcome": oc.get("cause_name") or oc.get("type"),  # 확정 원인, 없으면 결과 종류(원인 미상 · 감시)
            "action": f"{act['name']}({act['section']})" if act else None,
        }
    return {
        "equipment_id": alarm.equipment_id,
        "symptom_group": {"code": alarm.symptom_group, "name": deps.group_names()[alarm.symptom_group]},
        "occurred_at": alarm.occurred_at.isoformat(),
        "sensor_values": {"alarm": alarm.alarm, "snapshot": alarm.sensor_snapshot},
        "previous_maintenance": previous,
        "recent_maintenance": recent_maintenance(c, alarm),  # D-570: 같은 설비 최근 N일 정비 이력 전부
        "ai_first_check": {"section": pv["section"], "title": pv["title"]} if pv and pv.get("section") else None,
        "confirmed_cause": cause,
        "report_id": report_id,
    }


def report_view(
    c: Connection, row: m.DiagSession, alarm: m.Alarm, state: session.SessionState, step: dict, events: list[dict]
) -> dict:
    stored = c.execute(select(m.Report.__table__).where(m.Report.session_id == row.session_id)).first()
    k = knowledge_of(events)
    reopened = stored is not None and stored.status == "rejected" and not state.report_confirmed
    if stored and not reopened:
        fields = stored.fields
        status, confirmed_at = stored.status, stored.confirmed_at
    else:
        facts = report_draft(c, row, alarm, state, step, events, None)
        key = draft_key(row, events, report_draft(c, row, alarm, state, step, events, None, grades={}))
        sent, sent_seq = report_sentences(row, events, facts, key)
        facts |= {k: sent[k]["text"] for k in REPORT_SENTENCES}
        fallback = {k: bool(sent[k]["fallback"]) for k in REPORT_SENTENCES}
        gr = _report_grounding(row, events, facts)
        edits = {}
        rejected_seq = (state.report_rejected_seq or 0) if reopened else 0
        before = next(  # 반려 전 마지막 문장 기록 — 없으면(이 판 전 세션) FLOW-06 그대로
            (e for e in reversed(events) if e["type"] == "report_written" and e["seq"] < rejected_seq), None
        )
        # CH-20: 반려 뒤 추가 점검 · 정정으로 초안 열쇠가 반려 전과 달라졌으면 새 초안 · 새 문장(A7 · A13)
        redrafted = reopened and before is not None and before.get("draft_key") != key
        since = max(rejected_seq, sent_seq)  # 정비자 수정은 그 문장 기록(또는 반려) 뒤 것만
        if reopened and not redrafted:  # FLOW-06: 반려된 보고서는 확정했던 문장에서 다시 고친다(그 뒤 수정만 새로 검사)
            base = {f["key"]: f["text"] for f in stored.fields}
            facts = {key: base.get(key, facts[key]) for key in facts}
            fallback = {f["key"]: bool(f.get("fallback")) for f in stored.fields if f["key"] in REPORT_SENTENCES}
            gr = _report_grounding(row, events, {**facts, **base})
        for e in events:
            if e["type"] == "report_edit" and not e.get("void") and e["seq"] > since:
                edits[e["field"]] = e["text"]
        fields = []
        for key, label, source in REPORT_FIELDS:
            text = edits.get(key, facts[key])
            ok = key not in edits or grounding.is_grounded(Draft(text), gr)
            missing = sorted(grounding.numbers_in(text) - gr.source_numbers) if key in edits else []
            fields.append(
                {
                    "key": key,
                    "label": label,
                    "source": source,
                    "text": text,
                    "edited": key in edits,
                    "ok": ok,
                    "unsupported_numbers": missing,
                }
                | ({"fallback": fallback.get(key, False) and key not in edits} if key in REPORT_SENTENCES else {})
            )
        status, confirmed_at = "draft", None
    failures = [f for f in fields if not f["ok"]]
    authority_ok = _authority_ok(events, k)
    required_ok = all(f["text"].strip() for f in fields)
    checks = [
        {
            "key": "grounded",
            "label": "근거 없는 문장 없음" if not failures else f"근거 없는 문장 {len(failures)}건",
            "ok": not failures,
        },
        {
            "key": "authority",
            "label": "권한 등급 위반 없음" if authority_ok else "권한 등급 위반 있음",
            "ok": authority_ok,
        },
        {
            "key": "required",
            "label": "필수 8항목 모두 채워짐" if required_ok else "필수 항목이 비어 있음",
            "ok": required_ok,
        },
    ]
    fix = "문장을 고치거나 지우십시오."

    def quote(f: dict) -> str:  # T09 / check_failed: 근거 없는 수치가 든 문장 조각을 인용
        parts = [p for p in f["text"].split(" · ") if any(n in p for n in f["unsupported_numbers"])]
        return (parts[0] if parts else f["text"])[:60]

    messages = [
        f'"{quote(f)}"의 수치 {", ".join(f["unsupported_numbers"])}가 이번 세션 기록에 없습니다. {fix}'
        if f["unsupported_numbers"]
        else f'"{quote(f)}"의 절 번호 · 근거 기록이 이번 세션 기록에 없습니다. {fix}'
        for f in failures
    ]
    rejection = stored.rejection if stored is not None and stored.rejection else None
    return {
        "status": status,
        "report_id": stored.report_id if stored is not None else None,  # D-528 「최종본 보기」(확정한 보고서 행)
        "rejection": (
            {
                **{k2: rejection.get(k2) for k2 in ("reason", "by", "at")},
                "by_name": c.execute(
                    select(m.Account.display_name).where(m.Account.user_id == rejection.get("by"))
                ).scalar(),
            }
            if rejection
            else None
        ),
        "fields": fields,
        "checks": checks,
        "messages": messages,
        "can_confirm": status == "draft" and all(x["ok"] for x in checks),
        "confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
        "exception_record": record_out(exception_of(events)),
        "exception_draft": exception_draft_of(events),
        "exception_auto": exception_auto(fields, events),  # 확정 전 칸에 보일 상황 · 결과
        "exception_split": exception_split_of(events),  # D-556 「AI 나눔」 마지막 결과(확정 전)
        "exception_session": progress_of(events) is not None,  # 고침A 18쪽: 예외 진행 세션만 팝업 · 예외 구역
        "exception_applied": record_out(
            exception_applied_of(events)
        ),  # 정답지 18 · 19쪽: 「보고서에 적용」한 것(확정 전)
        "low_evidence": rules.low_evidence(
            session.fold(k, row.symptom_group, [rules.Similar(**s) for s in row.similar], []).similar
        ),
    }


def _authority_ok(events: list[dict], k: ManualKnowledge) -> bool:
    """확정 전 확인 ②: 결과 · 조치는 그때 지시로 열린 절에만(R2). 서버가 이미 거부하므로 기록 대조."""
    opened = set()
    for e in events:
        if e["type"] == "step_opened" and e["decision"].get("kind") in {
            "instruct",
            "instruct_after_checklist",
            "action",
            "action_after_checklist",
        }:
            opened.add(e["decision"]["section"])
        if e["type"] in {"result", "action_done"} and not e.get("void") and e.get("section") not in opened:
            return False
    return True


# ---------------------------------------------------------------- 입력 검증 · 부수 효과


def accept(
    c: Connection,
    row: m.DiagSession,
    actor: str,
    step: dict,
    state: session.SessionState,
    events: list[dict],
    etype: str,
    payload: dict,
) -> tuple[str, dict]:
    """S-03 입력을 검사하고 (저장할 업무 기록 type, payload)를 돌려준다. 거부는 409."""
    k = knowledge_of(events)
    kind = step.get("kind")

    def reject(reason: str):
        raise HTTPException(status_code=409, detail=reason)

    if state.report_confirmed:
        reject("session_closed")
    if etype == "exception_draft":  # D-543 「노하우 등록」: 칸만 남김 → 보고서 칸에 채워져 보고서와 함께 확정 · 승인
        if not state.locked:
            reject("not_locked")
        text = exception_draft_text(payload.get("exception_record"))
        return "exception_draft", {"exception_record": {"text": text} if text else None}
    if etype == "exception_split":  # D-556 1단계 「AI 나눔」: 보고서 단계 · 확정 전만 → 배포 Run(임시 kind) → 줄
        if kind != "report":
            reject("no_report_step")
        text = (payload.get("exception_text") or "").strip()
        if not text:
            reject("exception_text_required")
        alarm = sessions_alarm(c, row)
        seq = (events[-1]["seq"] if events else 0) + 1
        try:
            res = runs.split(alarm, row.symptom_group, text, seq)
        except PackUnavailable as e:  # 나누지 못함 — 보고서는 막히지 않음(글을 지우면 확정 가능 · 다시 누르면 새 Run)
            return "exception_split", {"text": text, "exception": None, "error": "pack_unavailable", "run_id": e.run_id}
        ex = split_exception(res.output)
        if ex is None:  # 나누지 못함(한쪽 0 · 꼴 밖) — Run 실패와 같이 받음(약속 v10 2절)
            reason = split_reason(res.output)
            return "exception_split", {"text": text, "exception": None, "error": reason, "run_id": res.run_id}
        out = {"text": text, "exception": ex, "error": None, "similar_error": None, "run_id": res.run_id}
        found: list[dict] = []
        try:  # 약속 v10 3절: 예외 통째로 비슷한 것 찾기 한 번(실패하면 비슷한 것 없이 — 아님만 고를 수 있음)
            sim = runs.exception_similar(alarm, row.symptom_group, similar_query(ex), seq)
            found = similar_matches(sim.output)
            out["similar_run_id"] = sim.run_id
        except PackUnavailable as e:
            out["similar_error"], out["similar_run_id"] = "pack_unavailable", e.run_id
        out["exception"] = {**ex, "matches": found}
        return "exception_split", out
    if etype == "exception_apply":  # 고침A 18쪽 「보고서에 적용」: 지금 글의 나눔 · 같음 / 아님을 확정 전 보고서에 붙임
        if kind != "report" or state.report_confirmed:
            reject("no_report_step")
        text = ((payload.get("exception_record") or {}).get("text") or "").strip()
        if not text:
            reject("exception_text_required")  # 빈 글이면 적용 0(화면 단추 잠김과 같음)
        split = exception_split_of(events)
        return "exception_apply", {
            "text": text,
            **exception_review(text, split, payload.get("exception_review")),
            "split_error": split["error"] if split and split.get("error") else None,
        }
    if etype == "handover_request":  # AC-31-C2 · S6: 문장 생성 3회 연속 실패로 인계를 권할 때만 정비자가 고른다
        from c1a15.api import sessions as svc

        if not grounding.should_recommend_handover(svc.llm_failures(events)) or kind in {
            "handover",
            "report",
            "closed",
        }:
            reject("no_handover_recommendation")
        return "handover_request", {"reason": "llm_failure"}
    if etype == "action_done":
        if kind not in {"action", "action_after_checklist"} or payload.get("section") != step.get("section"):
            reject("no_action_step")
        if kind == "action_after_checklist" and step["section"] not in state.b_checklist_done:
            reject("b_checklist_required")  # AC-32-C2 · AC-43-C4
        if step["section"] in state.actions_done:
            reject("already_done")
        # 약속 v10 5절: 예외 안내 조치 걸음이면 사건에 그 걸음 이름(a1 …) — 판정 결정이 정한 것을 그대로
        return "action_done", {"section": step["section"], **({"step": step["step"]} if step.get("step") else {})}
    if etype == "restart":
        if kind != "restart_check":
            reject("no_restart_step")
        if sorted(payload.get("checked") or []) != sorted(checklist_of(k, c, session.RESTART_SECTION)):
            reject("checklist_incomplete")  # AC-21-C1
        return "restart", {"checked": payload["checked"]}
    if etype == "restart_result":
        if kind != "restart_value" or payload.get("outcome") not in {"within", "outside"}:
            reject("no_restart_value_step")
        return "restart_result", {"outcome": payload["outcome"]}
    if etype == "monitor_close":
        if kind != "monitor_close":
            reject("no_monitor_step")
        return "monitor_close", {}
    if etype in {"call_send", "call_wait"}:
        if kind not in {"call", "handover"} or step.get("call_status") == "sent":
            reject("no_call_step")
        corrected = {"corrected": True} if step.get("corrected") else {}  # T10 / summary_corrected(AC-47-C1)
        if etype == "call_wait":
            if kind != "call":
                reject("no_call_step")
            return "call_wait", {"section": step.get("section"), **corrected}
        alarm = sessions_alarm(c, row)
        text = (payload.get("summary") or "").strip() or call_summary(alarm, state, k, step, events)["text"]
        call_kind = "handover" if kind == "handover" else "approval"
        cor = correction_of(events, step) if corrected else None
        call_id = c.execute(
            m.Call.__table__.insert()
            .values(
                session_id=row.session_id,
                kind=call_kind,
                section=step.get("section"),
                grade=None if corrected else step.get("grade"),
                summary=text,
                sent_by=actor,
                correction=cor,
                change_seq=deps.next_change_seq(c),
            )
            .returning(m.Call.call_id)
        ).scalar_one()
        return "call_sent", {
            "kind": call_kind,
            "section": step.get("section"),
            "grade": None if corrected else step.get("grade"),
            "call_id": call_id,
            "summary": text,
            **corrected,
        }
    if etype == "report_edit":
        if kind != "report":
            reject("no_report_step")
        if payload.get("field") not in FIELD_KEYS or payload.get("text") is None:
            reject("invalid_report_field")
        return "report_edit", {"field": payload["field"], "text": payload["text"]}
    if etype == "report_confirm":
        if kind != "report":
            reject("no_report_step")
        alarm = sessions_alarm(c, row)
        view = report_view(c, row, alarm, state, step, events)
        if not view["can_confirm"]:  # AC-07-C1: 실패 항목이 있으면 확정 거부
            raise HTTPException(status_code=409, detail={"reason": "precheck_failed", "checks": view["checks"]})
        ex_in = payload.get("exception_record") or {}
        ex_text = (ex_in.get("text") or "").strip()
        review = payload.get("exception_review")
        split = exception_split_of(events)
        applied = exception_applied_of(events)
        if not ex_text and applied:  # 정답지 19쪽: 팝업에서 「보고서에 적용」한 것을 함께 확정
            ex_text, split = applied["text"], {"error": applied.get("split_error")}
            part = {k: v for k, v in applied.items() if k not in {"text", "split_error"}}
        else:
            if not ex_text and progress_of(events):  # 예외 진행 세션은 예외 상황을 적용해야 확정(정답지 18쪽)
                raise HTTPException(status_code=409, detail={"reason": "exception_apply_needed"})
            part = exception_review(ex_text, split, review) if ex_text else {}
        now = datetime.now(UTC)
        fields = view["fields"]
        for f in fields:  # 확정 때 정지 시간 · 시각을 확정 시각으로 고정
            if f["key"] in {"equipment_alarm", "downtime"} and not f["edited"]:
                f["text"] = report_draft(c, row, alarm, state, step, events, now)[f["key"]]
        values = {
            "author": actor,
            "status": "awaiting_approval",
            "fields": fields,
            "low_evidence": view["low_evidence"],
            "confirmed_at": now,
            "updated_at": now,
            "change_seq": deps.next_change_seq(c),
        }
        if c.execute(select(m.Report.report_id).where(m.Report.session_id == row.session_id)).first():
            c.execute(  # 반려 뒤 다시 확정(AC-11-C2): 같은 보고서 행을 승인 대기로(반려 기록은 남김)
                m.Report.__table__.update().where(m.Report.session_id == row.session_id).values(**values)
            )
        else:
            c.execute(m.Report.__table__.insert().values(session_id=row.session_id, **values))
        c.execute(
            m.DiagSession.__table__.update().where(m.DiagSession.session_id == row.session_id).values(status="reported")
        )
        c.execute(
            m.Alarm.__table__.update()
            .where(m.Alarm.alarm_id == row.alarm_id)
            .values(status="done", change_seq=deps.next_change_seq(c))
        )
        out = {"confirmed_at": now.isoformat(), "exception_record": None}
        if ex_text:  # D-538: 정비자가 예외라고 판단해 적었을 때만 — 자동 정보는 서버가 확정 때 붙임
            rid = c.execute(select(m.Report.report_id).where(m.Report.session_id == row.session_id)).scalar()
            cause = next((f["text"] for f in fields if f["key"] == "cause"), "")
            auto = exception_auto(fields, events)  # 상황 · 결과 자동
            out["exception_record"] = {
                "situation": auto["situation"],
                "text": ex_text,  # 정비자 글 그대로
                # 승인 기록 파일 칸(약속 v10 6절): exception = 1절 꼴 하나 · exception_review {decision, match} 한 번
                # (나누지 못함이면 둘 다 null — 글 그대로 새 예외) · exception_matches = 그때 보인 비슷한 것(앱 칸)
                **part,
                **({"split_error": split["error"]} if split and split.get("error") else {}),  # 나누지 못함 → 새 예외
                "result": auto["result"],
                "context": exception_context(c, alarm, events, cause, rid),
            }
        return "report_confirm", out
    reject("unknown_event")
    raise AssertionError


def sessions_alarm(c: Connection, row: m.DiagSession) -> m.Alarm:
    return m.Alarm(**c.execute(select(m.Alarm.__table__).where(m.Alarm.alarm_id == row.alarm_id)).first()._mapping)


# ---------------------------------------------------------------- 세션 응답의 S-03 블록


def _call_title(events: list[dict], step: dict, k: ManualKnowledge, eg: dict, sec: str | None) -> str | None:
    """호출 카드 제목: 절 이름 · 약속 v10 걸음 길 예외 절이면 지금(첫 남은) 걸음 무엇(정답지 26쪽)."""
    now = next((w for w in walk_of(events, step) or [] if w["now"]), None) if sec == eg.get("section") else None
    if now:
        return now["what"]
    return k.sections[sec]["title"] if sec in k.sections else (eg if eg.get("section") == sec else {}).get("title")


def flow_view(c: Connection, row: m.DiagSession, state: session.SessionState, step: dict, events: list[dict]) -> dict:
    k = knowledge_of(events)
    g, minutes = deps.grades(c)
    _, g, minutes = grades_of(events, g, minutes)
    alarm = sessions_alarm(c, row)
    kind = step.get("kind")
    out: dict = {"path": path(events, k, step, alarm)}
    fixed = next(  # T08 / corrected_path: 정정으로 다시 판정한 점검(정정 기록은 P-14 · S-05가 만든다)
        (e for e in reversed(events) if e["type"] == "result" and e.get("corrected_from") and not e.get("void")), None
    )
    rejudged = fixed and (fixed.get("corrections") or [{}])[-1].get("mode", "rejudge") == "rejudge"
    if rejudged and fixed["corrected_from"].get("value") != fixed.get("value"):
        out["correction"] = {
            "section": fixed["section"],
            "title": k.sections[fixed["section"]]["title"],
            "at": fixed["corrected_from"].get("at"),
            "before": fixed["corrected_from"].get("value"),
            "after": fixed.get("value"),
        }
    if state.confirmed:
        cause = k.cause(state.confirmed)
        ac = cause.action_section
        done_e = next(
            (e for e in events if e["type"] == "action_done" and e.get("section") == ac and not e.get("void")), None
        )
        confirming = cause.confirm_check
        needs = g.get(ac) == "B"
        out["action"] = {
            "cause_code": cause.code,
            "cause_name": cause.name,
            "evidence_record_ids": screen.evidence_record_ids(k, confirming, row.similar),
            "section": ac,
            "title": k.sections[ac]["title"],
            "verb": k.sections[ac]["title"].split()[-1],  # 버튼 '{동사} 완료'(T08: 흡기 필터 교체 → 교체 완료)
            "grade": g.get(ac),
            "minutes": minutes.get(ac),
            "done_at": (done_e or {}).get("_at"),
            "checklist": checklist_of(k, c, ac) if needs else [],
            "checklist_done": ac in state.b_checklist_done,
            "steps": screen.procedure_steps(
                k, ac
            ),  # D-530 답장 001-zzy(가이드 14쪽): 조치 카드 절차 — 매뉴얼 번호 줄 그대로
        }
    elif (ex_act := exception_action(c, events, g, minutes) or progress_action(events, step)) is not None:
        out["action"] = ex_act  # 약속 v8 예외 절 이상 → 조치 · v9 예외 진행 조치
    if kind in {"restart_check", "restart_value", "monitor_close", "report"} or state.restarted:
        restart_e, result_e = _restart_events(events)
        out["restart"] = {
            "section": session.RESTART_SECTION,
            "title": k.sections[session.RESTART_SECTION]["title"],
            "grade": g.get(session.RESTART_SECTION),
            "checklist": checklist_of(k, c, session.RESTART_SECTION),
            "restarted_at": (restart_e or {}).get("_at"),
            "outcome": (result_e or {}).get("outcome"),
            **({"value": restart_block(alarm, bool(state.confirmed))} if restart_e else {}),
        }
    if kind == "monitor_close" or state.monitor_closed:
        out["monitor"] = {
            "section": session.MONITOR_SECTION,
            "title": k.sections[session.MONITOR_SECTION]["title"],
            "grade": g.get(session.MONITOR_SECTION),
            "closed": state.monitor_closed,
        }
    ex_answer = next(  # 예외 절 호출의 답(로컬 접기는 예외 절 사건을 뺌 — 약속 v8)
        (
            e
            for e in reversed(events)
            if e["type"] == "call_answer"
            and not e.get("void")
            and str(e.get("section") or "").startswith(exception.PREFIX)
        ),
        None,
    )
    answer = state.call_answer or ex_answer or {}
    approved_open = answer.get("answer") == "approved" and step.get("section") == answer.get("section")
    # T10 ans_approved: 승인 배너와 그 C 점검 지시가 호출 카드에 함께 열린다
    if (
        kind in {"call", "handover", "call_direct"}
        or approved_open
        or (kind == "report" and (state.call_answer or state.handover_sent))
    ):
        last_call = next((e for e in reversed(events) if e["type"] == "call_sent" and not e.get("void")), None)
        # 정정 호출이면(지금 단계 또는 답이 온 뒤 보고서 단계) 정정 · 조치 값을 함께(T10 / summary_corrected · 019 ①)
        cor = correction_of(events, step) if step.get("corrected") or (last_call or {}).get("corrected") else None
        scope = [e for e in events if e["seq"] > cor["seq"]] if cor else events  # 정정 호출은 그 정정 뒤 기록만
        call_e = next((e for e in reversed(scope) if e["type"] == "call_sent" and not e.get("void")), None)
        wait_e = next((e for e in reversed(scope) if e["type"] == "call_wait" and not e.get("void")), None)
        ans_e = next((e for e in reversed(scope) if e["type"] == "call_answer" and not e.get("void")), None)
        if kind in {"call", "handover"} and not step.get("call_status"):
            # 아직 보내지도 기다리지도 않은 새 호출 단계 — 같은 세션 앞 호출의 보냄 · 대기 · 답을 붙이지 않음
            # (S-06 시연에서 발견: 승인 답 뒤 두 번째 호출)
            call_e = wait_e = ans_e = None
        elif call_e and ans_e and ans_e.get("call_id") != call_e.get("call_id"):
            ans_e = None  # 답은 그 호출 것만
        sec = step.get("section") or (call_e or {}).get("section")
        eg = exception_guide_of(events) or {}  # 약속 v8 예외 안내(호출 절이 예외 절일 때만 카드)
        summary = {"text": call_e["summary"], "fallback": False, "label": None} if call_e else None
        if kind in {"call", "handover"} or summary is None:
            gen = call_summary(
                alarm,
                state,
                k,
                {
                    **step,
                    "kind": "handover" if (call_e or {}).get("kind") == "handover" or kind == "handover" else "call",
                    "section": sec,
                    "grade": step.get("grade") or (call_e or {}).get("grade"),
                },
                events,
            )
            summary = summary or {k2: gen[k2] for k2 in ("text", "fallback", "label")}
            checks, cands = gen["checks_done"], gen["candidates"]
        else:
            checks, cands = checks_done(events, k), _candidates(state, k, step)
        status = "answered" if ans_e else "sent" if call_e else "waiting" if wait_e else "none"
        out["call"] = {
            "kind": "handover" if kind == "handover" or (call_e or {}).get("kind") == "handover" else "approval",
            "reason": step.get("reason"),
            "head": handover_head(step, state, k) if kind == "handover" else None,
            "section": sec,
            "title": _call_title(events, step, k, eg, sec),
            "grade": (call_e or {}).get("grade")
            if approved_open
            else step.get("grade") or (call_e or {}).get("grade") or (g.get(sec) if sec else None),
            "minutes": minutes.get(sec) if sec else None,
            "checks_done": checks,
            "candidates": cands,
            "summary": summary,
            "status": status,
            "waiting_at": (wait_e or {}).get("_at"),
            "sent_at": (call_e or {}).get("_at"),
            "answer": (
                {k2: ans_e.get(k2) for k2 in ("answer", "by", "at", "note")}
                | {
                    "by_name": c.execute(
                        select(m.Account.display_name).where(m.Account.user_id == ans_e.get("by"))
                    ).scalar()
                }
                if ans_e
                else None
            ),
            "exception_guide": exception_guide_view(c, eg)
            if kind == "call" and not cor and eg.get("section") == sec
            else None,
        }
        if cor:  # T10 / summary_corrected: 정정 · 조치 칩, 지금까지 점검 · 조치, 남은 후보는 다시 계산하지 않음
            out["call"].update(
                correction={k2: cor[k2] for k2 in ("section", "title", "before", "after", "reason", "at")},
                actions_done=actions_of(events, k),
                candidates=[],
                grade=None,
                minutes=None,
            )
    if kind in {"report", "closed"}:
        out["report"] = report_view(c, row, alarm, state, step, events)
    return out
