# c1a15-pack — C1-a15 Pack

정비 길잡이(C1-a15)의 LangGraph 에이전트 부분. packrt **v0.15.0(commit d3bc887)** 으로 돈다(태그 `v1` ~ `v3`는 0.11.0 — 그 사본은 자기 `vendor/packrt`로, 예 `PYTHONPATH=<사본>/vendor/packrt`).

- **입출력 약속 정본 = `contracts/v4/`**(D-291 · D-310, 요청 모양 = v3 · `start` 결과 `retrieval` 넷 · `start.similar`는 받되 쓰지 않음). C1-a15 앱 · 배포 서버는 이 schema를 따른다. 바꾸는 길은 규칙 06(질문 → 오케 D-nn → 새 version 폴더). `pack/schemas`는 정본과 바이트까지 같다(시험).
- **C1-a15 앱**(`c1a15-maint-guide`, 화면 + 앱 서버)과 따로 둔다. Pack 코드는 앱 모듈 · 앱 DB를 import하지 않고, 입력은 요청 본문으로만 받는다.
- **배포단** = C1-a15 앱 + 배포 서버(llmopsagent-deploy, D-242). 앱은 배포 서버 `POST /runs`로 이 Pack을 부르고, 배포 서버는 이 저장소 태그를 checkout해 돌린다.
- `vendor/packrt/` = `git -C agent-pack-runtime archive v0.15.0 packrt` 사본(고치지 않음, tree `e64080bf…`, D-476 — 함수 node 옵션 `params.options`(choice · number · integer · switch · 한도 · 지금 값) · 값 바꾸기 patch `set_option` · 선언은 고정 흐름 `options:<graph>` · 0.14.0 reasoning_effort 칸도 들어옴(이 Pack은 안 씀) · 의존 같음). 앞 0.12.5(tree `2257b82b…`, D-418 — 0.12.3에서: retrieve@2 `rerank.pool`(센서 재정렬이 top_k보다 많이 보고 고름 · 칸 없으면 동작 같음) · tracewriter 0.1.4. 앞 0.12.3(tree `21c5f87a…`, D-404 덧): `pack/models.yaml`의 `env_fallback`(OPENAI_API_KEY_2) 키 자동 전환이 켜짐. 앞 0.12.2(tree `170931b4…`, D-396 (2))는 0.12.1에서 파일 넷: 키 이름이 있는데 키가 비면 루프백 밖 주소로는 요청 전에 멈춤(GAP-004) · `packrt.index build`가 PACKRT_ENV_FILE에서도 키를 읽음).

## 구성
- `pack/pack.yaml` — 그래프 `run`, `route_request`(router@1)가 요청 종류로 가른다.
  - `alarm`: 경계 판정(`prepare_alarm`) → 경계 길(`decide_alarm_boundary`) / ML 길(`prepare_features` → `predict_failure` predict@1, 실패하면 `model_failure` 후보 → `decide_alarm`) — 두 node는 같은 함수 `custom.alarm:decide_alarm`, 경계 길은 `prediction`을 읽지 않음(흐름 검사 F3, v7)
  - `start`: 첫 Run — `prepare_history_query`(질의 글 · 증상군 · 센서 7칸) → `retrieve_history`(retrieve@2, store `history`) → `retrieve_manual`(retrieve@2, store `manuals` — 결과는 trace에만, PS-03) → `first_judge`(상위 10건 · 첫 판정 · `retrieval` 넷)
  - `judge`: 다음 Run
  - `report`: 보고서 문장(`cause` · `action`) — `write_report` llm@1 → `check_report` validate@2(통과하면 `finish_report_1`, 떨어지면 `rewrite_report` → `check_report_2` → `finish_report`) — 두 끝 node는 같은 함수 `custom.report:finish`(칸마다 `{text, fallback, attempts, reason}`), 첫 길은 2차 칸을 읽지 않음(F3, v7)
