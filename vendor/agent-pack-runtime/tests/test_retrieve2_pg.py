"""EV-008 P1 (CHG-P01, D-298 · D-308): a retrieve@2 store on pgvector — V-PG2-SEL · V-PG2-EQ · V-PG1-B · V-PROBE-PG.
No database: `packrt.ir.pg.connect` is replaced by a fake runtime_reader session whose `<pack_id>.data_release` and `index_chunk` rows
are the H0 releases (tests/fixtures/index_h0) as `python -m packrt.index load` would put them (manifest = manifest.json as written,
sha256 = index_sha256, one index_chunk row per chunk with its locator and its vector as text).
- V-PG2-SEL: C-1 over the rows (base · candidate · none · two · not loaded · embedding mismatch · other corpus) gives the file answer.
- V-PG2-EQ: the same releases bound flat-local and pgvector give the same hits, ranks, locators, metadata and node meta; scores are
  equal with the vectors as written (a) and within 1e-5 after a float4 round trip (b); the run manifest keeps the same keys and recipe
  fields (manifest_sha256 = the 0.11.0 pg rule over the manifest as written, its backend: flat-local field included); same drs.
- V-PG1-B: retrieve@1 on a release `packrt.index build` made reads index_chunk and answers in the @1 shape (as flat-local @1).
- V-PROBE-PG: index_unchecked on the projection (always) and on the error shape (only when not empty)."""
import copy
import json
import re
import struct
from pathlib import Path

import pytest

from packrt import probe, trace
from packrt.ir import load_pack, pg
from packrt.ir.releases import IndexAmbiguous, IndexMissing, ReleaseError, chunk_table
from packrt.run.engine import Runtime

from test_index_select import BASE, H0, SENSORS, h0, load, make

DSN = "postgresql:///llmopsagent?user=runtime_reader&password=SECRET-p1"
PG_LINE = {"backend": "pgvector", "connection": {"env": "LLMOPSAGENT_RUNTIME_DSN"}}
KEYS = pg.ROW_KEYS


def f4(x):
    return float(repr(struct.unpack("f", struct.pack("f", x))[0]))


