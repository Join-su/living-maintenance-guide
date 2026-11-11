# C1-a15 Pack 약속 v6 — v5에서 알람 칸 하나: 후보(candidate) 지움 → 알람 없음 사유(no_alarm_reason)

- 2026-11-03 · 약속 주인 c1a15-pack · 가지 dev-2(지금 시연 길 v5 · v22와 따로) · 근거 D-529(사람 판단 후보 지움) · D-530 덧 2(약속 v6) · 앱 dev-2 f33e12b `alarms/ml.py`
- v1 ~ v5는 그대로입니다. 지금 떠 있는 Pack(v22)은 v5를 씁니다.

## 바뀐 것
| 칸 | v5 | v6 |
|---|---|---|
| `alarm.candidate` | null 또는 {reason · risk · top_k · boundary_crossed · model_release} | **없음** |
| `alarm.no_alarm_reason` | 없음 | `low_confidence` · `rnf` · `model_failure` · null |

- 사유는 ML 길에서 위험도 ≥ 문턱인데 알람이 없을 때만 씁니다.
  - `low_confidence`: 1등 유형 확률이 문턱 아래입니다.
  - `rnf`: 1등 유형이 RNF입니다.
  - 그 밖(알람 남 · 위험도 < 문턱 · 경계 길)은 null입니다.
- 위험도 · 유형 후보는 `alarm.ml`에 그대로 있습니다. 후보 칸이 따로 들고 있던 위험도 · top_k · model_release는 ml 칸과 같은 값이었습니다.
- `boundary_crossed`는 없어졌습니다. 앱 · Pack 모두 읽는 곳이 0입니다.
- `model_failure`: Pack은 예측 node가 실패하면 Run 실패(`run.predict_failure`)로 끝납니다. 앱이 그것을 이 사유로 기록합니다. 예시 `alarm-ml-model-failure.result.json`은 앱 route()가 내는 꼴입니다.

## 예시
- v5 예시 그대로(바이트 같음): 요청 넷 · judge · report · start 셋
- `alarm-boundary.result.json`: `candidate: null` → `no_alarm_reason: null`(칸 자리 같음)
- `alarm-ml-candidate.result.json`: 없앰
- 새로 둘 — 앱 f33e12b route() 실제 실행 두 줄을 그대로 쓰고, Pack 알람 칸(event_id · alarm_block · sensor_derived)만 더했습니다.
  - `alarm-ml-low-confidence.result.json`
  - `alarm-ml-model-failure.result.json`

## 바뀌지 않는 것
- 요청 schema(run_request.json)는 v5와 바이트가 같습니다.
- 결과 schema의 그 밖 칸(decision · similar · retrieval · preview · manual_support · report)도 같습니다.
- 경계 길 알람 · 판정 · 보고서 출력 값은 v5와 같습니다. alarm 칸 이름 하나만 다릅니다.
