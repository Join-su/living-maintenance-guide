#!/usr/bin/env python3
"""외부 호출 경로 정적 검사(external_call_zero_in_fixture gate의 정적 부분).

제품 코드(server/src · apps · packages)에 외부 HTTP 클라이언트 · 외부 주소 · LLM SDK가 있으면 종료 코드 1.
live 호출은 승인 뒤 LLM Adapter(B-07) 한 곳에만 둘 수 있으며, 그때 이 검사에 허용 위치를 명시해 더한다.
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = [ROOT / "server" / "src", ROOT / "apps", ROOT / "packages", ROOT / "tools"]  # tools도 범위(EV-001 ES-03 — tools/ingest는 ES-18에서 지움)
SKIP = {"node_modules", "dist", "dist-types", "__pycache__"}
CODE_PATTERNS = re.compile(
    r"\bimport (httpx|requests|urllib3|aiohttp|openai)\b|\bfrom (httpx|requests|urllib3|urllib|aiohttp|openai)\b|urllib\.request"
    # S02-G6-2: 표준 라이브러리 저수준 연결 · 브라우저 직접 연결도 외부 호출 경로로 본다
    r"|\bimport (http\.client|socket)\b|\bfrom (http\.client|socket) import\b|\bhttp\.client\b|\bsocket\.(socket|create_connection)\b"
    r"|\bnew (WebSocket|EventSource)\(|\bXMLHttpRequest\b|\bnavigator\.sendBeacon\("
    r"|https?://(?!127\.0\.0\.1|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
LLM_PACKAGES = {"openai", "anthropic", "langchain", "langchain-openai", "@openai/api", "openai-node"}
# 승인된 예외(사용자 승인 2026-10-13, execution/S-02/separate-approvals.md): live LLM 경로 한 파일만 openai를 쓴다.
# 시연 알람 발생기(P-07)는 같은 호스트 · compose 내부 API만 부른다(주소 제한은 그 파일의 check_url · 시험).
# 배포 서버 클라이언트(EV-001 CH-15 · CH-38, D-274)는 배포 서버 POST /runs 한 곳만 부른다 — 맥 host = 127.0.0.1,
# 컨테이너 = C1A15_DEPLOY_HOST 내부망 이름 하나(주소 제한은 그 파일의 check_url · check_host_name · 시험).
ALLOWED = {
    "server/src/c1a15/llm/live.py": {"openai"},
    "server/src/c1a15/alarms/__main__.py": {"urllib"},
    "server/src/c1a15/deploy/client.py": {"urllib"},
}
ALLOWED_PY_DEPS = {"openai"}  # 위 파일에서만 import(아래 검사로 확인)

hits = []
for base in CODE_DIRS:
    for path in base.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".js", ".mjs", ".html"} and not any(p in SKIP for p in path.parts):
            text = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(ROOT))
            for m in CODE_PATTERNS.finditer(text):
                module = next((g for g in m.groups() if g), None) or ("urllib" if m.group(0) == "urllib.request" else None)
                if module and module in ALLOWED.get(rel, ()):  # 허용은 모듈 이름 정확 비교(urllib3 ≠ urllib)
                    continue
                hits.append(f"code {rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}")
py_deps = tomllib.loads((ROOT / "server" / "pyproject.toml").read_text())["project"]["dependencies"]
for dep in py_deps:
    name = re.split(r"[<>=\[ ]", dep, maxsplit=1)[0].lower()
    if name in LLM_PACKAGES and name not in ALLOWED_PY_DEPS:
        hits.append(f"python runtime dependency: {dep}")
lock = json.loads((ROOT / "package-lock.json").read_text())
for name in lock.get("packages", {}):
    if name.split("node_modules/")[-1] in LLM_PACKAGES:
        hits.append(f"npm package: {name}")
for h in hits:
    print("EXTERNAL", h)
print(f"external_call_check: {len(hits)} hit(s)")
sys.exit(1 if hits else 0)
