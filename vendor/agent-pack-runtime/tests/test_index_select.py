"""EV-006 S3b · V-SELECT · V-PROBE · V-CFG (CHG-R06, contract C · D-266 · D-267 C-1 · C-9, N-3 · N-6): a retrieve@2 store's `release`
is the base and fixes the corpus; the candidates are the base and the store's `releases`; the one with the node's recipe hash, the
base's corpus and an embedding that passes E-3 is bound — none is IndexMissing (with the build hash the builder will give), two are
IndexAmbiguous. A retrieve@2 store that is neither flat-local nor pgvector is refused first (backend_unsupported, EV-008 CHG-P01 1). `releases` stays an unknown item for
other stores. collect_missing lists what is missing instead of raising, and the probe carries the list. Releases are the H0 sample
(tests/fixtures/index_h0); configs without `releases` and retrieve@1 keep the EV-007 expectations (test_load_preserved ·
test_retrieve1_preserved)."""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

import packrt
from packrt import probe
from packrt.index import build_sha256, index_recipe, index_recipe_sha256
from packrt.ir import load_pack
from packrt.ir.releases import IndexAmbiguous, IndexMissing, ReleaseError, load_execution_config

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
SENSORS = ["torque_nm", "tool_wear_min"]
EMB = {"provider": "openai", "model": "text-embedding-3-small"}
STORES = {
    "manuals": {"type": "vector", "index": {"embedding": EMB, "split": "section", "metadata": ["kind", "group"]}},
    "history": {"type": "vector", "index": {"embedding": EMB, "split": "record", "metadata": ["symptom_group", "source"] + SENSORS,
                                             "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}}},
}
BASE = {"manuals": "ir-manuals-bb52ba51", "history": "ir-history-c6510147"}


def make(tmp_path, manual_index=None, kind="retrieve@2", stores=None, extra=None, manual_cfg=None):
    """A Pack reading manuals and history with retrieve@2 (or manuals with retrieve@1), its execution config and the H0 releases."""
    roots = tmp_path / "releases"
    shutil.copytree(H0 / "indexes", roots)
    pack = tmp_path / "pack"
    (pack / "schemas").mkdir(parents=True)
    for f in ("q.json", "hit.json", "note.json"):
        (pack / "schemas" / f).write_text(json.dumps({"type": "string" if f == "note.json" else "object"}), encoding="utf-8")
    (pack / "templates").mkdir()
    (pack / "templates" / "n.md").write_text("done\n", encoding="utf-8")
    mparams = {"store": "manuals", "query": "{state.q.text}", "top_k": 2}
    if manual_index is not None and kind == "retrieve@2":
        mparams["index"] = manual_index
    nodes = [{"id": "find_manuals", "kind": kind, "params": mparams, "reads": ["q"], "writes": ["manual"]}]
    if kind == "retrieve@2":
        nodes.append({"id": "find_similar", "kind": "retrieve@2", "params": {"store": "history", "query": "{state.q.text}", "top_k": 2},
                      "reads": ["q"], "writes": ["similar"]})
    nodes.append({"id": "fin", "kind": "render@1", "params": {"template": "templates/n.md", "format": "md", "to": "note"}, "reads": ["q"], "writes": ["note"]})
    names = [n["id"] for n in nodes]
    doc = {"ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__}, "pack": {"id": "sel", "version": "V0", "title": "t"},
           "env": {"required": [], "optional": []}, "stores": copy.deepcopy(STORES),
           "schemas": {"Q": "schemas/q.json", "Hit": "schemas/hit.json", "Note": "schemas/note.json"},
           "graphs": {"g": {"state": {"q": "Q", "note": "Note", "manual": {"type": "list[Hit]"}, "similar": {"type": "list[Hit]"}},
                            "nodes": nodes, "edges": [["START", names[0]]] + [[a, b] for a, b in zip(names, names[1:])] + [[names[-1], "END"]]}},
           "entrypoints": [{"graph": "g", "input": "Q", "output": "Note"}], "trace": {"contract": 1}}
    (pack / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cfg = {"manuals": {"backend": "flat-local", "release": BASE["manuals"], "embedding": {"mode": "fake"}, **(manual_cfg or {})},
           "history": {"backend": "flat-local", "release": BASE["history"], "embedding": {"mode": "fake"}}}
    for name, v in (stores or {}).items():
        cfg[name].update(v)
    if kind != "retrieve@2":
        del cfg["history"]
    lines = ["config_version: 1", "entrypoint: g", f'environment: {{kind: local-python, python: "{sys.executable}"}}',
             f'release_roots: ["{roots}"]', "stores:"] + [f"  {k}: {json.dumps(v)}" for k, v in cfg.items()] + ["models: {}"]
    (pack / "execution-config.yaml").write_text("\n".join(lines + (extra or [])) + "\n", encoding="utf-8")
    return pack, roots


def load(pack, **kw):
    return load_execution_config(pack / "execution-config.yaml", load_pack(pack).doc, **kw)


def h0(name):
    return json.loads((H0 / "indexes" / name / "manifest.json").read_text(encoding="utf-8"))


def clone(roots, src, rid, change=None):
    """Another release folder with the same files (manifest.json is outside index_sha256) under a new release id."""
    d = roots / rid
    shutil.copytree(roots / src, d)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["release_id"] = rid
    if change:
        change(m)
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def test_the_base_release_is_bound_when_its_recipe_is_the_node_s(tmp_path):
    pack, _ = make(tmp_path)
    b = load(pack)["bindings"]["stores"]
    assert b["manuals"]["release_id"] == "ir-manuals-bb52ba51" and b["history"]["release_id"] == "ir-history-c6510147"


def test_a_candidate_with_the_node_s_recipe_is_bound(tmp_path):
    pack, roots = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores={"manuals": {"releases": ["ir-manuals-7dd35828"]}})
    cfg = load(pack)
    bound = cfg["bindings"]["stores"]["manuals"]
    assert bound["release_id"] == "ir-manuals-7dd35828" and bound["path"] == str(roots / "ir-manuals-7dd35828")
    assert bound["index_sha256"] == h0("ir-manuals-7dd35828")["index_sha256"] and bound["embedding_mode"] == "fake"
    assert cfg["store_folders"]["manuals"] == str(roots / "ir-manuals-7dd35828")
    assert {str(roots / "ir-manuals-bb52ba51"), str(roots / "ir-manuals-7dd35828")} <= set(cfg["deny_paths"])


def test_no_version_of_the_recipe_is_index_missing_with_the_build_hash_the_builder_gives(tmp_path):
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8})
    with pytest.raises(IndexMissing) as e:
        load(pack)
    want = h0("ir-manuals-7dd35828")
    assert e.value.as_dict() == {"store": "manuals", "node": "find_manuals", "index_recipe_sha256": want["index_recipe_sha256"],
                                 "build_sha256": want["build_sha256"]}
    assert str(e.value).startswith("(index_missing) node 'find_manuals'의 index recipe e2d726860418…에 맞는 version이 store 'manuals' "
                                   "후보(기준 ir-manuals-bb52ba51 corpus mc-h0-manuals-v1)에 없습니다")
    assert isinstance(e.value, ReleaseError)


