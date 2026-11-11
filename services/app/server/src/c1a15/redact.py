"""정비자 메모 속 사람 이름 거르기(D-301 (4) · 001-63): 앱 밖으로 나가는 글만 [이름].

화면 · 앱 DB · 확정 보고서는 원문.

출구 = 판정 Run 메모 인용 · 보고서 Run 초안 · R12 받은 편지 글 · live 직접 호출 문장(부르는 곳은 각 출구 한 줄).
규칙(결정적): ① 앱 계정 이름표 ② 계정 ID 꼴 ③ 전화 ④ 성명 + 직함 ⑤ 성 + 직함 · 호칭 ⑥ 이름 + 씨 · 님.
기록 ID(H-/R-nnnn) · 절 번호 · 수치는 그대로(PR-16 · c1a15-pack 동의 — 근거 검사 기준).
직함 없이 이름만 쓴 글은 못 잡음 → Pack 메모 해석 노드(실제 LLM)가 두 번째 막기(D-444 #19).
"""

import re

from c1a15.loader.core import LEAD_ACCOUNTS, TECH_ACCOUNTS

MARK = "[이름]"
SURNAMES = (  # 흔한 한 글자 성
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채"
    "원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경"
)
TITLES = ("파트장", "팀장", "반장", "조장", "과장", "대리", "주임", "기사", "선임", "책임", "정비", "님", "씨")
# 성명 뒤 직함 — '이상을 정비' 꼴을 잡지 않게 정비 · 파트장 · 기사는 뺌
FULL_TITLES = ("팀장", "반장", "조장", "과장", "대리", "주임", "선임", "책임", "님", "씨")
_JOSA = "을를은는이가면에의도로와과"  # 성명 끝 글자가 조사면 일반 글('차례면 파트장')
_HAN = "[가-힣]"
_ACCOUNTS = [*TECH_ACCOUNTS, *LEAD_ACCOUNTS]
_RULES = (
    re.compile("|".join(re.escape(name) for _, name in _ACCOUNTS)),
    re.compile(r"\b(?:tech|lead)\.[a-z]+\b|" + "|".join(re.escape(uid) for uid, _ in _ACCOUNTS)),
    re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)"),
    re.compile(f"(?<!{_HAN})[{SURNAMES}]{_HAN}{_HAN}(?<![{_JOSA}])\\s?(?:{'|'.join(FULL_TITLES)})"),
    re.compile(f"(?<!{_HAN})[{SURNAMES}]\\s?(?:{'|'.join(TITLES)})"),
    re.compile(f"(?<!{_HAN}){_HAN}{{2,4}}\\s?(?:씨|님)(?=$|[^가-힣]|[이가은는을를의께에도와과랑만])"),
)


def redact(text: str) -> tuple[str, int]:
    """글 하나 → (거른 글, 거른 수)."""
    n = 0
    for rule in _RULES:
        text, k = rule.subn(MARK, text)
        n += k
    return text, n


def redact_obj(obj):
    """dict · list 안의 글 칸을 모두 거른 사본 → (사본, 거른 수). 원본은 그대로."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        pairs = [(k, redact_obj(v)) for k, v in obj.items()]
        return {k: v for k, (v, _) in pairs}, sum(n for _, (_, n) in pairs)
    if isinstance(obj, list | tuple):
        items = [redact_obj(v) for v in obj]
        return type(obj)(v for v, _ in items), sum(n for _, n in items)
    return obj, 0
