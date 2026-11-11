# [dev-2] 시연 감시 env(값 0 · 자리만) — 시연 worktree에서 `. demo/dev2/env.sh` 뒤 scripts/py scripts/autodeploy.py start|status|stop
_src="${BASH_SOURCE[0]:-${(%):-%x}}"   # bash · zsh 둘 다
. "$(cd "$(dirname "$_src")" && pwd)/common.sh"
M=$DEMO_VAR
export DEPLOY_VENV="$DEMO_REPO/llmopsagent-deploy/.venv" AD_CONFIG=config/c1a15-dev2-demo.yaml AD_PORT=8796 AD_SERVE_LOG=$M/logs/serve-8796-demo.log AD_STATE=$M/ad-state
export AD_KG=on AD_VERSIONS=off AD_ROLLBACK=on                     # 시작 장치 = 로그(KG 반영 대기 폴더) · 시연 기간 버전 전환 0 · 되돌리기 요청 받음(D-633)
export KG_INBOX=$DEMO_STATE/kg-inbox KG_STATE=$M/kg-state KG_SIGNAL_DIR=$M/signal KG_HISTORY=$M/history-demo.jsonl
export KG_LOADER_PY="$DEMO_REPO/worktrees/c1a15-pack-dev2/kg-test/.venv-kg/bin/python" KGDB_URI=bolt://127.0.0.1:28789 KGDB_ENV_FILE=$M/neo4j.env
export KG_EXGRAPH=$M/llmops-7e6ff55/llmops/exgraph.py   # LLMOps 7e6ff55 사본(D-642 · instruction 받음 · 옛 llmops = a2e9e45)
export KG_GATE="sh -c 'cd $M/llmops-7e6ff55 && exec scripts/isolated-env -- .venv/bin/python -m llmops.kggate \"\$@\"' kggate"
export KG_KEY_FILE="$DEMO_KEY_FILE" KG_CAP=0.02                  # 실제 추출(경로만 · 합 \$0.10 · 한 번 상한 \$0.02)
# 감시 · kg_apply가 8796을 다시 띄울 때도 AI 키 경로 · 주소가 따라가게(D-577 Run 500 고침 · 경로만)
export PACKRT_ENV_FILE="$DEMO_KEY_FILE" OPENAI_BASE_URL=https://api.openai.com/v1
