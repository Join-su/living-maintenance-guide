#!/usr/bin/env bash
# 시연 스택 도구(scripts/demo) 자기 시험 — Docker · 스택 손 0(띄우지 않고 멈춤 길 · 꼴만 봄). check-fast가 부름.
#   1 쓰임 글에 backup · rebuild · rollback · up · down
#   2 모르는 스택 이름 · 상태 폴더 없음 → 멈춤(1)
#   3 인자 빠짐 → 멈춤: backup <폴더> · rollback <꼬리표> · ROLLBACK_TREE · 트리와 꼬리표 다름
#   4 up이 app_rev(되돌릴 꼬리표)를 남김
#   5 scripts/demo 안 사용자 홈 아래 절대 경로(맥 · 리눅스) 0
# 상태 폴더는 가짜 TMPDIR — 진짜 $TMPDIR/c1a15-demo-* 손 0.
set -u
H=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$H/../.." && pwd)
S="$H/stack.sh"
FAKE=$(mktemp -d "${TMPDIR:-/tmp}/demo-selftest.XXXXXX")
trap 'rm -rf "${FAKE:?}"' EXIT
fail=0
ok() { echo "○ $*"; }
no() { echo "× $*"; fail=1; }
run() { TMPDIR="$FAKE/" bash "$S" "$@" 2>&1; }

[ -f "$S" ] || { echo "× stack.sh 없음"; echo "demo selftest: FAIL"; exit 1; }
u=$(run 2>&1 || true)
for c in up deps backup rebuild rollback down; do echo "$u" | grep -q "^#   $c" && ok "쓰임에 $c" || no "쓰임에 $c 없음"; done
out=$(run prod up); [ $? -ne 0 ] && echo "$out" | grep -q "스택 이름" && ok "모르는 스택 이름 → 멈춤" || no "모르는 스택 이름 '$out'"

for k in real copy view; do
  for c in "backup $FAKE/b" rebuild "rollback 88e0d06" info check; do
    out=$(ROLLBACK_TREE="$ROOT" run $k $c); rc=$?
    [ $rc -ne 0 ] && echo "$out" | grep -q "스택 없음" && ok "$k $c → 스택 없음 멈춤" || no "$k $c → rc=$rc '$out'"
  done
  st=c1a15-demo-$k; [ $k = view ] && st=c1a15-dev2-view
  mkdir -p "$FAKE/$st"; printf 'POSTGRES_PASSWORD=x\n' > "$FAKE/$st/stack.env"
  out=$(run $k backup); [ $? -ne 0 ] && echo "$out" | grep -q "백업 폴더" && ok "$k backup 폴더 빠짐 → 멈춤" || no "$k backup 폴더 빠짐 '$out'"
  out=$(run $k rollback); [ $? -ne 0 ] && echo "$out" | grep -q "꼬리표" && ok "$k rollback 꼬리표 빠짐 → 멈춤" || no "$k rollback 꼬리표 빠짐 '$out'"
  out=$(TMPDIR="$FAKE/" env -u ROLLBACK_TREE bash "$S" $k rollback 88e0d06 2>&1); [ $? -ne 0 ] && echo "$out" | grep -q "ROLLBACK_TREE" && ok "$k rollback 트리 빠짐 → 멈춤" || no "$k rollback 트리 빠짐 '$out'"
  out=$(ROLLBACK_TREE="$FAKE" run $k rollback 0000000); [ $? -ne 0 ] && echo "$out" | grep -q "아님" && ok "$k rollback 트리 · 꼬리표 다름 → 멈춤" || no "$k rollback 트리 · 꼬리표 다름 '$out'"
  rm -rf "${FAKE:?}/$st"
done

awk '/^  up\)/,/;;/' "$S" | grep -q 'app_rev' && ok "up → app_rev" || no "up이 app_rev를 남기지 않음"
# 6 view(D-546 F2) = ML만 · 받은 편지함 묶음 · 28181 · 28182 · DB 55453, real · copy = 경계 · 받은 편지함 0
for k in real copy view; do
  got=$(TMPDIR="$FAKE/" bash -c '. "$1" "$2"; echo "$ALARM_MODE|${KG_INBOX_DIR:+inbox}|$TECH_PORT|$LEAD_PORT|$DB_HOST_PORT|$REPORT_START|$CHECKS"' _ "$H/stack-env.sh" "$k")
  case $k in real) want="boundary||8081|8082|55437||deploy" ;; copy) want="boundary||18181|18182|55440||deploy" ;; view) want="ml|inbox|28181|28182|55453|2|off" ;; esac
  [ "$got" = "$want" ] && ok "$k 칸 $got" || no "$k 칸 '$got' ≠ '$want'"
done
# 7 빌드 전 의존 검사(D-546 덧 38): 같은 로컬 앱 이미지 없으면 멈춤 · 있으면 통과 · build_images가 빌드 전에 부름(가짜 docker · 진짜 손 0)
mkdir -p "$FAKE/bin"
cat > "$FAKE/bin/docker" <<'DK'
#!/bin/sh
case "$1" in
  images) echo "c1a15-demo-real-api:latest" ;;
  run) [ -n "$FAKE_SAME" ] && cat "$FAKE_ROOT/server/pyproject.toml" "$FAKE_ROOT/server/uv.lock" "$FAKE_ROOT/server/.python-version" || echo old ;;
esac
DK
chmod +x "$FAKE/bin/docker"
out=$(PATH="$FAKE/bin:$PATH" FAKE_ROOT="$ROOT" run view deps); [ $? -ne 0 ] && echo "$out" | grep -q "빌드 전 멈춤" && ok "의존 다른 이미지만 → 빌드 전 멈춤" || no "의존 검사 멈춤 '$out'"
out=$(PATH="$FAKE/bin:$PATH" FAKE_ROOT="$ROOT" FAKE_SAME=1 run view deps); [ $? -eq 0 ] && echo "$out" | grep -q "의존 같음" && ok "의존 같은 이미지 → 통과" || no "의존 검사 통과 '$out'"
awk '/^build_images\(\) \{/,/^\}/' "$S" | head -3 | grep -q deps_check && ok "build_images가 빌드 전에 의존 검사" || no "build_images 의존 검사 빠짐"
grep -q '/inbox/kg' "$H/ports-view.yml" && grep -q '28181:8081' "$H/ports-view.yml" && ok "ports-view 받은 편지함 · 포트" || no "ports-view 칸 빠짐"
HOME_PAT='/U''sers/|/h''ome/'  # 이 파일 자신이 걸리지 않게 낱말을 나눠 씀
hits=$(grep -rlE "$HOME_PAT" "$H" || true)
[ -z "$hits" ] && ok "scripts/demo 맥 절대 경로 0" || no "맥 절대 경로: $hits"

[ $fail -eq 0 ] && echo "demo selftest: pass" || { echo "demo selftest: FAIL"; exit 1; }
