"""EV-006 H0 (D-267; contract C): the locked index format sample tests/fixtures/index_h0 — three corpus releases (documents-1) and four
index releases built from them (manuals as sections, manuals cut 40 · 8, history kept whole, history after one approved report with its
parent). It was written by the run folder's plan/h0-format-draft/make_h0.py (git-ignored) and checked on its own by the packrt session
(report 053; v2 for D-277: a manual locator counts from its section's first character). The builder must give these bytes; here only
the sample's own rules are held: release checks, hashes, ids, locators (V-LOC)."""
import hashlib
import json
from pathlib import Path

from packrt.ir.releases import check_index, dir_hash
from packrt.run.backends import FlatLocal
from packrt.run.embedding import FAKE_DIM, fake_embedding

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
CORPUS = ["hc-h0-history-v1", "hc-h0-history-v2", "mc-h0-manuals-v1"]
INDEXES = ["ir-history-543e3885", "ir-history-c6510147", "ir-manuals-7dd35828", "ir-manuals-bb52ba51"]
RECIPE_KEYS = {"split", "chunk_size", "overlap", "metadata", "chunker", "chunker_sha256", "rerank_norm"}


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _manifest(folder):
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def _lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_the_sample_holds_three_corpus_and_four_index_releases():
    assert sorted(p.name for p in (H0 / "corpus").iterdir()) == CORPUS
    assert sorted(p.name for p in (H0 / "indexes").iterdir()) == INDEXES


def test_corpus_releases_are_documents_1_with_their_folder_hash():
    for name in CORPUS:
        folder = H0 / "corpus" / name
        m = _manifest(folder)
        assert (m["kind"], m["format"], m["release_id"]) == ("corpus", "documents-1", name)
        assert m["sha256"] == dir_hash(folder)
        assert m["files"] == {"documents.jsonl": hashlib.sha256((folder / "documents.jsonl").read_bytes()).hexdigest()}
        assert m["documents"] == len(_lines(folder / "documents.jsonl"))
    assert _manifest(H0 / "corpus" / "hc-h0-history-v2")["parent_release"] == "hc-h0-history-v1"


def test_index_releases_pass_the_0_11_0_release_check_and_read_as_flat_local():
    for name in INDEXES:
        folder = H0 / "indexes" / name
        m = _manifest(folder)
        info = check_index(folder, m["store"])
        assert info["release_id"] == name and info["index_sha256"] == m["index_sha256"]
        assert len(FlatLocal(folder).search(fake_embedding("토크 과부하"), top_k=2)) == 2


def test_recipe_hashes_and_release_ids_follow_the_format():
    for name in INDEXES:
        m = _manifest(H0 / "indexes" / name)
        corpus = _manifest(H0 / "corpus" / m["corpus_release"]["id"])
        assert m["corpus_release"]["sha256"] == corpus["sha256"]
        assert set(m["index_recipe"]) == RECIPE_KEYS and m["index_recipe"]["metadata"] == sorted(set(m["index_recipe"]["metadata"]))
        assert m["index_recipe_sha256"] == _sha(m["index_recipe"])
        assert m["builder"] == {"name": "packrt.index", "version": 1}
        assert m["embedding"] == {"provider": "openai", "model": "text-embedding-3-small", "dim": FAKE_DIM, "mode": "fake"}
        assert m["build_sha256"] == _sha({"corpus_release": m["corpus_release"], "index_recipe_sha256": m["index_recipe_sha256"],
                                          "embedding": m["embedding"], "builder": m["builder"]})
        assert name == f"ir-{m['store']}-{m['build_sha256'][:8]}"


