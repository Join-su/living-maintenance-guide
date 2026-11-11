"""감시 자동 승인(D-565 · D-560 덧 9 · dev-2만) — LLMOps가 스스로 낸 배포 승인 요청(결정자 auto-apply)을 감시가 처리할지.
AD_AUTO_APPLY 기본 off: auto-apply 요청은 처리 0 · 배포 상태 failed('자동 승인 꺼짐 — 사람 승인 필요') / on: 사람 요청과 같이 끝까지.
사람이 결정한 요청 · 결정자를 못 읽은 요청은 지금처럼. 결정자 = LLMOps /api/state 버전 칸 rollout(change_id · decided_by) — DB 권한 0 · HTTP 읽기만."""
import importlib
import json

import pytest

from test_autodeploy import A, DRS, NEW, deploy_calls, load, req  # noqa: F401 - fixture A


def state(cid, by):
    return {"projects": [{"id": "p-1", "releases": [{"name": "V8", "version": {"rollout": {"change_id": cid, "decided_by": by}}},
                                                     {"name": "V7", "version": {}}]}]}


@pytest.mark.parametrize("on,by,goes", [(False, "auto-apply", False), (True, "auto-apply", True), (False, "owner-01", True),
                                        (True, "owner-01", True), (False, None, True)])
def test_auto_apply_request_is_taken_only_when_on(A, monkeypatch, on, by, goes):  # noqa: F811
    monkeypatch.setattr(A, "AUTO_APPLY", on)
    monkeypatch.setattr(A, "llmops_state", lambda: state("CHG-c1a15-V8-auto", by) if by else (_ for _ in ()).throw(OSError("down")))
    req(A, "CHG-c1a15-V8-auto")
    A.run(once=True, baseline={})
    if goes:
        assert A.switched == [("CHG-c1a15-V8-auto", "v13", NEW, DRS)]
    else:
        assert A.switched == [] and not any(str(A.TOOL) in c for c in A.fake.calls)   # Pack 받아 넣기도 부르지 않음
        f = deploy_calls(A, "failed")
        assert len(f) == 1 and "자동 승인 꺼짐" in f[0] and "사람 승인" in f[0]


def test_decided_by_reads_the_rollout_of_that_change(A, monkeypatch):  # noqa: F811
    monkeypatch.setattr(A, "llmops_state", lambda: state("CHG-c1a15-V8-auto", "auto-apply"))
    assert A.decided_by("CHG-c1a15-V8-auto") == "auto-apply" and A.decided_by("CHG-c1a15-V9-x") is None


def test_auto_apply_setting_default_off_and_bad_value_refused(monkeypatch):
    monkeypatch.delenv("AD_AUTO_APPLY", raising=False)
    assert load().AUTO_APPLY is False
    monkeypatch.setenv("AD_AUTO_APPLY", "on")
    assert load().AUTO_APPLY is True
    monkeypatch.setenv("AD_AUTO_APPLY", "yes")
    with pytest.raises(SystemExit):
        load()
