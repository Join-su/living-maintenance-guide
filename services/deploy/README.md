# llmopsagent-deploy — Deployment Server

LLMOpsAgent 고리의 6~7단계를 맡습니다.
- 6단계: 배포단이 태그된 Pack 버전으로 요청을 처리합니다.
- 7단계: 운영 기록이 logDB(trace)에 쌓입니다.

이 서버는 정비 담당자의 사건(EventCard)을 받아 packrt로 Pack을 실행하고 보고서를 돌려줍니다.

- API 계약(정본): `docs/api-contract.md`(로컬 문서 — git에 올리지 않음, 규칙 02)
- 015 뒤 바꾼 곳·남은 것: `docs/after-015.md`(로컬 문서)
- 기준 문서는 llmops-lite-v2에 있습니다. 여기에 복사하지 않고 읽기만 합니다.
  - `docs/shared-db-design.md` v1.0
  - `docs/contracts/trace-event-contract.md`(trace 계약 2)
  - `docs/contracts/llmops-decision-contract.md`

## 운영 1단계 범위
- 사람 검토: 자동 처리(`actor_kind: auto`). 답 검사 통과면 자동 승인, 답 검사 실패 초안은 자동 escalate입니다. 응답과 보고서 정보에 "…(사람 검토 아님)"이 표시됩니다(API 계약 2절).
- 배포 id
  - `task10@dev-local`(`config/dev-local.yaml`, 태그 `v0`(commit `93ca0a1`), 원문 보존 `dev` 7일)
  - `task10@v0-local`(`config/v0-local.yaml`, 태그 `v0`, 원문 보존 `standard`)
  - `task10@v0-pg-local`(`config/v0-pg-local.yaml`, 태그 `v0` + PostgreSQL 실행 설정 `postgres-table`·`pgvector`, 실행용 DSN `LLMOPSAGENT_RUNTIME_DSN`, 원문 보존 `standard`). 등록·실제 요청은 VFE run EV-001(packrt 0.8.0)
  - `task10@v1-pg-local`(`config/v1-pg-local.yaml`, 태그 `v1` commit `6628470`(10-19 이력 정리 뒤 번호), revision `sha256:3e315c04…`, `v0-pg-local`과 같은 PostgreSQL 실행 설정·drs). **2026-10-15 실제 배포**(지시 016, VFE EV-002) — 등록 줄은 llmops가 배포 승인 근거로 씀(S-20), 배포단은 읽어 확인하고 상태 파일 `status/deployments/<change id>.json`에 `registered`·`switched`를 씀
- 서버 코드는 `llmops`(서버·화면 패키지)를 import하지 않습니다. 허용은 packrt·공유 기록기뿐이며 시험이 막습니다(D-13).
- 수집 API·outbox·checkpoint·등록 대기는 없습니다. 기록기가 직접 추가합니다.
- packrt와 공유 기록기는 만들지 않고 가져다 씁니다(소유: packrt 세션, 저장소 `agent-pack-runtime` = `https://github.com/Join-su/agent-pack-runtime.git`). 배포단 전용 `.venv`에 그 저장소 태그 고정·비편집으로 설치합니다(`pinned.json`: 지금 태그 `v0.15.0` commit `d3bc887`, packrt 0.15.0(node_options · reasoning_effort 칸 — D-475 · D-476), tracewriter 0.1.4(체크 기록 판 2), langgraph 1.2.13 · langsmith 0.14.4 — D-416 다시 고정 2026-10-27, 빌드 backend `setuptools==84.0.0`(이미지 해시 고정 backend와 같은 판). 되돌림용 그 전 고정: `v0.12.4` `2fbb680`(D-416, 2026-10-27) · `v0.12.2` `4278933`(D-397, 2026-10-26) · `v0.12.1` `9569561`(D-319, 2026-10-25) · `v0.11.0` `e6f5a26`(D-179 · D-201, 2026-10-21) · `v0.10.0` `be6cd94`(답장 019-11))
  - 실행: packrt `Runtime.run(…, sink=, on_artifact=)`
  - 기록: tracewriter `TraceWriter` → PostgreSQL `trace`(`trace_writer` 계정, DSN은 `llmops-lite-v2/db/.env`에서 읽음, 출력하지 않음)
  - 등록 대조(`deploy_server/registration.py`, 서버 · `deploy-status` · `es3_check`가 같은 함수로 세 칸 대조, VFE EV-005 ES-E): 설정 `registration.mode`
    - `llmops_db`: `llmops-lite-v2/.llmops-data/llmops.db`의 `deployment` 표(읽기 전용) — `dev-local` · `v0-local` · 예행연습(A5 뒤 llmops.db는 이전됨 표시, 더 쓰이지 않음). 등록은 llmops `python -m llmops.admin deploy-bootstrap`
    - `llmops_pg`: PostgreSQL `llmops.deployment`(llmops 변경 파일 0005 · 0006)를 `runtime_reader`(`LLMOPSAGENT_RUNTIME_DSN`, 실행용과 같은 이름 · 같은 찾는 곳)로 SELECT만 — Mac `v0-pg-local` · `v1-pg-local`(답장 031-01, D-153)과 VPS 설정(`config/*-pg-vps.yaml`). 등록은 llmops

