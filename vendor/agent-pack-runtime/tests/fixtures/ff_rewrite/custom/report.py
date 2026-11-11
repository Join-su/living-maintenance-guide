"""시험용(D-462): C1-a15 custom/report.py 꼴 축소 — 두 검사가 같은 judge_text · basis · report_grounding을 씀."""
from custom import report_grounding as rg
from custom.llm_base import Draft


def basis(request):
    return rg.Grounding(words=frozenset(rg.words_in(request["draft"])))


def judge_text(text, g):
    if not (text or "").strip():
        return False, "empty"
    if rg.is_grounded(Draft(text), g):
        return True, None
    return False, "ungrounded"


def _result(ok, reason):
    return {"passed": ok, "reason": reason, "message": "통과" if ok else "떨어짐"}


def grounded(request, text):
    return _result(*judge_text((text or {}).get("text"), basis(request)))


def grounded_2(request, text, check, text_2):
    return _result(*judge_text((text_2 or {}).get("text"), basis(request)))


def finish(request, text, check, text_2=None, check_2=None):
    if check and check.get("passed"):
        return {"result": {"text": text["text"], "fallback": False}}
    if check_2 and check_2.get("passed"):
        return {"result": {"text": text_2["text"], "fallback": False}}
    return {"result": {"text": request["draft"], "fallback": True}}
