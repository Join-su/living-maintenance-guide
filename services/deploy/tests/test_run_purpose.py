"""D-476 덧 9(오케 승인 · deploy 약속 변경): 요청 머리글 X-Run-Purpose → trace run_started 본문 meta.purpose.
값 목록은 trial 하나 · 모르는 값은 400 run_purpose_invalid · 머리글이 없으면 지금과 바이트 같음(trace · 응답 · 같은 키 재요청)."""
import json

import pytest


def post(client, body, key, purpose=None):
    h = {"Content-Type": "application/json", "Idempotency-Key": key}
    if purpose is not None:
        h["X-Run-Purpose"] = purpose
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(), headers=h)


def started(dep, run_id):
    ev = [e for e in dep.trace.events_of(run_id) if e["type"] == "run_started"]
    assert len(ev) == 1
    return ev[0]["body"]


def test_trial_goes_to_run_started_meta(make_deployment, client_for, cases):
    dep = make_deployment()
    r = post(client_for(dep), cases["C01"], "purpose-trial", "trial")
    assert r.status_code == 201
    body = started(dep, r.json()["run_id"])
    assert body["meta"] == {"purpose": "trial"}
    assert [e["type"] for e in dep.trace.events_of(r.json()["run_id"])][-1] == "run_ended"


def test_no_header_is_unchanged(make_deployment, client_for, cases):
    a, b = make_deployment(), make_deployment()
    ra = post(client_for(a), cases["C01"], "purpose-none")
    rb = post(client_for(b), cases["C01"], "purpose-none-2", "trial")
    assert ra.status_code == rb.status_code == 201
    sa, sb = started(a, ra.json()["run_id"]), started(b, rb.json()["run_id"])
    assert "meta" not in sa
    assert {k: v for k, v in sb.items() if k != "meta"}.keys() == sa.keys()
    drop = lambda j: {k: v for k, v in j.items() if k not in ("run_id", "href", "trace", "trace_id", "started_at", "ended_at", "report")}
    assert drop(ra.json()) == drop(rb.json())   # 응답 칸은 머리글이 있어도 같음(trace에만 적음)


@pytest.mark.parametrize("value", ["", "Trial", "eval", "trial,trial", "x" * 300])
def test_unknown_value_is_400_and_no_run(make_deployment, client_for, cases, value):
    dep = make_deployment()
    r = post(client_for(dep), cases["C01"], f"purpose-bad-{len(value)}", value)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "run_purpose_invalid" and err["where"] == "header:X-Run-Purpose"
    assert value[:20] not in err["message"] or value == ""   # 받은 값은 되풀이하지 않음
    assert dep.trace.events == {}


def test_same_key_replay_and_purpose_is_part_of_the_request(make_deployment, client_for, cases):
    dep = make_deployment()
    c = client_for(dep)
    first = post(c, cases["C01"], "purpose-key", "trial")
    again = post(c, cases["C01"], "purpose-key", "trial")
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true" and again.json() == first.json()
    other = post(c, cases["C01"], "purpose-key")   # 같은 키 · 같은 본문 · 머리글만 다름 → 다른 요청
    assert other.status_code == 422 and other.json()["error"]["code"] == "idempotency_key_reused"
    plain = post(c, cases["C01"], "plain-key")
    assert post(c, cases["C01"], "plain-key").json() == plain.json()
