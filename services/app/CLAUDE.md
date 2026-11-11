# 정비 길잡이(C1-a15) 저장소

구현 계획 · 승인 기록은 `.vibe-factory/runs/2026-10-11-c1a15-product-r2/`(VCF run). 승인된 plan item 밖은 구현하지 않는다.

## 구성
- `apps/tech`(정비자 앱, 8081) · `apps/lead`(파트장 앱, 8082) · `packages/ui`(공통): React + TypeScript + Vite, npm workspaces. 앱끼리 서로 import하지 않는다.
- `server/`: uv 프로젝트(Python 3.12). `src/c1a15/` = API(FastAPI) · DB 모델 · 적재기. migration은 `server/migrations`(Alembic).
- `docker-compose.yml`: db → init(migration · 적재) → api → web(nginx). 값은 `.env`(Git 제외, `.env.example` 참고).

## 명령
- `scripts/check-fast` — 비밀값 · 정답 자료 접근 · ruff · pytest(unit) · typecheck
- `scripts/verify-db` — 시험 DB(pgvector)로 migration 왕복 · 적재 · 저장값
- `scripts/verify-e2e` — 두 앱 빌드 + compose smoke

## 지킬 것
- `eval/` · `generation/`은 제품 코드(서버 · 앱 · 적재기)가 읽지 않는다(`scripts/forbidden_paths_check.py`).
- 비밀번호 · 키 원문을 파일 · 로그에 두지 않는다. fixture 모드(기본)는 외부 호출 0.
- 패키지 설치 · image pull · live OpenAI · commit · push는 사용자 승인 뒤.
