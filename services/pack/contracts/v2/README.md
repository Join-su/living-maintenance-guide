# C1-a15 Pack 입출력 약속 v2 (정본 — v1 + D-264)

- 근거: 약속 확정본 `orchestration/records/contract-locked-2026-10-24.md`(A · B) · D-259 · D-260 · D-263 · **D-264(product_type)** · c1a15 보고 009 · 답장 011-01
- **v1과 다른 곳은 하나:** `event_card.product_type`(`L` · `M` · `H`) — `source_mode = ml_predicted`이면 필수, `sensor_boundary`이면 넣어도 되고 빼도 됨. 출력 모양은 v1과 같음. c1a15-app · deploy는 **v2 기준**으로 만듭니다(v1으로 만들지 않음).
- **이 폴더(`contracts/v2`)의 `run_request.json` · `run_result.json`이 정본**입니다. C1-a15 앱(c1a15-app)과 배포 서버(deploy)는 이 schema를 따릅니다. 배포 서버는 이 schema로 받기만 합니다(일반 방식). 이 schema에서 거절되는 본문은 배포 서버의 `422 invalid_input`입니다.
- 바꿀 때는 규칙 06(질문 → 오케 D-nn → c1a15-pack이 새 version 폴더 → 같은 알림)으로 합니다. v1 · v2는 고치지 않습니다.
- 지금 Pack 그래프(`pack/schemas`, kind `judge` · `predict`)는 아직 v0입니다. VFE 승인 뒤 첫 slice(PS-01)에서 `pack/schemas`를 **이 v2**와 바이트까지 같게 바꾸고 그래프를 맞춥니다.
- 시험: `tests/test_contract_v2.py`(공통 사례 `tests/contract_cases.py` + product_type 사례)

## 요청 `kind`
| kind | 언제 | 필수 칸 | 두면 안 되는 칸 |
|---|---|---|---|
| `alarm` | 센서 행 하나 → 알람을 켤지 · 증상군(Pack이 정함, D-259 (2)) | `event_card`. `source_mode = ml_predicted`이면 `settings.ml_alarm_threshold`도 필수 | 세션 · 판정 칸 전부. 경계 길이면 `settings`도 |
| `start` | 세션 시작 한 번 — 리트리버 + 첫 판정 | `session_id` · `event_id`(세션을 연 알람) · `group` · `query{direction, sensor_snapshot 5칸}` · `events: []` · `grades` · `minutes`. 선택 `similar` = **P1 다리**(앱 임시 리트리버 결과, 있으면 Pack 리트리버를 건너뜀 · 0.12.0 뒤 빠짐) | `event_card` · `settings` |
| `judge` | 정비자 입력마다 | `session_id` · `group` · `similar`(첫 Run 결과 그대로) · `events` · `grades` · `minutes` | `event_card` · `settings` · `event_id` · `query` |

- `event_card`: 센서 5칸(AI4I 원값, 추가 칸 금지) · `source_mode: sensor_boundary | ml_predicted` · `product_type: L | M | H`(ML 길 필수, 시연 · 평가 행은 UDI로 원본 Type) · `equipment_id ^M-[0-9]{2}$` · `alarm: {source: none}` · `data_lineage{source_release ^ai4i-2020, row_id, split?}`. 출력 `power_w` · 온도 차 `temp_diff_k`는 Pack이 계산합니다.
- `session_id` · `event_id`: 문자열 `^[A-Za-z0-9-]{1,40}$`. 앱 세션 번호는 문자열로 보냅니다(예 `S-12`).
- 모델 입력(task10-pack model release 몫, D-264): ONNX 입력 6칸 `[type_code, air, proc, rpm, torque, wear]` · 계산 칸 4개는 ONNX 그래프 안. Pack 특징 node는 `product_type` → `type_code`를 model release가 정한 대응으로 바꿉니다.
- 넣지 않는 것: 사용자 ID · 표시 이름 · 메모 원문 · 정정 사유 · 고장 라벨 · 추이 · 각본 번호 · 평가 모드 · 시각 · 배정(앱 몫).

## 출력(`result.output` = 배포 서버 201 응답에 그대로)
| kind | 칸 |
|---|---|
| `alarm` | `alarm{event_id, alarm, group, group_source, alarm_block, sensor_derived, ml, candidate}` |
| `start` | `decision` · `similar`(상위 10건 `{record_id, cause, score}`, 점수 내림차순 · 같으면 `record_id` 순) · `retrieval{source: packrt\|request, vector_db_version, query_from, top_k}` |
| `judge` | `decision` |

