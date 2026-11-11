# C1-a15 Pack 약속 v8 — v7에서 예외 안내 칸 둘: 결과 `exception_guide` · judge 요청 `exception_relation` · report 요청 `allowed_sections`가 `EX-…`도 받음

- 2026-11-04 · 약속 주인 c1a15-pack · 가지 dev-2 · 근거 D-545(그래프 자리 · 예외 안내 · 앱 질문 001-zzz15 (가)) · 그래프 꼴 = `orchestration/agents/c1a15/handoff/d545/graph-shape.md`
- v1 ~ v7은 그대로입니다.

## 바뀐 것(선택 칸 — 안 쓰면 v7과 같음 · 넓힌 꼴 하나)
| 칸 | 어디 | 꼴 | 뜻 |
|---|---|---|---|
| `exception_guide` | start 결과 | `{section, relation_id, grade, title, text, steps[], criteria, basis[], minutes, next_section}` — section · relation_id는 `EX-…` · minutes = 예외 절 분(요청 분 표 → 없으면 그래프 줄) · next_section = 예외 절이 없었을 때의 다음 점검(앱 카드 '원래 순서 다음' · D-546 덧 6) | 그래프 예외 안내 관계(exception_relations · mode 안내)가 알람(증상군 · 센서 구간 · 같은 설비)에 맞을 때만. 지시 글 · 절차 · 판정 기준을 담습니다 |
| `exception_relation` | judge 요청 | 문자열 `EX-…` | start 결과 `exception_guide.relation_id`를 앱이 저장해 같은 세션 judge 요청마다 그대로 다시 보냄(judge 요청엔 센서가 없어 Pack이 다시 맞춰 볼 수 없음 — manual_support와 같은 길) |
| `exception_guide.check` · `.action` · `.note` | start 결과(exception_guide 안 선택 칸) | `check{what, how, criteria, minutes}` · `action{what, parts[], minutes}` · `note` 글 | D-554 예외 입력 칸 나눔 — 그래프 예외 절 줄에 칸이 있을 때만 그대로 실음(분은 적재기가 채운 값). 옛 칸(text · steps · criteria · minutes)은 그대로 · 칸 꼴 = `orchestration/agents/c1a15/handoff/d554/fields-gate.md` |
| `allowed_sections` 꼴 | report 요청 | 항목 = 매뉴얼 절 번호(`4.3` · `4.3.2`) **또는 `EX-…`** | 예외 절을 거친 세션의 보고서 요청(D-546 덧 36 · F1 걷기에서 v7 꼴이 `EX-R-0001`을 거절해 422). Pack 근거 검사는 글의 `EX-…`를 절 번호로 봅니다 — 요청 allowed_sections에 있어야 통과하고, 안의 `R-0001`은 기록 ID로 세지 않습니다 |

## 판정 흐름(다음 걸음은 Pack이 정함 · 001-zzz15 (가))
1. 잠금 → 사전 확인은 v7과 같습니다(안전 규칙 그대로).
2. 그 뒤 첫 점검 차례에 예외 절을 먼저 냅니다.
   - 등급(요청 등급표 · 없으면 그래프 C)이 C · D이면 `call`(reason `exception_guide`), 파트장 승인(`call_answer approved` · section = 예외 절) 뒤에는 `instruct`
   - A · B이면 바로 `instruct` / `instruct_after_checklist`
3. 예외 절 `result`:
   - **정상**(또는 확인 불가) → 원래 순서 다음 점검(예외가 없던 것과 같은 판정)
   - **이상** → `action`(같은 예외 절) → `action_done` → `restart_check`(6.1) → `restart_value` → `report`(cause = 관계 원인 · 없으면 outcome `unknown`) · 재가동 뒤 경계 밖이면 `handover`
- 예외 갈래 결정에는 `relation`(관계 id)과 `graph{used = 관계 id, layer = 그때 그래프 layer}` 칸이 붙습니다. LLMOps ① · deploy가 '어느 관계 · 어느 그래프 버전으로 추천했나'를 읽는 자리입니다(D-546 덧 · 173-ok). 결과 정상으로 원래 순서에 돌아간 결정에는 붙지 않습니다.
- 예외 절 사건(section = `EX-…`)은 앱이 보내는 사건 꼴 그대로입니다(result · call_sent · call_answer · approval · action_done).
- 그래프 버전이 바뀌어 관계가 없어지면, judge는 예외 안내 없이 원래 판정을 냅니다.

## 예시
- v7 예시 그대로(바이트 같음)
- 새로 셋 — 시험용 예외 관계 하나(EX-R-0001 · 온도 차 ≤ 8.2 K)를 넣은 그래프로 Pack 함수를 실제로 돌린 출력입니다.
  - `start-exception.result.json`: exception_guide · 미리보기 = 예외 절 파트장 호출
  - `judge-exception.request.json` · `judge-exception.result.json`: 잠금 · 사전 확인 · 승인 뒤 → 예외 절 지시
- `report-exception.request.json`(D-546 덧 36): 예외 절 EX-R-0001을 거친 세션의 보고서 요청 — allowed_sections · 점검 경로 · 조치 칸에 EX 절(결과는 v7 report 꼴 그대로)

## 바뀌지 않는 것
- 결과 · 요청의 그 밖 칸, 모르는 칸 거절, 사용자 ID · 메모 원문 0은 v7과 같습니다.
