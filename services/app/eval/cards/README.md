# 평가 카드 (정답 — Agent 코드·검색 색인 접근 금지)

분할 v2(D-227 · D-236 (3) · D-246)로 다시 만든 두 묶음. 옛 카드 30건(2026-10-11 작성)은 git 이력에만 남긴다.

| 묶음 | 쓰임 | 몫(증상군) | 원본 행 |
|---|---|---|---|
| `val/` | 작업용(조율 · 바퀴) — `scripts/eval-run`이 기본으로 씀 | CL 7 · PW 11 · TW 7 · LD 5 | val 몫 + test 구간 56 CL 알람 7행(예외) |
| `test/` | 숨김 — 숨김 모드에서만(ES-05) | CL 8 · PW 10 · TW 6 · LD 6 | test 몫 |

묶음마다 파일
- `cards.jsonl`: 평가 카드 30건. 숨은 정답 원인, 점검별 결과·메모, 재가동 결과, 기대 결과.
- `card_inputs.json`: 작성자에게 준 입력(알람 30건의 센서 값·직전 12회 측정). 고장 라벨 · 원본 행 번호 없음.
- `card_udi_map.json`: 카드 ↔ 원본 CSV 행(UDI). 작성자에게 주지 않음. 이력 · 시연과 겹치지 않음을 검사하는 데 씀.
- `selection.json`: 고른 규칙(`eval/select_card_rows.py --plan 나`) · 후보 수 · seed · 구간 56 예외 기록.

작성 방식: 이 저장소의 원인 비율표·정비 이력·생성 코드·원본 CSV를 보지 않은 **격리된 AI agent**가 `작성-지시.md`, `card_inputs.json`(작성자 폴더 이름 `alarms.json`), 매뉴얼 두 파일만 읽고 작성했다(agent 자기 보고 기준, 준 파일 sha256은 EV-001 ES-02 증거). 사람 작성자가 아니므로 평가 결과 발표 때 이 한계를 밝힌다.

선정: train 경계로 알람인 행에서 증상군 층화(LD 몫을 키움), seed `2026-val` · `2026-test`. 정답 유형은 묶음마다 manual_cause 22 · none 5 · out_of_graph 3.

**한계(D-246):** val에는 원래 온도 차 저하(CL) 알람이 없어(더운 구간이 train · 시연 · test 구간 56에만 있음), test 구간 56의 CL 알람 15행을 배치 순서로 번갈아 val 7 · test 8로 나눴다. 같은 공구 교체 구간의 이웃 행이 두 묶음에 갈라져 **test의 CL 점수가 조금 낙관적일 수 있다.** 분할 파일은 바꾸지 않았다.

검사: `python3 eval/validate_cards.py` (묶음마다 형식·건수·정답과 점검 결과 일관성·메모의 원인 이름 노출·메모 중복·이력 분리·분할 몫) · `python3 scripts/check_demo_separation.py`(시연 · 이력 · 카드 분리와 분할 몫).