def test_a_new_recipe_is_missing_even_with_other_candidates(tmp_path):
    pack, _ = make(tmp_path, {"chunk_size": 30, "overlap": 5}, stores={"manuals": {"releases": ["ir-manuals-7dd35828"]}})
    with pytest.raises(IndexMissing) as e:
        load(pack)
    m = h0("ir-manuals-bb52ba51")
    recipe = index_recipe(STORES["manuals"], {"chunk_size": 30, "overlap": 5})
    assert e.value.build_sha256 == build_sha256(m["corpus_release"], index_recipe_sha256(recipe), m["embedding"])


def test_two_matching_candidates_are_index_ambiguous(tmp_path):
    pack, roots = make(tmp_path, {"chunk_size": 40, "overlap": 8},
                       stores={"manuals": {"releases": ["ir-manuals-7dd35828", "ir-manuals-copy0001"]}})
    clone(roots, "ir-manuals-7dd35828", "ir-manuals-copy0001")
    with pytest.raises(IndexAmbiguous, match=r"^\(index_ambiguous\) node 'find_manuals'의 index recipe e2d726860418…에 맞는 version이 "
                                             r"둘입니다: ir-manuals-7dd35828 · ir-manuals-copy0001 — releases에 하나만 두세요$"):
        load(pack)


def test_a_candidate_on_another_corpus_is_not_a_match(tmp_path):
    pack, _ = make(tmp_path, stores={"history": {"releases": ["ir-history-543e3885"]}})
    assert load(pack)["bindings"]["stores"]["history"]["release_id"] == "ir-history-c6510147"
    pack2, _ = make(tmp_path / "b", stores={"history": {"release": "ir-history-543e3885", "releases": ["ir-history-c6510147"]}})
    assert load(pack2)["bindings"]["stores"]["history"]["release_id"] == "ir-history-543e3885"   # R12: the base names the new corpus