## 실행 환경
- **전용 가상환경 `.venv`(git 밖)**
  - `scripts/py`(서버·시험·도구 공통)가 씁니다.
  - Pack 실행 설정의 Python도 이 환경입니다. 서버의 `sys.executable`을 쓰기 때문입니다.
- **설치·재고정:** `scripts/install-venv.sh <packrt 저장소 태그> [<llmops-lite-v2 commit 40자>]`
  - packrt·tracewriter는 `agent-pack-runtime`(기본 `../agent-pack-runtime`, `PACKRT_REPO`)의 **같은 annotated 태그 하나**에서 `git archive` 사본으로 비편집 설치합니다. 태그가 아닌 이름(가지·commit·lightweight 태그)은 받지 않습니다.
  - 제3자 패키지는 llmops commit(기본 = `pinned.json`의 `third_party.llmops_commit`, 지금 `0867ce0` — 10-19 llmops 이력 정리 뒤 번호, `uv.lock` 바이트 같음)의 `uv.lock` 판입니다. 단 packrt·tracewriter `pyproject.toml`이 `==`로 고정한 판(langgraph · langsmith — 둘 다 없으면 멈춤)은 그 판을 씁니다. 배포단에 필요한 것만 넣습니다.
  - 설치 전에 해석 결과(`uv pip compile`)를 지금 `requirements/deploy-venv.txt`와 비교해, 새 패키지 종류가 있으면 종료 3으로 멈춥니다(venv·기록 그대로, 승인 범위 밖). 설치 뒤에는 `uv pip check`와 같은 비교를 다시 합니다.
  - 기록 파일은 모든 확인이 끝난 뒤에만 바꿉니다. export·packrt 설치가 실패하면 기록은 그대로입니다.
  - 사본 예행연습: `DEPLOY_VENV_DIR=<시험 venv> DEPLOY_PIN_OUT=<기록 폴더>`로 다른 곳에 설치합니다. `INSTALL_PLAN_ONLY=1`은 태그 → commit · 제약만 보이고 설치하지 않습니다.
  - Python은 llmops `.venv`와 같은 인터프리터를 쓰고, 내려받지 않습니다.
  - 기록 파일은 `pinned.json`(태그·commit·tree·판·packrt 고정 판·uv.lock sha256·Python)과 `requirements/deploy-venv.txt`(설치된 판)입니다.
  - 재고정은 오케 시작 알림 + 그 설치에 대한 사용자 승인 때만 합니다(지시 008 · 019). 재고정 뒤에는 시험 전부·`scripts/check-pin.py`·Mac 운영 확인을 다시 하고, status·보고에 태그·판을 적습니다. drs가 바뀌거나 알림과 다른 판이면 멈추고 지시를 받습니다.
- **고정 확인(V-14):** `scripts/py scripts/check-pin.py`
  - 확인: 태그 객체·commit이 `pinned.json`과 같음(다시 찍힌 태그 거부)·tree, packrt·tracewriter 파일 바이트·판·비편집·`.venv` 안, langgraph·langsmith 판 = packrt 고정 판, llmops `uv.lock` sha256, 설치된 패키지 전부 = `requirements/deploy-venv.txt`
  - 출력은 일치/불일치와 다른 파일 이름뿐입니다.
