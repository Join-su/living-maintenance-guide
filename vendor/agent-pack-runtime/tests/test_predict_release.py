"""V-REL (EV-003 LS-1, CHG-P01 · P02 · P03 · P06): the model release check at stage 3 — json-linear · onnx only, no pickle-family
file or mark, feature fields required and matching the json-linear file, onnx classes, artifact.output kind proba, and the
optional threshold_config (shape, range, classes, canonical sha256). The checks that were there keep their text and come first."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from packrt.ir import load_pack, releases
from packrt.ir.releases import ReleaseError, check_model

FIX = Path(__file__).resolve().parent / "fixtures" / "predict_min"
VALUES = {"min_probability": 0.5, "normal_classes": ["NONE"]}


def canonical(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def release(tmp_path, manifest=None, model=None, artifact=None, extra=None, name="mr-min-001"):
    """A copy of mr-min-001 with the manifest · model file changed, the file hashes made right again."""
    folder = tmp_path / "rel" / "models" / name
    shutil.copytree(FIX / "releases" / "models" / "mr-min-001", folder)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if model is not None:
        (folder / m["artifact"]["file"]).write_text(json.dumps(model(json.loads((folder / "model.json").read_text()))), encoding="utf-8")
    for fname, data in (extra or {}).items():
        (folder / fname).write_bytes(data)
    if artifact is not None:
        art_name, data = artifact
        (folder / "model.json").unlink()
        (folder / art_name).write_bytes(data)
        m["artifact"]["file"] = art_name
    m["files"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.name != "manifest.json"}
    m["artifact"]["sha256"] = m["files"][m["artifact"]["file"]]
    m["release_id"] = name
    if manifest:
        manifest(m)
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return folder


def refused(folder, needle):
    with pytest.raises(ReleaseError) as e:
        check_model(folder)
    assert needle in str(e.value), str(e.value)
    return str(e.value)


def test_the_five_field_release_passes_and_binds_classes_and_fields(tmp_path):
    info = check_model(release(tmp_path))
    assert info["classes"] == ["OSF", "HDF", "PWF", "TWF", "NONE"]
    assert info["feature_fields"] == ["air_temp", "process_temp", "rpm", "torque", "tool_wear"]
    assert "threshold" not in info and info["format"] == "json-linear" and info["feature_schema_version"] == 1


# CHG-P01 — formats and pickle
@pytest.mark.parametrize("fmt", ["custom.loader:Pickled", "sklearn", None])
def test_only_json_linear_and_onnx(tmp_path, fmt):
    msg = refused(release(tmp_path, manifest=lambda m: m["artifact"].__setitem__("format", fmt)), f"형식 '{fmt}'는 받지 않습니다(json-linear · onnx만)")
    assert "pickle 계열과 '모듈:클래스'" in msg


@pytest.mark.parametrize("fname", ["model.pkl", "extra.PICKLE", "m.joblib"])
def test_a_pickle_family_file_in_the_release_is_refused(tmp_path, fname):
    refused(release(tmp_path, extra={fname: b"x"}), f"pickle 계열 파일이 있습니다: {fname}")


@pytest.mark.parametrize("proto", [2, 5])
def test_a_model_file_with_the_pickle_mark_is_refused(tmp_path, proto):
    folder = release(tmp_path, artifact=("model.onnx", bytes([0x80, proto]) + b"rest"),
                     manifest=lambda m: (m["artifact"].__setitem__("format", "onnx"), m.__setitem__("classes", ["a", "b"])))
    refused(folder, "모델 파일이 pickle 모양입니다")


def test_the_old_checks_keep_their_text_and_come_first(tmp_path):
    # untrusted and a refused format at once: the trusted check (there before) is the one that speaks
    refused(release(tmp_path, manifest=lambda m: (m.__setitem__("trusted", False), m["artifact"].__setitem__("format", "x:Y"))), "trusted: true가 아닙니다")
    folder = release(tmp_path / "b", manifest=lambda m: m["artifact"].__setitem__("format", "x:Y"))
    (folder / "model.json").write_text("{}", encoding="utf-8")   # hash no longer matches: the old text, before the format check
    refused(folder, "파일 hash가 manifest와 다릅니다")


# CHG-P02 — fields and shape
@pytest.mark.parametrize("schema", [None, {"version": 1}, {"version": 1, "fields": []}, {"version": 1, "fields": ["a", "a"]}, {"version": 1, "fields": ["a", ""]}])
def test_feature_fields_are_required(tmp_path, schema):
    refused(release(tmp_path, manifest=lambda m: m.__setitem__("feature_schema", schema)), "feature_schema.fields(모델 입력 칸 목록)가 없거나 형식이 틀립니다")


def test_the_json_linear_features_must_be_the_fields_in_order(tmp_path):
    refused(release(tmp_path, manifest=lambda m: m["feature_schema"].__setitem__("fields", ["process_temp", "air_temp", "rpm", "torque", "tool_wear"])),
            "모델 파일 features가 manifest feature_schema.fields와 다릅니다")


@pytest.mark.parametrize("change, needle", [
    (lambda d: "not json", "UTF-8 JSON이 아닙니다"),
    (lambda d: {**d, "classes": ["OSF"]}, "classes는 겹치지 않는 이름 2개 이상"),
    (lambda d: {**d, "weights": d["weights"][:-1]}, "weights는 class 수 × 칸 수"),
    (lambda d: {**d, "weights": [r[:-1] for r in d["weights"]]}, "weights는 class 수 × 칸 수"),
    (lambda d: {**d, "weights": [[True] * 5] + d["weights"][1:]}, "weights는 class 수 × 칸 수"),
    (lambda d: {**d, "bias": d["bias"][:2]}, "bias는 class 수만큼"),
    (lambda d: {**d, "features": "air_temp"}, "features가 목록이 아닙니다"),
])
def test_the_json_linear_file_shape(tmp_path, change, needle):
    folder = release(tmp_path, model=change)
    if needle == "UTF-8 JSON이 아닙니다":
        (folder / "model.json").write_text("not json", encoding="utf-8")
        m = json.loads((folder / "manifest.json").read_text())
        m["files"]["model.json"] = m["artifact"]["sha256"] = hashlib.sha256(b"not json").hexdigest()
        (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    refused(folder, needle)


def test_manifest_classes_must_match_the_json_linear_file(tmp_path):
    refused(release(tmp_path, manifest=lambda m: m.__setitem__("classes", ["NONE", "OSF", "HDF", "PWF", "TWF"])), "모델 파일 classes가 manifest classes와 다릅니다")
    assert check_model(release(tmp_path / "ok", manifest=lambda m: m.__setitem__("classes", ["OSF", "HDF", "PWF", "TWF", "NONE"])))["classes"][0] == "OSF"


def test_onnx_needs_classes(tmp_path):
    def onnx(m, classes=None):
        m["artifact"]["format"] = "onnx"
        if classes is not None:
            m["classes"] = classes
    refused(release(tmp_path, artifact=("model.onnx", b"\x08\x07onnx"), manifest=onnx), "(onnx)에 classes가 없습니다")
    refused(release(tmp_path / "b", artifact=("model.onnx", b"\x08\x07onnx"), manifest=lambda m: onnx(m, ["one"])), "(onnx)에 classes가 없습니다")
    info = check_model(release(tmp_path / "c", artifact=("model.onnx", b"\x08\x07onnx"), manifest=lambda m: onnx(m, ["0", "1"])))
    assert info["classes"] == ["0", "1"] and info["format"] == "onnx"


# CHG-P06 — artifact.output
@pytest.mark.parametrize("out, ok", [({"kind": "proba"}, True), ({"kind": "proba", "name": "probabilities"}, True), ({"kind": "score"}, False),
                                     ({"kind": "proba", "name": ""}, False), ({"kind": "proba", "extra": 1}, False), ("proba", False)])
def test_artifact_output_takes_probability_models_only(tmp_path, out, ok):
    folder = release(tmp_path, manifest=lambda m: m["artifact"].__setitem__("output", out))
    if ok:
        check_model(folder)
    else:
        refused(folder, "artifact.output은 {kind: proba, name?}만 받습니다 — 이 runtime은 확률 출력 모델만 받습니다")


# CHG-P03 — threshold_config
def with_threshold(values, sha=None):
    return lambda m: m.__setitem__("threshold_config", {"values": values, "sha256": sha if sha is not None else canonical(values)})


def test_a_right_threshold_config_is_bound_with_its_sha(tmp_path):
    info = check_model(release(tmp_path, manifest=with_threshold(VALUES)))
    assert info["threshold"] == {"min_probability": 0.5, "normal_classes": ["NONE"], "sha256": canonical(VALUES)}
    # the canonical form: sorted keys, no spaces, UTF-8 — the same bytes whatever order the manifest writes
    assert canonical(VALUES) == hashlib.sha256(b'{"min_probability":0.5,"normal_classes":["NONE"]}').hexdigest()


@pytest.mark.parametrize("tc, needle", [
    ({"values": VALUES}, "{sha256, values}만 받습니다"),
    ({"values": VALUES, "sha256": "x", "note": 1}, "{sha256, values}만 받습니다"),
    ("0.5", "{sha256, values}만 받습니다"),
    ({"values": {"min_probability": 0.5}, "sha256": "x"}, "values는 {min_probability, normal_classes}만"),
    ({"values": {**VALUES, "max": 1}, "sha256": "x"}, "values는 {min_probability, normal_classes}만"),
])
def test_threshold_config_shape(tmp_path, tc, needle):
    refused(release(tmp_path, manifest=lambda m: m.__setitem__("threshold_config", tc)), "threshold_config가 틀립니다: " + needle)


@pytest.mark.parametrize("values, needle", [
    ({"min_probability": 1.5, "normal_classes": ["NONE"]}, "min_probability는 0 ~ 1 숫자"),
    ({"min_probability": -0.1, "normal_classes": ["NONE"]}, "min_probability는 0 ~ 1 숫자"),
    ({"min_probability": True, "normal_classes": ["NONE"]}, "min_probability는 0 ~ 1 숫자"),
    ({"min_probability": "0.5", "normal_classes": ["NONE"]}, "min_probability는 0 ~ 1 숫자"),
    ({"min_probability": 0.5, "normal_classes": []}, "normal_classes는 겹치지 않는 class 이름 1개 이상"),
    ({"min_probability": 0.5, "normal_classes": ["NONE", "NONE"]}, "normal_classes는 겹치지 않는 class 이름 1개 이상"),
    ({"min_probability": 0.5, "normal_classes": ["OK"]}, "normal_classes에 없는 class가 있습니다: OK"),
    ({"min_probability": 0.5, "normal_classes": ["OSF", "HDF", "PWF", "TWF", "NONE"]}, "normal_classes가 모든 class입니다"),
])
def test_threshold_values(tmp_path, values, needle):
    refused(release(tmp_path, manifest=with_threshold(values)), needle)


@pytest.mark.parametrize("bound", [0, 1, 0.0, 1.0])
def test_the_range_ends_are_taken(tmp_path, bound):
    v = {"min_probability": bound, "normal_classes": ["NONE"]}
    assert check_model(release(tmp_path, manifest=with_threshold(v)))["threshold"]["min_probability"] == bound


def test_a_wrong_threshold_sha_is_refused(tmp_path):
    refused(release(tmp_path, manifest=with_threshold(VALUES, sha=canonical({**VALUES, "min_probability": 0.6}))), "threshold_config sha256이 values와 다릅니다")
    spaced = hashlib.sha256(json.dumps(VALUES).encode()).hexdigest()   # json.dumps with spaces is not the canonical form
    refused(release(tmp_path / "b", manifest=with_threshold(VALUES, sha=spaced)), "threshold_config sha256이 values와 다릅니다")


# through the execution config (stage 3): every model of the config is checked, the bindings only add keys
def config(tmp_path, models):
    work = tmp_path / "p"
    shutil.copytree(FIX, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: detect\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{tmp_path / "rel"}"]\nstores: {{}}\nmodels: {{{models}}}\n', encoding="utf-8")
    return work / "execution-config.yaml", load_pack(work).doc


def test_the_bindings_only_add_keys(tmp_path):
    release(tmp_path, manifest=with_threshold(VALUES))
    path, doc = config(tmp_path, "failure_classifier: {release: mr-min-001}")
    b = releases.load_execution_config(path, doc)["bindings"]["models"]["failure_classifier"]
    assert list(b)[:7] == ["path", "release_id", "manifest_sha256", "artifact_sha256", "format", "feature_schema_version", "classes"]
    assert set(b) == {"path", "release_id", "manifest_sha256", "artifact_sha256", "format", "feature_schema_version", "classes", "feature_fields", "threshold"}


def test_a_model_the_entrypoint_does_not_use_is_still_checked(tmp_path):
    release(tmp_path)
    release(tmp_path, name="mr-bad-001", manifest=lambda m: m["artifact"].__setitem__("format", "x.y:Z"))
    path, doc = config(tmp_path, "failure_classifier: {release: mr-min-001}, spare: {release: mr-bad-001}")
    with pytest.raises(ReleaseError, match="형식 'x.y:Z'는 받지 않습니다"):
        releases.load_execution_config(path, doc)
