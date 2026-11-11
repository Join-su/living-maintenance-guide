"""V-RUN stage 2 (EV-003 LS-2, CHG-P04) and PRES-P01 · P02: a predict node names an output schema, its features are {from: <state
key>} alone or a field mapping; Packs that were right load as with 3e88689 (errors, warnings, revision — expected-3e88689.json)."""
import json
import shutil
from pathlib import Path

import pytest

from packrt.ir import load_pack

FIX = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIX / "predict_min" / "expected-3e88689.json").read_text(encoding="utf-8"))
NODE = "{id: predict_failure, kind: predict@1, params: {model: failure_classifier, features: {from: event}, output: Prediction}, reads: [event], writes: [prediction]}"


def load(name):
    r = load_pack(FIX / name)
    return {"errors": [[p["stage"], p["path"], p["message"]] for p in r.errors], "warnings": [[p["stage"], p["path"], p["message"]] for p in r.warnings],
            "revision": r.revision}


@pytest.mark.parametrize("name", ["task10_min", "decide_min", "llm_min", "predict_min"])
def test_load_results_are_unchanged(name):
    assert load(name) == EXPECTED["load"][name]   # BYTE: errors, warnings, revision as with 3e88689


def edited(tmp_path, node):
    work = tmp_path / "p"
    shutil.copytree(FIX / "predict_min", work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    assert NODE in text
    (work / "pack.yaml").write_text(text.replace(NODE, node), encoding="utf-8")
    return load_pack(work)


@pytest.mark.parametrize("node, needle", [
    (NODE.replace("output: Prediction", "output: Predction"), "node 'predict_failure'의 output 'Predction'이 schemas에 없습니다"),
    (NODE.replace("features: {from: event}", "features: {from: event, air_temp: 1}"), "node 'predict_failure'의 features는 {from: <state 키>} 하나이거나 칸 이름 mapping입니다"),
    (NODE.replace("features: {from: event}", "features: {from: sensor}"), "node 'predict_failure'의 features.from 'sensor'가 state에 없습니다"),
    (NODE.replace("features: {from: event}", "features: {from: \"{state.event}\"}"), "features.from '{state.event}'가 state에 없습니다"),
    (NODE.replace("features: {from: event}", "features: {}"), "node 'predict_failure'의 features가 비었습니다"),
    (NODE.replace("features: {from: event}", "features: {from: [event]}"), "node 'predict_failure'의 features.from '['event']'가 state에 없습니다"),   # not text: an error, not a crash (E5 review)
    (NODE.replace("features: {from: event}", "features: {from: {a: 1}}"), "node 'predict_failure'의 features.from '{'a': 1}'가 state에 없습니다"),
])
def test_stage_2_predict_checks(tmp_path, node, needle):
    r = edited(tmp_path, node)
    assert not r.ok and any(needle in e["message"] and e["stage"] == "semantic" for e in r.errors), r.errors


def test_a_field_mapping_loads(tmp_path):
    mapping = "{air_temp: \"{state.event.air_temp}\", process_temp: \"{state.event.process_temp}\", rpm: 1500, torque: \"{state.event.torque}\", tool_wear: \"{state.event.tool_wear}\"}"
    r = edited(tmp_path, NODE.replace("features: {from: event}", f"features: {mapping}"))
    assert r.ok, r.errors
