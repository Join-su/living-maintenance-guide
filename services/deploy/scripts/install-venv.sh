#!/bin/sh
# 배포단 전용 venv(.venv) 설치·재고정: scripts/install-venv.sh <packrt 저장소 태그> [<llmops-lite-v2 commit 40자>]
# (VFE EV-005 ES-D, CHG-01 · CHG-07) packrt · tracewriter = packrt 저장소(agent-pack-runtime)의 같은 annotated 태그 하나(D-63 · D-69).
# - 태그만 받음: refs/tags/<태그>가 annotated 태그가 아니면(가지 이름 · commit · lightweight 태그) 멈춤. 그 commit의 git archive 사본에서 비편집 설치
# - 제3자 패키지: llmops commit(기본 = 지금 pinned.json third_party.llmops_commit)의 uv.lock 판(uv export 제약).
#   단 packrt · tracewriter pyproject가 `==`로 고정한 판(langgraph · langsmith, D-70 · D-71 — 둘 다 있어야 함)은 그 판으로 바꿈
# - 설치 전: 해석 결과(uv pip compile)에 requirements/deploy-venv.txt에 없던 패키지 종류가 있으면 종료 3(venv · 기록 그대로)
# - 설치 뒤: uv pip check, 다시 새 종류 확인. 기록(pinned.json · requirements/deploy-venv.txt)은 모든 확인이 끝난 뒤에만 바꿈
# 환경 변수: PACKRT_REPO(기본 ../agent-pack-runtime) · LLMOPS_REPO(기본 ../llmops-lite-v2) · DEPLOY_VENV_DIR(기본 .venv)
#   · DEPLOY_PIN_OUT(기록 폴더, 기본 저장소 뿌리 — 사본 예행연습은 다른 폴더) · INSTALL_PLAN_ONLY=1(출처 · 제약만 보이고 멈춤, 설치 없음)
# 사용자 승인: 지시 008(2026-10-13, PyPI 내려받기 · 같은 패키지 재고정), 답장 019-09(A-4, 2026-10-18 — packrt 태그 v0.9.0, langgraph 1.2.13) · 019-11(13:56 승인 범위 — 태그 v0.10.0)
# 파이프 앞 명령의 실패를 놓치지 않게, 실패를 봐야 하는 명령은 파일로 받고 나서 거른다.
set -eu
TAG=${1:?packrt 저장소 태그를 주세요(예 v0.9.0)}
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PACKRT="${PACKRT_REPO:-$HERE/../agent-pack-runtime}"
LLMOPS="${LLMOPS_REPO:-$HERE/../llmops-lite-v2}"
VENV="${DEPLOY_VENV_DIR:-$HERE/.venv}"
OUT="${DEPLOY_PIN_OUT:-$HERE}"
LOCK_COMMIT=${2:-$(sed -n 's/^ *"llmops_commit": "\([0-9a-f]\{40\}\)".*/\1/p' "$HERE/pinned.json" | head -1)}
case "$LOCK_COMMIT" in
  *[!0-9a-f]*|"") echo "제3자 판 llmops commit은 40자 hex만 받습니다(pinned.json 또는 두 번째 인자)" >&2; exit 2;;