- **되돌림(0.8.0으로):** 커밋 되돌리기 + 옛 `pinned.json`으로 `.venv` 다시 고정(이것도 설치라 사용자 승인). `DEPLOY_VENV=<다른 venv>`로 다른 환경을 쓰면 시험 `test_environment`는 사유를 밝히고 건너뜁니다.
- `.venv`가 없으면 `scripts/py`가 안내하고 멈춥니다. 조용히 공유 환경을 쓰지 않습니다.

## 비밀 값(`.env`)
- **배포단 자체 `.env`(git 밖, 권한 600)**
  - `LLMOPSAGENT_RUNTIME_DSN`(runtime_reader, PG 실행 설정의 store 읽기)과 `LLMOPSAGENT_TRACE_DSN`(trace_writer, 기록) 두 줄만 있습니다.
  - 설정 4개의 `trace.env_file`·`runtime_db.env_file`이 이 파일을 가리킵니다. 다른 DSN(owner·superuser·loader)은 없습니다.
- **만들기:** `scripts/make-env.sh`
  - llmops-lite-v2 `db/.env`에서 두 이름만 값 출력 없이 옮깁니다.
  - 이미 있으면 `--force`로만 덮습니다. llmops가 비밀번호를 바꾸면 다시 만듭니다.
- **확인:** `scripts/py scripts/check-env.py`(권한·이름·값 같음 여부·git 추적 밖, 값은 출력하지 않음)
- 시험의 DB 관리(시험 DB 만들기·지우기)는 시험 도구라서 llmops `db/.env`의 관리자 계정을 그대로 씁니다. 서버는 쓰지 않습니다.

## 예행연습(태그 v1, 격리)
- **설정:** `config/v1-pg-rehearsal.yaml`·`config/v0-pg-rehearsal.yaml`
  - Pack 저장소는 사본 `../.rehearsal/task10-pack-v1`입니다.
  - 등록은 `var/rehearsal/llmops.db`(사본), 기록은 `var/rehearsal/trace.env` → 임시 DB입니다.
- **도구:** `.vibe-factory/evolutions/EV-004-own-env-and-v1-rehearsal/verification/rehearsal.py`(로컬 작업 기록 — git에 올리지 않음)
  - 명령: `prepare` → `bootstrap-config` → `register` → `run` → `inspect` → `cleanup` → `verify-isolation`
  - 실제 llmops.db·개발 logDB에는 쓰지 않습니다. register 직후, 서버를 켜기 전, 첫 요청 직후, 마지막에 확인합니다.
- **결과·실제 실행 때 바꿀 것:** 같은 run의 `verification/rehearsal-result.md`·`real-run-changes.md`

## 배포 상태 파일(배포 승인 화면 3·4단계)
- **무엇:** LLMOps 배포 승인 창의 3단계 '배포단 등록'·4단계 '운영 전환'을 화면에 단계로 보이게 하는 파일입니다. LLMOps는 읽기만 합니다(지시 010, VFE EV-002 CHG-08).
- **경로:** `status/deployments/<id>.json`(git 밖). `<id>`는 승인된 변경 요청 id(`approved-change@1`의 `change_id`)입니다.
- **형식(세 세션 공통 9칸):** `{"id","version","revision","stage","detail","commit","tag","deployment_id","updated_at"}`
  - 이 저장소가 쓰는 stage는 `registered`·`switched`·`failed`입니다(`requested`·`applied`·`tagged`는 task10-pack).
