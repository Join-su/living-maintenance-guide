"""C1-a15 Pack 보고서 문장 node(custom_code@3 · validate@2 custom, 약속 v3 kind report · Pack EV-001 PC-09) — 망 · 부작용 · 앱 import 0.

근거 검사 = 앱 llm/grounding.py is_grounded 사본(custom/report_grounding.py, 출처 COPIED_FROM.json). 기준 집합은 앱
flow._report_grounding과 같게 요청 칸에서 만든다: 수치 = 8항목 초안 글의 수치, 절 번호 = 초안 수치 중 절 번호 꼴 + 요청
allowed_sections, 근거 사례 = similar record_id — 칸마다 그 칸 초안 ID, 없으면 초안 근거 기록 칸 ID만(D-469 (2): 초안이 가리킨 기록을 다른 ID로 바꿔 쓰면 떨어짐). 칸마다 판정해 떨어진 칸만 다시 쓰고(그래프 rewrite_report), 두 번째도 떨어지면
원문(report_draft 그 칸) · fallback true. reason: 통과면 null, 원문 대체 때만 마지막 검사 실패 이유(D-285 · Change Contract PC-09).
빈 문장은 앱 is_grounded와 달리 실패(reason empty) — 약속 v3에 따름.
기록 ID(D-486 덧 7): 앱 사본 꼴(rg._RECORD · 낱말 경계)은 ID에 한글이 붙으면 못 봄 → Pack 꼴 RECORD로 기준 밖 ID를 한 번 더 봄(사본 파일 · 사본 표 그대로).
예외 절(D-546 덧 36 · 약속 v8): 글의 'EX-R-0001'은 절 번호 — allowed_sections에 있어야 하고, 지운 뒤 기록 ID · 수치를 봄(안의 'R-0001'을 기록 ID로 세지 않음).
"""
import re
from dataclasses import replace

from custom import report_grounding as rg
from custom.llm_base import Draft

FIELDS = ("cause", "action")
RECORD = re.compile(r"(?<![A-Za-z0-9])[HR]-\d{4}(?![0-9])")   # D-486 덧 7: 한글 · 문장부호가 붙어도 기록 ID(앱 사본 rg._RECORD는 낱말 경계 꼴이라 'H-0153입니다'를 못 봄)
EX = re.compile(r"(?<![A-Za-z0-9-])EX-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")   # 예외 절 번호(약속 v8 EX-… · 한글 · 문장부호가 붙어도)


def _ex(text, g):
    """(기준 밖 예외 절, 예외 절을 지운 글)."""
    text = text or ""
    return set(EX.findall(text)) - g.allowed_sections, EX.sub(" ", text)


def basis(request):
    draft = request["report_draft"]
    nums = rg.numbers_in(*draft.values())
    secs = {s for s in nums if rg.is_section_like(s)} | {s for s in request.get("allowed_sections", []) if s}
    return rg.Grounding(allowed_sections=frozenset(secs), source_numbers=nums, top_record_ids=frozenset(s["record_id"] for s in request["similar"]))


def field_basis(request, field):
    """칸 하나의 기준 — 기록 ID는 초안 그 칸에 ID가 있으면 그것만, 없으면 초안 '근거 기록' 칸 ID(둘 다 ∩ 상위 10건) ·
    수치 · 절은 basis 그대로. 앱 초안 원인 · 조치 칸엔 ID가 없어 근거 기록 인용은 통과, 초안이 가리킨 기록을 다른 ID로 바꾸면 떨어짐."""
    g, draft = basis(request), request["report_draft"]
    ids = frozenset(RECORD.findall(EX.sub(" ", draft.get(field) or ""))) or frozenset(RECORD.findall(draft.get("evidence_records") or ""))
    return replace(g, top_record_ids=ids & g.top_record_ids)


def found(text, g):
    """기준 밖 낱말(절 번호 · 기록 ID · 수치) — 다시 쓰기가 지울 것."""
    ex, text = _ex(text, g)
    secs = ex | (set(rg._SECTION.findall(text)) - g.allowed_sections)
    recs = set(RECORD.findall(text)) - g.top_record_ids
    nums = set(rg._NUM.findall(RECORD.sub(" ", rg._SECTION.sub(" ", text)))) - g.source_numbers
    return sorted(secs | recs | nums)


def judge_text(text, g):
    """(ok, reason). ok = 앱 is_grounded 판정(빈 문장은 실패). reason = 처음 어긋난 조건."""
    if not (text or "").strip():
        return False, "empty"
    ex, text = _ex(text, g)
    if ex:
        return False, "ungrounded_section"
    if rg.is_grounded(Draft(text), g):   # 앱 사본 판정을 지난 글도 기준 밖 기록 ID(RECORD 꼴)면 떨어짐(D-486 덧 7)
        return (True, None) if set(RECORD.findall(text)) <= g.top_record_ids else (False, "ungrounded_record")
    if not set(rg._SECTION.findall(text)) <= g.allowed_sections:
        return False, "ungrounded_section"
    if not set(RECORD.findall(text)) <= g.top_record_ids:
        return False, "ungrounded_record"
    return False, "ungrounded_number"


def _result(rows):
    failed = [f for f in FIELDS if f in rows and not rows[f]["ok"]]
    return {"passed": not failed, "reason": rows[failed[0]]["reason"] if failed else None, "message": ", ".join(failed) or "모든 칸 통과", "details": rows}


def _row(text, g):
    ok, reason = judge_text(text, g)
    return {"ok": ok, "reason": reason, "found": found(text, g)}


def grounded(request, report_text):
    return _result({f: _row((report_text or {}).get(f), field_basis(request, f)) for f in FIELDS})


def grounded_2(request, report_text, report_check, report_text_2):
    """다시 쓴 답 검사 — 첫 검사에서 떨어진 칸만 본다."""
    first = _details(report_check)
    return _result({f: _row((report_text_2 or {}).get(f), field_basis(request, f)) for f in FIELDS if not first[f]["ok"]})


def _details(check):
    """validate@2 결과에서 custom 규칙 하나의 칸별 판정 {칸: {ok, reason}} — details[규칙 이름] = {passed, reason, message, details}."""
    entry = next(iter(((check or {}).get("details") or {}).values()), {})
    return entry.get("details") or {}


def finish(request, report_text, report_check, report_text_2=None, report_check_2=None):
    draft, first = request["report_draft"], _details(report_check)
    second = _details(report_check_2) if report_check_2 else {}
    out = {}
    for f in FIELDS:
        if first[f]["ok"]:
            out[f] = {"text": report_text[f], "fallback": False, "attempts": 1, "reason": None}
        elif second.get(f, {}).get("ok"):
            out[f] = {"text": report_text_2[f], "fallback": False, "attempts": 2, "reason": None}
        else:
            out[f] = {"text": draft[f], "fallback": True, "attempts": 2, "reason": second.get(f, first[f])["reason"]}
    return {"result": {"report": out}}
