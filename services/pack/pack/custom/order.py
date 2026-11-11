"""점검 순서 덧층(Pack 옵션 check_first_<증상군> · D-565 덧 3 · 2단계 #7) — 순수 함수(망 · 파일 · DB 0). 사본 session · rules는 손 0(바깥에서 덧댐).
꼴 정본 = orchestration agents/c1a15 handoff/d565/check-before.md.
- 옵션 = 증상군마다 하나 check_first_tw · _pw · _cl · _ld(choice) · 값 = **먼저 볼 점검 절 하나 X**(그 증상군 A · B 점검 절) · 기본 none = 사본 판정 그대로.
  packrt choice 선택지는 이름 꼴(^[a-z][a-z0-9_]*$)만 받으므로 절은 `s<절 번호의 . → _>`로 적음(예 s4_5 = 4.5) · none = 없음.
- 사본이 best_pick으로 낸 A · B 점검 지시 Y(≠ X)에만: X가 고를 수 있는 점검(selectable)이면 **Y 자리에 X**(헛걸음 고침 = 사람이 고른 X를 다음엔 먼저) ·
  X를 고를 수 없으면(이미 함 · 원인 빠짐 · 등급 C · D · 확인 불가 2회) 그대로 · 나머지 차례는 손 0.
- 못 바꿈: 안전 단계 · 등급 규칙(C · D 호출 · 승인된 C 점검 먼저) · 원인 확률 · 예외 안내 · 점검 바꾸기(이 덧층 뒤에 덧댐).
- 그 증상군 점검 절이 아닌 값 · 모르는 증상군 옵션 이름은 OrderError(Run 실패). 여러 짝(pairs 형)은 11-06 뒤."""
from custom import rules

KINDS = {"instruct", "instruct_after_checklist"}
PREFIX = "check_first_"
NONE = "none"


class OrderError(ValueError):
    pass


def choice_name(section):
    """절 번호 → choice 이름(4.5 → s4_5)."""
    return "s" + section.replace(".", "_")


def section_of(value):
    """choice 이름 → 절 번호(s4_5 → 4.5) · none · 빈 값 → None."""
    if value in (NONE, "", None):
        return None
    if not (isinstance(value, str) and value.startswith("s")):
        raise OrderError(f"먼저 볼 점검 값 꼴이 다름(s<절> · none): {value!r}")
    return value[1:].replace("_", ".")


def firsts(options, k):
    """옵션 → {증상군 코드: 먼저 볼 점검 절} · 뜻 검사(그 증상군 점검 절만)."""
    out = {}
    for name, value in (options or {}).items():
        if not name.startswith(PREFIX):
            continue
        group = name[len(PREFIX):].upper()
        if group not in k.checks:
            raise OrderError(f"모르는 증상군 옵션: {name}")
        sec = section_of(value)
        if sec is None:
            continue
        if sec not in {c.section_id for c in k.checks[group]}:
            raise OrderError(f"{name} 값이 그 증상군 점검 절이 아님: {value!r}")
        out[group] = sec
    return out


def apply(d, state, k, grades, minutes, options):
    """사본 판정 d → 먼저 볼 점검을 덧댄 판정(바뀌면 reordered{from, to, by})."""
    first = firsts(options, k).get(state.group)
    if not first or d.get("kind") not in KINDS or d.get("section") in (first, *state.approvals):
        return d
    probs = d.get("probabilities") or {}
    picks = rules.selectable_checks(k, state.group, probs, state.done, state.unable, grades, minutes, state.approvals)
    natural = rules.best_pick(picks, state.unable)
    if natural is None or natural.check.section_id != d.get("section"):
        return d   # 사본이 best_pick 말고 다른 까닭으로 고른 결정은 손 0
    pick = next((p for p in picks if p.check.section_id == first), None)
    if pick is None:
        return d   # X를 고를 수 없음 → 그대로
    alternatives = [{"section": c.section_id, "grade": grades[c.section_id]}
                    for c in k.checks[state.group] if c.section_id != first and c.section_id not in state.done and c.cause in probs]
    return {**d, "kind": "instruct" if pick.grade == "A" else "instruct_after_checklist", "section": first, "cause": pick.check.cause, "grade": grades[first],
            "reason_facts": {**(d.get("reason_facts") or {}), "probability": pick.probability, "minutes": pick.minutes, "grade": pick.grade, "alternatives": alternatives,
                             "picked_by": {"option": f"{PREFIX}{state.group.lower()}", "section": first, "title": k.sections[first]["title"],   # D-652 12-f: 이유 글이 '옵션으로 정해 둔 점검'이라 말하게(값 = 옵션 · 매뉴얼)
                                           "instead_of": d["section"], "instead_title": k.sections[d["section"]]["title"]}},
            "unable_before": state.unable.get(first, 0), "reordered": {"from": d["section"], "to": first, "by": f"{PREFIX}{state.group.lower()}"}}