- **도구:** `scripts/py scripts/deploy-status.py <registered|switched|failed|show> --id <id> --version V1 --config config/v1-pg-local.yaml [...]`
  - `registered`: 등록 원장(llmops.db `deployment` 또는 PG `llmops.deployment` — 설정 `registration.mode`)을 읽기 전용으로 열어, 설정의 배포 id와 `expect_revision`이 같은 줄이 있을 때만 씁니다.
  - `switched --run-id <run_id>`: `registered` 뒤에만 씁니다. 서버 실행 요약(`data_dir/runs/<run_id>.json`)이 같은 배포·revision·tag이고 succeeded여야 합니다.
  - `failed --detail "<한 줄>"`: 등록 도구 거부·서버 시작 실패처럼 도구 밖에서 멈춘 경우에 씁니다. 접속 정보처럼 보이는 글은 받지 않습니다.
  - 실패 글귀(EV-002 v12): 화면 단계 창 칸에 맞춰 ③ "배포단 등록 실패 — …"(등록 확인, ES-3), ④ "운영 전환 실패 — …"(운영 전환, ES-4)로 시작합니다. 도구가 스스로 쓰는 실패 글귀도 같습니다. ③ 이유에는 '전환'이라는 말을 쓰지 않습니다(LLMOps는 '전환'이 있으면 ④로 표시). ES-2에서 멈추면 상태 파일을 쓰지 않습니다(③ 대기).
  - 대조에 실패하면 `failed`를 쓰고 종료 코드 1, 사용 오류는 아무것도 쓰지 않고 종료 코드 2입니다.
  - 기본 폴더는 실제 등록 원장을 대조하는 설정에서만 씁니다: 실제 llmops.db, 또는 이 저장소 `.env`의 `runtime_reader`로 PG 장부를 읽는 Mac `*-pg-local`. 예행연습·시험 설정과 VPS 설정은 `--status-dir`를 따로 줍니다(예: `var/rehearsal/status/deployments`, VPS `/var/lib/status/deployments`).
- **언제:** 실제 태그 v1 등록 직후 `registered`, v1 첫 요청(C01) 201 직후 `switched`입니다.

## C1-a15 VPS 배포(단계 A 정의, D-334 — VPS 손 · 이미지 · Pack 받기는 사용자 승인 뒤)
- compose 서비스 `deploy-c1a15-v1-pg`(standby · `127.0.0.1:8786` · `ORT_DISABLE_TELEMETRY=1` · 벡터 DB 호스트 사본 `/srv/llmopsagent/c1a15-releases` 읽기만) · 전용 망 `c1a15-edge`(이 서비스만, 앱이 external로 붙음 — 앱 URL `http://deploy-c1a15-v1-pg:8786`) · 설정 `config/c1a15-v1-pg-vps.yaml`(`c1a15@v1-pg-local` · Pack v6 deploy.yaml 그대로 · 가짜 DB flat-local · 가짜 provider).
- 아직 없는 것(승인 거리): Pack v6를 VPS가 받는 길(원격 · 열쇠 ⑦) · 이미지의 onnxruntime · 벡터 DB 폴더 옮기기 · 맥 amd64 예행. 단계 B(공유 DB) · C(실제 DB · 키)는 단계마다 승인. 계획 = 기록 git `C1A15-VPS-E1-E3.md`.

## VPS R12(`scripts/vps/deploy-check r12`, D-325 — VPS 손은 C1-a15 VPS 배포 날)
- 호스트 한 줄이 deploy-tools 일회용(`docker/compose.r12.yaml` 덧파일: 대상 data volume · 받은 편지 호스트 폴더 읽기만 · `index-loader.env` 읽기만)에서 `r12-update update`를 돌리고, 운영 설정이 바뀌면 그 서비스를 `compose restart` → `/info` drs 대조 → 다르거나 응답 없으면 `.before-r12` 옛 설정으로 되돌려 다시 띄웁니다. 새 편지가 없으면 서비스 손 0.
- 깨우기는 root crontab 10분 + `flock`(UTC). 사용자 손 순서 · 맥 예행 기록은 기록 git `R12-VPS-wiring-E1-E3.md`.
- **장부(llmops EV-044, D-326 · D-328):** 맥 `--ledger-cmd "cd ../llmops-lite-v2 && LLMOPS_STORE=pg .venv/bin/python -m llmops.admin data-release-auto --deployment-id {deployment_id} --pack-revision {pack_revision} --drs {drs} --evidence {evidence}"`(llmops 0010 적용 전에는 종료 1 → R12는 운영 설정 그대로 멈춤). VPS는 `deploy-check r12 --ledger-at-host` — R12가 장부 앞에서 멈추고(`--until-ledger`) 호스트가 llmops 컨테이너에서 장부를 쓴 뒤(증거 JSON = stdin) `after-ledger`로 이어 감. 증거 파일 10칸(+ `manifest_sha256` · `config_sha256` · `model_release_id`, packrt 계산). 묶기 대조는 장부 요청 앞 · 되돌리면 '쓴 장부 줄이 돌지 않음' 알림. 서버 Run 대조는 장부에 모델 칸이 있으면 모델도(옛 null 줄은 받아들임).

