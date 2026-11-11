"""V-ONNX (EV-003 LS-3, CHG-P06 · C7): the onnx path with a fake onnxruntime and a fake numpy put in sys.modules inside each test
only (nothing installed). The fake session reads its model file as a JSON description: inputs, outputs, and the rows it answers.
Checked: ZipMap and array answers give the same result, a named output, one float input [N, fields] (dynamic dims as None or
text), the class names and row length, the 0 ~ 1 range, onnxruntime missing — model_load / model_output node errors."""
import hashlib
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from packrt.run.engine import Runtime

FIX = Path(__file__).resolve().parent / "fixtures" / "predict_min"
FIELDS = ["air_temp", "process_temp", "rpm", "torque", "tool_wear"]
CLASSES = ["NONE", "TWF", "HDF"]
EVENT = {"event_id": "E-1", "air_temp": 300, "process_temp": 310, "rpm": 1000, "torque": 60, "tool_wear": 50}
IN = [{"name": "X", "type": "tensor(float)", "shape": [None, 5]}]
OUTS = [{"name": "output_label"}, {"name": "output_probability"}]
FEEDS = []


class FakeArray:
    def __init__(self, data, dtype=None):
        self.data, self.dtype = data, dtype

    def tolist(self):
        return list(self.data)


class FakeRow(FakeArray):
    """A numpy row: answers tolist() like the real one."""


def fake_numpy():
    np = types.ModuleType("numpy")
    np.float32 = "float32"
    np.array = lambda data, dtype=None: FakeArray(data, dtype)
    return np


def fake_onnxruntime():
    ort = types.ModuleType("onnxruntime")

    class Arg:
        def __init__(self, d):
            self.name, self.type, self.shape = d["name"], d.get("type"), d.get("shape")

    class InferenceSession:
        def __init__(self, path, providers=None):
            self.desc = json.loads(Path(path).read_text(encoding="utf-8"))
            assert providers == ["CPUExecutionProvider"]

        def get_inputs(self):
            return [Arg(d) for d in self.desc["inputs"]]

        def get_outputs(self):
            return [Arg(d) for d in self.desc["outputs"]]

        def run(self, names, feed):
            FEEDS.append({k: (v.data, v.dtype) for k, v in feed.items()})
            return [[FakeRow(r) if self.desc.get("numpy_rows") and isinstance(r, list) else r for r in rows] for rows in self.desc["answers"]]

    ort.InferenceSession = InferenceSession
    return ort


@pytest.fixture(autouse=True)
def _restore_onnx_state():
    """EV-009: packrt sets ORT_DISABLE_TELEMETRY and a test may import a fake onnxruntime — both are put back after every test here."""
    import os
    env, mod = os.environ.get("ORT_DISABLE_TELEMETRY"), sys.modules.get("onnxruntime", _MISSING)
    yield
    if env is None:
        os.environ.pop("ORT_DISABLE_TELEMETRY", None)
    else:
        os.environ["ORT_DISABLE_TELEMETRY"] = env
    if mod is _MISSING:
        sys.modules.pop("onnxruntime", None)
    else:
        sys.modules["onnxruntime"] = mod


_MISSING = object()


@pytest.fixture
def fakes(monkeypatch):
    FEEDS.clear()
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "1")   # EV-009: the fake is already in sys.modules, as if imported with telemetry off
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_onnxruntime())
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy())


