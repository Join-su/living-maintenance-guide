"""V-DECIDE-RUN (EV-001 CHG-102): a decide@1 node runs with the fake Jev, writes {question: normal form} to its one write, applies
the judgement table and fallbacks (spec 7.1 · 11), the next router branches on .value, and the node record carries the decide
fields. run_ended keeps its fields; the run's token totals include the decision tokens."""
import shutil
import sys
from pathlib import Path

import pytest

import packrt
from packrt.fake.jev import FakeJevProvider
from packrt.run.engine import Runtime

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "decide_min"
TICKET = {"id": "T-1", "text": "결제가 두 번 됐어요", "channel": "web"}


class Recorder:
    """A TraceSink keeping what the runtime hands over."""

    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append({"role": role, "value": value, "retention_class": retention_class})
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


def runtime(tmp_path, overrides=None, edit=None):
    work = tmp_path / "p"
    shutil.copytree(FIXTURE, work)
    text = (work / "pack.yaml").read_text(encoding="utf-8").replace("min_runtime_version: 0.10.0", f"min_runtime_version: {packrt.__version__}")
    if edit:
        text = edit(text)
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    roots = tmp_path / "releases"
    roots.mkdir()
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: triage\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
    return Runtime(work, provider=FakeJevProvider(overrides))


def run(rt, ticket=TICKET):
    rec = Recorder()
    res = rt.run(ticket, sink=rec, review={"policy": "auto_approve"})
    return res, rec


def decide_event(rec):
    return next(e for e in rec.events if e["type"] == "node_ended" and e["node_id"] == "assess")


def triage_value(rec):
    out_ref = decide_event(rec)["body"]["output_ref"]["ref"]
    return next(p["value"] for i, p in enumerate(rec.payloads, 1) if out_ref == "sha256:" + f"{i:064x}")["triage"]


NOUL = lambda p: {"type": "noul", "noul": p}


@pytest.mark.parametrize("p, value, branch, used", [
    (0.9, True, "page_now", False),    # sure enough: the model's answer
    (0.1, False, "queue", False),      # sure enough the other way
    (0.62, True, "page_now", True),    # max(p, 1-p) = 0.62 < 0.7: fallback true (the fixture's fallback)
    (0.35, True, "page_now", True),    # 0.65 < 0.7: fallback true although the model leans to false
])
def test_noul_judgement_fallback_and_branch(tmp_path, p, value, branch, used):
    res, rec = run(runtime(tmp_path, overrides={"urgent": NOUL(p)}))
    assert res["status"] == "succeeded", res
    t = triage_value(rec)
    u = t["urgent"]
    assert u["value"] is value and u["confidence"] == round(max(p, 1 - p), 6) and u["probabilities"] == {"true": round(p, 6), "false": round(1 - p, 6)}
    assert u["confidence_source"] == "runtime_derived" and u["confidence_method"] == "max_probability"
    ran = [e["node_id"] for e in rec.events if e["type"] == "node_ended"]
    assert branch in ran and ({"page_now", "queue"} - {branch}) & set(ran) == set()
    meta = decide_event(rec)["body"]["meta"]
    assert (meta.get("fallback_used") == ["urgent"]) is used


def test_choice_score_and_record_fields(tmp_path):
    res, rec = run(runtime(tmp_path, overrides={"urgent": NOUL(0.9)}))
    t = triage_value(rec)
    assert set(t) == {"urgent", "category", "severity"}
    c, s = t["category"], t["severity"]
    assert c["confidence_source"] == "provider" and c["value"] in ("billing", "technical", "other") and c["confidence"] == c["probabilities"][c["value"]]
    assert s["confidence_source"] == "provider" and s["value"] in (1, 2, 3)
    body = decide_event(rec)["body"]
    m = body["meta"]
    assert m["kind"] == "decide@1" and m["provider"] == "jev" and m["model"] == "jev-1.13.0"
    assert m["tokens_in"] > 0 and m["tokens_out"] == 3 and m["limits"] == ["fake_decision"]
    assert m["questions"] == {"urgent": {"type": "noul", "confidence_source": "runtime_derived", "confidence_method": "max_probability"},
                              "category": {"type": "choice", "confidence_source": "provider"}, "severity": {"type": "score", "confidence_source": "provider"}}
    roles = [p["role"] for p in rec.payloads]
    assert "decide_state" in roles and "provider_raw" in roles and m["state_ref"] and m["provider_raw_ref"]
    state_payload = next(p["value"] for p in rec.payloads if p["role"] == "decide_state")
    assert state_payload == {"text": TICKET["text"], "channel": TICKET["channel"]}
    ended = next(e for e in rec.events if e["type"] == "run_ended")["body"]
    assert set(ended) == {"status", "outcome", "output_ref", "error", "ms", "tokens_in", "tokens_out"}   # run_ended fields unchanged
    assert ended["tokens_in"] == m["tokens_in"] and ended["tokens_out"] == m["tokens_out"]


def test_provider_absent_uses_the_fallback(tmp_path):
    rt = runtime(tmp_path, overrides={"urgent": NOUL(0.9), "category": {"type": "choice", "choice": "technical", "probabilities": {"technical": 1.0}}},
                 edit=lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, category: other}"))
    res, rec = run(rt)
    c = triage_value(rec)["category"]
    assert c["value"] == "other" and c["confidence"] is None and c["confidence_source"] == "provider_absent"
    assert decide_event(rec)["body"]["meta"]["fallback_used"] == ["category"]


