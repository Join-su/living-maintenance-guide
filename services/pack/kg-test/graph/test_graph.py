"""D-534 그래프 DB 시험 — 사본(127.0.0.1:27687)에 시험용 꼬리표로 넣고 끝나면 지움 · 파일은 임시 폴더만. 실패 글은 칸 이름 · 개수만.
kg-test/.venv-kg/bin/python -m unittest discover -s kg-test/graph -p 'test_*.py'"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

import yaml

import conn
import graph_node as G
import load_graph as L

import build_layer as B   # noqa: E402
import gen                # noqa: E402
import kg_node as N       # noqa: E402
import methods as M       # noqa: E402

KG = conn.KG
K = M.Knowledge()
RULES = N.load_rules(KG / "exceptions.csv", K.causes)
OPT = yaml.safe_load((KG / "node.yaml").read_text(encoding="utf-8"))["options"]


def _alarm(r):
    return {x: r[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}


class Graph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drv = conn.driver()
        cls.tag = f"test-{uuid.uuid4().hex[:8]}"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.raw = Path(cls.tmp.name) / "raw"
        gen.write(gen.load_rules(), cls.raw)
        L.load(cls.raw, KG / "relation_defs.yaml", cls.tag, drv=cls.drv)
        cls.case_ids = [c["record_id"] for c in B._rows(cls.raw / "cases.jsonl")]
        cls.dev = [r for r in B._rows(cls.raw / "history.jsonl") if r["ts"] >= "2026-01-01"][::20]

    @classmethod
    def tearDownClass(cls):
        with cls.drv.session() as s:
            for t in (cls.tag, cls.tag + "-b", cls.tag + "-leak"):
                L.clear(s, t)
        cls.drv.close()
        cls.tmp.cleanup()

    def test_reload_same_dump(self):   # 다시 넣으면 같은 결과
        h1, n1 = L.dump(self.tag, self.drv)
        L.load(self.raw, KG / "relation_defs.yaml", self.tag + "-b", drv=self.drv)
        h2, n2 = L.dump(self.tag + "-b", self.drv)
        L.load(self.raw, KG / "relation_defs.yaml", self.tag, drv=self.drv)
        h3, _ = L.dump(self.tag, self.drv)
        self.assertEqual((h1, n1), (h3, n1))
        self.assertEqual(n1, n2)
        self.assertNotEqual(h1, h2)   # 꼬리표가 dump에 들어감 — 같은 꼬리표끼리만 같음

    def test_graph_edges_same_as_file_layer(self):   # 그래프 관계 = 파일 관계 층(같은 함수 · 같은 정의)
        with tempfile.TemporaryDirectory() as d:
            v, _ = B.build(self.raw, KG / "relation_defs.yaml", d)
            file_edges = sorted(json.dumps(e, sort_keys=True) for e in B._rows(v / "edges.jsonl"))
        with self.drv.session() as s:
            sl = s.run("MATCH (a:Event {layer: $v})-[r:SAME_LINE_BEFORE]->(b) RETURN a.id AS f, b.id AS t, r.age_min AS m, b.machine AS bm", v=self.tag).data()
            mw = s.run("MATCH (a:Event {layer: $v})-[r:PRIOR_WORK]->(b) RETURN a.id AS f, b.id AS t, r.kind AS k, r.age_days AS d", v=self.tag).data()
        got = sorted([json.dumps({"from": x["f"], "rel": "same_line", "to": x["t"], "machine": x["bm"], "age_min": x["m"]}, sort_keys=True) for x in sl]
                     + [json.dumps({"from": x["f"], "rel": "machine_work", "to": x["t"], "kind": x["k"], "age_days": x["d"]}, sort_keys=True) for x in mw])
        self.assertEqual(len(got), len(file_edges))
        self.assertEqual(got, file_edges)

    def test_rules_not_stored(self):   # 규칙 표는 그래프에 0
        with self.drv.session() as s:
            labels = {r["l"] for r in s.run("MATCH (n {layer: $v}) UNWIND labels(n) AS l RETURN DISTINCT l", v=self.tag).data()}
            text = json.dumps(s.run("MATCH (n {layer: $v}) RETURN properties(n) AS p", v=self.tag).data(), ensure_ascii=False)
        self.assertEqual(labels, {"Layer", "Line", "Machine", "Event", "Cause", "Step", "Grade"})
        for r in RULES:
            self.assertNotIn(r["basis"], text, r["id"])

    def test_same_answer_as_file_node_options(self):   # 실제 꼴: 같은 알람 · 같은 옵션 → 파일 노드와 같은 단계 · 같은 규칙 줄
        with tempfile.TemporaryDirectory() as d:
            v, _ = B.build(self.raw, KG / "relation_defs.yaml", d)
            layer = N.Layer(v, raw=self.raw, case_ids=self.case_ids)
            for opt in (OPT, {"scope": "line", "half_life_days": 60, "min_support": 5}, {"scope": "machine", "half_life_days": None, "min_support": 3}):
                f = N.Node(layer, RULES, opt, K)
                g = G.GraphNode(RULES, opt, K, layer=self.tag, drv=self.drv, case_ids=self.case_ids)
                for r in self.dev:
                    a, b = f.run(K, _alarm(r)), g.run(K, _alarm(r))
                    self.assertEqual((a["steps"], a["used"], a["support"]), (b["steps"], b["used"], b["support"]), f"{opt['scope']} {r['record_id']}")

    def test_bad_field_refused(self):   # 실패 쪽: 규칙 표 칸 이름이 질의 틀에 그대로 들어가지 않음
        g = G.GraphNode(RULES, OPT, K, layer=self.tag, drv=self.drv)
        for where in ([("x} DETACH DELETE (e) //", "=", "1")], [("unknown_field", ">=", "1")]):
            rule = dict(RULES[0], rel="self", where=where)
            g.rules = [rule]
            with self.assertRaises(N.RuleError):
                g.run(K, _alarm(self.dev[0]))

    def test_missing_layer_refused(self):
        with self.assertRaises(ValueError):
            G.GraphNode(RULES, OPT, K, layer="no-such-layer", drv=self.drv)

    def test_leak_refused(self):   # 실패 쪽: 시험 사례가 원인과 함께 그래프에 있으면 멈춤
        t = self.tag + "-leak"
        L.load(self.raw, KG / "relation_defs.yaml", t, drv=self.drv)
        with self.drv.session() as s:
            s.run("MATCH (e:Event {layer: $v, id: $id}), (c:Cause {layer: $v, code: 'C1'}) CREATE (e)-[:CONFIRMED_CAUSE]->(c)", v=t, id=self.case_ids[0]).consume()
        with self.assertRaises(M.Leak):
            G.GraphNode(RULES, OPT, K, layer=t, drv=self.drv, case_ids=self.case_ids)

    def test_options_three_only(self):
        with self.assertRaises(ValueError):
            G.GraphNode(RULES, {**OPT, "same_line_window": 30}, K, layer=self.tag, drv=self.drv)


class Sealed(unittest.TestCase):   # 다음 비교용 시험 사례 한 벌 — 값은 읽지 않고 바이트만 대조
    def test_sealed_bytes_and_regenerate(self):
        d = KG / "holdout-next"
        m = json.loads((d / "SEALED.json").read_text(encoding="utf-8"))
        for f, h in m["sha256"].items():
            self.assertEqual(hashlib.sha256((d / f).read_bytes()).hexdigest(), h, f)
        r = gen.load_rules()
        r["seed"] = m["seed"]
        with tempfile.TemporaryDirectory() as t:
            gen.write(r, Path(t))
            for f in m["sha256"]:
                self.assertEqual((Path(t) / f).read_bytes(), (d / f).read_bytes(), f)

    def test_not_same_as_seen_cases(self):
        a = (KG / "holdout-next/cases.jsonl").read_bytes()
        self.assertNotEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256((KG / "data/cases.jsonl").read_bytes()).hexdigest())


class Secret(unittest.TestCase):   # 비밀번호 값이 저장소 파일 · 그래프에 0(값은 메모리에서만 비교 · 출력 0)
    def test_password_not_in_repo_files(self):
        pw = conn._auth()[1]
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "kg-test"], cwd=KG.parent, capture_output=True, text=True).stdout.split()
        hits = sum(1 for f in files if (KG.parent / f).is_file() and pw.encode() in (KG.parent / f).read_bytes())
        self.assertEqual(hits, 0, "비밀번호 값이 든 파일 수")
        self.assertEqual(os.stat(conn.ENV_FILE).st_mode & 0o077, 0, "env 파일 모드")


if __name__ == "__main__":
    unittest.main()
