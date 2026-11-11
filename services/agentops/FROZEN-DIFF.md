# AgentOps 코드 — 원본에서 바뀐 곳

이 폴더는 AgentOps 원본(llmops-lite-v2 22a9677)을 복사한 것입니다. 바뀐 곳은 아래 한 줄과 그 시험뿐입니다.

| 파일 | 바뀜 | 왜 |
|---|---|---|
| `llmops/config.py` `load_settings` | 리눅스에서 실제 모드(`LLMOPS_LLM_MODE=real`)는 원래 거절 — **`LLMOPS_ALLOW_REAL_ON_LINUX=1`일 때만** 허용. 이 값이 없거나 `1`이 아니면 원본 그대로 거절 | 도커 컨테이너는 리눅스라, 이 설정 없이는 AgentOps가 실제 모델을 쓸 수 없음(정확도 · 효율 개선이 실제 모델을 부름). 이 저장소의 `docker-compose.yml`만 켬 |
| `tests_repo/test_real_on_linux.py`(새 파일) | 위 조건 시험 넷(안 켬 → 거절 · `1` 아닌 값 → 거절 · `1` → 실제 · 리눅스 가짜 · 맥 실제는 그대로) | — |

- 끄려면: `.env`에서 `LLMOPS_LLM_MODE=fake`(AgentOps가 가짜 provider로 돎 · 점수 · 비용이 실제가 아님).
- 그 밖의 파일(`Dockerfile` · `start.sh`)은 이 저장소에서 새로 쓴 것이라 원본에 없습니다.
