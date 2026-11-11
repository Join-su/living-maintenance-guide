# 데이터 분할 v2(D-227) — 생성기 입력

- `ai4i-split-by-udi.csv`: AI4I 2020 행(UDI)마다 공구 교체 구간(segment)과 몫(split = train · val · test · demo). **고장 라벨 없음.**
- `ai4i-split.json`: 분할 방법 · seed · 몫별 요약(라벨 건수 합계 포함) · train 경계 · 시연 행(demo_udi).
- `split.py`: 분할을 만든 도구(원본 CSV의 고장 라벨로 몫을 맞춤 — 이 폴더 밖 제품 코드는 읽지 않음).
- 쓰는 곳: `scripts/build_data.py`(이력 = train 행 · 경계 · 평소 범위 · 정규화 = train), `scripts/build_demo_scenarios.py`(시연 = demo 행), `eval/select_card_rows.py`(카드 = val · test 행). 시간으로 행을 고르지 않는다(D-236).
- sha256: csv `1929f57e…` · json `7964c6f8…` · split.py `1acbee5b…`(EV-001 입력 잠금과 같음).
