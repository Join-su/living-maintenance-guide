"""버전 전환 뒤 배운 예외 그래프 옮겨 싣기(D-588 C) — 감시가 새 Pack 버전으로 바꿔 끼우면 그 버전의 그래프 파일은 Pack 본줄 것(시연에서 배운 예외 0)이라,
kg_apply --carry-from <전 태그>가 전 태그의 그래프 파일을 새 버전 위에 한 파일만 바꾼 커밋(새 g 태그)으로 실어 다시 띄움 · 같으면 손 0 · 신호 한 줄."""
import json
import subprocess

import pytest

from test_kg_apply import K  # noqa: F401 - fixture K

LEARNED = json.dumps({"layer": "L-learned-ex1", "exception_relations": [{"id": "EX-R-0801"}]})
BASE = json.dumps({"layer": "L1", "exception_relations": []})


@pytest.fixture
def C(K, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("KG_SIGNAL_DIR", str(tmp_path / "signal"))
    K.graphs = {"g8": LEARNED, "g1": BASE}
    def git(*a):
        if a and a[0] == "show":
            t = a[1].split(":")[0]
            if t not in K.graphs:
                raise subprocess.CalledProcessError(128, "git show")
            return K.graphs[t]
        return ""
    monkeypatch.setattr(K, "git", git)
    K.STATE.mkdir(parents=True, exist_ok=True)
    return K


def test_carries_learned_graph_onto_new_version(C):
    assert C.carry_from("g8") == "g2"
    assert C.calls["new_revision"] == 1 and C.calls["swap"] == ["g2"]
    assert json.loads((C.STATE / "current.json").read_text())["layer"] == "L-learned-ex1"
    s = json.loads((C.STATE.parent / "signal" / "carry-g1.json").read_text())
    assert s["status"] == "applied" and s["reason"] == "carry_on_version_switch" and s["graph"] == {"running_tag": "g1", "new_tag": "g2"} and s["carried_from"] == "g8"


def test_same_graph_does_nothing(C):
    C.graphs["g8"] = BASE
    assert C.carry_from("g8") == "same"
    assert C.calls["swap"] == [] and C.calls["new_revision"] == 0


def test_missing_previous_tag_stops(C):
    with pytest.raises(C.ApplyError, match="그래프"):
        C.carry_from("g99")


def test_main_flag_runs_carry_under_the_lock(C, monkeypatch):
    monkeypatch.setattr(C.sys, "argv", ["kg_apply.py", "--carry-from", "g8"])
    assert C.main() == "g2"
    assert (C.STATE / "kg-apply.lock").exists() and C.calls["extract"] == 0
