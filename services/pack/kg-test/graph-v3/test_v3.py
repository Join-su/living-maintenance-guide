"""D-536 ② 시험 — 그래프 사본(127.0.0.1:27687)에 시험 꼬리표로 넣고 끝나면 지움 · 파일은 임시 폴더만. 실패 글은 칸 이름 · 개수만.
KG3_HISTORY=<앱 저장소>/data/history/maintenance_history.jsonl kg-test/.venv-kg/bin/python -B -m unittest discover -s kg-test/graph-v3 -p 'test_*.py'"""
import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

import build_v3 as B
from conn import driver

HIST = Path(os.environ["KG3_HISTORY"])


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drv = driver()
        cls.tag = f"test3-{uuid.uuid4().hex[:8]}"

    @classmethod
    def tearDownClass(cls):
        with cls.drv.session() as s:
            B.clear(s, cls.tag)
        cls.drv.close()

    def test_rebuild_same_dump(self):   # 다시 만들면 같은 결과
        B.load(HIST, self.tag, drv=self.drv)
        h1 = B.dump(self.tag, self.drv)
        B.load(HIST, self.tag, drv=self.drv)
        self.assertEqual(h1, B.dump(self.tag, self.drv))

    def test_mapping_not_in_graph(self):   # 대응 표는 그래프 입력 0 — 절끼리 잇는 관계 0 · 꼬리표 다섯뿐
        B.load(HIST, self.tag, drv=self.drv)
        with self.drv.session() as s:
            labels = {r["l"] for r in s.run("MATCH (n {layer: $v}) UNWIND labels(n) AS l RETURN DISTINCT l", v=self.tag).data()}
            ss = s.run("MATCH (:Section {layer: $v})-[r]-(:Section {layer: $v}) RETURN count(r) AS n", v=self.tag).single()["n"]
            old = s.run("MATCH (n:Section {layer: $v}) WHERE n.no IN ['4.3', '4.3.2', '5.6', '7.1'] RETURN count(n) AS n", v=self.tag).single()["n"]
        self.assertEqual(labels, set(B.LABELS))
        self.assertEqual(ss, 0, "절–절 관계 수")
        self.assertEqual(old, 0, "옛 번호 절 수")

    def test_equipment_time_line(self):   # D-570 사용자: '같은 증상에서의 점검한 시간들이 관계로 연결' — 설비 노드 → 이력(HAD) · 같은 설비 이력끼리 시각순 NEXT · 그때 한 일(DID · CHECKED) · 실패 쪽: 다른 설비끼리 NEXT 0 · 거꾸로 NEXT 0
        B.load(HIST, self.tag, drv=self.drv)
        with self.drv.session() as s:
            n_eq = s.run("MATCH (e:Equipment {layer: $v}) RETURN count(e) AS n", v=self.tag).single()["n"]
            n_rec = s.run("MATCH (r:Record {layer: $v}) RETURN count(r) AS n", v=self.tag).single()["n"]
            had = s.run("MATCH (r:Record {layer: $v}) OPTIONAL MATCH (e:Equipment)-[:HAD]->(r) RETURN r.id AS id, count(e) AS n, collect(e.id)[0] AS eq, r.equipment AS want", v=self.tag).data()
            nxt = s.run("MATCH (e1:Equipment)-[:HAD]->(a:Record {layer: $v})-[x:NEXT]->(b:Record)<-[:HAD]-(e2:Equipment) RETURN e1.id = e2.id AS same, a.ts AS a, b.ts AS b, x.days AS d", v=self.tag).data()
            walk = s.run("MATCH (e:Equipment {layer: $v})-[:HAD]->(r:Record)-[:DID]->(x:Section) RETURN count(DISTINCT e) AS n", v=self.tag).single()["n"]
        self.assertTrue(n_eq > 1 and all(h["n"] == 1 and h["eq"] == h["want"] for h in had))
        self.assertEqual(len(nxt), n_rec - n_eq)   # 설비마다 이력 수 − 1
        self.assertTrue(all(x["same"] for x in nxt))
        self.assertTrue(all(datetime.fromisoformat(x["a"]) <= datetime.fromisoformat(x["b"]) and x["d"] >= 0 for x in nxt))
        self.assertEqual(walk, n_eq)   # 설비 → 이력 → 그때 한 조치 길이 설비마다 있음


