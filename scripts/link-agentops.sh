#!/usr/bin/env bash
# The two steps AgentOps has no screen for (guide 3, after the project, the first version and its evaluation are made on the screen):
#   ① first operational version: the deployment the Pack server runs now is recorded against the version you registered
#   ② link: the project's operational Runs get the judge, and each graph the deploy side applies becomes the project's next version
# Then only agentops restarts (about 10 s). Run it once; running it again changes nothing that is already set.
#   scripts/link-agentops.sh "<project name>" [<Pack folder registered on the screen, default /packs/first>]
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=${1:?프로젝트 이름을 주세요 — 예: scripts/link-agentops.sh "C1-a15 정비 가이드"}
PACK=${2:-/packs/first}
set -a; [ -f .env ] && . ./.env; set +a
DEP=${DEPLOYMENT_ID:-c1a15@local}
TAG=${FIRST_PACK_TAG:-first}
dc() { docker compose ${LMG_COMPOSE_ARGS:-} "$@"; }   # LMG_COMPOSE_ARGS: e.g. "-f docker-compose.yml -f compose.key-file.yml"

PID=$(dc exec -T agentops python - "$NAME" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:18868/api/state", timeout=30) as r:
    ps = [p for p in json.loads(r.read())["projects"] if p.get("name") == sys.argv[1]]
if len(ps) != 1:
    sys.exit(f"이름이 '{sys.argv[1]}'인 프로젝트가 {len(ps)}개입니다 — 화면의 프로젝트 이름과 똑같이 주세요")
print(ps[0]["id"])
PY
)
echo "① 프로젝트 ${NAME} = ${PID}"

if dc exec -T agentops sh -c "test -f /var/lib/llmops/.first-operational"; then
  echo "② 첫 운영 지정: 이미 함 — 건너뜀"
else
  dc exec -T agentops python -m llmops.admin deploy-bootstrap --deployment-id "$DEP" --pack-dir "$PACK" \
    --execution-config "$PACK/execution-config.yaml" --decided-by "${USER:-team}" --reason "첫 운영 지정(설치 안내 3)" --pack-tag "$TAG"
  dc exec -T agentops sh -c "date -u +%FT%TZ > /var/lib/llmops/.first-operational"
  echo "② 첫 운영 지정: ${DEP} → ${PACK}(태그 ${TAG})"
fi

umask 077
cat > .env.agentops <<EOF
# made by scripts/link-agentops.sh — the project ${NAME}
LLMOPS_OPS_JUDGE=${PID}
LLMOPS_KG_LINK=${PID}|${DEP}|http://pack-server:8796
EOF
dc up -d --no-deps agentops >/dev/null   # only agentops restarts (the one-run setup containers are not run again)
echo "③ 연결: 운영 Run 판정 · 그래프 반영 → 새 버전 켬 · agentops 다시 켬 — 화면을 새로 고치세요(http://127.0.0.1:${AGENTOPS_PORT:-18868})"
