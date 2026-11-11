"""EV-015 (D-414, packrt-1): retrieve@2 `rerank.pool` — how many candidates the sensor re-rank sees before top_k is kept. Without it (or
equal to top_k) the search is byte for byte what it was: the first top_k by the retrieval score, re-ranked among themselves. With a larger
pool the re-rank sees that many and keeps the best top_k, so the sensors can bring in a record the query text alone left out. The
expected values come from REF_similar_pool below (the C1-a15 R3 copy in test_retrieve2_run with the pool cut moved), not from packrt."""
import json
import math

from test_retrieve2_load import make as load_make, messages, node as load_node
from test_retrieve2_run import (HISTORY, QSENSOR, QUERY, R3, REF_BM25, SENSORS, REF_similar, node, run, written)

from packrt.ir import load_pack
from packrt.ir.patch import apply_patch
from packrt.run.embedding import fake_embedding


def REF_similar_pool(group, qtext, qsensor, top_k, pool=None, method="hybrid", k=60, w_rrf=0.5, w_sensor=0.5):
    """The R3 history search with the re-rank over the first `pool` (default top_k) candidates, then the first top_k."""
    pool = top_k if pool is None else pool
    docs = {r[0]: r for r in HISTORY}
    bm = REF_BM25()
    for r in HISTORY:
        bm.add(r[0], r[2])
    base = [r for r in HISTORY if r[5] == "generated"]
    std = {f: math.sqrt(sum((x - sum(v) / len(v)) ** 2 for x in v) / len(v)) or 1.0
           for f, v in (("torque_nm", [r[3] for r in base]), ("tool_wear_min", [r[4] for r in base]))}
    ids = sorted(r[0] for r in HISTORY if r[1] == group)
    qv = fake_embedding(qtext)

    def cos(a, b):
        na, nb = math.sqrt(sum(x * x for x in a)) or 1.0, math.sqrt(sum(x * x for x in b)) or 1.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    sims = {d: cos(qv, fake_embedding(docs[d][2])) for d in ids}
    semantic = sorted(ids, key=lambda d: (-sims[d], d))
    if method == "hybrid":
        score = {}
        for ranks in (bm.rank(qtext, ids), semantic):
            for i, d in enumerate(ranks, start=1):
                score[d] = score.get(d, 0.0) + 1.0 / (k + i)
    elif method == "vector":
        score = {d: round(sims[d], 6) for d in ids}   # FlatLocal.search rounds the cosine it hands on
    else:
        raise ValueError(method)
    cand = sorted(score, key=lambda d: (-score[d], d))[:pool]
    dist = {d: math.sqrt(sum(((qsensor[f] - docs[d][3 if f == "torque_nm" else 4]) / std[f]) ** 2 for f in SENSORS)) for d in cand}
    mr, md = max(score[d] for d in cand), (max(dist.values()) or 1.0)
    final = {d: w_rrf * (score[d] / mr) + w_sensor * (1 - dist[d] / md) for d in cand}
    return [(d, round(final[d], 6)) for d in sorted(cand, key=lambda d: (-final[d], d))[:top_k]]


def ids_scores(meta):
    return [(r["id"].split("#")[0], r["score"]) for r in meta["results"]]


def test_reference_sees_the_pool():
    """The fixture is one where the pool matters: with top_k 2 the sensors change which records are kept."""
    assert REF_similar_pool("TW", QUERY, QSENSOR, top_k=2) == REF_similar("TW", QUERY, QSENSOR, top_k=2)   # no pool = R3 as before
    assert {d for d, _ in REF_similar_pool("TW", QUERY, QSENSOR, top_k=2, pool=6)} != {d for d, _ in REF_similar_pool("TW", QUERY, QSENSOR, top_k=2)}


def test_no_pool_and_pool_equal_to_top_k_are_the_search_of_before(tmp_path):
    p = {**R3, "top_k": 2}
    _, _, log_a = run(tmp_path / "a", [node("similar", "history", "sim", **p)])
    _, _, log_b = run(tmp_path / "b", [node("similar", "history", "sim", **{**p, "rerank": {**p["rerank"], "pool": 2}})])
    _, a = written(log_a, "similar")
    _, b = written(log_b, "similar")
    assert ids_scores(a) == REF_similar("TW", QUERY, QSENSOR, top_k=2)
    assert a["results"] == b["results"]   # ids, scores, locators and ranks byte for byte


