#!/bin/bash
# [dev-2] 앱 시연 이력 줄 둘(python -m c1a15.demo_history add가 찍은 M-01 · M-03 줄)로 그래프 이력(KG_HISTORY)을 처음 이력 + 두 줄로 다시 만듦 — 줄마다 approval 칸 확인
# (D-575 리허설: approval 없는 줄은 적재기 KeyError). 사용: bash demo/dev2/history.sh   (줄 파일 = $DEMO_STATE/line-M-01.jsonl · line-M-03.jsonl)
set -eo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/common.sh"; cd "$DEMO_W"; M=$DEMO_VAR
for f in "$DEMO_STATE/line-M-01.jsonl" "$DEMO_STATE/line-M-03.jsonl"; do tail -1 "$f" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert (d.get('approval') or {}).get('status')=='승인', 'approval 칸 없음'; print(d['record_id'], d['equipment_id'], d['occurred_at'])"; done
"$DEMO_PY" scripts/demo_dev2.py history --base "$M/history-base.jsonl" --out "$M/history-1.jsonl" --at now --line "$DEMO_STATE/line-M-01.jsonl" --days 7 > /dev/null
cp "$M/history-1.jsonl" "$M/history-2.jsonl"; tail -1 "$DEMO_STATE/line-M-03.jsonl" >> "$M/history-2.jsonl"; cp "$M/history-2.jsonl" "$M/history-demo.jsonl"
echo "$(date '+%H:%M:%S') 그래프 이력 $(wc -l < "$M/history-demo.jsonl" | tr -d ' ')줄"