def make(tmp_path, answers, inputs=IN, outputs=OUTS, classes=CLASSES, output=None, numpy_rows=False):
    """The predict_min Pack with an onnx release whose model file describes the fake session."""
    work = tmp_path / "p"
    shutil.copytree(FIX, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    rel = tmp_path / "rel" / "models" / "mr-onnx-001"
    rel.mkdir(parents=True)
    (rel / "model.onnx").write_text(json.dumps({"inputs": inputs, "outputs": outputs, "answers": answers, "numpy_rows": numpy_rows}), encoding="utf-8")
    h = hashlib.sha256((rel / "model.onnx").read_bytes()).hexdigest()
    art = {"file": "model.onnx", "format": "onnx", "sha256": h, **({"output": output} if output else {})}
    man = {"release_id": "mr-onnx-001", "kind": "model", "artifact": art, "classes": classes,
           "feature_schema": {"version": 1, "fields": FIELDS}, "trusted": True, "files": {"model.onnx": h}}
    (rel / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: detect\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{tmp_path / "rel"}"]\nstores: {{}}\nmodels: {{failure_classifier: {{release: mr-onnx-001}}}}\n', encoding="utf-8")
    return work


def run(work):
    return Runtime(work).run(EVENT, review={"policy": "auto_approve"})


def zipmap(row):
    return [[["TWF"]], [row]]   # output_label, output_probability (a list of one dict per input row)


def test_zipmap_and_array_give_the_same_result(tmp_path, fakes):
    a = run(make(tmp_path / "a", zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    b = run(make(tmp_path / "b", [[["TWF"]], [[0.2, 0.7, 0.1]]]))
    c = run(make(tmp_path / "c", [[["TWF"]], [[0.2, 0.7, 0.1]]], numpy_rows=True))   # a numpy row: tolist()
    assert a["status"] == b["status"] == c["status"] == "succeeded"
    assert a["output"] == b["output"] == c["output"] == {"type": "TWF", "probability": 0.7}


def test_the_model_gets_one_float32_row_in_the_release_field_order(tmp_path, fakes):
    run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    assert FEEDS == [{"X": ([[300.0, 310.0, 1000.0, 60.0, 50.0]], "float32")}]


def test_zipmap_keys_may_be_class_ids(tmp_path, fakes):
    out = run(make(tmp_path, [[[1]], [{0: 0.25, 1: 0.75}]], classes=["0", "1"]))
    assert out["output"] == {"type": "1", "probability": 0.75}


def test_a_named_output_is_the_one_read(tmp_path, fakes):
    outs = [{"name": "probs"}, {"name": "label"}]
    out = run(make(tmp_path, [[[0.6, 0.3, 0.1]], [["NONE"]]], outputs=outs, output={"kind": "proba", "name": "probs"}))
    assert out["output"] == {"type": "NONE", "probability": 0.6}


@pytest.mark.parametrize("shape", [[None, 5], ["N", 5], [1, 5], [None, None], ["batch", "n_features"]])
def test_dynamic_dims_are_taken_as_none_or_text(tmp_path, fakes, shape):
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1}), inputs=[{**IN[0], "shape": shape}]))
    assert out["status"] == "succeeded", out


@pytest.mark.parametrize("inputs, outputs, output, needle", [
    (IN + [{"name": "Y", "type": "tensor(float)", "shape": [None, 1]}], OUTS, None, "onnx 모델 입력은 하나여야 합니다(받은 것: 2개)"),
    ([{**IN[0], "type": "tensor(int64)"}], OUTS, None, "onnx 모델 입력 형이 tensor(float)가 아닙니다: tensor(int64)"),
    ([{**IN[0], "shape": [None, 4]}], OUTS, None, "onnx 모델 입력 모양이 [N, 칸 수]가 아닙니다: [None, 4]"),
    ([{**IN[0], "shape": [5]}], OUTS, None, "onnx 모델 입력 모양이 [N, 칸 수]가 아닙니다: [5]"),
    (IN, OUTS, {"kind": "proba", "name": "scores"}, "onnx 모델에 출력 'scores'이 없습니다"),
])
def test_model_load_errors(tmp_path, fakes, inputs, outputs, output, needle):
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1}), inputs=inputs, outputs=outputs, output=output))
    assert out["status"] == "failed" and out["error"]["code"] == "model_load" and out["error"]["message"] == needle, out["error"]


