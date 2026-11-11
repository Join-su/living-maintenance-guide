#!/bin/bash
# [dev-2] 8796 AI를 가짜/실제로(D-576 · D-578 덧 1): fake = AI 나눔 · 보고서 문장 가짜(시연 복제 tools/fake 답) + 질의 임베딩만 실제 칸 / http = AI 셋 실제(gpt-4.1-mini · 키 경로만)
# 설정 한 칸 + embedding 칸(http면 뺌 — 설정 규칙) → 8796 다시 띄움. 사용: bash demo/dev2/provider.sh fake|http
set -eo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/common.sh"; cd "$DEMO_W"
MODE="${1:?fake | http}"; [ "$MODE" = fake ] || [ "$MODE" = http ] || { echo "fake 또는 http"; exit 2; }
cp config/c1a15-dev2-demo.yaml "$DEMO_VAR/config-before-provider-$(date '+%H%M').yaml"
python3 - "$MODE" <<'PY'
import re,sys
p='config/c1a15-dev2-demo.yaml'; s=open(p).read()
emb = "embedding:                     # 질의 임베딩만 실제(키는 경로만)\n  provider: http\n  env_file: ../../../c1a15-pack/.env\n  base_url: https://api.openai.com/v1\n"
s = re.sub(r'^provider: \S+[^\n]*$', 'provider: fake' if sys.argv[1] == 'fake' else 'provider: http', s, count=1, flags=re.M)
s = re.sub(r'embedding:[^\n]*\n  provider: http\n  env_file: [^\n]*\n  base_url: [^\n]*\n', '', s)
if sys.argv[1] == 'fake':
    s = s.replace('\nregistration:', '\n' + emb + 'registration:', 1)
open(p,'w').write(s)
PY
grep -n '^provider\|^embedding' config/c1a15-dev2-demo.yaml
bash "$D/restart.sh"
