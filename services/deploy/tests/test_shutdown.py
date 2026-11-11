"""서버 종료 때 기록기 연결 정리(EV-002 CHG-02, V-16 a~c). DB 없음(FakeWriter). 종료 뒤 다시 기록되는지는 tests/test_logdb.py(V-16 d)."""
import json
import threading
import time

from fastapi.testclient import TestClient

from deploy_server.app import create_app
from deploy_server.fake_provider import FakeProvider


def _post(client, body, key):
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(), headers={"Idempotency-Key": key})


def test_app_shutdown_closes_writer_once(make_deployment, cases):
    dep = make_deployment()
    with TestClient(create_app(deployment=dep)) as c:   # with 블록이 끝나면 lifespan 종료
        assert _post(c, cases["C01"], "sd-1").status_code == 201
        assert dep.trace.closed == []
    assert len(dep.trace.closed) == 1


class _NoCloseWriter:
    """close가 없는 기록기(sink_for만)."""

    def __init__(self, inner):
        self.inner = inner

    def sink_for(self, ctx):
        return self.inner.sink_for(ctx)


def test_writer_without_close_shuts_down_cleanly(make_deployment, cases, tmp_path):
    from fake_writer import FakeWriter
    inner = FakeWriter(tmp_path / "trace")
    dep = make_deployment(trace_writer=_NoCloseWriter(inner))
    with TestClient(create_app(deployment=dep)) as c:
        assert _post(c, cases["C01"], "sd-2").status_code == 201
    assert inner.closed == []   # 부르지 않음, 오류도 없음


class _SlowModel(FakeProvider):
    """초안을 만들기 전에 신호를 기다린다(실행 중 종료를 겹치게 하려고)."""

    def __init__(self):
        super().__init__()
        self.entered, self.go = threading.Event(), threading.Event()

    def _draft(self, prompt):
        self.entered.set()
        assert self.go.wait(10)
        return super()._draft(prompt)


def test_close_during_run_waits_for_run_ended(make_deployment, cases):
    """실행 중에 종료(close)를 시작해도 그 실행은 201로 끝나고, close는 run_ended까지 기록된 뒤에 불린다."""
    model = _SlowModel()
    dep = make_deployment(provider=model)
    out = {}
    run = threading.Thread(target=lambda: out.update(r=dep.post_run(json.dumps(cases["C01"]).encode(), "sd-3")))
    run.start()
    assert model.entered.wait(10)
    closer = threading.Thread(target=dep.close)
    closer.start()
    time.sleep(0.3)
    assert dep.trace.closed == [] and closer.is_alive()   # 실행이 끝날 때까지 닫지 않음
    model.go.set()
    run.join(10)
    closer.join(10)
    status, body, replayed = out["r"]
    assert status == 201 and not replayed
    events = dep.trace.events_of(body["run_id"])
    assert events[-1]["type"] == "run_ended"
    assert dep.trace.closed == [len(events)]   # close 때 이미 run_ended까지 기록돼 있었음
