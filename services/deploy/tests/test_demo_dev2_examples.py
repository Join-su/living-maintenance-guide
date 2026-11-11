"""시연 첫 상태 그래프 이력에서 사전 점검 예시 줄을 패턴으로 고름(scripts/demo_dev2.py examples) — 다음 첫 상태는 패턴 A(흡기 필터 교체 → 덮개)만.
B(공구 교체) · C(엔코더) 예시 줄은 넣지 않음(지움 0 · 새 파일). 예시 아닌 줄은 그대로 · 차례 그대로."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def row(rid, eq, code, example=True):
    r = {"record_id": rid, "equipment_id": eq, "occurred_at": "2026-09-08T10:00:00+09:00", "symptom_group": {"code": code, "name": "x"}}
    return {**r, "provenance": "사전 점검 예시"} if example else r


ROWS = [row("H-0300", "M-02", "CL", False), row("H-P001", "M-05", "CL"), row("H-P002", "M-05", "CL"), row("H-P008", "M-09", "TW"),
        row("H-0301", "M-01", "CL", False), row("H-P017", "M-15", "PW"), row("H-P007", "M-04", "CL"), row("H-P016", "M-04", "TW")]


def run(tmp_path, rows, *extra):
    src, out = tmp_path / "h.jsonl", tmp_path / "out.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "demo_dev2.py"), "examples", "--in", str(src), "--out", str(out), *extra],
                       capture_output=True, text=True)
    return r, src, out


def ids(p):
    return [json.loads(l)["record_id"] for l in p.read_text(encoding="utf-8").splitlines()]


def test_default_keeps_pattern_a_only_and_other_rows_in_order(tmp_path):
    r, src, out = run(tmp_path, ROWS)
    assert r.returncode == 0, r.stderr
    assert ids(out) == ["H-0300", "H-P001", "H-P002", "H-0301", "H-P007"]
    s = json.loads(r.stdout.strip().splitlines()[-1])
    assert s["kept"] == {"A": 3} and s["left_out"] == {"B": 2, "C": 1} and s["other"] == 2
    assert ids(src) == [x["record_id"] for x in ROWS]                    # 원래 파일 그대로(지움 0)


def test_keep_all_three_is_the_same_file(tmp_path):
    r, src, out = run(tmp_path, ROWS, "--keep", "A,B,C")
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_already_a_only_file_passes_unchanged(tmp_path):
    rows = [x for x in ROWS if x["symptom_group"]["code"] == "CL" or "provenance" not in x]
    r, src, out = run(tmp_path, rows)
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_kept_pattern_with_no_rows_stops(tmp_path):
    r, _, out = run(tmp_path, [x for x in ROWS if x["symptom_group"]["code"] != "CL" or "provenance" not in x])
    assert r.returncode == 2 and "A" in r.stdout and not out.exists()


def test_unknown_example_group_stops(tmp_path):
    r, _, out = run(tmp_path, ROWS + [row("H-P099", "M-20", "XX")])
    assert r.returncode == 2 and "XX" in r.stdout and not out.exists()


def test_out_must_differ_from_in(tmp_path):
    src = tmp_path / "h.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "demo_dev2.py"), "examples", "--in", str(src), "--out", str(src)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and ids(src) == [x["record_id"] for x in ROWS]


def test_app_lines_replace_the_same_numbers_with_app_valid_time(tmp_path):
    new = {**row("H-0301", "M-01", "CL", False), "occurred_at": "2026-11-05T08:51:22+00:00", "provenance": "시연 이력 줄"}
    (tmp_path / "line-M-01.jsonl").write_text(json.dumps(new, ensure_ascii=False) + "\n", encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--line", str(tmp_path / "line-M-01.jsonl"))
    assert r.returncode == 0, r.stderr
    got = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [g["record_id"] for g in got] == ["H-0300", "H-P001", "H-P002", "H-0301", "H-P007"]   # 자리 그대로
    assert got[3] == new and json.loads(r.stdout.strip().splitlines()[-1])["lines_from_app"] == {"H-0301": "2026-11-05T08:51:22+00:00"}


def test_app_line_file_new_number_is_appended_known_one_replaced(tmp_path):
    known = {**row("H-0301", "M-01", "CL", False), "occurred_at": "2026-11-05T21:26:26+09:00"}
    extra = {**row("H-0999", "M-13", "CL", False), "occurred_at": "2026-10-19T21:26:26+09:00"}   # 앱이 이번 첫 상태에 더한 새 번호
    (tmp_path / "l.jsonl").write_text(json.dumps(known) + "\n" + json.dumps(extra) + "\n", encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--line", str(tmp_path / "l.jsonl"))
    assert r.returncode == 0, r.stderr
    got = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [g["record_id"] for g in got] == ["H-0300", "H-P001", "H-P002", "H-0301", "H-P007", "H-0999"]   # 있는 번호 자리 그대로 · 새 번호 끝에
    assert got[3] == known and got[-1] == extra
    s = json.loads(r.stdout.strip().splitlines()[-1])
    assert s["lines_from_app"] == {"H-0301": known["occurred_at"], "H-0999": extra["occurred_at"]} and s["appended"] == ["H-0999"]


def test_new_example_number_still_stops(tmp_path):
    (tmp_path / "ex.jsonl").write_text(json.dumps(row("H-P099", "M-20", "CL")) + "\n", encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--app-examples", str(tmp_path / "ex.jsonl"))
    assert r.returncode == 2 and "H-P099" in r.stdout and not out.exists()


def test_same_number_twice_in_app_files_stops(tmp_path):
    a = {**row("H-0301", "M-01", "CL", False), "occurred_at": "2026-11-05T21:26:26+09:00"}
    (tmp_path / "l1.jsonl").write_text(json.dumps(a) + "\n", encoding="utf-8")
    (tmp_path / "l2.jsonl").write_text(json.dumps({**a, "occurred_at": "2026-11-04T00:00:00+09:00"}) + "\n", encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--line", str(tmp_path / "l1.jsonl"), "--line", str(tmp_path / "l2.jsonl"))
    assert r.returncode == 2 and "H-0301" in r.stdout and not out.exists()


def test_app_examples_file_replaces_example_rows_with_app_times(tmp_path):
    app = [{**row("H-P001", "M-05", "CL"), "occurred_at": "2026-10-10T10:00:00+09:00"},
           {**row("H-P007", "M-04", "CL"), "occurred_at": "2026-11-06T10:00:00+09:00"},
           {**row("H-P008", "M-09", "TW"), "occurred_at": "2026-10-09T10:00:00+09:00"}]   # 앱 파일에 B 줄이 있어도 --keep으로 뺌
    (tmp_path / "ex.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in app), encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--app-examples", str(tmp_path / "ex.jsonl"))
    assert r.returncode == 0, r.stderr
    got = {g["record_id"]: g for g in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert list(got) == ["H-0300", "H-P001", "H-P002", "H-0301", "H-P007"]
    assert got["H-P001"] == app[0] and got["H-P007"] == app[1] and got["H-P002"]["occurred_at"] == "2026-09-08T10:00:00+09:00"
    assert json.loads(r.stdout.strip().splitlines()[-1])["examples_from_app"] == 3


def test_app_examples_with_unknown_number_stops(tmp_path):
    (tmp_path / "ex.jsonl").write_text(json.dumps(row("H-P099", "M-20", "CL")) + "\n", encoding="utf-8")
    r, _, out = run(tmp_path, ROWS, "--app-examples", str(tmp_path / "ex.jsonl"))
    assert r.returncode == 2 and "H-P099" in r.stdout and not out.exists()


# D-656: 펌웨어 패턴 F = 예시 파일(new-pack · F만)에 든 번호만 남김. 증상 코드로 가르지 않음(F의 이어짐 줄도 CL이라 A와 겹침) · 바탕에 없는 새 번호는 덧붙임.
def write_ex(tmp_path, rows):
    (tmp_path / "ex.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return str(tmp_path / "ex.jsonl")


def test_keep_f_keeps_only_example_file_numbers_and_appends_new_ones(tmp_path):
    app = [{**row("H-P001", "M-05", "CL"), "occurred_at": "2026-10-01T10:00:00+09:00"}, row("H-P026", "M-02", "PW"), row("H-P027", "M-04", "CL")]
    r, src, out = run(tmp_path, ROWS, "--keep", "F", "--app-examples", write_ex(tmp_path, app))
    assert r.returncode == 0, r.stderr
    got = {g["record_id"]: g for g in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert list(got) == ["H-0300", "H-P001", "H-0301", "H-P026", "H-P027"]   # 바탕 예시 중 파일 밖 번호(A · B · C) 뺌 · 새 번호는 끝에
    assert got["H-P001"] == app[0]
    s = json.loads(r.stdout.strip().splitlines()[-1])
    assert s["kept"] == {"F": 3} and s["left_out"] == {"A": 2, "B": 2, "C": 1} and s["appended"] == ["H-P026", "H-P027"]
    assert ids(src) == [x["record_id"] for x in ROWS]


def test_keep_f_needs_example_file(tmp_path):
    r, _, out = run(tmp_path, ROWS, "--keep", "F")
    assert r.returncode != 0 and "F" in (r.stdout + r.stderr) and not out.exists()


def test_keep_f_cannot_mix_with_group_patterns(tmp_path):
    r, _, out = run(tmp_path, ROWS, "--keep", "A,F", "--app-examples", write_ex(tmp_path, [row("H-P026", "M-02", "PW")]))
    assert r.returncode != 0 and not out.exists()


def test_keep_f_example_file_row_that_is_not_an_example_stops(tmp_path):
    r, _, out = run(tmp_path, ROWS, "--keep", "F", "--app-examples", write_ex(tmp_path, [row("H-0399", "M-02", "CL", example=False)]))
    assert r.returncode == 2 and "H-0399" in r.stdout and not out.exists()
