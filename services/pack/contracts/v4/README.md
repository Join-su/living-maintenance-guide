# C1-a15 Pack 약속 v4 초안 r3 — start 다리 뺌 · retrieval 칸(Pack 리트리버 retrieve@2)

- 쓴 세션: c1a15-pack · 오케 답장 012-03(D-282 앞당김) · 정본 D-266 · D-267 · D-273 · D-277 · D-278 · D-285 · D-287 반영
- **r2(10-25):** D-291 확정값 · D-303(store `index.embedding`) · D-305(이력 `cause` = metadata · `return_metadata [cause]`) · D-301(벡터 DB는 packrt-1이 build) 반영 — E3-A13 ISS-1301. 첫 초안(`../contract-v4/`)은 그대로 둡니다. 
- **r3(10-25):** E3-A14 ISS-1401 — 3절 store 선언에서 `filterable`을 store 바로 아래로 옮김 · 5 · 7절 지난 시점 글 고침 · 예시 `vector_db_version`은 자리 글이라고 적음. PS-02에서 이 r3를 `contracts/v4`로 옮깁니다(r2는 그대로 둠).
- **초안입니다.** Pack 코드 0 · 태그 0 · 설치 0
- 파일(이 폴더):
  - `run_request.json` · `run_result.json`: 약속 v3 정본에서 만든 초안 schema
  - `examples/` 12개(r2: `start-empty.result.json` — similar 0건, 지금 Pack 판정으로 만든 값)
    - `start.result.json`의 `vector_db_version` `ir-history-0a1b2c3d` · 점수는 **모양을 보이는 자리 글**입니다. 실제 값(시험 = 가짜 DB `ir-history-412ea43e`)은 PS-02 새 기대값 목록(③)에서 정합니다.
  - `check_v4_draft.py`: 시험 31가지(r2: 예시 하나 더), 모두 통과
- 읽은 근거(읽기만):
  - **packrt `v0.12.0` 태그 `fc5abfa`** 의 git 객체:
    - `packrt/run/engine.py` `_n_retrieve2`(646 ~ 662): 결과 조각 · `meta.retrievals`
    - `packrt/run/backends.py` `search2`: 점수 · 순서 · `return_metadata`
    - `packrt/index/build.py`: 이력 조각 id `<record_id>#0` · locator
    - `packrt/ir/pack.schema.json`: retrieve@2 params · store index
  - 앱 `d9e8022`(deploy-wiring) `deploy/runs.py` start_body · `api/sessions.py` 255 ~ 275: retrieval을 통째로 저장
  - Pack `pack/custom/judge.py` 34 ~ 35: v3 start는 similar가 없으면 `retriever_unavailable`

## 결론
1. **`start`의 상위 10건은 Pack 리트리버(retrieve@2, store `history`)가 고릅니다.** 앱 임시 리트리버 결과(P1 다리)는 쓰지 않습니다.
   - `similar` 모양 `{record_id, cause, score}`은 그대로입니다. 그래서 `judge` · `report` 입력도 그대로입니다.
2. **`retrieval` 칸은 Pack state에서 사실대로 채울 수 있는 넷만 둡니다**(아래 2절):
   - `source: "packrt"`
   - `vector_db_version`(= 조각의 `index_release`)
   - `query_from`
   - `fallback_used`
   v3의 `top_k`는 뺍니다. 방식 · 상위 k · recipe sha · 다시 찾기 사유 · 순위 · locator는 **trace(LLMOps `retrievals`)에만** 있습니다.
3. **요청 schema 모양은 v3와 같습니다(H-5 (나) 권장).** `start.similar`는 받아도 쓰지 않습니다.
   - v3 Pack은 similar 없는 start를 실패로 끝냅니다. 그래서 v4를 v3로 되돌릴 때 앱이 손댈 것이 0이 되게 합니다.
   - 칸을 아예 빼는 것은 앱이 임시 리트리버를 걷어 낸 뒤(3단계) 새 version으로 합니다.
4. `alarm` · `judge` · `report`는 v3와 같습니다. v3 예시가 v4 schema에서도 모두 맞습니다(시험).

