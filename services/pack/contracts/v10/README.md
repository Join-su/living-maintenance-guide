# C1-a15 Pack 약속 v10 — v9에서 예외 하나 = 주제 하나 + 점검 목록 + 조치 목록(D-566)

- 2026-11-05 · 약속 주인 c1a15-pack · 가지 dev-2 · 근거 D-566 · D-566 덧 1(걸음마다 · 승인 한 번 · 한쪽 0 = 나누지 못함) · 꼴 한 장 = `orchestration/agents/c1a15/handoff/d566/contract-v10.md`
- v1 ~ v9는 그대로입니다(v9 = 짝 꼴). **Pack 출력은 v10 꼴 하나**입니다(pack/schemas = v10 바이트).

## v10에서 바뀐 것(v9 대비)
| 칸 | 어디 | v10 꼴 | 뜻 |
|---|---|---|---|
| `exception_split` | 결과 | `{exception: {topic, checks[1 ~ 10], actions[1 ~ 10]} \| null, reason: null \| "not_split" \| "invalid_output"}` | 글 하나 = 예외 하나(짝 꼴 없음 · 적은 순서) · **점검 0 또는 조치 0 = null + not_split** · 거르기 · topic 다듬기는 v9와 같음(칸마다) |
| kind `exception_similar` | 요청 | `{kind, session_id, group, exception: <위 꼴>}`(v9 `items` 대신) | 보고서 예외 칸 나눔 결과 예외 하나 |
| `exception_similar` | 결과 | `{matches: [{kind, no, name, score}] ≤ 3}` | 예외 통째(주제 · 점검 · 조치 글)로 한 번 · 최소 점수 0.15 · 같음 / 아님 한 번 · **kind 예외 줄엔 그 예외의 지금 `checks` · `actions`(선택 · D-560 덧 10 · '같음 + 고침' 바탕)** · 매뉴얼 줄은 그대로 |
| `checks[] · actions[]`의 `instruction` | 요청(exception_similar) · 결과(exception_split · exception_similar · exception_guide) | 글(선택 · D-642 C) | 그 줄을 시키는 한 문장(무엇을 · 어떻게 · 판정 기준). AI 나눔이 만들고, 원문에 없는 수 · 언제 말 · '기준 없음'이 있거나 120자를 넘으면 칸 값으로 짠 문장. 승인되면 그래프 예외 절에 실려 다른 기기 안내까지 그대로 |
| `reason_facts.picked_by` | 결과(start · judge · preview) | `{option, section, title, instead_of, instead_title}`(선택 · D-652 12-f) | 옵션 `check_first_<증상군>`이 첫 점검을 바꿨을 때만. 이유 글은 '확률 ÷ 분' 대신 '먼저 볼 점검으로 정해 둔 {section}절'로 써야 셈과 맞음. 값 = 옵션 · 매뉴얼 절 이름 |
| `exception_guide` | 결과(start) | v9 `check` · `action` 대신 `checks` · `actions`(목록) · `topic` | 그래프 예외 절 목록 그대로 · 옛 하나 꼴 절은 목록 하나로 |
| 사건 `step` | judge 요청 events | `^[ca][0-9]{1,2}$` — 점검 `c1` · `c2` … · 조치 `a1` · `a2` … | 다음 알람 예외 안내 걸음(결과 `result` · 조치 끝 `action_done`에 붙임) · 승인 사건엔 없음(예외 전체에 한 번) |
| 결정(걸음) | judge 결과 decision | `{kind: instruct \| action \| call, section: "EX-…", step, step_index, steps_total, check \| action, grade, cause, reason: "exception_guide", relation, graph}` | 승인 한 번 → c1 … → a1 …(결과로 건너뛰지 않음) → 재가동 → 재가동 값 → 안이면 보고서 · **밖이면 원래 점검 순서**(D-566 덧 2) · **반려 뒤 다시 보고서** · 목록 없는 옛 예외 절은 옛 길(정상이면 원래 순서) |

## v9 그대로 이어지는 것(아래 v9 README)

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

## D-570 덧 — 최근 정비 원인 예외(start 선택 칸 하나 · 그래프 줄)
- 요청 `recent_work`(start만 · 선택): 알람 설비의 알람 시각 앞 30일 안 정비 이력 `[{record_id, at, sections[앱 번호]}]` ≤ 60. 다른 종류에 넣으면 거절.
- 그래프 예외 관계 `when.recent = {section, days 1 ~ 30}`: 알람 설비 이력(그래프 records did · checked + 요청 recent_work) 중 [알람 시각 − days, 알람 시각) 안에 그 절이 있으면 맞음(설비 무관). equipment_id · occurred_at이 없으면 안 맞음.
- 그래프 release records에 `did` · `checked`(그 이력이 한 조치 · 점검 절 · 앱 번호) 칸이 생김 → 예시 start-exception-lists의 vector_db_version(그래프 release_id)이 바뀜.
- 꼭 맞는 정본: orchestration agents/c1a15 handoff/d570/recent-condition.md.

## D-580 덧 — 적재된 예외 = 매뉴얼과 같은 점검 후보
- 조건 맞은 예외는 같은 순위 셈(확률 ÷ 분 · 원인이 비면 1위 원인 확률)의 후보입니다. 등급 A · B는 첫 추천이 될 수 있고, C · D는 후보 목록(`reason_facts.alternatives` · 줄에 `relation` · `title`)에만 뜹니다. `title`은 예외 첫 점검 이름이고, 점검 칸이 없는 옛 줄은 절 이름입니다(D-585). 결정 · 미리보기 `record_counts`(D-642)는 확률 셈에 쓴 기록입니다: `{scope: "equipment" | "group", total, by_cause: {원인: 건수}, prior}`. 확률 = (건수 + prior) ÷ (total + prior × 원인 수)이고, count_equipment는 prior 1입니다. similar · evidence 셈이면 칸이 없습니다. `exception_guide.matched.source_equipment`는 이 예외를 배운 등록 기기입니다. 관계에 값이 없으면 칸이 없고, `equipment_id`는 그대로 알람 기기입니다(D-593).
- 사건 `switch_check.section`에 그 세션 예외 절 EX-…를 받습니다(요청에 `exception_relation` 필요). 예외에서 다른 후보로도 바꿉니다.
- 예외 점검을 모두 본 뒤 '이상'이 없으면 조치로 안 가고 다음 후보로 갑니다. 그 예외는 후보에서 빠집니다.
- start 결과 `exception_guide.matched` = 맞은 조건 근거(증상군 · 설비 · 센서 조건 · 근거 예외 · 기록 · 최근 정비 이력).
- 위 5절 '결과로 건너뛰지 않음' · '첫 점검 차례에 예외 절 먼저'는 이 덧으로 바뀝니다.

## 한계
- 비슷한 것 셈은 글자 두 개 묶음 코사인입니다(로컬 · 호출 0 · 1단계). 뜻이 같고 낱말이 다르면 놓칠 수 있습니다.
