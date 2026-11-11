"""감시가 후보 그래프 파일도 반영(D-570 · dev-2 시연만) — AD_KG=on이면 KG_CANDIDATES에 처리 표시 없는 파일이 보일 때 kg_apply 한 번,
같은 묶음이 실패했으면 묶음이 바뀔 때까지 다시 0 · 버전 전환은 kg_apply와 같은 잠금 안 · AD_VERSIONS=off면 요청 폴더 · 받아 넣기 도구를 안 봄.
기본(AD_KG · AD_VERSIONS 없음)은 지금 감시 그대로(운영 · dev)."""
import fcntl
import threading
import time
import types

import pytest

from test_autodeploy import A, DRS, NEW, load, req  # noqa: F401 - fixture A


@pytest.fixture
def KG(A, monkeypatch, tmp_path):  # noqa: F811
    cands, st = tmp_path / "kg-candidates", tmp_path / "kg-state"
    cands.mkdir()
    for k, v in {"KG": True, "KG_CANDS": cands, "KG_STATE": st}.items():
        monkeypatch.setattr(A, k, v)
    A.kg_rc = [0]
    real_sh = A.sh

    def sh(args, **kw):
        if "scripts/kg_apply.py" in args:
            A.fake.calls.append("kg_apply")
            return types.SimpleNamespace(returncode=A.kg_rc.pop(0) if len(A.kg_rc) > 1 else A.kg_rc[0], stdout="", stderr="")
        return real_sh(args, **kw)
    monkeypatch.setattr(A, "sh", sh)
    A.cand = lambda n: (cands / f"{n}.json").write_text("{}")
    return A


def kg_calls(A):
    return A.fake.calls.count("kg_apply")


def test_new_candidate_calls_kg_apply_once(KG):
    KG.cand("KGC-c1a15-0001-aaaaaaaa")
    KG.run(once=True, baseline={})
    assert kg_calls(KG) == 1


def test_done_or_failed_or_hidden_files_do_not_call(KG):
    for d, n in (("done-candidates", "KGC-c1a15-0001-aaaaaaaa"), ("failed-candidates", "KGC-c1a15-0002-bbbbbbbb")):
        KG.cand(n)
        (KG.KG_STATE / d).mkdir(parents=True, exist_ok=True)
        (KG.KG_STATE / d / n).write_text("x")
    (KG.KG_CANDS / ".KGC-c1a15-0003-cccccccc.json.tmp").write_text("{")
    KG.run(once=True, baseline={})
    assert kg_calls(KG) == 0


def test_failed_batch_is_not_called_again_until_it_changes(KG, monkeypatch):
    KG.kg_rc = [1, 1, 0]
    KG.cand("KGC-c1a15-0001-aaaaaaaa")
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] == 3:
            KG.cand("KGC-c1a15-0002-bbbbbbbb")   # 새 후보가 오면 묶음이 바뀜 → 다시 부름
        if n[0] == 6:
            KG.STOP = True
    monkeypatch.setattr(KG.time, "sleep", sleep)
    KG.run(baseline={})
    assert kg_calls(KG) == 2                       # 처음 1 · 같은 묶음 되풀이 0 · 바뀐 묶음 1


def test_version_switch_waits_for_the_kg_lock(KG, monkeypatch):
    monkeypatch.setattr(KG.signal, "signal", lambda *a: None)   # 시험만: 감시를 다른 스레드에서 돌림
    KG.KG_STATE.mkdir(parents=True, exist_ok=True)
    req(KG, "CHG-c1a15-V8-x")
    lk = open(KG.KG_STATE / "kg-apply.lock", "w")
    fcntl.flock(lk, fcntl.LOCK_EX)                    # kg_apply가 그래프를 바꿔 띄우는 중
    t = threading.Thread(target=lambda: KG.run(once=True, baseline={}))
    t.start()
    time.sleep(0.5)
    assert KG.switched == []                          # 잠금이 풀릴 때까지 버전 전환 0
    fcntl.flock(lk, fcntl.LOCK_UN)
    lk.close()
    t.join(5)
    assert KG.switched == [("CHG-c1a15-V8-x", "v13", NEW, DRS)]


