"""EV-006 S3a · V-RECIPE (CHG-R05, contract C · D-267): the index recipe of a retrieve@2 node and its hashes give the H0 sample's values
(tests/fixtures/index_h0); key order, metadata order and repeats do not change a hash, any one value does; metadata falls back to
metadata_required (C-3). index_recipe_of takes a Pack folder or a checked pack document."""
import copy
import json
from pathlib import Path

import pytest

import packrt
from packrt.index import (BUILDER, RECIPE_KEYS, RecipeError, build_sha256, index_recipe, index_recipe_of, index_recipe_sha256,
                          release_id)
from packrt.ir import load_pack

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
SENSORS = ["torque_nm", "tool_wear_min"]
EMB = {"provider": "openai", "model": "text-embedding-3-small"}
BASE = {
    "ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__},
    "pack": {"id": "r2", "version": "V0", "title": "index recipe"}, "env": {"required": [], "optional": []},
    "stores": {
        "manuals": {"type": "vector", "index": {"embedding": EMB, "split": "section", "metadata": ["kind", "group"]}},
        "history": {"type": "vector", "index": {"embedding": EMB, "split": "record",
                                                 "metadata": ["symptom_group", "source", "torque_nm", "tool_wear_min"],
                                                 "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}}},
        "rows": {"type": "table", "schema": "Row", "key": "row_id"},
    },
    "schemas": {"Query": "schemas/query.json", "Hit": "schemas/hit.json", "Row": "schemas/row.json"},
    "graphs": {"start": {
        "state": {"q": "Query", "manual": {"type": "list[Hit]"}, "similar": {"type": "list[Hit]"}, "rows": {"type": "list[Row]"}},
        "nodes": [
            {"id": "find_manuals", "kind": "retrieve@2", "reads": ["q"], "writes": ["manual"],
             "params": {"store": "manuals", "query": "{state.q.text}", "top_k": 4}},
            {"id": "find_similar", "kind": "retrieve@2", "reads": ["q"], "writes": ["similar"],
             "params": {"store": "history", "query": "{state.q.text}", "method": "hybrid", "top_k": 10,
                        "rerank": {"fields": SENSORS, "query": "{state.q.sensors}", "weights": {"retrieval": 0.5, "sensor": 0.5}}}},
            {"id": "find_rows", "kind": "retrieve@1", "reads": ["q"], "writes": ["rows"],
             "params": {"store": "rows", "where": {"group": "{state.q.group}"}, "limit": 2}},
        ],
        "edges": [["START", "find_manuals"], ["find_manuals", "find_similar"], ["find_similar", "find_rows"], ["find_rows", "END"]]}},
    "entrypoints": [{"graph": "start", "input": "Query", "output": "Hit"}],
    "trace": {"contract": 1},
}
SCHEMAS = {"query.json": {"type": "object"}, "hit.json": {"type": "object"}, "row.json": {"type": "object"}}


def make(tmp_path, change=None, name="p"):
    doc = copy.deepcopy(BASE)
    if change:
        change(doc)
    d = tmp_path / name
    (d / "schemas").mkdir(parents=True)
    for f, body in SCHEMAS.items():
        (d / "schemas" / f).write_text(json.dumps(body), encoding="utf-8")
    (d / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d


def manual_index(value):
    def change(doc):
        doc["graphs"]["start"]["nodes"][0]["params"]["index"] = value
    return change


def h0(name):
    return json.loads((H0 / "indexes" / name / "manifest.json").read_text(encoding="utf-8"))


def test_the_test_pack_loads(tmp_path):
    res = load_pack(make(tmp_path))
    assert res.ok, res.errors


@pytest.mark.parametrize("change, node, sample", [
    (None, "find_manuals", "ir-manuals-bb52ba51"),
    (manual_index({"chunk_size": 40, "overlap": 8}), "find_manuals", "ir-manuals-7dd35828"),
    (None, "find_similar", "ir-history-c6510147"),
])
def test_recipe_and_hashes_are_the_h0_values(tmp_path, change, node, sample):
    m = h0(sample)
    recipe = index_recipe_of(make(tmp_path, change), "start", node)
    assert recipe == m["index_recipe"] and list(recipe) == list(RECIPE_KEYS)
    assert index_recipe_sha256(recipe) == m["index_recipe_sha256"]
    bsha = build_sha256(m["corpus_release"], index_recipe_sha256(recipe), m["embedding"])
    assert bsha == m["build_sha256"] and release_id(m["store"], bsha) == sample
    assert BUILDER == m["builder"]


def test_a_checked_pack_document_gives_the_same_recipe(tmp_path):
    folder = make(tmp_path)
    assert index_recipe_of(load_pack(folder).doc, "start", "find_similar") == index_recipe_of(folder, "start", "find_similar")


def test_key_order_metadata_order_and_repeats_do_not_change_the_hash():
    store = BASE["stores"]["manuals"]
    a = index_recipe(store, {"chunk_size": 40, "overlap": 8})
    b = index_recipe({"type": "vector", "index": {"metadata": ["group", "kind", "group"], "split": "section", "embedding": EMB}},
                     {"overlap": 8, "chunk_size": 40})
    assert a == b and index_recipe_sha256(a) == index_recipe_sha256(dict(reversed(list(a.items()))))


def test_overlap_zero_is_the_default():
    store = BASE["stores"]["manuals"]
    assert index_recipe_sha256(index_recipe(store, {})) == index_recipe_sha256(index_recipe(store, {"overlap": 0}))


@pytest.mark.parametrize("key, value", [
    ("chunk_size", 41), ("overlap", 9), ("split", "chars"), ("metadata", ["group"]),
    ("rerank_norm", {"fields": ["torque_nm"], "from": {"source": "generated"}}), ("chunker", "custom.cut:split"),
])
def test_any_one_value_changes_the_hash(key, value):
    base = index_recipe(BASE["stores"]["manuals"], {"chunk_size": 40, "overlap": 8})
    other = dict(base, **{key: value})
    assert index_recipe_sha256(other) != index_recipe_sha256(base)


def test_build_hash_follows_corpus_embedding_and_builder_but_not_lineage():
    m = h0("ir-history-543e3885")
    args = (m["corpus_release"], m["index_recipe_sha256"], m["embedding"])
    assert build_sha256(*args) == m["build_sha256"]
    assert build_sha256(dict(m["corpus_release"], sha256="0" * 64), *args[1:]) != m["build_sha256"]
    assert build_sha256(args[0], args[1], dict(args[2], mode="real")) != m["build_sha256"]
    assert build_sha256(*args, builder={"name": "packrt.index", "version": 2}) != m["build_sha256"]
    assert build_sha256(dict(m["corpus_release"], parent="x"), *args[1:]) == m["build_sha256"]   # only id · sha256 of the corpus


def test_metadata_falls_back_to_metadata_required():
    store = {"type": "vector", "index": {"embedding": EMB, "split": "record"}, "metadata_required": ["symptom_group", "record_id"]}
    assert index_recipe(store)["metadata"] == ["record_id", "symptom_group"]
    assert index_recipe({"type": "vector", "index": {"embedding": EMB, "split": "record"}})["metadata"] == []


@pytest.mark.parametrize("graph, node, words", [
    ("nope", "find_manuals", "그래프 'nope'"),
    ("start", "nope", "node 'nope'가 없습니다"),
    ("start", "find_rows", "retrieve@2 node에만"),
])
def test_wrong_graph_node_or_kind_is_refused(tmp_path, graph, node, words):
    with pytest.raises(RecipeError, match=words):
        index_recipe_of(make(tmp_path), graph, node)


def test_a_pack_that_does_not_load_is_refused(tmp_path):
    def broken(doc):
        doc["graphs"]["start"]["nodes"][0]["params"]["index"] = {"chunk_size": 10, "overlap": 10}   # overlap must stay below
    with pytest.raises(RecipeError, match="Pack을 불러올 수 없습니다"):
        index_recipe_of(make(tmp_path, broken), "start", "find_manuals")
