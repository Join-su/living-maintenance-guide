#!/bin/sh
# 배포단 자체 .env(git 밖, 권한 600)를 만든다: scripts/make-env.sh [--force]
# llmops-lite-v2 db/.env에서 LLMOPSAGENT_RUNTIME_DSN·LLMOPSAGENT_TRACE_DSN 두 줄만 옮긴다(다른 DSN·비밀번호는 옮기지 않음). 값은 출력하지 않는다.
# llmops가 비밀번호를 바꾸면 --force로 다시 만든다. 확인: scripts/py scripts/check-env.py
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${LLMOPS_DB_ENV:-$HERE/../llmops-lite-v2/db/.env}"
DST="$HERE/.env"
[ -f "$SRC" ] || { echo "원본이 없습니다: $SRC" >&2; exit 2; }
if [ -e "$DST" ] && [ "${1:-}" != "--force" ]; then echo ".env가 이미 있습니다(다시 만들려면 --force)" >&2; exit 2; fi
umask 077
TMP="$(mktemp "$HERE/.env.tmp.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
{
  echo "# 배포단 자체 .env — scripts/make-env.sh가 llmops-lite-v2 db/.env에서 두 이름만 옮김. git에 올리지 않음, 출력 금지"
  for name in LLMOPSAGENT_RUNTIME_DSN LLMOPSAGENT_TRACE_DSN; do
    line="$(grep -E "^${name}=" "$SRC" | tail -1 || true)"
    [ -n "$line" ] || { echo "원본에 $name이 없습니다" >&2; exit 2; }
    printf '%s\n' "$line"
  done
} > "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$DST"
trap - EXIT
echo ".env를 만들었습니다(이름 2개, 권한 600). 값은 출력하지 않습니다"