def v_loc(chunks, docs):
    """V-LOC (contract v5, D-277): a manual (split section) chunk's locator counts from its section's first character, not per chunk —
    ① corpus text[section start + char_start : section start + char_end] = chunk text ② 0 ≤ char_start < char_end ≤ section length
    ③ from the second chunk of a section char_start > 0 ④ char_start grows within a section; every such chunk names its section."""
    by_section = {}
    for c in chunks:
        loc = c["locator"]
        assert "section" in loc, c["id"]
        sec = next(s for s in docs[loc["doc"]]["sections"] if s["id"] == loc["section"])
        start, length = sec["char_start"], sec["char_end"] - sec["char_start"]
        assert docs[loc["doc"]]["text"][start + loc["char_start"]:start + loc["char_end"]] == c["text"], c["id"]   # ①
        assert 0 <= loc["char_start"] < loc["char_end"] <= length, c["id"]                                          # ②
        by_section.setdefault((loc["doc"], loc["section"]), []).append(loc["char_start"])
    for starts in by_section.values():
        assert all(s > 0 for s in starts[1:]), starts                                                                # ③
        assert starts == sorted(starts) and len(set(starts)) == len(starts), starts                                 # ④
    return by_section


def test_chunks_cut_from_their_corpus_text_and_carry_fake_vectors():
    for name in INDEXES:
        folder = H0 / "indexes" / name
        m = _manifest(folder)
        docs = {d["doc"]: d for d in _lines(H0 / "corpus" / m["corpus_release"]["id"] / "documents.jsonl")}
        vectors = json.loads((folder / "vectors.json").read_text(encoding="utf-8"))
        chunks = _lines(folder / "chunks.jsonl")
        assert m["chunks"] == len(chunks) == len(vectors)
        if m["index_recipe"]["split"] == "section":
            v_loc(chunks, docs)
        else:   # history (split record): the whole record, from 0
            for c in chunks:
                loc = c["locator"]
                assert loc["record_id"] == docs[loc["doc"]]["record_id"] and docs[loc["doc"]]["text"][loc["char_start"]:loc["char_end"]] == c["text"]
        for c in chunks:
            assert set(c["metadata"]) <= set(m["index_recipe"]["metadata"])
            assert vectors[c["id"]] == fake_embedding(c["text"])


def test_a_section_cut_in_pieces_keeps_counting_from_the_section_start():
    chunks = _lines(H0 / "indexes" / "ir-manuals-7dd35828" / "chunks.jsonl")
    docs = {d["doc"]: d for d in _lines(H0 / "corpus" / "mc-h0-manuals-v1" / "documents.jsonl")}
    starts = v_loc(chunks, docs)
    assert starts[("MG-A", "s2")] == [0, 32] and max(len(v) for v in starts.values()) == 2   # 40 · 8 windows: 0, 32
    whole = {c["id"]: c["locator"] for c in _lines(H0 / "indexes" / "ir-manuals-bb52ba51" / "chunks.jsonl")}
    assert all(loc["char_start"] == 0 for loc in whole.values())   # sections kept whole start at 0


def test_the_history_releases_differ_only_by_the_approved_report():
    v1, v2 = (_manifest(H0 / "indexes" / n) for n in ("ir-history-c6510147", "ir-history-543e3885"))
    assert v1["index_recipe_sha256"] == v2["index_recipe_sha256"] and v2["parent_release"] == "ir-history-c6510147"
    assert v1["rerank_norm"] == v2["rerank_norm"]   # the norm counts generated records only (G-7)
    assert [c["id"] for c in _lines(H0 / "indexes" / "ir-history-543e3885" / "chunks.jsonl")] == ["R-0001#0", "R-0002#0", "R-0003#0", "R-0101#0"]


def test_the_two_manual_releases_differ_only_by_chunk_size_and_overlap():
    a, b = (_manifest(H0 / "indexes" / n)["index_recipe"] for n in ("ir-manuals-bb52ba51", "ir-manuals-7dd35828"))
    assert {k for k in RECIPE_KEYS if a[k] != b[k]} == {"chunk_size", "overlap"}
    assert (a["chunk_size"], a["overlap"], b["chunk_size"], b["overlap"]) == (None, 0, 40, 8)
