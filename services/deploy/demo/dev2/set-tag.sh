#!/bin/bash
# [dev-2] 시연 그래프를 태그 하나로(첫 상태 · 되돌림): 감시 끔 → 처리 표시 · 작업 폴더 · 신호 옆으로(지우기 0 · 대기 폴더에 남은 파일은 처리 표시만 되살림 = 다시 처리 0)
# → 설정 ref · expect → 8796 다시 띄움(restart.sh) → 감시 다시 켬. 태그는 지우지 않음. 사용: bash demo/dev2/set-tag.sh d580-g1
set -eo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/env.sh"; cd "$DEMO_W"
TAG="${1:?태그(예 d580-g1)}"; M=$DEMO_VAR
R=$("$DEMO_PY" -c "import sys; sys.path.insert(0,'scripts'); import graph_swap as S; print(S.rev('$TAG', repo='$M/c1a15-pack')[1])"); echo "$TAG ${R:7:8}"
scripts/py scripts/autodeploy.py stop | tail -1 | cut -c1-40
O=$M/old/$(date '+%m%d-%H%M')-$TAG; mkdir -p "$O"; mv "$M/kg-state" "$M/signal" "$O/"; mkdir -p "$M/kg-state/done" "$M/signal"
for f in "$KG_INBOX"/*.json; do [ -e "$f" ] || continue; n=$(basename "$f" .json); [ -e "$O/kg-state/done/$n" ] && cp "$O/kg-state/done/$n" "$M/kg-state/done/$n" && echo "처리 표시 되살림 $n"; done
# kg_apply 적재기 · 관문 graph_gate는 Pack 저장소 작업 폴더 코드를 씀 → 첫 상태 태그로 맞춤(kg_apply new_revision과 같은 checkout · D-642 옛 46aabcd 적재기가 instruction 줄을 뺌)
[ -z "$(git -C "$M/c1a15-pack" status --porcelain --untracked-files=no)" ] || { echo "멈춤: Pack 작업 폴더에 바뀜 있음"; exit 2; }
git -C "$M/c1a15-pack" checkout -q -B kg-apply "$TAG"; echo "Pack 작업 폴더 kg-apply = $TAG $(git -C "$M/c1a15-pack" rev-parse --short HEAD)"
cp config/c1a15-dev2-demo.yaml "$O/config-before.yaml"
python3 - "$TAG" "$R" <<'PY'
import re,sys
p='config/c1a15-dev2-demo.yaml'; s=open(p).read()
s=re.sub(r'^  ref: \S+ ', f'  ref: {sys.argv[1]} ', s, count=1, flags=re.M)
s=re.sub(r'^  expect_revision: "[^"]*"', f'  expect_revision: "{sys.argv[2]}"', s, count=1, flags=re.M)
open(p,'w').write(s)
PY
bash "$D/restart.sh"
scripts/py scripts/autodeploy.py start | tail -1 | cut -c1-40; sleep 2; scripts/py scripts/autodeploy.py status | tail -1 | cut -c1-60
echo "옮긴 자리 $O"