def rows_of(rid, as_id=None, float4=False, change=None):
    """One H0 index release as the loader would put it: (data_release row, index_chunk rows)."""
    folder = H0 / "indexes" / rid
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    vectors = json.loads((folder / "vectors.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rid = as_id or rid
    e = m["embedding"]
    row = {"release_id": rid, "kind": "index", "sha256": m["index_sha256"], "manifest": m, "embedding_provider": e["provider"],
           "embedding_model": e["model"], "embedding_dim": e["dim"], "embedding_mode": e["mode"], "store": m["store"]}
    if change:
        change(row)
    chunk_rows = [{"release_id": rid, "chunk_id": c["id"], "text": c["text"], "metadata": c["metadata"], "locator": c["locator"],
                   "embedding": json.dumps([f4(x) for x in vectors[c["id"]]] if float4 else vectors[c["id"]])} for c in chunks]
    return row, chunk_rows


class Cur:
    def __init__(self, rows, cols):
        self._rows, self.description = rows, [type("Col", (), {"name": c})() for c in cols]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """A runtime_reader session over `sel.data_release` and `sel.index_chunk` / `sel.manual_chunk`; logs every statement."""

    def __init__(self, releases, user="runtime_reader"):
        self.log, self.user, self.data, self.chunks = [], user, {}, {}
        for row, chunk_rows in releases:
            self.data[row["release_id"]] = row
            self.chunks.setdefault(chunk_table(row["manifest"], "vector", row["store"]), []).extend(chunk_rows)

    def connect(self, dsn):
        assert dsn == DSN
        self.log.append("connect")
        return self

    closed = False

    def close(self):
        self.log.append("close")

    def execute(self, sql, args=()):
        text = " ".join(sql.split())
        self.log.append(text)
        if text == "SELECT current_user":
            return Cur([(self.user,)], ["current_user"])
        head = ('SELECT release_id, kind, sha256, manifest, embedding_provider, embedding_model, embedding_dim, embedding_mode, store '
                'FROM "sel".data_release WHERE release_id ')
        if text == head + "= %s":
            r = self.data.get(args[0])
            return Cur([tuple(r[k] for k in KEYS)] if r else [], list(KEYS))
        if text == head + "= ANY(%s) ORDER BY release_id":
            return Cur([tuple(self.data[i][k] for k in KEYS) for i in sorted(set(args[0])) if i in self.data], list(KEYS))
        m = re.match(r'^SELECT chunk_id, text, metadata(, locator)?, embedding::text AS embedding FROM "sel"\."(\w+)" WHERE release_id = %s ORDER BY chunk_id$', text)
        if m:
            cols = ["chunk_id", "text", "metadata"] + (["locator"] if m.group(1) else []) + ["embedding"]
            rows = sorted((r for r in self.chunks.get(m.group(2), []) if r["release_id"] == args[0]), key=lambda r: r["chunk_id"])
            return Cur([tuple(r[c] for c in cols) for r in rows], cols)
        raise AssertionError(f"모르는 SQL: {text}")


@pytest.fixture
def db(monkeypatch):
    def install(*releases, user="runtime_reader"):
        fake = FakeDB(releases, user)
        monkeypatch.setattr(pg, "connect", fake.connect)
        monkeypatch.setenv("LLMOPSAGENT_RUNTIME_DSN", DSN)
        return fake
    return install


def pg_stores(**extra):
    out = {"manuals": dict(PG_LINE), "history": dict(PG_LINE)}
    for k, v in extra.items():
        out[k].update(v)
    return out


def load_pg(pack, **kw):
    return load(pack, env={"LLMOPSAGENT_RUNTIME_DSN": DSN}, **kw)


H0_ALL = ("ir-manuals-bb52ba51", "ir-manuals-7dd35828", "ir-history-c6510147", "ir-history-543e3885")


# ---------------------------------------------------------------- V-PG2-SEL
def test_the_base_row_is_bound_with_one_read_and_the_index_chunk_table(tmp_path, db):
    fake = db(*(rows_of(r) for r in H0_ALL))
    pack, _ = make(tmp_path, stores=pg_stores())
    cfg = load_pg(pack)
    b = cfg["bindings"]["stores"]["manuals"]
    assert (b["release_id"], b["table"], b["schema"], b["backend"]) == (BASE["manuals"], "index_chunk", "sel", "pgvector")
    assert b["manifest"] == h0(BASE["manuals"]) and b["manifest_sha256"] == pg.canonical_sha256(h0(BASE["manuals"]))
    assert cfg["bindings"]["stores"]["history"]["release_id"] == BASE["history"]
    reads = [s for s in fake.log if "data_release" in s]
    assert len(reads) == 2 and all(s.endswith("= ANY(%s) ORDER BY release_id") for s in reads)   # one read per store, base and candidates together
    assert fake.log[:2] == ["connect", "SELECT current_user"]


@pytest.mark.parametrize("case", ["base", "candidate", "missing"])
def test_the_pg_choice_is_the_file_choice(tmp_path, db, case):
    """The same config over the same releases: flat-local folders and pgvector rows pick the same release (or the same IndexMissing)."""
    db(*(rows_of(r) for r in H0_ALL))
    index = {"base": None, "candidate": {"chunk_size": 40, "overlap": 8}, "missing": {"chunk_size": 50, "overlap": 5}}[case]
    extra = {"manuals": {"releases": ["ir-manuals-7dd35828"]}} if case == "candidate" else {}
    flat, _ = make(tmp_path / "flat", index, stores=extra)
    onpg, _ = make(tmp_path / "pg", index, stores=pg_stores(**extra))
    if case == "missing":
        with pytest.raises(IndexMissing) as ef:
            load(flat)
        with pytest.raises(IndexMissing) as ep:
            load_pg(onpg)
        assert ep.value.as_dict() == ef.value.as_dict() and str(ep.value) == str(ef.value)
        return
    assert load_pg(onpg)["bindings"]["stores"]["manuals"]["release_id"] == load(flat)["bindings"]["stores"]["manuals"]["release_id"]


def test_two_matching_rows_are_index_ambiguous(tmp_path, db):
    db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-manuals-bb52ba51", as_id="ir-manuals-copy0001"), rows_of("ir-history-c6510147"))
    pack, _ = make(tmp_path, stores=pg_stores(manuals={"releases": ["ir-manuals-copy0001"]}))
    with pytest.raises(IndexAmbiguous, match=r"^\(index_ambiguous\) node 'find_manuals'.*둘입니다: ir-manuals-bb52ba51 · ir-manuals-copy0001"):
        load_pg(pack)


def test_a_candidate_not_in_the_database_is_refused(tmp_path, db):
    db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-history-c6510147"))
    pack, _ = make(tmp_path, stores=pg_stores(manuals={"releases": ["ir-manuals-nope0000"]}))
    with pytest.raises(ReleaseError, match=r"^release 'ir-manuals-nope0000'이 DB에 적재되지 않았습니다$"):
        load_pg(pack)


def test_a_candidate_whose_embedding_does_not_match_is_not_a_match(tmp_path, db):
    db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-manuals-7dd35828", change=lambda r: r.update(embedding_mode="real")),
       rows_of("ir-history-c6510147"))
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores=pg_stores(manuals={"releases": ["ir-manuals-7dd35828"]}))
    with pytest.raises(IndexMissing):
        load_pg(pack)


