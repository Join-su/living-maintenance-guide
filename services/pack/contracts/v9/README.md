# C1-a15 Pack 약속 v9 — v8에서 점검 바꾸기 · 예외 진행 사건 · kind `exception_similar` · `exception_split`

- 2026-11-04 · 약속 주인 c1a15-pack · 가지 dev-2 · 근거 D-556(11-05 할 일) · D-555(KG 예외 설계 정리) · 꼴 한 장 = `orchestration/agents/c1a15/handoff/d556/contract-v9.md`
- v1 ~ v8은 그대로입니다. **새 사건 · 새 kind를 안 쓰면 v8과 똑같습니다.**

## 바뀐 것
| 칸 | 어디 | 꼴 | 뜻 |
|---|---|---|---|
| 사건 `switch_check` | judge 요청 events | `{type, section = 고른 점검, from_section = 그때 추천(새 칸), choice: "candidate"}` | [점검 바꾸기]에서 후보 하나를 고름(헛걸음 기록의 from → to) |
| 사건 `exception_start` | judge 요청 events | `{type, mode: "call_first" \| "act_now"}` | [점검 바꾸기]에서 [예외] — 호출 후 조치 / 바로 조치(정비자가 고름). 뒤 사건 절 = `EX-NEW` |
| `decision.reason_facts.alternatives[].desc` | 결과(start · judge · preview) | 글(선택 · D-560) | 점검 바꾸기 후보 줄 설명 = 그래프 점검 절 설명(매뉴얼 판정 기준 첫 문장 · 적재 때 · LLM 0) — 설명 없는 절은 칸 0 · decision 꼴은 kind만 필수라 schema 그대로 |
| `exception_guide.check.how` · `check.criteria` | 결과(start) | 빈 글 허용(v8은 1자 이상 · D-556 덧 12) | 방법 · 기준이 빈 예외 절도 KG에 들어감 — 점검 what · 조치 what만 필수 · 안내 기준 줄 `exception_guide.criteria`는 기준이 있을 때만(빈 기준이면 null) · 빈 방법이면 안내 글 · 절차 0 |
| kind `exception_split` | 요청 | `{kind, session_id, group, text}`(text 1 ~ 4000자 · D-556 덧 7) | 보고서 예외 칸 자유 글(정비자가 적은 그대로) — 앱 → 배포 → Pack |
| `exception_split` | 결과 | `{items: [{topic, check{what, how, criteria, minutes}, action{what, parts, minutes}}] ≤ 10, reason: null \| "not_split" \| "invalid_output"}` | AI 나눔(모델 호출 1번 · 다시 묻기 0) — items는 그대로 `exception_similar` 요청 items(글에 방법 · 기준이 없으면 `how` · `criteria` = 빈 글 · 점검 what · 조치 what만 필수 · D-556 덧 11 · 12) · `topic` = 짝의 짧은 주제(예외 이름 '예외상황-{topic}' · Pack이 '예외상황' 머리 떼고 15자 안(마지막 띄어쓰기) · 비면 조치 what 앞 15자 · D-560) — exception_similar 요청 items에도 그대로 실어도 됨(선택 칸) · 빈 items면 까닭(짝 0 = not_split · 꼴 밖 짝뿐 = invalid_output). provider 오류 · 시간 넘김(packrt 60초)은 Run 실패 |
| kind `exception_similar` | 요청 | `{kind, session_id, group, items: [{check{what, how, criteria, minutes?}, action{what, parts?, minutes?}}]}`(1 ~ 10) | 보고서 예외 칸 AI 나눔 결과(점검 1 + 조치 1 × n) |
| `exception_similar` | 결과 | `[{item, matches: [{kind: "예외" \| "매뉴얼", no, name, score 0 ~ 1}] ≤ 3}]` | 그 증상군 등록 예외 · 매뉴얼 점검 · 조치 절 중 비슷한 것(호출 0 · 높은 점수 먼저) — 정비자 같음(놓침) / 아님(새 예외) · 최소 점수 0.15 밑은 뺌 · `matches` 빈 목록 = 비슷한 것 없음 → 앱은 새 예외로 |

## 판정(judge 결정 칸 — decision은 kind만 필수라 schema는 그대로)
1. **안전 먼저:** 잠금 · 사전 확인 · 확인 질문 · 범위 밖이 먼저 나옵니다(바꾼 점검 · 예외보다 앞 · 건너뜀 0).
2. **점검 바꾸기:** 고른 점검 S에 결과가 아직 없고 지금이 점검 차례(`instruct` · `instruct_after_checklist` · `call`)이면, 다음 단계 = S입니다.
   - 등급 그대로: A → `instruct` · B → `instruct_after_checklist` · C · D → `call`(reason `switched_check` · 승인 뒤 `instruct`)
   - 결정 칸 `switched: {from, to}`
   - S 결과가 오면 S에서 이어 갑니다(이상 → 그 원인 조치 · 정상 → 다음 점검).
   - 건너뛴 점검 = '안 함'(결과 0 · 정상으로 세지 않음 · 차례가 오면 다시 추천).
   - 그 증상군 점검 절이 아니면(조치 · 안전 · 다른 증상군 · 없는 절) Run 실패입니다.
3. **예외 진행(절 `EX-NEW` · 등급 C)** — 결정 칸 `exception_progress: {mode}` · reason `exception_progress`:
   - `call_first`: `call` → 파트장 승인(`call_answer approved` · `approval` · section `EX-NEW`) → `action`(stage `exception_progress`)
   - `act_now`: 바로 `action` + `lead_notice: "acted_without_call"`(파트장에게 '호출 없이 조치함')
   - 그 뒤 `action_done`(section `EX-NEW`) → `restart_check` → `restart_value` → `report`(outcome `unknown` · cause null) · 재가동 뒤 밖이면 `handover`
   - 모르는 mode는 Run 실패입니다.

## 기록 이름(앱 · LLMOps 공용 — Pack은 이름만 정함)
- 헛걸음 `wasted_step`(후보 안 바꿈 → 노드 순서) · 놓침 `missed`(보고서 같음 → KG 관계) · 새 예외 `new_exception`(보고서 아님 → 관문)
- 함께: `human_check: {section, from_section}`(예외 진행이면 section `EX-NEW`)

## 예시
- v8 예시 그대로(바이트 같음) + 새 셋 짝 — 요청을 Pack 함수에 넣은 실제 출력입니다.
  - `judge-switch`: 잠금 · 사전 확인 뒤 추천 4.3 → 4.5 고름 → 4.5 지시 · switched
  - `judge-exception-start`: [예외] 호출 후 조치 → EX-NEW 파트장 호출
  - `exception-similar`: 냉각수 글 → 매뉴얼 냉각수 보충 · 냉각수 수위 확인(지금 그래프엔 등록 예외 0)

## 한계
- 비슷한 것 셈은 글자 두 개 묶음 코사인입니다(로컬 · 호출 0 · 1단계). 뜻이 같고 낱말이 다르면 놓칠 수 있습니다.
