#!/usr/bin/env bash
# MG-MM 3.0(관계없음) PDF 만들기 — 앱 저장소 scripts/manual_pdf/make_pdf.sh(MG-MM 2.0 · EV-005) 복사본을 고침(D-535).
# HTML → PDF → 차례 쪽 번호 채워 다시 → 표지 + 본문 합침 → kg-test/manual-v3/MG-MM-3.0-관계없음.pdf
# 사용: kg-test/manual-v3/pdf/make_pdf.sh <작업 폴더(절대 경로)> — 설치 0 · 밖 요청 0. Playwright는 앱 저장소 node_modules 것을 읽기만(PW_NODE_MODULES 필수).
set -euo pipefail
H=$(cd "$(dirname "$0")" && pwd)
OUTPDF="$H/../MG-MM-3.0-관계없음.pdf"
W=${1:?작업 폴더}
NM=${PW_NODE_MODULES:?앱 저장소 node_modules 경로}
mkdir -p "$W/html"
python3 -I "$H/build.py" "$W/html"
NODE_PATH="$NM" node "$H/print.cjs" "$W/html" "$W"
python3 -I "$H/build.py" "$W/html" --pages-from "$W/body.pdf"
NODE_PATH="$NM" node "$H/print.cjs" "$W/html" "$W"
pdfunite "$W/cover.pdf" "$W/body.pdf" "$OUTPDF"
echo "pdf $(basename "$OUTPDF") $(pdfinfo "$OUTPDF" | awk '/^Pages/{print $2}')쪽 · 막은 요청 $(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['blocked'])" "$W/print.json")"
