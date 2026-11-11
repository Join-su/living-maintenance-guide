"""배포단 API 더하기(VFE EV-005 지시 040, D-178 · D-180, E3 C-1 ~ C-5): 실행 목록 · /info · /health · provider 칸 · 생성 문서 끔.
기존 응답 칸 · 오류 본문 · 같은 키 재요청은 그대로(더하기만)."""
import json
import threading

from deploy_server.fake_provider import OLD_RULE, FakeProvider


def post(client, body, key):
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(),
                       headers={"Content-Type": "application/json", "Idempotency-Key": key})


def test_t1_list_newest_first_with_pages(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    ids = [post(c, cases[k], f"list-{k}").json()["run_id"] for k in ("C01", "C02", "C03")]
    newest = sorted(ids, reverse=True)
    r = c.get("/runs", params={"limit": 2})
    assert r.status_code == 200
    b = r.json()
    assert [x["run_id"] for x in b["items"]] == newest[:2] and b["next_before"] == newest[1]
    b2 = c.get("/runs", params={"limit": 2, "before": b["next_before"]}).json()
    assert [x["run_id"] for x in b2["items"]] == newest[2:] and b2["next_before"] is None
    assert [x["run_id"] for x in c.get("/runs").json()["items"]] == newest   # 기본 쪽 크기 20


def test_t2_list_items_leave_out_result_and_keep_error(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    ok = post(c, cases["C01"], "items-ok").json()
    dep.runs.save({**c.get(f"/runs/{ok['run_id']}").json(), "run_id": "run_ZZZZFAILED", "status": "failed",
                   "result": None, "error": {"code": "run_failed", "message": "x"}})   # 실패 실행 기록(저장 모양 그대로)
    items = {x["run_id"]: x for x in c.get("/runs").json()["items"]}
    assert "result" not in items[ok["run_id"]] and items[ok["run_id"]]["error"] is None
    assert items[ok["run_id"]]["pack"] == ok["pack"] and items[ok["run_id"]]["report"] == ok["report"]
    assert items["run_ZZZZFAILED"]["status"] == "failed" and items["run_ZZZZFAILED"]["error"]["code"] == "run_failed"


def test_t3_list_query_errors(make_deployment, client_for):
    c = client_for(make_deployment())
    for params, where in (({"limit": "0"}, "query:limit"), ({"limit": "101"}, "query:limit"), ({"limit": "abc"}, "query:limit"),
                          ({"limit": "-1"}, "query:limit"), ({"before": "x"}, "query:before"), ({"before": "run_a-b"}, "query:before")):
        r = c.get("/runs", params=params)
        e = r.json()["error"]
        assert r.status_code == 422 and e["code"] == "invalid_query" and e["where"] == where and e["run_id"] is None, params


def test_t4_empty_list(make_deployment, client_for):
    assert client_for(make_deployment()).get("/runs").json() == {"items": [], "next_before": None}


def test_t5_info_matches_run_response(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    info = c.get("/info").json()
    run = post(c, cases["C01"], "info-run").json()
    for k in ("deployment_id", "pack", "data_release_id", "runtime_version"):
        assert info[k] == run[k], k
    assert info["provider"] == {"mode": "fake", "fake_rule": OLD_RULE} and info["review_policy"] == "auto_approve"
    assert info["server"]["name"] == "llmopsagent-deploy" and info["server"]["version"] == "0.2.0" and info["server"]["started_at"].endswith("Z")
    text = json.dumps(info, ensure_ascii=False)
    assert "/Users/" not in text and "/private/" not in text and str(dep.settings.data_dir) not in text and "postgresql://" not in text


def test_t6_health_answers_while_a_run_holds_the_lock(make_deployment, client_for):
    dep = make_deployment()
    c = client_for(dep)
    got = {}
    with dep.lock:   # 실행 중과 같은 상태
        t = threading.Thread(target=lambda: got.update(r=c.get("/health")))
        t.start()
        t.join(timeout=10)
    assert not t.is_alive()
    assert got["r"].status_code == 200
    assert got["r"].json() == {"status": "ok", "deployment_id": "task10@dev-local", "pack_revision": dep.runner.rt.revision}


class _Marking(FakeProvider):
    """옛 규칙 답은 그대로 두고, 켜 두면 chat 호출마다 한계 prompt_unknown을 남기는 가짜(실행별 모으기 시험용)."""
    mark = False

    def chat(self, *a, **kw):
        out = super().chat(*a, **kw)
        if self.mark:
            self.limits[-1] = ["prompt_unknown"]
        return out


def test_t7_provider_field_on_runs_and_reports_with_limits_per_run(make_deployment, client_for, cases):
    prov = _Marking()
    dep = make_deployment(provider=prov)
    c = client_for(dep)
    first = post(c, cases["C01"], "mode-1").json()
    assert first["provider"] == {"mode": "fake", "fake_rule": OLD_RULE, "fake_limits": []}
    assert c.get(f"/runs/{first['run_id']}").json()["provider"] == first["provider"]
    assert c.get(first["report"]["href"]).json()["provider"] == {"mode": "fake", "fake_rule": OLD_RULE}
    prov.mark = True
    second = post(c, cases["C01"], "mode-2").json()
    assert prov.count > 0 and second["provider"]["fake_limits"] == ["prompt_unknown"]
    prov.mark = False
    third = post(c, cases["C01"], "mode-3").json()
    assert third["provider"]["fake_limits"] == []                       # 앞 실행의 한계를 끌고 오지 않음
    # 기존 칸은 그대로(이름 · 값)
    for k in ("run_id", "status", "outcome", "deployment_id", "pack", "data_release_id", "runtime_version", "trace", "report", "review",
              "started_at", "ended_at", "href"):
        assert k in third, k
    assert third["review"]["actor_kind"] == "auto" and third["review"] == first["review"]


def test_t7b_limits_since_counts_and_truncation():
    p = FakeProvider()
    for _ in range(3):
        p._note(["prompt_unknown"])
    mark = p.count
    p._note([])
    p._note(["passthrough"])
    assert p.limits_since(mark) == ([[], ["passthrough"]], False) and p.limits_since(p.count) == ([], False)
    q = FakeProvider()
    q.limits = type(q.limits)(maxlen=2)
    for r in ("a", "b", "c"):
        q._note([r])
    assert q.limits_since(0) == ([["b"], ["c"]], True)


def test_t8_replay_and_error_bodies_keep_their_shape(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    r1 = post(c, cases["C01"], "same-key")
    r2 = post(c, cases["C01"], "same-key")
    assert r2.headers["Idempotent-Replayed"] == "true" and r2.content == r1.content and "provider" in r1.json()
    bad = c.post("/runs", content=b"{", headers={"Content-Type": "application/json", "Idempotency-Key": "bad-json"})
    assert bad.status_code == 400 and set(bad.json()) == {"error"} and set(bad.json()["error"]) == {"code", "message", "where", "retryable", "run_id", "details"}

    class Boom(FakeProvider):
        def chat(self, *a, **kw):
            raise RuntimeError("boom")
    dep2 = make_deployment(provider=Boom(), data_dir=dep.settings.data_dir.parent / "data2")
    c2 = client_for(dep2)
    failed = post(c2, cases["C01"], "fails")
    assert failed.status_code >= 500 and set(failed.json()) == {"error"}            # 오류 본문에는 칸을 넣지 않음
    rec = c2.get(f"/runs/{failed.json()['error']['run_id']}").json()
    assert rec["status"] == "failed" and rec["provider"]["mode"] == "fake" and rec["provider"]["fake_limits"] == []


def test_t9_http_mode_shows_only_the_mode(make_deployment):
    dep = make_deployment(provider=FakeProvider())
    dep.settings.provider = "http"   # 실행하지 않고 모양만(실제 호출 없음)
    assert dep.provider_info() == {"mode": "http"} and dep.info()["provider"] == {"mode": "http"}
    assert dep._summary("run_X", "0" * 32, "t")["provider"] == {"mode": "http"}


def test_t10_generated_docs_are_off(make_deployment, client_for):
    c = client_for(make_deployment())
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert c.get(path).status_code == 404, path


def test_t11_deploy_check_probe_still_404(make_deployment, client_for):
    r = client_for(make_deployment()).get("/runs/run_ping")
    assert r.status_code == 404 and r.json()["error"]["code"] == "run_not_found"