- `pack/custom/` — 판정 · 알람 규칙 사본(출처 `COPIED_FROM.json`, `tools/sync_from_app.py <앱> <commit>`로만) + Pack 쪽 node(`alarm.py` · `judge.py` · `features.py`)
- `pack/models.yaml` — llm node provider · model 이름(비밀 값 없음, 키는 env 이름 `OPENAI_API_KEY`만) · `tools/fake/rule.py` — 가짜 provider 규칙(배포 서버 · LLMOps가 부름, 초안 그대로 돌려줌)
- `pack/execution/local.yaml` — 이 맥 실행 설정(LLMOps '＋ release'용) · `pack/execution/deploy.yaml` — 배포용 실행 설정(배포 서버가 옮겨 씀) · `pack/execution-config.yaml` — packrt.runner가 Pack 폴더에서 바로 읽는 설정(비밀 값 없음)
  - store `history` · `manuals`: 이 맥은 packrt-1이 build한 벡터 DB 자리 `../agent-pack-runtime-ingest/releases-c1a15`(상대 경로, 읽기만)의 가짜 DB `ir-history-412ea43e` · `ir-manuals-a5c07752`(`embedding: {mode: fake}`, 망 0). 실제 DB `ir-history-16d297d8` · `ir-manuals-3ae4dcdf`는 `pack/execution/deploy-real.yaml`(start 한 번에 실제 임베딩 2건 — 사용자 승인 뒤에만)
  - pg 설정(PS-06): `pack/execution/deploy-pg.yaml`(가짜 DB) · `deploy-pg-real.yaml`(실제 DB) — stores만 `backend: pgvector` · `connection {env: LLMOPSAGENT_RUNTIME_DSN}`. 공유 DB DDL · 적재(사용자 승인) 전에는 운영에 쓰지 않음(deploy가 고를 때만). 시험은 일회용 컨테이너만
  - **운영 전 설정(EV-002 CH-01)** — 아래 셋은 사용자 승인 전에는 운영에 고르지 않습니다. 잘못 골랐을 때의 동작(packrt 0.12.1에서 저장소 시험 `FailClosed` · 일회용 컨테이너 증거로 확인, 0.12.2에서 같은 시험 다시 통과 — 파일은 그대로):

    | 파일 | 언제 쓰나 | 잘못 골랐을 때 | 고르는 쪽 |
    |---|---|---|---|
    | `deploy-pg.yaml` | 공유 DB DDL · 가짜 DB 적재 뒤(사용자 승인) | DSN env 없음 → 불러오기 실패(env 이름만) · DDL 전 DB → `'c1a15.data_release'를 읽을 수 없습니다` · 적재 전 → `release '…'이 DB에 적재되지 않았습니다`. 모두 Runtime 0 · flat 대체 0 · DSN · 비밀번호 글 0 | deploy(운영 설정에 옮김) · LLMOps 등록(목록에 `execution/*.yaml`이 모두 보임) |
    | `deploy-pg-real.yaml` | 위 + 실제 DB 적재 · 실제 임베딩 승인 뒤 | 같음. DSN이 있고 적재된 DB일 때만 임베딩 단계에 닿고, 그 뒤는 아래 `deploy-real`과 같음 | 같음 |
    | `deploy-real.yaml` | 실제 임베딩 승인 뒤(보고 012-28) | 키 · 주소 env가 둘 다 없거나 키만 있고 주소가 없으면 start Run 실패(`run.retrieve_history` — 주소 env 없음 글, 망 0). 주소 env(`OPENAI_BASE_URL`)만 있고 키가 비면 packrt 0.12.2가 요청 전에 멈춥니다(루프백 주소만 키 없이 감 — GAP-004, 0.12.1까지는 키 없이 보냈음), 키 · 주소가 다 있으면 실제로 나갑니다 → 고르는 쪽이 막음 | deploy · LLMOps(평가 격리가 밖 주소 · 키 변수를 막는다고 적혀 있음 — llmops `llmops/guard.py` 설명, 이 저장소에서 돌려 본 것 아님 · EV-002 GAP-001) · Pack 도구 |
- `pack/evals/` — LLMOps 평가 suite 넷(입력 · judge-demo · alarm = `tools/make_evals.py`, start 기대값 = `tools/make_start_suite.py`, report = `tools/make_report_suite.py`)
  - `c1a15-judge-demo`: 시연 10건 각 첫 보고서 단계
  - `c1a15-alarm`: 시연 각본 알람 10
  - `c1a15-start`: 시연 10 세션 첫 Run
  - `c1a15-report`: 보고서 문장 20(기대값 = 앱 내보내기 report_written 문장, D-290 (2) — 가짜 provider는 초안 그대로라 같은 값)
- `releases/models/` — model release(사용자 모델 `mr-ai4i-lgbm-large-v1` · 되돌리기용 자리 모델 `mr-c1a15-standin-v1`)
- `tests/` — 표준 라이브러리 unittest(`.venv/bin/python -m unittest discover -s tests`)
  - 벡터 DB 자리(`../agent-pack-runtime-ingest/releases-c1a15`, packrt-1 · git 밖)를 쓰는 시험(Runtime을 만들거나 그 자리를 읽는 시험, 지금 30개)은 자리가 없으면 시험마다 '벡터 DB 자리 없음' 한 줄 이유로 실패합니다. `C1A15_PACK_TESTS_WITHOUT_VECTOR_DB=1`일 때만 건너뜁니다(EV-002 CH-03). 태그 전 깨끗한 사본 시험(규칙 04)은 자리를 옆에 둔 사본에서 합니다
