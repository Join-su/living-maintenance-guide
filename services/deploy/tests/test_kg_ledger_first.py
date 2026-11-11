"""[dev-2] D-617: 그래프 전환 틈 — 서버는 장부에 없는 revision의 Run을 거부하고, LLMOps는 applied 신호를 보고 30초마다 줄을 씀.
KG_LEDGER_WAIT를 주면 신호 → 줄 기다림 → 바꿔 띄움 차례(틈 0). 안 주면 지금 차례 그대로."""
import pytest

from test_kg_apply import REV2, K, letter  # noqa: F401 - fixture K


def order(K, monkeypatch, rows):  # noqa: F811
    """신호 · 줄 읽기 · 바꿔 띄움 차례를 적음. rows = 차례마다 ledger_row가 낼 값."""
    seen, it = [], iter(rows)
    monkeypatch.setattr(K, "signal", lambda new, status, **kw: seen.append(f"signal {status}"))
    monkeypatch.setattr(K.S, "rev", lambda tag: ("c", REV2, "L2"))
    monkeypatch.setattr(K.A, "ledger_row", lambda rev: seen.append(f"row {rev == REV2}") or next(it, None))
    monkeypatch.setattr(K.S, "swap", lambda tag: seen.append(f"swap {tag}") or REV2)
    monkeypatch.setattr(K.time, "sleep", lambda s: None)
    return seen


def test_off_keeps_todays_order(K, monkeypatch):  # noqa: F811
    monkeypatch.delenv("KG_LEDGER_WAIT", raising=False)
    seen = order(K, monkeypatch, [])
    letter(K)
    assert K.main() == "g2"
    assert seen == ["swap g2", "signal applied"]


def test_row_comes_before_swap(K, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KG_LEDGER_WAIT", "60")
    seen = order(K, monkeypatch, [None, None, ("g2", "drs:x")])
    letter(K)
    assert K.main() == "g2"
    assert seen == ["signal applied", "row True", "row True", "row True", "swap g2"]   # applied 신호는 한 번


def test_no_row_in_time_swaps_as_today_and_says_so(K, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KG_LEDGER_WAIT", "60")
    seen = order(K, monkeypatch, [])
    clock = iter(range(0, 1000, 30))
    monkeypatch.setattr(K.time, "time", lambda: next(clock))
    letter(K)
    assert K.main() == "g2"
    assert seen[0] == "signal applied" and seen[-1] == "swap g2" and seen.count("signal applied") == 1
    assert "장부 줄 없음" in (K.STATE / "kg-apply.log").read_text()


def test_swap_failure_after_early_signal_says_failed(K, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KG_LEDGER_WAIT", "60")
    seen = order(K, monkeypatch, [("g2", "drs:x")])

    def boom(tag):
        raise K.S.SwapError("서버가 뜨지 않음")

    monkeypatch.setattr(K.S, "swap", boom)
    letter(K)
    with pytest.raises(SystemExit):
        K.main()
    assert seen == ["signal applied", "row True", "signal failed"]   # 신호 파일은 마지막 상태 한 장 — failed가 덮음
