"""EV-006 S5g · V-MANIFEST (CHG-R11 · R12, contract E, PRES-R12 · R13): release_line / model_release_id, data_release_id unchanged,
and the run manifest index entry that gains the recipe fields only for a release built from a recipe."""
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import packrt
from packrt import trace
from packrt.run.engine import Runtime

FIX = Path(__file__).resolve().parent / "fixtures"
FIVE = {"release_id", "manifest_sha256", "index_sha256", "embedding", "location"}


def retrieve_min_runtime(tmp_path):
    work = tmp_path / "p"
    shutil.copytree(FIX / "retrieve_min", work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    roots = tmp_path / "releases"
    shutil.copytree(FIX / "retrieve_min" / "releases", roots)
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: probe\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\n'
        "stores:\n  docs: {backend: flat-local, release: ir-docs-v1, embedding: {mode: fake}}\n"
        "  rows: {backend: sqlite-file, release: tr-rows-v1}\nmodels: {}\n", encoding="utf-8")
    return Runtime(work, env={})


def test_model_release_id():
    assert trace.model_release_id({}) is None
    a = trace.model_release_id({"failure": "mr-ai4i-lgbm-large-v1", "aux": "mr-x-v2"})
    assert re.fullmatch(r"mrs:[0-9a-f]{16}", a)
    assert a == trace.model_release_id({"aux": "mr-x-v2", "failure": "mr-ai4i-lgbm-large-v1"})   # order does not matter
    assert a != trace.model_release_id({"failure": "mr-ai4i-lgbm-large-v2", "aux": "mr-x-v2"})   # a new model release is a new id


def test_release_line_keeps_data_release_id(tmp_path):
    rt = retrieve_min_runtime(tmp_path)
    line = packrt.release_line(rt)
    assert line == trace.release_line(rt)
    stores = {k: v.get("release_id") for k, v in rt.bindings["stores"].items()}
    assert line["data_release_id"] == trace.data_release_id(stores)
    expected = json.loads((FIX / "retrieve_min" / "expected-e6f5a26.json").read_text(encoding="utf-8"))["run"]["log"]
    run_row = next(e["params"] for e in expected if "sql" in e and e["sql"].startswith("INSERT INTO trace.run"))
    assert line["data_release_id"] == run_row[8]   # the value 0.11.0 wrote in trace.run (PRES-R13)
    assert line == {"pack": {"id": "retrieve_min", "revision": rt.revision}, "packrt": packrt.__version__,
                    "vector_db": {"docs": "ir-docs-v1"}, "tables": {"rows": "tr-rows-v1"}, "models": {},
                    "data_release_id": run_row[8], "model_release_id": None}


def test_release_line_with_a_model():
    rt = SimpleNamespace(doc={"pack": {"id": "c1a15"}}, revision="sha256:abc",
                         bindings={"stores": {"manuals": {"type": "vector", "release_id": "ir-manuals-1f2e"}},
                                   "models": {"failure": {"release_id": "mr-ai4i-lgbm-large-v1"}}})
    line = trace.release_line(rt)
    assert line["models"] == {"failure": "mr-ai4i-lgbm-large-v1"}
    assert line["model_release_id"] == trace.model_release_id({"failure": "mr-ai4i-lgbm-large-v1"})
    assert line["data_release_id"] == trace.data_release_id({"manuals": "ir-manuals-1f2e"})   # models stay out of drs


def test_manifest_index_entry_has_recipe_fields_only_for_recipe_releases(tmp_path):
    rt = retrieve_min_runtime(tmp_path)
    assert set(rt.manifest()["evidence_index_releases"]["docs"]) == FIVE   # PRES-R12: no recipe, five keys
    import test_retrieve2_run as r2
    rt2 = Runtime(r2.make(tmp_path / "b", [r2.node("similar", "history", "sim", **r2.R3)]), env={})
    entry = rt2.manifest()["evidence_index_releases"]["history"]
    assert set(entry) == FIVE | {"corpus_release", "index_recipe", "index_recipe_sha256", "build_sha256", "builder"}
    assert entry["index_recipe_sha256"] == r2.recipe_sha256(json.loads((tmp_path / "b" / "pack" / "pack.yaml").read_text(encoding="utf-8"))["stores"]["history"])[1]
    assert entry["corpus_release"] == r2.CORPUS["history"] and entry["builder"] == {"name": "packrt.index", "version": 1}
