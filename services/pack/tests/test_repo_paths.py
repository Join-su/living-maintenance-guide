"""D-363 덧 · packrt-1 085 324: 저장소 글에 이 맥의 사용자 홈 절대 경로 0 — 공개 · 공유 전. 도구 · 약속 시험은 상대 경로 · 인자로.
금지 글 목록 자체('"/Users/"' 꼴 문자열)는 경로가 아니라 셈 밖."""
import re
import unittest
from pathlib import Path

from test_pack import ROOT

PATH = re.compile(r"/Users/(?!\")")   # 따옴표로 닫힌 '"/Users/"'(금지 글 목록)는 경로 아님
SKIP = {".git", ".venv", ".venv-kg", "status", "__pycache__", ".tmp", ".vibe-factory"}   # .venv-kg = kg-test 전용 가상환경(D-534 · git 밖)


class NoAbsolutePath(unittest.TestCase):
    def test_no_user_home_path_in_repo_text(self):
        hits = []
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if any(part in SKIP for part in rel.parts) or not p.is_file() or p.is_symlink() or p == Path(__file__).resolve():   # 이 시험 파일(꼴 정의)은 셈 밖
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1) if PATH.search(line)]
        self.assertEqual(hits, [], "이 맥 절대 경로가 남은 줄")


if __name__ == "__main__":
    unittest.main()
