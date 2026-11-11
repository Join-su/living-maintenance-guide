#!/usr/bin/env python3
"""가이드 예시 값 하드코딩 검사(AC-17-C2 · S-06 P-17): Prototype template(packet 부록)에 보이는 데이터 모양 값이 앱 소스에 박혀 있으면 실패.

데이터 모양 값 = 기록 번호(H-0000 · R-0000) · 설비 번호(M-00) · 시각(00:00) · 단위 붙은 측정값(8.3 K · 1,511 rpm · 52 °C · 40분 · 83%)
· 가린 사람 이름(김○○). 화면 이름표 · 버튼 글자는 데이터가 아니라 세지 않는다.
앱 소스 = apps/*/src · packages/ui/src(시험 · fixture · 생성 타입 제외).
사용: python3 scripts/example_value_check.py <부록 templates 폴더>  (찾으면 종료 코드 1)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(
    r"\b[HR]-\d{4}\b|\bM-\d{2}\b|\b\d{1,2}:\d{2}\b|\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:K|°C|rpm|Nm|분|%)(?![\w])|[가-힣]○○"
)
SRC = [ROOT / "apps" / "tech" / "src", ROOT / "apps" / "lead" / "src", ROOT / "packages" / "ui" / "src"]
SKIP = {"__tests__", "fixtures", "api"}  # 시험 · fixture · 생성 타입(schema.d.ts)


def visible_text(html: str) -> str:
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"(^|[^:])//[^\n]*", r"\1", src)


def main() -> int:
    tpl = Path(sys.argv[1])
    tokens: dict[str, set[str]] = {}
    for f in sorted(tpl.glob("*.html")):
        for m in TOKEN.finditer(visible_text(f.read_text(encoding="utf-8"))):
            tokens.setdefault(m.group(0).strip(), set()).add(f.stem)
    hits = []
    for base in SRC:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".tsx", ".css"} or any(p in SKIP for p in path.parts):
                continue
            text = strip_comments(path.read_text(encoding="utf-8"))  # 설명 주석 속 형식 예시는 화면 값이 아님
            for tok in tokens:
                if re.search(r"(?<![\w.,])" + re.escape(tok) + r"(?![\w])", text):
                    hits.append(f"{path.relative_to(ROOT)}: '{tok}' (template {', '.join(sorted(tokens[tok])[:3])})")
    print(f"example_value_check: template 예시 값 {len(tokens)}종, 앱 소스에서 찾은 것 {len(hits)}건")
    for h in hits:
        print("HARDCODED", h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