def test_a_candidate_whose_embedding_does_not_match_is_not_a_match(tmp_path):
    pack, roots = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores={"manuals": {"releases": ["ir-manuals-real0001"]}})
    clone(roots, "ir-manuals-7dd35828", "ir-manuals-real0001", lambda m: m["embedding"].update(mode="real"))
    with pytest.raises(IndexMissing):
        load(pack)


def test_the_base_still_refuses_a_wrong_embedding_as_before(tmp_path):
    pack, _ = make(tmp_path, stores={"manuals": {"embedding": {"mode": "real", "key_env": "OPENAI_API_KEY"}}})
    with pytest.raises(ReleaseError, match=r"^\(embedding_mismatch\) release 'ir-manuals-bb52ba51'는 fake 임베딩인데 실행 설정은 real입니다$"):
        load(pack)


def test_a_retrieve2_store_that_is_not_flat_local_or_pgvector_is_refused_first(tmp_path):
    """EV-008 CHG-P01 1: pgvector is no longer refused (test_retrieve2_pg); any other backend still is, before anything is read."""
    pack, _ = make(tmp_path, stores={"manuals": {"backend": "custom.store:Mine"}})
    with pytest.raises(ReleaseError, match=r"^\(backend_unsupported\) store 'manuals': retrieve@2는 flat-local · pgvector만 씁니다$"):
        load(pack)


def test_releases_is_an_unknown_item_on_a_store_retrieve2_does_not_read(tmp_path):
    pack, _ = make(tmp_path, kind="retrieve@1", stores={"manuals": {"releases": ["ir-manuals-7dd35828"]}})
    with pytest.raises(ReleaseError, match=r"^실행 설정의 store 'manuals'에 모르는 항목이 있습니다: \['releases'\]$"):
        load(pack)


@pytest.mark.parametrize("value, words", [
    ([], "releases는 release 번호 목록이어야 합니다"),
    ("ir-manuals-7dd35828", "releases는 release 번호 목록이어야 합니다"),
    (["releases/ir-manuals-7dd35828"], "release는 경로가 아니라 번호"),
    (["ir-manuals-nope0000"], "'ir-manuals-nope0000'을 보관 폴더에서 찾을 수 없습니다"),
])
def test_bad_releases_values_are_refused(tmp_path, value, words):
    pack, _ = make(tmp_path, stores={"manuals": {"releases": value}})
    with pytest.raises(ReleaseError, match=words):
        load(pack)


def test_the_base_repeated_in_releases_counts_once(tmp_path):
    pack, _ = make(tmp_path, stores={"manuals": {"releases": ["ir-manuals-bb52ba51"]}})
    assert load(pack)["bindings"]["stores"]["manuals"]["release_id"] == "ir-manuals-bb52ba51"


def test_collect_missing_lists_instead_of_raising_and_leaves_the_store_unbound(tmp_path):
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8})
    cfg = load(pack, collect_missing=True)
    want = h0("ir-manuals-7dd35828")
    assert cfg["index_missing"] == [{"store": "manuals", "node": "find_manuals", "index_recipe_sha256": want["index_recipe_sha256"],
                                     "build_sha256": want["build_sha256"]}]
    assert "manuals" not in cfg["bindings"]["stores"] and cfg["bindings"]["stores"]["history"]["release_id"] == "ir-history-c6510147"
    ok, _ = make(tmp_path / "ok")
    assert load(ok, collect_missing=True)["index_missing"] == [] and "index_missing" not in load(ok)


def test_collect_missing_still_raises_other_errors(tmp_path):
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores={"manuals": {"releases": ["ir-manuals-nope0000"]}})
    with pytest.raises(ReleaseError, match="보관 폴더에서 찾을 수 없습니다"):
        load(pack, collect_missing=True)


def test_the_probe_reports_missing_versions_on_the_build_error(tmp_path):
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8})
    out = probe.main(str(pack))
    want = h0("ir-manuals-7dd35828")
    assert out["error"] == "build" and out["message"].startswith("IndexMissing: (index_missing) node 'find_manuals'")
    assert out["index_missing"] == [{"store": "manuals", "node": "find_manuals", "index_recipe_sha256": want["index_recipe_sha256"],
                                     "build_sha256": want["build_sha256"]}]


def test_the_probe_projection_carries_an_empty_list(tmp_path):
    pack, _ = make(tmp_path)
    out = probe.main(str(pack))
    assert out.get("index_missing") == [], out
    assert {"runtime", "pack_revision", "entrypoint", "nodes", "edges", "routers", "reads_writes"} <= set(out)
