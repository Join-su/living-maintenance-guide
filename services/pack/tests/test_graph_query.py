"""D-536 ④ 그래프 질의 node(custom/graph_query.py) 시험 — 그래프 release 파일만 읽음(망 0 · 그래프 DB 없이) · 임시 폴더만. 실패 글은 칸 이름 · 개수만.
- 실제 꼴: 그래프 release 하나에서 만든 판정 지식 = 앱 매뉴얼 2.0 사본에서 만든 지식(원인 후보 · 점검 → 원인(선별 · 확정) · 원인 → 조치 · 사전 확인 · 체크리스트)
  · Pack은 매뉴얼 파일을 열지 않음(D-543 — 사본은 이 시험에서만 맞대기로 읽음)
- release 무결: release id = 내용 sha256 앞 8자리 · 저장소 밖 경로 0
- 비슷한 기록: 옵션 셋만 · 범위 값 · 최소 근거 수 → 고른 기록 0 · 규칙 표 한 줄이 근거를 좁힘 · 노하우 줄이 규칙 표 앞 · 근거 부족 노하우는 고르기에 안 씀
- 실패 쪽: 허용 밖 칸 · start에서 볼 수 없는 관계 · 그래프에 없는 원인 · 규칙 표 머리 다름은 거절
- 순서 신호(D-540): 코드 기본 = 비슷한 10건(지금 출력) · evidence = 몫 크기 그대로 · 나누는 기준만 그래프 근거 건수 · 근거 n · 근거 적음은 비슷한 10건 그대로 · 모르는 값 거절"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "pack"
if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))

from custom import graph_query as G, judge, knowledge, rules, switch  # noqa: E402
from start_contract import requests  # noqa: E402

HEAD = "id,name,rel,where,then,basis\n"


def _sections():
    raw = json.loads((PACK / "custom/manual_sections.json").read_text(encoding="utf-8"))
    return [{"id": s["id"], **{k: s.get(k) for k in ("title", "kind", "chapter", "group", "grade", "minutes", "parent")}, "text": s.get("text") or ""} for s in raw]


def _hq(group="CL"):
    rel = G.release()
    r = next(x for x in rel["records"] if x["group"] == group)
    return {"text": "", "group": group, "sensors": dict(r["sensors"])}


class Knowledge(unittest.TestCase):
    def test_graph_knowledge_equals_manual_copy(self):   # 제목은 3.0에서 바뀐 절이 있어 빼고 견줌(판정 출력에 제목 0)
        a, b = knowledge.from_sections(_sections()), G.knowledge_from_graph()
        self.assertEqual(a.causes, b.causes, "원인 후보 · 확정 점검 · 조치")
        strip = lambda k: {g: [(c.section_id, c.group, c.cause, c.kind) for c in cs] for g, cs in k.checks.items()}
        self.assertEqual(strip(a), strip(b), "점검 → 원인 · 선별/확정")
        self.assertEqual(a.prechecks, b.prechecks)
        # 체크리스트: 2.0은 점검 · 조치가 한 절(2.3 · 4.5)이라 점검에 붙었고, 3.0은 조치 절(3.1.2 · 3.3.3)로 갈라 조치 노드에 붙음 — 덩어리는 같음(판정 출력에 체크리스트 글 0)
        raw = json.loads((PACK / "custom/manual_sections.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(knowledge._checklist(s.get("text")) for s in raw if knowledge._checklist(s.get("text"))),
                         sorted(s["checklist"] for s in b.sections.values() if s["checklist"]))

    def test_judge_reads_graph(self):   # 판정 node가 쓰는 지식 = 그래프에서 만든 지식
        self.assertEqual(judge.manual(), G.knowledge_from_graph())

    def test_pack_opens_no_manual_file(self):   # D-543: 판정 길에서 연 파일 = 그래프 release 하나(매뉴얼 파일 0)
        opened = []
        sys.addaudithook(lambda ev, args: opened.append(str(args[0])) if ev == "open" and isinstance(args[0], (str, Path)) else None)
        judge.manual.cache_clear()
        G.release.cache_clear()
        start = len(opened)
        req = next(r for _, r in requests(ROOT) if r["kind"] == "start")
        judge.start(req, G.query(req, judge.history_query(req)["history_query"])["history_hits"])
        files = {Path(p).name for p in opened[start:] if Path(p).suffix in (".json", ".md", ".csv", ".jsonl")}
        self.assertIn("current.json", files)
        self.assertFalse({"manual_sections.json", "MG-MM.md"} & files, sorted(files))

    def test_section_missing_in_graph_refused(self):   # 실패 쪽: 관계가 가리키는 점검 절이 그래프 절 노드에 없으면 판정 지식을 만들지 않음(매뉴얼 파일로 메우기 0)
        G.release.cache_clear()
        try:
            rel = G.release()
            gone = rel["indicates"][0]["app_no"]
            rel["sections"] = [s for s in rel["sections"] if s["app_no"] != gone]
            with self.assertRaises(KeyError):
                G.knowledge_from_graph()
        finally:
            G.release.cache_clear()

    def test_release_integrity(self):
        text = G.CURRENT.read_text(encoding="utf-8")
        body = json.loads(text)
        rid = body.pop("release_id")
        self.assertEqual(rid, f"graph-{body['layer']}-{hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]}")
        self.assertNotIn("/Users/", text)


class Query(unittest.TestCase):
    def _rules(self, rows):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "rules.csv"
        p.write_text(HEAD + "".join(r + "\n" for r in rows), encoding="utf-8")
        return p

    def test_hits_shape_and_order(self):
        hits = G.query({}, _hq())["history_hits"]
        self.assertEqual(len(hits), 10)
        self.assertEqual([h["score"] for h in hits], sorted((h["score"] for h in hits), reverse=True))
        self.assertTrue(all(h["locator"]["record_id"] and h["index_release"] == G.release()["release_id"] for h in hits))
        self.assertTrue(all(r["group"] == "CL" for r in G.release()["records"] if r["id"] in {h["locator"]["record_id"] for h in hits}))

    def test_options_three_only(self):
        with self.assertRaises(ValueError):
            G.query({}, _hq(), {"scope": "group", "top_k": 5})
        with self.assertRaises(ValueError):
            G.query({}, _hq(), {"scope": "machine"})

    def test_min_support_falls_back_to_empty(self):   # 근거가 모자라면 고른 기록 0 → 판정은 매뉴얼 순서
        self.assertEqual(G.query({}, _hq(), {"min_support": 10_000})["history_hits"], [])

    def test_rule_row_narrows_evidence(self):   # 규칙 표 한 줄 — 알람 조건이 참이고 근거 1등이 then이면 그 근거로 좁힘
        hq = _hq()
        v = hq["sensors"]["rotational_speed_rpm"]
        ev = [r for r in G.release()["records"] if r["group"] == "CL" and r["sensors"]["rotational_speed_rpm"] >= v]
        top = max(sorted({r["cause"] for r in ev}), key=lambda c: sum(1 for r in ev if r["cause"] == c))
        hits = G.query({}, hq, rules_path=self._rules([f"R1,시험,self,rotational_speed_rpm>={v},{top},시험"]))["history_hits"]
        self.assertTrue(hits and all(h["graph"]["used"] == "R1" for h in hits))
        ids = {r["id"] for r in ev}
        self.assertTrue(all(h["locator"]["record_id"] in ids for h in hits))

    def test_knowhow_before_rules_and_low_weight_skipped(self):
        rel = G.release()
        hq = _hq()
        v = hq["sensors"]["rotational_speed_rpm"]
        ev = [r for r in rel["records"] if r["group"] == "CL" and r["sensors"]["rotational_speed_rpm"] >= v]
        top = max(sorted({r["cause"] for r in ev}), key=lambda c: sum(1 for r in ev if r["cause"] == c))
        rules = self._rules([f"R1,시험,self,rotational_speed_rpm>={v},분포,시험"])
        kh = {"knowhow_id": "t1", "rel": "self", "where": f"rotational_speed_rpm>={v}", "then": top, "weight": "normal"}
        G.release.cache_clear()
        try:
            G.release()["knowhow"] = [kh]
            self.assertEqual(G.query({}, hq, rules_path=rules)["history_hits"][0]["graph"]["used"], "KH-t1")
            G.release()["knowhow"] = [{**kh, "weight": "low"}]
            self.assertEqual(G.query({}, hq, rules_path=rules)["history_hits"][0]["graph"]["used"], "R1")
        finally:
            G.release.cache_clear()

    def test_bad_rules_refused(self):   # 실패 쪽
        for row in ("R1,x,machine_work,kind=1,CL-1,x", "R1,x,self,unknown_field>=1,CL-1,x", "R1,x,self,torque_nm>=1,ZZ-9,x"):
            with self.assertRaises(G.RuleError, msg=row.split(",")[2]):
                G.query({}, _hq(), rules_path=self._rules([row]))
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "rules.csv"
        p.write_text("id,rel\n", encoding="utf-8")
        with self.assertRaises(G.RuleError):
            G.query({}, _hq(), rules_path=p)

    def test_committed_rules_table_is_header_only(self):   # 지금 규칙 표는 머리만(예외 줄 0) — 노하우 · 예외는 시험 · 승인 뒤
        self.assertEqual((G.HERE / "rules.csv").read_text(encoding="utf-8"), HEAD)


class Order(unittest.TestCase):   # 사람 손 길: T1 start 요청(입력만) → 그래프 질의 node 상위 10건 → 첫 판정 node
    def setUp(self):
        self.reqs = [r for _, r in requests(ROOT) if r["kind"] == "start"]

    def _start(self, req, options=None):
        hits = G.query(req, judge.history_query(req)["history_query"])["history_hits"]
        return judge.start(req, hits, None, options)["result"]

    def test_default_is_similar(self):   # 코드 기본(옵션 없음) = similar = 지금 출력
        for req in self.reqs[:10]:
            self.assertEqual(self._start(req), self._start(req, {"order_signal": "similar"}))

    def test_evidence_follows_graph_counts(self):   # 실제 꼴: 미리보기 확률 차례 = 그래프 근거 건수 차례 · 근거 n · 근거 적음 · similar 그대로
        changed = 0
        for req in self.reqs:
            a, b = self._start(req), self._start(req, {"order_signal": "evidence"})
            self.assertEqual(a["similar"], b["similar"])
            for d in ("decision", "preview"):
                self.assertEqual((a[d]["evidence"], a[d]["low_evidence"]), (b[d]["evidence"], b[d]["low_evidence"]))
            ev, p = G.cause_evidence(req["group"]), b["preview"]["probabilities"]
            if sum(1 for s in a["similar"] if s["cause"] in p):
                self.assertEqual(sorted(p, key=lambda c: (-p[c], c)), sorted(p, key=lambda c: (-ev.get(c, 0), -p[c], c)), req["group"])
            changed += a["preview"] != b["preview"]
        self.assertGreater(changed, 0)

    def test_evidence_ignores_which_records(self):   # 비슷한 10건의 원인을 바꿔도(몫 크기 같음) evidence 확률은 그대로
        req = self.reqs[0]
        hits = G.query(req, judge.history_query(req)["history_query"])["history_hits"]
        causes = [h["metadata"]["cause"] for h in hits]
        moved = [{**h, "metadata": {"cause": causes[(i + 1) % len(causes)]}} for i, h in enumerate(hits)]
        o = {"order_signal": "evidence"}
        self.assertEqual(judge.start(req, hits, None, o)["result"]["preview"]["probabilities"], judge.start(req, moved, None, o)["result"]["preview"]["probabilities"])

    def test_no_records_falls_back_to_manual_order(self):   # 근거 0이면 두 신호 모두 α만(매뉴얼 순서)
        req = self.reqs[0]
        self.assertEqual(judge.start(req, [], None, {"order_signal": "evidence"}), judge.start(req, [], None))

    def test_count_ratio_is_scope_counts(self):   # 정본 확률(D-543): 확률 = 범위 안 원인별 개수 비율(비슷한 10건 몫 0) — 실제 T1 start 요청
        for req in self.reqs:
            p = self._start(req, {"order_signal": "count_group"})["preview"]["probabilities"]
            n = G.scope_counts(req["group"])
            self.assertEqual(p, rules.round_to_100({c: n.get(c, 0) / sum(n.get(x, 0) for x in p) + judge.NO_ZERO for c in p}), req["group"])
            self.assertEqual(sorted(p, key=lambda c: (-p[c], c)), sorted(p, key=lambda c: (-n.get(c, 0), -p[c], c)))

    def test_count_equipment_narrows_or_falls_back(self):   # 같은 설비 기록이 최소 근거 수 이상이면 좁힘 · 모자라거나 칸이 없으면 같은 증상군
        recs = G.release()["records"]
        eq = max(sorted({r["equipment"] for r in recs if r["group"] == "CL"}), key=lambda e: sum(1 for r in recs if r["group"] == "CL" and r["equipment"] == e))
        self.assertEqual(sum(G.scope_counts("CL", eq).values()), sum(1 for r in recs if r["group"] == "CL" and r["equipment"] == eq))
        self.assertEqual(G.scope_counts("CL", "M-ZZ"), G.scope_counts("CL"))
        req = next(r for r in self.reqs if r["group"] == "CL")
        e, g = self._start(req, {"order_signal": "count_equipment"}), self._start(req, {"order_signal": "count_group"})   # T1 요청엔 설비 칸 0 → 같은 증상군 범위
        self.assertEqual((e["preview"]["record_counts"]["scope"], {k: e["preview"][k] for k in ("kind", "section")}), ("group", {k: g["preview"][k] for k in ("kind", "section")}))   # D-642: 기본 1건만큼 %는 다를 수 있음
        a = self._start({**req, "equipment_id": eq}, {"order_signal": "count_equipment"})["preview"]["probabilities"]
        n = G.scope_counts("CL", eq)
        self.assertEqual(sorted(a, key=lambda c: (-a[c], c)), sorted(a, key=lambda c: (-n.get(c, 0), -a[c], c)))

    def test_count_equipment_prior_one(self):   # D-642 A(사용자 R3): 이 설비 기록 셈에 원인마다 기본 1건(작은 표본 100% 막기) · 기록 n · k를 결과에 실음 · 실패 쪽: count_group은 기본 0
        recs = G.release()["records"]
        eqs = sorted({r["equipment"] for r in recs if r["group"] == "CL"})
        eq = max(eqs, key=lambda e: sum(1 for r in recs if r["group"] == "CL" and r["equipment"] == e))
        req = {**next(r for r in self.reqs if r["group"] == "CL"), "equipment_id": eq}
        out = self._start(req, {"order_signal": "count_equipment"})
        p, rc = out["preview"]["probabilities"], out["preview"]["record_counts"]
        n = G.scope_counts("CL", eq)
        self.assertEqual(rc, {"scope": "equipment", "total": sum(n.values()), "by_cause": {c: n.get(c, 0) for c in p}, "prior": 1})
        self.assertEqual(p, rules.round_to_100({c: (n.get(c, 0) + 1) / (sum(n.values()) + len(p)) + judge.NO_ZERO for c in p}))
        self.assertTrue(all(v > 0 for v in p.values()))   # 기록 0인 원인도 0%가 아님
        self.assertEqual(out["decision"]["record_counts"], rc)
        g = self._start(req, {"order_signal": "count_group"})["preview"]["record_counts"]
        self.assertEqual((g["scope"], g["prior"]), ("group", 0))
        self.assertNotIn("record_counts", self._start(req, {"order_signal": "similar"})["preview"])
        few = self._start({**req, "equipment_id": "M-ZZ"}, {"order_signal": "count_equipment"})["preview"]["record_counts"]
        self.assertEqual((few["scope"], few["prior"]), ("group", 1))   # 그 설비 기록이 모자라면 같은 증상군으로 넓힘(기본 1건은 그대로)

    def test_count_with_no_earlier_records_is_manual_order(self):   # 실패 쪽: 알람 시각 앞선 기록 0 → 나눗셈 0 없이 매뉴얼 순서(모든 원인 같은 확률)
        req = {**self.reqs[0], "occurred_at": "2000-01-01T00:00:00+09:00"}
        self.assertEqual(G.scope_counts(req["group"], None, req["occurred_at"]), {})
        p = self._start(req, {"order_signal": "count_group"})["preview"]["probabilities"]
        self.assertLessEqual(max(p.values()) - min(p.values()), 1)

    def test_unknown_signal_refused(self):   # 실패 쪽
        with self.assertRaises(ValueError):
            self._start(self.reqs[0], {"order_signal": "vote"})


class Context(unittest.TestCase):   # 약속 v7 선택 칸 셋(D-543 덧 3) — 설비 · 알람 시각 · 라인
    CTX = json.loads((ROOT / "contracts/v7/examples/start-context.request.json").read_text(encoding="utf-8"))

    def _hq(self, **kw):
        req = {**self.CTX, **kw}
        return judge.history_query(req)["history_query"]

    def test_query_carries_fields_only_when_present(self):   # 칸 없는 요청 = v6과 같은 질의
        base = {k: v for k, v in self.CTX.items() if k not in ("equipment_id", "occurred_at", "line_id")}
        self.assertEqual(set(judge.history_query(base)["history_query"]), {"text", "group", "sensors"})
        self.assertEqual({k: self._hq()[k] for k in ("equipment_id", "occurred_at", "line_id")},
                         {k: self.CTX[k] for k in ("equipment_id", "occurred_at", "line_id")})

    def test_equipment_scope(self):   # 같은 증상군의 같은 설비 기록만 · 설비 칸이 없으면 같은 증상군(쓴 범위를 조각에 남김)
        recs = {r["id"]: r for r in G.release()["records"]}
        eq = max(sorted({r["equipment"] for r in recs.values() if r["group"] == "CL"}), key=lambda e: sum(1 for r in recs.values() if r["group"] == "CL" and r["equipment"] == e))
        hits = G.query({}, self._hq(equipment_id=eq, occurred_at="2027-01-01T00:00:00+09:00"), {"scope": "equipment"})["history_hits"]
        self.assertTrue(hits)
        self.assertTrue(all(recs[h["locator"]["record_id"]]["equipment"] == eq and recs[h["locator"]["record_id"]]["group"] == "CL" and h["graph"]["scope"] == "equipment" for h in hits))
        hq = {k: v for k, v in self._hq().items() if k != "equipment_id"}
        a, b = G.query({}, hq, {"scope": "equipment"})["history_hits"], G.query({}, hq, {"scope": "group"})["history_hits"]
        self.assertEqual(a, b)
        self.assertEqual({h["graph"]["scope"] for h in a}, {"group"})

    def test_unknown_equipment_gives_no_records(self):   # 실패 쪽: 그 설비 기록이 최소 근거 수보다 적으면 고른 기록 0 → 판정은 매뉴얼 순서
        self.assertEqual(G.query({}, self._hq(equipment_id="M-ZZ"), {"scope": "equipment"})["history_hits"], [])

    def test_occurred_at_uses_only_earlier_records(self):   # 알람 시각 앞선 기록만 · 마지막 기록 뒤 시각이면(무게 0) 칸 없을 때와 같음
        recs = {r["id"]: r for r in G.release()["records"]}
        mid = sorted(r["ts"] for r in recs.values())[len(recs) // 2]
        hits = G.query({}, self._hq(occurred_at=mid))["history_hits"]
        self.assertTrue(hits and all(G._ts(recs[h["locator"]["record_id"]]["ts"]) < G._ts(mid) for h in hits))
        late = {k: v for k, v in self._hq(occurred_at="2027-01-01T00:00:00+09:00").items() if k != "equipment_id"}
        none = {k: v for k, v in late.items() if k != "occurred_at"}
        self.assertEqual(G.query({}, late)["history_hits"], G.query({}, none)["history_hits"])
        self.assertEqual(G._ts("2026-06-01T09:00:00+09:00"), G._ts("2026-06-01T00:00:00+00:00"))   # 시간대가 달라도 같은 순간

    def test_ts_forms(self):   # D-585(앱 zzz93): 소수 초 · 시간대 꼴이 달라도 같은 순간 — 고치기 전엔 소수 초 자리를 시간대로 읽어 1.48일 · 3.30일로 갈림 · Z는 오류
        want = G._ts("2026-06-01T00:00:00+00:00")
        for t in ("2026-06-01T09:00:00.123456+09:00", "2026-06-01T09:00:00.5+09:00", "2026-05-31T19:00:00.999-05:00", "2026-06-01T00:00:00Z", "2026-06-01T00:00:00.25Z",
                  "2026-06-01T09:00:00+0900", "2026-06-01T09:00:00+09", "2026-06-01T00:00:00", "2026-06-01T00:00:00.75"):
            self.assertEqual(G._ts(t), want, t)
        self.assertEqual(G._ts("2026-06-01T09:30:00+09:30"), want)

    def test_recent_fraction_seconds(self):   # D-585: 최근 정비 며칠 전 · 창 안팎 대조가 소수 초에 흔들리지 않음 · 실패 쪽: 7일 밖은 소수 초여도 안 맞음
        x = {"step": "EX-R-1", "records": [], "when": {"group": "CL", "recent": {"section": "4.3.2", "days": 7}}}
        at = "2026-11-03T21:15:00+09:00"
        m = lambda t: G.exception.matched({}, x, "M-01", at, [{"record_id": "H-1", "at": t, "sections": ["4.3.2"]}]).get("recent")
        self.assertEqual(m("2026-10-31T21:15:00.123456+09:00")["days_ago"], m("2026-10-31T21:15:00+09:00")["days_ago"])
        self.assertEqual(m("2026-10-31T21:15:00.123456+09:00")["days_ago"], 3.0)
        self.assertIsNone(m("2026-10-26T21:15:00.123456+09:00"))

    def test_start_with_context_matches_result_schema(self):   # 실제 꼴: v7 예시 요청 → 그래프 질의(같은 설비) → 첫 판정 · 결과 schema(v7 = v6 바이트)
        import jsonschema
        res = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v7/run_result.json").read_text(encoding="utf-8")))
        out = judge.start(self.CTX, G.query(self.CTX, self._hq(), {"scope": "equipment"})["history_hits"])
        self.assertEqual([e.message for e in res.iter_errors(out["result"])], [])


class Exception_(unittest.TestCase):   # D-545 고침 3: 예외 관계(exception_relations) → 예외 안내 갈래 · 사본 session 손 0
    CTX = json.loads((ROOT / "contracts/v7/examples/start-context.request.json").read_text(encoding="utf-8"))
    SID = "EX-R-9001"

    def _rel(self, **when):
        rel = copy.deepcopy(G.release())
        rel["sections"].append({"no": self.SID, "app_no": self.SID, "kind": "예외", "grade": "C", "name": "필터 덮개 고정 나사 확인", "guide": "덮개 나사를 확인하고 빠졌으면 새로 끼운다",
                                "steps": ["덮개 나사 확인"], "criteria": "나사 네 개 체결 = 정상", "group": "온도 차 저하", "minutes": 5, "checklist": [], "records": ["R-9001"]})
        rel["exception_relations"] = [{"id": self.SID, "step": self.SID, "mode": "안내", "grade": "C", "cause": "CL-1", "cause_name": "냉각 필터 막힘", "records": ["R-9001"], "n": 1,
                                       "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2]], **when}}]
        return rel

    def _with(self, rel):
        real, cur = G._read, str(G.CURRENT.resolve())
        judge.manual.cache_clear()
        G.release.cache_clear()
        self.addCleanup(lambda: (judge.manual.cache_clear(), G.release.cache_clear()))
        return mock.patch.object(G, "_read", lambda p: rel if p == cur else real(p))

    def _start(self, req=None):
        req = req or self.CTX
        return judge.start(req, G.query(req, judge.history_query(req)["history_query"])["history_hits"])["result"]

    def _judge(self, *events, rel_id=SID):
        req = {"kind": "judge", "session_id": "S-1", "group": "CL", "similar": self.sim, "grades": self.CTX["grades"], "minutes": self.CTX["minutes"],
               "events": [{"seq": i + 1, **e} for i, e in enumerate(events)], "exception_relation": rel_id}
        return judge.judge(req)["result"]["decision"]

    def setUp(self):
        self.base = self._start()
        self.sim = self.base["similar"]

    def test_no_relation_no_guide(self):   # 예외 줄이 없는 그래프 = 지금 출력(결과 칸 · 판정 그대로)
        self.assertNotIn("exception_guide", self.base)

    def test_matching_alarm_gets_guide_first_after_lock(self):   # 사람 손 길: 알람 맞음 → 결과 exception_guide · 잠금 먼저(안전 규칙) · D-580: 미리보기 첫 추천 = 매뉴얼 그대로 · C 예외는 후보 목록
        with self._with(self._rel()):
            r = self._start()
        g = r["exception_guide"]
        self.assertEqual((g["section"], g["relation_id"], g["grade"], g["basis"], g["steps"], g["minutes"]), (self.SID, self.SID, "C", ["R-9001"], ["덮개 나사 확인"], 5))
        self.assertEqual(g["next_section"], self.base["preview"]["section"])   # 예외 절이 없었을 때의 다음 점검
        self.assertEqual(r["decision"]["kind"], "lock")
        self.assertEqual((r["preview"]["kind"], r["preview"]["section"]), (self.base["preview"]["kind"], self.base["preview"]["section"]))
        self.assertIn(self.SID, [a["section"] for a in r["preview"]["reason_facts"]["alternatives"]])
        self.assertEqual(r["preview"]["probabilities"], self.base["preview"]["probabilities"])

    def test_judge_flow(self):   # 예외로 바꿈 → 승인 → 지시 · 결과 정상 → 원래 순서 다음 · 이상 → 조치 → 재가동 → 재가동 값 → 보고서(원인 = 관계 원인)
        lock = [{"type": "lock"}, {"type": "precheck_done"}]
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        into = {"type": "switch_check", "section": self.SID, "from_section": "4.3"}
        with self._with(self._rel()):
            self.assertEqual(self._judge(*lock, into)["kind"], "call")
            self.assertEqual(self._judge(*lock, {"type": "call_sent", "kind": "approval", "section": self.SID})["call_status"], "sent")
            self.assertEqual(self._judge(*lock, ok)["kind"], "instruct")
            normal = self._judge(*lock, ok, {"type": "result", "section": self.SID, "value": "정상"})
            bad = [*lock, ok, {"type": "result", "section": self.SID, "value": "이상"}]
            self.assertEqual(self._judge(*bad)["kind"], "action")
            done = [*bad, {"type": "action_done", "section": self.SID}]
            self.assertEqual(self._judge(*done)["kind"], "restart_check")
            self.assertEqual(self._judge(*done, {"type": "restart", "section": "6.1"})["kind"], "restart_value")
            rep = self._judge(*done, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "inside"})
            self.assertEqual((rep["kind"], rep["cause"], rep["outcome"], rep["relation"]), ("report", "CL-1", "confirmed", self.SID))
            self.assertEqual(rep["graph"], {"used": self.SID, "layer": G.release()["layer"]})   # 실행 기록 graph.used · layer(LLMOps ① · deploy K6)
            self.assertEqual(self._judge(*lock, into)["graph"]["used"], self.SID)
        self.assertNotIn("graph", normal)   # 정상이면 원래 판정 그대로(예외 칸 0)
        self.assertEqual(normal, self._judge(*lock, rel_id=None))   # 정상이면 예외가 없던 것과 같은 판정

    def _rel_lists(self):   # v10(D-566): 점검 둘 · 조치 둘인 예외 하나
        rel = self._rel()
        rel["sections"][-1].update({"topic": "필터 덮개 나사",
                                    "checks": [{"what": "필터 덮개 고정 나사", "how": "손으로 돌려 봄", "criteria": "모두 체결 = 정상", "minutes": 5},
                                               {"what": "냉각수 수위", "how": "눈금을 봄", "criteria": "", "minutes": 3}],
                                    "actions": [{"what": "풀린 나사 다시 조임", "parts": [], "minutes": 5}, {"what": "냉각수 보충", "parts": [], "minutes": 10}]})
        return rel

    def test_walk_steps(self):   # D-566 덧 1 사람 손 길: 승인 한 번 → c1 → c2(남은 점검은 봄) → 이상이 있으면 a1 → a2 → 재가동 → 재가동 값 → 보고서 · 반려 뒤 다시 보고서
        lock = [{"type": "lock"}, {"type": "precheck_done"}]
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        r = lambda s, v: {"type": "result", "section": self.SID, "step": s, "value": v}
        done = lambda s: {"type": "action_done", "section": self.SID, "step": s}
        with self._with(self._rel_lists()):
            first = self._judge(*lock, {"type": "switch_check", "section": self.SID, "from_section": "4.3"})
            self.assertEqual((first["kind"], first["section"], first["steps_total"]), ("call", self.SID, 4))   # C → 예외 전체에 한 번
            c1 = self._judge(*lock, ok)
            self.assertEqual((c1["kind"], c1["step"], c1["step_index"], c1["check"]["what"]), ("instruct", "c1", 1, "필터 덮개 고정 나사"))
            c2 = self._judge(*lock, ok, r("c1", "정상"))   # 정상이어도 다음 걸음(원래 순서로 돌아가지 않음)
            self.assertEqual((c2["kind"], c2["step"], c2["step_index"]), ("instruct", "c2", 2))
            a1 = self._judge(*lock, ok, r("c1", "정상"), r("c2", "이상"))
            self.assertEqual((a1["kind"], a1["step"], a1["action"]["what"], a1["stage"]), ("action", "a1", "풀린 나사 다시 조임", "action"))
            self.assertNotIn("check", a1)
            walk = [*lock, ok, r("c1", "정상"), r("c2", "이상"), done("a1")]
            a2 = self._judge(*walk)
            self.assertEqual((a2["kind"], a2["step"], a2["step_index"], a2["steps_total"]), ("action", "a2", 4, 4))
            end = [*walk, done("a2")]
            self.assertEqual(self._judge(*end)["kind"], "restart_check")   # 승인 다시 묻지 않음(한 번)
            self.assertEqual(self._judge(*end, {"type": "restart", "section": "6.1"})["kind"], "restart_value")
            rest = [*end, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "inside"}]
            rep = self._judge(*rest)
            self.assertEqual((rep["kind"], rep["cause"], rep["outcome"], rep["relation"]), ("report", "CL-1", "confirmed", self.SID))
            self.assertEqual(self._judge(*rest, {"type": "report_confirm"})["kind"], "closed")
            self.assertEqual(self._judge(*rest, {"type": "report_confirm"}, {"type": "report_rejected"})["kind"], "report")   # 반려 뒤 다시 열림(결함 고침)
            self.assertEqual(self._judge(*rest, {"type": "report_confirm"}, {"type": "report_rejected"}, {"type": "report_confirm"})["kind"], "closed")
            skip = self._judge(*lock, ok, r("c2", "정상"))   # c1을 안 하고 c2만 보내도 c1부터(차례 그대로)
            self.assertEqual(skip["step"], "c1")
            self.assertEqual(self._judge({"type": "lock"})["kind"], "precheck")   # 안전 먼저 그대로
            self.assertEqual(self._judge(*lock, ok, {"type": "result", "section": self.SID, "value": "정상"})["step"], "c1")   # step 없는 결과는 걸음으로 안 셈

    def test_walk_restart_outside_back_to_order(self):   # D-566 덧 2: 예외 안내를 마친 뒤 재가동 확인 → 정상이면 보고서 · 아니면 원래 점검 순서(4.3 …) · 그 뒤는 보통 세션
        lock = [{"type": "lock"}, {"type": "precheck_done"}]
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        steps = [{"type": "result", "section": self.SID, "step": "c1", "value": "정상"}, {"type": "result", "section": self.SID, "step": "c2", "value": "이상"},
                 {"type": "action_done", "section": self.SID, "step": "a1"}, {"type": "action_done", "section": self.SID, "step": "a2"}]
        out = [*lock, ok, *steps, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "outside"}]
        with self._with(self._rel_lists()):
            back = self._judge(*out)
            self.assertEqual((back["kind"], back["section"]), ("instruct", self._judge(*lock, rel_id=None)["section"]))   # 원래 순서 첫 점검(4.3)
            self.assertFalse({"graph", "relation", "step"} & set(back))   # 예외 칸 0
            self.assertEqual(back, self._judge(*lock, rel_id=None))   # 예외 · 그 재가동 둘을 뺀 판정과 같음
            nxt = [*out, {"type": "result", "section": back["section"], "value": "이상"}]
            self.assertEqual(self._judge(*nxt), self._judge(*lock, {"type": "result", "section": back["section"], "value": "이상"}, rel_id=None))   # 그 뒤는 보통 세션
            inside = self._judge(*lock, ok, *steps, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "inside"})
            self.assertEqual((inside["kind"], inside["relation"], inside["outcome"]), ("report", self.SID, "confirmed"))   # 정상이면 예외 보고서
        with self._with(self._rel()):   # 목록 없는 옛 절은 옛 길(재가동 밖 = 인계)
            old = [*lock, ok, {"type": "result", "section": self.SID, "value": "이상"}, {"type": "action_done", "section": self.SID},
                   {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "outside"}]
            self.assertEqual(self._judge(*old)["kind"], "handover")

    def test_walk_request_shape(self):   # 약속 v10 사건 step 칸 · 실패 쪽: 꼴 밖 step 거절 · v9는 step을 모름
        import jsonschema
        v9, v10 = (jsonschema.Draft202012Validator(json.loads((ROOT / f"contracts/{v}/run_request.json").read_text(encoding="utf-8"))) for v in ("v9", "v10"))
        ev = lambda step: {"kind": "judge", "session_id": "S-1", "group": "CL", "similar": [], "grades": {}, "minutes": {}, "exception_relation": self.SID,
                           "events": [{"seq": 1, "type": "result", "section": self.SID, "step": step, "value": "정상"}]}
        self.assertTrue(v10.is_valid(ev("c1")))
        self.assertTrue(v10.is_valid(ev("a12")))
        for bad in ("x1", "c", "c123", "C1"):
            self.assertFalse(v10.is_valid(ev(bad)), bad)
        self.assertFalse(v9.is_valid(ev("c1")))

    def test_old_section_keeps_old_flow(self):   # 목록 없는 옛 예외 절(check · action 칸 0) = 옛 길 그대로(정상이면 원래 순서) · 옛 하나 꼴(check · action)은 목록 하나로 걸음 길
        lock = [{"type": "lock"}, {"type": "precheck_done"}]
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        with self._with(self._rel()):
            self.assertNotIn("step", self._judge(*lock, ok))
        rel = self._rel()
        rel["sections"][-1].update({"check": {"what": "필터 덮개 나사", "how": "", "criteria": "", "minutes": 5}, "action": {"what": "다시 조임", "parts": [], "minutes": 5}})
        with self._with(rel):
            d = self._judge(*lock, ok)
            self.assertEqual((d["step"], d["steps_total"]), ("c1", 2))

    def test_conditions(self):   # 센서 구간 밖 · 같은 설비 조건인데 요청 설비 다름/없음 · mode 참고 → 안내 없음
        for rel in (self._rel(sensors=[["temp_diff_k", ">=", 20]]), self._rel(equipment_id="M-99")):
            with self._with(rel):
                self.assertNotIn("exception_guide", self._start())
        with self._with(self._rel(equipment_id=self.CTX["equipment_id"])):
            self.assertIn("exception_guide", self._start())
            self.assertNotIn("exception_guide", self._start({k: v for k, v in self.CTX.items() if k != "equipment_id"}))
        rel = self._rel()
        rel["exception_relations"][0]["mode"] = "참고"
        with self._with(rel):
            self.assertNotIn("exception_guide", self._start())

    def test_guide_fields(self):   # D-554 ④ · v10(D-566): 예외 절 칸 → exception_guide에 checks · actions 목록 · topic · note · 옛 하나 꼴(check · action)도 목록 하나로 · 약속 v10 결과 꼴 · 칸 없으면 칸 0
        import jsonschema
        res = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v10/run_result.json").read_text(encoding="utf-8")))
        c1 = {"what": "필터 덮개 나사", "how": "덮개 나사 네 개를 손으로 돌려 본다", "criteria": "네 개 모두 체결 = 정상", "minutes": 5}
        c2 = {"what": "냉각수 수위", "how": "탱크 눈금을 본다", "criteria": "아래 선 밑 = 이상", "minutes": 3}
        a1 = {"what": "필터 교체", "parts": ["냉각 필터 F-200"], "minutes": 10}
        for fields, want in (({"topic": "필터 덮개 나사", "checks": [c1, c2], "actions": [a1], "note": "야간 근무 때 두 번 봄"}, {"topic": "필터 덮개 나사", "checks": [c1, c2], "actions": [a1], "note": "야간 근무 때 두 번 봄"}),
                             ({"check": c1, "action": a1}, {"checks": [c1], "actions": [a1]})):   # 옛 하나 꼴 → 목록 하나
            rel = self._rel()
            rel["sections"][-1].update(fields)
            with self._with(rel):
                out = self._start()
            g = out["exception_guide"]
            self.assertEqual({k: g[k] for k in want}, want)
            self.assertNotIn("check", g)
            self.assertEqual([e.message for e in res.iter_errors(out)], [])
        with self._with(self._rel()):
            plain = self._start()
        self.assertFalse({"checks", "actions", "topic", "note"} & set(plain["exception_guide"]))   # 칸 없는 옛 줄 = 옛 출력
        self.assertEqual([e.message for e in res.iter_errors(plain)], [])
        for bad in ({**g, "checks": [{**c1, "why": "x"}]}, {**g, "checks": [{**c1, "minutes": "5분"}]}, {**g, "actions": [{**a1, "parts": "F-200"}]}, {**g, "checks": []}, {**g, "check": c1}):
            self.assertFalse(res.is_valid({**out, "exception_guide": bad}))   # 실패 쪽: 모르는 칸 · 분 꼴 · 부품 꼴 · 빈 목록 · v9 하나 꼴

    def test_guide_empty_criteria(self):   # D-556 덧 12: 기준 · 방법이 빈 예외 절 → checks[i] criteria · how = "" · 안내 기준 줄 None · v10 꼴 통과(v8은 1자 이상)
        import jsonschema
        v8, v10 = (jsonschema.Draft202012Validator(json.loads((ROOT / f"contracts/{v}/run_result.json").read_text(encoding="utf-8"))) for v in ("v8", "v10"))
        rel = self._rel()
        rel["sections"][-1].update({"criteria": "", "checks": [{"what": "구동 벨트", "how": "", "criteria": "", "minutes": 5}], "actions": [{"what": "벨트 장력 조정", "parts": [], "minutes": 5}]})
        with self._with(rel):
            out = self._start()
        g = out["exception_guide"]
        self.assertEqual((g["checks"][0]["criteria"], g["checks"][0]["how"], g["criteria"]), ("", "", None))
        self.assertEqual([e.message for e in v10.iter_errors(out)], [])
        self.assertFalse(v8.is_valid(out))
        self.assertFalse(v10.is_valid({**out, "exception_guide": {**g, "checks": [{k: v for k, v in g["checks"][0].items() if k != "criteria"}]}}))   # 실패 쪽: 기준 칸 없음

    def test_overlap_narrow_first(self):   # D-546 덧 15(eval 127 꼴): 설비 조건 없는 넓은 줄(n 큼)과 설비 조건 붙은 좁은 줄이 함께 맞으면 좁은 줄 — 고치기 전엔 넓은 줄이 나왔음
        rel = self._rel()
        wide = rel["exception_relations"][0]
        wide["n"] = 3
        rel["sections"].append({**rel["sections"][-1], "no": "EX-R-9005", "app_no": "EX-R-9005", "records": ["R-9005"]})
        rel["exception_relations"].append({**wide, "id": "EX-R-9005", "step": "EX-R-9005", "n": 1, "records": ["R-9005"],
                                           "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.0]], "equipment_id": self.CTX["equipment_id"]}})
        with self._with(rel):
            self.assertEqual(self._start()["exception_guide"]["relation_id"], "EX-R-9005")
            self.assertEqual(self._start({k: v for k, v in self.CTX.items() if k != "equipment_id"})["exception_guide"]["relation_id"], self.SID)   # 설비 칸 없으면 넓은 줄만 맞음
        rel["exception_relations"][1]["when"].pop("equipment_id")   # 설비 조건이 둘 다 없으면 센서 줄 수 → n 차례
        rel["exception_relations"][1]["when"]["sensors"].append(["torque_nm", "<=", 100])
        with self._with(rel):
            self.assertEqual(self._start()["exception_guide"]["relation_id"], "EX-R-9005")

    def test_overlap_tie_later_first(self):   # D-546 덧 36(F1 걷기 EX-R-0001 · 0008 꼴): 설비 조건 · 센서 줄 수 · n이 같으면 나중 관계(id 큰 것) 먼저 — 고치기 전엔 id 작은 앞 줄이 나왔음
        rel = self._rel()
        rel["sections"].append({**rel["sections"][-1], "no": "EX-R-9008", "app_no": "EX-R-9008", "records": ["R-9008"]})
        rel["exception_relations"].append({**rel["exception_relations"][0], "id": "EX-R-9008", "step": "EX-R-9008", "records": ["R-9008"],
                                           "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.1]]}})
        with self._with(rel):
            self.assertEqual(self._start()["exception_guide"]["relation_id"], "EX-R-9008")
        rel["exception_relations"][0]["n"] = 2   # 앞 단계가 다르면 그 단계가 이김(n 큰 앞 줄)
        with self._with(rel):
            self.assertEqual(self._start()["exception_guide"]["relation_id"], self.SID)

    def _recent(self, *hist, **recent):   # D-570: 최근 정비 조건 관계(sensors 비움 · 설비 무관) + 알람 설비 이력 줄(시각 · 한 조치 · 점검)
        rel = self._rel(sensors=[], recent={"section": "4.3.2", "days": 7, **recent})
        for i, (ts, eq, did, checked) in enumerate(hist):
            rel["records"].append({"id": f"H-99{i:02d}", "group": "CL", "cause": "CL-1", "ts": ts, "equipment": eq, "sensors": dict(rel["records"][0]["sensors"]), "did": did, "checked": checked})
        return rel

    def _cand(self, grade="C", minutes=5, **when):   # D-580: 점검 둘 · 조치 하나인 예외 후보(등급 · 분을 바꿔 순위를 봄)
        rel = self._rel_lists() if not when else self._rel(**when)
        sec = next(s for s in rel["sections"] if s["no"] == self.SID)
        sec.update(grade=grade, minutes=minutes, checks=[{"what": "덮개 볼트", "how": "손으로 돌려 본다", "criteria": "", "minutes": 5}, {"what": "덮개 패킹", "how": "", "criteria": "", "minutes": 5}],
                   actions=[{"what": "볼트 다시 조임", "parts": [], "minutes": 5}], topic="덮개 볼트")
        return rel

    LOCK = ({"type": "lock"}, {"type": "precheck_done"})

    def test_candidate_c_stays_in_list(self):   # D-580 사용자: 적재 뒤엔 매뉴얼 후보처럼 — C는 자동 추천에서 빠지고 후보 목록에 뜸 · 첫 추천 = 매뉴얼 그대로
        plain = self._judge(*self.LOCK, rel_id=None)
        with self._with(self._cand()):
            r, d = self._start(), self._judge(*self.LOCK)
        self.assertEqual((r["preview"]["kind"], r["preview"]["section"]), (plain["kind"], plain["section"]))
        self.assertEqual((d["kind"], d["section"]), (plain["kind"], plain["section"]))
        alt = next(a for a in d["reason_facts"]["alternatives"] if a["section"] == self.SID)
        self.assertEqual((alt["grade"], alt["relation"], alt["title"]), ("C", self.SID, "덮개 볼트"))
        self.assertEqual([a for a in d["reason_facts"]["alternatives"] if a["section"] != self.SID], plain["reason_facts"]["alternatives"])

    def test_guide_carries_instruction(self):   # D-642 C: 그래프 예외 절 줄의 지시 문장(instruction)이 start exception_guide에 그대로 · 약속 v10 결과 통과 · 실패 쪽: 칸 없는 줄은 칸 0
        import jsonschema
        rel = self._cand()
        sec = next(s for s in rel["sections"] if s["no"] == self.SID)
        sec["checks"][0]["instruction"] = "덮개 볼트를 손으로 돌려 보고 풀려 있으면 이상입니다."
        sec["actions"][0]["instruction"] = "풀린 볼트를 다시 조입니다."
        with self._with(rel):
            r = self._start()
        g = r["exception_guide"]
        self.assertEqual((g["checks"][0]["instruction"], g["actions"][0]["instruction"]), (sec["checks"][0]["instruction"], sec["actions"][0]["instruction"]))
        self.assertNotIn("instruction", g["checks"][1])
        v = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v10/run_result.json").read_text(encoding="utf-8")))
        self.assertEqual([e.message for e in v.iter_errors(r)], [])

    def test_candidate_title_first_check(self):   # D-585: 이유 글에 내부 번호 대신 쓸 이름 = 예외 첫 점검 이름 · 점검 칸 없는 옛 줄 = 절 이름(실패 쪽)
        def title(rel):
            with self._with(rel):
                d = self._judge(*self.LOCK)
            return next(a for a in d["reason_facts"]["alternatives"] if a["section"] == self.SID)["title"]
        rel = self._cand()
        self.assertEqual(title(rel), "덮개 볼트")
        sec = next(s for s in rel["sections"] if s["no"] == self.SID)
        del sec["checks"], sec["actions"]
        self.assertEqual(title(rel), sec["name"])

    def test_candidate_a_ranks_by_same_score(self):   # 같은 순위 셈(확률 ÷ 분): 원인 빈 예외 = 1위 원인 확률 · 분이 짧으면 첫 추천(다른 후보는 목록에) · 분이 길면 매뉴얼이 첫 추천 · 예외는 후보
        plain = self._judge(*self.LOCK, rel_id=None)
        with self._with(self._cand(grade="A", minutes=1)):
            d = self._judge(*self.LOCK)
        self.assertEqual((d["kind"], d["section"], d["step"]), ("instruct", self.SID, "c1"))
        self.assertIn(plain["section"], [a["section"] for a in d["reason_facts"]["alternatives"]])
        with self._with(self._cand(grade="A", minutes=99)):
            d = self._judge(*self.LOCK)
        self.assertEqual(d["section"], plain["section"])
        self.assertIn(self.SID, [a["section"] for a in d["reason_facts"]["alternatives"]])

    def test_switch_in_and_out(self):   # 바꾸기: 매뉴얼 → 예외(C는 승인 한 번 → c1) · 예외 → 매뉴얼(그 점검 지시 · 예외는 다시 후보) · 실패 쪽: 없는 예외 절
        plain = self._judge(*self.LOCK, rel_id=None)
        into = {"type": "switch_check", "section": self.SID, "from_section": plain["section"]}
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        with self._with(self._cand()):
            self.assertEqual(self._judge(*self.LOCK, into)["kind"], "call")
            d = self._judge(*self.LOCK, into, ok)
            self.assertEqual((d["kind"], d["section"], d["step"]), ("instruct", self.SID, "c1"))
            self.assertIn(plain["section"], [a["section"] for a in d["reason_facts"]["alternatives"]])
            back = {"type": "switch_check", "section": "4.5", "from_section": self.SID}
            o = self._judge(*self.LOCK, into, ok, back)
            self.assertEqual((o["section"], o["switched"]), ("4.5", {"from": self.SID, "to": "4.5"}))
            with self.assertRaises(switch.SwitchError):
                self._judge(*self.LOCK, {"type": "switch_check", "section": "4.5"}, {"type": "switch_check", "section": "EX-없음", "from_section": "4.5"}, rel_id=None)

    def test_normal_goes_to_next_candidate(self):   # 사용자: 정상이면 조치로 안 감 → 다음 후보 · 이상이 하나라도 있으면 조치 · 실패 쪽: 확인 불가도 조치로 안 감
        plain = self._judge(*self.LOCK, rel_id=None)
        into = {"type": "switch_check", "section": self.SID, "from_section": plain["section"]}
        ok = {"type": "call_answer", "answer": "approved", "section": self.SID}
        res = lambda s, v: {"type": "result", "section": self.SID, "step": s, "value": v}
        with self._with(self._cand()):
            self.assertEqual(self._judge(*self.LOCK, into, ok, res("c1", "정상"))["step"], "c2")   # 남은 점검은 봄
            d = self._judge(*self.LOCK, into, ok, res("c1", "정상"), res("c2", "정상"))
            self.assertEqual((d["kind"], d["section"]), (plain["kind"], plain["section"]))
            self.assertNotIn(self.SID, [a["section"] for a in d["reason_facts"]["alternatives"]])   # 한 예외는 후보에서 빠짐
            self.assertEqual(self._judge(*self.LOCK, into, ok, res("c1", "확인 불가"), res("c2", "정상"))["section"], plain["section"])
            a = self._judge(*self.LOCK, into, ok, res("c1", "정상"), res("c2", "이상"))
            self.assertEqual((a["kind"], a["step"]), ("action", "a1"))

    def test_matched_basis(self):   # 근거: 어떤 조건이 맞았는지 칸(증상군 · 설비 · 며칠 전 · 절 · 근거 예외) · 센서 조건이면 센서 줄 · 약속 v10 결과 통과
        import jsonschema
        eq = self.CTX["equipment_id"]
        with self._with(self._recent(("2026-10-31T21:15:00+09:00", eq, ["4.3.2"], []))):
            m = self._start()["exception_guide"]["matched"]
        self.assertEqual(m, {"group": "온도 차 저하", "equipment_id": eq, "sensors": [], "exception": self.SID, "records": ["R-9001"],
                             "recent": {"section": "4.3.2", "days": 7, "record_id": "H-9900", "at": "2026-10-31T21:15:00+09:00", "days_ago": 3.0}})
        with self._with(self._rel()):
            r = self._start()
        self.assertEqual(r["exception_guide"]["matched"]["sensors"], [["temp_diff_k", "<=", 8.2]])
        self.assertNotIn("recent", r["exception_guide"]["matched"])
        self.assertNotIn("source_equipment", r["exception_guide"]["matched"])   # 실패 쪽: 관계에 등록 기기가 없으면 칸 0
        v = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v10/run_result.json").read_text(encoding="utf-8")))
        self.assertEqual([e.message for e in v.iter_errors(r)], [])
        rel = self._rel()
        rel["exception_relations"][0]["context"] = {"equipment_id": "M-09"}   # D-593: 등록 기기 ≠ 알람 기기 — 기존 equipment_id 뜻 그대로
        with self._with(rel):
            r = self._start()
        m = r["exception_guide"]["matched"]
        self.assertEqual((m["source_equipment"], m["equipment_id"]), ("M-09", self.CTX["equipment_id"]))
        self.assertEqual([e.message for e in v.iter_errors(r)], [])

    def test_recent_work_condition(self):   # D-570 사람 손 길: 알람 설비(M-02)가 3일 전 4.3.2를 했음 → 예외 추천 · 실패 쪽: 정비 없음 · 7일 밖 · 다른 설비 · 다른 절 · 알람 뒤 · 시각 칸 없음 → 추천 0
        at3, at8, after = "2026-10-31T21:15:00+09:00", "2026-10-26T21:15:00+09:00", "2026-11-03T21:15:00+09:00"
        eq = self.CTX["equipment_id"]
        with self._with(self._recent((at3, eq, ["4.3.2"], ["4.3"]))):
            r = self._start()
            self.assertEqual(r["exception_guide"]["relation_id"], self.SID)
            self.assertIn(self.SID, [a["section"] for a in r["preview"]["reason_facts"]["alternatives"]])
            self.assertNotIn("exception_guide", self._start({k: v for k, v in self.CTX.items() if k != "occurred_at"}))
            self.assertNotIn("exception_guide", self._start({k: v for k, v in self.CTX.items() if k != "equipment_id"}))
        with self._with(self._recent((at3, eq, [], ["4.3"]), section="4.3")):
            self.assertIn("exception_guide", self._start())   # 점검 절도 '한 일'
        for hist in ((), ((at8, eq, ["4.3.2"], []),), ((at3, "M-99", ["4.3.2"], []),), ((at3, eq, ["4.5.2"], ["4.3"]),), ((after, eq, ["4.3.2"], []),)):
            with self._with(self._recent(*hist)):
                self.assertNotIn("exception_guide", self._start(), hist)
        with self._with(self._recent((at8, eq, ["4.3.2"], []), days=10)):
            self.assertIn("exception_guide", self._start())   # 넓힘(days 늘림)이면 8일 전도 맞음

    def test_recent_work_from_request(self):   # D-570 덧(밤 범위 2): 앱이 start 요청에 그 설비 최근 정비(recent_work · 앱 DB)를 실음 → 그래프 이력에 없어도 대조 · 실패 쪽: 7일 밖 · 다른 절 → 0 · 약속 v10: start만 받음 · 칸 꼴 밖 거절
        import jsonschema
        rel = self._recent()
        work = lambda at, *secs: {**self.CTX, "recent_work": [{"record_id": "H-0301", "at": at, "sections": list(secs)}]}
        with self._with(rel):
            self.assertNotIn("exception_guide", self._start())   # 그래프 이력 0 · 요청 칸 없음
            self.assertEqual(self._start(work("2026-10-31T21:15:00+09:00", "4.3.2"))["exception_guide"]["relation_id"], self.SID)
            self.assertNotIn("exception_guide", self._start(work("2026-10-26T21:15:00+09:00", "4.3.2")))
            self.assertNotIn("exception_guide", self._start(work("2026-10-31T21:15:00+09:00", "4.5.2")))
        v = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v10/run_request.json").read_text(encoding="utf-8")))
        ok = work("2026-10-31T21:15:00+09:00", "4.3.2")
        self.assertEqual([e.message for e in v.iter_errors(ok)], [])
        self.assertEqual((ROOT / "contracts/v10/run_request.json").read_bytes(), (PACK / "schemas/run_request.json").read_bytes())
        judge_req = {"kind": "judge", "session_id": "S-1", "group": "CL", "similar": self.sim, "grades": self.CTX["grades"], "minutes": self.CTX["minutes"], "events": []}
        self.assertTrue(v.is_valid(judge_req))
        from packrt.run.engine import Runtime
        sys.path.insert(0, str(ROOT / "tools"))
        import graph_gate as GG
        with tempfile.TemporaryDirectory() as d:   # 실제 꼴: 그래프 파일만 바꾼 Pack 사본으로 Run 한 번(packrt 요청 schema 검사 · custom 코드는 따로 도는 python — mock이 안 닿음)
            g = Path(d) / "g.json"
            g.write_text(json.dumps(rel, ensure_ascii=False), encoding="utf-8")
            out = Runtime(GG._copy(g, d)).run(ok)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        self.assertEqual(out["output"]["exception_guide"]["relation_id"], self.SID)
        for bad in ({**judge_req, "recent_work": ok["recent_work"]}, {**ok, "recent_work": [{"record_id": "H-1", "at": "2026-10-31", "sections": ["4.3.2"]}]},
                    {**ok, "recent_work": [{"record_id": "H-1", "at": "2026-10-31T21:15:00+09:00", "sections": []}]}, {**ok, "recent_work": [{**ok["recent_work"][0], "x": 1}]}):
            self.assertFalse(v.is_valid(bad), bad)

    def test_recent_narrow_first_and_bad_shape(self):   # 좁은 것 먼저: 최근 정비 줄 = 설비 조건 줄 자리(넓은 센서 줄 n 큼보다 먼저) · 실패 쪽: 꼴 밖 recent → 거절
        rel = self._recent(("2026-10-31T21:15:00+09:00", self.CTX["equipment_id"], ["4.3.2"], []))
        wide = {**copy.deepcopy(rel["exception_relations"][0]), "id": "EX-R-9999", "n": 9, "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2]]}}
        rel["exception_relations"].append(wide)
        with self._with(rel):
            self.assertEqual(self._start()["exception_guide"]["relation_id"], self.SID)
        for bad in ({"days": 0}, {"days": 31}, {"days": True}, {"days": 7.0}, {"section": ""}, {"extra": 1}):
            with self._with(self._recent(**bad)):
                with self.assertRaises(G.RuleError, msg=str(bad)):
                    self._start()

    def test_bad_relation_refused_and_lost_relation_ignored(self):   # 실패 쪽: 허용 밖 조건 칸 · 없는 절 → 거절 · 그래프가 바뀌어 관계가 없어지면 원래 판정
        rel = self._rel(sensors=[["vibration", "<=", 1]])
        with self._with(rel):
            with self.assertRaises(G.RuleError):
                self._start()
        rel = self._rel()
        rel["exception_relations"][0]["step"] = "EX-없음"
        with self._with(rel):
            with self.assertRaises(G.RuleError):
                self._start()
        lock = [{"type": "lock"}, {"type": "precheck_done"}]
        self.assertEqual(self._judge(*lock), self._judge(*lock, rel_id=None))


if __name__ == "__main__":
    unittest.main()


class Desc(unittest.TestCase):   # D-560 ②: 점검 바꾸기 후보 줄 설명 = 그래프 점검 절 설명(desc · 매뉴얼 판정 기준 첫 문장 · 적재 때 · LLM 0) → 결정 reason_facts.alternatives
    REQ = json.loads((ROOT / "contracts/v8/examples/judge.request.json").read_text(encoding="utf-8"))

    def _run(self, rel=None):
        if rel is None:
            return judge.judge({**self.REQ, "events": [{"seq": 1, "type": "lock"}, {"seq": 2, "type": "precheck_done"}]})["result"]["decision"]
        real, cur = G._read, str(G.CURRENT.resolve())
        judge.manual.cache_clear()
        G.release.cache_clear()
        self.addCleanup(lambda: (judge.manual.cache_clear(), G.release.cache_clear()))
        with mock.patch.object(G, "_read", lambda p: rel if p == cur else real(p)):
            return self._run()

    def test_alternatives_carry_desc(self):   # 사람 손 길: 냉각 추천 4.3 → 후보 4.4 · 4.5 · 4.6마다 설명(13쪽)
        d = self._run()
        self.assertEqual(d["section"], "4.3")
        alts = d["reason_facts"]["alternatives"]
        self.assertEqual([a["section"] for a in alts], ["4.4", "4.5", "4.6"])
        self.assertEqual(alts[1]["desc"], '수위가 MIN 선 아래면 "이상"이다.')
        self.assertTrue(all(a["desc"] == G.check_desc()[a["section"]] for a in alts))
        self.assertEqual({k for a in alts for k in a}, {"section", "grade", "desc"})

    def test_no_desc_graph_same_as_before(self):   # 실패 쪽: 설명 없는 그래프(mm3-v2 꼴) · 설명 없는 절 → 칸 0(옛 결정 그대로) · 후보 없는 결정은 손 0
        rel = copy.deepcopy(G.release())
        for s in rel["sections"]:
            s.pop("desc", None)
        d = self._run(rel)
        self.assertFalse([a for a in d["reason_facts"]["alternatives"] if "desc" in a])
        part = copy.deepcopy(G.release())
        next(s for s in part["sections"] if s.get("app_no") == "4.5").pop("desc")
        self.assertEqual(["desc" in a for a in self._run(part)["reason_facts"]["alternatives"]], [True, False, True])
        lock = {"kind": "lock"}
        self.assertIs(G.with_desc(lock), lock)
        self.assertEqual(len(G.check_desc()), 16)