@pytest.mark.parametrize("change, words", [
    (lambda r: r.update(store="history"), r"^release 'ir-manuals-7dd35828'는 저장소 'history'용입니다\(필요: manuals\)$"),
    (lambda r: r.update(kind="table"), r"^release 'ir-manuals-7dd35828'는 kind 'table'라 저장소 'manuals'\(vector\)에 backend 'pgvector'로 쓸 수 없습니다$")])
def test_a_candidate_of_another_store_or_kind_is_refused_not_skipped(tmp_path, db, change, words):
    """Only an embedding mismatch makes a candidate no candidate; a wrong store or kind is an error, as for folders."""
    db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-manuals-7dd35828", change=change), rows_of("ir-history-c6510147"))
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores=pg_stores(manuals={"releases": ["ir-manuals-7dd35828"]}))
    with pytest.raises(ReleaseError, match=words):
        load_pg(pack)


def test_collect_missing_with_the_database_lists_the_pg_store(tmp_path, db):
    db(*(rows_of(r) for r in H0_ALL))
    pack, _ = make(tmp_path, {"chunk_size": 50, "overlap": 5}, stores=pg_stores())
    cfg = load_pg(pack, collect_missing=True)
    assert [m["store"] for m in cfg["index_missing"]] == ["manuals"] and "manuals" not in cfg["bindings"]["stores"]
    assert cfg["index_unchecked"] == []


@pytest.mark.parametrize("value", ["ir-manuals-7dd35828", [], [3]])
def test_bad_releases_are_refused_without_a_database_too(tmp_path, monkeypatch, value):
    """The probe (no database) refuses what the start refuses, as for flat-local."""
    monkeypatch.setattr(pg, "connect", lambda dsn: pytest.fail("no database here"))
    pack, _ = make(tmp_path, stores=pg_stores(manuals={"releases": value}))
    with pytest.raises(ReleaseError, match="releases"):
        load(pack, pg_lookup=False, collect_missing=True)
    out = probe.main(str(pack))
    assert out["error"] == "build" and "releases" in out["message"] and "index_unchecked" not in out


def test_the_base_still_refuses_a_wrong_embedding(tmp_path, db):
    db(rows_of("ir-manuals-bb52ba51", change=lambda r: r.update(embedding_mode="real")), rows_of("ir-history-c6510147"))
    pack, _ = make(tmp_path, stores=pg_stores())
    with pytest.raises(ReleaseError, match=r"^\(embedding_mismatch\) release 'ir-manuals-bb52ba51'는 real 임베딩인데 실행 설정은 fake입니다$"):
        load_pg(pack)


def test_a_candidate_on_another_corpus_is_not_a_match(tmp_path, db):
    db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-history-c6510147"), rows_of("ir-history-543e3885"))
    pack, _ = make(tmp_path, stores=pg_stores(history={"releases": ["ir-history-543e3885"]}))   # same recipe, corpus v2
    assert load_pg(pack)["bindings"]["stores"]["history"]["release_id"] == "ir-history-c6510147"


def test_another_account_is_refused_before_reading(tmp_path, db):
    fake = db(rows_of("ir-manuals-bb52ba51"), rows_of("ir-history-c6510147"), user="index_loader")
    pack, _ = make(tmp_path, stores=pg_stores())
    with pytest.raises(ReleaseError, match=r"^실행용 DB 계정은 runtime_reader여야 합니다\(지금: index_loader\)$"):
        load_pg(pack)
    assert not [s for s in fake.log if "data_release" in s]


