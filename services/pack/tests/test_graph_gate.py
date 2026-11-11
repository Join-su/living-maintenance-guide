"""D-554 ① 관문이 부르는 Pack 길(tools/graph_gate.py) 시험 — 후보 그래프 = 지금 그래프 + 시험 예외 관계 하나.
실제 Pack start(두 그래프 사본) · 호출 0 · 정답 파일 읽기 0(suite 입력 dataset만)."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import graph_gate as GG  # noqa: E402
from packrt.ir import revision  # noqa: E402
from test_pack import need_vector_db  # noqa: E402

CURRENT = ROOT / "pack/templates/graph/current.json"
SUITE = ROOT / "pack/evals/c1a15-start-v8b/dataset.jsonl"
RID = "EX-R-9001"


def _rows():
    return [json.loads(x) for x in SUITE.read_text(encoding="utf-8").splitlines() if x.strip()]


def _sensors(row):
    s = row["input"]["query"]["sensor_snapshot"]
    return {**s, **GG.alarm.derived(s)}


class Gate(unittest.TestCase):
    def setUp(self):
        need_vector_db(self)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)

    def _cand(self, sensors, name="cand.json"):
        g = json.loads(CURRENT.read_text(encoding="utf-8"))
        g["sections"].append({"no": RID, "app_no": RID, "kind": "예외", "grade": "C", "name": "필터 덮개 고정 나사 확인", "guide": "덮개 나사를 확인한다",
                              "steps": ["덮개 나사 확인"], "criteria": "나사 네 개 체결 = 정상", "group": "온도 차 저하", "minutes": 5, "checklist": [], "records": ["R-9001"]})
        g["exception_relations"] = [{"id": RID, "step": RID, "mode": "안내", "grade": "C", "cause": "CL-1", "cause_name": "냉각 필터 막힘", "records": ["R-9001"], "n": 1,
                                     "when": {"group": "온도 차 저하", "sensors": sensors}}]
        g["layer"] += "-gate"
        p = self.dir / name
        p.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        return p

    def test_new_relation_impact_and_outputs(self):   # 사람 손 길: 후보 = 관계 하나 더함 → 바뀐 관계 · 영향 범위(직접 센 수와 같음) · 끼어든 case만 출력 다름
        cl = next(r for r in _rows() if r["input"]["group"] == "CL")
        td = _sensors(cl)["temp_diff_k"]
        res = GG.gate(CURRENT, self._cand([["temp_diff_k", "<=", td]]), [SUITE])
        self.assertEqual(res["relations"], {"added": [RID], "changed": [], "removed": []})
        recs = json.loads(CURRENT.read_text(encoding="utf-8"))["records"]
        want = sum(1 for r in recs if r["group"] == "CL" and r["sensors"]["temp_diff_k"] <= td)   # 따로 센 과거 알람 수
        self.assertEqual(res["impact"]["alarms"], {"n": want, "total": len(recs), "by_relation": {RID: want} if want else {}})
        ids = res["impact"]["cases"]["ids"]
        self.assertIn(f"c1a15-start-v8b/{cl['case_id']}", ids)
        self.assertEqual(res["impact"]["cases"]["total"], len(_rows()))
        for c in res["cases"]:
            self.assertEqual(c["same"], f"{c['suite']}/{c['case_id']}" not in ids, c["case_id"])   # 끼어들지 않은 case는 출력 바이트 같음
        hit = next(c for c in res["cases"] if c["case_id"] == cl["case_id"])
        self.assertEqual((hit["cand"]["exception_guide"]["relation_id"], "exception_guide" in hit["base"]), (RID, False))
        self.assertEqual(res["base"]["revision"], revision.compute(ROOT / "pack")[0])   # 지금 그래프 = 지금 Pack revision
        self.assertNotEqual(res["cand"]["revision"], res["base"]["revision"])   # 그래프 파일은 revision 안(배포가 같은 값인지 봄)

    def test_same_graph_and_bad_candidate(self):   # 실패 쪽: 같은 그래프면 영향 0 · 출력 모두 같음 / 꼴 틀린 관계 → 거절(사본 Run 전) · 한 줄 · 종료 2
        rows = _rows()[:3]
        small = self.dir / "c1a15-start-v8b" / "dataset.jsonl"
        small.parent.mkdir()
        small.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        res = GG.gate(CURRENT, CURRENT, [small])
        self.assertEqual((res["relations"], res["impact"]["alarms"]["n"], res["impact"]["cases"]["n"]), ({"added": [], "changed": [], "removed": []}, 0, 0))
        self.assertTrue(all(c["same"] for c in res["cases"]) and len(res["cases"]) == 3)
        bad = self._cand([["vibration", "<=", 1]], "bad.json")
        with self.assertRaises(GG.exception.RuleError):
            GG.gate(CURRENT, bad, [small])
        p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/graph_gate.py"), "--base", str(CURRENT), "--cand", str(bad), "--cases", str(small), "--out", str(self.dir / "o.json")],
                           capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stderr.count("\n"), "Traceback" in p.stderr, (self.dir / "o.json").exists()), (2, 1, False, False))
        moved = copy.deepcopy(json.loads(self._cand([["temp_diff_k", "<=", 99]], "w.json").read_text(encoding="utf-8")))   # 같은 id · 조건만 바뀜 → changed
        moved["exception_relations"][0]["when"]["sensors"] = [["temp_diff_k", "<=", 1]]
        a, b = json.loads((self.dir / "w.json").read_text(encoding="utf-8")), moved
        self.assertEqual(GG.relations(a, b), {"added": [], "changed": [RID], "removed": []})


    def _order_cl(self, n=1):   # 두 걸음 suite(c1a15-order) 입력 중 냉각(CL) case — 시험 자리에 따로 씀(그 suite 폴더 이름 그대로)
        rows = [json.loads(x) for x in (ROOT / "pack/evals/c1a15-order/dataset.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        cl = [r for r in rows if r["input"]["group"] == "CL" and r.get("then")][:n]
        f = self.dir / "c1a15-order" / "dataset.jsonl"
        f.parent.mkdir(exist_ok=True)
        return cl, f

    def test_two_step_then(self):   # eval 141 ①: 두 걸음(then) — 둘째 입력 = LLMOps 규칙(drop → set → take) · 판단 Run 출력이 final · 예외 관계를 이어 받으면 판단 망가짐도 보임
        (row,), f = self._order_cl()
        first = {"similar": [{"record_id": "H-1"}], "exception_guide": {"relation_id": RID}}
        then = {**row["then"], "take": {**row["then"].get("take", {}), "exception_relation": "exception_guide.relation_id"}}
        nxt = GG.next_input(row["input"], then, first)
        self.assertEqual((nxt["kind"], nxt["similar"], nxt["exception_relation"]), ("judge", first["similar"], RID))
        self.assertFalse(set(row["then"].get("drop") or []) & set(nxt))   # drop 길 빠짐
        self.assertEqual(GG.next_input(row["input"], {"take": {"x.y": "없는.길"}}, first)["x"], {"y": None})   # 첫 출력에 없는 길 = null(LLMOps와 같음)
        f.write_text(json.dumps({**row, "then": then}, ensure_ascii=False) + "\n", encoding="utf-8")
        td = _sensors(row)["temp_diff_k"]
        res = GG.gate(CURRENT, self._cand([["temp_diff_k", "<=", td]]), [f])
        (c,) = res["cases"]
        self.assertEqual(c["first"]["cand"]["exception_guide"]["relation_id"], RID)   # 첫 Run(start)에 예외 안내
        self.assertNotIn("exception_guide", c["first"]["base"])
        self.assertIn(RID, [x.get("relation") for x in c["cand"]["decision"]["reason_facts"]["alternatives"]])   # 판단 Run(final)에 예외가 후보로 뜸(D-580 · C는 첫 추천 아님)
        self.assertNotEqual((c["base"].get("decision") or {}).get("reason"), "exception_guide")   # base는 첫 출력에 안내가 없어 take 값 null → 둘째 요청 거절(LLMOps도 같은 입력)
        self.assertFalse(c["same"])
        one = {k: v for k, v in row.items() if k != "then"}   # then 없으면 한 걸음 · first 칸 0
        f.write_text(json.dumps(one, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertNotIn("first", GG.gate(CURRENT, CURRENT, [f])["cases"][0])

    def test_base_all_before_cand(self):   # eval 141 ③: 차례 — base Runtime으로 모두 돈 뒤 cand Runtime을 만듦(Runtime 둘을 한꺼번에 두면 섞였던 일 · eval 135)
        rows = _rows()[:2]
        small = self.dir / "c1a15-start-v8b" / "dataset.jsonl"
        small.parent.mkdir()
        small.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        log, real = [], GG.Runtime

        class Spy(real):
            def __init__(self, pack, **kw):
                log.append(("new", json.loads((Path(pack) / "templates/graph/current.json").read_text(encoding="utf-8"))["layer"]))
                super().__init__(pack, **kw)

            def run(self, req):
                log.append(("run",))
                return super().run(req)

        from unittest import mock
        with mock.patch.object(GG, "Runtime", Spy):
            GG.gate(CURRENT, self._cand([["temp_diff_k", "<=", 99]]), [small])
        base = json.loads(CURRENT.read_text(encoding="utf-8"))["layer"]
        self.assertEqual(log, [("new", base), ("run",), ("run",), ("new", base + "-gate"), ("run",), ("run",)])

if __name__ == "__main__":
    unittest.main()
