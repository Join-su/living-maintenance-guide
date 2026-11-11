"""Folder / file choice through the macOS chooser (EV-003 CHG-17, CHG-18).

A web page cannot learn the real path of a folder the user picks, so the local server (127.0.0.1, same computer) opens the
macOS chooser with /usr/bin/osascript and returns the chosen path. It only reads: nothing is written anywhere.
The real chooser is started only by _run_osascript; tests replace it (tests/conftest.py) so no automated run opens a window.
Test harness only: LLMOPS_FAKE_PICK (read only inside scripts/isolated-env runs, see config.py) answers instead of the chooser."""
import json
import subprocess
import sys
import threading
from pathlib import Path

OSASCRIPT = "/usr/bin/osascript"
TIMEOUT_S = 300
SCRIPTS = {   # fixed text: no user value ever goes into the script
    "folder": 'POSIX path of (choose folder with prompt "release 폴더를 고르세요")',
    "file": 'POSIX path of (choose file with prompt "실행 설정 파일(yaml)을 고르세요")',
}
WORD = {"folder": "폴더", "file": "파일"}
MESSAGES = {
    "unsupported": "이 컴퓨터에서는 {w} 선택 창을 쓸 수 없습니다 — 경로를 직접 입력하세요",
    "timeout": "{w} 선택 창이 응답하지 않습니다 — 경로를 직접 입력하세요",
    "failed": "{w} 선택 창을 열 수 없습니다 — 경로를 직접 입력하세요",
    "busy": "{w} 선택 창이 이미 열려 있습니다",
}
_lock = threading.Lock()   # one chooser at a time


class PickError(Exception):
    def __init__(self, what, kind):
        self.kind = "pick-" + kind
        self.status = 409 if kind == "busy" else 422
        super().__init__(MESSAGES[kind].format(w=WORD[what]))

    def detail(self):
        return {"kind": self.kind, "message": str(self)}


def _supported():
    return sys.platform == "darwin" and Path(OSASCRIPT).exists()


def _run_osascript(script, timeout):
    return subprocess.run([OSASCRIPT, "-e", script], capture_output=True, text=True, timeout=timeout)


def _answer(what, value):
    if value is None:
        return {"cancelled": True}
    if isinstance(value, dict) and value.get("error") in MESSAGES:
        raise PickError(what, value["error"])
    if isinstance(value, str) and value.strip():
        return {"path": value.strip().rstrip("/") or "/"}
    raise PickError(what, "failed")


def pick(what, fake_file=""):
    """Return {"path": <absolute path>} or {"cancelled": True}. Raises PickError."""
    if fake_file:   # test harness answer, read on every call so a test can set the next answer
        try:
            value = json.loads(Path(fake_file).read_text(encoding="utf-8")).get(what)
        except (OSError, ValueError, AttributeError):
            raise PickError(what, "failed")
        return _answer(what, value)
    if not _supported():
        raise PickError(what, "unsupported")
    if not _lock.acquire(blocking=False):
        raise PickError(what, "busy")
    try:
        try:
            r = _run_osascript(SCRIPTS[what], TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise PickError(what, "timeout")
        except OSError:
            raise PickError(what, "failed")
        if r.returncode != 0:
            if "-128" in (r.stderr or ""):   # "User canceled. (-128)"
                return {"cancelled": True}
            raise PickError(what, "failed")
        return _answer(what, r.stdout)
    finally:
        _lock.release()
