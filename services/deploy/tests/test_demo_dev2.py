"""D-570 시연 이력 한 줄(scripts/demo_dev2.py history) — 처음 이력 그대로 + 시연 줄 · 시각 · 설비만 바뀜 · 다른 설비 최근 정비가 있으면 멈춤."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rec(rid, eq, at, section="4.3.2", kind="action"):
    return {"record_id": rid, "equipment_id": eq, "occurred_at": at, "closed_at": at, "steps": [{"at": at, "kind": kind, "section": section}],
            "approval": {"status": "승인", "at": at}, "narrative": "x"}


def run(tmp_path, recs, *extra):
    base, out = tmp_path / "h.jsonl", tmp_path / "demo.jsonl"
    base.write_text("".join(json.dumps(r) + "\n" for r in recs))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "demo_dev2.py"), "history", "--base", str(base), "--out", str(out),
                        "--at", "2026-11-11T10:00:00+09:00", *extra], capture_output=True, text=True)
    return r, out


def test_adds_one_demo_line_three_days_before(tmp_path):
    r, out = run(tmp_path, [rec("H-0001", "M-05", "2026-09-30T04:49:00+09:00"), rec("H-0002", "M-02", "2026-10-01T00:00:00+09:00", "4.3")])
    assert r.returncode == 0, r.stderr
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(lines) == 3 and lines[:2] == [json.loads(l) for l in (tmp_path / "h.jsonl").read_text().splitlines()]
    d = lines[-1]
    assert d["record_id"] == "H-D570" and d["equipment_id"] == "M-01" and d["occurred_at"].startswith("2026-11-08T10:00")
    assert d["steps"][0]["section"] == "4.3.2" and d["steps"][0]["at"] == d["occurred_at"] and d["provenance"] == "demo_d570"


def test_other_equipment_recent_work_stops(tmp_path):
    r, out = run(tmp_path, [rec("H-0001", "M-05", "2026-09-30T04:49:00+09:00"), rec("H-0009", "M-02", "2026-11-07T09:00:00+09:00")])
    assert r.returncode == 2 and json.loads(r.stdout)["equipment"] == ["M-02"] and not out.exists()


def test_existing_record_id_stops(tmp_path):
    r, _ = run(tmp_path, [rec("H-D570", "M-05", "2026-09-30T04:49:00+09:00")])
    assert r.returncode != 0 and "이미 있음" in r.stderr


def test_at_now_means_three_days_before_now(tmp_path):   # D-570 덧 3: 시연 그때 기준 — 넣는 순간 '지금부터 3일 전'
    from datetime import datetime, timedelta
    base, out = tmp_path / "h.jsonl", tmp_path / "demo.jsonl"
    base.write_text(json.dumps(rec("H-0001", "M-05", "2026-01-30T04:49:00+09:00")) + "\n")
    t0 = datetime.now().astimezone()
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "demo_dev2.py"), "history", "--base", str(base), "--out", str(out), "--at", "now"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = datetime.fromisoformat(json.loads(out.read_text().splitlines()[-1])["occurred_at"])
    assert abs((t0 - timedelta(days=3)) - got) < timedelta(minutes=2)


def test_line_from_app_is_appended_as_is(tmp_path):   # 앱 demo_history add가 찍은 줄 그대로(앱 이력 표 · Pack records 같은 번호)
    base, out, line = tmp_path / "h.jsonl", tmp_path / "demo.jsonl", tmp_path / "app-line.json"
    base.write_text(json.dumps(rec("H-0001", "M-05", "2026-01-30T04:49:00+09:00")) + "\n")
    app = {**rec("H-0301", "M-01", "2026-11-08T10:00:00+09:00"), "demo": "D-570"}
    line.write_text(json.dumps(app, ensure_ascii=False) + "\n")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "demo_dev2.py"), "history", "--base", str(base), "--out", str(out),
                        "--at", "2026-11-11T10:00:00+09:00", "--line", str(line)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text().splitlines()[-1]) == app
