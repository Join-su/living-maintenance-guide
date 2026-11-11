"""감시가 LLMOps 되돌리기 요청을 받음(D-633 · dev-2 시연만) — AD_ROLLBACK=on이면 요청 폴더의 llmops/rollback-request@1(RB- id) 하나마다
장부 줄에서 태그 → 태그 revision 대조 → registered → 바꿔 끼우기 → /info 대조 → switched. 장부 · 전환 · 서버 모두 가짜, 네트워크 · DB 0."""
import json
import os

import pytest

from test_autodeploy import A, DRS, NEW, OLD  # noqa: F401 - fixture A

DEP = "c1a15@dev-local"
RID = "RB-c1a15-V7-1"


@pytest.fixture
def RB(A, monkeypatch):  # noqa: F811
    monkeypatch.setattr(A, "ROLLBACK", True)
    A.swaps, A.tags = [], {"v13": ("c" * 40, NEW, "mm3-v3-test"), "v12": ("b" * 40, OLD, None)}
    A.info = {"pack": {"tag": None, "pack_revision": None, "commit": "c" * 40}, "data_release_id": DRS}

    def tag_rev(tag):
        if tag not in A.tags:
            raise RuntimeError("Pack 저장소에 태그가 없음")
        return A.tags[tag]

    def rb_swap(tag):
        A.swaps.append(tag)
        if getattr(A, "swap_error", None):
            raise A.swap_error
        A.info["pack"].update(tag=tag, pack_revision=A.tags[tag][1])
    monkeypatch.setattr(A, "tag_rev", tag_rev)
    monkeypatch.setattr(A, "rb_swap", rb_swap)
    monkeypatch.setattr(A, "get", lambda path, timeout=5: A.info)
    monkeypatch.setattr(A, "listener", lambda: "123")
    return A


def rbreq(A, rid=RID, **kw):
    d = {"format": A.RB_FORMAT, "rollback_id": rid, "deployment_id": DEP, "version": "V7", "revision": NEW, "release": "rel-x",
         "from_version": "V8", "decision_id": 21, "decided_by": "사람", "tag": None, **kw}
    (A.REQ / f"{rid}.json").write_text(json.dumps(d))


def status(A, rid=RID):
    p = A.ROOT / "status" / "deployments" / f"{rid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def stages(A):
    return [c.split("상태 ", 1)[1].split()[1] for c in A.said if "상태 RB-" in c]


@pytest.fixture(autouse=True)
def said(A, monkeypatch):  # noqa: F811
    A.said = []
    real = A.say
    monkeypatch.setattr(A, "say", lambda *a: A.said.append(" ".join(map(str, a))) or real(*a))


def test_new_request_registered_then_switched(RB):
    rbreq(RB)
    RB.run(once=True, baseline={})
    assert RB.swaps == ["v13"]
    s = status(RB)
    assert list(s) == list(RB.STATUS_FIELDS)
    assert (s["id"], s["stage"], s["version"], s["revision"], s["tag"], s["deployment_id"]) == (RID, "switched", "V7", NEW, "v13", DEP)
    assert "/info v13 33333333" in s["detail"] and "전환" not in s["detail"]
    assert stages(RB) == ["registered", "switched"]
    assert RB.switched == [] and not any("deploy-status.py" in c or "/runs" in c for c in RB.fake.calls)   # CHG 길 · C01 Run 0


def test_baseline_status_dry_other_deployment_and_off_are_untouched(RB, monkeypatch):
    rbreq(RB, "RB-c1a15-V7-old")
    base = {p.name: p.stat().st_mtime_ns for p in RB.REQ.glob("*.json")}
    rbreq(RB, "RB-c1a15-V7-dry", dry_run=True)
    rbreq(RB, "RB-c1a15-V7-other", deployment_id="c1a15@elsewhere")
    rbreq(RB, "RB-c1a15-V7-badname-x", rollback_id="RB-c1a15-V7-mismatch")
    rbreq(RB, "RB-c1a15-V7-fmt", format="task10-pack/approved-change@2")
    rbreq(RB, "RB-c1a15-V7-has")
    (RB.ROOT / "status" / "deployments").mkdir(parents=True)
    (RB.ROOT / "status" / "deployments" / "RB-c1a15-V7-has.json").write_text("{}")
    assert RB.rb_pick(base) == []
    rbreq(RB, "RB-c1a15-V7-new")
    assert [p.stem for p in RB.rb_pick(base)] == ["RB-c1a15-V7-new"]
    monkeypatch.setattr(RB, "ROLLBACK", False)
    assert RB.rb_pick(base) == []                         # 기본 off = 지금 감시 그대로
    assert RB.pick(base) == []                            # CHG 길은 RB 파일을 보지 않음


def test_run_keeps_requests_present_at_start(RB):
    rbreq(RB)
    RB.run(once=True)                                     # 기준선 = 켤 때 있던 파일
    assert RB.swaps == [] and status(RB) is None


