# C1-a15 Pack 입출력 약속 v1 (정본)

- 근거: 약속 확정본 `orchestration/records/contract-locked-2026-10-24.md`(A · B) · D-259 · D-260 · D-263 · c1a15 보고 009
- **이 폴더의 `run_request.json` · `run_result.json`이 정본**입니다. C1-a15 앱(c1a15-app)과 배포 서버(deploy)는 이 schema를 따릅니다. 배포 서버는 이 schema로 받기만 합니다(일반 방식). 이 schema에서 거절되는 본문은 배포 서버의 `422 invalid_input`입니다.
- 바꿀 때는 c1a15-pack이 먼저 v2 폴더를 만들고 알립니다. v1은 고치지 않습니다.
- 지금 Pack 그래프(`pack/schemas`, kind `judge` · `predict`)는 아직 v0입니다. 다음 slice에서 `pack/schemas`를 이 v1과 바이트까지 같게 바꾸고 그래프를 맞춥니다(VFE 승인 뒤).
- 시험: `tests/test_contract_v1.py` — 예시 10개가 맞는지, 약속을 어긴 본문이 거절되는지 봅니다.

## 요청 `kind`
| kind | 언제 | 필수 칸 | 두면 안 되는 칸 |
|---|---|---|---|
| `alarm` | 센서 행 하나 → 알람을 켤지 · 증상군(Pack이 정함, D-259 (2)) | `event_card`. `source_mode = ml_predicted`이면 `settings.ml_alarm_threshold`도 필수 | 세션 · 판정 칸 전부. 경계 길이면 `settings`도 |
| `start` | 세션 시작 한 번 — 리트리버 + 첫 판정 | `session_id` · `event_id`(세션을 연 알람) · `group` · `query{direction, sensor_snapshot 5칸}` · `events: []` · `grades` · `minutes`. 선택 `similar` = **P1 다리**(앱 임시 리트리버 결과, 있으면 Pack 리트리버를 건너뜀 · 0.12.0 뒤 빠짐) | `event_card` · `settings` |
| `judge` | 정비자 입력마다 | `session_id` · `group` · `similar`(첫 Run 결과 그대로) · `events` · `grades` · `minutes` | `event_card` · `settings` · `event_id` · `query` |

- `event_card`: 센서 5칸(AI4I 원값, 추가 칸 금지) · `source_mode: sensor_boundary | ml_predicted` · `equipment_id ^M-[0-9]{2}$` · `alarm: {source: none}` · `data_lineage{source_release ^ai4i-2020, row_id, split?}`. 출력 `power_w` · 온도 차 `temp_diff_k`는 Pack이 계산합니다.
- `session_id` · `event_id`: 문자열 `^[A-Za-z0-9-]{1,40}$`. 앱 세션 번호는 문자열로 보냅니다(예 `S-12`).
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

## 파일 sha256
- `run_request.json` `c37e1f6409dd548937ddbee348db0301a1652e7af163110068722cb281019f0c`
- `run_result.json` `ae3f8a0bd6df1f41b71c52a34d025155d9635a5937f9bf8013bb6c3587e72515`
