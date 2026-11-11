"""Deployment Server (운영 1단계): 태그된 Agent Pack 하나를 불러와 그 Pack의 RunRequest(입력 schema는 Pack이 정함 — task10은 EventCard)를 packrt로 실행하고 결과 · 보고서를 돌려준다.
API 계약: docs/api-contract.md(로컬 문서). 실행 기록: trace 계약 2(llmops-lite-v2 docs/contracts/trace-event-contract.md)."""
__version__ = "0.2.0"
