"""약속 v4(contracts/v4 = 초안 r3 바이트, D-291 · D-310) 시험 — 초안 검사 31가지(check_v4_draft.py)를 그대로 돌림 · v3 예시가 v4 요청으로도 맞음."""
import subprocess
import sys
import unittest
from pathlib import Path

V4 = Path(__file__).resolve().parents[1] / "contracts/v4"


class DraftChecks(unittest.TestCase):
    def test_all_31_checks_pass(self):
        r = subprocess.run([sys.executable, "-B", str(V4 / "check_v4_draft.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(sum(1 for x in lines if x.startswith("통과")), 31, r.stdout)
        self.assertFalse([x for x in lines if x.startswith("실패")], r.stdout)
        self.assertEqual(lines[-1], "결과: 모두 통과")