def test_a_larger_pool_lets_the_sensors_choose_the_top_k(tmp_path):
    p = {**R3, "top_k": 2, "rerank": {**R3["rerank"], "pool": 6}}
    _, res, log = run(tmp_path, [node("similar", "history", "sim", **p)])
    _, meta = written(log, "similar")
    assert ids_scores(meta) == REF_similar_pool("TW", QUERY, QSENSOR, top_k=2, pool=6)
    assert len(meta["results"]) == 2
    assert ids_scores(meta) != REF_similar("TW", QUERY, QSENSOR, top_k=2)


def test_the_fused_rank_is_the_place_before_the_re_rank(tmp_path):
    """ranks.fused keeps the retrieval order of the pool (1 … pool), so a hit the sensors brought in shows a fused rank above top_k."""
    p = {**R3, "top_k": 2, "rerank": {**R3["rerank"], "pool": 6}}
    _, _, log = run(tmp_path, [node("similar", "history", "sim", **p)])
    _, meta = written(log, "similar")
    full = REF_similar_pool("TW", QUERY, QSENSOR, top_k=6, pool=6)   # every candidate with its final score
    before = [d for d, _ in REF_similar_pool("TW", QUERY, QSENSOR, top_k=6, pool=6, w_rrf=1.0, w_sensor=0.0)]   # retrieval order alone
    assert {d for d, _ in full} == set(before)
    for r in meta["results"]:
        assert r["ranks"]["fused"] == before.index(r["id"].split("#")[0]) + 1
    assert max(r["ranks"]["fused"] for r in meta["results"]) > 2


def test_the_vector_method_fetches_the_pool(tmp_path):
    p = {"filter": {"symptom_group": "{state.q.group}"}, "method": "vector", "top_k": 2, "return_metadata": SENSORS,
         "rerank": {**R3["rerank"], "pool": 6}}
    _, _, log = run(tmp_path, [node("similar", "history", "sim", **p)])
    _, meta = written(log, "similar")
    assert ids_scores(meta) == REF_similar_pool("TW", QUERY, QSENSOR, top_k=2, pool=6, method="vector")


def test_a_pool_larger_than_the_filtered_set_keeps_what_there_is(tmp_path):
    p = {**R3, "top_k": 10, "rerank": {**R3["rerank"], "pool": 50}}
    _, _, log = run(tmp_path, [node("similar", "history", "sim", **p)])
    _, meta = written(log, "similar")
    assert ids_scores(meta) == REF_similar("TW", QUERY, QSENSOR)   # six TW records: pool 50 ⊇ all, the same as top_k 10 ⊇ all
    assert json.dumps(meta["results"])


def test_pool_loads_and_is_refused_below_top_k(tmp_path):
    ok = load_pack(load_make(tmp_path, lambda d: load_node(d, "find_similar")["params"]["rerank"].update(pool=30), "a"))
    assert ok.ok, messages(ok)
    same = load_pack(load_make(tmp_path, lambda d: load_node(d, "find_similar")["params"]["rerank"].update(pool=10), "b"))
    assert same.ok, messages(same)
    low = load_pack(load_make(tmp_path, lambda d: load_node(d, "find_similar")["params"]["rerank"].update(pool=5), "c"))
    assert not low.ok and "rerank.pool 5는 top_k 10보다 작을 수 없습니다" in messages(low)
    for bad, word in ((0, "minimum"), (2.5, "integer"), ("30", "integer")):
        r = load_pack(load_make(tmp_path, lambda d, v=bad: load_node(d, "find_similar")["params"]["rerank"].update(pool=v), f"x{word}{bad}"))
        assert not r.ok


def test_pool_is_a_wheel_knob_inside_rerank(tmp_path):
    folder = load_make(tmp_path)
    rr = load_pack(folder).doc["graphs"]["start"]["nodes"][1]["params"]["rerank"]
    res = apply_patch(folder, [{"op": "set", "graph": "start", "node": "find_similar", "param": "rerank", "value": {**rr, "pool": 30}}],
                      out_dir=tmp_path / "out")
    assert res.ok, res.errors
    assert res.revision != res.base_revision
    res = apply_patch(folder, [{"op": "set", "graph": "start", "node": "find_similar", "param": "rerank", "value": {**rr, "pool": 3}}],
                      out_dir=tmp_path / "out2")
    assert not res.ok   # the copy is loaded again: below top_k is refused
