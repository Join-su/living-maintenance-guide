#!/bin/bash
# [dev-2] D-687 시연 전 점검 한 줄(인자 0 · 바꿈 0 · 비밀 값 출력 0): 감시 살아 있음 · 켠 지 몇 시간 · 8796 태그 = 첫 상태 · 18868이 그 revision을 앎 · 받아 넣기 도구 시작.
# 끝 코드: 0 통과 · 3 감시 없음 · 4 8796 응답 없음 · 5 태그가 첫 상태 아님(ANY_TAG=1이면 안 봄) · 6 18868에 그 revision 없음 · 7 받아 넣기 도구가 안 돎. 첫 실패에서 멈추고 한 줄.
# 한계: 도구 시작은 이 셸에서 봄 — 감시 프로세스 자식으로만 죽는 고장(D-685)은 못 잡음. 그래서 켠 지 3시간이 넘으면 '다시 켜기 권함'을 찍음(D-685는 켠 지 3시간 45분 뒤 남 · 통과는 그대로).
set -o pipefail
. "$(cd "$(dirname "$0")" && pwd)/common.sh"; M=$DEMO_VAR; PACK=$M/c1a15-pack; LLMOPS=${DEMO_LLMOPS_URL:-http://127.0.0.1:18868}; WARN_H=${WARN_HOURS:-3}
fail() { echo "시연 전 점검 멈춤($1): $2"; exit "$1"; }
AD=$(cat "$M/ad-state/pid" 2>/dev/null); [ -n "$AD" ] && kill -0 "$AD" 2>/dev/null || fail 3 "감시 pid ${AD:-없음} 안 돎"
ELAPSED=$(ps -o etime= -p "$AD" | tr -d ' '); H=$(echo "$ELAPSED" | awk -F'[-:]' '{ if (NF==4) print $1*24+$2; else if (NF==3) print $1+0; else print 0 }')
INFO=$(curl -s -m 5 127.0.0.1:8796/info); [ -n "$INFO" ] || fail 4 "8796 /info 응답 없음"
read -r TAG REV <<<"$(echo "$INFO" | "$DEMO_PY" -c 'import json,sys; p=json.load(sys.stdin)["pack"]; print(p.get("tag"), p["pack_revision"])')"
FIRST=$(sed -n 's/^FIRST_TAG=\([^ ]*\).*/\1/p' "$M/reset-graph.sh" | head -1)
[ "${ANY_TAG:-0}" = 1 ] || [ "$TAG" = "$FIRST" ] || fail 5 "8796 $TAG ≠ 첫 상태 $FIRST"
NAME=$(curl -s -m 8 "$LLMOPS/api/state" | "$DEMO_PY" -c '
import json,sys
want=sys.argv[1]
for p in json.load(sys.stdin)["projects"]:
    for r in p["releases"]:
        if (r.get("version") or {}).get("revision")==want: print(r["name"]); raise SystemExit
' "$REV" 2>/dev/null); [ -n "$NAME" ] || fail 6 "18868 버전 목록에 8796 revision ${REV:7:8} 없음"
T=$(mktemp -d); echo '{"format":"preflight","change_id":"preflight"}' > "$T/req.json"
OUT=$(cd "$PACK" && "$PACK/.venv/bin/python" -B tools/change/receive_approved.py "$T/req.json" --repo "$PACK" --branch kg-apply --packrt-accept 0 --status-dir "$T/status" 2>&1); RC=$?; rm -f "$T/req.json"; rmdir "$T" 2>/dev/null
[ $RC -eq 2 ] && echo "$OUT" | grep -q format_error || fail 7 "받아 넣기 도구 끝 코드 $RC: $(echo "$OUT" | tail -1 | cut -c1-200)"
W=""; [ "${H:-0}" -ge "$WARN_H" ] && W=" · ⚠ 감시 켠 지 ${H}시간 — 시연 전 reset으로 다시 켜기 권함"
echo "시연 전 점검 통과 · 감시 살아 있음 pid $AD · 켠 지 $ELAPSED · 8796 $TAG ${REV:7:8} · 18868 $NAME · 받아 넣기 도구 시작 정상$W"
