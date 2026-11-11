"""긴급 계약 메일 회신 — v1: 메일 분류 → (긴급/일반) 회신 초안."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from llm import llm

INPUT_FIELD = "mail"
URGENT_WORDS = ("오늘", "즉시", "마감", "해지", "위약")


class State(TypedDict, total=False):
    mail: str
    urgency: str
    reply: str


def classify(s):
    return {"urgency": "urgent" if any(w in s["mail"] for w in URGENT_WORDS) else "normal"}


def route(s):
    return s["urgency"]


def draft_urgent(s):
    return {"reply": llm("긴급 계약 메일에 오늘 안에 처리 일정을 약속하는 정중한 회신을 쓰세요.", s["mail"])}


def draft_normal(s):
    return {"reply": llm("계약 메일에 2영업일 안에 답하겠다는 정중한 회신을 쓰세요.", s["mail"])}


def build_graph():
    g = StateGraph(State)
    g.add_node("classify", classify)
    g.add_node("draft_urgent", draft_urgent)
    g.add_node("draft_normal", draft_normal)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route, {"urgent": "draft_urgent", "normal": "draft_normal"})
    g.add_edge("draft_urgent", END)
    g.add_edge("draft_normal", END)
    return g
