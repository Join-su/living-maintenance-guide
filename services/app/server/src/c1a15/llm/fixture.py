"""fixture 모드(기본): 템플릿 문장 · 매뉴얼 판정 기준 단어 규칙(R5-6 · R7). 외부 호출 없음."""

import re

from c1a15.llm.base import Draft, Facts, MemoSignal, josa

ABNORMAL_WORDS = (
    "기름때",
    "막힘",
    "MIN 아래",
    "유격",
    "변색",
    "이완",
    "손상",
    "치핑",
    "누설",
    "새는",
    "부족",
    "흔들림",
)
NORMAL_WORDS = ("깨끗", "정상", "이상 없음", "문제 없음", "양호")
OUT_OF_SCOPE_WORDS = ("타는 냄새", "연기", "스파크", "불꽃", "감전")


class FixtureAdapter:
    mode = "fixture"

    def write(self, facts: Facts) -> Draft:
        v = facts.values
        if facts.kind == "reason":  # R5-6 '이 점검을 고른 이유'
            alts = (
                ", ".join(f"{a.get('title') or a['section']}({a['grade']})" for a in v.get("alternatives", []))
                or "없음"
            )
            rank = v.get("rank", 1)
            text = (
                f"{v['cause_name']} 가능성이 {v['probability']}%로 가장 높고, "
                f"{facts.section}절 점검은 약 {v['minutes']}분 · {v['grade']}등급입니다. 매뉴얼의 다른 점검: {alts}."
                if rank == 1
                else f"{rank}위 {v['cause_name']} {v['probability']}%: "  # D-450: 1위가 아니면 1위라고 말하지 않음
                f"{facts.section}절 점검은 약 {v['minutes']}분 · {v['grade']}등급입니다. 매뉴얼의 다른 점검: {alts}."
            )
            return Draft(text, sections=(facts.section,))
        if facts.kind == "call_reason":  # P-18: 첫 판정이 호출일 때 '호출이 필요한 이유'(T04 / first_call)
            who = f"{v['cause_name']}({v['probability']}%)"
            label = (
                facts.section if facts.section.startswith("EX-") else f"{facts.section}절"
            )  # 예외 절(약속 v8)은 절 번호 아님
            what = f"{v['check_title']}({label}, {v['grade']}등급)"
            if v["reason"] == "exception_guide" and v.get("topic"):  # 고침A 11쪽: '예외상황-{주제}(C등급)와 같은 조건'
                head = (
                    f"예외상황-{v['topic']}({v['grade']}등급){josa(v['topic'], '과', '와')} 같은 조건이라 "
                    f"{v['check_title']}{josa(v['check_title'], '을', '를')} 먼저 봅니다. 파트장 승인이 필요합니다."
                )
            elif v["reason"] == "exception_guide":  # 약속 v8: 예외 기록과 같은 조건 — 점검방법 먼저(파트장 승인)
                head = (
                    f"예외 기록과 같은 조건이라 {what}{josa(v['check_title'], '을', '를')} 먼저 봅니다. "
                    "파트장 승인이 필요합니다."
                )
            elif v["reason"] == "top_cause_cd_only":
                head = f"1순위 {who}는 {what}으로만 확인할 수 있어 혼자 진행할 수 없습니다."
            else:
                head = (
                    "지금 등급으로는 혼자 할 수 있는 점검(A · B)이 없습니다. "
                    + f"{who}를 확인할 {what}을 파트장에게 요청합니다."
                )
            return Draft(head + " 잠금을 확인한 뒤 파트장 호출 요약으로 갑니다.", sections=(facts.section,))
        if facts.kind == "ai_reading":  # P-19: 알람 화면(T03) 'AI 해석' — 판정이 아닌 쉬운 말 문장
            change = "작아졌습니다." if v["direction"] == "경계 아래" else "커졌습니다."
            return Draft(
                f"{v['sensor']} 값({v['value_text']})이 경계({v['boundary_text']})보다 {change} "
                f"{v['system']} 쪽 문제일 가능성이 높습니다."
            )
        if facts.kind == "instruction":  # 지시문 = 절 원문 절차
            return Draft(v["procedure"], sections=(facts.section,))
        return Draft(v.get("template", ""), sections=(facts.section,) if facts.section else ())

    def memo_signal(self, check_section: str, criteria: str, memo: str, button: str | None) -> MemoSignal | None:
        """R7: 메모가 버튼과 반대 뜻(a) · 버튼 없이 메모만(b) · 후보 원인 밖 소견(c)일 때만 신호."""
        memo = memo or ""
        quote = memo[:40]
        if any(w in memo for w in OUT_OF_SCOPE_WORDS):
            return MemoSignal("불명", False, True, quote)
        normal = any(w in memo for w in NORMAL_WORDS) and not re.search(r"(정상|이상 없음)\s*(아님|아니)", memo)
        abnormal = any(w in memo for w in ABNORMAL_WORDS) or any(w in memo for w in _criteria_words(criteria))
        interpretation = "이상" if abnormal and not normal else "정상" if normal and not abnormal else "불명"
        if button is None and memo:
            return MemoSignal(interpretation, False, False, quote)
        if button in {"정상", "이상"} and interpretation in {"정상", "이상"} and interpretation != button:
            return MemoSignal(interpretation, True, False, quote)
        return None


def _criteria_words(criteria: str) -> list[str]:
    """판정 기준 문장의 핵심 단어(예: '기름때·먼지')."""
    return [
        w
        for w in re.findall(r"[가-힣]{2,}", criteria or "")
        if w not in {"이상", "정상", "이다", "판정", "기준", "절반", "표면", "필터"}
    ][:6]
