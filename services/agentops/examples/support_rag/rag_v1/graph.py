"""기술지원 문서 RAG — v1: 질문 정리 → 문서 검색 → 답변 (선형)."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from llm import llm

INPUT_FIELD = "question"
KB = {
    "비밀번호": "비밀번호 재설정: 로그인 화면의 '비밀번호 찾기'에서 메일 링크를 누릅니다(링크 유효 30분).",
    "환불": "환불: 결제 후 7일 안에 설정 > 결제 > 환불 요청. 사용 기록이 있으면 부분 환불입니다.",
    "동기화": "동기화 오류: 앱을 최신 버전으로 올리고 설정 > 계정 > 다시 동기화를 누릅니다.",
    "요금제": "요금제 변경: 설정 > 결제 > 요금제. 상위 요금제는 즉시, 하위 요금제는 다음 결제일부터 적용됩니다.",
}


class State(TypedDict, total=False):
    question: str
    query: str
    docs: list
    answer: str


def query(s):
    return {"query": s["question"].strip()}


def retrieve(s):
    return {"docs": [v for k, v in KB.items() if k in s["query"]][:2]}


def generate(s):
    context = "\n".join(s.get("docs") or ["(관련 문서 없음)"])
    return {"answer": llm("문서만 근거로 짧게 답하세요.", f"문서:\n{context}\n\n질문: {s['question']}")}


def build_graph():
    g = StateGraph(State)
    g.add_node("query", query)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_edge(START, "query")
    g.add_edge("query", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    return g