class Offline(unittest.TestCase):
    def test_build_path_does_not_read_gold(self):   # 그래프 만드는 길은 2.0 매뉴얼(부록 A 정답)을 열지 않음
        opened = []
        hook = lambda ev, args: opened.append(str(args[0])) if ev == "open" and isinstance(args[0], (str, Path)) else None
        sys.addaudithook(hook)
        start = len(opened)
        B.graph_rows(HIST)
        files = {Path(p).resolve() for p in opened[start:] if Path(p).suffix in (".md", ".csv", ".jsonl", ".json", ".pdf")}
        self.assertEqual(files, {B.MANUAL.resolve(), B.MAP.resolve(), HIST.resolve()})
        self.assertFalse(any(p.name == "MG-MM.md" for p in files))

    def test_checklist_on_section_nodes(self):   # v2(D-543): B등급 절의 시작 전 체크리스트가 절 노드 칸으로 — 실패 쪽: 체크리스트 없는 절은 빈 목록
        secs = {s["no"]: s for s in B.manual_sections()}
        with_list = {no for no, s in secs.items() if s["checklist"]}
        self.assertEqual(len(with_list), 5)
        self.assertTrue(all(len(secs[no]["checklist"]) == 3 for no in with_list))
        self.assertEqual(secs["1.2"]["checklist"], [])
        self.assertEqual(B.checklist("**시작 전 체크리스트**\n\n글만 있음"), [])

    def test_desc_on_check_nodes(self):   # v3(D-560): 점검 절 노드 설명 칸 = 판정 기준 첫 문장(LLM 0) · 16/16 · 점검 아닌 절은 칸 0 · 그래프 파일까지 · 실패 쪽: 판정 기준 없음 = None
        secs = B.manual_sections()
        chk = [s for s in secs if s["kind"] == "점검"]
        self.assertEqual((len(chk), sum(1 for s in chk if s.get("desc"))), (16, 16))
        self.assertFalse([s["no"] for s in secs if s["kind"] != "점검" and "desc" in s])
        self.assertTrue(all(s["desc"].endswith("다.") and "**" not in s["desc"] for s in chk))
        self.assertEqual(next(s for s in chk if s["no"] == "2.3.6")["desc"], '어느 하나라도 표준값보다 낮게 설정되어 있으면 "이상"이다.')   # '보다'의 '다'에서 끊지 않음
        self.assertIsNone(B.describe("**점검 방법**\n\n1. 연다."))
        rel = json.loads((B.KG.parent / "pack/templates/graph/current.json").read_text(encoding="utf-8"))
        self.assertEqual({s["app_no"]: s["desc"] for s in rel["sections"] if "desc" in s}, {s["app_no"]: s["desc"] for s in rel["sections"] if s["kind"] == "점검"})
        self.assertEqual(len([s for s in rel["sections"] if "desc" in s]), 16)

    def test_counts_match_raw(self):   # 실제 꼴: 관계 근거 건수 합 = 원본 이력에서 센 수
        g = B.graph_rows(HIST)
        recs = [json.loads(l) for l in HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
        conf = [r for r in recs if r["outcome"].get("cause_code")]
        self.assertEqual(sum(x["n"] for x in g["indicates"]), sum(1 for r in conf for s in r["steps"] if s["kind"] == "check" and s["result"] == "이상"))
        self.assertEqual(sum(x["n"] for x in g["candidate"]), len(conf))
        self.assertEqual(sum(x["n"] for x in g["remedied_by"]), sum(1 for r in conf for s in r["steps"] if s["kind"] == "action"))

    def _bad(self, edit):
        recs = [json.loads(l) for l in HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
        edit(recs[0])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.jsonl"
            p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            with self.assertRaises(ValueError):
                B.graph_rows(p)

    def test_unknown_section_refused(self):   # 실패 쪽: 대응 표에 없는 절 번호
        self._bad(lambda r: next(s for s in r["steps"] if s["kind"] == "check").update(section="9.9"))

    def test_unknown_group_refused(self):   # 실패 쪽: 매뉴얼에 없는 증상군
        self._bad(lambda r: r["symptom_group"].update(name="없는 증상군"))


class Compare(unittest.TestCase):   # 대조 셈 — 정답 파일 대신 그래프 값으로 만든 가짜 정답(정답 파일 안 읽음)
    def setUp(self):
        import check_v3 as C
        self.C = C
        g = B.graph_rows(HIST)
        self.cand = [{"g": x["group"], "c": x["code"], "n": x["n"]} for x in g["candidate"]]
        self.ind = [{"no": x["no"], "c": x["code"], "n": x["n"]} for x in g["indicates"]]
        self.rem = [{"c": x["code"], "no": x["no"], "n": x["n"]} for x in g["remedied_by"]]
        chk = {}
        for x in self.ind:
            chk.setdefault(x["c"], []).append(x["no"])
        self.gold = [{"code": x["c"], "name": "", "group": x["g"], "screen": (sorted(chk[x["c"]]) + [None])[1] if len(chk[x["c"]]) > 1 else None,
                      "confirm": sorted(chk[x["c"]])[0], "action": next(r["no"] for r in self.rem if r["c"] == x["c"])} for x in self.cand]

    def test_same_graph_all_right(self):
        _, links, _ = self.C.compare(self.gold, self.cand, self.ind, self.rem)
        self.assertEqual((links["빠짐"], links["틀림"]), (0, 0))

    def test_missing_and_wrong_counted(self):   # 실패 쪽: 연결 하나 빼면 빠짐 1 · 엉뚱한 연결 하나 더하면 틀림 1
        _, links, _ = self.C.compare(self.gold, self.cand, self.ind[1:], self.rem)
        self.assertEqual((links["빠짐"], links["틀림"]), (1, 0))
        wrong = dict(self.ind[0], c=next(x["c"] for x in self.ind if x["c"] != self.ind[0]["c"]))
        rows, links, _ = self.C.compare(self.gold, self.cand, self.ind + [wrong], self.rem)
        self.assertEqual(links["틀림"], 1)
        self.assertEqual(sum(1 for r in rows if r["res"]["원인"] == "틀림"), 1)


if __name__ == "__main__":
    unittest.main()
