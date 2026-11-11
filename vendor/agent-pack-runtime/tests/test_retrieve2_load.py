"""EV-006 S1 · V-LOAD (CHG-R01 · R05 · R07, D-250 · D-266 · D-267): retrieve@2 loads with its own params; the store fixes split · metadata
(and filterable); the node's index (chunk_size · overlap) is refused on a store kept whole (history, split record); and the six refusals
plus one warning of the contract. retrieve@1 Packs load as before (EV-007 tests/test_load_preserved.py)."""
import copy
import json

import packrt
from packrt.ir import load_pack
from packrt.ir.patch import ADDABLE, apply_patch, editable_params

SENSORS = ["torque_nm", "tool_wear_min"]
BASE = {
    "ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__},   # 0.12.0 once M raises the version
    "pack": {"id": "r2", "version": "V0", "title": "retrieve@2 불러오기"}, "env": {"required": [], "optional": []},
    "stores": {
        "manuals": {"type": "vector", "index": {"embedding": {"provider": "openai", "model": "text-embedding-3-small"}, "split": "section",
                                                 "metadata": ["group", "kind"]}, "metadata_required": ["group", "kind"], "filterable": ["group"]},
        "history": {"type": "vector", "index": {"embedding": {"provider": "openai", "model": "text-embedding-3-small"}, "split": "record",
                                                 "metadata": ["source", "symptom_group"] + SENSORS,
                                                 "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}},
                    "metadata_required": ["symptom_group"], "filterable": ["symptom_group"]},
        "rows": {"type": "table", "schema": "Row", "key": "row_id"},
    },
    "schemas": {"Query": "schemas/query.json", "Hit": "schemas/hit.json", "Row": "schemas/row.json"},
    "graphs": {"start": {
        "state": {"q": "Query", "manual": {"type": "list[Hit]"}, "similar": {"type": "list[Hit]"}},
        "nodes": [
            {"id": "find_manuals", "kind": "retrieve@2", "reads": ["q"], "writes": ["manual"],
             "params": {"store": "manuals", "query": "{state.q.text}", "filter": {"group": "{state.q.group}"},
                        "index": {"chunk_size": 600, "overlap": 100}, "method": "hybrid", "lexical": {"tokenizer": "ko-bigram-1"},
                        "fuse": {"method": "rrf", "k": 60, "pool": 300}, "top_k": 5, "score_threshold": None}},
            {"id": "find_similar", "kind": "retrieve@2", "reads": ["q"], "writes": ["similar"],
             "params": {"store": "history", "query": "{state.q.text}", "filter": {"symptom_group": "{state.q.group}"}, "method": "hybrid",
                        "rerank": {"fields": SENSORS, "query": "{state.q.sensors}", "weights": {"retrieval": 0.5, "sensor": 0.5}},
                        "top_k": 10, "return_metadata": SENSORS}},
        ],
        "edges": [["START", "find_manuals"], ["find_manuals", "find_similar"], ["find_similar", "END"]]}},
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
    (d / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")   # JSON is YAML; no anchors
    return d


def node(doc, nid):
    return next(n for n in doc["graphs"]["start"]["nodes"] if n["id"] == nid)


def messages(res):
    return " | ".join(str(e.get("message", e)) for e in res.errors)


def test_retrieve2_pack_loads(tmp_path):
    r = load_pack(make(tmp_path))
    assert r.ok, r.errors
    assert r.warnings == []
    assert r.revision.startswith("sha256:")


def test_unknown_param_and_at1_shape_are_refused(tmp_path):
    r = load_pack(make(tmp_path, lambda d: node(d, "find_manuals")["params"].update(where={"group": "TW"})))
    assert not r.ok and "where" in messages(r)
    r = load_pack(make(tmp_path, lambda d: node(d, "find_manuals")["params"].update(method="semantic"), "b"))
    assert not r.ok


def test_history_store_keeps_records_whole(tmp_path):
    r = load_pack(make(tmp_path, lambda d: node(d, "find_similar")["params"].update(index={"chunk_size": 200, "overlap": 0})))
    assert not r.ok and "split record라 청크 · 오버랩을 쓸 수 없습니다" in messages(r)


def test_filter_key_outside_filterable(tmp_path):
    r = load_pack(make(tmp_path, lambda d: node(d, "find_similar")["params"]["filter"].update(cause_code="{state.q.group}")))
    assert not r.ok and "filter 키 'cause_code'는 store 'history' filterable 밖입니다" in messages(r)


def test_same_store_needs_same_index(tmp_path):
    def two(d):
        extra = copy.deepcopy(node(d, "find_manuals"))
        extra.update(id="find_more", writes=["manual"])
        extra["params"]["index"] = {"chunk_size": 400, "overlap": 0}
        d["graphs"]["start"]["nodes"].append(extra)
        d["graphs"]["start"]["edges"] = [["START", "find_manuals"], ["find_manuals", "find_more"], ["find_more", "find_similar"], ["find_similar", "END"]]
    r = load_pack(make(tmp_path, two))
    assert not r.ok and "index 값이 다릅니다" in messages(r)


def test_chunker_is_reserved(tmp_path):
    r = load_pack(make(tmp_path, lambda d: d["stores"]["manuals"]["index"].update(chunker="custom.ingest:chunk")))
    assert not r.ok and "chunker는 0.12.0에서 쓸 수 없습니다" in messages(r)


def test_weights_must_add_up_to_one(tmp_path):
    r = load_pack(make(tmp_path, lambda d: node(d, "find_similar")["params"]["rerank"].update(weights={"retrieval": 0.6, "sensor": 0.5})))
    assert not r.ok and "rerank.weights 합이 1이 아닙니다" in messages(r)
    r = load_pack(make(tmp_path, lambda d: node(d, "find_similar")["params"]["rerank"].update(weights={"retrieval": 0.7, "sensor": 0.3}), "b"))
    assert r.ok, r.errors


def test_at1_and_at2_on_one_store(tmp_path):
    def mixed(d):
        d["graphs"]["start"]["nodes"].append({"id": "old", "kind": "retrieve@1", "reads": ["q"], "writes": ["manual"],
                                              "params": {"store": "manuals", "query": "{state.q.text}", "top_k": 4}})
        d["graphs"]["start"]["edges"] = [["START", "find_manuals"], ["find_manuals", "old"], ["old", "find_similar"], ["find_similar", "END"]]
    r = load_pack(make(tmp_path, mixed))
    assert not r.ok and "retrieve@1 'old'와 retrieve@2 'find_manuals'가 함께 씁니다" in messages(r)


def test_split_and_size_rules(tmp_path):
    r = load_pack(make(tmp_path, lambda d: d["stores"]["manuals"]["index"].pop("split")))
    assert not r.ok and "index.split" in messages(r)
    r = load_pack(make(tmp_path, lambda d: node(d, "find_manuals")["params"].update(index={"chunk_size": 100, "overlap": 100}), "b"))
    assert not r.ok and "overlap 100은 chunk_size 100보다 작아야 합니다" in messages(r)
    r = load_pack(make(tmp_path, lambda d: (d["stores"]["manuals"]["index"].update(split="chars"), node(d, "find_manuals")["params"].pop("index")), "c"))
    assert not r.ok and "chunk_size가 필요합니다" in messages(r)
    r = load_pack(make(tmp_path, lambda d: node(d, "find_manuals")["params"].update(store="rows"), "d"))
    assert not r.ok and "retrieve@2는 vector store만" in messages(r)


def test_store_chunk_warning(tmp_path):
    r = load_pack(make(tmp_path, lambda d: d["stores"]["manuals"]["index"].update(chunk={"size": 800, "overlap": 100})))
    assert r.ok, r.errors
    assert any("index.chunk는 retrieve@2 index가 있을 때 쓰지 않습니다" in str(w) for w in r.warnings)


def test_wheel_knobs_are_editable(tmp_path):
    folder = make(tmp_path)
    doc = load_pack(folder).doc
    assert {"index", "method", "fuse", "top_k", "score_threshold", "rerank", "filter", "fallback"} <= editable_params(doc, node(doc, "find_manuals"))
    assert "retrieve@2" in ADDABLE
    res = apply_patch(folder, [{"op": "set", "graph": "start", "node": "find_manuals", "param": "index", "value": {"chunk_size": 400, "overlap": 50}}],
                      out_dir=tmp_path / "out")
    assert res.ok, res.errors
    assert res.revision != res.base_revision
    res = apply_patch(folder, [{"op": "set", "graph": "start", "node": "find_similar", "param": "index", "value": {"chunk_size": 400}}],
                      out_dir=tmp_path / "out2")
    assert not res.ok   # the copy is loaded again: the history store refuses chunks


def test_rerank_needs_store_norm(tmp_path):
    """D-273 R-1: rerank needs the store's index.rerank_norm, and its fields inside that norm."""
    r = load_pack(make(tmp_path, lambda d: d["stores"]["history"]["index"].pop("rerank_norm")))
    assert not r.ok and "index.rerank_norm이 없습니다" in messages(r)
    r = load_pack(make(tmp_path, lambda d: node(d, "find_similar")["params"]["rerank"].update(fields=SENSORS + ["power_w"]), "b"))
    assert not r.ok and "rerank_norm.fields 밖입니다" in messages(r)
