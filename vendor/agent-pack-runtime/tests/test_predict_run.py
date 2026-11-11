"""V-RUN (EV-003 LS-2, CHG-P03 · P04 · P05 · P07) and PRES-P03 · P04 · P05 · P08 · P13.
- Packs without predict run as with 3e88689: result, trace events, payloads, run manifest (BYTE after N-LIST).
- The five-field json-linear Pack keeps its state value and run manifest entry (BYTE); its predict node_ended meta keeps the
  0.10.1 keys and values in order, with only the new keys after them (PREFIX).
- Input values are checked (no 0 for a missing field), the release fields and the Pack features meet in Runtime.build,
  threshold_config gives alarm · threshold, the output schema picks the extra state keys (N1) and checks the value."""
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

import packrt
from packrt import probe
from packrt.fake.jev import FakeJevProvider
from packrt.run import providers
from packrt.run.engine import Runtime

FIX = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIX / "predict_min" / "expected-3e88689.json").read_text(encoding="utf-8"))
REL = FIX / "predict_min" / "releases"
KEY = "fake-openai-key-ls0-0123456789"
ENV = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
EVENT = {"event_id": "E-1", "air_temp": 300, "process_temp": 310, "rpm": 1000, "torque": 60, "tool_wear": 50}
MODELS = "failure_classifier: {release: mr-min-001}"
VALUES = {"min_probability": 0.5, "normal_classes": ["NONE"]}
PROBS = {"OSF": 0.355439, "HDF": 0.039384, "PWF": 0.321615, "TWF": 0.176506, "NONE": 0.107056}   # softmax(W·x + b) of mr-min-001 for EVENT, by hand
# N-LIST (preservation contract v3 head) — the same list as the run folder's evidence/scripts/characterize.py. execution_config.sha256
# is the hash of a config text holding the temp folder and this Python's path, so it goes with the temp prefix (ledger UF-P05)
DROP_KEYS = {"at", "span_id", "parent_span_id", "ms", "trace", "pack_env"}
VERSION_KEYS = {"runtime_version"} if packrt.__version__ != EXPECTED["packrt_version"] else set()   # LS-4: the version moves on purpose


def b(value):
    """BYTE: the JSON text in the order the runtime wrote it (dict == would not see key order)."""
    return json.dumps(value, ensure_ascii=False)


def norm(value, tmps):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in DROP_KEYS or k in VERSION_KEYS:
                continue
            if k == "runtime" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk != "digest" and not (VERSION_KEYS and kk == "version")}
            if k == "execution_config" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk != "sha256"}
            out[k] = norm(v, tmps)
        return out
    if isinstance(value, list):
        return [norm(v, tmps) for v in value]
    if isinstance(value, str):
        for t in tmps:
            value = value.replace(t, "<TMP>")
        return value
    return value


class Recorder:
    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append({"role": role, "value": value, "retention_class": retention_class})
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    body = {"id": "chatcmpl-ls0", "object": "chat.completion", "model": "gpt-4.1-mini-2025-04-14", "system_fingerprint": "fp_ls0",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps({"summary": "결제 두 번", "category": "billing", "confidence": 0.9}, ensure_ascii=False), "refusal": None}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}
    return Resp(json.dumps(body).encode())


