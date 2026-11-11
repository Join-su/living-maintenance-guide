"""D-533 관계 질의 노드 · 적재 두 층 시험 — 임시 폴더만 씀. 실패 글은 칸 이름 · 개수만(사례별 정답 0).
python -m unittest discover -s kg-test -p 'test_*.py'"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

import build_layer as B
import gen
import kg_node as N
import methods as M

RULES = gen.load_rules()
K = M.Knowledge()
HERE = gen.HERE
OPT = {"scope": "plant", "half_life_days": None, "min_support": 3}
FILES = ("nodes.jsonl", "edges.jsonl", "defs.yaml", "manifest.json")


def _alarm(r):
    return {x: r[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}


class Layers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.raw = cls.dir / "raw"
        gen.write(RULES, cls.raw)
        cls.case_ids = [c["record_id"] for c in B._rows(cls.raw / "cases.jsonl")]
        cls.raw_sha = {f: B._sha((cls.raw / f).read_bytes()) for f in B.RAW}
        cls.v1, _ = B.build(cls.raw, HERE / "relation_defs.yaml", cls.dir / "layer")
        cls.layer = N.Layer(cls.v1, raw=cls.raw, case_ids=cls.case_ids)
        cls.rules = N.load_rules(HERE / "exceptions.csv", K.causes)
        cls.hist = B._rows(cls.raw / "history.jsonl")
        cls.dev = [r for r in cls.hist if r["ts"] >= "2026-01-01"][::25]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def node(self, **opt):
        return N.Node(self.layer, self.rules, {**OPT, **opt}, K)

    # --- 적재 두 층 ---
    def test_rebuild_same_bytes(self):   # 관계 층은 원본에서 다시 만들어도 바이트 같음
        again, _ = B.build(self.raw, HERE / "relation_defs.yaml", self.dir / "again")
        for f in FILES:
            self.assertEqual((again / f).read_bytes(), (self.v1 / f).read_bytes(), f)

    def test_committed_layer_is_rebuild(self):   # 실제 꼴: 저장소의 layer/rel-v1 = 원본 층에서 다시 만든 것
        for f in FILES:
            self.assertEqual((HERE / "layer/rel-v1" / f).read_bytes(), (self.v1 / f).read_bytes(), f)

    def test_raw_untouched_by_build(self):   # 원본 층은 고치지 않음
        self.assertEqual({f: B._sha((self.raw / f).read_bytes()) for f in B.RAW}, self.raw_sha)
        self.assertEqual(json.loads((self.v1 / "manifest.json").read_text(encoding="utf-8"))["raw_sha256"], self.raw_sha)

    def test_rules_not_baked(self):   # 관계 층에는 규칙 id · 결론 · 규칙 숫자가 없음 — 관계 칸만
        keys = {frozenset(e) for es in self.layer.edges.values() for e in es}
        self.assertEqual(keys, {frozenset({"from", "rel", "to", "machine", "age_min"}), frozenset({"from", "rel", "to", "kind", "age_days"})})
        text = (self.v1 / "edges.jsonl").read_text(encoding="utf-8") + (self.v1 / "nodes.jsonl").read_text(encoding="utf-8")
        for r in self.rules:
            self.assertNotIn(f'"{r["id"]}"', text, r["id"])

    def test_new_alarm_edges_same_as_stored(self):   # 새 알람의 관계 = 관계 층을 만든 같은 함수 · 같은 정의
        for n in self.layer.nodes[::40]:
            self.assertEqual(B.edges_for(n, self.layer.idx, self.layer.defs), self.layer.edges.get(n["id"], []), n["id"])

    def test_edges_point_back_only(self):
        ts = {n["id"]: n["ts"] for n in self.layer.nodes}
        self.assertEqual(sum(1 for es in self.layer.edges.values() for e in es if ts[e["to"]] >= ts[e["from"]]), 0)

    def test_records_within_2h_invisible(self):
        es = [{"rel": "machine_work", "age_days": 1 / 24}, {"rel": "machine_work", "age_days": 3 / 24}, {"rel": "same_line", "age_min": 5}]
        self.assertEqual([e.get("age_days", "occ") for e in N.visible(es)], [3 / 24, "occ"])

    # --- 바뀐 것별 처리 ③: 관계 정의 바꿈 → 관계 층만 새 버전 · 원본 손 0 · 옛 버전 남김 ---
    def test_redefine_rebuilds_relation_layer_only(self):
        defs = yaml.safe_load((HERE / "relation_defs.yaml").read_text(encoding="utf-8"))
        defs.update(version="rel-v2", same_line={"window_min": 20})
        p = self.dir / "defs-v2.yaml"
        p.write_text(yaml.safe_dump(defs, allow_unicode=True, sort_keys=False), encoding="utf-8")
        v2, m2 = B.build(self.raw, p, self.dir / "layer")
        self.assertEqual({f: B._sha((self.raw / f).read_bytes()) for f in B.RAW}, self.raw_sha)   # 원본 손 0
        self.assertTrue((self.v1 / "edges.jsonl").is_file())                                         # 옛 버전 남음(되돌림용)
        self.assertEqual((v2 / "nodes.jsonl").read_bytes(), (self.v1 / "nodes.jsonl").read_bytes())  # 노드는 같음
        e1, e2 = B._rows(self.v1 / "edges.jsonl"), B._rows(v2 / "edges.jsonl")
        self.assertEqual([e for e in e2 if e["rel"] == "machine_work"], [e for e in e1 if e["rel"] == "machine_work"])
        sl1, sl2 = [e for e in e1 if e["rel"] == "same_line"], [e for e in e2 if e["rel"] == "same_line"]
        self.assertEqual(sl2, [e for e in sl1 if e["age_min"] <= 20])
        self.assertLess(len(sl2), len(sl1))
        # 좁히는 쪽은 규칙 표만 바꿔도 같은 답(적재 손 0) — 넓히는 쪽(예 40분)은 관계 층에 없어 다시 만들어야 함
        narrowed = [dict(r, where=r["where"] + [("age_min", "<=", "20")]) if r["rel"] == "same_line" else r for r in self.rules]
        a = N.Node(self.layer, narrowed, OPT, K)
        b = N.Node(N.Layer(v2, raw=self.raw, case_ids=self.case_ids), self.rules, OPT, K)
        for r in self.dev:
            self.assertEqual(a.run(K, _alarm(r))["steps"], b.run(K, _alarm(r))["steps"], r["record_id"])

    # --- 노드: 옵션 셋 · 규칙 표 ---
    def test_runs_on_dev_records(self):   # 손보기는 이력 안 사례(dev)로만 — 시험 100건 0
        node = N.Node.from_config(K, HERE / "node.yaml", raw=HERE / "data")
        for r in self.dev:
            out = node.run(K, _alarm(r))
            self.assertTrue(out["steps"])
            self.assertTrue(all(s["section"] in RULES["steps"] for s in out["steps"]))
            self.assertIn(out["used"], {x["id"] for x in self.rules} | {"manual"})

    def test_options_three_only(self):   # 실패 쪽: 예외별 숫자를 옵션으로 넣으면 거절
        with self.assertRaises(ValueError):
            N.Node(self.layer, self.rules, {**OPT, "line_window_min": 30}, K)
        with self.assertRaises(ValueError):
            N.Node(self.layer, self.rules, {**OPT, "scope": "region"}, K)

    def test_min_support_short_gives_manual_order(self):
        out = self.node(min_support=10 ** 6).run(K, _alarm(self.dev[0]))
        self.assertEqual((out["used"], out["steps"]), ("manual", M.manual_only(K, None, None)["steps"]))

    def test_scope_narrows_evidence(self):
        r = self.dev[-1]
        sup = {s: self.node(scope=s, min_support=1).run(K, _alarm(r))["support"] for s in N.SCOPES}
        self.assertTrue(sup["machine"] <= sup["line"] <= sup["plant"], sup)

    def test_new_exception_is_one_row(self):   # 새 예외 = 규칙 표 한 줄 — 노드 · 옵션 · 관계 층 그대로
        extra = Path(self.dir / "exceptions-plus.csv")
        extra.write_text((HERE / "exceptions.csv").read_text(encoding="utf-8") + 'R6,여름 필터,self,season=summer,1,C1,"시험용 한 줄"\n', encoding="utf-8")
        rules = N.load_rules(extra, K.causes)
        before = B._sha((self.v1 / "edges.jsonl").read_bytes())
        summer = next(r for r in self.hist if r["ts"][5:7] == "07")
        out = N.Node(self.layer, [rules[-1]] + rules[:-1], OPT, K).run(K, _alarm(summer))
        self.assertEqual(out["used"], "R6")
        self.assertEqual(B._sha((self.v1 / "edges.jsonl").read_bytes()), before)

    def test_bad_rule_refused(self):   # 실패 쪽: 모르는 관계 · 모르는 결론 · 연산 없는 조건
        for row in ('R9,x,other_rel,,1,X1,"-"', 'R9,x,self,,1,Z9,"-"', 'R9,x,self,season,1,X1,"-"'):
            p = self.dir / "bad.csv"
            p.write_text("id,name,rel,where,at_least,then,basis\n" + row + "\n", encoding="utf-8")
            with self.assertRaises(N.RuleError):
                N.load_rules(p, K.causes)

    def test_layer_from_other_raw_refused(self):   # 실패 쪽: 원본이 바뀌었는데 옛 관계 층을 읽으면 멈춤
        other = self.dir / "raw-other"
        shutil.copytree(self.raw, other, dirs_exist_ok=True)
        (other / "history.jsonl").write_text((other / "history.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            N.Layer(self.v1, raw=other)

    def test_leak_refused(self):   # 실패 쪽: 시험 사례가 관계 층에 원인과 함께 있으면 멈춤
        bad = self.dir / "bad-layer"
        shutil.copytree(self.v1, bad, dirs_exist_ok=True)
        rows = B._rows(bad / "nodes.jsonl")
        for n in rows:
            if n["id"] == self.case_ids[0]:
                n["cause"] = "C1"
        (bad / "nodes.jsonl").write_text("".join(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n" for n in rows), encoding="utf-8")
        with self.assertRaises(M.Leak):
            N.Layer(bad, case_ids=self.case_ids)


if __name__ == "__main__":
    unittest.main()
