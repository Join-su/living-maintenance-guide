"""EV-011 (D-345, E3-A1 B1): no test sees the PACKRT_ENV_FILE of the shell it is run from. packrt.index build and the runner read
the file that variable names, so a test (or a subprocess that copies os.environ) would otherwise open a real Pack .env. A test that
needs the variable sets it itself, to a file under tmp_path."""
import pytest


@pytest.fixture(autouse=True)
def _no_outside_env_file(monkeypatch):
    monkeypatch.delenv("PACKRT_ENV_FILE", raising=False)
