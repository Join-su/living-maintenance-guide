"""EV-006 S4a · V-BUILD · V-CORPUS (CHG-R08, contract C · D-266 (2) · D-267 C-4 · C-10): `packrt.index build` gives the H0 index
releases byte for byte from the H0 corpus releases (manuals as sections, cut 40 · 8, history whole, history v2 with its parent); the same
build again is `exists` with nothing embedded; a failure leaves nothing under --out; the error codes; `packrt.index corpus` gives the
H0 corpus releases byte for byte, `exists` for the same content whatever the parent, corpus_conflict for other content, index_invalid
for bad input; the command line's JSON and exit codes 0 · 1 · 2. Fake embedding only — no provider is called."""
import copy
import filecmp
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_index_h0_fixture import v_loc

import packrt
from packrt.index import build as build_mod
from packrt.index.build import build
from packrt.index.corpus import make
from packrt.index.errors import IndexBuildError

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
SENSORS = ["torque_nm", "tool_wear_min"]
EMB = {"provider": "openai", "model": "text-embedding-3-small"}
DOC = {
    "ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__},
    "pack": {"id": "bld", "version": "V0", "title": "index build"}, "env": {"required": [], "optional": []},
    "stores": {
        "manuals": {"type": "vector", "index": {"embedding": EMB, "split": "section", "metadata": ["kind", "group"]}},
        "history": {"type": "vector", "index": {"embedding": EMB, "split": "record",
                                                 "metadata": ["symptom_group", "source"] + SENSORS,
                                                 "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}}},
    },
    "schemas": {"Query": "schemas/query.json", "Hit": "schemas/hit.json"},
    "graphs": {"start": {
        "state": {"q": "Query", "manual": {"type": "list[Hit]"}, "similar": {"type": "list[Hit]"}},
        "nodes": [
            {"id": "find_manuals", "kind": "retrieve@2", "reads": ["q"], "writes": ["manual"],
             "params": {"store": "manuals", "query": "{state.q.text}", "top_k": 4}},
            {"id": "find_similar", "kind": "retrieve@2", "reads": ["q"], "writes": ["similar"], "params": {"store": "history", "query": "{state.q.text}"}},
        ],
        "edges": [["START", "find_manuals"], ["find_manuals", "find_similar"], ["find_similar", "END"]]}},
    "entrypoints": [{"graph": "start", "input": "Query", "output": "Hit"}],
    "trace": {"contract": 1},
}


def pack(tmp_path, manual_index=None, name="pack", change=None):
    doc = copy.deepcopy(DOC)
    if manual_index is not None:
        doc["graphs"]["start"]["nodes"][0]["params"]["index"] = manual_index
    if change:
        change(doc)
    d = tmp_path / name
    (d / "schemas").mkdir(parents=True)
    for f in ("query.json", "hit.json"):
        (d / "schemas" / f).write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (d / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d


def same_tree(a, b):
    cmp = filecmp.dircmp(a, b)
    assert not (cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files), (cmp.left_only, cmp.right_only, cmp.diff_files)
    for f in cmp.common_files:
        assert (Path(a) / f).read_bytes() == (Path(b) / f).read_bytes(), f


def corpus(name):
    return H0 / "corpus" / name


@pytest.mark.parametrize("node, index, corp, parent, sample", [
    ("find_manuals", None, "mc-h0-manuals-v1", None, "ir-manuals-bb52ba51"),
    ("find_manuals", {"chunk_size": 40, "overlap": 8}, "mc-h0-manuals-v1", None, "ir-manuals-7dd35828"),
    ("find_similar", None, "hc-h0-history-v1", None, "ir-history-c6510147"),
    ("find_similar", None, "hc-h0-history-v2", "ir-history-c6510147", "ir-history-543e3885"),
])
def test_build_gives_the_h0_index_releases_byte_for_byte(tmp_path, node, index, corp, parent, sample):
    out = tmp_path / "out"
    r = build(pack(tmp_path, index), "start", node, corpus(corp), out, parent=parent)
    want = json.loads((H0 / "build-results.jsonl").read_text(encoding="utf-8").splitlines()[
        ["ir-manuals-bb52ba51", "ir-manuals-7dd35828", "ir-history-c6510147", "ir-history-543e3885"].index(sample)])
    assert {k: r[k] for k in r if k not in ("seconds", "location")} == {k: want[k] for k in want if k not in ("seconds", "location")}
    assert r["location"] == str(out / sample) and sorted(p.name for p in out.iterdir()) == [sample]
    same_tree(out / sample, H0 / "indexes" / sample)


def test_built_manual_locators_pass_v_loc(tmp_path):
    out = tmp_path / "out"
    r = build(pack(tmp_path, {"chunk_size": 25, "overlap": 5}), "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)
    chunks = [json.loads(l) for l in (out / r["release_id"] / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    docs = {d["doc"]: d for d in (json.loads(l) for l in (corpus("mc-h0-manuals-v1") / "documents.jsonl").read_text(encoding="utf-8").splitlines())}
    starts = v_loc(chunks, docs)
    assert max(len(v) for v in starts.values()) >= 3   # sections cut in three or more pieces still count from the section start


def test_split_chars_cuts_the_whole_document(tmp_path):
    def chars(doc):
        doc["stores"]["manuals"]["index"]["split"] = "chars"
    out = tmp_path / "out"
    r = build(pack(tmp_path, {"chunk_size": 50, "overlap": 10}, change=chars), "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)
    docs = {d["doc"]: d["text"] for d in (json.loads(l) for l in (corpus("mc-h0-manuals-v1") / "documents.jsonl").read_text(encoding="utf-8").splitlines())}
    chunks = [json.loads(l) for l in (out / r["release_id"] / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [c["id"] for c in chunks if c["locator"]["doc"] == "MG-A"] == ["MG-A#0", "MG-A#1", "MG-A#2"]
    for c in chunks:
        loc = c["locator"]
        assert set(loc) == {"doc", "char_start", "char_end"} and docs[loc["doc"]][loc["char_start"]:loc["char_end"]] == c["text"]


def test_finished_folders_follow_the_umask_not_the_temporary_0700(tmp_path, monkeypatch):
    from packrt.index import corpus as corpus_mod
    monkeypatch.setattr(corpus_mod, "_UMASK", 0o022)
    r = build(pack(tmp_path), "start", "find_manuals", corpus("mc-h0-manuals-v1"), tmp_path / "out")
    c = make(corpus("hc-h0-history-v1") / "documents.jsonl", "hc-h0-history-v1", tmp_path / "cout")
    assert (Path(r["location"]).stat().st_mode & 0o777) == 0o755 and (Path(c["location"]).stat().st_mode & 0o777) == 0o755


def test_the_same_build_again_is_exists_and_embeds_nothing(tmp_path, monkeypatch):
    out, p = tmp_path / "out", pack(tmp_path)
    build(p, "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)
    before = {f.name: f.stat().st_mtime_ns for f in (out / "ir-manuals-bb52ba51").iterdir()}
    monkeypatch.setattr(build_mod, "fake_embedder", lambda texts: pytest.fail("embedded again"))
    r = build(p, "start", "find_manuals", corpus("mc-h0-manuals-v1"), out, parent="ir-other")
    assert (r["status"], r["chunks"], r["embed_calls"]) == ("exists", 4, 0)
    assert {f.name: f.stat().st_mtime_ns for f in (out / "ir-manuals-bb52ba51").iterdir()} == before
    assert json.loads((out / "ir-manuals-bb52ba51" / "manifest.json").read_text(encoding="utf-8"))["parent_release"] is None


def test_a_failing_embedding_leaves_nothing_under_out(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()

    def boom(texts):
        raise ConnectionError("provider said sk-secret-value")
    monkeypatch.setattr(build_mod, "fake_embedder", boom)
    with pytest.raises(IndexBuildError) as e:
        build(pack(tmp_path), "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)
    assert e.value.code == "embedding_failed" and str(e.value) == "임베딩 실패: ConnectionError"
    assert list(out.iterdir()) == []


def test_a_failure_while_writing_leaves_no_half_folder(tmp_path, monkeypatch):
    out = tmp_path / "out"

    def broken(folder, manifest):
        (folder / "manifest.json").write_text("{", encoding="utf-8")
        raise OSError("disk full")
    monkeypatch.setattr(build_mod, "write_manifest", broken)
    with pytest.raises(OSError):
        build(pack(tmp_path), "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)
    assert list(out.iterdir()) == []


def test_wrong_vector_size_is_embedding_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mod, "fake_embedder", lambda texts: ([[0.0] * 3 for _ in texts], 0, 0))
    with pytest.raises(IndexBuildError) as e:
        build(pack(tmp_path), "start", "find_manuals", corpus("mc-h0-manuals-v1"), tmp_path / "out")
    assert e.value.code == "embedding_failed"


def test_a_missing_corpus_is_corpus_missing(tmp_path):
    with pytest.raises(IndexBuildError) as e:
        build(pack(tmp_path), "start", "find_manuals", tmp_path / "nope", tmp_path / "out")
    assert e.value.code == "corpus_missing"


@pytest.mark.parametrize("what", ["recipe", "corpus_hash", "record_split_without_record_id", "section_split_without_sections", "graph"])
def test_index_invalid_cases(tmp_path, what):
    p, corp = pack(tmp_path), corpus("mc-h0-manuals-v1")
    graph, node = "start", "find_manuals"
    if what == "recipe":
        p = pack(tmp_path, {"chunk_size": 10, "overlap": 10}, name="bad")   # the Pack does not load (overlap < chunk_size)
    elif what == "corpus_hash":
        corp = tmp_path / "c"
        shutil.copytree(corpus("mc-h0-manuals-v1"), corp)
        with open(corp / "documents.jsonl", "a", encoding="utf-8") as f:
            f.write("\n")
    elif what == "record_split_without_record_id":
        node = "find_similar"   # history (split record) over the manual corpus, whose documents have no record_id
    elif what == "section_split_without_sections":
        corp = corpus("hc-h0-history-v1")
    else:
        graph = "nope"
    with pytest.raises(IndexBuildError) as e:
        build(p, graph, node, corp, tmp_path / "out")
    assert e.value.code == "index_invalid", str(e.value)
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []


# ---- corpus ----
@pytest.mark.parametrize("name, parent", [("mc-h0-manuals-v1", None), ("hc-h0-history-v1", None), ("hc-h0-history-v2", "hc-h0-history-v1")])
def test_corpus_gives_the_h0_corpus_releases_byte_for_byte(tmp_path, name, parent):
    r = make(corpus(name) / "documents.jsonl", name, tmp_path / "out", parent=parent)
    m = json.loads((corpus(name) / "manifest.json").read_text(encoding="utf-8"))
    assert r == {"status": "built", "release_id": name, "sha256": m["sha256"], "documents": m["documents"], "location": str(tmp_path / "out" / name),
                 "error": None}
    same_tree(tmp_path / "out" / name, corpus(name))


def test_corpus_lines_are_normalized(tmp_path):
    rows = [json.loads(l) for l in (corpus("hc-h0-history-v1") / "documents.jsonl").read_text(encoding="utf-8").splitlines()]
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(dict(reversed(list(r.items()))), ensure_ascii=True) for r in rows) + "\n\n", encoding="utf-8")
    assert make(src, "hc-h0-history-v1", tmp_path / "out")["sha256"] == json.loads((corpus("hc-h0-history-v1") / "manifest.json").read_text())["sha256"]


def test_corpus_same_content_is_exists_whatever_the_parent_and_other_content_is_a_conflict(tmp_path):
    out = tmp_path / "out"
    docs = corpus("hc-h0-history-v1") / "documents.jsonl"
    make(docs, "c1a15-history-v1", out)
    r = make(docs, "c1a15-history-v1", out, parent="c1a15-history-v0")
    assert r["status"] == "exists" and json.loads((out / "c1a15-history-v1" / "manifest.json").read_text())["parent_release"] is None
    with pytest.raises(IndexBuildError) as e:
        make(corpus("hc-h0-history-v2") / "documents.jsonl", "c1a15-history-v1", out)
    assert e.value.code == "corpus_conflict" and str(e.value).startswith("c1a15-history-v1가 이미 다른 내용으로 있습니다(sha256 ")
    assert sorted(p.name for p in out.iterdir()) == ["c1a15-history-v1"]


@pytest.mark.parametrize("rows, words", [
    ([{"doc": "a", "text": "x"}, {"doc": "a", "text": "y"}], "doc 'a'가 겹칩니다"),
    ([{"doc": "a", "text": "x", "record_id": "R"}, {"doc": "b", "text": "y", "record_id": "R"}], "record_id 'R'가 겹칩니다"),
    ([{"doc": "a", "text": "abc", "sections": [{"id": "s1", "char_start": 0, "char_end": 9}]}], "경계가 글 밖"),
    ([{"doc": "a", "text": "abcdef", "sections": [{"id": "s1", "char_start": 0, "char_end": 4}, {"id": "s2", "char_start": 3, "char_end": 6}]}], "앞 절과 겹칩니다"),
    ([{"doc": "a", "text": 3}], "text가 글이 아닙니다"),
    ([], "문서가 하나도 없습니다"),
])
def test_corpus_input_checks_are_index_invalid(tmp_path, rows, words):
    src = tmp_path / "in.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(IndexBuildError, match=words) as e:
        make(src, "c-1", tmp_path / "out")
    assert e.value.code == "index_invalid" and not (tmp_path / "out").exists()


def test_a_corpus_id_with_a_trailing_newline_is_refused(tmp_path):
    with pytest.raises(IndexBuildError) as e:
        make(corpus("hc-h0-history-v1") / "documents.jsonl", "abc\n", tmp_path / "out")
    assert e.value.code == "index_invalid" and not (tmp_path / "out").exists()


@pytest.mark.parametrize("fault", ["not_json", "no_release_id"])
def test_a_damaged_corpus_release_is_index_invalid(tmp_path, fault):
    import hashlib
    from packrt.ir.releases import dir_hash
    corp = tmp_path / "c"
    shutil.copytree(corpus("hc-h0-history-v1"), corp)
    m = json.loads((corp / "manifest.json").read_text(encoding="utf-8"))
    if fault == "not_json":
        (corp / "documents.jsonl").write_text("not json\n", encoding="utf-8")
        m["files"] = {"documents.jsonl": hashlib.sha256((corp / "documents.jsonl").read_bytes()).hexdigest()}
        m["sha256"] = dir_hash(corp)
    else:
        del m["release_id"]
    (corp / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(IndexBuildError) as e:
        build(pack(tmp_path), "start", "find_similar", corp, tmp_path / "out")
    assert e.value.code == "index_invalid"


def test_corpus_missing_documents_file(tmp_path):
    with pytest.raises(IndexBuildError) as e:
        make(tmp_path / "nope.jsonl", "c-1", tmp_path / "out")
    assert e.value.code == "corpus_missing"


# ---- command line ----
def cli(*args, cwd):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}   # not run from the repository root: its packrt/ folder hides the package
    return subprocess.run([sys.executable, "-B", "-m", "packrt.index", *map(str, args)], capture_output=True, text=True, env=env, cwd=cwd)


def test_cli_build_json_and_exit_codes(tmp_path):
    p, out = pack(tmp_path), tmp_path / "out"
    args = ("build", "--pack", p, "--graph", "start", "--node", "find_manuals", "--corpus", corpus("mc-h0-manuals-v1"), "--out", out,
            "--embedding", "fake", "--json")
    r = cli(*args, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout.strip().splitlines()[-1])
    assert list(j) == ["status", "release_id", "build_sha256", "index_recipe_sha256", "chunks", "embed_calls", "embed_tokens", "seconds",
                       "location", "error"]
    assert (j["status"], j["release_id"], j["error"]) == ("built", "ir-manuals-bb52ba51", None)
    assert json.loads(cli(*args, cwd=tmp_path).stdout)["status"] == "exists"
    bad = cli("build", "--pack", p, "--graph", "start", "--node", "find_manuals", "--corpus", tmp_path / "nope", "--out", out,
              "--embedding", "fake", "--json", cwd=tmp_path)
    assert bad.returncode == 1 and json.loads(bad.stdout)["error"]["code"] == "corpus_missing"
    fake_key = cli("build", "--pack", p, "--graph", "start", "--node", "find_manuals", "--corpus", corpus("mc-h0-manuals-v1"), "--out", out,
                   "--embedding", "fake", "--key-env", "SOME_KEY", "--json", cwd=tmp_path)
    assert fake_key.returncode == 2 and fake_key.stdout == "" and "--key-env" in fake_key.stderr
    usage = cli("build", "--pack", p, "--json", cwd=tmp_path)
    assert usage.returncode == 2 and usage.stdout == ""


def test_cli_corpus_json_and_exit_codes(tmp_path):
    out, docs = tmp_path / "out", corpus("hc-h0-history-v1") / "documents.jsonl"
    r = cli("corpus", "--documents", docs, "--id", "hc-h0-history-v1", "--out", out, "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert list(j) == ["status", "release_id", "sha256", "documents", "location", "error"] and j["status"] == "built"
    conflict = cli("corpus", "--documents", corpus("hc-h0-history-v2") / "documents.jsonl", "--id", "hc-h0-history-v1", "--out", out, "--json", cwd=tmp_path)
    assert conflict.returncode == 1 and json.loads(conflict.stdout)["error"]["code"] == "corpus_conflict"
    bad_id = cli("corpus", "--documents", docs, "--id", "Bad/Id", "--out", out, "--json", cwd=tmp_path)
    assert bad_id.returncode == 2 and bad_id.stdout == ""