## 1. v3 → v4 바뀌는 곳
| 칸 | v3 | v4 초안 |
|---|---|---|
| `start.similar`(요청) | 선택 · P1 다리. 있으면 그대로 돌려주고, 없으면 Run 실패 | **받아도 쓰지 않음**(trace 입력에만 남음). 상위 10건은 retrieve@2가 고름 |
| `start.query` | `{direction, sensor_snapshot 5칸}` | 그대로. 질의 글은 Pack이 증상군 이름 · 방향으로 만듭니다. 센서 재정렬 7칸은 Pack이 계산합니다(`temp_diff_k` · `power_w`는 alarm과 같은 식) |
| `result.similar`(start) | 받은 순서 그대로 | retrieve@2 상위 10건 — 2절 대응표 |
| `result.retrieval` | `{source: packrt\|request, vector_db_version\|null, query_from: embed\|release\|null, top_k}` | **`{source: "packrt", vector_db_version, query_from: embed\|null, fallback_used}`** — `top_k` · `request` · `release` 뺌 |
| `judge` · `report` · `alarm` | — | 바뀜 0 |
| 점수 값 | 앱 리트리버 점수 | **바뀜.** packrt-1이 build한 이력 DB(시험 = 가짜 `ir-history-412ea43e`)의 retrieve@2 점수(소수 6자리)입니다. 기대값 목록을 새로 잡고 사람이 승인합니다(PS-02 별도 승인 ③) |

## 2. retrieve@2 결과 → v4 칸(packrt v0.12.0 `fc5abfa`)
retrieve@2 node는 state에 조각 목록 하나만 씁니다(`writes[0]`). 조각마다 `{source_type, store, id, index_release, score, text, locator, ranks{lexical, vector, fused}, metadata, fallback_used}`입니다. Pack 끝 node(custom_code)는 **이 목록만** 보고 아래처럼 옮깁니다.

| v4 칸 | 어디서 | 비고 |
|---|---|---|
| `similar[].record_id` | 조각 `locator.record_id` | 조각 id는 `<record_id>#0`입니다. 출력에는 record_id를 씁니다. record_id는 corpus 문서의 바깥 칸이라 metadata에 실리지 않습니다(packrt-1 002-01) |
| `similar[].cause` | 조각 `metadata.cause`(`return_metadata: [cause]`) | `index.metadata`에는 넣고 `filterable`에서는 뺍니다. 필터 · 순위에 쓰지 않습니다(G-8 · D-287 · D-305). 확정 원인이 없는 이력은 null입니다 |
| `similar[].score` | 조각 `score`(최종 점수, `round(·, 6)`) | rerank가 있으면 `w_retrieval · base ÷ max(base) + w_sensor · (1 − d ÷ max d)`입니다(D-273, 앱 R3과 같은 셈) |
| 순서 | packrt가 `(−score, 조각 id)`로 정렬 | 조각 id = `record_id#0`이라 '같으면 record_id 순'(PP-09)과 같습니다 |
| `retrieval.vector_db_version` | 조각 `index_release` | 모든 조각이 같은 값입니다. similar가 비면 null |
| `retrieval.query_from` | 조각 `ranks.vector`가 하나라도 있으면 `embed` | lexical 방식이거나 similar가 비면 null. 미리 계산한 질의 벡터(G-3)는 0.12.0에 없습니다(11-01 뒤 새 version) |
| `retrieval.fallback_used` | 조각 `fallback_used` | similar가 비면 null |
| (출력 없음) `method` · `top_k` · `index_recipe_sha256` · `fallback_reason` · `ranks` · `locator` · `source_type` · `text` | `meta.retrievals` = trace만 | node param · release manifest 값이라 state에 없습니다. 출력에 넣으려면 Pack이 param을 따로 적어야 하는데, 그러면 바퀴 ①이 param을 바꿀 때 출력과 실제가 어긋납니다(H-6). 리트리버 채점은 trace locator로 합니다(이력 `{record_id}`, D-277) |

## 3. Pack 그래프 쪽 이름 · 값(약속 schema 밖이지만 닿는 곳이 씀 — 확정 제안)
- **store `history` · node `retrieve_history`**(012-02 잠정 → 이 초안에서 확정 제안). deploy R12 build 명령은 `python -m packrt.index build --pack … --graph run --node retrieve_history`이고, 오류 `error.where = run.retrieve_history`입니다.
- store `history`(pack.yaml `stores.history`):
  - `index`:
    - `split: record`
    - `embedding: {provider: openai, model: text-embedding-3-small}`(D-303, 매뉴얼 store도 같음). release id에 들어가므로 가짜 · 실제 DB가 같은 선언을 씁니다.
    - `metadata`: `evidence_type` · `source` · `symptom_group` · `cause`(r2부터 `record_id`는 뺌 — locator에 있음) · 센서 7칸 `air_temperature_k` · `process_temperature_k` · `rotational_speed_rpm` · `torque_nm` · `tool_wear_min` · `temp_diff_k` · `power_w`
    - `rerank_norm: {fields: 센서 7칸, from: {source: generated}}`
  - `filterable: [symptom_group]` — **store 바로 아래**(`stores.history.filterable`). `index` 안에 두면 packrt 0.12.0 schema 오류입니다(packrt-1 002-01 알림 1).
  - 이것이 D-287 받은 편지 문서 칸입니다(문서 metadata = 위 `index.metadata` 칸).
