#!/bin/bash
# [dev-2] 첫 상태 그래프 이력(KG_HISTORY) — 앱이 이번 첫 상태에 찍은 줄을 그대로 씀: 시연 줄(demo_history add → line-M-01 · 03 · 13)
# + 예시 줄(precheck-add에 넣은 파일 · 주면). 기준 시각은 앱 한 곳(앱이 줄을 찍은 순간 · --at) · 손으로 박는 날짜 0. 사전 점검 예시는 고른 패턴만(기본 A · D-650 B).
# 바탕 = $DEMO_VAR/history-demo-base.jsonl(없으면 지금 KG_HISTORY를 한 번 복사) · 옛 KG_HISTORY는 old/로 옮김(지움 0).
# 사용: bash demo/dev2/first-history.sh [앱 예시 파일] [남길 예시 패턴 = A · 예 B · F = 예시 파일 번호만]   (앱 복원 · 시연 줄 add 뒤 · set-tag 앞)
set -eo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/env.sh"; cd "$DEMO_W"; M=$DEMO_VAR
B=$M/history-demo-base.jsonl; [ -e "$B" ] || cp "$KG_HISTORY" "$B"
L=(); for e in ${DEMO_LINES:-M-01 M-03 M-13}; do L+=(--line "$DEMO_STATE/line-$e.jsonl"); done   # D-656: 앱 줄 파일이 늘면 DEMO_LINES="M-01 M-02 M-03 M-13"
X=(); [ -n "${1:-}" ] && X=(--app-examples "$1"); K=${2:-A}
N=$M/history-demo.next.jsonl
"$DEMO_PY" scripts/demo_dev2.py examples --in "$B" --out "$N" --keep "$K" "${L[@]}" "${X[@]}"
O=$M/old/$(date '+%m%d-%H%M')-history; mkdir -p "$O"; mv "$KG_HISTORY" "$O/"; mv "$N" "$KG_HISTORY"
echo "$(date '+%H:%M:%S') 그래프 이력 $(wc -l < "$KG_HISTORY" | tr -d ' ')줄 · 옛 파일 $O"
