#!/bin/bash
# pack-server start: the deployment server (port 8796) in the background, then the watcher in the foreground (it restarts the server
# itself on deploy · rollback · graph apply). PID 1 is the compose init (tini), so a server the watcher starts again is reaped.
# No value is printed: the model key and the graph DB account become files in memory (/run/lmg is a tmpfs) and are passed by path.
#
# Needs (compose): OPENAI_API_KEY · NEO4J_AUTH (neo4j/<password>, same as the neo4j container) · the volumes below · kg-init done.
# Values: DEPLOYMENT_ID (c1a15@local) · FIRST_PACK_TAG (first) · AGENTOPS_URL (http://agentops:18868) · KGDB_URI (bolt://neo4j:7687)
#         · SERVER_WAIT seconds for the first start (300) · KG_CAP (0.02 USD per graph extraction).
# Exit: 2 input missing · 3 kg-init state missing · 4 settings check failed · 5 server did not start.
set -euo pipefail
say() { echo "$(date '+%H:%M:%S') pack-server · $*"; }
stop() { echo "PACK_SERVER stop($1) · $2"; exit "$1"; }
mask() { sed -E 's/sk-[A-Za-z0-9_-]{6,}/<가림>/g; s#(://[^/[:space:]:@]+):[^/[:space:]@]+@#\1:<가림>@#g'; }

D=/var/lib/deploy; LIVE=/srv/pack-repo; PACK=$LIVE/c1a15-pack; CFG=$D/config/team.yaml
DEPLOYMENT_ID=${DEPLOYMENT_ID:-c1a15@local}; TAG=${FIRST_PACK_TAG:-first}

# 1 secrets → files in memory (by path only from here on)
mkdir -p /run/lmg; umask 077
# a key given by file path instead (compose secret openai_key: a line OPENAI_API_KEY=… or the key alone) — read here, never printed
if [ -z "${OPENAI_API_KEY:-}" ] && [ -r /run/secrets/openai_key ]; then
  OPENAI_API_KEY=$(sed -n 's/^OPENAI_API_KEY=//p' /run/secrets/openai_key | head -1); [ -n "$OPENAI_API_KEY" ] || OPENAI_API_KEY=$(head -1 /run/secrets/openai_key)
fi
[ -n "${OPENAI_API_KEY:-}" ] || stop 2 "OPENAI_API_KEY 없음 — .env에 키를 넣으세요(배포 서버가 진단 · 검색에 씀)"
printf 'OPENAI_API_KEY=%s\n' "$OPENAI_API_KEY" > /run/lmg/openai.env
[ -n "${NEO4J_AUTH:-}" ] || stop 2 "NEO4J_AUTH 없음(neo4j 컨테이너와 같은 값)"
printf 'NEO4J_AUTH=%s\n' "$NEO4J_AUTH" > /run/lmg/neo4j.env
unset OPENAI_API_KEY NEO4J_AUTH; umask 022
[ -s /secrets/trace-db.env ] || stop 2 "/secrets/trace-db.env 없음 — trace-db-init이 먼저 끝나야 함"

# 2 first state from kg-init (Pack git repository · first tag · first graph history)
[ -d "$PACK/.git" ] && git -C "$PACK" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || stop 3 "Pack 저장소 $PACK에 첫 태그 $TAG 없음 — kg-init을 먼저(docker compose run --rm kg-init)"
[ -s "$LIVE/kg-history.jsonl" ] || stop 3 "$LIVE/kg-history.jsonl 없음(kg-init이 남기는 첫 그래프 이력)"

# 3 folders · the Pack's status folder is the shared status volume (AgentOps reads /shared/status/approved-changes)
mkdir -p "$D/config" "$D/ad-state" "$D/kg-state/done" "$D/logs" "$D/data" "$D/packs" \
         /shared/status/approved-changes /shared/status/deployments /shared/signal /shared/trace-payloads
if [ ! -e "$PACK/status" ]; then ln -s /shared/status "$PACK/status"
elif [ ! -L "$PACK/status" ]; then stop 3 "$PACK/status가 폴더임 — 공유 상태 볼륨(/shared/status)으로 이어야 함(옮긴 뒤 다시 켜기)"; fi
# the link is a file to git (.gitignore '/status/' matches folders only) — keep it out of 'git status' for this clone only,
# or the apply tool refuses ("작업 트리가 깨끗하지 않음: ?? status")
mkdir -p "$PACK/.git/info"; grep -qxF status "$PACK/.git/info/exclude" 2>/dev/null || echo status >> "$PACK/.git/info/exclude"
# the Pack's own python = the image venv: the graph gate (kggate → tools/graph_gate.py · pack_moves) and the apply tool's unit tests
# call <Pack>/.venv/bin/python (.venv is in the Pack's .gitignore)
if [ ! -e "$PACK/.venv" ] || [ -L "$PACK/.venv" ]; then ln -sfn /opt/venv "$PACK/.venv"
else say "⚠ $PACK/.venv가 링크가 아닌 폴더임 — 그대로 둠(관문 · 받아 넣기가 그 파이썬을 씀)"; fi

