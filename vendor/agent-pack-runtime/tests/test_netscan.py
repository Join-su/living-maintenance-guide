"""The static network scan (scripts/static_net_scan.py) finds no violation in this repository."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_static_net_scan_is_clean():
    out = subprocess.run([sys.executable, str(ROOT / "scripts/static_net_scan.py"), "--root", str(ROOT)], capture_output=True, text=True, cwd=ROOT / "tests")
    res = json.loads(out.stdout)
    assert out.returncode == 0 and res["ok"] and res["violations"] == [] and res["files_scanned"] >= 20, res
