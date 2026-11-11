# living-maintenance-guide

설비 정비 안내 앱(정비자 · 상급자) · 지식 그래프 · 정비 Agent Pack · 배포 서버 · AgentOps를 한 컴퓨터에서 도커로 모두 띄우는 저장소입니다.
AgentOps는 **빈 상태**로 뜨고, 프로젝트 만들기부터 화면으로 따라 합니다.

## 빠른 시작
1. Docker Desktop 설치 · 메모리 8GB 이상(16GB 권장) · 디스크 20GB
2. `git clone <이 저장소>` → `cd living-maintenance-guide`
3. `cp .env.example .env` → `OPENAI_API_KEY`와 비밀번호 줄 채우기
4. `docker compose up -d`
5. 브라우저: 정비자 앱 http://127.0.0.1:8081 · 상급자 앱 http://127.0.0.1:8082 · AgentOps http://127.0.0.1:18868

## 문서
`docs/` — 설치와 실행 · DB 적용 · AgentOps 프로젝트 만들기 · 시연(메인 · 정확도 · 효율) · 초기화 · 문제 해결 · AWS 서버에 올리기

## 코드
- `services/` 아래 코드는 각 서비스 원본을 복사한 것입니다. AgentOps만 한 줄 바뀌었습니다(리눅스 컨테이너에서 실제 모델 쓰기 — `services/agentops/FROZEN-DIFF.md`).

## 자료 출처
- AI4I 2020 Predictive Maintenance Dataset — S. Matzka, UCI Machine Learning Repository(https://archive.ics.uci.edu/), CC BY 4.0(https://creativecommons.org/licenses/by/4.0/)
  - 이 저장소에 든 것: 원본 표는 넣지 않았고, 행 번호(UDI)별 시험 · 학습 나눔(`services/app/generation/split/`)과 이 자료로 학습한 모델 · 비교 값(`services/pack/releases/models/`)만 있습니다
