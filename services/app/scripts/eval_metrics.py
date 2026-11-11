"""P-16 평가 지표 6개(AC-18) — 사용자 결정 020 ⑤ ~ ⑧ · ⑩ ~ ⑫의 계산 정의를 그대로 옮긴 순수 함수.

입력은 평가 실행기가 카드마다 모은 기록(record)과 매뉴얼(data/manuals/manual_sections.json)뿐이다.
진단 처리 · 색인 코드는 이 모듈을 쓰지 않는다.
"""

import re

TARGETS = {  # AC-18-C2: 목표(80% · 80% · 20% · 95% · 0 · 0), 미달도 결과로 보고
    "similar_top3": {"name": "유사 사례 Top-3 포함률", "target": 80.0, "unit": "%", "better": "higher"},
    "cause_top3": {"name": "원인 Top-3 포함률", "target": 80.0, "unit": "%", "better": "higher"},
    "minutes_reduction": {"name": "점검 소요 분 단축률", "target": 20.0, "unit": "%", "better": "higher"},
    "section_match": {"name": "인용 절 일치율", "target": 95.0, "unit": "%", "better": "higher"},
    "fabricated_causes": {"name": "날조 원인 수", "target": 0, "unit": "건", "better": "lower"},
    "grade_violations": {"name": "등급 위반 수", "target": 0, "unit": "건", "better": "lower"},
}
UNKNOWN = "원인 미상"
# 제품이 정해 둔 확정 원인 칸 결과 표시(server flow.OUTCOME_CAUSE 값과 같아야 함 — 시험이 대조), 결정 020 ⑪
OUTCOME_LABELS = {UNKNOWN, "이상 없음 · 감시"}
# 절 자리에 쓴 절 번호만: '(4.3, 이상)' · '(4.3.2, A)' · '4.4절'. '온도 차 9.1 K' 같은 측정값은 세지 않는다(결정 020 ⑩)
SECTION_REF = re.compile(r"(?:(?<=\()|(?<=\s)|^)(\d\.\d(?:\.\d)?)(?=,|\)|절)")


class Manual:
    def __init__(self, sections: list[dict]):
        self.sections = {s["id"]: s for s in sections}
        self.checks: dict[str, list[dict]] = {}
        self.causes: dict[str, dict[str, str]] = {}
        for s in sections:
            if s["kind"] == "check":
                self.checks.setdefault(s["group"], []).append(s)
            if s["kind"] == "info" and s.get("group") and s["id"].endswith(".1"):
                rows = re.findall(r"^\| ([A-Z]{2}-\d) \| ([^|]+?) \|", s["text"], re.M)
                self.causes[s["group"]] = {code: name.strip() for code, name in rows}
        for g in self.checks:
            self.checks[g].sort(key=lambda s: [int(x) for x in s["id"].split(".")])

    def baseline_minutes(self, group: str, code: str) -> int:
        """매뉴얼 절 순서대로 정답 결정 점검까지 한 소요 분 합(결정 020 ⑥)."""
        total = 0
        for s in self.checks[group]:
            total += s["minutes"]
            if s["cause"] == code:
                return total
        raise ValueError(f"{group}에 {code} 결정 점검 없음")


def top3(probabilities: dict[str, float]) -> list[str]:
    """첫 원인 순위 상위 3(확률 내림차순, 같으면 코드 순)."""
    return [c for c, _ in sorted(probabilities.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]


def cited_sections(text: str) -> list[str]:
    return SECTION_REF.findall(text or "")


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 1) if den else None


def _met(key: str, value) -> bool | None:
    if value is None:
        return None
    t = TARGETS[key]
    return value >= t["target"] if t["better"] == "higher" else value <= t["target"]


def compute(records: list[dict], manual: Manual) -> dict:
    """카드 기록 → 지표 6개 + 목표 + 분모 · 분자. 실행에 실패한 카드는 분모에 넣되 맞지 않은 것으로 센다(FLOW-12).
    알람이 되지 않은 카드(ML 길 registered: false)는 계산 전에 걸러 낸다 — cards만 전체(EV-001 CH-27)."""
    every, records = records, [r for r in records if r.get("registered", True)]
    graded = [r for r in records if r["hidden"]["type"] == "manual_cause"]
    m1 = sum(1 for r in graded if not r.get("failed") and r["hidden"]["code"] in r["similar_top3_causes"])
    m2 = sum(1 for r in graded if not r.get("failed") and r["hidden"]["code"] in top3(r["initial_probabilities"]))
    base = sum(manual.baseline_minutes(r["group"], r["hidden"]["code"]) for r in graded)
    # 실패 카드는 단축 0(Agent 분 = 기준 분) — 0분으로 넣어 단축률을 부풀리지 않음(최종 통합 검토 비차단 ③)
    agent = sum(
        manual.baseline_minutes(r["group"], r["hidden"]["code"])
        if r.get("failed")
        else sum(r["check_minutes_until_confirm"])
        for r in graded
    )
    cites = matched = 0
    for r in records:
        performed, remaining = set(r["performed_sections"]), set(r["remaining_candidate_sections"])
        for sec, row in r["citations"]:
            cites += 1
            in_manual = sec in manual.sections
            # 결정 020 ⑦ ⑫: 실제 단계 = 지시 · 수행 · 호출한 절,
            # 미확인 행은 실제 단계 또는 끝에 후보로 남은 원인의 점검 절
            ok_place = sec in performed or (row == "unresolved" and sec in remaining)
            matched += 1 if in_manual and ok_place else 0
    fabricated = 0
    for r in records:
        allowed = set(manual.causes.get(r["group"], {}).values()) | OUTCOME_LABELS
        names = list(r["candidate_names"]) + ([r["report_cause"]] if r.get("report_cause") else [])
        fabricated += sum(1 for n in names if n not in allowed)
    violations = sum(r["violations"] for r in records)
    values = {
        "similar_top3": (_pct(m1, len(graded)), m1, len(graded)),
        "cause_top3": (_pct(m2, len(graded)), m2, len(graded)),
        "minutes_reduction": (round(100.0 * (1 - agent / base), 1) if base else None, agent, base),
        "section_match": (_pct(matched, cites), matched, cites),
        "fabricated_causes": (fabricated, fabricated, None),
        "grade_violations": (violations, violations, None),
    }
    metrics = [
        {
            "id": k,
            **TARGETS[k],
            "value": v,
            "numerator": n,
            "denominator": d,
            "met": _met(k, v),
        }
        for k, (v, n, d) in values.items()
    ]
    return {
        "metrics": metrics,
        "cards": len(every),
        "registered": len(records),
        "not_registered": len(every) - len(records),
        "graded_cards": len(graded),
        "ungraded_cards": {t: sum(1 for r in records if r["hidden"]["type"] == t) for t in ("none", "out_of_graph")},
        "failed_cards": sum(1 for r in records if r.get("failed")),
        "calls": sum(r.get("calls", 0) for r in records),
        "outcome_match": sum(1 for r in records if r.get("outcome_match")),
    }
