#!/usr/bin/env python3
"""정답 자료 접근 정적 검사(AC-15-C2 · leakage_check gate): 제품 코드가 평가 카드 · 원인 비율표를 가리키면 종료 코드 1."""

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[sys.argv.index("--root") + 1]) if "--root" in sys.argv else Path(__file__).resolve().parents[1]  # --root: 시험용 사본(ADV-09)
PRODUCT_DIRS = [ROOT / "server" / "src", ROOT / "apps", ROOT / "packages", ROOT / "infra", ROOT / "tools"]  # tools도 범위(EV-001 ES-03 — tools/ingest는 ES-18에서 지움)
FORBIDDEN = re.compile(r"eval/cards|cards\.jsonl|card_inputs|card_udi_map|cause_ratio_table|generation/")
SKIP = {"node_modules", "dist", "dist-types", "__pycache__"}

hits = []
for base in PRODUCT_DIRS:
    for path in base.rglob("*") if base.exists() else []:
        if path.is_file() and not any(p in SKIP for p in path.parts):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for m in FORBIDDEN.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}")
for h in hits:
    print("FORBIDDEN", h)
print(f"forbidden_paths_check: {len(hits)} hit(s)")
sys.exit(1 if hits else 0)
