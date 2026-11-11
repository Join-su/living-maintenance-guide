"""갈래 k(VFE EV-005 K-c1a15-path-E1-E3, D-259 (4) · D-265): POST /runs 201 본문에 실행 결과(result) · /info에 release 이름.
더하기만 — GET /runs/{id} · 목록 항목(result 없음) · 실패 응답 · 같은 키 재요청 규칙은 그대로."""
import json

from deploy_server.fake_provider import FakeProvider
from deploy_server.settings import load_settings

from conftest import ROOT


def post(client, body, key):
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(),
                       headers={"Content-Type": "application/json", "Idempotency-Key": key})


def test_k1_201_carries_the_same_result_as_get(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    r = post(c, cases["C01"], "k1-same")
    assert r.status_code == 201
    body = r.json()
    got = c.get(body["href"]).json()
    assert body["result"] == got["result"] and body["result"]["output"]   # 실행 기록과 같은 값(가림 뒤)
    assert {k: v for k, v in body.items() if k != "result"} == {k: v for k, v in got.items() if k not in ("result", "error")}   # 나머지 칸 그대로
    assert "error" not in body


def test_k1_replay_returns_the_first_body(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    first = post(c, cases["C01"], "k1-replay")
    again = post(c, cases["C01"], "k1-replay")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() and "result" in again.json()
    assert again.headers.get("Idempotent-Replayed") == "true"


def test_k1_list_items_still_leave_out_result(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    rid = post(c, cases["C01"], "k1-list").json()["run_id"]
    item = next(x for x in c.get("/runs").json()["items"] if x["run_id"] == rid)
    assert "result" not in item


def test_k1_failures_keep_their_shape(make_deployment, client_for, cases):
    c = client_for(make_deployment())
    bad = post(c, {**cases["C01"], "event_id": 5}, "k1-bad")
    assert bad.status_code == 422 and set(bad.json()) == {"error"} and bad.json()["error"]["code"] == "invalid_input"


class LeakyModel(FakeProvider):
    def _draft(self, prompt):
        out = super()._draft(prompt)
        out["uncertainty"] = "키 fake-secret-value-0001 와 sk-fakeABCDEFGHIJKLMNOPQRST 가 섞인 답"
        return out


def test_k1_201_result_is_redacted(make_deployment, client_for, cases, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret-value-0001")
    c = client_for(make_deployment(provider=LeakyModel()))
    r = post(c, cases["C01"], "k1-redact")
    assert r.status_code == 201
    assert b"fake-secret-value-0001" not in r.content and b"sk-fakeABCDEFGHIJKLMNOPQRST" not in r.content


def test_k4_info_shows_store_and_model_releases(make_deployment, client_for):
    info = client_for(make_deployment()).get("/info").json()
    ex = load_settings(ROOT / "config" / "dev-local.yaml").execution
    assert info["releases"] == {"stores": {k: v["release"] for k, v in sorted(ex["stores"].items())},
                                "models": {k: v["release"] for k, v in sorted((ex.get("models") or {}).items())}}
    assert info["releases"]["stores"]   # task10 dev-local: history · manuals


def test_k4_v4_config_lists_the_model_release():
    ex = load_settings(ROOT / "config" / "v4-pg-vps.yaml").execution
    assert ex["models"]["failure_classifier"]["release"] == "mr-ai4i-standin-v3"   # /info releases.models의 출처(설정 읽기만)
