#!/bin/bash
# [dev-2] 새 Pack 커밋을 시연 복제에 들임(D-580): fetch → <커밋> = 태그 <이름>-g0 · 가짜 AI 나눔 가이드 문장 한 줄(시연 복제 d570-g1 커밋)을 다시 얹어 <이름>-g1
# 공용 c1a15-pack 쓰기 0(fetch만). 뒤에 set-tag.sh <이름>-g1. 사용: bash demo/dev2/import-pack.sh ef9ec74 d580
set -eo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/common.sh"; cd "$DEMO_W"
C="${1:?Pack 커밋}"; N="${2:?태그 머리(예 d580)}"; P=$DEMO_VAR/c1a15-pack
DEMO_PACK_SRC="$DEMO_REPO/c1a15-pack" "$DEMO_PY" scripts/demo_dev2.py clone --dest "$P" --ref "$C" --tag "$N-g0"
FAKE=$(git -C "$P" rev-list -n1 d570-g1)
git -C "$P" -c user.name=deploy -c user.email=deploy@local cherry-pick "$FAKE" >/dev/null && git -C "$P" tag "$N-g1"
git -C "$P" diff --stat "$N-g0" "$N-g1" | tail -1
