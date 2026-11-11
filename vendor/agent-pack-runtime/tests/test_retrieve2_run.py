"""EV-006 S2 · V-ENGINE · V-PARITY (CHG-R02 ~ R04 · R09, D-266 flat-local only): retrieve@2 runs over a flat-local release made here
with the fake 64-number embedding. V-PARITY: the history search (filter → BM25 + vector → RRF 60 → top 10 → sensor re-rank 0.5 / 0.5)
gives the same order and scores as the C1-a15 R3 code, copied below as REF_* (c1a15-maint-guide 6cf03bd retrieval/tokens.py ·
bm25.py · search.py, read only). V-ENGINE: the three methods, filter · relax, threshold, ties, empty results, one embedding call per
search (none in fake mode, none for lexical), result fields and the node meta / trace.retrieval keys of @2."""
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

import packrt
from packrt.ir.releases import dir_hash
from packrt.run.embedding import fake_embedding
from packrt.run.engine import Runtime
from tracewriter import RunSink, TraceWriter

SENSORS = ["torque_nm", "tool_wear_min"]
HISTORY = [   # record_id, symptom_group, narrative, torque, wear, source
    ("R-01", "TW", "공구 마모 경고 뒤 공구를 바꿈 마모 폭 0.35mm", 41.0, 210, "generated"),
    ("R-02", "TW", "공구 끝 깨짐 확인 후 교체 토크 상승 동반", 55.0, 180, "generated"),
    ("R-03", "TW", "마모 폭 기록 후 그대로 운전 다음 점검에 교체", 39.5, 90, "generated"),
    ("R-04", "TW", "공구 마모와 토크 상승 주축 부하 점검", 60.2, 220, "generated"),
    ("R-05", "TW", "공구를 바꾸고 마모 폭을 기록함", 44.0, 200, "approved_report"),
    ("R-06", "PW", "전력 이상 알람 뒤 공급 전압 확인", 30.0, 50, "generated"),
    ("R-07", "PW", "회전 속도와 토크를 다시 잼 전력 범위 밖", 62.0, 120, "generated"),
    ("R-08", "HD", "방열 불량 공기 온도와 공정 온도 차가 작음", 35.0, 40, "generated"),
    ("R-09", "TW", "공구 마모 경고 뒤 공구를 바꿈 마모 폭 0.35mm", 41.0, 210, "generated"),   # same text as R-01: a tie
]
MANUAL = [   # id, section, group, text
    ("MG-A#s1#0", "s1", "TW", "## 1 점검 준비 전원을 내리고 잠금 표지를 단다"),
    ("MG-A#s2#0", "s2", "TW", "## 2 공구 마모 확인 공구 끝 마모 폭을 재고 0.3mm를 넘으면 교체"),
    ("MG-B#s1#0", "s1", "PW", "## 1 전력 점검 공급 전압과 전류를 잰다"),
    ("MG-B#s2#0", "s2", "PW", "## 2 회전 속도 확인 회전 속도와 토크를 다시 잰다"),
]
QUERY = "공구 마모 경고 토크 상승"
QSENSOR = {"torque_nm": 50.0, "tool_wear_min": 200}


# ---- C1-a15 R3 reference (copied, read only) ----
_W = re.compile(r"[0-9A-Za-z가-힣._%-]+")
_H = re.compile(r"[가-힣]")


def REF_tokenize(text):
    t = []
    for w in _W.findall(text or ""):
        t.append(w.lower())
        if _H.search(w) and len(w) > 2:
            t.extend(w[i:i + 2] for i in range(len(w) - 1))
    return t


class REF_BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b, self.docs, self.lengths, self.df = k1, b, {}, {}, Counter()

    def add(self, doc_id, text):
        terms = Counter(REF_tokenize(text))
        self.docs[doc_id], self.lengths[doc_id] = terms, sum(terms.values())
        self.df.update(terms.keys())

    def rank(self, query, doc_ids):
        q, n = REF_tokenize(query), len(self.docs)
        avg = sum(self.lengths.values()) / n
        scores = {}
        for d in doc_ids:
            tf, length, s = self.docs[d], self.lengths[d], 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * length / avg))
            scores[d] = s
        return sorted(doc_ids, key=lambda d: (-scores[d], d))


