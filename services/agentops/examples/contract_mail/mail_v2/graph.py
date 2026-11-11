"""긴급 계약 메일 회신 — v2: 분류 → 계약 조회 도구 → 초안 → 검토(필수 항목이 빠지면 최대 2번 다시 초안)."""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from llm import llm

INPUT_FIELD = "mail"
MAX_REDRAFTS = 2
URGENT_WORDS = ("오늘", "즉시", "마감", "해지", "위약")
CONTRACTS = {"A-1042": {"고객": "한빛물산", "만료일": "2026-12-31", "위약금": "월 이용료의 2배"},
             "B-2207": {"고객": "누리전자", "만료일": "2026-10-15", "위약금": "없음"}}


class State(TypedDict, total=False):
    mail: str
    urgency: str
    contract: dict
    tool_calls: Annotated[list, operator.add]
    reply: str
    redrafts: int
    missing: list


def lookup_contract(contract_id):
    """Tool: 계약 대장 조회 (로컬 대장, 네트워크 없음)."""
    return CONTRACTS.get(contract_id, {})


def classify(s):
    return {"urgency": "urgent" if any(w in s["mail"] for w in URGENT_WORDS) else "normal", "redrafts": 0}


def route(s):
    return s["urgency"]


def lookup(s):
    cid = next((c for c in CONTRACTS if c in s["mail"]), "")
    found = lookup_contract(cid)
    return {"contract": found, "tool_calls": [{"tool": "lookup_contract", "args": {"contract_id": cid}, "found": bool(found)}]}


def draft(s):
    facts = ", ".join(f"{k}: {v}" for k, v in (s.get("contract") or {}).items()) or "계약 정보 없음"
    tone = "오늘 안에 처리 일정을 약속" if s["urgency"] == "urgent" else "2영업일 안에 답변"
    return {"reply": llm(f"계약 메일 회신을 쓰세요. {tone}하고 계약 정보를 정확히 인용하세요.", f"계약 정보: {facts}\n\n메일:\n{s['mail']}"),
            "redrafts": s.get("redrafts", 0) + 1}


def review(s):
    need = [str(v) for v in (s.get("contract") or {}).values()][:2]
    return {"missing": [n for n in need if n not in s["reply"]]}


def after_review(s):
    return "done" if not s["missing"] or s["redrafts"] > MAX_REDRAFTS else "redraft"


def build_graph():
    g = StateGraph(State)
    g.add_node("classify", classify)
    g.add_node("lookup", lookup)
    g.add_node("draft", draft)
    g.add_node("review", review)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route, {"urgent": "lookup", "normal": "draft"})
    g.add_edge("lookup", "draft")
    g.add_edge("draft", "review")
    g.add_conditional_edges("review", after_review, {"redraft": "draft", "done": END})
    return g
