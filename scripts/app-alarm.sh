#!/usr/bin/env bash
# 메인 시나리오 알람 한 건(ML 냉각 · 센서 값만 · 고장 라벨 0): scripts/app-alarm.sh M-01 | M-02 | M-03 | M-13
# 앱 서버(api 컨테이너) 안의 알람 등록 도구에 data/app/alarms/<설비>.jsonl을 넣음 → 정비자 앱 홈에 알람 · 담당자 알림.
# 시나리오 차례: M-01(지난 같은 조치 3일 전 → 사람 확인 · 승인) → M-03(추천) → M-02(추천 없음) — 문서 참고.
set -euo pipefail
cd "$(dirname "$0")/.."
EQ=${1:?쓰는 법: scripts/app-alarm.sh M-01 | M-02 | M-03 | M-13}
F=data/app/alarms/$EQ.jsonl
[ -f "$F" ] || { echo "알람 입력 없음: $F (M-01 · M-02 · M-03 · M-13)" >&2; exit 2; }
docker compose ${LMG_COMPOSE_ARGS:-} exec -T api python -m c1a15.alarms --stdin < "$F"
