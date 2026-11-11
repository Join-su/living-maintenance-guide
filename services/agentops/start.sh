#!/bin/sh
# AgentOps start. A Pack process reads its model key from <Pack folder>/.env (a link to the file below) and runs as the evaluation
# account packeval, so the key file is written here from the compose environment: root-owned, group packeval, 0640, in memory
# (/run/lmg is a tmpfs). No value is printed and nothing is kept in the image.
set -eu
mkdir -p /run/lmg
# a key given by file path instead (compose secret openai_key: a line OPENAI_API_KEY=… or the key alone) — read here, never printed
if [ -z "${OPENAI_API_KEY:-}" ] && [ -r /run/secrets/openai_key ]; then
  OPENAI_API_KEY=$(sed -n 's/^OPENAI_API_KEY=//p' /run/secrets/openai_key | head -1); [ -n "$OPENAI_API_KEY" ] || OPENAI_API_KEY=$(head -1 /run/secrets/openai_key)
fi
if [ -n "${OPENAI_API_KEY:-}" ]; then
  # the Pack's models.yaml also reads OPENAI_BASE_URL from this file (base_url_env), as pack-server gives it
  ( umask 077; printf 'OPENAI_API_KEY=%s\nOPENAI_BASE_URL=%s\n' "$OPENAI_API_KEY" "${OPENAI_BASE_URL:-${LLMOPS_LLM_BASE_URL:-https://api.openai.com/v1}}" > /run/lmg/openai.env )   # umask only here: the server keeps 022 (its copies must be readable by packeval)
  chgrp packeval /run/lmg/openai.env && chmod 0640 /run/lmg/openai.env
  export LLMOPS_OPENAI_ENV_FILE=/run/lmg/openai.env LLMOPS_OPENAI_API_KEY="$OPENAI_API_KEY"
fi
unset OPENAI_API_KEY
# the quality review model (Jev) key — the efficiency wheel's ⑤ review asks it (decide@1). The same two ways: JEV_API_KEY from .env, or
# by file path (compose.jev-key-file.yml, secret jev_key: a line TYPESAFE_API_KEY=… or JEV_API_KEY=…, `export` and quotes allowed, or the
# key alone). Written to a root-only file the server reads (LLMOPS_TYPESAFE_ENV_FILE); Pack processes (packeval) cannot read it.
if [ -z "${JEV_API_KEY:-}" ] && [ -r /run/secrets/jev_key ]; then
  JEV_API_KEY=$(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?(TYPESAFE_API_KEY|JEV_API_KEY)=//p' /run/secrets/jev_key | head -1 | sed -E "s/^[\"'](.*)[\"'][[:space:]]*$/\\1/")
  [ -n "$JEV_API_KEY" ] || ! grep -q = /run/secrets/jev_key || echo "agentops: Jev 키 파일에 TYPESAFE_API_KEY= 줄이 없음"
  [ -n "$JEV_API_KEY" ] || grep -q = /run/secrets/jev_key || JEV_API_KEY=$(head -1 /run/secrets/jev_key)   # the key alone
fi
if [ -n "${JEV_API_KEY:-}" ]; then
  ( umask 077; printf 'TYPESAFE_API_KEY=%s\n' "$JEV_API_KEY" > /run/lmg/typesafe.env )
  export LLMOPS_TYPESAFE_ENV_FILE=/run/lmg/typesafe.env
fi
unset JEV_API_KEY
REVIEW=${LLMOPS_AUTO_REVIEW:-on}
if [ "${LLMOPS_LLM_MODE:-fake}" = real ] && [ "$REVIEW" = on ] && [ ! -s /run/lmg/typesafe.env ]; then
  echo "agentops: Jev 키 없음 — 효율 개선에서 출력이 바뀐 후보는 ⑤ 품질 검토에서 멈춤(decide_no_key_file) · 끄려면 .env에 LLMOPS_AUTO_REVIEW=off"
fi
echo "agentops: 포트 ${LLMOPS_PORT} · 실행 모드 ${LLMOPS_LLM_MODE:-fake} · OpenAI 키 $( [ -s /run/lmg/openai.env ] && echo 있음 || echo 없음) · Jev 키 $( [ -s /run/lmg/typesafe.env ] && echo 있음 || echo 없음) · Jev 주소 $( [ -n "${LLMOPS_TYPESAFE_BASE_URL:-}" ] && echo 있음 || echo 없음) · 품질 검토 ${REVIEW} · 그래프 연결 $( [ -n "${LLMOPS_KG_LINK:-}" ] && echo 켬 || echo '꺼짐(프로젝트 만든 뒤 scripts/link-agentops.sh)')"
exec python -m llmops
