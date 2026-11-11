"""기술지원 문서 RAG — v2: 문서 검색과 웹 검색 도구를 병렬로 → 합치기 → 답변 → 검토(근거가 없으면 최대 2번 다시 답변)."""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from llm import llm

INPUT_FIELD = "question"
MAX_RETRIES = 2
KB = {
    "비밀번호": "비밀번호 재설정: 로그인 화면의 '비밀번호 찾기'에서 메일 링크를 누릅니다(링크 유효 30분).",
    "환불": "환불: 결제 후 7일 안에 설정 > 결제 > 환불 요청. 사용 기록이 있으면 부분 환불입니다.",
    "동기화": "동기화 오류: 앱을 최신 버전으로 올리고 설정 > 계정 > 다시 동기화를 누릅니다.",
    "요금제": "요금제 변경: 설정 > 결제 > 요금제. 상위 요금제는 즉시, 하위 요금제는 다음 결제일부터 적용됩니다.",
}
FAQ_INDEX = {"오프라인": "오프라인 모드: 마지막 동기화 이후 문서는 오프라인에서도 열람할 수 있습니다."}


class State(TypedDict, total=False):
    question: str
    query: str
    docs: Annotated[list, operator.add]
    tool_calls: Annotated[list, operator.add]
    answer: str
    tries: int
    grounded: bool


def search_faq(q):
    """Tool: FAQ 색인 검색 (로컬 색인, 네트워크 없음)."""
    return [v for k, v in FAQ_INDEX.items() if k in q]


def query(s):
    return {"query": s["question"].strip(), "tries": 0}


def retrieve(s):
    return {"docs": [v for k, v in KB.items() if k in s["query"]][:2]}


def websearch(s):
    found = search_faq(s["query"])
    return {"docs": found, "tool_calls": [{"tool": "search_faq", "args": {"q": s["query"]}, "result_count": len(found)}]}


def merge(s):
    return {"docs": [] if s.get("docs") else ["(관련 문서 없음)"]}


def generate(s):
    context = "\n".join(s.get("docs") or [])
    return {"answer": llm("문서만 근거로 짧게 답하세요. 문서에 없으면 모른다고 답하세요.", f"문서:\n{context}\n\n질문: {s['question']}"),
            "tries": s.get("tries", 0) + 1}


def grade(s):
    docs = [d for d in s.get("docs") or [] if d != "(관련 문서 없음)"]
    return {"grounded": any(d.split(":")[0] in s["answer"] or d[:12] in s["answer"] for d in docs) or not docs}


def after_grade(s):
    return "done" if s["grounded"] or s["tries"] > MAX_RETRIES else "retry"


def build_graph():
    g = StateGraph(State)
    g.add_node("query", query)
    g.add_node("retrieve", retrieve)
    g.add_node("websearch", websearch)
    g.add_node("merge", merge)
    g.add_node("generate", generate)
    g.add_node("grade", grade)
    g.add_edge(START, "query")
    g.add_edge("query", "retrieve")
    g.add_edge("query", "websearch")
    g.add_edge(["retrieve", "websearch"], "merge")
    g.add_edge("merge", "generate")
    g.add_edge("generate", "grade")
    g.add_conditional_edges("grade", after_grade, {"retry": "generate", "done": END})
    return g
