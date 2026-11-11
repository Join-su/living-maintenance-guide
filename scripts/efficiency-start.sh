#!/usr/bin/env bash
# Efficiency scenario start (guide 08): the efficiency starting Pack becomes the one the deployment server runs and the operational
# version in AgentOps — the same steps the demo took. Use it after registering the starting version on the AgentOps screen
# (＋ release · name V<n> · /packs/eff-model or /packs/eff-structure · execution/container.yaml) and evaluating it.
#   scripts/efficiency-start.sh eff-model | eff-structure
# ① the deployment server switches to the starting Pack's tag (server only restarts) and its /info revision is checked
# ② AgentOps records that deployment against the registered version (it shows '운영')
# ③ one ordinary alarm (M-01) so the deployment has run on the new line before the next approval
# ⓪ stops before ① — exit 5 when this stack already has a deploy approval (guide 07) · exit 4 when the folder is not registered.
# Back to the first state: guide 09.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${1:?쓰는 법: scripts/efficiency-start.sh eff-model | eff-structure}
case "$TAG" in eff-model|eff-structure) ;; *) echo "eff-model 또는 eff-structure만" >&2; exit 2 ;; esac
set -a; [ -f .env ] && . ./.env; set +a
dc() { docker compose ${LMG_COMPOSE_ARGS:-} "$@"; }   # LMG_COMPOSE_ARGS: e.g. "-f docker-compose.yml -f compose.key-file.yml"
SWITCH=${PACK_SWITCH_CMD:-/app/docker/pack-switch.sh}   # in the pack-server container (deploy side)

# ⓪ before anything changes: AgentOps must be able to take the operational line ② (agentops-operational.sh --check, writes nothing).
#   After a deploy approval of this Pack (guide 07) the store closes that path, so the script stops here and the server stays as it is.
echo "⓪ 확인: AgentOps가 운영 줄을 받을 수 있는지(배포 서버는 아직 그대로)"
set +e; LMG_COMPOSE_ARGS=${LMG_COMPOSE_ARGS:-} scripts/agentops-operational.sh --check "/packs/${TAG}" "$TAG"; rc=$?; set -e
case $rc in
  0) ;;
  5) echo "멈춤 — 이 스택에는 이미 배포 승인(설치 안내 07)이 있습니다 · 배포 서버는 바꾸지 않았습니다 · 효율은 첫 상태에서 하세요(설치 안내 09로 되돌린 뒤 08)"; exit 5 ;;
  4) echo "멈춤 — /packs/${TAG}를 화면에서 ＋ release로 먼저 등록하세요(설치 안내 08-1) · 배포 서버는 바꾸지 않았습니다"; exit 4 ;;
  *) echo "멈춤 — 위 줄을 보세요 · 배포 서버는 바꾸지 않았습니다"; exit "$rc" ;;
esac

echo "① 배포 서버 → ${TAG}"
dc exec -T pack-server "$SWITCH" "$TAG"

echo "② AgentOps 운영 줄(배포 서버 /info와 같을 때만)"
LMG_COMPOSE_ARGS=${LMG_COMPOSE_ARGS:-} scripts/agentops-operational.sh "/packs/${TAG}" "$TAG"

echo "③ 보통 알람 한 건(M-01)"
LMG_COMPOSE_ARGS=${LMG_COMPOSE_ARGS:-} scripts/app-alarm.sh M-01
echo "끝 — AgentOps 화면을 새로 고치면 출발 버전이 '운영'입니다(설치 안내 08-2로)"
