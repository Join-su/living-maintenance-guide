"""S-01 결함 주입(ADV-01 · ADV-02 · ADV-03 중 메모리 · 임시 파일 부분). 대상은 tmp_path · 메모리 객체뿐이다."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from c1a15.checks.passwords import is_argon2id_hash_of
from c1a15.checks.separation import overlap

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("secret_scan", REPO / "scripts" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secret_scan)


def test_adv01_secret_scan_fails_closed(tmp_path):
    """ADV-01: 가짜 키 · 비밀번호를 넣으면 스캔이 파일 · 행을 보고하고 종료 코드 1로 실패한다."""
    (tmp_path / "clean.py").write_text("PASSWORD_ENV = 'C1A15_TECH_PASSWORD'\n")
    assert secret_scan.scan(tmp_path) == []
    # 가짜 비밀값은 실행 중에만 조립한다(저장소 파일에는 탐지 패턴이 남지 않게).
    fake_key = "s" + "k-" + "test-FAKE" * 3
    (tmp_path / "leak.env").write_text("OPENAI" + "_API_KEY=" + fake_key + "\n")
    (tmp_path / "leak.py").write_text("pass" + "word = '" + "hunter2-plain" + "'\n")
    found = secret_scan.scan(tmp_path)
    assert {Path(f[0]).name for f in found} == {"leak.env", "leak.py"}
    assert all(f[1] == 1 for f in found)  # 행 번호 보고
    run = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "secret_scan.py"), str(tmp_path)], capture_output=True, text=True
    )
    assert run.returncode == 1 and "leak.env:1" in run.stdout and "leak.py:1" in run.stdout


def test_adv02_separation_detects_injected_overlap():
    """ADV-02: 이력 사본(메모리)에 평가 카드 원본 행 하나와 각본 행 하나를 끼워 넣으면 분리 검사가 둘 다 보고한다."""
    history_rows = {"udi:7013", "udi:7014"}
    fake_eval_rows = {"udi:9001", "udi:9002"}  # 실제 eval/cards를 열지 않은 가짜 목록
    fake_script_rows = {"udi:9501", "udi:9502"}  # 가짜 시연 각본 행
    assert overlap(history_rows, fake_eval_rows) == set() and overlap(history_rows, fake_script_rows) == set()
    tainted = history_rows | {"udi:9001", "udi:9501"}
    assert overlap(tainted, fake_eval_rows) == {"udi:9001"}  # 평가 정답 누수(AC-15)
    assert overlap(tainted, fake_script_rows) == {"udi:9501"}  # 각본 행 겹침(AC-34-C3)


def test_adv03_plaintext_storage_is_rejected():
    assert not is_argon2id_hash_of("plain-pass", "plain-pass")
    assert not is_argon2id_hash_of("$argon2i$v=19$m=8,t=1,p=1$c29tZXNhbHQ$abc", "plain-pass")
