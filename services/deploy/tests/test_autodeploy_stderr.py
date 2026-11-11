"""감시가 실패한 도구의 stderr 끝 줄을 로그에 남김(D-687) — capture 뒤 버려져 V60 ② 실패 원인을 못 찾았음. 키 · DSN 비밀번호 모양은 가림."""
import types

import pytest

from test_autodeploy import A  # noqa: F401 - fixture A

KEY = "sk-" + "a1B2" * 8
ERR = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom " + KEY + " postgres://u:hunter2@db/x"


@pytest.fixture
def said(A, monkeypatch):  # noqa: F811
    out = []
    monkeypatch.setattr(A, "say", lambda *a: out.append(" ".join(map(str, a))))
    return out


def failing(A, monkeypatch, err=ERR):  # noqa: F811
    monkeypatch.setattr(A, "sh", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=err))


def test_pack_tool_failure_logs_stderr_tail_masked(A, said, monkeypatch, tmp_path):  # noqa: F811
    failing(A, monkeypatch)
    rc, j = A.pack_tool(tmp_path / "CHG.json", apply=False)
    text = "\n".join(said)
    assert rc == 1 and j == {} and "ValueError: boom" in text
    assert KEY not in text and "hunter2" not in text and "<가림>" in text


def test_kg_apply_failure_logs_stderr_tail(A, said, monkeypatch):  # noqa: F811
    failing(A, monkeypatch)
    assert A.kg_apply_once(["RPT-1"]) == 1
    assert any("ValueError: boom" in s for s in said)


def test_kg_carry_failure_logs_stderr_tail(A, said, monkeypatch):  # noqa: F811
    failing(A, monkeypatch)
    assert A.kg_carry("g1") == 1
    assert any("ValueError: boom" in s for s in said)


def test_only_last_lines_and_cut_long(A, said, monkeypatch, tmp_path):  # noqa: F811
    failing(A, monkeypatch, "\n".join(f"줄{i}" for i in range(10)) + "\n" + "x" * 900)
    A.pack_tool(tmp_path / "CHG.json", apply=False)
    lines = [s for s in said if "stderr:" in s]
    assert len(lines) == 3 and "줄7" not in " ".join(lines) and all(len(s) < 340 for s in lines)


def test_success_logs_no_stderr(A, said, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(A, "sh", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="{}", stderr="경고 한 줄"))
    A.pack_tool(tmp_path / "CHG.json", apply=False)
    assert not any("stderr:" in s for s in said)