## 운영 되돌리기(VPS — `scripts/vps/deploy-check rollback`, VFE EV-005 ES-R · D-140)
- **무엇:** 운영 중인 V1을 이미 등록된 V0으로 되돌립니다(반대쪽도 같은 명령). LLMOps 29쪽 운영 V1 띠의 '이전 버전(V0)으로 되돌리기' 확인 창 뒤에, 사용자가 VPS에서 한 줄로 돌립니다. 되돌리기 id는 LLMOps가 만들고, 배포단은 상태 파일 이름으로만 씁니다.
  ```sh
  DEPLOY_TAG=… PACKRT_TAG=… DEPLOY_COMMIT=<deploy 태그 40자> scripts/vps/deploy-check rollback --id <되돌리기 id> --version V0 \
      --config config-live/v0-pg-vps.yaml --status-dir /var/lib/status/deployments \
      --service deploy-task10-v0-pg-local --old-service deploy-task10-v1-pg-local
  ```
- **deploy-tools 안 확인(restore-check):** 운영 설정 · expect_revision(seed로 채운 것) → 되돌리기 id 파일(이미 `switched`이거나 다른 배포의 파일이면 멈춤) → 지금 운영(`switched` 중 `updated_at` 시각이 가장 늦은 것 = 대상이면 멈춤, 읽을 수 없는 상태 파일이 있으면 멈춤) → Pack 캐시 태그 commit(fetch 없음) → V-04(`es3_check`) → 되돌리기 id `registered`.
- **그 뒤:** `switch`와 같습니다(대상 서비스 기동 → C01 → `switched` → 그 뒤에만 옛 서비스 내림). `--old-service`는 꼭 줍니다.
- **빌드 commit 대조(F-10):** 모든 deploy-check 명령은 빌드 전에 `DEPLOY_TAG` · `PACKRT_TAG` · `DEPLOY_COMMIT`(deploy 태그의 40자 commit, 승인 알림 값)을 봅니다. 빌드는 ② 받은 deploy 태그 commit = `DEPLOY_COMMIT`(Dockerfile 셸), ① 설치된 packrt · tracewriter의 `direct_url.json` commit = `pinned.json` `packrt_repo.commit`(`scripts/build_commit_check.py`)이 아니면 멈춥니다 — 태그 보호를 못 쓰는 비공개 저장소에서 태그 바꿔치기를 막습니다.
- **이미지 나머지(답장 030-01):** GitHub 호스트 키 `docker/github_known_hosts`(pack-10 `ci/runner/github_known_hosts`와 같은 키 줄, 공개 지문은 시험이 대조) · `pip check` · 시험용 패키지(pytest · pluggy · iniconfig · pygments) 뺌 · `scripts/py`가 이미지 venv(`DEPLOY_VENV=/opt/venv`)를 씀 · 모든 서비스 `traefik.enable=false`.
- **바뀌지 않는 것:** V-17('revision ≠ V0')은 register에만 남고, 실패 글귀 ③ · ④와 상태 파일 형식은 그대로입니다. 멈추면 상태 파일을 쓰지 않거나 지금 글귀로 `failed`를 쓰고, 옛 서비스는 그대로 운영합니다.
- **전제:** 장부의 대상 배포 줄 commit이 40자 또는 빈 칸이어야 V-04를 통과합니다(옛 SQLite 장부의 `task10@v0-pg-local` 줄은 7자였고, A5 PG 이전 때 새 40자 `93ca0a1…`로 옮겨짐 — 10-19 실제 PG 읽기 확인, GAP-009 해소). 시연은 VPS 운영(ES-G) 뒤입니다(GAP-008).