def test_versions_off_still_keeps_start_requests(RB, monkeypatch):
    monkeypatch.setattr(RB, "VERSIONS", False)
    rbreq(RB)
    RB.run(once=True)
    assert RB.swaps == [] and status(RB) is None


def test_same_id_twice_zero(RB):
    rbreq(RB)
    RB.run(once=True, baseline={})
    (RB.ROOT / "status" / "deployments" / f"{RID}.json").unlink()
    os.utime(RB.REQ / f"{RID}.json")
    RB.run(once=True, baseline={})
    assert RB.swaps == ["v13"]                            # 처리 표시가 있으면 다시 0


def test_target_deployment_id_and_request_tag(RB):
    RB.tags["v13-again"] = ("d" * 40, NEW, None)
    rbreq(RB, deployment_id=None, target_deployment_id=DEP, tag="v13-again")
    RB.run(once=True, baseline={})
    assert RB.swaps == ["v13-again"] and status(RB)["stage"] == "switched"


def test_latest_ledger_row_of_the_revision(RB, monkeypatch):
    RB.tags["v13b"] = ("e" * 40, NEW, None)
    monkeypatch.setattr(RB, "ledger", lambda: [("v13", NEW, DRS), ("v14", OLD, DRS), ("v13b", NEW, DRS)])
    rbreq(RB)
    RB.run(once=True, baseline={})
    assert RB.swaps == ["v13b"]


def test_no_ledger_row_fails_at_step2(RB):
    rbreq(RB, revision="sha256:" + "9" * 64)
    RB.run(once=True, baseline={})
    s = status(RB)
    assert RB.swaps == [] and s["stage"] == "failed" and "장부 줄 없음" in s["detail"] and "전환" not in s["detail"]


@pytest.mark.parametrize("tags, why", [({}, "읽지 못함"), ({"v13": ("c" * 40, OLD, None)}, "다름")])
def test_tag_problems_fail_at_step2(RB, tags, why):
    RB.tags = tags
    rbreq(RB)
    RB.run(once=True, baseline={})
    s = status(RB)
    assert RB.swaps == [] and s["stage"] == "failed" and why in s["detail"] and "전환" not in s["detail"]


def test_swap_failure_fails_at_step3(RB):
    class SwapError(RuntimeError):
        pass
    RB.swap_error = SwapError("서버가 30초 안에 뜨지 않음 — 설정 옛 값 · 옛 서버 다시 뜸")
    rbreq(RB)
    RB.run(once=True, baseline={})
    s = status(RB)
    assert s["stage"] == "failed" and s["detail"].startswith("운영 전환 실패 — 서버가 30초") and s["tag"] == "v13"
    assert stages(RB) == ["registered", "failed"]


def test_info_mismatch_fails_at_step3(RB, monkeypatch):
    monkeypatch.setattr(RB, "rb_swap", lambda tag: RB.swaps.append(tag))   # 띄웠는데 /info가 옛 revision
    RB.info["pack"].update(tag="v12", pack_revision=OLD)
    rbreq(RB)
    RB.run(once=True, baseline={})
    s = status(RB)
    assert s["stage"] == "failed" and "전환" in s["detail"] and "/info" in s["detail"]


def test_swap_runs_inside_the_kg_lock(RB, monkeypatch, tmp_path):
    import fcntl
    monkeypatch.setattr(RB, "KG", True)
    monkeypatch.setattr(RB, "KG_STATE", tmp_path / "kg-state")
    monkeypatch.setattr(RB, "KG_CANDS", tmp_path / "kg-candidates")   # 빈 폴더 = kg_apply 부름 0
    held = []

    def rb_swap(tag):
        with open(RB.KG_STATE / "kg-apply.lock", "w") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)                         # 감시가 이미 잡고 있음
        RB.info["pack"].update(tag=tag, pack_revision=NEW)
    monkeypatch.setattr(RB, "rb_swap", rb_swap)
    rbreq(RB)
    RB.run(once=True, baseline={})
    assert held == [True] and status(RB)["stage"] == "switched"


def test_same_revision_running_is_switched_without_restart(RB):
    RB.info["pack"].update(tag="v13-other-name", pack_revision=NEW, commit="f" * 40)   # 장부 태그 v13 · 서버는 같은 revision 다른 이름
    rbreq(RB, tag="v13")
    RB.run(once=True, baseline={})
    s = status(RB)
    assert RB.swaps == [] and s["stage"] == "switched" and s["tag"] == "v13-other-name" and "다시 띄움 0" in s["detail"]
    assert stages(RB) == ["registered", "switched"]


def test_no_decision_id_and_extra_fields_are_fine(RB):
    rbreq(RB, decision_id=None, tag="v13", commit=None, from_release="rel-y", from_revision=OLD, from_deployment_id=DEP, reason="시험")
    RB.run(once=True, baseline={})
    assert RB.swaps == ["v13"] and status(RB)["stage"] == "switched"
