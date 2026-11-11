"""D-543 ② 적재기 시험 — 그래프 사본에 시험 꼬리표로 넣고 끝나면 지움 · 받은 편지함 · release는 임시 폴더만. 실패 글은 칸 이름 · 개수만.
KG3_HISTORY=<앱 저장소>/data/history/maintenance_history.jsonl KG_INBOX_EXAMPLE=<c1a15-app handoff>/d543/kg-inbox-example.json \\
  kg-test/.venv-kg/bin/python -B -m unittest discover -s kg-test/graph-v3 -p 'test_loader.py'"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import build_v3 as B
import loader as L
import precheck_examples as X
from conn import driver

HIST = Path(os.environ["KG3_HISTORY"])
EXAMPLE = json.loads(Path(os.environ["KG_INBOX_EXAMPLE"]).read_text(encoding="utf-8"))   # 앱 시험 DB가 승인한 이력 한 건 그대로(앱 초안)
PACK = B.KG.parent / "pack"
from custom import graph_query as G  # noqa: E402 — loader가 Pack custom을 sys.path에 넣음


class Loader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drv = driver()
        cls.tags = set()
        with cls.drv.session() as s:   # 시험 전에 있던 꼬리표(지금 current.json의 버전 등)는 지우지 않음
            cls.before = {r["l"] for r in s.run("MATCH (m:Layer) RETURN m.layer AS l").data()}

    @classmethod
    def tearDownClass(cls):
        with cls.drv.session() as s:
            for t in cls.tags - cls.before:
                B.clear(s, t)
        cls.drv.close()

    def _dirs(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        inbox, out = Path(d.name) / "inbox", Path(d.name) / "out/current.json"
        inbox.mkdir()
        return inbox, out

    def _put(self, inbox, name, body):
        (inbox / name).write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), encoding="utf-8")

    def _load(self, inbox, out):
        r = L.load(HIST, inbox, out, self.drv)
        self.tags.add(r["tag"])
        return r

    def test_inbox_record_becomes_new_version(self):   # 사람 손 길 · 실제 꼴: 앱 승인 편지 하나 → 새 꼬리표 · release에 그 기록(시각 초 단위) · 관계 근거 +1
        inbox, out = self._dirs()
        base = self._load(inbox, out)
        a = json.loads(out.read_text(encoding="utf-8"))   # 같은 자리(current.json)에 덮어쓰므로 먼저 읽어 둠
        self._put(inbox, "RPT-000001.json", EXAMPLE)
        new = self._load(inbox, out)
        self.assertNotEqual(base["tag"], new["tag"])
        self.assertEqual((new["records"], new["rejected"], new["changed"]), ({"history": 300, "inbox": 1}, [], True))
        b = json.loads(out.read_text(encoding="utf-8"))
        rec = next(r for r in b["records"] if r["id"] == EXAMPLE["record"]["record_id"])
        self.assertEqual(len(rec["ts"]), 25)   # 'YYYY-MM-DDTHH:MM:SS+hh:mm'
        self.assertEqual(sum(x["n"] for x in b["candidate"]), sum(x["n"] for x in a["candidate"]) + 1)
        self.assertEqual(sum(x["n"] for x in b["indicates"]), sum(x["n"] for x in a["indicates"]) + sum(1 for s in EXAMPLE["record"]["steps"] if s["kind"] == "check" and s["result"] == "이상"))

    def test_same_originals_same_version(self):   # 같은 원본이면 같은 버전 · 다시 만들지 않음 · 처음 이력만이면 관계 = 지금 release(current.json)
        inbox, out = self._dirs()
        one, two = self._load(inbox, out), self._load(inbox, out)
        self.assertEqual((one["tag"], one["changed"], two["changed"]), (two["tag"], True, False))
        rel = json.loads(Path(one["release"]).read_text(encoding="utf-8"))
        now = json.loads((PACK / "templates/graph/current.json").read_text(encoding="utf-8"))
        for k in ("indicates", "remedied_by", "candidate", "records", "sections", "causes", "groups"):
            self.assertEqual(rel[k], now[k], k)

    def test_bad_letters_skipped_and_listed(self):   # 실패 쪽: 꼴이 틀린 편지는 빼고 이름 · 까닭만 남김 · 나머지는 싣음
        inbox, out = self._dirs()
        self._put(inbox, "RPT-000001.json", EXAMPLE)
        bad_sec = json.loads(json.dumps(EXAMPLE))
        bad_sec["record"]["record_id"] = "R-9002"
        next(s for s in bad_sec["record"]["steps"] if s["kind"] == "check")["section"] = "9.9"
        self._put(inbox, "RPT-000002.json", bad_sec)
        self._put(inbox, "RPT-000003.json", "{not json")
        self._put(inbox, "RPT-000004.json", {**EXAMPLE, "kind": "r12"})
        self._put(inbox, "RPT-000005.json", EXAMPLE)   # 같은 이력 번호 두 번
        r = self._load(inbox, out)
        self.assertEqual(r["records"]["inbox"], 1)
        self.assertEqual([x["file"] for x in r["rejected"]], ["RPT-000002.json", "RPT-000003.json", "RPT-000004.json", "RPT-000005.json"])

    def test_pack_reads_loaded_release(self):   # 받아 넣기 전 확인: Pack 판정 지식이 새 release에서 그대로 만들어짐(바꿔 끼움 = current.json)
        inbox, out = self._dirs()
        self._put(inbox, "RPT-000001.json", EXAMPLE)
        r = self._load(inbox, out)
        sys.path.insert(0, str(PACK))
        from custom import graph_query as G
        k = G.knowledge_from_graph(out)
        self.assertEqual(sorted(k.causes), ["CL", "LD", "PW", "TW"])
        self.assertIn(EXAMPLE["record"]["record_id"], {x["id"] for x in G.release(out)["records"]})
        G.release.cache_clear()

    def test_nodes_file_adds_exception_rows(self):   # D-545 --nodes: 추출기 줄 파일 → 예외 절 · 관계가 그래프를 거쳐 release에 · 분 채움 · 틀린 줄은 빼고 목록 · Pack이 알람에 맞춤
        inbox, out = self._dirs()
        nodes = Path(__file__).with_name("nodes-example.json")   # 꼴 정본(handoff/d545/graph-shape.md) 예 — 맞는 줄 하나 · 틀린 줄 둘
        r = self._load_nodes(inbox, out, nodes)
        self.assertEqual((r["tag"].endswith("-ex1"), r["exception"]), (True, {"sections": 1, "relations": 1, "carried": 0}))
        self.assertEqual([x["why"] for x in r["rejected_rows"]], ["매뉴얼에 없는 증상군", "예외 조건 칸이 허용 목록 밖"])
        rel = json.loads(out.read_text(encoding="utf-8"))
        ex = [s for s in rel["sections"] if s["kind"] == "예외"]
        self.assertEqual((ex[0]["app_no"], ex[0]["minutes"], ex[0]["criteria"]), (ex[0]["no"], 5, None))   # app_no = no · 분 = 온도 차 저하 점검 절 중앙값
        self.assertEqual(rel["exception_relations"][0]["when"], {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2]]})
        self.assertEqual(rel["source"]["exception_records"], ["R-0001"])
        now = json.loads((PACK / "templates/graph/current.json").read_text(encoding="utf-8"))
        self.assertEqual([s for s in rel["sections"] if s["kind"] != "예외"], now["sections"])   # 기존 절 · 관계는 그대로
        self.assertEqual(rel["indicates"], now["indicates"])
        self.assertFalse(self._load_nodes(inbox, out, nodes)["changed"])
        sys.path.insert(0, str(PACK))
        from custom import graph_query as G
        hit = G.exception_match("CL", {"temp_diff_k": 7.9, **{f: 0 for f in ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min", "power_w")}}, path=out)
        self.assertEqual(hit[0]["id"], "EX-R-0001")
        G.release.cache_clear()

    def _nodes(self, name, rid, sensors, n=1):   # 추출기 줄 파일 한 줄(꼴 = nodes-example 맞는 줄)
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        sec.update(no=f"EX-{rid}", records=[rid])
        rel.update(id=f"EX-{rid}", step=f"EX-{rid}", records=[rid], n=n, when={"group": "온도 차 저하", "sensors": sensors})
        p = Path(tempfile.mkdtemp()) / name
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps({"sections": [sec], "exception_relations": [rel]}, ensure_ascii=False), encoding="utf-8")
        return p

    def test_exception_rows_carry_over(self):   # D-546 덧 27 ①(이음 시험 181 2회차 꼴): 추출기는 새 줄만 냄 → 적재기가 지금 release의 예외 줄을 이어받음 · 같은 id면 새 것
        inbox, out = self._dirs()
        one = self._load_nodes(inbox, out, self._nodes("n1.json", "R-0001", [["temp_diff_k", "<=", 8.2]]))
        two = self._load_nodes(inbox, out, self._nodes("n2.json", "R-0002", [["temp_diff_k", "<=", 7.0]]))
        rel = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in rel["exception_relations"]], ["EX-R-0001", "EX-R-0002"])   # 고치기 전: EX-R-0002 하나뿐
        self.assertEqual(sorted(s["no"] for s in rel["sections"] if s["kind"] == "예외"), ["EX-R-0001", "EX-R-0002"])
        self.assertEqual(sorted(rel["source"]["exception_records"]), ["R-0001", "R-0002"])
        self.assertEqual((one["tag"].endswith("-ex1"), two["tag"].endswith("-ex2")), (True, True))   # 꼬리표 = 실제 관계 수
        again = self._load_nodes(inbox, out, self._nodes("n3.json", "R-0001", [["temp_diff_k", "<=", 8.2]], n=2))   # 같은 id → 새 것(n 2)
        rel = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([(x["id"], x["n"]) for x in rel["exception_relations"]], [("EX-R-0001", 2), ("EX-R-0002", 1)])
        self.assertTrue(again["tag"].endswith("-ex2"))
        self.assertNotEqual(again["tag"], two["tag"])
        plain = self._load(inbox, out)   # 새 줄 파일 없이 다시 적재해도 예외 줄은 남음(지우기는 LLMOps '끔' 길만)
        self.assertEqual((plain["tag"], plain["changed"]), (again["tag"], False))

    def test_same_tag_same_content(self):   # 같은 꼬리표에 다른 내용 0 — 관계 수가 같아도 예외 줄이 다르면 다른 꼬리표(고치기 전: 둘 다 <기준>-ex1)
        a = self._load_nodes(*self._dirs(), self._nodes("a.json", "R-0001", [["temp_diff_k", "<=", 8.2]]))
        b = self._load_nodes(*self._dirs(), self._nodes("b.json", "R-0002", [["temp_diff_k", "<=", 7.0]]))
        self.assertEqual((a["tag"].endswith("-ex1"), b["tag"].endswith("-ex1")), (True, True))
        self.assertNotEqual(a["tag"], b["tag"])

    def test_exception_session_letter_kept(self):   # D-546 덧 27 ②: 예외 절(EX-…)을 거친 세션 편지는 받음 — EX 절은 번호를 바꾸지 않고 매뉴얼 관계에도 안 넣음
        inbox, out = self._dirs()
        base = self._load(inbox, out)
        a = json.loads(out.read_text(encoding="utf-8"))
        ex = json.loads(json.dumps(EXAMPLE))
        ex["record"]["record_id"] = "R-9007"
        for s in ex["record"]["steps"]:
            if s["kind"] in ("check", "action"):
                s["section"] = "EX-R-0001"
        self._put(inbox, "RPT-000007.json", ex)
        r = self._load(inbox, out)
        self.assertEqual((r["rejected"], r["records"]["inbox"]), ([], 1))   # 고치기 전: '번호를 바꿀 수 없는 절'로 뺌
        b = json.loads(out.read_text(encoding="utf-8"))
        self.assertNotEqual(base["tag"], r["tag"])
        self.assertEqual((b["indicates"], b["remedied_by"]), (a["indicates"], a["remedied_by"]))   # 매뉴얼 절 관계 손 0
        self.assertIn("R-9007", {x["id"] for x in b["records"]})   # 원인 확정 기록 → 다음 개수 셈(scope_counts)에 들어감
        self.assertEqual(sum(x["n"] for x in b["candidate"]), sum(x["n"] for x in a["candidate"]) + 1)

    def test_exception_fields(self):   # D-554 ④: 예외 절 칸(점검 · 조치 · 덧붙임) → 그래프를 거쳐 release에 그대로 · 분 채움 · 옛 칸 채움 · 틀린 칸 꼴은 빼고 까닭
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        del sec["guide"]
        good = {**sec, "check": {"what": "필터 덮개 나사", "how": "덮개 나사 네 개를 손으로 돌려 본다", "criteria": "네 개 모두 체결 = 정상", "minutes": None},
                "action": {"what": "필터 교체", "parts": ["냉각 필터 F-200"], "minutes": None}, "note": "야간 근무 때 두 번 봄"}
        bad = {"check": {"what": "", "how": "y", "criteria": "y", "minutes": 3},   # 빈 글(점검 what — 덧 12 뒤 방법 · 기준은 빈 글 허용)
               "action": {"what": "x", "parts": "F-200", "minutes": 3},           # 부품이 목록 아님
               "check ": {"what": "x", "how": "y", "criteria": "z", "minutes": 0}}   # 분 0(정수 ≥ 1 아님)
        rows = {"sections": [good], "exception_relations": [rel]}
        for i, (k, v) in enumerate(bad.items(), start=2):
            rid = f"R-000{i}"
            rows["sections"].append({**sec, "no": f"EX-{rid}", "records": [rid], k.strip(): v})
            rows["exception_relations"].append({**rel, "id": f"EX-{rid}", "step": f"EX-{rid}", "records": [rid]})
        rows["sections"].append({**sec, "no": "EX-R-0005", "records": ["R-0005"], "check": {**good["check"], "why": "x"}})   # 모르는 칸
        rows["exception_relations"].append({**rel, "id": "EX-R-0005", "step": "EX-R-0005", "records": ["R-0005"]})
        p = Path(tempfile.mkdtemp()) / "fields.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        self.assertEqual(r["exception"]["relations"], 1)
        self.assertEqual([x["why"] for x in r["rejected_rows"] if x["row"].startswith("EX-R-000") and x["row"] != "EX-R-0001"][:4],
                         ["점검 칸 꼴이 다름", "조치 칸 꼴이 다름", "점검 칸 꼴이 다름", "점검 칸 꼴이 다름"])   # 절 넷 빠짐(그 절을 가리킨 관계도 빠짐)
        got = next(s for s in json.loads(out.read_text(encoding="utf-8"))["sections"] if s["kind"] == "예외")
        self.assertEqual(got["checks"], [{**good["check"], "minutes": 5}])   # 온도 차 저하 점검 절 분 중앙값 · v10: 옛 하나 꼴 → 목록 하나
        self.assertEqual(got["actions"], [{**good["action"], "minutes": 10}])   # 온도 차 저하 조치 절 분 중앙값(올림)
        self.assertFalse({"check", "action"} & set(got))
        self.assertEqual((got["note"], got["guide"], got["criteria"], got["steps"], got["minutes"]),
                         (good["note"], good["check"]["how"], good["check"]["criteria"], [good["check"]["how"]], 5))   # 옛 칸 채움(Pack 판정 · 앱 옛 화면 그대로)
        self.assertFalse(self._load_nodes(inbox, out, p)["changed"])

    def test_exception_lists(self):   # v10(D-566): 예외 하나 = 주제 + 점검 목록 + 조치 목록 → 그래프를 거쳐 release에 그대로(적은 순서 · 분 채움 · 옛 칸 = 첫 점검) · 실패 쪽: 하나 꼴과 목록을 함께 · 빈 목록 · 목록 안 꼴 틀림 · 빈 주제
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        for k in ("guide", "criteria", "steps"):
            sec.pop(k, None)
        c1 = {"what": "필터 덮개 고정 나사", "how": "손으로 돌려 본다", "criteria": "모두 체결 = 정상", "minutes": None}
        c2 = {"what": "냉각수 수위", "how": "", "criteria": "", "minutes": 4}
        a1, a2 = {"what": "풀린 나사 다시 조임", "parts": [], "minutes": None}, {"what": "냉각수 보충", "parts": ["냉각수"], "minutes": 10}
        good = {**sec, "topic": "필터 덮개 나사", "checks": [c1, c2], "actions": [a1, a2]}
        bad = [{"check": c1, "checks": [c1]}, {"checks": []}, {"actions": [{**a1, "parts": "x"}]}, {"checks": [{**c1, "what": ""}]}, {"topic": " ", "checks": [c1]}]
        rows = {"sections": [good], "exception_relations": [rel]}
        for i, b in enumerate(bad, start=2):
            rid = f"R-000{i}"
            rows["sections"].append({**sec, "no": f"EX-{rid}", "records": [rid], **b})
            rows["exception_relations"].append({**rel, "id": f"EX-{rid}", "step": f"EX-{rid}", "records": [rid]})
        p = Path(tempfile.mkdtemp()) / "lists.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        self.assertEqual(r["exception"]["relations"], 1)
        whys = [x["why"] for x in r["rejected_rows"] if x["row"] in {f"EX-R-000{i}" for i in range(2, 7)} and "칸 꼴" in x["why"] or x["why"] == "주제 꼴이 다름"]
        self.assertEqual(sorted(whys), sorted(["점검 칸 꼴이 다름", "점검 칸 꼴이 다름", "조치 칸 꼴이 다름", "점검 칸 꼴이 다름", "주제 꼴이 다름"]))
        got = next(s for s in json.loads(out.read_text(encoding="utf-8"))["sections"] if s["kind"] == "예외")
        self.assertEqual(got["checks"], [{**c1, "minutes": 5}, c2])   # 적은 순서 · 분 없으면 중앙값
        self.assertEqual(got["actions"], [{**a1, "minutes": 10}, a2])
        self.assertEqual((got["topic"], got["guide"], got["criteria"], got["steps"]), ("필터 덮개 나사", c1["how"], c1["criteria"], [c1["how"]]))   # 옛 칸 = 첫 점검 · 빈 방법은 절차에서 뺌

    def test_exception_instruction(self):   # D-642 C: 승인된 지시 문장(줄마다 instruction)이 그래프를 거쳐 release · Pack 안내까지 그대로 · 실패 쪽: 문장 칸 꼴 틀림(빈 글 · 글 아님)은 그 줄만 거절 · 칸 없는 옛 줄 그대로
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        for k in ("guide", "criteria", "steps"):
            sec.pop(k, None)
        c1 = {"what": "필터 덮개 고정 나사", "how": "손으로 돌려 본다", "criteria": "모두 체결 = 정상", "minutes": 5, "instruction": "필터 덮개 고정 나사를 손으로 돌려 보고, 모두 체결이면 정상입니다."}
        a1 = {"what": "풀린 나사 다시 조임", "parts": [], "minutes": 5, "instruction": "풀린 나사를 다시 조입니다."}
        rows = {"sections": [{**sec, "topic": "필터 덮개 나사", "checks": [c1], "actions": [a1]}], "exception_relations": [rel]}
        for i, b in enumerate(({"checks": [{**c1, "instruction": ""}]}, {"actions": [{**a1, "instruction": 3}]}), start=2):
            rid = f"R-000{i}"
            rows["sections"].append({**sec, "no": f"EX-{rid}", "records": [rid], "topic": "덮개", "actions": [a1], "checks": [c1], **b})
            rows["exception_relations"].append({**rel, "id": f"EX-{rid}", "step": f"EX-{rid}", "records": [rid]})
        p = Path(tempfile.mkdtemp()) / "say.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        self.assertEqual(sorted(x["why"] for x in r["rejected_rows"] if "칸 꼴" in x["why"]), ["점검 칸 꼴이 다름", "조치 칸 꼴이 다름"])   # 그 절을 가리키는 관계도 같이 빠짐
        got = next(s for s in json.loads(out.read_text(encoding="utf-8"))["sections"] if s["kind"] == "예외")
        self.assertEqual((got["checks"], got["actions"]), ([c1], [a1]))

    def test_same_edit_replaces(self):   # D-560 7 · same-edit.md 3절 사람 손 길: 등록 예외 → 같음 + 고침 승인 → 같은 id 새 내용 · 옛 내용 previous · records + 1 · n + 1 · when 그대로 · 실패 쪽: 기준에 없는 id
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        sec.pop("guide", None)
        c1 = {"what": "필터 덮개 고정 나사", "how": "손으로 돌려 본다", "criteria": "모두 체결 = 정상", "minutes": 5}
        a1 = {"what": "풀린 나사 다시 조임", "parts": [], "minutes": 5}
        first = {"sections": [{**sec, "topic": "필터 덮개 나사", "checks": [c1], "actions": [a1]}], "exception_relations": [rel]}
        c1b = {**c1, "how": "덮개 고정 나사 네 개가 다 있는지 보고 풀린 나사가 있으면 손으로 돌려 본다"}
        a2 = {"what": "빠진 나사는 새로 끼우고 남은 나사도 다시 조인다", "parts": [], "minutes": 5}
        edit = {"sections": [{**sec, "topic": "필터 덮개 나사", "checks": [c1b], "actions": [a1, a2], "edit_record": "R-0007"}],
                "exception_relations": [{**rel, "records": ["R-0007"], "n": 1, "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 9.9]]}, "edit": True}]}
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [f.unlink() for f in d.iterdir()] and d.rmdir())
        p1, p2 = d / "first.json", d / "edit.json"
        p1.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        p2.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        self._load_nodes(inbox, out, p1)
        r = self._load_nodes(inbox, out, p2)   # 기준 = 앞 release(--base 기본 = out)
        self.assertTrue(r["changed"])
        got = json.loads(out.read_text(encoding="utf-8"))
        s = next(x for x in got["sections"] if x["kind"] == "예외")
        x = got["exception_relations"][0]
        self.assertEqual((s["no"], s["topic"], s["checks"], s["actions"]), ("EX-R-0001", "필터 덮개 나사", [c1b], [a1, a2]))   # 같은 이름 · 새 내용
        self.assertEqual(s["previous"], [{"record": "R-0007", "topic": "필터 덮개 나사", "checks": [c1], "actions": [a1]}])   # 옛 내용
        self.assertNotIn("edit_record", s)
        self.assertEqual((x["records"], x["n"], x["when"], x["cause"]), (["R-0001", "R-0007"], 2, rel["when"], rel["cause"]))   # 근거 + 1 · 조건 · 원인 기준 그대로
        self.assertNotIn("edit", x)
        self.assertEqual(len(got["exception_relations"]), 1)   # 새 이름 0
        p3 = d / "missing.json"
        p3.write_text(json.dumps({"sections": [{**edit["sections"][0], "no": "EX-R-0099", "records": ["R-0099"]}],
                                   "exception_relations": [{**edit["exception_relations"][0], "id": "EX-R-0099", "step": "EX-R-0099"}]}, ensure_ascii=False), encoding="utf-8")
        r3 = self._load_nodes(inbox, out, p3)
        self.assertEqual([b["why"] for b in r3["rejected_rows"] if b["row"] == "EX-R-0099"], ["고칠 예외가 그래프에 없음"] * 2)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["exception_relations"], got["exception_relations"])   # 나머지 그대로

    def test_empty_criteria(self):   # D-556 덧 12: 기준이 빈 점검도 받음(옛 기준 칸 = None → Pack 안내 기준 줄 0) · 실패 쪽: 기준 칸 없음 · 글 아님은 뺌
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        for k in ("guide", "criteria"):
            sec.pop(k, None)
        chk = {"what": "구동 벨트", "how": "", "criteria": "", "minutes": None}   # 덧 12: 방법 · 기준 둘 다 빈 글
        rows = {"sections": [{**sec, "check": chk}], "exception_relations": [rel]}
        for i, c in enumerate(({k: v for k, v in chk.items() if k != "criteria"}, {**chk, "how": None}), start=2):   # 실패 쪽: 기준 칸 없음 · 방법이 글 아님
            rid = f"R-000{i}"
            rows["sections"].append({**sec, "no": f"EX-{rid}", "records": [rid], "check": c})
            rows["exception_relations"].append({**rel, "id": f"EX-{rid}", "step": f"EX-{rid}", "records": [rid]})
        p = Path(tempfile.mkdtemp()) / "empty.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        self.assertEqual(r["exception"]["relations"], 1)
        why = [x["why"] for x in r["rejected_rows"] if x["row"] in ("EX-R-0002", "EX-R-0003")]
        self.assertEqual(why.count("점검 칸 꼴이 다름"), 2)   # 기준 칸 없음 · 방법 null — 절 둘 빠짐
        self.assertEqual(len(why), 4)   # 그 절을 가리킨 관계 줄 둘도 빠짐
        got = next(s for s in json.loads(out.read_text(encoding="utf-8"))["sections"] if s["kind"] == "예외")
        self.assertEqual((got["checks"][0]["criteria"], got["checks"][0]["how"], got["criteria"], got.get("guide"), got["steps"]), ("", "", None, None, []))   # 안내 글(None이면 그래프 DB에 칸 0) · 절차 · 기준 줄 0

    def test_recent_condition_and_record_work(self):   # D-570 recent-condition.md: 최근 정비 조건 줄 실음 · release 이력에 한 조치(did) · 점검(checked) 절(앱 번호) · 실패 쪽: 사전 확인 절 · 없는 절 · 꼴 밖 days → 그 줄만 뺌
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = dict(ex["sections"][0]), dict(ex["exception_relations"][0])
        rows = {"sections": [sec], "exception_relations": []}
        for rid, recent in (("EX-R-0001", {"section": "4.3.2", "days": 7}), ("EX-R-0002", {"section": "4.2", "days": 7}),
                            ("EX-R-0003", {"section": "9.9", "days": 7}), ("EX-R-0004", {"section": "4.3", "days": 31})):
            rows["exception_relations"].append({**rel, "id": rid, "when": {"group": "온도 차 저하", "sensors": [], "recent": recent}})
        rows["exception_relations"].append({**rel, "id": "EX-R-0005", "when": {"group": "온도 차 저하", "sensors": []}})   # D-570 덧 4(eval 186): 조건 0 줄 = 그 증상군 모든 알람에 추천 → 거절
        p = Path(tempfile.mkdtemp()) / "recent.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        self.assertEqual([(x["row"], x["why"]) for x in r["rejected_rows"]],
                         [("EX-R-0002", "최근 정비 절이 그 증상군 점검 · 조치 절이 아님"), ("EX-R-0003", "최근 정비 절이 그 증상군 점검 · 조치 절이 아님"), ("EX-R-0004", "최근 정비 조건 꼴이 다름"),
                          ("EX-R-0005", "조건 칸이 하나도 없음(센서 · 설비 · 최근 정비)")])
        g = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([x["when"]["recent"] for x in g["exception_relations"]], [{"section": "4.3.2", "days": 7}])
        q = "MATCH p = (e:ExRel)-[w:WHEN_RECENT]->(s:Section) WHERE e.layer = $v RETURN e.id AS id, w.app_no AS app_no, w.days AS days, s.no AS no"   # D-592 시연 쿼리와 같은 선
        want = [{"id": "EX-R-0001", "app_no": "4.3.2", "days": 7, "no": next(x["no"] for x in g["sections"] if x.get("app_no") == "4.3.2")}]
        with self.drv.session() as s:
            self.assertEqual(s.run(q, v=r["tag"]).data(), want)   # 절 번호는 그래프 번호 · 앱 번호는 선 칸 · 조건 0 · 거절 줄은 선 0
            self.assertEqual(len(s.run("MATCH p = (e:ExRel)-[:WHEN_RECENT|GUIDES]->(:Section) WHERE e.layer = $v RETURN p", v=r["tag"]).data()), 2)   # 시연 쿼리 그대로: 안내 절 선 + 조건 절 선
            s.run("MATCH (:ExRel {layer: $v})-[w:WHEN_RECENT]->() DELETE w", v=r["tag"]).consume()   # 선 없는 옛 꼬리표(이 고침 전 적재)
        again = self._load_nodes(inbox, out, p)
        self.assertEqual((again["tag"], again["changed"]), (r["tag"], False))
        with self.drv.session() as s:
            self.assertEqual(s.run(q, v=r["tag"]).data(), want)   # 같은 내용 다시 받으면 선만 채움 · 두 번 그리지 않음
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), g)   # 선은 그래프 DB만 — release 파일 그대로
        kind = {s["app_no"]: s["kind"] for s in g["sections"] if s["kind"] != "예외"}
        self.assertTrue(all("did" in h and "checked" in h for h in g["records"]))
        self.assertTrue(any(h["did"] for h in g["records"]) and any(h["checked"] for h in g["records"]))
        self.assertEqual({kind[n] for h in g["records"] for n in h["did"]}, {"조치"})
        self.assertEqual({kind[n] for h in g["records"] for n in h["checked"]}, {"점검"})

    def test_precheck_examples(self):   # D-619 사람 손 길: 예시 줄 → 적재기 → 패턴 그림(deploy 쿼리 그대로) → 승인 보고서가 덮개 선을 잇음 · 메인 시연 값 변화 0 · 실패 쪽: 시연 네 기기에 예시 0 · 후보 설비 뒤 선 0
        at = "2026-11-07T10:00:00+09:00"
        four = ("M-01", "M-02", "M-03", "M-13")
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [*(f.unlink() for f in d.iterdir()), d.rmdir()])
        rows = X.rows(HIST, at)
        self.assertEqual((len(rows), len({r["record_id"] for r in rows}), {r["demo"] for r in rows}), (25, 25, {"D-619"}))
        self.assertFalse({r["equipment_id"] for r in rows} & set(four))
        self.assertEqual(sum(1 for r in rows if r["equipment_id"] == "M-04"), 3)
        for r in rows:   # 이력 화면에 보이는 글 · 시각이 줄 값과 맞음(본 줄 값이 남지 않음 · 앱 보고)
            self.assertEqual(set(re.findall(r"M-\d\d", json.dumps(r, ensure_ascii=False))), {r["equipment_id"]}, r["record_id"])
            self.assertTrue(r["narrative"].startswith(r["occurred_at"][11:16] + " " + r["equipment_id"]), r["record_id"])
            self.assertTrue(all(r["occurred_at"] <= x["at"] <= r["closed_at"] for x in r["steps"] if x.get("at")), r["record_id"])
            self.assertTrue(r["closed_at"] <= r["approval"]["at"] <= at, r["record_id"])
        self.assertEqual(sum("덮개" in r["narrative"] for r in rows), 2)   # A 뒤 줄 글도 덮개
        h0301 = {**next(r for r in rows if r["record_id"] == "H-P007"), "record_id": "H-0301", "equipment_id": "M-01", "occurred_at": "2026-11-04T10:00:00+09:00", "closed_at": "2026-11-04T10:30:00+09:00", "demo": "D-570", "outcome": {"type": "원인 확정", "cause_code": "CL-1", "cause_name": "냉각 필터 막힘"}}
        hist = d / "history.jsonl"
        hist.write_text(HIST.read_text(encoding="utf-8").rstrip("\n") + "\n" + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [*rows, h0301]), encoding="utf-8")
        inbox, out = self._dirs()
        _, plain = self._dirs()
        base = self._load(inbox, plain)
        r = L.load(hist, inbox, out, self.drv)
        self.tags.add(r["tag"])
        one, two = (json.loads(f.read_text(encoding="utf-8")) for f in (plain, out))
        h = next(x for x in two["records"] if x["id"] == "H-0301")   # H-0301은 지금 시연에도 있는 줄
        two["records"].remove(h)
        two["candidate"] = one["candidate"] if [x for x in two["candidate"] if x["code"] != "CL-1"] == [x for x in one["candidate"] if x["code"] != "CL-1"] else two["candidate"]
        skip = ("layer", "release_id", "source", "indicates", "remedied_by")   # 꼬리표 · 이력 sha · H-0301 몫 근거 수
        self.assertEqual({k: v for k, v in two.items() if k not in skip}, {k: v for k, v in one.items() if k not in skip})   # 예시 줄은 release 판정 재료에 안 듦(원인 확정 0)
        q = {"A": "MATCH p = (e:Equipment {layer: $v})-[:HAD]->(a:Record {layer: $v})-[:DID]->(:Section {no: '3.3.4'}) WHERE a.demo = $mark OR a.id = 'H-0301' OPTIONAL MATCH q = (a)-[n:NEXT]->(b:Record)-[:HAD_ISSUE]->(i) WHERE n.days <= 7 RETURN a.equipment AS eq, q IS NOT NULL AS hit",
             "B": "MATCH p = (e:Equipment {layer: $v})-[:HAD]->(a:Record {layer: $v})-[:DID]->(:Section {no: '3.1.1'}) WHERE a.demo = $mark OPTIONAL MATCH q = (a)-[n:NEXT]->(b:Record)-[:CHECKED {result: '이상'}]->(:Section {no: '2.1.3'}) WHERE n.days <= 14 RETURN a.equipment AS eq, q IS NOT NULL AS hit",
             "C": "MATCH p = (e:Equipment {layer: $v})-[:HAD]->(a:Record {layer: $v})-[:DID]->(:Section {no: '3.2.2'}) WHERE a.demo = $mark OPTIONAL MATCH q = (a)-[n:NEXT]->(b:Record)-[:CHECKED {result: '이상'}]->(:Section {no: '2.2.3'}) WHERE n.days <= 7 RETURN a.equipment AS eq, q IS NOT NULL AS hit"}

        def seen(tag):
            with self.drv.session() as s:
                return {k: s.run(v, v=tag, mark="D-619").data() for k, v in q.items()}
        got = seen(r["tag"])
        self.assertEqual({k: (len(v), sum(x["hit"] for x in v)) for k, v in got.items()}, {"A": (6, 2), "B": (6, 3), "C": (6, 3)})
        self.assertFalse([x for v in got.values() for x in v if x["eq"] == "M-04" and x["hit"]])   # 지금 설비는 뒤가 비어 있음
        self._put(inbox, "RPT-007001.json", {**EXAMPLE, "record": {**EXAMPLE["record"], "record_id": "R-7001", "equipment_id": "M-01", "occurred_at": "2026-11-07T09:00:00+09:00", "closed_at": "2026-11-07T09:30:00+09:00",
                                                                  "exception_record": {"text": "3일 전 점검한 흡기 필터 덮개 볼트가 덜 조여져 있었음 · 다시 조임"}}})
        after = L.load(hist, inbox, out, self.drv)
        self.tags.add(after["tag"])
        self.assertEqual(after["rejected"], [])
        got = seen(after["tag"])
        self.assertEqual((len(got["A"]), sum(x["hit"] for x in got["A"])), (6, 3))   # 승인 뒤 이어짐 셋
        self.assertTrue(next(x for x in got["A"] if x["eq"] == "M-01")["hit"])
        with self.drv.session() as s:
            self.assertEqual(s.run("MATCH (:Record {layer: $v})-[:HAD_ISSUE]->(i:Issue {layer: $v}) RETURN i.name AS name, count(*) AS n", v=after["tag"]).data(), [{"name": "필터 덮개 고정", "n": 3}])
            self.assertEqual(s.run("MATCH (i:Issue {layer: $v}) RETURN count(i) AS n", v=base["tag"]).single()["n"], 0)   # 실패 쪽: 예시 없는 그래프는 노드 0

    def test_precheck_examples_pattern_a_only(self):   # D-642 E(사용자 R11): 시연 첫 상태엔 패턴 A 줄만(B · C 줄은 만들지 않음 · 파일 지움 0) · 실패 쪽: 모르는 패턴은 거절 · 기본은 셋 다(지금 그대로)
        at = "2026-11-07T10:00:00+09:00"
        a = X.rows(HIST, at, patterns=("A",))
        self.assertEqual(len(a), 7)
        self.assertEqual({r["equipment_id"] for r in a}, {"M-04", "M-05", "M-06", "M-07", "M-08"})
        self.assertEqual({r["symptom_group"]["code"] for r in a}, {"CL"})
        self.assertEqual([r["record_id"] for r in a], [r["record_id"] for r in X.rows(HIST, at)][:7])   # 번호 · 값은 셋 다일 때의 A 줄과 같음
        self.assertEqual(len(X.rows(HIST, at)), 25)
        with self.assertRaises(ValueError):
            X.rows(HIST, at, patterns=("Z",))

    def test_precheck_examples_pattern_f(self):   # D-656 메인 시나리오: 컨트롤러 펌웨어 업데이트(7.1 전기 계통 작업 — 매뉴얼에 펌웨어 절 없음 · 지어낸 절 0) → 7일 안 4.6 냉각 설정값 이상
        at = "2026-11-08T21:26:26+09:00"
        now = datetime.fromisoformat(at)
        f = X.rows(HIST, at, patterns=("F",))
        self.assertEqual(len(f), 8)   # 다른 설비 넷(이어짐 둘 = 앞 · 뒤 · 이상 없음 둘) + M-02 · M-04 앞 줄
        self.assertEqual([r["record_id"] for r in f], [f"H-P{i:03d}" for i in range(26, 34)])   # 번호는 A · B · C 25줄 뒤
        self.assertEqual(len(X.rows(HIST, at)), 25)   # 기본(F 없음)은 지금 그대로 · 시연 기기 줄 0
        fw = [r for r in f if "7.1" in [s["section"] for s in r["steps"] if s["kind"] == "action"]]
        self.assertEqual(sorted(r["equipment_id"] for r in fw), ["M-02", "M-04", "M-05", "M-06", "M-07", "M-08"])
        for r in fw:   # 펌웨어 줄: 잠금 · 7.1 조치 · 재가동만 · 예외 칸 0 · 이력 글이 펌웨어 업데이트
            self.assertEqual([(s["kind"], s["section"]) for s in r["steps"]], [("lock", "1.2"), ("action", "7.1"), ("restart", "6.1")])
            self.assertIn("컨트롤러 펌웨어 업데이트", r["narrative"])
            self.assertNotIn("exception_record", r)
            self.assertEqual((r["symptom_group"]["code"], r["escalations"]), ("PW", []))
        ago = {r["equipment_id"]: (now - datetime.fromisoformat(r["occurred_at"])).days for r in fw if r["equipment_id"] in ("M-02", "M-04")}
        self.assertEqual(ago, {"M-02": 5, "M-04": 3})
        follow = [r for r in f if any(s["kind"] == "check" and s["section"] == "4.6" and s["result"] == "이상" for s in r["steps"])]
        self.assertEqual(sorted(r["equipment_id"] for r in follow), ["M-05", "M-06"])
        for r in follow:   # 같은 설비 펌웨어 뒤 7일 안 · 원인 칸 빔(release · Pack 판정 재료 밖)
            b = next(x for x in fw if x["equipment_id"] == r["equipment_id"])
            self.assertTrue(timedelta(0) < datetime.fromisoformat(r["occurred_at"]) - datetime.fromisoformat(b["occurred_at"]) <= timedelta(days=7))
            self.assertEqual(r["outcome"], {"type": "원인 미상·숙련자 인계"})
        for r in f:
            self.assertEqual(set(re.findall(r"M-\d\d", json.dumps(r, ensure_ascii=False))), {r["equipment_id"]}, r["record_id"])
            self.assertTrue(r["narrative"].startswith(r["occurred_at"][11:16] + " " + r["equipment_id"]), r["record_id"])
            self.assertTrue(all(r["occurred_at"] <= x["at"] <= r["closed_at"] for x in r["steps"] if x.get("at")), r["record_id"])
            self.assertTrue(r["closed_at"] <= r["approval"]["at"] <= at, r["record_id"])
        cf = X.rows(HIST, at, patterns=("C", "F"))
        self.assertEqual(cf, [*X.rows(HIST, at, patterns=("C",)), *f])   # C 줄은 F와 같이 만들어도 그대로

    def test_exception_node_name(self):   # D-619 사용자 답: Neo4j 예외 노드 표시 이름 = 예외 주제('원인 미상이며…' 글이 이름 자리에 뜸) · 표시만 — release 그대로 · 실패 쪽: 주제 없는 절은 절 이름 · 옛 꼬리표는 같은 내용을 다시 받으면 채움
        ex = json.loads(Path(__file__).with_name("nodes-example.json").read_text(encoding="utf-8"))
        sec, rel = {**ex["sections"][0], "topic": "흡기 필터 덮개 볼트"}, {**ex["exception_relations"][0], "cause_name": "원인 미상이며, 잠금 확인(1.2)을 실시함."}
        p = Path(tempfile.mkdtemp()) / "name.json"
        self.addCleanup(lambda: (p.unlink(), p.parent.rmdir()))
        p.write_text(json.dumps({"sections": [sec], "exception_relations": [rel]}, ensure_ascii=False), encoding="utf-8")
        inbox, out = self._dirs()
        r = self._load_nodes(inbox, out, p)
        g = json.loads(out.read_text(encoding="utf-8"))
        q = "MATCH (e:ExRel {layer: $v}) RETURN e.name AS name"
        with self.drv.session() as s:
            self.assertEqual(s.run(q, v=r["tag"]).data(), [{"name": "흡기 필터 덮개 볼트"}])
            s.run("MATCH (e:ExRel {layer: $v}) REMOVE e.name", v=r["tag"]).consume()   # 이 고침 전 적재한 꼬리표
        self.assertNotIn("name", g["exception_relations"][0])
        again = self._load_nodes(inbox, out, p)
        self.assertEqual((again["tag"], again["changed"], again["rejected_rows"]), (r["tag"], False, []))
        with self.drv.session() as s:
            self.assertEqual(s.run(q, v=r["tag"]).data(), [{"name": "흡기 필터 덮개 볼트"}])
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), g)
        sec2 = {k: v for k, v in sec.items() if k != "topic"}
        self.assertEqual(L.relation_names([sec2], [rel]), {rel["id"]: sec2["name"]})

    def _load_nodes(self, inbox, out, nodes):
        r = L.load(HIST, inbox, out, self.drv, nodes)
        self.tags.add(r["tag"])
        return r


if __name__ == "__main__":
    unittest.main()