## 실행

```sh
scripts/test.sh                 # 시험(가짜 provider + DB 없는 가짜 기록기, 네트워크 없음)
scripts/test-db.sh              # logDB · PG 등록 대조 시험(로컬 PostgreSQL, 시험마다 새 DB llmopsagent_t_*를 만들고 지움 — 공유 DB · 계정은 건드리지 않음)
scripts/serve.sh                # 개발 서버 127.0.0.1:8780 (DEPLOY_CONFIG, DEPLOY_PORT)
curl -s -X POST http://127.0.0.1:8780/runs -H 'Content-Type: application/json' \
     -H "Idempotency-Key: $(uuidgen)" -d @event_card.json
```

## 구성
| 파일 | 하는 일 |
|---|---|
| `deploy_server/app.py` | FastAPI 앱. `POST /runs`(201에 `result` 포함, API 계약 v0.9), `GET /runs/{run_id}`, `GET /reports/{report_id}`, `GET /runs` · `/info`(`releases`) · `/health` · 피드백 |
| `deploy_server/pack_source.py` | Pack 버전(태그·commit)을 `git archive`로 꺼내 읽기 전용 사본(`.packs/`)에 둠. 실행 설정 작성 |
| `deploy_server/execution.py` | 설정 `execution` 검사(파일·PostgreSQL backend, 비밀 값 없음), 실행용 DSN 한 줄 읽기·가림 목록, 프로세스 환경의 다른 DB 비밀 빼기 |
| `deploy_server/runner.py` | packrt `Runtime.run` 어댑터. 결과 → 응답 모양(지나간 node, 검토 기록, 오류 칸) |
| `deploy_server/stores.py` | 반복 요청 키·실행 요약·보고서 파일(`data_dir`) |
| `deploy_server/fake_provider.py` | 가짜 provider(task10-pack EV-007 규칙과 같음) |
| `tests/fake_writer.py` | DB 없는 시험용 기록기(tracewriter 부품 사용, 제품 코드는 안 씀) |
| `tests/test_logdb.py` | logDB 시험(PostgreSQL `trace`에 쌓이는지) |
| `config/*.yaml`(`dev-local`, `v0-local`, `v0-pg-local`, `v1-pg-local`, 예행연습 `*-rehearsal`) | 배포 설정(비밀 값 없음, 환경 변수 이름만). 서버 하나가 파일 하나를 씀(`DEPLOY_CONFIG`) |

`.packs/`(Pack 사본)와 `var/`(서버 데이터)는 git에 올리지 않습니다.

## R12 새 이력 자동 반영(맥, 지시 046 · D-283)
- `scripts/py scripts/r12-update serve --config config/<배포>.yaml` → 운영 설정 `config-live/<같은 이름>`(git 밖)으로 서버를 띄움(pid 기록).
- 앱이 승인 보고서를 `<data_dir>/r12-inbox/<report_id>.json`에 넣으면 `scripts/py scripts/r12-update update --config … --store <이력 store> --graph <graph> --node <retrieve@2 node> [--ledger-cmd …]`가 새 corpus · 새 벡터 DB version(packrt 0.12.0 `packrt.index`, 가짜 임베딩) → 장부 줄 확인 → 운영 설정 store release 한 칸 → 이 작업이 띄운 서버만 다시 시작.
- 실패면 운영 설정 그대로. 기록 = `<data_dir>/r12-state.json`. 받은 편지 문서 모양 = API 계약 v0.10 11-1.
- pgvector store(packrt 0.12.1, D-323): build 뒤 `load --check` → `load`가 더해짐 — `--loader-env-file <파일>`(그 파일의 `LLMOPSAGENT_INDEX_LOADER_DSN` 한 줄, 이름표 `docker/env/index-loader.env.example`)이 없으면 build 뒤 멈춤. corpus 찾기 = packrt `find_corpus` · 운영 설정 쓰기 전 묶기 대조는 PostgreSQL store면 DB까지(API 계약 v0.12 11-2). 시험: `DEPLOY_PG_CONTAINER=1 scripts/test.sh tests/test_r12_pg_container.py`(일회용 pgvector 컨테이너, 끝나면 지움).