@pytest.mark.parametrize("answers, needle", [
    (zipmap({"NONE": 0.2, "TWF": 0.8}), "onnx 확률 출력의 class가 manifest classes와 다릅니다: HDF"),
    (zipmap({"NONE": 0.2, "TWF": 0.7, "OSF": 0.1}), "onnx 확률 출력의 class가 manifest classes와 다릅니다: HDF, OSF"),
    (zipmap({"NONE": 0.2, "TWF": 0.6, "HDF": 0.1, "OSF": 0.1}), "onnx 확률 출력의 class가 manifest classes와 다릅니다: OSF"),   # a class only the model has (LS-3 review note)
    ([[["TWF"]], [[0.3, 0.7]]], "onnx 확률 출력 모양이 class 수와 다릅니다: 2"),
    ([[["TWF"]], [0.7]], "onnx 확률 출력 모양이 class 수와 다릅니다: float"),   # zipmap='columns'-like: one number per output
    ([[["TWF"]], []], "onnx 확률 출력 모양이 class 수와 다릅니다: 첫 행이 없음"),
    ([[["TWF"]], [[0.2, 1.2, -0.4]]], "onnx 확률 값이 0 ~ 1 밖입니다: TWF"),
    ([[["TWF"]], [[0.2, "x", 0.1]]], "onnx 확률 값이 0 ~ 1 밖입니다: TWF"),
])
def test_model_output_errors(tmp_path, fakes, answers, needle):
    out = run(make(tmp_path, answers))
    assert out["status"] == "failed" and out["error"]["code"] == "model_output" and out["error"]["message"] == needle, out["error"]


def test_a_tiny_rounding_overshoot_is_taken(tmp_path, fakes):
    out = run(make(tmp_path, [[["TWF"]], [[-0.0000004, 1.0000004, 0.0]]]))   # float noise inside the 1e-6 allowance
    assert out["output"] == {"type": "TWF", "probability": 1.0}   # rounded to 6 places: back inside the Prediction schema


def test_without_onnxruntime_the_node_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)   # EV-009 sets it before the import (put back by _restore_onnx_state)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)   # import onnxruntime raises ImportError
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    assert out["error"] == {"code": "model_load", "message": "onnx 모델에는 Pack 환경에 onnxruntime이 필요합니다"} | {k: out["error"][k] for k in out["error"] if k not in ("code", "message")}


def test_the_fakes_stay_inside_the_test(monkeypatch):
    before = (sys.modules.get("onnxruntime"), sys.modules.get("numpy"))
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "onnxruntime", fake_onnxruntime())
        m.setitem(sys.modules, "numpy", fake_numpy())
        assert sys.modules["onnxruntime"].__name__ == "onnxruntime"
    assert (sys.modules.get("onnxruntime"), sys.modules.get("numpy")) == before
    assert not isinstance(sys.modules.get("numpy"), types.ModuleType) or hasattr(sys.modules["numpy"], "__file__")   # no fake left behind


# ---- EV-009 (CHG-T01, D-311 · D-315): onnxruntime is opened with its telemetry off ----
TELEMETRY_STOPPED = "onnx 모델을 열기 전에 onnxruntime이 telemetry를 끄지 않은 채 이미 불러와졌습니다 — ORT_DISABLE_TELEMETRY=1을 먼저 두세요"


class _ImportHook:
    """A fake onnxruntime that is imported for real (not put in sys.modules first): records the environment at import time."""

    def __init__(self):
        self.env_at_import, self.disabled = [], 0

    def find_spec(self, name, path=None, target=None):
        import importlib.util
        return importlib.util.spec_from_loader(name, self) if name == "onnxruntime" else None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        import os
        self.env_at_import.append(os.environ.get("ORT_DISABLE_TELEMETRY"))
        fake = fake_onnxruntime()
        module.InferenceSession = fake.InferenceSession

        def disable_telemetry_events():
            self.disabled += 1
        module.disable_telemetry_events = disable_telemetry_events


@pytest.mark.parametrize("before", [None, "0", "1"])
def test_telemetry_is_off_when_onnxruntime_is_imported(tmp_path, monkeypatch, before):
    """V-T1: the variable is "1" at the moment onnxruntime is imported (whatever it was), and the API is called once."""
    FEEDS.clear()
    hook = _ImportHook()
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy())
    monkeypatch.setattr(sys, "meta_path", [hook] + sys.meta_path)
    if before is None:
        monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("ORT_DISABLE_TELEMETRY", before)
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    assert out["status"] == "succeeded", out
    assert hook.env_at_import == ["1"] and hook.disabled == 1
    import os
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"   # left set (D-311: off for the process)


