#!/bin/bash
# pack-switch: the deployment server switches to one Pack tag — the efficiency scenario start (guide 08 · scripts/efficiency-start.sh ①).
# The demo did the same with its reset-graph-eff script. Runs inside the pack-server container:
#   docker compose exec -T pack-server /app/docker/pack-switch.sh eff-model | eff-structure      (DRY=1: check only, nothing changes)
# Steps, all under the watcher's own lock (KG_STATE/kg-apply.lock), so the watcher keeps running and never deploys at the same time:
#   ① the Pack working branch kg-apply = the tag's commit — the apply tool takes the next approved change on top of what is checked out
#     (its "current revision = base_revision" check), so without this the next ✦ deploy is refused
#   ② settings ref · expect_revision → the tag and its packrt revision · bind check · only the server restarts (graph_swap.swap — the frozen
#     code the watcher uses; it puts the old settings and server back if a step fails) · /info revision checked
#   ③ /info tag · revision = the tag's · data release (drs) unchanged (and = EFF_DRS when given) · working branch = the tag · watcher alive
# The tags eff-model · eff-structure (and same-named branches) are added by kg-init; git calls here name refs/tags/<tag>. No value is printed. Back to the first state: guide 09.
# Exit: 0 switched (or already on it) · 2 check before switching failed (nothing changed) · 3 switch failed (branch put back) · 4 no /info
#       · 5 /info tag · revision differs · 6 watcher not running · 7 working branch ≠ tag · 8 lock not free in LOCK_WAIT seconds (nothing changed)
#       · 9 drs differs (switched)
set -uo pipefail
TAG=${1:?쓰는 법: pack-switch.sh eff-model | eff-structure}
stop() { echo "PACK_SWITCH stop($1) · $2"; exit "$1"; }
mask() { sed -E 's/sk-[A-Za-z0-9_-]{6,}/<가림>/g; s#(://[^/[:space:]:@]+):[^/[:space:]@]+@#\1:<가림>@#g'; }
case "$TAG" in eff-model|eff-structure) ;; *) [ "${PACK_SWITCH_ANY_TAG:-0}" = 1 ] || stop 2 "태그는 eff-model 또는 eff-structure만(다른 태그는 PACK_SWITCH_ANY_TAG=1)";; esac

# the watcher's values (paths · switches only, written by pack-server-start)
[ -r /run/lmg/watch.env ] || stop 2 "/run/lmg/watch.env 없음 — pack-server를 이 이미지로 다시 띄우세요(docker compose up -d pack-server)"
. /run/lmg/watch.env
PACK=$AD_PACK_REPO; cd /app
git -C "$PACK" rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null || stop 2 "Pack 저장소에 태그 $TAG 없음 — kg-init이 효율 출발 태그를 더한 뒤에(docker compose run --rm kg-init)"
COMMIT=$(git -C "$PACK" rev-parse "refs/tags/$TAG^{commit}")
# kg-init makes a branch with the same name as the tag; the settings keep the short name (the server · /info · AgentOps --pack-tag use it,
# and git takes the tag first), so a same-named branch must be on the same commit or the name could mean two Packs
HC=$(git -C "$PACK" rev-parse -q --verify "refs/heads/$TAG^{commit}" 2>/dev/null)
[ -z "$HC" ] || [ "$HC" = "$COMMIT" ] || stop 2 "가지 $TAG ${HC:0:7} ≠ 태그 $TAG ${COMMIT:0:7} — 같은 이름이 두 Pack을 가리킴(바꿈 0)"
[ -z "$(git -C "$PACK" status --porcelain --untracked-files=no)" ] || stop 2 "Pack 작업 폴더에 바뀜 있음(커밋 안 된 것)"
WANT=$(scripts/py -c "import sys; sys.path.insert(0, 'scripts'); import graph_swap as S; print(S.rev(sys.argv[1], repo=sys.argv[2])[1])" "refs/tags/$TAG" "$PACK" 2>/dev/null) \
  && [ -n "$WANT" ] || stop 2 "태그 $TAG revision을 셀 수 없음"
