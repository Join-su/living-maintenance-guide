# llmops-lite

LangGraph Agent 코드를 불러와 검증데이터나 입력값 세트로 실행하고, 채점·진단·권고를 보고 사람이 판정하며, 버전끼리 비교하는 로컬 LLMOps 제품입니다. 이 컴퓨터(127.0.0.1)에서만 열립니다.

## 준비 (처음 한 번)

Python 3.14 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
uv sync
```

## 실행

```bash
.venv/bin/python -m llmops
```

브라우저에서 http://127.0.0.1:8765 를 엽니다. 끌 때는 `Ctrl+C`를 누릅니다.

- **처음 써 볼 때:** Projects 화면의 **예시로 시작**을 누르면 내장 예시 두 가지로 바로 실행·결과·판정·비교·모니터를 해 볼 수 있습니다.
- **데이터 위치:** 데이터는 `.llmops-data/`에 쌓입니다.
- **기본 모드:** 기본은 **가짜 provider** 모드라 실제 모델을 부르지 않습니다.
  - 표준 base URL 환경변수(`OPENAI_BASE_URL` 등)를 따르는 Agent 코드는 로컬 가짜 provider로 갑니다.
  - 호출 주소를 따르지 않는 코드는 실제 모델을 부를 수 있습니다.

### 설정 (실행할 때 환경변수)

| 변수 | 뜻 | 기본값 |
|---|---|---|
| `LLMOPS_PORT` | 포트 | `8765` |
| `LLMOPS_DATA_DIR` | 데이터 폴더 | `.llmops-data` |
| `LLMOPS_OPENAI_API_KEY` | OpenAI 키(LLM 채점·입력값 생성·suite 모델 검토). 없으면 `LLMOPS_API_KEY`도 OpenAI 키로 읽음 | 없음 — 화면 **설정**에서 provider별 임시 키를 넣을 수도 있음(끄면 사라짐) |
| `LLMOPS_ANTHROPIC_API_KEY` | Anthropic(Claude) 키 | 없음 |
| `LLMOPS_GEMINI_API_KEY` | Google Gemini 키 | 없음 |
| `LLMOPS_LLM_MODE` | `fake` 또는 `real` | `fake` |
| `LLMOPS_LLM_BASE_URL` | real 모드에서 LLMOps가 부를 OpenAI 호환 주소 | 없음 |
| `LLMOPS_ANTHROPIC_BASE_URL` | real 모드의 Anthropic messages 주소(`…/messages`를 붙여 부름) | 없음 |
| `LLMOPS_GEMINI_BASE_URL` | real 모드의 Gemini 주소(`…/models/<모델>:generateContent`를 붙여 부름) | 없음 |

**real 모드 사용 시 주의**
- real 모드는 실제 provider로 데이터와 키를 보냅니다.
- 실행하려면 `LLMOPS_LLM_MODE=real`과, Project가 쓰는 provider의 주소·키(예: OpenAI는 `LLMOPS_LLM_BASE_URL`·`LLMOPS_OPENAI_API_KEY`)가 필요합니다.
- provider는 Project마다 고릅니다(LLM 채점을 켤 때). 고르지 않은 Project는 OpenAI와 기본 모델을 씁니다.
- Agent의 모델 주소·키는 Agent가 자기 설정에서 읽습니다.
- 모드는 제품 밖 설정으로만 바뀌며, 화면에는 바꾸는 조작이 없습니다.

## 구성

- `llmops/` 서버(FastAPI). 주요 파일은 다음과 같습니다.
  - 그래프 읽기: `graphread.py`, `graph_probe.py`
  - Run 실행: `runs.py`, `runner.py`
  - 채점·진단·권고: `evaluation.py`
  - 비교: `compare.py`
  - LLM 게이트웨이와 가짜 provider: `gateway.py`
  - 키: `keys.py`
  - 가리기·업로드 복사·Agent sandbox: `guard.py`
  - 저장소(SQLite): `store.py`
- `web/` 화면(vanilla JS, 빌드 단계 없음)
- `examples/` 내장 예시 Agent(실제 LangGraph 코드)
- `tests/` pytest(단위·통합·adversarial)와 `tests/e2e/`(Playwright)
- `scripts/` 격리 실행(`isolated-env`), 네트워크 감시(`netguard/`), sandbox profile, 정적 검사, 자동 게이트(`run_gates.py`)와 판정기 검증(`validate_judges.py`)

## 검사 (유지보수·버전업)

검사는 반드시 `scripts/isolated-env`를 거쳐 실행합니다. 이 방식은 허용된 환경변수만 넘기고, 가짜 키만 쓰며, 외부 네트워크를 막습니다.

```bash
scripts/isolated-env -- scripts/check-fast.sh   # 단위·통합 (pytest)
scripts/isolated-env -- scripts/verify-e2e.sh   # 브라우저 검사 (Playwright, npm install 후 .playwright-browsers/에 Chromium 필요)
.venv/bin/python scripts/run_gates.py --slice <S-xx> --run-id <id> --allowed 'llmops/*' 'web/*' 'scripts/*' 'tests/*' 'examples/*' ...
```

개발 규칙은 `CLAUDE.md`를 봅니다.
