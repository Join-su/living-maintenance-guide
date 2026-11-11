#!/usr/bin/env bash
# MG-MM 2.0 PDF 만들기(EV-005): HTML → PDF → 차례 쪽 번호 채워 다시 → 표지 + 본문 합침 → data/manuals/MG-MM-2.0.pdf
# 사용: scripts/manual_pdf/make_pdf.sh <작업 폴더(절대 경로)> — 설치 0 · 밖 요청 0. Playwright는 저장소 node_modules 것을 읽기만(다른 곳이면 PW_NODE_MODULES).
set -euo pipefail
H=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$H/../.." && pwd)
W=${1:?작업 폴더}
NM=${PW_NODE_MODULES:-$ROOT/node_modules}
mkdir -p "$W/html"
python3 -I "$H/build.py" "$ROOT" "$W/html"
NODE_PATH="$NM" node "$H/print.cjs" "$W/html" "$W"
python3 -I "$H/build.py" "$ROOT" "$W/html" --pages-from "$W/body.pdf"
NODE_PATH="$NM" node "$H/print.cjs" "$W/html" "$W"
pdfunite "$W/cover.pdf" "$W/body.pdf" "$ROOT/data/manuals/MG-MM-2.0.pdf"
echo "pdf $ROOT/data/manuals/MG-MM-2.0.pdf $(pdfinfo "$ROOT/data/manuals/MG-MM-2.0.pdf" | awk '/^Pages/{print $2}')쪽"