def REF_similar(group, qtext, qsensor, top_k=10, k=60, w_rrf=0.5, w_sensor=0.5, candidates=300):
    docs = {r[0]: r for r in HISTORY}
    bm = REF_BM25()
    for r in HISTORY:
        bm.add(r[0], r[2])
    base = [r for r in HISTORY if r[5] == "generated"]
    std = {f: math.sqrt(sum((x - sum(v) / len(v)) ** 2 for x in v) / len(v)) or 1.0
           for f, v in (("torque_nm", [r[3] for r in base]), ("tool_wear_min", [r[4] for r in base]))}
    pool = sorted(r[0] for r in HISTORY if r[1] == group)
    qv = fake_embedding(qtext)

    def cos(a, b):
        na, nb = math.sqrt(sum(x * x for x in a)) or 1.0, math.sqrt(sum(x * x for x in b)) or 1.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    sims = {d: cos(qv, fake_embedding(docs[d][2])) for d in pool}   # ranked before any rounding, as C1-a15 and FlatLocal.search do
    semantic = sorted(pool, key=lambda d: (-sims[d], d))[:candidates]
    fused = {}
    for ranks in (bm.rank(qtext, pool), semantic):
        for i, d in enumerate(ranks, start=1):
            fused[d] = fused.get(d, 0.0) + 1.0 / (k + i)
    top = sorted(fused, key=lambda d: (-fused[d], d))[:top_k]
    sens = {d: {"torque_nm": docs[d][3], "tool_wear_min": docs[d][4]} for d in top}
    dist = {d: math.sqrt(sum(((qsensor[f] - sens[d][f]) / std[f]) ** 2 for f in SENSORS)) for d in top}
    mr, md = max(fused[d] for d in top), (max(dist.values()) or 1.0)
    score = {d: w_rrf * (fused[d] / mr) + w_sensor * (1 - dist[d] / md) for d in top}
    return [(d, round(score[d], 6)) for d in sorted(top, key=lambda d: (-score[d], d))]


# ---- fixture made here ----
def _release(root, rid, store, chunks, extra=None):
    d = root / rid
    d.mkdir(parents=True)
    (d / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in chunks), encoding="utf-8")
    (d / "vectors.json").write_text(json.dumps({c["id"]: fake_embedding(c["text"]) for c in chunks}, sort_keys=True), encoding="utf-8")
    m = {"kind": "index", "release_id": rid, "store": store, "backend": "flat-local", "chunks": len(chunks),
         "embedding": {"provider": "openai", "model": "text-embedding-3-small", "dim": 64, "mode": "fake"},
         "files": {f: hashlib.sha256((d / f).read_bytes()).hexdigest() for f in ("chunks.jsonl", "vectors.json")}, **(extra or {})}
    m["index_sha256"] = dir_hash(d)
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _norm():
    base = [r for r in HISTORY if r[5] == "generated"]
    out = {}
    for f, i in (("torque_nm", 3), ("tool_wear_min", 4)):
        v = [r[i] for r in base]
        mean = sum(v) / len(v)
        out[f] = {"mean": round(mean, 6), "std": round(math.sqrt(sum((x - mean) ** 2 for x in v) / len(v)), 6), "n": len(v)}
    return out