- retrieve@2 params: `filter {symptom_group}` · `return_metadata [cause]` · `top_k 10`. method · RRF k · 센서 가중 · 임계값 · 다시 찾기는 **앱 R3 설정값을 PS-02 기준선으로** 씁니다. 바퀴 ① 손잡이(약속 C)가 바꾸는 값입니다.
- 매뉴얼 DB 리트리버(PS-03, store `manuals`)는 trace에만 남기고 출력 칸은 0입니다. locator 기준은 D-277입니다.
- 0.12.0 retrieve@2는 flat-local만 됩니다(D-266). 이력 DB release는 파일 폴더입니다. pgvector로 옮기는 0.12.1에서도 **약속 칸은 바뀌지 않습니다**(store backend만 바뀜).

## 4. 실패
| 언제 | 무엇 | 배포 서버 · 앱 |
|---|---|---|
| Runtime 시작(불러오기) | `index_missing` · `index_ambiguous`(실행 설정 후보에 node recipe와 맞는 이력 DB가 없거나 둘 이상, D-267) · rerank_norm 없음(D-273) | **Pack 전체를 불러오지 못합니다. 그래서 `alarm` · `judge` · `report`까지 모두 503**입니다(onnxruntime이 없을 때와 같음). R12 새 release를 등록할 때 recipe가 맞는지 먼저 봐야 합니다 |
| start Run | `rerank_invalid`(질의 · 조각 센서 칸 없음) · `embedding_mismatch` | Run 실패 `500 run_failed`, `error.where = run.retrieve_history`. 앱은 지금처럼 세션을 만들지 않습니다 |
| 결과 0건 | 다시 찾기 뒤에도 0건 | 실패가 아닙니다. `similar: []` · retrieval 셋 = null · 첫 판정은 근거 0으로 냅니다(v3 start에 `similar: []`를 준 것과 같은 셈) |

## 5. 닿는 곳
| 쪽 | v4에서 할 일 | 언제 |
|---|---|---|
| **c1a15-app** | **요청은 바꿀 것 0입니다**(H-5 (나): start에 다리 similar를 계속 보내도 됨). 저장: `retrieval`을 통째로 저장하는 지금 길(`sessions.py` 273) 그대로. 칸이 넷으로 바뀝니다(`top_k` 없음 · `fallback_used` 생김) — 이 칸을 읽는 화면 · 시험이 있으면 고칩니다. `vector_db_version`이 이제 값을 가지므로, release 표시(E) · 진단 기록에서 앱 `app_release_id` 대신 이것을 쓸지 앱이 정합니다. 앱 임시 리트리버를 걷는 일은 3단계입니다(그때 새 version으로 start.similar 뺌) | v4 배포 뒤 |
| **deploy** | ① packrt 0.12.0(세 곳 설치 — D-301 승인) ② C1-a15 배포 설정 `execution/deploy.yaml`에 store `history` 묶음 — `release_roots`(찾는 곳: packrt-1이 build한 `releases-c1a15` 자리 + R12 새 release 폴더 — 배포 서버 자리는 deploy가 정함) · `releases`(고를 후보), D-267 고르기 ③ R12 (가′): 승인 → `packrt.index build … --node retrieve_history` → 새 release → register/switch. 새 release의 recipe가 node와 다르면 Runtime 시작에서 `index_missing`이고 모든 kind 503입니다(4절) ④ 진단 대조 · VC-C07에 start 예시 추가 ⑤ `grounding_keys = []` 그대로(D-278) | 0.12.0 설치 승인 뒤 |
| **llmops** | ① 등록 · 평가 환경 packrt 0.12.0(retrieve@2 node 등록) ② suite `c1a15-start` 기대값이 바뀝니다. Pack이 다시 만들고, 바뀐 목록은 사람 승인(③)입니다 ③ retrieve@2 trace(`retrievals`: method · recipe sha · ranks · locator)로 리트리버 채점 · 바퀴 ① 손잡이(method · RRF k · top_k · 임계값 · 센서 가중 · 다시 찾기)입니다. 이 값들은 출력이 아니라 release param · trace에서 읽습니다 | 0.12.0 설치 뒤 |
| **packrt** | 바뀜 0(0.12.0 retrieve@2 그대로). H-6 (나)를 고르면 retrieve@2 요약을 state에 쓰는 기능이 필요합니다(0.12.x 요청) | — |