info() { curl -s -m 5 "http://127.0.0.1:${AD_PORT:-8796}/info" | python -I -c 'import json,sys; d=json.load(sys.stdin); p=d["pack"]; print(p.get("tag"), p["pack_revision"], d.get("data_release_id"))' 2>/dev/null; }
BEFORE=$(info); [ -n "$BEFORE" ] || stop 4 "배포 서버 /info 응답 없음(바꿈 0)"
read -r BT BREV BDRS <<<"$BEFORE"
DONE=$(ls "$AD_STATE/done" 2>/dev/null | wc -l | tr -d ' ')
NOTE=""; [ "$DONE" = 0 ] || NOTE=" · 지난 배포 처리 표시 ${DONE}개(같은 change id가 다시 나오면 감시가 건너뜀 — 처음부터는 안내 09)"
if [ "${DRY:-0}" = 1 ]; then
  echo "PACK_SWITCH dry · 지금 $BT ${BREV:7:8} $BDRS · 가지 $(git -C "$PACK" rev-parse --short HEAD) → 출발 $TAG ${WANT:7:8} ${COMMIT:0:7}$NOTE"
  exit 0
fi

# ① ② under the watcher's lock (it holds the same lock for every deploy · rollback · graph apply)
exec 9>"$KG_STATE/kg-apply.lock"
flock -w "${LOCK_WAIT:-600}" 9 || stop 8 "감시 잠금을 ${LOCK_WAIT:-600}초 안에 못 얻음(배포 · 반영 중) — 바꿈 0"
OLD=$(git -C "$PACK" rev-parse HEAD); BR=$(git -C "$PACK" rev-parse --abbrev-ref HEAD)
git -C "$PACK" checkout -q -B kg-apply "$COMMIT" || stop 3 "Pack 작업 가지를 $TAG로 못 바꿈"
RC=0; OUT=$(scripts/py scripts/graph_swap.py swap "$TAG" 2>&1) || RC=$?
echo "$OUT" | tail -2 | mask
if [ "$RC" -ne 0 ]; then
  git -C "$PACK" checkout -q -B "$BR" "$OLD"
  stop 3 "전환 실패(위 줄 · 설정 · 서버는 graph_swap이 옛 것으로) — Pack 작업 가지 되돌림 ${OLD:0:7}"
fi
flock -u 9

# ③ checks
NOW=$(info); [ -n "$NOW" ] || stop 4 "전환 뒤 /info 응답 없음"
read -r T REV DRS <<<"$NOW"
# /info may name another tag of the same commit (first = d001-g1 on a fresh stack) — the commit and the revision must match
TC=$(git -C "$PACK" rev-parse -q --verify "refs/tags/$T^{commit}" 2>/dev/null)
[ "$TC" = "$COMMIT" ] && [ "$REV" = "$WANT" ] || stop 5 "/info $T ${REV:7:8} ≠ 출발 $TAG ${WANT:7:8}"
[ "$(git -C "$PACK" rev-parse HEAD)" = "$COMMIT" ] || stop 7 "Pack 작업 가지 $(git -C "$PACK" rev-parse --short HEAD) ≠ $TAG ${COMMIT:0:7}"
AD=$(pgrep -f 'scripts/autodeploy.py run' | head -1); [ -n "$AD" ] || stop 6 "감시가 안 돎(pack-server 다시 띄우기)"
[ "$DRS" = "$BDRS" ] || stop 9 "drs $BDRS → $DRS(데이터가 바뀜 · 전환은 됨)"
[ -z "${EFF_DRS:-}" ] || [ "$DRS" = "$EFF_DRS" ] || stop 9 "drs $DRS ≠ 기대 $EFF_DRS(전환은 됨)"
echo "PACK_SWITCH ok · $BT ${BREV:7:8} → $T ${REV:7:8} · drs $DRS 그대로 · Pack 작업 가지 kg-apply = $TAG ${COMMIT:0:7} · 감시 pid $AD$NOTE"
