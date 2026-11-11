"""EV-008 P2 V-FIND (CHG-P02 · D-293, PRES-P05): packrt.index.find_corpus finds the corpus release folder by the 0.12.x rule and gives
the answer the inner functions give (releases.find_releases · _locate, corpus.read_corpus) — one or two levels under the roots, an
unreadable manifest skipped, the same real folder once, two real folders = corpus_ambiguous, none = corpus_missing, changed files or
another sha256 = index_invalid. Corpus releases are the H0 sample (tests/fixtures/index_h0/corpus)."""
import json
import os
import shutil
from pathlib import Path

import pytest

from packrt.index import IndexBuildError, find_corpus
from packrt.index.corpus import read_corpus
from packrt.ir.releases import ReleaseError, _locate, find_releases

H0 = Path(__file__).resolve().parent / "fixtures" / "index_h0"
REF = json.loads((H0 / "indexes" / "ir-history-c6510147" / "manifest.json").read_text(encoding="utf-8"))["corpus_release"]


def roots(tmp_path, layout):
    """layout: {relative folder: corpus id} copied from H0 under tmp_path/r."""
    base = tmp_path / "r"
    for rel, cid in layout.items():
        shutil.copytree(H0 / "corpus" / cid, base / rel)
    return base


def same_as_inner(roots_, cid):
    """What the inner rule gives (D-293): _locate over find_releases, then read_corpus."""
    folder = _locate(find_releases([Path(r) for r in roots_]), cid)
    data, _, ref = read_corpus(folder)
    return folder, ref


def test_one_level_and_the_reference_as_written(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v1": "hc-h0-history-v1", "hc-h0-history-v2": "hc-h0-history-v2"})
    assert REF["id"] == "hc-h0-history-v1"
    got = find_corpus([r], REF)
    folder, ref = same_as_inner([r], REF["id"])
    assert got == folder == r / "hc-h0-history-v1" and ref == REF
    assert find_corpus([str(r)], REF["id"]) == got   # an id alone, roots as text


def test_two_levels(tmp_path):
    r = roots(tmp_path, {"corpus/hc-h0-history-v1": "hc-h0-history-v1"})
    assert find_corpus([r], REF) == r / "corpus" / "hc-h0-history-v1"


def test_three_levels_is_not_looked_at(tmp_path):
    r = roots(tmp_path, {"a/b/hc-h0-history-v1": "hc-h0-history-v1"})
    with pytest.raises(IndexBuildError) as e:
        find_corpus([r], REF)
    assert e.value.code == "corpus_missing" and "hc-h0-history-v1" in str(e.value)


def test_an_unreadable_manifest_is_skipped(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v1": "hc-h0-history-v1"})
    (r / "broken").mkdir()
    (r / "broken" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert find_corpus([r], REF) == r / "hc-h0-history-v1"


def test_the_same_real_folder_counts_once(tmp_path):
    r = roots(tmp_path, {"corpus/hc-h0-history-v1": "hc-h0-history-v1"})
    link = tmp_path / "link"
    os.symlink(r / "corpus", link)
    got = find_corpus([r, r / "corpus", link], REF)   # overlapping roots and a symlink: one real folder
    assert os.path.realpath(got) == os.path.realpath(r / "corpus" / "hc-h0-history-v1")


def test_two_real_folders_are_corpus_ambiguous(tmp_path):
    r = roots(tmp_path, {"a/hc-h0-history-v1": "hc-h0-history-v1", "b/hc-h0-history-v1": "hc-h0-history-v1"})
    with pytest.raises(IndexBuildError) as e:
        find_corpus([r], REF)
    assert e.value.code == "corpus_ambiguous"
    assert str(r / "a" / "hc-h0-history-v1") in str(e.value) and str(r / "b" / "hc-h0-history-v1") in str(e.value)
    with pytest.raises(ReleaseError, match="release 번호가 겹칩니다"):   # the inner rule refuses the same case
        _locate(find_releases([r]), REF["id"])


def test_no_such_id_is_corpus_missing(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v2": "hc-h0-history-v2"})
    with pytest.raises(IndexBuildError) as e:
        find_corpus([r, tmp_path / "no-such-root"], REF)
    assert e.value.code == "corpus_missing"


def test_changed_files_are_index_invalid(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v1": "hc-h0-history-v1"})
    with open(r / "hc-h0-history-v1" / "documents.jsonl", "a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(IndexBuildError) as e:
        find_corpus([r], REF)
    assert e.value.code == "index_invalid"


def test_another_sha256_is_index_invalid(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v1": "hc-h0-history-v1"})
    with pytest.raises(IndexBuildError) as e:
        find_corpus([r], {"id": REF["id"], "sha256": "0" * 64})
    assert e.value.code == "index_invalid" and "sha256" in str(e.value)


def test_a_release_that_is_not_a_corpus_is_index_invalid(tmp_path):
    base = tmp_path / "r"
    shutil.copytree(H0 / "indexes" / "ir-history-c6510147", base / "ir-history-c6510147")
    with pytest.raises(IndexBuildError) as e:
        find_corpus([base], "ir-history-c6510147")
    assert e.value.code == "index_invalid" and "kind corpus" in str(e.value)


@pytest.mark.parametrize("roots_, ref", [("one/path", "hc-h0-history-v1"), ([1], "hc-h0-history-v1"), (["x"], {"sha256": "a"}),
                                          (["x"], ""), (["x"], {"id": "hc", "sha256": 5})])
def test_bad_arguments_are_index_invalid(roots_, ref):
    with pytest.raises(IndexBuildError) as e:
        find_corpus(roots_, ref)
    assert e.value.code == "index_invalid"


def test_files_are_only_read(tmp_path):
    r = roots(tmp_path, {"hc-h0-history-v1": "hc-h0-history-v1"})
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in r.rglob("*") if p.is_file()}
    find_corpus([r], REF)
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in r.rglob("*") if p.is_file()} == before
