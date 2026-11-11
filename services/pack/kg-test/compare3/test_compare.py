"""세 줄 비교 시험 — 그래프 사본(127.0.0.1:27687)에는 시험 꼬리표로 넣고 끝나면 지움 · 파일은 임시 폴더만. 실패 글은 칸 이름 · 개수만.
KG_HIDDEN=<kg-hidden 폴더> KG_MANUAL20=<앱 저장소>/data/manuals/MG-MM.md kg-test/.venv-kg/bin/python -B -m unittest discover -s kg-test/compare3 -p 'test_*.py'"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import compare as C
from conn import driver

HIDDEN = Path(os.environ["KG_HIDDEN"])
HIST = HIDDEN / "history.jsonl"
MANUAL20 = Path(os.environ["KG_MANUAL20"])


class Inputs(unittest.TestCase):
    def test_rag_and_graph_open_only_history_and_manual3(self):   # ② · ③ 길은 관계표 · rules.yaml · 2.0 매뉴얼 · 정답을 열지 않음
        opened = []
        sys.addaudithook(lambda ev, args: opened.append(str(args[0])) if ev == "open" and isinstance(args[0], (str, Path)) else None)
        start = len(opened)
        C.history_rows(HIST)
        C.graph_rows(HIST)
        files = {Path(p).resolve() for p in opened[start:] if Path(p).suffix in (".md", ".csv", ".jsonl", ".json", ".yaml")}
        self.assertEqual(files, {C.B.MANUAL.resolve(), HIST.resolve()})

    def test_relation_table_refused(self):   # 실패 쪽: 사람이 쓴 관계표 · 2.0 매뉴얼 · 정답을 ② · ③에 넣으면 거절
        for bad in (C.KG / "relations/check_cause.csv", MANUAL20, HIDDEN / "answers.jsonl", HIDDEN / "case-categories.csv"):
            with self.assertRaises(C.Leak, msg=bad.name):
                C.graph_rows(bad)
            with self.assertRaises(C.Leak, msg=bad.name):
                C.history_rows(HIST, bad)

    def test_alarm_with_answer_field_refused(self):   # 실패 쪽: 알람에 화면 밖 칸(정답 등)이 있으면 멈춤
        a = json.loads(next(l for l in (HIDDEN / "alarms.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()))
        C.check_alarms([a])
        with self.assertRaises(C.Leak):
            C.check_alarms([{**a, "truth": {}}])

    def test_counts_match_raw(self):   # 실제 꼴: 관계 근거 건수 합 = 이력에서 센 수
        g = C.graph_rows(HIST)
        recs = [json.loads(l) for l in HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
        conf = [r for r in recs if r["outcome"] == "원인 확정"]
        self.assertEqual(len(g["records"]), len(recs))
        self.assertEqual(sum(x["n"] for x in g["candidate"]), len(conf))
        self.assertEqual(sum(x["n"] for x in g["indicates"]), sum(1 for r in conf for c in r["checks"] if c["result"] == "이상"))
        self.assertEqual(sum(x["n"] for x in g["remedied_by"]), sum(1 for r in conf if r.get("action")))


class Plans(unittest.TestCase):   # 조치는 이력에서만 — 번호 구조(점검 2.k.n ↔ 조치 3.k.m)로 짐작 0
    def test_rag_no_action_without_record(self):
        sim = [{"id": "x1", "abnormal": ["2.3.3"], "action": None}, {"id": "x2", "abnormal": ["2.3.4"], "action": "3.3.9"}]
        p = C.plan_rag({}, sim)
        self.assertEqual(p["order"], ["2.3.3", "2.3.4"])
        self.assertNotIn("2.3.3", p["action"])
        self.assertEqual(p["action"]["2.3.4"], "3.3.9")

    def test_graph_no_action_without_edge(self):
        kg = {"cand": [{"g": "온도 차 저하", "c": "a", "n": 2}], "ind": [{"no": "2.3.5", "c": "a", "n": 2}], "rem": []}
        p = C.plan_graph({"group_name": "온도 차 저하"}, [], kg)
        self.assertEqual((p["order"], p["action"]), (["2.3.5"], {}))

    def test_score_falls_back_to_manual_order(self):   # 목록에 없으면 매뉴얼 차례로 이어 함 · 헛걸음 = 앞서 한 점검
        s = C.score({"order": ["2.3.4"], "action": {}}, {"confirm_check": "2.3.5", "action": "3.3.1"}, ["2.3.3", "2.3.4", "2.3.5", "2.3.6"], {"2.3.3": 3, "2.3.4": 10})
        self.assertEqual((s["first"], s["waste_n"], s["waste_min"], s["loaded"], s["action"]), (False, 2, 13, False, False))


class Run(unittest.TestCase):   # 사람 손 길: 실제 kg-hidden 전체 · 그래프 사본 시험 꼬리표
    @classmethod
    def setUpClass(cls):
        cls.drv = driver()
        cls.tag = f"test-c3-{uuid.uuid4().hex[:8]}"

    @classmethod
    def tearDownClass(cls):
        with cls.drv.session() as s:
            C.B.clear(s, cls.tag)
        cls.drv.close()

    def test_graph_read_back_equals_rows_and_tally_shape(self):
        opened = []
        sys.addaudithook(lambda ev, args: opened.append(Path(str(args[0])).name) if ev == "open" and isinstance(args[0], (str, Path)) else None)
        start = len(opened)
        res, overlap = C.run(HIDDEN, MANUAL20, self.drv, layer=self.tag)
        seen = [n for n in opened[start:] if n in ("history.jsonl", "alarms.jsonl", "answers.jsonl", "case-categories.csv", "groups.csv")]
        self.assertEqual(seen[-2:], ["answers.jsonl", "case-categories.csv"])   # 정답 · 갈래는 맨 끝(채점에만) · 한 번씩
        self.assertEqual((seen.count("answers.jsonl"), seen.count("case-categories.csv"), seen.count("groups.csv")), (1, 1, 0))
        kg, g = C.read_graph(self.drv, self.tag), C.graph_rows(HIST)
        self.assertEqual(sum(x["n"] for x in kg["ind"]), sum(x["n"] for x in g["indicates"]))
        self.assertEqual(sorted(r["id"] for r in kg["records"]), sorted(r["id"] for r in g["records"]))
        alarms = (HIDDEN / "alarms.jsonl").read_text(encoding="utf-8").count("\n")
        allrow = [r for r in res["rows"] if r["row"] == "모두"]
        cats = [r for r in res["rows"] if r["row"].startswith("갈래 ") and r["line"] == C.LINES[0]]
        self.assertEqual(sum(r["n"] for r in cats), allrow[0]["n"])   # 원인 확정 사례는 갈래 하나씩
        self.assertEqual(len(allrow), len(C.LINES) + len(C.REFS))
        self.assertEqual(allrow[0]["n"] + sum(res["not_confirmed"].values()), alarms)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        md = C.write(res, overlap, Path(d))
        self.assertTrue((Path(d) / "세줄.json").is_file() and "KT-" not in md)


if __name__ == "__main__":
    unittest.main()