def test_no_dsn_in_any_message(tmp_path, db, monkeypatch):
    def boom(dsn):
        raise pg.PgError(f"DB에 연결할 수 없습니다: {dsn}")   # even a message that quotes the DSN is masked
    db(rows_of("ir-manuals-bb52ba51"))
    monkeypatch.setattr(pg, "connect", boom)
    pack, _ = make(tmp_path, stores=pg_stores())
    with pytest.raises(ReleaseError) as e:
        load_pg(pack)
    assert "SECRET-p1" not in str(e.value) and "[가림]" in str(e.value)


# ---------------------------------------------------------------- V-PG2-EQ
QSENSOR = {"torque_nm": 50.0, "tool_wear_min": 200}


def eq_pack(tmp_path, stores=None):
    """The H0 Pack with hybrid search on both stores and the sensor re-rank on history (C1-a15 R3 shape)."""
    pack, roots = make(tmp_path, stores=stores)
    doc = json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))
    for n in doc["graphs"]["g"]["nodes"]:
        if n["id"] == "find_manuals":
            n["params"].update(method="hybrid", top_k=3, return_metadata=["group"])
        if n["id"] == "find_similar":
            n["params"].update(method="hybrid", top_k=4, filter={"symptom_group": "{state.q.group}"}, return_metadata=SENSORS,
                               rerank={"fields": SENSORS, "query": "{state.q.sensors}", "weights": {"retrieval": 0.5, "sensor": 0.5}})
    (pack / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    assert load_pack(pack).ok
    return pack


def search(rt):
    """Each retrieve@2 node run once on the same input: (hits, node meta) by node id."""
    state = {"q": {"text": "공구 마모 경고 뒤 공구 교체", "group": "TW", "sensors": QSENSOR}}
    out = {}
    for n in rt.graph_doc["nodes"]:
        if n["kind"] == "retrieve@2":
            meta = {}
            writes, _ = rt._n_retrieve2(n, state, None, meta)
            out[n["id"]] = (writes[n["writes"][0]], meta)
    return out


def without_scores(v):
    if isinstance(v, dict):
        return {k: without_scores(x) for k, x in v.items() if k != "score"}
    if isinstance(v, list):
        return [without_scores(x) for x in v]
    return v


def scores(v):
    return [h["score"] for h in v[0]] + [r["score"] for r in v[1]["retrievals"]["results"]]


@pytest.mark.parametrize("float4", [False, True])
def test_flat_local_and_pgvector_give_the_same_result(tmp_path, db, float4):
    db(*(rows_of(r, float4=float4) for r in H0_ALL))
    flat = Runtime(eq_pack(tmp_path / "flat"), env={})
    onpg = Runtime(eq_pack(tmp_path / "pg", stores=pg_stores()), env={"LLMOPSAGENT_RUNTIME_DSN": DSN})
    a, b = search(flat), search(onpg)
    assert set(a) == {"find_manuals", "find_similar"} and all(len(a[k][0]) >= 2 for k in a)
    assert any(h["ranks"]["lexical"] and h["ranks"]["vector"] for h in a["find_similar"][0])   # hybrid really fused both lists
    for k in a:
        assert without_scores(b[k]) == without_scores(a[k])   # ids · order · locator · ranks · metadata · node meta (CHG-P01 6)
        if float4:
            assert all(abs(x - y) <= 1e-5 for x, y in zip(scores(a[k]), scores(b[k])))
            assert len(scores(a[k])) == len(scores(b[k]))
        else:
            assert scores(b[k]) == scores(a[k])
    assert a["find_similar"][0][0]["locator"]["record_id"] and a["find_manuals"][0][0]["locator"]["section"]
    ma, mb = flat.manifest()["evidence_index_releases"], onpg.manifest()["evidence_index_releases"]
    for k in ma:
        assert set(mb[k]) == set(ma[k])
        assert {x: mb[k][x] for x in mb[k] if x not in ("manifest_sha256", "location")} == {x: ma[k][x] for x in ma[k] if x not in ("manifest_sha256", "location")}
        raw = h0(BASE[k])
        assert raw["backend"] == "flat-local" and mb[k]["manifest_sha256"] == pg.canonical_sha256(raw)   # the manifest as written (CHG-P01 5)
        assert mb[k]["location"] == "postgresql:sel.index_chunk"
    la, lb = trace.release_line(flat), trace.release_line(onpg)
    assert lb["data_release_id"] == la["data_release_id"] and lb["vector_db"] == la["vector_db"]   # same drs (PRES-P10)



def vector_pack(tmp_path, stores=None):
    """EV-010 CHG-Q05: the eq_pack Pack with method vector on both stores, so the scores are the cosine values themselves (hybrid
    scores come from ranks and do not move when a vector moves a little)."""
    pack = eq_pack(tmp_path, stores=stores)
    doc = json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))
    for n in doc["graphs"]["g"]["nodes"]:
        if n["id"] in ("find_manuals", "find_similar"):
            n["params"]["method"] = "vector"
    (pack / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    assert load_pack(pack).ok
    return pack


@pytest.mark.parametrize("float4", [False, True])
def test_flat_local_and_pgvector_give_the_same_result_by_vector(tmp_path, db, float4):
    """EV-010 CHG-Q05 (EV-008 CHG-P01 6 on cosine scores): same ids · order · locator · ranks · metadata · node meta; scores equal
    with the vectors as stored, within 1e-5 after a float4 round trip; the same drs."""
    db(*(rows_of(r, float4=float4) for r in H0_ALL))
    flat = Runtime(vector_pack(tmp_path / "flat"), env={})
    onpg = Runtime(vector_pack(tmp_path / "pg", stores=pg_stores()), env={"LLMOPSAGENT_RUNTIME_DSN": DSN})
    a, b = search(flat), search(onpg)
    assert set(a) == {"find_manuals", "find_similar"} and all(len(a[k][0]) >= 2 for k in a)
    assert all(h["ranks"]["vector"] and not h["ranks"].get("lexical") for k in a for h in a[k][0])   # the vector list alone
    for k in a:
        assert without_scores(b[k]) == without_scores(a[k])
        assert len(scores(a[k])) == len(scores(b[k]))
        if float4:
            assert all(abs(x - y) <= 1e-5 for x, y in zip(scores(a[k]), scores(b[k])))
        else:
            assert scores(b[k]) == scores(a[k])
    la, lb = trace.release_line(flat), trace.release_line(onpg)
    assert lb["data_release_id"] == la["data_release_id"] and lb["vector_db"] == la["vector_db"]   # same drs (PRES-P10)

def test_a_whole_pg_run_succeeds_with_the_engine_guard_passed(tmp_path, db):
    db(*(rows_of(r) for r in H0_ALL))
    rt = Runtime(eq_pack(tmp_path, stores=pg_stores()), env={"LLMOPSAGENT_RUNTIME_DSN": DSN})
    res = rt.run({"text": "공구 마모", "group": "TW", "sensors": QSENSOR}, context={"run_id": "run_01JC0000000000000000000801",
                 "trace_id": "0123456789abcdef0123456789abc801", "origin": "evaluation", "evaluation_run_id": "p1", "deployment_id": None,
                 "retention_class": "dev"}, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    assert "backend_unsupported" not in json.dumps(res, ensure_ascii=False)


# ---------------------------------------------------------------- V-PG1-B
def test_retrieve1_on_a_built_release_reads_index_chunk_in_the_retrieve1_shape(tmp_path, db):
    fake = db(rows_of("ir-manuals-bb52ba51"))
    flat, _ = make(tmp_path / "flat", kind="retrieve@1")
    onpg, _ = make(tmp_path / "pg", kind="retrieve@1", stores={"manuals": dict(PG_LINE)})
    rf, rp = Runtime(flat, env={}), Runtime(onpg, env={"LLMOPSAGENT_RUNTIME_DSN": DSN})
    assert rp.bindings["stores"]["manuals"]["table"] == "index_chunk"
    n = next(x for x in rp.graph_doc["nodes"] if x["kind"] == "retrieve@1")
    state = {"q": {"text": "공구 마모 교체"}}
    meta_f, meta_p = {}, {}
    (wf, _), (wp, _) = rf._n_retrieve(n, state, None, meta_f), rp._n_retrieve(n, state, None, meta_p)
    hits_f, hits_p = wf[n["writes"][0]], wp[n["writes"][0]]
    assert hits_p == hits_f and hits_p   # same hits, @1 keys only (no locator · ranks)
    assert meta_p == meta_f and set(meta_p["retrievals"]) == {"store", "index_release", "results", "fallback_used", "fallback_reason"}
    ma, mb = rf.manifest()["evidence_index_releases"]["manuals"], rp.manifest()["evidence_index_releases"]["manuals"]
    assert set(mb) == set(ma) and {"index_recipe_sha256", "corpus_release", "builder"} <= set(mb)   # the recipe fields, as flat-local @1
    assert all(mb[k] == ma[k] for k in ("index_recipe_sha256", "corpus_release", "index_recipe", "build_sha256", "builder"))
    assert set(hits_p[0]) == {"source_type", "store", "id", "index_release", "score", "text", "fallback_used"}
    assert any('FROM "sel"."index_chunk"' in s and ", locator," in s for s in fake.log)


def test_the_table_rule(tmp_path):
    built = h0("ir-manuals-bb52ba51")
    assert chunk_table(built, "vector", "manuals") == "index_chunk"
    assert chunk_table({k: v for k, v in built.items() if k != "builder"}, "vector", "manuals") == "manual_chunk"   # V-PG1: as 0.12.0
    assert chunk_table({"table": "my_chunks"}, "vector", "manuals") == "my_chunks"
    assert chunk_table({"builder": {"name": "other"}}, "vector", "manuals") == "manual_chunk"
    assert chunk_table(built, "table", "rows") == "rows"   # a table store keeps its rule


# ---------------------------------------------------------------- V-PROBE-PG
def test_probe_projection_lists_the_pg_store_it_cannot_check(tmp_path, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)   # the probe gets no DSN and reads no database
    monkeypatch.setattr(pg, "connect", lambda dsn: pytest.fail("the probe must not connect"))
    pack, _ = make(tmp_path, stores={"manuals": dict(PG_LINE)})
    out = probe.main(str(pack))
    assert "error" not in out, out
    assert out["index_missing"] == []
    assert out["index_unchecked"] == [{"store": "manuals", "node": "find_manuals", "index_recipe_sha256": h0(BASE["manuals"])["index_recipe_sha256"]}]


def test_probe_projection_of_a_flat_pack_has_an_empty_list(tmp_path):
    pack, _ = make(tmp_path)
    out = probe.main(str(pack))
    assert out["index_missing"] == [] and out["index_unchecked"] == []


def broken(pack):
    cfg = pack / "execution-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("models: {}", "models: {ghost: {release: no-such-model}}"), encoding="utf-8")
    return pack


def test_probe_error_with_a_pg_retrieve2_store_carries_the_list(tmp_path, monkeypatch):
    """The config reads, the graph build fails afterwards: index_unchecked rides on the error shape."""
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    pack, _ = make(tmp_path, stores={"manuals": dict(PG_LINE)})
    monkeypatch.setattr(Runtime, "projection", lambda self: (_ for _ in ()).throw(RuntimeError("graph build failed")))
    out = probe.main(str(pack))
    assert out["error"] == "build"
    assert set(out) == {"error", "message", "index_unchecked"}   # index_missing stays out while it is empty
    assert [u["store"] for u in out["index_unchecked"]] == ["manuals"]


def test_probe_error_with_both_lists(tmp_path, monkeypatch):
    """A flat-local store with no version of its recipe (IndexMissing) next to a pg retrieve@2 store: both lists on the error."""
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    pack, _ = make(tmp_path, {"chunk_size": 40, "overlap": 8}, stores={"history": dict(PG_LINE)})
    out = probe.main(str(pack))
    assert out["message"].startswith("IndexMissing:")
    assert [m["store"] for m in out["index_missing"]] == ["manuals"] and [u["store"] for u in out["index_unchecked"]] == ["history"]


def test_probe_error_without_a_pg_retrieve2_store_keeps_the_old_shape(tmp_path):
    pack, _ = make(tmp_path)
    out = probe.main(str(broken(pack)))
    assert set(out) == {"error", "message"}
    pack2, _ = make(tmp_path / "b")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Runtime, "projection", lambda self: (_ for _ in ()).throw(RuntimeError("graph build failed")))
        assert probe.main(str(pack2)) == {"error": "build", "message": "RuntimeError: graph build failed"}
