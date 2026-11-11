# C1-a15 Pack 입출력 약속 v3 (정본 — v2 + kind `report`, D-285)

- 근거: D-285(H-1 (가) v3 = report만 · v4 = start 다리 뺌은 0.12.0 뒤) · D-275 · D-278 · 초안 `.vibe-factory/evolutions/EV-001-pack-contract-v1/drafts/contract-v3/README.md` · 보고 012
- **v2와 다른 곳은 하나 — 새 kind `report`(보고서 문장 node, NX-03)입니다.** `alarm` · `start`(P1 다리 `similar` 그대로) · `judge`의 입력 · 출력은 v2와 같습니다. v2 README의 '바로잡기 · 읽는 법' 절(D-275 · R6-1 · R6-2 · D-273 · D-278)은 v3에도 그대로 적용됩니다.
- 이 폴더의 `run_request.json` · `run_result.json`이 정본입니다. 바꿀 때는 규칙 06을 따릅니다(질문 → 오케 D-nn → 새 version 폴더). 이미 쓴 version의 schema 파일은 고치지 않습니다.
- 지금 Pack 그래프(태그 `v1` · `v2`)는 약속 v2입니다. `report`를 그래프에 넣는 일은 VFE 승인 뒤 slice(가짜 provider)에서 하고, 그때 `pack/schemas`를 이 v3와 바이트까지 같게 합니다.
- 시험: `tests/test_contract_v3.py`(v2 공통 사례 + report 사례)

## kind `report`
| 칸 | 필수 | 뜻 |
|---|---|---|
| `session_id` | 필수 | `S-A<alarm_id>`(R6-1) |
| `group` | 필수 | 증상군 |
| `similar` | 필수 | 이 세션 상위 10건(앱이 저장한 값 그대로) — 근거 기록 ID 검사 기준 |
| `report_draft` | 필수 | 앱이 코드로 채운 보고서 8항목 초안 `{equipment_alarm, sensor_evidence, check_path, cause, evidence_records, action, downtime, unresolved}`. 추가 칸은 금지합니다. `cause` · `action` 초안이 원문 대체 글입니다 |
| `allowed_sections` | 필수 | 세션에서 열린 · 기록된 절 번호 |
| 두면 안 되는 칸 | — | `event_card` · `settings` · `event_id` · `query` · `events` · `grades` · `minutes` · 사용자 ID · 표시 이름 · 메모 원문 |

- **출력 `result.report`:** `{cause, action}`, 칸마다 `{text, fallback, attempts(1 ~ 2), reason}`입니다.
  - `reason`은 `null | ungrounded_section | ungrounded_record | ungrounded_number | empty`입니다.
  - 근거 검사(앱 R8과 같은 규칙: 절 번호 · 기록 ID · 수치)에 떨어진 칸은 한 번 다시 씁니다. 그래도 떨어지면 `report_draft`의 그 칸(원문)을 쓰고 `fallback: true`가 됩니다.
  - 나머지 6칸은 돌려주지 않습니다. 앱 초안 그대로입니다.
- **실패:** LLM node 실패(provider 오류 · 시간 초과)는 Run 실패입니다(`error.where = run.write_report` 또는 `run.rewrite_report`, `on_error` 없음 — D-275). 앱이 이것을 어떻게 보여 주고 셀지는 앱 몫입니다(D-285 H-4).
- **부르는 때:** 보고서 화면을 처음 열 때 한 번 부르고, 결과를 앱이 저장합니다(D-285 H-2 (가) — c1a15-app 확인은 P1 뒤).
- 앱의 다른 LLM 문장 5곳(지시문 · 미리보기 이유 · 호출 사유 · AI 해석 · 호출 요약)은 이 약속 밖입니다(H-3 (가)).

## 파일 sha256
- `run_request.json` `e1fdbdc254fe457987a5e81d423ee82c32a3558af0e83c59cdac3bbc8f85d8af`
- `run_result.json` `2ff10582bd57dd37636a6344e6760760d80ea52ffaca4596d214c500fcd08b9b`

## 바로잡기 · 읽는 법(정본 뒤 D-nn — 위 본문보다 이김, schema 바이트는 그대로)
- **D-294(PR-16 · 보고 012-05-01 (다)):** 위 표 '두면 안 되는 칸'의 '메모 원문'은 아래로 읽습니다.
  - `report_draft`의 **정비자 점검 관찰 요약(앱이 30자로 자른 점검 경로 · 소견 줄)은 허용**합니다.
  - `unresolved`에 들어가던 **파트장 답 메모(호출 답 `note`)는 보내지 않습니다**(앱 몫, 화면 · 확정 보고서는 그대로).
  - 그 밖의 메모 원문 · 이름 · 사용자 ID는 0입니다.