def test_provider_absent_without_fallback_keeps_the_answer(tmp_path):
    rt = runtime(tmp_path, overrides={"urgent": NOUL(0.9), "category": {"type": "choice", "choice": "technical", "probabilities": {"technical": 1.0}}})
    res, rec = run(rt)
    c = triage_value(rec)["category"]
    assert c["value"] == "technical" and c["confidence_source"] == "provider_absent" and "fallback_used" not in decide_event(rec)["body"]["meta"]


def test_a_numeric_minimum_applies_to_every_question(tmp_path):
    rt = runtime(tmp_path, overrides={"urgent": NOUL(0.9), "severity": {"type": "score", "score": 3, "probabilities": {"1": 0.3, "2": 0.3, "3": 0.4}, "confidence": 0.4}},
                 edit=lambda t: t.replace("min_confidence: {urgent: 0.7}", "min_confidence: 0.5").replace("fallback: {urgent: true}", "fallback: {urgent: true, category: other, severity: 2}"))
    res, rec = run(rt)
    t = triage_value(rec)
    assert t["severity"]["value"] == 2 and "severity" in decide_event(rec)["body"]["meta"]["fallback_used"] and t["urgent"]["value"] is True


@pytest.mark.parametrize("q, answer, needle", [
    ("category", {"type": "choice", "choice": "travel", "probabilities": {}, "confidence": 0.9}, "선택지에 없습니다"),
    ("severity", {"type": "score", "score": 4, "probabilities": {}, "confidence": 0.9}, "score가 1 ~ 3 정수가 아닙니다"),
    ("severity", {"type": "score", "score": True, "probabilities": {}, "confidence": 0.9}, "score가 1 ~ 3 정수가 아닙니다"),
    ("urgent", {"type": "noul", "noul": 1.2}, "noul 확률이 0 ~ 1 숫자가 아닙니다"),
    ("urgent", {"type": "noul", "noul": float("nan")}, "noul 확률이 0 ~ 1 숫자가 아닙니다"),
    ("category", {"type": "choice", "choice": "billing", "probabilities": {}, "confidence": float("nan")}, "confidence가 0 ~ 1 숫자가 아닙니다"),
    ("category", {"type": "choice", "choice": "billing", "probabilities": {}, "confidence": 1.5}, "confidence가 0 ~ 1 숫자가 아닙니다"),
])
def test_a_wrong_answer_fails_the_node(tmp_path, q, answer, needle):
    over = {"urgent": NOUL(0.9)}
    over[q] = answer
    res, rec = run(runtime(tmp_path, overrides=over))
    assert res["status"] == "failed" and needle in str(res["error"])


def test_tokens_are_recorded_even_when_the_answer_is_refused(tmp_path):
    rt = runtime(tmp_path, overrides={"urgent": NOUL(0.9), "category": {"type": "choice", "choice": "travel", "probabilities": {}, "confidence": 0.9}},
                 edit=lambda t: t.replace("        reads: [ticket]\n        writes: [triage]\n", "        reads: [ticket]\n        writes: [triage]\n        on_error: {then: \"route:queue\"}\n"))
    res, rec = run(rt)
    m = decide_event(rec)["body"]["meta"]
    ended = next(e for e in rec.events if e["type"] == "run_ended")["body"]
    assert decide_event(rec)["body"]["status"] == "error" and m["tokens_in"] > 0 and ended["tokens_in"] >= m["tokens_in"]


@pytest.mark.parametrize("p, minimum, value", [(0.5, 0.5, True), (0.7, 0.7, True), (0.3, 0.7, False)])
def test_boundaries_half_and_the_minimum_itself(tmp_path, p, minimum, value):
    """p = 0.5 is true (p >= 0.5); a confidence equal to the minimum is not below it, so no fallback (the fallback is the other value)."""
    res, rec = run(runtime(tmp_path, overrides={"urgent": NOUL(p)}, edit=lambda t: t.replace(
        "min_confidence: {urgent: 0.7}", f"min_confidence: {{urgent: {minimum}}}").replace("fallback: {urgent: true}", f"fallback: {{urgent: {str(not value).lower()}}}")))
    assert res["status"] == "succeeded", res
    assert triage_value(rec)["urgent"]["value"] is value and not decide_event(rec)["body"]["meta"].get("fallback_used")


def test_engine_with_the_http_provider_records_no_key(tmp_path, monkeypatch):
    """The real provider path end to end (urlopen replaced, no socket): nothing recorded carries the key."""
    import io
    import json
    from packrt.fake.jev import respond
    from packrt.run import providers
    key = "fake-jev-key-in-the-env-123456"

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        assert req.get_header("Authorization") == f"Bearer {key}"
        return Resp(json.dumps(respond(body["state"], body["questions"], model=body["model"], overrides={"urgent": NOUL(0.9)})).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    rt = runtime(tmp_path)
    rt.provider = providers.HttpProvider(rt.models, {"JEV_API_KEY": key, "JEV_BASE_URL": "http://127.0.0.1:9"})
    res, rec = run(rt)
    assert res["status"] == "succeeded"
    assert key not in json.dumps(rec.events, ensure_ascii=False, default=str) and key not in json.dumps(rec.payloads, ensure_ascii=False, default=str)


def test_a_provider_without_decide_fails_the_node(tmp_path):
    rt = runtime(tmp_path)

    class ChatOnly:
        def chat(self, *a, **k):
            raise AssertionError("not called")
    rt.provider = ChatOnly()
    res, rec = run(rt)
    assert res["status"] == "failed" and "decide를 부를 수 없습니다" in str(res["error"])


def test_the_fixture_runs_end_to_end_and_writes_a_note(tmp_path):
    res, rec = run(runtime(tmp_path, overrides={"urgent": NOUL(0.2)}))
    assert res["status"] == "succeeded" and res["output"].startswith("# 대기열 — T-1")