def test_versions_off_ignores_requests_and_tool(KG, monkeypatch):
    monkeypatch.setattr(KG, "VERSIONS", False)
    monkeypatch.setattr(KG, "TOOL", KG.ROOT / "없는-도구.py")   # 받아 넣기 도구가 없어도 켜짐
    req(KG, "CHG-c1a15-V8-x")
    KG.cand("KGC-c1a15-0001-aaaaaaaa")
    KG.run(once=True)
    assert KG.switched == [] and kg_calls(KG) == 1


def test_kg_on_needs_its_folders(A, monkeypatch):  # noqa: F811
    monkeypatch.setattr(A, "KG", True)
    monkeypatch.setattr(A, "KG_CANDS", None)
    with pytest.raises(SystemExit):
        A.run(once=True, baseline={})


def test_defaults_are_today_and_bad_values_refused(monkeypatch):
    for k in ("AD_KG", "AD_VERSIONS"):
        monkeypatch.delenv(k, raising=False)
    m = load()
    assert m.KG is False and m.VERSIONS is True
    for k, v in (("AD_KG", "yes"), ("AD_VERSIONS", "0")):
        monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit):
            load()
        monkeypatch.delenv(k)


def test_start_with_versions_off_needs_no_tool(KG, monkeypatch):
    monkeypatch.setattr(KG, "VERSIONS", False)
    monkeypatch.setattr(KG, "TOOL", KG.ROOT / "없는-도구.py")
    monkeypatch.setattr(KG.subprocess, "Popen", lambda *a, **k: types.SimpleNamespace(pid=4242))
    monkeypatch.setattr(KG.time, "sleep", lambda _: None)
    KG.start()                                         # 시연 감시(후보만)는 받아 넣기 도구 없이 켜짐
    assert (KG.STATE / "pid").read_text() == "4242"


def test_start_with_versions_on_still_needs_tool(A, monkeypatch):  # noqa: F811
    monkeypatch.setattr(A, "TOOL", A.ROOT / "없는-도구.py")
    with pytest.raises(SystemExit):
        A.start()


@pytest.fixture
def KL(KG, monkeypatch, tmp_path):
    inbox = tmp_path / "kg-inbox"
    inbox.mkdir()
    monkeypatch.setattr(KG, "KG_INBOX", inbox)
    monkeypatch.setattr(KG, "KG_LETTERS", True)
    KG.letter = lambda n: (inbox / f"{n}.json").write_text("{}")
    return KG


def test_new_approval_file_in_the_log_folder_calls_kg_apply(KL):   # 시작 장치 = 로그(KG 반영 대기 폴더 · D-570 23:46)
    KL.letter("RPT-000001")
    KL.run(once=True, baseline={})
    assert kg_calls(KL) == 1


def test_done_approval_file_does_not_call(KL):
    KL.letter("RPT-000001")
    (KL.KG_STATE / "done").mkdir(parents=True)
    (KL.KG_STATE / "done" / "RPT-000001").write_text("x")
    (KL.KG_INBOX / ".RPT-000002.json.tmp").write_text("{")
    KL.run(once=True, baseline={})
    assert kg_calls(KL) == 0


def test_letters_off_ignores_the_log_folder(KL, monkeypatch):
    monkeypatch.setattr(KL, "KG_LETTERS", False)
    KL.letter("RPT-000001")
    KL.run(once=True, baseline={})
    assert kg_calls(KL) == 0


def test_version_switch_then_carry_runs_outside_the_lock(KG, monkeypatch):
    KG.KG_STATE.mkdir(parents=True, exist_ok=True)
    held = []
    real = KG.sh

    def sh(args, **kw):
        if "--carry-from" in args:
            with open(KG.KG_STATE / "kg-apply.lock", "w") as lk:      # kg_apply가 잠금을 잡을 수 있어야 함(감시가 쥐고 있으면 막힘)
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held.append(False)
                except BlockingIOError:
                    held.append(True)
            KG.fake.calls.append(" ".join(args))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return real(args, **kw)
    monkeypatch.setattr(KG, "sh", sh)
    req(KG, "CHG-c1a15-V8-x")
    KG.run(once=True, baseline={})
    assert KG.switched == [("CHG-c1a15-V8-x", "v13", NEW, DRS)]
    carry = [c for c in KG.fake.calls if "--carry-from" in c]
    assert carry == ["scripts/py scripts/kg_apply.py --carry-from v12"] and held == [False]


def test_no_carry_when_kg_off(A):  # noqa: F811
    req(A, "CHG-c1a15-V8-x")
    A.run(once=True, baseline={})
    assert A.switched and not any("--carry-from" in c for c in A.fake.calls)
