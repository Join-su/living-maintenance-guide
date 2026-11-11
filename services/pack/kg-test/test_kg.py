"""D-531 kg-test 시험 — 임시 폴더만 씀. 실패 글은 칸 이름 · 개수만(사례별 정답 0).
python -m unittest discover -s kg-test -p 'test_*.py'"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import gen
import methods as M
from packrt.run.backends import FlatLocal

RULES = gen.load_rules()
K = M.Knowledge()


def _rows(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


class Data(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        gen.write(RULES, cls.dir / "a")
        cls.events = gen.generate(RULES)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_seed_same_bytes(self):
        gen.write(RULES, self.dir / "b")
        for f in ("history.jsonl", "occurrences.jsonl", "cases.jsonl"):
            self.assertEqual((self.dir / "a" / f).read_bytes(), (self.dir / "b" / f).read_bytes(), f)

    def test_size(self):   # 이력 약 1,000 · 1년
        h = _rows(self.dir / "a/history.jsonl")
        self.assertTrue(950 <= len(h) <= 1100, len(h))
        ts = sorted(r["ts"] for r in h)
        self.assertTrue(ts[0] >= RULES["plant"]["period"]["start"] and ts[-1] <= RULES["plant"]["period"]["end"] + "T23:59")

    def test_every_event_obeys_rules(self):
        ev = self.events
        bad = {"alarm": 0, "E2": 0, "E3": 0, "E4": 0, "E5": 0}
        for e in ev:
            if not e["sensors"]["temp_diff_k"] < RULES["sensors"]["alarm"]["below"]:
                bad["alarm"] += 1
            same_m = [o for o in ev if o["machine"] == e["machine"] and o["ts"] < e["ts"]]
            if e["origin"] == "E3" and not any(o.get("late_filter") and timedelta(days=14) <= e["ts"] - o["ts"] <= timedelta(days=21) for o in same_m):
                bad["E3"] += 1
            if e["origin"] == "E4" and not any(o.get("replaced_part") and e["ts"] - o["ts"] <= timedelta(days=3) for o in same_m):
                bad["E4"] += 1
            if e["origin"] == "E5" and not (e["cause"] == "X3" and e["sensors"]["rotational_speed_rpm"] >= 1460):
                bad["E5"] += 1
        for b in {e["burst"] for e in ev if e["origin"] == "E2"}:
            m = [e for e in ev if e.get("burst") == b]
            if not (len(m) >= 3 and len({e["line"] for e in m}) == 1 and max(e["ts"] for e in m) - min(e["ts"] for e in m) <= timedelta(minutes=30)):
                bad["E2"] += 1
        self.assertEqual(bad, {k: 0 for k in bad})

    def test_holdout(self):
        cases = _rows(self.dir / "a/cases.jsonl")
        hist_ids = {r["record_id"] for r in _rows(self.dir / "a/history.jsonl")}
        got = {}
        for c in cases:
            got[c["category"]] = got.get(c["category"], 0) + 1
        self.assertEqual(got, RULES["holdout"]["counts"])
        self.assertEqual(len({c["record_id"] for c in cases} & hist_ids), 0)
        self.assertTrue(all(c["ts"] >= RULES["holdout"]["from"] for c in cases))
        parents = {e["record_id"] for e in self.events if e.get("is_parent")}
        self.assertEqual(len({c["record_id"] for c in cases} & parents), 0)

    def test_memo_has_no_query_words(self):   # 메모 낱말이 질의 낱말과 우연히 겹쳐 유사도 순위를 정하지 않게(dev에서 찾은 것)
        from packrt.run.backends import tokenize_ko_bigram_1 as tok
        q = set(tok(M.QUERY_TEXT))
        self.assertEqual(sum(1 for ms in gen.MEMO.values() for m in ms if set(tok(m)) & q), 0)

    def test_manual_has_no_cause_table(self):   # 사용자 결정: 원인 표 · 부록 A 없음
        text = (gen.HERE / "manual/cooling.md").read_text(encoding="utf-8")
        for word in ("원인 표", "부록", "원인 후보"):
            self.assertNotIn(word, text.replace("원인 표와 부록은 두지 않는다", ""), word)


class Methods(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        gen.write(RULES, d)
        cls.hist_rows = _rows(d / "history.jsonl")
        cls.occ = _rows(d / "occurrences.jsonl")
        cls.case_ids = [c["record_id"] for c in _rows(d / "cases.jsonl")]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_leak_refused(self):   # 실패 쪽: 시험 사례 기록이 이력에 들어 있으면 멈춤
        fake = dict(self.hist_rows[0], record_id=self.case_ids[0])
        with self.assertRaises(M.Leak):
            M.History(self.hist_rows + [fake], self.occ, self.case_ids)

    def test_view_hides_future(self):
        h = M.History([dict(r) for r in self.hist_rows], [dict(o) for o in self.occ], self.case_ids)
        t = datetime(2026, 7, 1, 12, 0)
        recs, occ = h.view(t)
        self.assertEqual(sum(1 for r in recs if r["_t"] >= t - timedelta(hours=2)), 0)
        self.assertEqual(sum(1 for o in occ if o["_t"] >= t), 0)
        self.assertTrue(recs and occ)

    def test_similarity_same_as_packrt_release(self):   # 실제 꼴: 메모리 store = packrt가 release 폴더(chunks.jsonl)를 읽은 store와 같은 결과
        h = M.History([dict(r) for r in self.hist_rows], [dict(o) for o in self.occ], self.case_ids)
        alarm = {x: self.hist_rows[-1][x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}
        got = M.similarity(K, alarm, h)
        recs, _ = h.view(M._t(alarm["ts"]))
        with tempfile.TemporaryDirectory() as rel:
            chunks = [{"id": r["record_id"], "text": M._text(r), "locator": {"record_id": r["record_id"]},
                       "metadata": {"symptom_group": r["group"], "cause": r["cause"], **r["sensors"]}} for r in recs]
            (Path(rel) / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
            (Path(rel) / "vectors.json").write_text("{}", encoding="utf-8")
            store = FlatLocal(rel)
            p = {"method": "lexical", "lexical": {"tokenizer": "ko-bigram-1", "k1": 1.5, "b": 0.75},
                 "rerank": {"fields": M.FIELDS, "weights": {"retrieval": 0.5, "sensor": 0.5}}, "top_k": 10, "return_metadata": ["cause"]}
            norm = M.rerank_norm(chunks, {"from": {"symptom_group": "CL"}, "fields": M.FIELDS})
            hits = store.search2(None, M.QUERY_TEXT, p, {"symptom_group": "CL"}, norm, alarm["sensors"])
        self.assertEqual(got["hits"], [x["locator"]["record_id"] for x in hits])
        self.assertEqual(len(got["hits"]), 10)

    def test_select_outside_only(self):
        probs = {c: 0.05 for c in K.causes}
        probs["X1"] = 0.65
        self.assertEqual([s["section"] for s in M.select(K, probs, {"X1", "X2", "X3"})], ["6.2"])

    def test_select_grade_mode(self):   # 권한: 등급 B · C는 '단독'이 아님
        probs = {c: 0.0 for c in K.causes}
        probs.update(C2=0.5, C5=0.5)
        steps = M.select(K, probs, set())
        self.assertEqual({s["section"]: s["mode"] for s in steps if s["kind"] == "action"}, {"5.2": "승인 요청", "5.5": "파트장 지시"})

    def test_manual_only_all_steps(self):
        out = M.manual_only(K, None, None)["steps"]
        self.assertEqual(len(out), len(RULES["steps"]))
        self.assertEqual(out[0]["section"], "4.4")

    def test_methods_run_on_dev_records(self):   # 방식 손보기는 이력 안 사례(dev)로만 — 시험 100건 0
        dev = self.hist_rows[-5:]
        h = M.History([dict(r) for r in self.hist_rows[:-5]], [dict(o) for o in self.occ], self.case_ids)
        for r in dev:
            alarm = {x: r[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}
            for name, fn in M.METHODS.items():
                out = fn(K, alarm, h)
                self.assertTrue(out["steps"], name)
                self.assertTrue(all(s["section"] in RULES["steps"] for s in out["steps"]), name)


if __name__ == "__main__":
    unittest.main()