def prepare(tmp, name, entry, models="", releases=None, edit=None, manifest=None, model=None):
    work = Path(tmp) / "p"
    shutil.copytree(FIX / name, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    if edit:
        for rel, change in edit.items():
            f = work / rel
            f.write_text(change(f.read_text(encoding="utf-8")), encoding="utf-8")
    roots = Path(tmp) / "releases"
    if releases:
        shutil.copytree(releases, roots)
    else:
        roots.mkdir()
    if manifest or model:   # change the model release (threshold_config, the model file ...), keep its hashes right
        mf = roots / "models" / "mr-min-001" / "manifest.json"
        m = json.loads(mf.read_text(encoding="utf-8"))
        if model:
            (mf.parent / "model.json").write_text(json.dumps(model), encoding="utf-8")
            m["files"]["model.json"] = m["artifact"]["sha256"] = hashlib.sha256((mf.parent / "model.json").read_bytes()).hexdigest()
        if manifest:
            manifest(m)
        mf.write_text(json.dumps(m), encoding="utf-8")
    (work / "execution-config.yaml").write_text(
        f"config_version: 1\nentrypoint: {entry}\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{{models}}}\n', encoding="utf-8")
    return work


def run_pack(name, entry, value, make_rt, models="", releases=None, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        work = prepare(tmp, name, entry, models, releases, **kw)
        rt = make_rt(work)
        rec = Recorder()
        res = rt.run(value, sink=rec, review={"policy": "auto_approve"})
        tmps = sorted({tmp, os.path.realpath(tmp)}, key=len, reverse=True)
        return norm({"result": res, "events": rec.events, "payloads": rec.payloads, "manifest": rt.manifest()}, tmps)


def http_rt(work):
    rt = Runtime(work, env=ENV)
    rt.provider = providers.HttpProvider(rt.models, ENV)
    return rt


def predict(value=EVENT, **kw):
    return run_pack("predict_min", "detect", value, lambda w: Runtime(w), MODELS, REL, **kw)


def ended(out):
    return next(e for e in out["events"] if e["type"] == "node_ended" and e["node_id"] == "predict_failure")["body"]


def with_threshold(values=VALUES):
    sha = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return lambda m: m.__setitem__("threshold_config", {"values": values, "sha256": sha})


def prediction_schema(*extra):
    props = {"type": {"type": "string"}, "probability": {"type": "number", "minimum": 0, "maximum": 1},
             "probabilities": {"type": "object"}, "model_release": {"type": "string"}, "alarm": {"type": "boolean"}, "threshold": {"type": "number"}}
    keep = ("type", "probability") + extra
    doc = {"title": "Prediction", "type": "object", "required": list(keep), "properties": {k: props[k] for k in keep}}
    return {"schemas/prediction.json": lambda _: json.dumps(doc)}


# PRES-P03 · P04 — Packs without predict: BYTE
def test_runs_without_predict_are_unchanged(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got = {"run_decide_min": run_pack("decide_min", "triage", {"id": "T-1", "text": "결제가 두 번 됐어요", "channel": "web"}, lambda w: Runtime(w, provider=FakeJevProvider())),
           "run_llm_min": run_pack("llm_min", "ask", {"id": "T-1", "text": "결제가 두 번 됐어요"}, http_rt)}
    for k, v in got.items():
        want = norm(EXPECTED[k], [])
        assert b(v["result"]) == b(want["result"]) and b(v["manifest"]) == b(want["manifest"]) and v["manifest"]["model_releases"] == {}
        assert b(v["events"]) == b(want["events"]) and b(v["payloads"]) == b(want["payloads"]), k
    assert KEY not in json.dumps(got, ensure_ascii=False)


# PRES-P05 · P13 — the five-field json-linear Pack: state value and manifest BYTE, predict meta PREFIX
def test_five_field_json_linear_keeps_its_value():
    got, want = predict(), norm(EXPECTED["run_predict_min"], [])
    assert b(got["result"]) == b(want["result"]) and b(got["result"]["output"]) == b({"type": "OSF", "probability": 0.355439})
    assert b(got["manifest"]) == b(want["manifest"])   # model_releases.failure_classifier: no threshold_config → same keys, values, order
    new, old = ended(got)["meta"], ended(want)["meta"]
    assert list(new)[:len(old)] == list(old) and all(new[k] == old[k] for k in old)
    assert list(new)[len(old):] == ["probabilities"]
    assert new["probabilities"] == PROBS
    meta_free = lambda evs: [{**e, "body": {**e["body"], "meta": None}} if e["type"] == "node_ended" and e["node_id"] == "predict_failure" else e for e in evs]
    assert b(meta_free(got["events"])) == b(meta_free(want["events"])) and b(got["payloads"]) == b(want["payloads"])


# CHG-P05 — input values
LOOSE = {"schemas/event.json": lambda t: json.dumps({**json.loads(t), "properties": {k: {} for k in json.loads(t)["properties"]}})}   # past the input check


@pytest.mark.parametrize("change, code, needle", [
    (lambda e: {k: v for k, v in e.items() if k != "tool_wear"}, "feature_missing", "모델 입력 칸이 없습니다: tool_wear"),
    (lambda e: {**e, "rpm": None, "torque": None}, "feature_missing", "모델 입력 칸이 없습니다: rpm, torque"),
    (lambda e: {**e, "torque": "60"}, "feature_not_number", "모델 입력 칸이 숫자가 아닙니다: torque(str)"),
    (lambda e: {**e, "torque": True, "rpm": float("nan")}, "feature_not_number", "모델 입력 칸이 숫자가 아닙니다: rpm(float), torque(bool)"),
    (lambda e: {**e, "air_temp": float("inf")}, "feature_not_number", "air_temp(float)"),
    (lambda e: {**e, "rpm": 10 ** 400}, "feature_not_number", "rpm(int)"),   # too large for a float: not a crash (E5 review)
    (lambda e: {k: v for k, v in e.items() if k != "rpm"} | {"torque": "x"}, "feature_missing", "모델 입력 칸이 없습니다: rpm"),   # missing speaks first
])
def test_input_values_are_checked(change, code, needle):
    out = predict(change(EVENT), edit=LOOSE)
    err = out["result"]["error"]
    assert out["result"]["status"] == "failed" and err["code"] == code and needle in err["message"], err
    assert "60" not in err["message"] or code != "feature_not_number" or "torque(str)" in err["message"]   # the value itself is not written


def test_the_missing_field_case_no_longer_runs_with_zero():
    assert EXPECTED["run_predict_min_missing_field"]["result"]["output"] == {"type": "PWF", "probability": 0.429637}   # what 3e88689 did
    assert predict({k: v for k, v in EVENT.items() if k != "tool_wear"})["result"]["error"]["code"] == "feature_missing"


def test_a_field_mapping_feeds_the_model_in_the_release_order():
    mapping = "{tool_wear: \"{state.event.tool_wear}\", torque: \"{state.event.torque}\", rpm: \"{state.event.rpm}\", process_temp: \"{state.event.process_temp}\", air_temp: \"{state.event.air_temp}\", note: x}"
    out = predict(edit={"pack.yaml": lambda t: t.replace("features: {from: event}", f"features: {mapping}")})
    assert out["result"]["output"] == {"type": "OSF", "probability": 0.355439}


# CHG-P04 — Runtime.build
def test_build_refuses_a_mapping_without_a_release_field():
    mapping = "{air_temp: \"{state.event.air_temp}\", process_temp: 1, rpm: 1, tool_wear: 1}"
    out = predict(edit={"pack.yaml": lambda t: t.replace("features: {from: event}", f"features: {mapping}")})
    assert out["result"]["error"] == {"code": "load_failed", "message": "node 'predict_failure'의 features에 모델 입력 칸이 없습니다: torque (모델 release mr-min-001)"}
    assert out["result"]["detail"]["stage"] == "load"


def test_build_refuses_a_from_schema_without_a_release_field():
    drop = lambda t: json.dumps({**json.loads(t), "properties": {k: v for k, v in json.loads(t)["properties"].items() if k != "rpm"}})
    out = predict(edit={"schemas/event.json": drop})
    assert out["result"]["error"]["message"] == "node 'predict_failure'의 features에 모델 입력 칸이 없습니다: rpm (모델 release mr-min-001)"


def test_build_skips_a_from_state_that_is_not_a_schema():
    out = predict(edit={"pack.yaml": lambda t: t.replace("event: Event\n", "event: Event\n      raw: dict\n").replace("features: {from: event}", "features: {from: raw}")})
    err = out["result"]["error"]   # no build error: the run reaches the node, and raw is not set
    assert err["code"] == "features_not_object" and err["message"] == "predict features가 객체가 아닙니다"


def test_build_needs_threshold_config_for_alarm():
    out = predict(edit=prediction_schema("alarm"))
    assert out["result"]["error"]["message"] == "node 'predict_failure'의 output Prediction이 alarm · threshold를 쓰는데 모델 release mr-min-001에 threshold_config가 없습니다"


def test_the_probe_reports_the_build_error(tmp_path):
    mapping = "{air_temp: 1, process_temp: 1, rpm: 1, tool_wear: 1}"
    work = prepare(tmp_path, "predict_min", "detect", MODELS, REL, edit={"pack.yaml": lambda t: t.replace("features: {from: event}", f"features: {mapping}")})
    assert probe.main(str(work)) == {"error": "build", "message": "ReleaseError: node 'predict_failure'의 features에 모델 입력 칸이 없습니다: torque (모델 release mr-min-001)"}
    ok = prepare(tmp_path / "ok", "predict_min", "detect", MODELS, REL)
    assert "error" not in probe.main(str(ok))


# CHG-P03 · P07 — alarm, threshold, N1, output check, records
@pytest.mark.parametrize("values, alarm", [
    ({"min_probability": 0.5, "normal_classes": ["NONE"]}, False),    # OSF 0.355439 < 0.5: a fault type, not sure enough
    ({"min_probability": 0.3, "normal_classes": ["NONE"]}, True),     # OSF ≥ 0.3
    ({"min_probability": 0.355439, "normal_classes": ["NONE"]}, True),  # the bound itself counts
    ({"min_probability": 0.1, "normal_classes": ["OSF"]}, False),     # the top class is a normal one
])
def test_alarm_and_threshold(values, alarm):
    out = predict(edit=prediction_schema("alarm", "threshold"), manifest=with_threshold(values))
    assert out["result"]["output"] == {"type": "OSF", "probability": 0.355439, "alarm": alarm, "threshold": values["min_probability"]}
    meta = ended(out)["meta"]
    assert list(meta)[-3:] == ["probabilities", "alarm", "threshold"] and meta["alarm"] is alarm
    sha = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    assert out["manifest"]["model_releases"]["failure_classifier"]["threshold_config_sha256"] == sha


def test_the_output_schema_picks_the_extra_state_keys():
    out = predict(edit=prediction_schema("probabilities", "model_release"), manifest=with_threshold())
    assert out["result"]["output"] == {"type": "OSF", "probability": 0.355439, "probabilities": PROBS,
                                       "model_release": "mr-min-001"}   # alarm · threshold exist but the schema does not name them
    plain = predict(manifest=with_threshold())   # the 3e88689 schema: the 3e88689 value, the threshold only in the record
    assert plain["result"]["output"] == {"type": "OSF", "probability": 0.355439} and ended(plain)["meta"]["alarm"] is False


def test_the_value_is_checked_against_the_output_schema():
    strict = lambda t: json.dumps({**json.loads(t), "properties": {**json.loads(t)["properties"], "type": {"enum": ["NONE"]}}})
    out = predict(edit={"schemas/prediction.json": strict})
    assert out["result"]["error"]["code"] == "output_schema"
    assert out["result"]["error"]["message"].startswith("예측 출력이 Prediction 형식에 맞지 않습니다: 'OSF' is not one of")


def test_the_new_record_keys_hold_numbers_and_flags_only():
    meta = ended(predict(manifest=with_threshold()))["meta"]
    assert all(isinstance(v, float) for v in meta["probabilities"].values()) and isinstance(meta["alarm"], bool) and isinstance(meta["threshold"], float)
    assert math.isclose(sum(meta["probabilities"].values()), 1.0, abs_tol=1e-5)


def test_the_class_is_picked_before_rounding():
    # two classes 2.5e-7 apart: both round to 0.5. 0.10.1 picks the larger one before rounding (B), not the first after (A)
    model = {"classes": ["A", "B"], "features": ["air_temp", "process_temp", "rpm", "torque", "tool_wear"], "weights": [[0] * 5, [0] * 5], "bias": [0, 1e-6]}
    out = predict(model=model, manifest=lambda m: m["feature_schema"].__setitem__("fields", model["features"]))
    assert out["result"]["output"] == {"type": "B", "probability": 0.5}
