"""Index recipe and its hashes (EV-006 CHG-R05, contract C · D-267): the values a vector DB version is built from, read from a
retrieve@2 node (chunk_size · overlap) and its store (split · metadata · rerank_norm, fixed by people). One place computes them so
LLMOps, the builder and the execution config never count the same hash twice (F-2). The sample is tests/fixtures/index_h0."""
import hashlib
import json
from pathlib import Path

BUILDER = {"name": "packrt.index", "version": 1}   # the output format version, not the packrt version (Q-h6, D-267 C-2)
RECIPE_KEYS = ("split", "chunk_size", "overlap", "metadata", "chunker", "chunker_sha256", "rerank_norm")


class RecipeError(ValueError):
    pass


def canonical_sha256(value):
    """= packrt.ir.pg.canonical_sha256: sorted keys, no spaces, UTF-8 as is."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def index_recipe(store_doc, node_index=None):
    """The seven recipe keys, every one written (null when absent). metadata = store index.metadata, else metadata_required (C-3),
    sorted without repeats; chunk_size null and overlap 0 by default; chunker_sha256 stays null while chunker is name-only (C-5)."""
    idx = (store_doc or {}).get("index") or {}
    ni = node_index or {}
    meta = idx.get("metadata")
    if meta is None:
        meta = (store_doc or {}).get("metadata_required") or []
    return {"split": idx.get("split"), "chunk_size": ni.get("chunk_size"), "overlap": ni.get("overlap", 0),
            "metadata": sorted(set(meta)), "chunker": idx.get("chunker"), "chunker_sha256": None,
            "rerank_norm": idx.get("rerank_norm")}


def index_recipe_sha256(recipe):
    return canonical_sha256({k: recipe.get(k) for k in RECIPE_KEYS})


def build_sha256(corpus_release, recipe_sha256, embedding, builder=None):
    """corpus_release {id, sha256} · the recipe hash · embedding {provider, model, dim, mode} · builder: the same inputs give the
    same vector DB version. parent_release is not part of it (lineage only)."""
    return canonical_sha256({"corpus_release": {"id": corpus_release["id"], "sha256": corpus_release["sha256"]},
                             "index_recipe_sha256": recipe_sha256,
                             "embedding": {k: embedding.get(k) for k in ("provider", "model", "dim", "mode")},
                             "builder": builder or BUILDER})


def release_id(store, build_sha):
    return f"ir-{store}-{build_sha[:8]}"


def _pack_doc(pack):
    if isinstance(pack, dict):
        return pack
    from ..ir import load_pack
    res = load_pack(Path(pack))
    if not res.ok:
        raise RecipeError(f"Pack을 불러올 수 없습니다: {res.errors[0]['message'] if res.errors else '알 수 없는 오류'}")
    return res.doc


def index_recipe_of(pack, graph, node):
    """The recipe of one retrieve@2 node: pack is a Pack folder (loaded and checked) or an already checked pack document.
    Returns the recipe; index_recipe_sha256(recipe) gives its hash."""
    doc = _pack_doc(pack)
    g = (doc.get("graphs") or {}).get(graph)
    if g is None:
        raise RecipeError(f"그래프 '{graph}'가 Pack에 없습니다")
    n = next((x for x in g.get("nodes", []) if x.get("id") == node), None)
    if n is None:
        raise RecipeError(f"그래프 '{graph}'에 node '{node}'가 없습니다")
    if n.get("kind") != "retrieve@2":
        raise RecipeError(f"node '{node}'는 {n.get('kind')}입니다 — index recipe는 retrieve@2 node에만 있습니다")
    params = n.get("params") or {}
    store = (doc.get("stores") or {}).get(params.get("store"))
    if not store or store.get("type") != "vector":
        raise RecipeError(f"node '{node}'의 store '{params.get('store')}'가 vector store가 아닙니다")
    return index_recipe(store, params.get("index"))
