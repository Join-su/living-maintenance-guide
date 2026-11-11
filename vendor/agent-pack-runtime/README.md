# agent-pack-runtime

Agent Pack을 돌리는 공통 엔진 **packrt**와 실행 기록기 **tracewriter**의 저장소입니다. LLMOps 서버, Deployment Server, Pack 저장소의 러너가 이 저장소의 같은 태그를 설치해 씁니다.

- `packrt/` — 패키지 `packrt`(pack.yaml 읽기 · 검사 · revision, 그래프 실행, trace 이벤트). 판 = `packrt.__version__`
- `tracewriter/` — 패키지 `tracewriter`(trace 이벤트를 PostgreSQL `trace`에 기록). 판 = `tracewriter.__version__`
- `PUBLIC-API.md` — 다른 저장소가 부를 수 있는 이름. 그 밖의 이름(특히 `_`로 시작하는 이름)은 안쪽 이름입니다.

## 설치

```sh
pip install "packrt @ git+<저장소 주소>@v0.15.0#subdirectory=packrt"
pip install "tracewriter @ git+<저장소 주소>@v0.15.0#subdirectory=tracewriter"
```

uv에서는 `[tool.uv.sources]`에 `{ git = "<주소>", tag = "v0.15.0", subdirectory = "packrt" }`처럼 적습니다.

## 판 · 태그 규칙

- **저장소 태그는 하나 = 저장소 판 `v<판>`** 입니다. 태그 메시지에 두 패키지의 판과 출발 커밋을 적습니다.
- **packrt `__version__`은 packrt 코드가 바뀔 때만 올립니다.** 이 판은 LLMOps 비교의 '같은 조건 — runtime'에 들어갑니다. 엔진이 그대로인데 판만 바뀌면 평가를 괜히 다시 돌려야 합니다.
- tracewriter만 바뀌면 tracewriter 판과 저장소 태그만 올립니다(예: `v0.9.1`, packrt는 0.9.0 그대로).
- 판은 **번호 + 태그**로 적습니다. 예: `packrt 0.9.0 @ v0.9.1 (커밋 앞 12자)`.
- 같은 번호에 다른 packrt 코드를 두지 않습니다. 태그는 다시 찍지 않습니다.
- 의존성 판 고정: langgraph 1.2.13 · langsmith 0.14.4(`packrt/pyproject.toml`). 쓰는 쪽(LLMOps · 배포서버 · 러너)은 이 판을 따릅니다.

## llm node 생각 정도(0.14.0)

- pack.yaml llm node `params.reasoning_effort`: `none` · `minimal` · `low` · `medium` · `high` · `xhigh` · `max`. 칸이 없으면 보내지 않습니다.
- models.yaml provider `models.reasoning: [<모델>]`: chat 목록 가운데 이 칸을 받는 모델입니다. 목록 밖 모델이면 보내지 않고, Pack 검사가 경고하며, trace `request_config`에 `reasoning_effort_skipped`를 남깁니다.
- 목록 모델은 node `max_tokens`를 `max_completion_tokens`로 받습니다. temperature는 Pack에 적은 대로 보냅니다(세기가 none이 아니면 검사 경고).
- provider `chat(...)`은 키워드 `reasoning_effort`를 보낼 때만 받습니다. 이 칸을 쓰는 Pack을 돌리는 가짜 provider는 `reasoning_effort=None`(또는 `**kw`)을 받아야 합니다. 효율 바퀴는 이 칸을 바꾸지 않습니다(보기만).

## 함수 node 옵션(0.15.0)

- `custom_code@3` node `params.options`: `{이름: {type, …, value, label?, loosen?}}` — type `choice`(choices 2 ~ 12) · `number` · `integer`(min · max 필수, step 선택) · `switch`. 이름은 `^[a-z][a-z0-9_]*$`, node마다 16개까지, label 40자까지, loosen은 판정이 느슨해지는 쪽 `up` · `down` · `none`(choice는 choices 뒤쪽이 up, switch는 false → true가 up).
- 선언한 node의 함수만 `options={이름: 지금 값}` 하나를 받습니다(매개변수 `options` 또는 `**kw` 필수). trace node_ended `request_config.options`에 그 값이 남습니다.
- 같은 그래프 안 같은 이름 옵션은 한 값입니다(선언 · 값이 node마다 같아야 함). 값은 patch op `set_option`(graph · node · option · value)으로 한도 안에서만 바꾸며, 같은 이름 자리가 함께 바뀝니다. 선언은 patch로 바꾸지 않습니다.
- `packrt.ir.node_options(folder 또는 doc)`이 선언 목록을 줍니다. 고정 흐름에는 `options:<graph>` 항목(선언만 · 값 빼고)이 생깁니다. 옵션이 없는 Pack은 0.14.0과 같습니다.

## 시험

```sh
python -m venv .venv && .venv/bin/pip install -e packrt -e tracewriter pytest   # 또는 uv
.venv/bin/pytest -q          # python -m pytest는 쓰지 않음: 저장소 맨 위의 packrt/ 프로젝트 폴더가 패키지를 가림
.venv/bin/python scripts/static_net_scan.py --root .
```

- 이 저장소의 시험은 최소 묶음입니다: 공개 이름이 있는지, import 경계(packrt → llmops 없음, tracewriter → llmops · packrt 없음), 시험 Pack의 revision 고정값, 가림 무늬, 정적 망 검사.
- 깊은 시험(실행 · 평가 · trace · DB)은 지금 llmops-lite-v2가 이 저장소의 태그 설치본으로 돌립니다(EV-023 결정, 사용자 H-4).
- CI(`.github/workflows/ci.yml`, 원격 저장소를 만든 뒤 켜짐): main push · 태그 · PR 때 위 시험과 정적 망 검사를 `ci/constraints.txt` 고정 판으로 돌립니다. 비밀 값은 쓰지 않습니다. 태그 push 때는 `scripts/check_tag.py <태그>`가 '주석 태그 · 받은 commit · 메시지 첫 줄의 packrt · tracewriter 판 = 코드 판'을 확인합니다. 의존 판을 올리면 로컬에서 시험을 통과한 판으로 `ci/constraints.txt`도 함께 바꿉니다.

## 이력

- llmops-lite-v2에서 떼어 냈습니다(EV-023, 2026-10-18). 이력은 처음부터 옮겼습니다(`git filter-repo`).
  - 옛 루트 `packrt/` → `packrt/packrt/`
  - `packages/packrt/` → `packrt/`
  - `packages/tracewriter/` → `tracewriter/`
- 커밋 메시지는 원래 llmops-lite-v2 커밋 그대로입니다. 메시지의 EV · CHG 번호는 그 저장소의 VFE 기록을 가리킵니다.
