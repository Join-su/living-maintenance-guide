"""EV-006 AF-01 · V-E2E (first end-to-end flow, fake mode, flat-local; contract v5 C · E, D-277): the wheel changes a manual DB's
chunk_size · overlap → the config check says index_missing (with the build hash to make) → `python -m packrt.index build --embedding
fake` makes that vector DB version (the same bytes as the H0 sample) → it is added to the store's `releases` → a retrieve@2 run binds
it and its record carries method · index_recipe_sha256 · section-based locators, and the run manifest carries the recipe fields.
packrt.index (the h side) is part of 0.12.0, so this is a plain import (no skip)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import packrt
import packrt.index.build  # noqa: F401  (the builder the subprocess runs; fails loudly if it ever goes)
import tracewriter
from packrt.ir.releases import IndexMissing, dir_hash
from packrt.run.engine import Runtime

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
BASE, BUILT = "ir-manuals-bb52ba51", "ir-manuals-7dd35828"   # the H0 sample: whole sections / chunk 40 · overlap 8
CTX = {"run_id": "run_01JC00000000000000000AF01E", "trace_id": "0123456789abcdef0123456789aaf01e", "origin": "evaluation",
       "evaluation_run_id": "af01", "deployment_id": None, "retention_class": "dev"}


def env():
    roots = [str(Path(packrt.__file__).resolve().parents[1]), str(Path(tracewriter.__file__).resolve().parents[1])]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(roots), "PYTHONDONTWRITEBYTECODE": "1"}


def pack(tmp_path, node_index):
    p = tmp_path / "pack"
    (p / "schemas").mkdir(parents=True)
    for n, body in (("q.json", {"type": "object"}), ("hit.json", {"type": "object"}), ("note.json", {"type": "string"})):
        (p / "schemas" / n).write_text(json.dumps(body), encoding="utf-8")
    (p / "templates").mkdir()
    (p / "templates" / "n.md").write_text("done\n", encoding="utf-8")
    find = {"id": "find_manuals", "kind": "retrieve@2", "reads": ["q"], "writes": ["manual"],
            "params": {"store": "manuals", "query": "{state.q.text}", "filter": {"group": "{state.q.group}"}, "method": "hybrid",
                       "index": node_index, "top_k": 3}}
    doc = {"ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__}, "pack": {"id": "af01", "version": "V0", "title": "AF-01"},
           "env": {"required": [], "optional": []},
           "stores": {"manuals": {"type": "vector", "index": {"embedding": {"provider": "openai", "model": "text-embedding-3-small"},
                                                             "split": "section", "metadata": ["group", "kind"]},
                                  "metadata_required": ["group", "kind"], "filterable": ["group"]}},
           "schemas": {"Q": "schemas/q.json", "Hit": "schemas/hit.json", "Note": "schemas/note.json"},
           "graphs": {"start": {"state": {"q": "Q", "manual": {"type": "list[Hit]"}, "note": "Note"},
                                "nodes": [find, {"id": "fin", "kind": "render@1", "params": {"template": "templates/n.md", "format": "md", "to": "note"},
                                                 "reads": ["q"], "writes": ["note"]}],
                                "edges": [["START", "find_manuals"], ["find_manuals", "fin"], ["fin", "END"]]}},
           "entrypoints": [{"graph": "start", "input": "Q", "output": "Note"}], "trace": {"contract": 1}}
    (p / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def config(p, roots, releases=None):
    extra = f", releases: [{', '.join(releases)}]" if releases else ""
    (p / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: start\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: [{", ".join(json.dumps(str(r)) for r in roots)}]\n'
        f"stores:\n  manuals: {{backend: flat-local, release: {BASE}, embedding: {{mode: fake}}{extra}}}\nmodels: {{}}\n", encoding="utf-8")


def test_af01_wheel_chunk_change_builds_and_runs(tmp_path):
    indexes, built = tmp_path / "indexes", tmp_path / "built"
    shutil.copytree(H0 / "indexes" / BASE, indexes / BASE)
    corpus = H0 / "corpus" / "mc-h0-manuals-v1"
    want = json.loads((H0 / "indexes" / BUILT / "manifest.json").read_text(encoding="utf-8"))

    # 1. the wheel's candidate: chunk_size 40 · overlap 8 on the manual DB → no version of that recipe yet
    p = pack(tmp_path, {"chunk_size": 40, "overlap": 8})
    config(p, [indexes])
    with pytest.raises(IndexMissing) as e:
        Runtime(p, env={})
    missing = e.value.as_dict()
    assert missing == {"store": "manuals", "node": "find_manuals", "index_recipe_sha256": want["index_recipe_sha256"], "build_sha256": want["build_sha256"]}
    probe = json.loads(subprocess.run([sys.executable, "-m", "packrt.probe", str(p)], capture_output=True, text=True, env=env(), check=True).stdout)
    assert probe["index_missing"] == [missing]

    # 2. build it (fake embedding) — the same bytes as the H0 sample; again = exists
    cmd = [sys.executable, "-m", "packrt.index", "build", "--pack", str(p), "--graph", "start", "--node", "find_manuals",
           "--corpus", str(corpus), "--out", str(built), "--embedding", "fake", "--json"]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env())
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert (res["status"], res["release_id"], res["build_sha256"], res["index_recipe_sha256"], res["embed_calls"]) == \
        ("built", BUILT, want["build_sha256"], want["index_recipe_sha256"], 0)
    assert dir_hash(built / BUILT) == dir_hash(H0 / "indexes" / BUILT) == want["index_sha256"]
    again = json.loads(subprocess.run(cmd, capture_output=True, text=True, env=env()).stdout)
    assert again["status"] == "exists" and again["release_id"] == BUILT

    # 3. add it to the store's candidates → the run binds it
    config(p, [indexes, built], releases=[BUILT])
    rt = Runtime(p, env={})
    assert rt.bindings["stores"]["manuals"]["release_id"] == BUILT
    res = rt.run({"text": "공구 마모 폭 교체", "group": "TW"}, context=dict(CTX), review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res

    # 4. what the run manifest carries (W-2 reads it; the node record is test_af01_run_record_fields)
    entry = rt.manifest()["evidence_index_releases"]["manuals"]
    assert entry["release_id"] == BUILT and entry["index_recipe_sha256"] == want["index_recipe_sha256"]
    assert entry["corpus_release"] == want["corpus_release"] and entry["build_sha256"] == want["build_sha256"]
    assert entry["builder"] == {"name": "packrt.index", "version": 1} and entry["index_recipe"]["chunk_size"] == 40


def test_af01_run_record_fields(tmp_path):
    """The node record of the run in step 3: method · index_recipe_sha256 · section-based locators (D-277) · ranks."""
    indexes = tmp_path / "indexes"
    shutil.copytree(H0 / "indexes" / BASE, indexes / BASE)
    shutil.copytree(H0 / "indexes" / BUILT, indexes / BUILT)
    want = json.loads((H0 / "indexes" / BUILT / "manifest.json").read_text(encoding="utf-8"))
    p = pack(tmp_path, {"chunk_size": 40, "overlap": 8})
    config(p, [indexes], releases=[BUILT])
    rt = Runtime(p, env={})
    events = []

    class Sink:
        def put_payload(self, value, role, media_type="application/json", **_):
            return {"ref": "sha256:" + "0" * 64, "size": 0, "media_type": media_type}

        def emit(self, event):
            events.append(event)

    res = rt.run({"text": "공구 마모 폭 교체", "group": "TW"}, context=dict(CTX), sink=Sink(), review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    meta = next(e["body"]["meta"] for e in events if e["type"] == "node_ended" and e["node_id"] == "find_manuals")
    assert meta["kind"] == "retrieve@2" and meta["method"] == "hybrid" and meta["release_id"] == BUILT
    assert meta["index_recipe_sha256"] == want["index_recipe_sha256"]
    chunks = {json.loads(l)["id"]: json.loads(l) for l in (H0 / "indexes" / BUILT / "chunks.jsonl").read_text(encoding="utf-8").splitlines()}
    assert meta["results"] and all(r["locator"] == chunks[r["id"]]["locator"] and r["locator"]["section"] for r in meta["results"])
    assert all(set(r["ranks"]) == {"lexical", "vector", "fused"} for r in meta["results"])
    print("AF01_RESULT", json.dumps(meta["results"][0], ensure_ascii=False, sort_keys=True))