def test_an_onnxruntime_imported_with_telemetry_on_is_not_used(tmp_path, monkeypatch):
    """V-T2 (가): already imported and the variable is not "1" → model_load with the contract's words; nothing is run."""
    FEEDS.clear()
    monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_onnxruntime())
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy())
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    assert out["status"] == "failed" and out["error"]["code"] == "model_load" and out["error"]["message"] == TELEMETRY_STOPPED, out["error"]
    assert FEEDS == []
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    assert run(make(tmp_path / "b", zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))["error"]["message"] == TELEMETRY_STOPPED


def test_an_onnxruntime_imported_with_telemetry_off_is_used(tmp_path, fakes):
    """V-T2: already imported while the variable was "1" → opened as before."""
    out = run(make(tmp_path, zipmap({"NONE": 0.2, "TWF": 0.7, "HDF": 0.1})))
    assert out["status"] == "succeeded" and out["output"]["type"] == "TWF"


def test_a_run_without_onnx_leaves_the_environment_alone(tmp_path, monkeypatch):
    """V-T1 · PRES-T02: a json-linear model never touches ORT_DISABLE_TELEMETRY."""
    import os
    monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    work = tmp_path / "p"
    shutil.copytree(FIX, work, ignore=shutil.ignore_patterns("expected-*.json"))
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: detect\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{work / "releases"}"]\nstores: {{}}\nmodels: {{failure_classifier: {{release: mr-min-001}}}}\n', encoding="utf-8")
    assert run(work)["status"] == "succeeded"
    assert "ORT_DISABLE_TELEMETRY" not in os.environ


def test_a_new_process_without_onnx_leaves_the_environment_alone(tmp_path):
    """EV-010 CHG-Q03 (CHG-T01 4 · PRES-T02) in a fresh process, so importing packrt itself is seen too: a json-linear predict run
    and a flat-local retrieve run never set ORT_DISABLE_TELEMETRY. The child keeps this environment (PYTHONPATH included) without
    that one variable."""
    import os
    import subprocess
    pw = tmp_path / "p"
    shutil.copytree(FIX, pw, ignore=shutil.ignore_patterns("expected-*.json"))
    (pw / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: detect\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{pw / "releases"}"]\nstores: {{}}\nmodels: {{failure_classifier: {{release: mr-min-001}}}}\n', encoding="utf-8")
    rfix = FIX.parent / "retrieve_min"
    rw = tmp_path / "r"
    shutil.copytree(rfix, rw, ignore=shutil.ignore_patterns("expected-*.json"))
    (rw / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: probe\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{rw / "releases"}"]\n'
        "stores:\n  docs: {backend: flat-local, release: ir-docs-v1, embedding: {mode: fake}}\n"
        "  rows: {backend: sqlite-file, release: tr-rows-v1}\nmodels: {}\n", encoding="utf-8")
    code = ("import json, os, sys\nfrom packrt.run.engine import Runtime\n"
            "before = os.environ.get('ORT_DISABLE_TELEMETRY')\n"
            "a = Runtime(sys.argv[1]).run(json.loads(sys.argv[3]), review={'policy': 'auto_approve'})\n"
            "b = Runtime(sys.argv[2], env={}).run(json.loads(sys.argv[4]), review={'policy': 'auto_approve'})\n"
            "print(json.dumps([before, a['status'], b['status'], 'onnxruntime' in sys.modules, os.environ.get('ORT_DISABLE_TELEMETRY')]))\n")
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    q = {"text": "공구 마모 점검 전에 전원을 내린다", "group": "TW", "groups": ["TW", "PW"], "rare": "XX"}
    out = subprocess.run([sys.executable, "-B", "-c", code, str(pw), str(rw), json.dumps(EVENT), json.dumps(q, ensure_ascii=False)],
                         capture_output=True, text=True, cwd=tmp_path, env=env, timeout=10)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [None, "succeeded", "succeeded", False, None]
