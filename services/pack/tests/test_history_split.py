"""D-525 덧 10 v20 증상군 갈래: prepare_history_query → route_history(router@1 · by state.history_query.group) — 냉각(CL) → retrieve_history_cl ·
나머지 → retrieve_history → 둘 다 retrieve_manual. 시작 값은 v19와 같음(출력 같음은 T1 지문 · PS-02가 봄 — 자동 개선이 냉각 node 값을 바꾸면 그 node만 바뀜).
실패 쪽: 갈래 값이 없으면 default(공용 node) · 요청에 group이 없으면 Run이 이력 node 전에 멈춤. 실패 글은 case 이름 · node 이름 · 개수만."""
import json
import unittest

import yaml

from test_pack import PACK, _NeedsVectorDB, _Sink

HIST = ("retrieve_history", "retrieve_history_cl")


def _nodes():
    return {n["id"]: n for n in yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))["graphs"]["run"]["nodes"]}


def _start_requests():
    out = []
    for suite in ("c1a15-start", "c1a15-order"):
        for line in (PACK / "evals" / suite / "dataset.jsonl").read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            if d["input"]["kind"] == "start":
                out.append((f"{suite}/{d['case_id']}", d["input"]))
    return out


def _ran(rt, req):
    sink = _Sink()
    out = rt.run(req, sink=sink)
    return out, [e["node_id"] for e in sink.events if e.get("type") == "node_ended" and e.get("node_id") in (*HIST, "route_history")]


class Shape(unittest.TestCase):
    def test_router_and_edges(self):
        nodes = _nodes()
        r = nodes["route_history"]
        self.assertEqual((r["kind"], r["params"]), ("router@1", {"by": "state.history_query.group", "cases": {"CL": "retrieve_history_cl"}, "default": "retrieve_history"}))
        self.assertEqual(r["reads"], ["history_query"])
        edges = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))["graphs"]["run"]["edges"]
        for e in (["prepare_history_query", "route_history"], ["retrieve_history", "retrieve_manual"], ["retrieve_history_cl", "retrieve_manual"]):
            self.assertIn(e, edges)
        self.assertNotIn(["prepare_history_query", "retrieve_history"], edges)

    def test_cl_node_same_kind_reads_writes(self):   # 갈래 뒤 이어지는 node가 같은 칸을 읽음(값은 자동 개선이 바꿀 수 있어 여기서 고정하지 않음 · v20 시작 값 같음은 T1 지문)
        nodes = _nodes()
        a, b = nodes["retrieve_history"], nodes["retrieve_history_cl"]
        self.assertEqual((b["kind"], b["reads"], b["writes"], b["params"]["store"]), (a["kind"], a["reads"], a["writes"], a["params"]["store"]))


class Route(_NeedsVectorDB):
    def test_cl_goes_cl_node_rest_shared(self):
        seen, bad = {"CL": 0, "rest": 0}, []
        for name, req in _start_requests():
            out, ran = _ran(self.rt, req)
            want = ["route_history", "retrieve_history_cl" if req["group"] == "CL" else "retrieve_history"]
            if out["status"] != "succeeded" or ran != want:
                bad.append(name)
            seen["CL" if req["group"] == "CL" else "rest"] += 1
        self.assertEqual(bad, [], f"갈래가 다른 case {len(bad)}개")
        self.assertGreater(seen["CL"], 0)
        self.assertGreater(seen["rest"], 0)

    def test_missing_group_value_takes_default(self):   # 실패 쪽 ①: 갈래 값이 없으면(None) 공용 node — 냉각 node로 새지 않음
        r = _nodes()["route_history"]
        _, target = self.rt._n_router(r, {"history_query": {"text": "x", "sensors": {}}}, ["history_query"], {})
        self.assertEqual(target, "retrieve_history")

    def test_request_without_group_stops_before_history(self):   # 실패 쪽 ②: 요청에 group이 없으면 Run 실패 · 이력 node 0
        req = next(r for _, r in _start_requests() if r["group"] == "CL")
        req = {k: v for k, v in req.items() if k != "group"}
        out, ran = _ran(self.rt, req)
        self.assertEqual(out["status"], "failed")
        self.assertEqual([n for n in ran if n in HIST], [])
