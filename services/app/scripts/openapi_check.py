#!/usr/bin/env python3
"""OpenAPI 정본 검사(P-06 verifier '재생성 diff 없음'): FastAPI가 만든 스키마와 server/openapi.json이 같아야 한다.

같지 않으면 diff 일부를 보이고 종료 코드 1. 정본 갱신: python3 scripts/openapi_check.py --write
앱 화면의 TypeScript 타입은 이 파일에서 만든다(openapi-typescript, 앱 화면 item에서).
"""

import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "server" / "openapi.json"
DUMP = "import json; from c1a15.api.main import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=1, sort_keys=True))"

# 앱은 import 때 배포 서버 주소를 확인함(EV-001 CH-30 — deploy만) — 스키마만 뽑으므로 주소는 확인용 자리 값(부르지 않음)
ENV = {"C1A15_DEPLOY_URL": "http://127.0.0.1:8796", **__import__("os").environ, "PYTHONPATH": "src"}
out = subprocess.run(["uv", "run", "--frozen", "python", "-c", DUMP], cwd=ROOT / "server", env=ENV,
                     check=True, capture_output=True, text=True).stdout
if "--write" in sys.argv:
    SNAPSHOT.write_text(out, encoding="utf-8")
    print(f"openapi_check: wrote {SNAPSHOT.relative_to(ROOT)}")
    sys.exit(0)
old = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
if old == out:
    print(f"openapi_check: pass ({len(json.loads(out)['paths'])} paths)")
    sys.exit(0)
sys.stdout.writelines(list(difflib.unified_diff(old.splitlines(True), out.splitlines(True), "server/openapi.json", "generated"))[:60])
print("openapi_check: FAIL — 재생성 결과가 정본과 다름")
sys.exit(1)