def recipe_sha256(store_doc, node_index=None):
    """The contract's index recipe hash (CHG-R05, D-267: seven keys, canonical JSON) — counted here so this file runs without
    packrt.index; on the merged tree it must equal what the h side binds with (S3b), or the store is index_missing."""
    idx, ni = store_doc.get("index") or {}, node_index or {}
    meta = idx.get("metadata") if idx.get("metadata") is not None else store_doc.get("metadata_required") or []
    recipe = {"split": idx.get("split"), "chunk_size": ni.get("chunk_size"), "overlap": ni.get("overlap", 0), "metadata": sorted(set(meta)),
              "chunker": idx.get("chunker"), "chunker_sha256": None, "rerank_norm": idx.get("rerank_norm")}
    return recipe, hashlib.sha256(json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


CORPUS = {"history": {"id": "hc-t1", "sha256": "1" * 64}, "manuals": {"id": "mc-t1", "sha256": "2" * 64}}


def make(tmp_path, nodes, manuals_filterable=("group",)):
    roots = tmp_path / "releases"
    hist = [{"id": f"{r[0]}#0", "text": r[2], "metadata": {"symptom_group": r[1], "torque_nm": r[3], "tool_wear_min": r[4], "source": r[5],
             "evidence_type": "history"}, "locator": {"doc": r[0], "record_id": r[0], "char_start": 0, "char_end": len(r[2])}} for r in HISTORY]
    man = [{"id": i, "text": t, "metadata": {"group": g, "kind": "manual", "evidence_type": "manual"},
            "locator": {"doc": i.split("#")[0], "section": s, "char_start": 0, "char_end": len(t)}} for i, s, g, t in MANUAL]
    pack = tmp_path / "pack"
    (pack / "schemas").mkdir(parents=True)
    (pack / "schemas" / "q.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (pack / "schemas" / "hit.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (pack / "schemas" / "note.json").write_text(json.dumps({"type": "string"}), encoding="utf-8")
    (pack / "templates").mkdir()
    (pack / "templates" / "n.md").write_text("done\n", encoding="utf-8")
    emb = {"provider": "openai", "model": "text-embedding-3-small"}
    state = {"q": "Q", "note": "Note", **{n["writes"][0]: {"type": "list[Hit]"} for n in nodes}}
    names = [n["id"] for n in nodes] + ["fin"]
    doc = {"ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__}, "pack": {"id": "r2run", "version": "V0", "title": "t"},
           "env": {"required": [], "optional": []},
           "stores": {"history": {"type": "vector", "index": {"embedding": emb, "split": "record", "metadata": ["source", "symptom_group"] + SENSORS,
                                                             "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}}, "filterable": ["symptom_group"]},
                      "manuals": {"type": "vector", "index": {"embedding": emb, "split": "section", "metadata": ["group", "kind"]},
                                  "filterable": list(manuals_filterable)}},
           "schemas": {"Q": "schemas/q.json", "Hit": "schemas/hit.json", "Note": "schemas/note.json"},
           "graphs": {"g": {"state": state, "nodes": nodes + [{"id": "fin", "kind": "render@1", "params": {"template": "templates/n.md", "format": "md", "to": "note"},
                                                               "reads": ["q"], "writes": ["note"]}],
                            "edges": [["START", names[0]]] + [[a, b] for a, b in zip(names, names[1:])] + [["fin", "END"]]}},
           "entrypoints": [{"graph": "g", "input": "Q", "output": "Note"}], "trace": {"contract": 1}}
    (pack / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for store, rid, chunks, extra in (("history", "ir-history-t1", hist, {"rerank_norm": _norm()}), ("manuals", "ir-manuals-t1", man, {})):
        ni = next(((n["params"].get("index")) for n in nodes if n["kind"] == "retrieve@2" and n["params"]["store"] == store), None)
        recipe, sha = recipe_sha256(doc["stores"][store], ni)
        _release(roots, rid, store, chunks, {**extra, "corpus_release": CORPUS[store], "index_recipe": recipe, "index_recipe_sha256": sha,
                                            "build_sha256": "0" * 64, "builder": {"name": "packrt.index", "version": 1}})
    (pack / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: g\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\n'
        "stores:\n  history: {backend: flat-local, release: ir-history-t1, embedding: {mode: fake}}\n"
        "  manuals: {backend: flat-local, release: ir-manuals-t1, embedding: {mode: fake}}\nmodels: {}\n", encoding="utf-8")
    return pack


class CountingProvider:
    def __init__(self):
        self.calls = 0

    def embed(self, *a, **k):
        self.calls += 1
        raise AssertionError("fake mode must not call the provider")


def run(tmp_path, nodes, q=None, **kw):
    rt = Runtime(make(tmp_path, nodes, **kw), env={})
    rt.provider = CountingProvider()
    log = []

    class Conn:
        closed = False

        def transaction(self):
            class T:
                def __enter__(s):
                    return s

                def __exit__(s, *e):
                    return False
            return T()

        def cursor(self):
            class C:
                def execute(s, sql, params=()):
                    log.append((" ".join(sql.split()), list(params)))
                    s.one = (params[1],) if "INSERT INTO trace.event" in sql else None

                def fetchone(s):
                    return s.one
            return C()

        def close(self):
            pass
    ctx = {"run_id": "run_01JC0000000000000000000206", "trace_id": "0123456789abcdef0123456789abc206", "origin": "evaluation",
           "evaluation_run_id": "s2", "deployment_id": None, "retention_class": "dev"}
    res = rt.run(q or {"text": QUERY, "group": "TW", "sensors": QSENSOR}, context=dict(ctx),
                 sink=RunSink(TraceWriter(payload_root=tmp_path / "pl", secrets=[], connect=lambda: Conn()), dict(ctx)), review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    assert rt.provider.calls == 0
    return rt, res, log


def node(nid, store, writes, **params):
    return {"id": nid, "kind": "retrieve@2", "reads": ["q"], "writes": [writes], "params": {"store": store, "query": "{state.q.text}", **params}}


def written(log, nid):
    """The node's hits from its node_ended meta results and trace.retrieval row."""
    rows = [p for sql, p in log if sql.startswith("INSERT INTO trace.retrieval") and p[2] == nid]
    metas = [p[8].obj for sql, p in log if sql.startswith("INSERT INTO trace.event") and p[5] == nid and p[4] == "node_ended"]
    return rows[0], metas[0]["meta"]


R3 = dict(filter={"symptom_group": "{state.q.group}"}, method="hybrid", fuse={"method": "rrf", "k": 60, "pool": 300},
          rerank={"fields": SENSORS, "query": "{state.q.sensors}", "weights": {"retrieval": 0.5, "sensor": 0.5}}, top_k=10,
          return_metadata=SENSORS)


def test_parity_with_c1a15_r3(tmp_path):
    rt, res, log = run(tmp_path, [node("similar", "history", "sim", **R3)])
    row, meta = written(log, "similar")
    got = [(r["id"].split("#")[0], r["score"]) for r in meta["results"]]
    assert got == REF_similar("TW", QUERY, QSENSOR)
    assert meta["method"] == "hybrid" and meta["kind"] == "retrieve@2"
    assert meta["index_recipe_sha256"] == json.loads((tmp_path / "releases" / "ir-history-t1" / "manifest.json").read_text(encoding="utf-8"))["index_recipe_sha256"]
    assert all(set(r) == {"id", "score", "source_type", "locator", "ranks"} for r in meta["results"])
    assert all(r["locator"]["record_id"] == r["id"].split("#")[0] for r in meta["results"])
    assert row[3] == "history" and row[4] == "ir-history-t1" and row[9] is False   # trace.retrieval columns unchanged, results in jsonb
    assert "approved_report" not in json.dumps(meta)   # R-05 is a candidate; its source only shaped nothing but the norm


def test_parity_when_top_k_and_pool_cut(tmp_path):
    """Normalising over the top_k (not the whole filtered set) and the pool on the vector side only are both seen here."""
    p = {**R3, "top_k": 3, "fuse": {"method": "rrf", "k": 60, "pool": 2}}
    rt, res, log = run(tmp_path, [node("similar", "history", "sim", **p)])
    _, meta = written(log, "similar")
    assert [(r["id"].split("#")[0], r["score"]) for r in meta["results"]] == REF_similar("TW", QUERY, QSENSOR, top_k=3, candidates=2)


def test_tie_and_rank_fields(tmp_path):
    rt, res, log = run(tmp_path, [node("lex", "history", "a", method="lexical", filter={"symptom_group": "TW"}, top_k=9)])
    _, meta = written(log, "lex")
    ids = [r["id"] for r in meta["results"]]
    assert ids.index("R-01#0") + 1 == ids.index("R-09#0")   # same text, same score: id order
    assert all(r["ranks"]["vector"] is None and r["ranks"]["lexical"] for r in meta["results"])
    assert meta["results"][0]["score"] == 1.0   # lexical without re-rank: BM25 ÷ the top score
    assert meta["embedding_model"] is None


def test_vector_method_matches_retrieve1_scores(tmp_path):
    nodes = [node("v2", "manuals", "a", method="vector", top_k=3),
             {"id": "v1", "kind": "retrieve@1", "reads": ["q"], "writes": ["b"], "params": {"store": "history", "query": "{state.q.text}", "top_k": 3}}]
    rt, res, log = run(tmp_path, nodes)
    _, m2 = written(log, "v2")
    assert [r["score"] for r in m2["results"]] == sorted((r["score"] for r in m2["results"]), reverse=True)
    from packrt.run.backends import FlatLocal
    want = FlatLocal(rt.bindings["stores"]["manuals"]["path"]).search(fake_embedding(QUERY), 3)
    assert [(h["id"], h["score"]) for h in want] == [(r["id"], r["score"]) for r in m2["results"]]


def test_threshold_relax_and_empty(tmp_path):
    nodes = [node("th", "history", "a", **{**R3, "score_threshold": 0.7}),
             node("rx", "manuals", "b", method="hybrid", filter={"group": "XX"}, fallback={"min_results": 2, "relax": ["group"]}, top_k=2),
             node("none", "manuals", "c", method="lexical", filter={"group": "XX"}, top_k=2)]
    rt, res, log = run(tmp_path, nodes)
    _, th = written(log, "th")
    assert th["results"] and all(r["score"] >= 0.7 for r in th["results"])
    assert len(th["results"]) < len(REF_similar("TW", QUERY, QSENSOR))
    _, rx = written(log, "rx")
    assert rx["fallback_used"] is True and "조건을 빼고 다시 검색" in rx["fallback_reason"] and len(rx["results"]) == 2
    _, none = written(log, "none")
    assert none["results"] == [] and none["fallback_used"] is False


def test_hits_in_state_carry_locator_ranks_and_asked_metadata(tmp_path):
    rt, res, log = run(tmp_path, [node("similar", "history", "sim", **R3)])
    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "pl").rglob("*") if p.is_file()]
    lists = [x["sim"] for x in payloads if isinstance(x, dict) and isinstance(x.get("sim"), list) and x["sim"]]   # node output {state key: hits}
    assert lists, "the node output payload"
    h = lists[0][0]
    assert set(h) >= {"source_type", "store", "id", "index_release", "score", "text", "fallback_used", "locator", "ranks", "metadata"}
    assert set(h["metadata"]) == set(SENSORS) and h["source_type"] == "history"


def test_real_mode_embeds_once_per_search_and_never_for_lexical(tmp_path):
    calls = []

    class P:
        def embed(self, provider, model, texts):
            calls.append(texts)
            return [fake_embedding(t) for t in texts]
    pack = make(tmp_path, [node("h", "history", "a", **R3), node("l", "manuals", "b", method="lexical")])
    cfg = pack / "execution-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("embedding: {mode: fake}", "embedding: {mode: real, key_env: OPENAI_API_KEY}"), encoding="utf-8")
    for d in (tmp_path / "releases").iterdir():
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        m["embedding"]["mode"] = "real"
        (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    rt = Runtime(pack, env={"OPENAI_API_KEY": "fake-openai-key-s2-0123456789"})
    rt.provider = P()
    res = rt.run({"text": QUERY, "group": "TW", "sensors": QSENSOR}, context={"run_id": "run_01JC0000000000000000000207", "trace_id": "0123456789abcdef0123456789abc207",
                 "origin": "evaluation", "evaluation_run_id": "s2", "deployment_id": None, "retention_class": "dev"}, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    assert calls == [[QUERY]]   # hybrid: one call; lexical: none


def test_relax_embeds_again_like_retrieve1(tmp_path):
    calls = []

    class P:
        def embed(self, provider, model, texts):
            calls.append(texts)
            return [fake_embedding(t) for t in texts]
    pack = make(tmp_path, [node("rx", "manuals", "b", method="hybrid", filter={"group": "XX"}, fallback={"min_results": 1, "relax": ["group"]})])
    cfg = pack / "execution-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("embedding: {mode: fake}", "embedding: {mode: real, key_env: OPENAI_API_KEY}"), encoding="utf-8")
    for d in (tmp_path / "releases").iterdir():
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        m["embedding"]["mode"] = "real"
        (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    rt = Runtime(pack, env={"OPENAI_API_KEY": "fake-openai-key-s2-0123456789"})
    rt.provider = P()
    res = rt.run({"text": QUERY, "group": "TW", "sensors": QSENSOR}, context={"run_id": "run_01JC0000000000000000000209", "trace_id": "0123456789abcdef0123456789abc209",
                 "origin": "evaluation", "evaluation_run_id": "s2", "deployment_id": None, "retention_class": "dev"}, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    assert calls == [[QUERY], [QUERY]]   # the search and the relaxed search, one call each (as retrieve@1)


def test_non_flat_local_backend_is_refused(tmp_path):
    """N-6: the config is refused first, when it is read (h's releases.py — on the merged tree); the engine keeps a second guard."""
    try:
        import packrt.index   # noqa: F401  (present once h is merged)
    except ImportError:
        pass
    else:
        pack = make(tmp_path / "cfg", [node("h", "history", "a", **R3)])
        cfg = pack / "execution-config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("history: {backend: flat-local, release: ir-history-t1, embedding: {mode: fake}}",
                                                               "history: {backend: \"custom.store:Mine\", release: ir-history-t1, embedding: {mode: fake}}"),
                       encoding="utf-8")   # EV-008: pgvector is allowed now; any other backend is still refused
        with pytest.raises(Exception) as e:
            Runtime(pack, env={})
        assert "backend_unsupported" in str(e.value)
    rt = Runtime(make(tmp_path, [node("h", "history", "a", **R3)]), env={})
    rt.bindings["stores"]["history"]["backend"] = "custom.store:Mine"   # the engine's second guard
    res = rt.run({"text": QUERY, "group": "TW", "sensors": QSENSOR}, context={"run_id": "run_01JC0000000000000000000208", "trace_id": "0123456789abcdef0123456789abc208",
                 "origin": "evaluation", "evaluation_run_id": "s2", "deployment_id": None, "retention_class": "dev"}, review={"policy": "auto_approve"})
    assert res["status"] == "failed" and "backend_unsupported" in json.dumps(res, ensure_ascii=False)
    assert "retrieve@2는 flat-local · pgvector만 씁니다(store 'history': custom.store:Mine)" in json.dumps(res, ensure_ascii=False)


@pytest.mark.parametrize("case", ["norm", "query", "chunk"])
def test_rerank_invalid(tmp_path, case):
    """D-273 R-1: a missing norm, query sensor or chunk sensor ends the node with rerank_invalid (not a Python error name)."""
    pack = make(tmp_path, [node("h", "history", "a", **R3)])
    q = {"text": QUERY, "group": "TW", "sensors": dict(QSENSOR)}
    if case == "query":
        q["sensors"].pop("torque_nm")
    for d in (tmp_path / "releases").iterdir():
        if d.name != "ir-history-t1":
            continue
        if case == "norm":
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            m["rerank_norm"].pop("tool_wear_min")
            (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        if case == "chunk":
            lines = [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
            for c in lines:
                c["metadata"]["torque_nm"] = None
            (d / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in lines), encoding="utf-8")
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            m["files"]["chunks.jsonl"] = hashlib.sha256((d / "chunks.jsonl").read_bytes()).hexdigest()
            m["index_sha256"] = dir_hash(d)
            (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    rt = Runtime(pack, env={})
    res = rt.run(q, context={"run_id": "run_01JC0000000000000000000210", "trace_id": "0123456789abcdef0123456789abc210", "origin": "evaluation",
                             "evaluation_run_id": "s2", "deployment_id": None, "retention_class": "dev"}, review={"policy": "auto_approve"})
    assert res["status"] == "failed" and "rerank_invalid" in json.dumps(res, ensure_ascii=False)
