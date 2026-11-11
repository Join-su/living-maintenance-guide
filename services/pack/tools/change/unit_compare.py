"""받아 넣기 도구가 쓰는 시험 결과 견주기(D-645) — unittest 출력에서 실패 · 오류 시험 이름을 읽고, 바탕(HEAD)에서 이미 실패한 것은 뺌."""
import re

NAME = re.compile(r"^(?:FAIL|ERROR): \S+ \((\S+)\)", re.M)   # unittest 실패 머리 줄: 'FAIL: test_a (mod.Class.test_a)'


def failed_names(stderr):
    """unittest stderr → 실패 · 오류 시험 이름(점 이름) 모음."""
    return set(NAME.findall(stderr or ""))


def judge(ok, cand, base):
    """(후보 시험 통과?, 후보 실패 이름, 바탕 실패 이름 · 바탕을 못 돌렸으면 None) → (판정, 새 실패, 뺀 바탕 실패).
    판정: ok = 받음(통과 또는 바탕 실패뿐) · new = 새 실패 있음(거부) · unknown = 실패인데 이름을 못 읽음(견줄 수 없음 · 거부)."""
    if ok:
        return "ok", set(), set()
    if not cand:
        return "unknown", set(), set()
    base = base or set()
    new = cand - base
    return ("new" if new else "ok"), new, cand & base