# 4 settings: once from the template (afterwards the watcher keeps them up to date)
if [ ! -s "$CFG" ]; then
  REV=$(cd /app && scripts/py -c "import sys; sys.path.insert(0, 'scripts'); import graph_swap as S; print(S.rev(sys.argv[1], repo=sys.argv[2])[1])" "$TAG" "$PACK") \
    || stop 4 "첫 태그 $TAG revision을 셀 수 없음"
  WANT=$(sed -n 's/^PACK_REVISION=//p' "$LIVE/first-state.env" 2>/dev/null | head -1)
  [ -z "$WANT" ] || [ "$WANT" = "$REV" ] || stop 4 "첫 태그 revision ${REV:7:8} ≠ kg-init 기록 ${WANT:7:8}"
  DEPLOYMENT_ID=$DEPLOYMENT_ID PACK_TAG=$TAG PACK_REVISION=$REV python -I -c '
import os, string, sys
t = string.Template(open(sys.argv[1], encoding="utf-8").read()).substitute(os.environ)
open(sys.argv[2], "w", encoding="utf-8").write(t)' /app/docker/team.yaml.tmpl "$CFG"
  say "설정 만듦 · 배포 $DEPLOYMENT_ID · 첫 태그 $TAG ${REV:7:8}"
else
  say "설정 그대로(감시가 이어 씀) · $(sed -n 's/^  ref: \([^ ]*\).*/\1/p' "$CFG")"
fi
[ "$(git -C "$PACK" rev-parse --abbrev-ref HEAD)" = kg-apply ] || say "⚠ Pack 저장소 가지가 kg-apply가 아님 — 받아 넣기 도구가 멈출 수 있음"

# 5 watcher · graph apply values (Mac demo's b-env.sh, container paths)
export AD_CONFIG=$CFG DEPLOY_CONFIG=$CFG AD_PORT=8796 DEPLOY_PORT=8796 DEPLOY_HOST=0.0.0.0 AD_SERVE_LOG=$D/logs/serve.log
export AD_VERSIONS=on AD_KG=on AD_ROLLBACK=on AD_AUTO_APPLY=on AD_WAIT=${AD_WAIT:-240}
export AD_REQ_DIR=/shared/agentops-data/approved-changes AD_LLMOPS_URL=${AGENTOPS_URL:-http://agentops:18868}
# apply tool + schema from the image
export AD_PACK_REPO=$PACK AD_PACK_PY=/opt/venv/bin/python AD_PACK_TOOL=/app/docker/pack-apply.py AD_BRANCH=kg-apply AD_ACCEPT=0.15.0 AD_C01_REF=$TAG
export AD_STATE=$D/ad-state AD_STATUS_DIR=/shared/status/deployments
export KG_INBOX=/shared/kg-inbox KG_STATE=$D/kg-state KG_SIGNAL_DIR=/shared/signal KG_HISTORY=$LIVE/kg-history.jsonl
export KG_LOADER_PY=/opt/venv/bin/python KGDB_URI=${KGDB_URI:-bolt://neo4j:7687} KGDB_ENV_FILE=/run/lmg/neo4j.env
export KG_EXGRAPH=/opt/llmops-gate/llmops/exgraph.py
export KG_GATE="sh -c 'cd /opt/llmops-gate && exec /opt/venv/bin/python -m llmops.kggate \"\$@\"' kggate"
export KG_KEY_FILE=/run/lmg/openai.env KG_CAP=${KG_CAP:-0.02} KG_LEDGER_WAIT=90 KG_KEEP_LAYERS=5
export PACKRT_ENV_FILE=/run/lmg/openai.env OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1} ORT_DISABLE_TELEMETRY=1
mkdir -p "$KG_INBOX" 2>/dev/null || true
# the same values for helpers run with docker exec (docker/pack-switch.sh) — paths and switches only, no secret value
export -p | grep -E '^declare -x (AD_|KG_|KGDB_|DEPLOY_|PACKRT_ENV_FILE|OPENAI_BASE_URL|ORT_)' > /run/lmg/watch.env

# 6 settings bind to packrt (store · model releases found) before the server starts
cd /app
RC=0; OUT=$(scripts/py scripts/r12-update check --config "$CFG" 2>&1) || RC=$?
echo "$OUT" | tail -1 | mask
[ "$RC" -eq 0 ] || stop 4 "packrt 묶기 대조 실패(위 한 줄)"

# 7 server (its own session, like the watcher starts it) → health
setsid nohup scripts/serve.sh >> "$AD_SERVE_LOG" 2>&1 < /dev/null &
for _ in $(seq 1 "${SERVER_WAIT:-300}"); do curl -sf -m 2 http://127.0.0.1:8796/health >/dev/null 2>&1 && break; sleep 1; done
curl -sf -m 2 http://127.0.0.1:8796/health >/dev/null 2>&1 || { tail -5 "$AD_SERVE_LOG" | mask; stop 5 "배포 서버가 ${SERVER_WAIT:-300}초 안에 뜨지 않음(위 로그 끝)"; }
say "배포 서버 뜸 · $(curl -s -m 5 http://127.0.0.1:8796/info | python -I -c 'import json,sys; d=json.load(sys.stdin); p=d["pack"]; print(p.get("tag"), p["pack_revision"][7:15], d["provider"]["mode"])')"

# 8 watcher in the foreground (logs go to docker logs)
exec scripts/py scripts/autodeploy.py run
