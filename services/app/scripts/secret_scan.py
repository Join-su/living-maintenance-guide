#!/usr/bin/env python3
"""비밀값 스캔(AC-36-C1 · secret_scan gate): 키 · 비밀번호 원문으로 보이는 문자열을 찾으면 종료 코드 1."""

import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "dist-types", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
SKIP_SUFFIXES = {".png", ".pdf", ".jpg", ".jpeg", ".svg", ".lock", ".pyc"}
PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)openai_api_key\s*[=:]\s*['\"]?[^\s'\"]+"),
    re.compile(r"(?i)\b(password|passwd|secret)\b\s*[=:]\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)^(C1A15_[A-Z_]*PASSWORD|POSTGRES_PASSWORD)[ \t]*=[ \t]*\S+", re.M),
]


def scan(root: Path) -> list[tuple[str, int, str]]:
    findings = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.relative_to(root).parts) or path.suffix in SKIP_SUFFIXES:
            continue
        if path.name == "package-lock.json":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                findings.append((str(path), text.count("\n", 0, match.start()) + 1, pattern.pattern))
    return findings


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    found = scan(root)
    for path, line, pattern in found:
        print(f"SECRET? {path}:{line} ({pattern})")
    print(f"secret_scan: {len(found)} finding(s)")
    sys.exit(1 if found else 0)
