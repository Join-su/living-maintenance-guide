# 예외 나누기 평가 묶음 짝 — 채점 칸(D-647 B)

`c1a15-split-work`(12 case) · `c1a15-split-confirm`(8 case)는 같은 꼴입니다. 겹치는 이력은 없습니다.

## 입력

- 약속 v10 `kind: exception_split` 요청 그대로: `session_id` · `group` · `text`.
- `text` = 승인된 정비 이력 원문 글 그대로(알람 값 · 재가동 · 정지 분이 섞여 있음 — 칸으로 옮기지 말아야 할 것).
- 줄의 `source` 칸 = 기대를 정할 근거(이력 번호 · 기기 · 시각 · 결과 · 원문에 적힌 점검 절 `checked` · 조치 절 `did` · 같은 기기 최근 30일 정비). LLMOps는 읽지 않습니다.
- 증상군(CL · TW · PW · LD)마다 조치가 있는 글과 조치가 없는 글(나누지 못함 쪽)을 섞었습니다.

## 기대값 — c1a15-eval이 원문에서 정함

지금 모델(gpt-4.1-mini)이나 다른 모델의 출력에 맞추지 않습니다. 문장 통째 같음 비교는 쓰지 않고 칸 단위로 봅니다. 검사 꼴은 LLMOps `suites.check`(equals · contains · contains_all · in · not_equals · not_contains_any · any_of)입니다. 경로 머리는 `exception_split.`입니다.

| 칸 | 경로 | 권하는 검사 |
|---|---|---|
| 나눔 여부 | `exception_split.reason` | 조치 있는 글 `equals: null` · 조치 없는 글 `equals: "not_split"` |
| 점검 수 | `exception_split.exception.checks[N]` | 원문 점검 수가 N이면 `checks[N]` `equals: null`(더 지어내지 않음) |
| 무엇(점검) | `…checks[i].what` | 원문 부품 · 곳 말 `contains` · 알람 값 말(온도 차 · 토크 · 출력 · 회전수) `not_contains_any` |
| 어떻게 | `…checks[i].how` | 원문 방법 말 `contains` + 언제 말(어제 · N일 전 · 지난주 …) `not_contains_any` · 방법이 없으면 `equals: ""` |
| 판정 기준 | `…checks[i].criteria` | 원문 기준 말 `contains` · 없으면 `equals: ""`('기준 없음' 같은 지은 말 0) |
| 주제 | `exception_split.exception.topic` | 대상 말 `contains` · `"예외상황"` `not_contains_any` |
| 분 | `…checks[i].minutes` · `…actions[i].minutes` | 원문에 'N분'이 없으면 `equals: null`(정지 분은 점검 · 조치 분 아님) |
| 조치 | `…actions[i].what` | 원문 한 일 `contains` · `재가동` `not_contains_any` |
| 부품 | `…actions[i].parts` | 원문 부품 `contains_all` · 없으면 `equals: []` |
| 지시 문장 | `…checks[i].instruction` · `…actions[i].instruction` | 그 줄 무엇 말 `contains` · 원문에 없는 숫자와 언제 말 `not_contains_any` |

- 지시 문장 · 무엇은 말 바꿈을 허용합니다(`equals` 0 · 낱말 검사).
- 길이(주제 15자 · 지시 문장 120자)는 검사하지 않습니다. Pack이 이미 거릅니다(120자를 넘으면 칸으로 짠 문장).
- 새 검사 종류는 만들지 않습니다. 규칙 정본 = c1a15-eval handoff/d647/split-gold-rules.md.
- 조치 없는 글은 `exception`이 `null`이라 그 밖의 칸이 없습니다.