esac
[ ${#LOCK_COMMIT} -eq 40 ] || { echo "제3자 판 llmops commit은 40자 hex만 받습니다" >&2; exit 2; }
if ! git -C "$PACKRT" show-ref --verify --quiet "refs/tags/$TAG" || [ "$(git -C "$PACKRT" cat-file -t "refs/tags/$TAG")" != tag ]; then
  echo "packrt 저장소에 annotated 태그 ${TAG}가 없습니다(태그만 받음 — 가지 · commit · lightweight 태그는 안 됨) — 멈춤" >&2; exit 2
fi
COMMIT="$(git -C "$PACKRT" rev-parse --verify "refs/tags/$TAG^{commit}")"
TAG_OBJECT="$(git -C "$PACKRT" rev-parse --verify "refs/tags/$TAG")"
FULL="$(git -C "$LLMOPS" rev-parse --verify "$LOCK_COMMIT^{commit}")"
PY_VERSION="$("$LLMOPS/.venv/bin/python" -c 'import platform;print(platform.python_version())')"
PY_HOME="$(sed -n 's/^home = //p' "$LLMOPS/.venv/pyvenv.cfg")"   # llmops .venv의 base 인터프리터(Python은 내려받지 않음)
PY_BIN="$PY_HOME/python${PY_VERSION%.*}"
[ -x "$PY_BIN" ] || { echo "llmops와 같은 인터프리터가 없습니다: $PY_BIN — 멈춤(질문)" >&2; exit 2; }
[ "$("$PY_BIN" -c 'import platform;print(platform.python_version())')" = "$PY_VERSION" ] || { echo "인터프리터 판이 llmops(${PY_VERSION})와 다릅니다: $PY_BIN — 멈춤(질문)" >&2; exit 2; }
export UV_PYTHON_DOWNLOADS=never
WORK_BC="$(mktemp "${DEPLOY_WORK_DIR:-${TMPDIR:-/tmp}}/deploy-bc.XXXXXX")"   # 빌드 backend 판 = 지난 기록 build_isolation(이미지의 해시 고정 backend와 같게, 10-21)
[ ! -f "$HERE/pinned.json" ] || sed -n 's/^ *"build_isolation": "\(setuptools==[0-9][0-9.]*\)".*/\1/p' "$HERE/pinned.json" > "$WORK_BC"
[ -s "$WORK_BC" ] && export UV_BUILD_CONSTRAINT="${UV_BUILD_CONSTRAINT:-$WORK_BC}"
WORK="$(mktemp -d "${DEPLOY_WORK_DIR:-${TMPDIR:-/tmp}}/deploy-venv.XXXXXX")"   # archive 사본·제약·기록 초안(끝나면 지움)
trap 'rm -rf "$WORK" "$WORK_BC"' EXIT
mkdir -p "$WORK/src" "$WORK/proj"
git -C "$PACKRT" archive -o "$WORK/src.tar" "$COMMIT" packrt tracewriter
tar -xf "$WORK/src.tar" -C "$WORK/src"
git -C "$LLMOPS" show "$FULL:uv.lock" > "$WORK/uv.lock"
git -C "$LLMOPS" show "$FULL:pyproject.toml" > "$WORK/proj/pyproject.toml"
cp "$WORK/uv.lock" "$WORK/proj/uv.lock"
# packrt · tracewriter pyproject의 dependencies 중 `이름==판`(extras · 마커 허용, 판 하나만)
"$PY_BIN" -I - "$WORK/src/packrt/pyproject.toml" "$WORK/src/tracewriter/pyproject.toml" > "$WORK/pins.txt" <<'PY'
import re, sys, tomllib
pat = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*==\s*([A-Za-z0-9.+!_-]+)\s*(;.*)?$")
for f in sys.argv[1:]:
    with open(f, "rb") as fh:
        for dep in tomllib.load(fh)["project"].get("dependencies", []):
            m = pat.match(dep)
            if m:
                print(f"{m[1]}=={m[3]}" + (f" ; {m[4][1:].strip()}" if m[4] else ""))
PY
norm() { sed 'y/ABCDEFGHIJKLMNOPQRSTUVWXYZ_./abcdefghijklmnopqrstuvwxyz--/'; }
sed 's/==.*//' "$WORK/pins.txt" | norm > "$WORK/pin-names.txt"
# 조건(마커)이 붙은 고정 판은 lock 줄을 지우면 그 조건 밖에서 판이 풀림 — 묻고 정함
if grep -q ';' "$WORK/pins.txt"; then echo "packrt 고정 판에 조건(마커)이 붙어 있습니다: $(grep ';' "$WORK/pins.txt" | tr '\n' ' ')— 멈춤(질문)" >&2; exit 2; fi
for need in langgraph langsmith; do
  grep -qx "$need" "$WORK/pin-names.txt" || { echo "packrt 태그 pyproject에 ${need}== 고정이 없습니다 — 멈춤(질문)" >&2; exit 2; }
done
# llmops는 packrt · tracewriter를 자기 안(옛 packages/) 또는 git 출처로 둠 — 둘 다 제약에서 뺌
(cd "$WORK/proj" && uv export --frozen --all-groups --no-hashes --no-emit-project --no-emit-package packrt --no-emit-package tracewriter --no-header) > "$WORK/export.txt"
grep -v '^ *#' "$WORK/export.txt" | grep -v '^ *$' > "$WORK/export-lines.txt" || :
[ -s "$WORK/export-lines.txt" ] || { echo "uv export 결과가 비었습니다 — 멈춤" >&2; exit 1; }
: > "$WORK/constraints.txt"
while IFS= read -r line; do
  name="$(printf '%s\n' "$line" | sed 's/[=<>!~;[ @].*//' | norm)"
  grep -qx "$name" "$WORK/pin-names.txt" || printf '%s\n' "$line" >> "$WORK/constraints.txt"
done < "$WORK/export-lines.txt"
cat "$WORK/pins.txt" >> "$WORK/constraints.txt"
echo "packrt 저장소 태그 $TAG → commit $COMMIT"
echo "packrt · tracewriter 고정 판: $(tr '\n' ' ' < "$WORK/pins.txt")"
echo "제3자 판: llmops $FULL uv.lock(고정 판은 위 판으로 바꿈)"
if [ "${INSTALL_PLAN_ONLY:-}" = 1 ]; then echo "--- 제약"; cat "$WORK/constraints.txt"; exit 0; fi
names() { sed -n 's/^\([A-Za-z0-9_.-]*\)[=@ ].*/\1/p' "$1" | norm | sort -u; }
if [ -f "$HERE/requirements/deploy-venv.txt" ]; then cp "$HERE/requirements/deploy-venv.txt" "$WORK/before.txt"; else : > "$WORK/before.txt"; fi
names "$WORK/before.txt" > "$WORK/names-before.txt"
printf '%s\n' fastapi uvicorn langgraph jsonschema pyyaml httpx "psycopg[binary]" pytest > "$WORK/request.in"
uv pip compile "$WORK/request.in" -c "$WORK/constraints.txt" --python "$PY_BIN" --no-header --no-annotate > "$WORK/resolved.txt"
names "$WORK/resolved.txt" > "$WORK/names-resolved.txt"
NEW="$(comm -13 "$WORK/names-before.txt" "$WORK/names-resolved.txt")"
if [ -n "$NEW" ]; then echo "새 패키지 종류가 생깁니다(승인 범위 밖 — 설치 전 멈춤, venv · 기록 그대로): $(echo $NEW)" >&2; exit 3; fi
if [ -f "$VENV/pyvenv.cfg" ] && ! grep -q "^version_info = $PY_VERSION\$" "$VENV/pyvenv.cfg"; then
  echo "이미 있는 venv의 Python 판이 ${PY_VERSION}이 아닙니다 — venv를 지우고 다시 만드세요: $VENV" >&2; exit 2
fi
[ -d "$VENV" ] || uv venv "$VENV" --no-python-downloads --python "$PY_BIN"
uv pip install --python "$VENV" -c "$WORK/constraints.txt" -r "$WORK/resolved.txt"
uv pip install -v --python "$VENV" --no-deps --reinstall-package packrt --reinstall-package tracewriter "$WORK/src/packrt" "$WORK/src/tracewriter" > "$WORK/build.log" 2>&1 \
  || { cat "$WORK/build.log" >&2; echo "packrt · tracewriter 설치 실패 — 기록은 바꾸지 않음" >&2; exit 1; }
cat "$WORK/build.log" >&2
uv pip check --python "$VENV"
SETUPTOOLS="$(sed -n 's/.*Selecting: \(setuptools==[0-9][0-9.]*\).*/\1/p' "$WORK/build.log" | head -1)"   # uv가 고른 판(색인의 옛 파일 이름 setuptools-0.6b1… 줄을 잡지 않게, 10-21)
uv pip freeze --python "$VENV" > "$WORK/freeze-all.txt"
# 맥 전용 덧목록(requirements/onnx-extra.txt, KS-3)으로 넣은 종류는 venv에 남기되 기록 · 새 종류 확인에서 뺌(deploy-venv.txt에 넣지 않는 약속)
: > "$WORK/extra-names.txt"
[ ! -f "$HERE/requirements/onnx-extra.txt" ] || names "$HERE/requirements/onnx-extra.txt" | grep -v '^$' | comm -23 - "$WORK/names-before.txt" > "$WORK/extra-names.txt" || :
: > "$WORK/freeze.txt"
while IFS= read -r line; do
  name="$(printf '%s\n' "$line" | sed 's/[=@ ].*//' | norm)"
  grep -qx "$name" "$WORK/extra-names.txt" || printf '%s\n' "$line" >> "$WORK/freeze.txt"
done < "$WORK/freeze-all.txt"
uv pip list --python "$VENV" --format json > "$WORK/list.json"
PREV_PIN="$HERE/pinned.json" "$PY_BIN" -I - "$TAG" "$TAG_OBJECT" "$COMMIT" "$PACKRT" "$FULL" "$WORK/uv.lock" "$SETUPTOOLS" "$WORK/pins.txt" "$WORK/freeze.txt" "$WORK/list.json" "$PY_VERSION" "$WORK" <<'PY'
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
tag, tag_object, commit, repo, full, lock, st, pins, freeze, lst, py, work = sys.argv[1:13]
ver = {d["name"].lower().replace("_", "-"): d["version"] for d in json.loads(Path(lst).read_text())}
tree = lambda p: subprocess.run(["git", "-C", repo, "rev-parse", f"{commit}:{p}"], capture_output=True, text=True, check=True).stdout.strip()
req = []
for line in Path(freeze).read_text().splitlines():
    name = re.split(r"[=@ ]", line, maxsplit=1)[0]
    req.append(f"{name}=={ver[name]}  # agent-pack-runtime {tag} {commit}" if name in ("packrt", "tracewriter") else line)
Path(work, "deploy-venv.txt").write_text("\n".join(req) + "\n")
prev = Path(os.environ.get("PREV_PIN", ""))   # 지난 기록의 저장소 주소는 이어받음(손으로 더한 칸이 다시 고정 때 빠지지 않게, 10-21)
url = {"url": u} if prev.is_file() and (u := json.loads(prev.read_text()).get("packrt_repo", {}).get("url")) else {}
fixed = dict(l.split(";")[0].strip().split("==", 1) for l in Path(pins).read_text().splitlines() if l.strip())
Path(work, "pinned.json").write_text(json.dumps({
    "packrt_repo": {"name": "agent-pack-runtime", **url, "tag": tag, "tag_object": tag_object, "commit": commit},
    "packrt": {"version": ver["packrt"], "tree": tree("packrt")},
    "tracewriter": {"version": ver["tracewriter"], "tree": tree("tracewriter")},
    "fixed_by_packrt": fixed,
    "third_party": {"llmops_commit": full, "uv_lock_sha256": hashlib.sha256(Path(lock).read_bytes()).hexdigest()},
    "python": py, "build_isolation": st or "setuptools(판 로그 없음)"}, indent=1) + "\n")
PY
names "$WORK/deploy-venv.txt" > "$WORK/names-after.txt"
NEW="$(comm -13 "$WORK/names-before.txt" "$WORK/names-after.txt")"
if [ -n "$NEW" ]; then
  echo "설치 뒤 새 패키지 종류: $(echo $NEW) — 기록은 바꾸지 않음. venv는 이미 바뀜: 옛 pinned.json 태그로 다시 고정(승인) 또는 DEPLOY_VENV 되돌림" >&2; exit 3
fi
mkdir -p "$OUT/requirements"
mv "$WORK/deploy-venv.txt" "$OUT/requirements/deploy-venv.txt"
mv "$WORK/pinned.json" "$OUT/pinned.json"
echo "설치 끝: packrt 저장소 $TAG ($COMMIT)"; cat "$OUT/pinned.json"
echo "새 패키지 종류: 없음"
