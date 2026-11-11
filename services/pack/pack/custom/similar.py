"""예외 비슷한 것 찾기 node(custom_code@3 · 약속 v10 kind exception_similar · D-566) — 망 · 호출 · 부작용 0, 그래프 release 파일만.
보고서 예외 칸 AI 나눔 결과(exception 하나: 주제 + 점검 목록 + 조치 목록) → **예외 통째로 한 번** 그 증상군의 등록 예외 절(sections kind 예외) · 매뉴얼 점검 · 조치 절 중
글이 비슷한 것 상위 3(높은 점수 먼저 · 같으면 절 번호 · MIN_SCORE 밑은 뺌 — 빈 matches = 비슷한 것 없음 → 앱은 새 예외로) · 예외 줄엔 그 예외의 checks · actions(D-560 덧 10). 셈 = 글자 두 개 묶음(공백 · 문장부호 뺌) 코사인 — 1단계 범위(로컬 · 정확도 한계는 보고에 적음).
꼴 정본 = orchestration agents/c1a15 handoff/d556/contract-v9.md 5절. 앱이 정비자에게 '같음 / 아님'을 묻는 데 씀(같음 = 놓침 · 아님 = 새 예외)."""
import math
import re
from collections import Counter

from custom import exception, graph_query

TOP = 3
MIN_SCORE = 0.15   # 이 점수 밑은 뺌(D-556 덧 5 · 값 덧 6 · eval 149: 0.125 ~ 0.20 같은 결과)
KINDS = {"예외": "예외", "점검": "매뉴얼", "조치": "매뉴얼"}   # 사전 확인 · 공통 · 재가동 · 마감 절은 찾지 않음
_DROP = re.compile(r"[\s\W_]+")


def _grams(text):
    t = _DROP.sub("", text or "")
    return Counter(t[i:i + 2] for i in range(len(t) - 1)) if len(t) > 1 else Counter(t)


def _cos(a, b):
    num = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def _exception_text(x):
    parts = [x.get("topic") or ""]
    for c in x.get("checks") or []:
        parts += [c.get("what", ""), c.get("how", ""), c.get("criteria", "")]
    for a in x.get("actions") or []:
        parts += [a.get("what", ""), *(a.get("parts") or [])]
    return " ".join(p for p in parts if p)


def _section_text(s):
    checks = s.get("checks") or ([s["check"]] if s.get("check") else [])   # v10 목록 · 옛 하나 꼴도 읽음
    actions = s.get("actions") or ([s["action"]] if s.get("action") else [])
    vals = [s.get("topic"), s.get("name"), s.get("guide"), s.get("criteria"), *(s.get("steps") or [])]
    for c in checks:
        vals += [c.get("what"), c.get("how"), c.get("criteria")]
    for a in actions:
        vals += [a.get("what"), *(a.get("parts") or [])]
    return " ".join(str(v) for v in vals if v)


def find(request):
    rel = graph_query.release()
    name = next(g["name"] for g in rel["groups"] if g["code"] == request["group"])
    secs = {s.get("app_no") or s["no"]: s for s in rel["sections"] if s.get("group") == name and s["kind"] in KINDS and (s.get("app_no") or s["no"])}
    pool = [(KINDS[s["kind"]], no, s.get("name"), _grams(_section_text(s))) for no, s in secs.items()]
    q = _grams(_exception_text(request["exception"]))
    hits = sorted(((round(_cos(q, g), 4), kind, no, nm) for kind, no, nm, g in pool), key=lambda h: (-h[0], h[2]))
    return {"result": {"exception_similar": {"matches": [_match(kind, no, nm, sc, secs[no]) for sc, kind, no, nm in hits[:TOP] if sc > 0 and sc >= MIN_SCORE]}}}


def _match(kind, no, name, score, sec):
    """비슷한 것 한 줄 — 등록 예외 줄엔 그 예외의 지금 점검 · 조치 목록(D-560 덧 10 · '같음 + 고침' 바탕 · 있을 때만) · 매뉴얼 줄은 그대로."""
    out = {"kind": kind, "no": no, "name": name, "score": score}
    if kind != "예외":
        return out
    checks, actions = exception.lists(sec)
    return {**out, **({"checks": checks} if checks else {}), **({"actions": actions} if actions else {})}