## 6. 주인 확인 거리(오케 D-nn)
| # | 무엇 | 안 | 권장 |
|---|---|---|---|
| H-5 | `start.similar` 칸 | (가) v4에서 **두면 안 됨**(422). 앱이 배포 전환 때 함께 보내기를 끄고, 되돌릴 때 다시 켬 / (나) **받아도 쓰지 않음**, 칸 빼기는 3단계 앱 리트리버를 걷을 때 새 version / (다) plan v12 PS-02 글 그대로 — 있으면 다리를 씀(`source: request`), 없을 때만 Pack 리트리버 | **(나)**: v3 Pack은 similar 없는 start를 `retriever_unavailable`로 실패시킵니다. (가) · (다)는 Pack 리트리버를 쓰려면 앱이 보내기를 꺼야 하므로, v4 → v3로 되돌릴 때 앱 설정도 함께 바꿔야 세션이 열립니다. (나)는 배포 서버만 되돌리면 끝납니다 |
| H-6 | `retrieval` 칸 범위 | (가) **state에서 채울 수 있는 넷만**(이 초안) · 나머지는 trace / (나) method · top_k · recipe sha · 다시 찾기 사유도 출력에 — packrt에 retrieve@2 요약을 state에 쓰는 기능 요청, 또는 Pack이 param을 따로 적음(바퀴가 바꾸면 어긋남) | **(가)**: 앱은 retrieval을 저장만 하고 읽지 않습니다(`d9e8022`). 리트리버 채점 · 바퀴는 trace를 읽습니다 |
| H-7 | store · node 이름 | `history` · `retrieve_history`(3절) | 그대로 확정: deploy R12 build 명령 · `error.where`에 들어갑니다 |

## 7. 이 초안 다음
- 주인 확인 D-291이 왔고 PS-02는 D-307로 시작합니다. PS-02 slice(packrt 0.12.0 설치 — D-301 승인)에서 `contracts/v4/`를 커밋하고 `pack/schemas`를 v4와 바이트까지 같게 합니다. 이력 DB는 packrt-1이 이미 build했으므로(D-301, 가짜 `ir-history-412ea43e` · 실제 `ir-history-16d297d8`) Pack은 실행 설정으로 고르기만 합니다. 그 뒤 바뀐 기대값 목록 → 사람 승인(③) → llmops · deploy repin 뒤 격리 LLMOps · 배포 서버 확인 → 태그 `v4` 순서입니다.
- E3 L1 amendment(L1 D-307)로 승인 plan의 PS-02 '다리 칸은 계속 받음'(H-5 (다))이 H-5 (나)로 바뀌고, Change Contract에 retrieval 넷(PC-10)이 들어갑니다.

## 초안 파일 sha256
- `run_request.json` `cb47370b1b1cab592e8d6a584ee516f14be601f14668e275013d45d368b85c09`
- `run_result.json` `61781c260c66db70fbb6a17f5a1e19f314ba4a81cfc2dc760e87fc4a3f3006e1`(r2 — similar 설명 한 줄만 바뀜, 첫 초안 `d88431d5…`)

## 바로잡기 · 읽는 법(정본 뒤 D-347 — 위 본문보다 이김, schema 바이트는 그대로)
- **① 이 폴더가 약속 v4 정본입니다**(PS-02 커밋 · Pack 태그 `v4`부터). 머리의 '초안 r3' · '**초안입니다.** Pack 코드 0 · 태그 0 · 설치 0'과 '초안 파일 sha256' 절 이름은 그 시점 기록입니다. 두 schema sha는 위 값 그대로입니다.
- **② packrt는 0.12.1입니다**(D-319). 5절(deploy · llmops · packrt 줄)과 7절의 packrt **0.12.0** 글은 PS-02 단계 기록으로 읽습니다. 지금 목표 · 설치는 0.12.1입니다(배포 서버 · LLMOps · Pack 같음).
- **③ 실제 DB 설정의 start 한 번 = 질의 임베딩 2건입니다**(이력 `retrieve_history` · 매뉴얼 `retrieve_manual` node가 같은 질의 글을 따로 부름, 캐시 · 대체 0). 가짜 DB 설정(`embedding: {mode: fake}`)은 0건(망 0)입니다. 실제 DB Run은 사용자 승인 뒤입니다.