- 칸 이름이 정본입니다. 배포 서버 `grounding_keys`도 이 이름(`alarm` · `decision` · `similar` · `retrieval`)을 씁니다.
- `alarm_block`은 지금 앱 알람 행 `alarm` 칸과 같은 모양입니다. `sensor_derived`를 받아 앱이 7칸으로 저장합니다.
- `candidate.reason`: `low_confidence` · `rnf` · `model_failure`. 예측 node가 실패해도 알람 Run은 `model_failure` 후보로 끝냅니다. 그래프에서 되는지는 다음 slice에서 확인합니다. 안 되면 v2로 고치고 알립니다.
- P1 다리(`retrieval.source = request`)에서는 `vector_db_version = null`입니다. 앱은 자기 임시 release id를 저장합니다.

## 보고 009와 다른 곳(정본이 이김)
- `start.query.sensor_snapshot`: 009 B-1의 7칸 → **5칸**입니다. 계산 칸은 Pack 한 곳에서만 셉니다.
- `start`에 `event_id`(세션을 연 알람)가 필수로 들어갑니다. 알람 Run과 세션 Run을 trace에서 잇습니다.
- `session_id`: 009에 없던 칸입니다(확정본 B · deploy Q-4).
- 옛 kind `predict`(ES-04)는 v1에 없습니다. `alarm`으로 바뀝니다.

## 예시
- `examples/alarm-ml-*`의 ML 값(위험도 0.62 · 유형 확률)은 모양을 보이는 예시 값이고, 자리 모델 · 사용자 모델 출력이 아닙니다.

## 파일 sha256
- `run_request.json` `60af45aec149b02c4faa5afff1e76ae791700576c3a18f5e815827df1be7d1dc`
- `run_result.json` `a18cae9472b0c7ddb8f6c68f377cfa7d843320b7745e5e0aac950275c99fbf0d`

## 바로잡기 · 읽는 법(정본 뒤 D-nn — 위 본문보다 이김, schema 바이트는 그대로)
- **D-275 예측 실패:** 위 '출력' 절의 '예측 node가 실패해도 알람 Run은 `model_failure` 후보로 끝냅니다 … 안 되면 v2로 고치고 알립니다'는 아래로 읽습니다.
  - ML 길(`ml_predicted`)에서 예측 node가 실패하면 **Run 실패**입니다(배포 서버 `500 run_failed`, `error.where = run.predict_failure`).
  - **앱이 이 실패 하나를 후보 `model_failure`로 읽습니다**(`ops.alarm_candidates`, 알람 없음).
  - 그 밖의 Run 실패는 `503 pack_unavailable`(약속 B)입니다. Pack이 `result.alarm.candidate.reason = model_failure`를 내는 경우는 없습니다(schema에는 남아 있음).
  - 까닭: LLMOps가 packrt `on_error` 길을 등록 대조에서 세지 않습니다(llmops EV-045에서 고침). 그 뒤에 Pack 안에서 내는 길로 되돌릴지는 새 약속 version으로 정합니다.
- **D-275 · 31행 '안 되면 v2로 고침':** 이미 쓴 version은 고치지 않으므로 '새 약속 version'으로 읽습니다.
- **c1a15-app 001-03 R6-1(보고 011-07):** `session_id` = **`S-A<alarm_id>`**(세션을 연 알람 id, 위 예시 `S-12`는 모양만). Pack은 판정에 쓰지 않고 trace 입력으로만 남깁니다.
- **R6-2:** `start.event_id` = 세션을 연 알람의 `event_id`입니다. 기본 길(inproc)로 등록돼 없으면 **`C1A-A<alarm_id>`**를 씁니다.
- **D-273(약속 C, Pack 리트리버 version부터):** retrieve@2 오류 code `rerank_invalid` · rerank를 쓰면 store `index.rerank_norm` 필수 · `rerank.fields ⊆` 그 fields.
- **D-278(배포 서버 grounding):** 위 '출력' 절의 '배포 서버 `grounding_keys`도 이 이름(`alarm` · `decision` · `similar` · `retrieval`)을 씁니다'는 아래로 읽습니다. **앱은 배포 서버 201 `result.output`에서 이 칸들을 읽습니다. 배포 서버 `grounding_keys = []`입니다.** 이 넷은 Run 출력 칸이고, 그래프 state 키가 아니기 때문입니다(deploy 보고 098). 칸 이름 자체는 그대로 정본입니다.
