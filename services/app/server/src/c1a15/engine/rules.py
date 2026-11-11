"""판정 규칙 순수 함수(명세 4장 R2 · R4 · R5 · R6 · R9 · R13). 입력은 값뿐이고 부작용이 없다."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from c1a15.engine.knowledge import Cause, Check, ManualKnowledge, section_key

ALPHA = 0.5  # R4 [기본안]
CALL_THRESHOLD = 50  # R5 call_threshold(서버 설정 값)
LOW_EVIDENCE_SCORE = 0.5  # R6
LOW_EVIDENCE_COUNT = 3  # R6 [기본안]
INSTRUCTABLE = {"A", "B"}
SCORE_RULES = ("sum", "rank_weighted", "near_only")  # R4 셈 꼴(D-476 Pack 옵션 score_rule)


@dataclass(frozen=True)
class Similar:
    """R3 상위 10건의 한 기록: 확정 원인과 score(0~1)."""

    record_id: str
    cause: str | None
    score: float


def cause_scores(
    candidates: Iterable[str],
    similar: Iterable[Similar],
    alpha: float = ALPHA,
    rule: str = "sum",
    near_min: float = 0.8,
    manual: Mapping[str, float] | None = None,
    manual_weight: float = 0.0,
) -> dict[str, float]:
    """R4: 점수(c) = α + Σ w(r) (+ manual_weight × manual[c]), r = 상위 10건 중 확정 원인이 c인 기록.

    sum: w = score(지금) · rank_weighted: w = 1 / 순위(similar 들어온 순서, 1부터)
    near_only: w = score, score ≥ near_min만.
    manual: {원인 코드: 매뉴얼 몫} 또는 None(지금). 매개변수 = Pack 옵션(D-476) — 앱은 늘 기본값.
    """
    if rule not in SCORE_RULES:
        raise ValueError(f"모르는 셈 꼴: {rule}")
    scores = {c: alpha for c in candidates}
    for rank, r in enumerate(similar, start=1):
        if r.cause not in scores:
            continue
        if rule == "sum":
            scores[r.cause] += r.score
        elif rule == "rank_weighted":
            scores[r.cause] += 1 / rank
        elif r.score >= near_min:
            scores[r.cause] += r.score
    for c, v in (manual or {}).items():
        if c in scores:
            scores[c] += manual_weight * v
    return scores


def evidence_counts(candidates: Iterable[str], similar: Iterable[Similar]) -> dict[str, int]:
    """R4 '근거 n' = 상위 10건 중 원인 c인 기록 수."""
    similar = list(similar)
    return {c: sum(1 for r in similar if r.cause == c) for c in candidates}


def round_to_100(scores: Mapping[str, float]) -> dict[str, int]:
    """R4 확률(%) = 점수 ÷ 합 × 100, 합이 100이 되게 반올림 조정(큰 나머지 순, 같으면 후보 순서)."""
    if not scores:
        return {}
    total = sum(scores.values())
    raw = {c: s / total * 100 for c, s in scores.items()}
    floors = {c: int(v) for c, v in raw.items()}
    order = sorted(raw, key=lambda c: (-(raw[c] - floors[c]), list(raw).index(c)))
    for c in order[: 100 - sum(floors.values())]:
        floors[c] += 1
    return floors


def low_evidence(similar: Iterable[Similar]) -> bool:
    """R6: 상위 10건 중 score ≥ 0.5인 기록이 3건 미만이면 근거 적음."""
    return sum(1 for r in similar if r.score >= LOW_EVIDENCE_SCORE) < LOW_EVIDENCE_COUNT


def grade_decision(grade: str) -> str:
    """R2: A 지시, B 체크리스트 확인 후 지시, C · D 지시하지 않음(호출)."""
    return {"A": "instruct", "B": "instruct_after_checklist"}.get(grade, "call")


def effective_grade(section_id: str, grades: Mapping[str, str], approvals: Iterable[str]) -> str:
    """현재 등급(R13: 판정 시점의 등급표). 파트장 '승인 후 진행'은 그 C 작업 하나만 연다(D는 예외 없음).

    승인 범위 해석(v13 Human Gate): 같은 작업 재지시는 같은 승인, 그 절이 D가 되면 승인 무효, B가 되면 체크리스트 먼저.
    """
    grade = grades[section_id]
    if grade == "C" and section_id in set(approvals):
        return "A"
    return grade


@dataclass(frozen=True)
class Pick:
    check: Check
    probability: int
    minutes: int
    grade: str
    value: float  # R5 점수 = 확률 ÷ 소요 분


def selectable_checks(
    k: ManualKnowledge,
    group: str,
    probs: Mapping[str, int],
    done: set[str],
    unable: Mapping[str, int],
    grades: Mapping[str, str],
    minutes: Mapping[str, int],
    approvals: Iterable[str],
) -> list[Pick]:
    """R5-2: 아직 하지 않았고, 확인 불가 2회가 아니며, 연결 원인이 후보에 남아 있고, 현재 등급이 A · B인 점검."""
    approvals = set(approvals)
    picks = []
    for check in k.checks.get(group, []):
        sid = check.section_id
        if sid in done or unable.get(sid, 0) >= 2 or check.cause not in probs:
            continue
        grade = effective_grade(sid, grades, approvals)
        if grade not in INSTRUCTABLE:
            continue
        picks.append(Pick(check, probs[check.cause], minutes[sid], grade, probs[check.cause] / minutes[sid]))
    return picks


def best_pick(picks: list[Pick], unable: Mapping[str, int]) -> Pick | None:
    """R5-3: 점수가 가장 큰 것, 같으면 절 번호가 앞선 것.

    AC-19-C1(확인 불가 뒤 다른 점검이 제안된다): 확인 불가 1회인 점검은 다른 후보가 없을 때만 다시 고른다(실행 해석 ④).
    """
    fresh = [p for p in picks if unable.get(p.check.section_id, 0) == 0]
    pool = fresh or picks
    if not pool:
        return None
    return sorted(pool, key=lambda p: (-p.value, section_key(p.check.section_id)))[0]


def top_cause(probs: Mapping[str, int], order: list[str]) -> str | None:
    if not probs:
        return None
    return sorted(probs, key=lambda c: (-probs[c], order.index(c)))[0]


def cause_checks_open(
    k: ManualKnowledge, group: str, cause: str, done: set[str], unable: Mapping[str, int]
) -> list[Check]:
    """그 원인을 확인할 점검 중 아직 하지 않았고 확인 불가 2회가 아닌 것(절 번호 순)."""
    return [
        c
        for c in k.checks.get(group, [])
        if c.cause == cause and c.section_id not in done and unable.get(c.section_id, 0) < 2
    ]


def assign(equipment_id: str, assignments: Mapping[str, str], logged_in_open_counts: Mapping[str, int]) -> str | None:
    """R9: 담당자가 로그인해 있으면 담당자, 아니면 로그인한 정비자 중 처리 중 알람이 가장 적은 사람
    (같으면 사용자 ID 순),
    아무도 없으면 미배정(None)."""
    owner = assignments.get(equipment_id)
    if owner in logged_in_open_counts:
        return owner
    if not logged_in_open_counts:
        return None
    return sorted(logged_in_open_counts, key=lambda u: (logged_in_open_counts[u], u))[0]


def ordered_causes(k: ManualKnowledge, group: str) -> list[Cause]:
    return list(k.causes.get(group, []))
