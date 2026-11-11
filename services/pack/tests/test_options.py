"""D-476 판정 옵션(packrt 0.15.0 params.options) 시험 — ① 이력 질의 글 · ③ 판정 셈 · ④ 매뉴얼 결과를 판정에.
T1 코드 기본 값 = 마지막 승인 출력 지문 같음(첫 값 v15 · preview · manual_support 덧칸 밖 · 셈 바꿈 승인은 도구가 start 줄만 다시 씀) · T9 V9 값(score_rule rank_weighted) = 코드 기본 대비 확률 · 근거 글만 다름 · T2 꼴 셈 · T3 안전 규칙 격자 · T5 선언 · T6 ① 글 · T7 ④ · T8 두 node 같음.
선언 값(지금 값) 고정 = tests/fixtures/declared_options.json — 받아 넣기 도구가 승인 요청의 set_option만큼 후보 값으로 씀, 그 밖의 pack.yaml 손 고침은 T5에서 떨어짐(D-492 덧 34 · 013-129 (ㄷ)).
실패 글은 case 이름 · 옵션 값 · 칸 이름 · 개수만(정답 값 · 출력 값 0)."""
import itertools
import json
import sys
import unittest
from pathlib import Path

import yaml
from packrt import ir
from packrt.run.engine import Runtime

from start_contract import FINGERPRINTS, code_default, fingerprint, requests
from test_pack import CODE_DEFAULT, PACK, ROOT, _NeedsVectorDB

if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))
from custom import alarm, judge, rules, session   # noqa: E402

JUDGE_OPTS = {"score_rule", "near_min", "base_score", "call_threshold", "use_manual", "manual_weight", "order_signal", *(f"check_first_{g}" for g in ("tw", "pw", "cl", "ld"))}   # D-565 덧 3: 먼저 볼 점검(증상군마다)
DEFAULTS = CODE_DEFAULT   # 코드 기본(v15) — 한 벌(tests/start_contract.py · 받아 넣기 도구 다시 셈과 같음)
DECLARED = json.loads((ROOT / "tests/fixtures/declared_options.json").read_text(encoding="utf-8"))   # pack.yaml 선언 값 기록(받아 넣기 도구가 승인 요청 값으로 씀)
V9 = {**DEFAULTS, "score_rule": "rank_weighted"}   # V9(D-484 덧 14) 값 — T9 코드 성질(pack.yaml 안 읽음)


def _graph():
    return yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))["graphs"]["run"]["nodes"]


def _node(nid):
    return next(n for n in _graph() if n["id"] == nid)


def _requests():
    return requests(ROOT)   # T1 지문과 같은 요청(받아 넣기 도구와 같은 약속 · start_contract)


class Declaration(unittest.TestCase):   # T5 · T8
    def test_node_options_table(self):
        res = ir.load_pack(PACK)
        self.assertTrue(res.ok, [e["message"] for e in res.errors][:3])
        got = {(o["node"], o["name"]): o for o in ir.node_options(PACK)}
        self.assertEqual({n for n, _ in got}, {"prepare_history_query", "first_judge", "judge"})
        self.assertEqual({k for n, k in got if n == "prepare_history_query"}, {"query_text"})
        for nid in ("first_judge", "judge"):
            self.assertEqual({k for n, k in got if n == nid}, JUDGE_OPTS, nid)
        for (n, k), o in got.items():
            self.assertEqual(o["value"], DECLARED[k], f"{n}.{k} 지금 값")
        self.assertEqual(got[("prepare_history_query", "query_text")]["choices"], ["group_direction", "sensor_words", "sensor_boundary"])
        self.assertEqual(got[("first_judge", "score_rule")]["choices"], ["sum", "rank_weighted", "near_only"])
        for k, lo, hi in (("near_min", 0.5, 0.95), ("base_score", 0.1, 2), ("call_threshold", 30, 90), ("manual_weight", 0, 1)):
            self.assertEqual((got[("first_judge", k)]["min"], got[("first_judge", k)]["max"]), (lo, hi), k)
        self.assertEqual(got[("first_judge", "call_threshold")]["type"], "integer")

    def test_two_judge_nodes_same_declaration(self):   # T8(Q-1): 첫 판정 · 다음 판정 확률이 어긋나지 않게
        self.assertEqual(_node("first_judge")["params"]["options"], _node("judge")["params"]["options"])

    def test_safety_rules_not_declared(self):   # 안전 규칙(잠금 · A · B만 지시 · C · D 호출 · 확인 불가 2회 · 사전 점검)은 옵션 밖
        names = {k for n in _graph() for k in ((n.get("params") or {}).get("options") or {})}
        self.assertEqual(names, JUDGE_OPTS | {"query_text"})

    def test_code_default_covers_every_option(self):   # 코드 기본 Runtime(T1 · PS-02 · 앱 inproc)이 어느 옵션 후보에도 같은 값(013-129 A3)
        self.assertEqual(set(CODE_DEFAULT), JUDGE_OPTS | {"query_text"})

    def test_first_judge_reads_manual_hits(self):   # ④ 구조 바뀜 한 번(옵션 아님)
        self.assertEqual(_node("first_judge")["reads"], ["request", "history_hits", "manual_hits"])


class SameBytes(_NeedsVectorDB):   # T1: 코드 기본 값이면 마지막 승인 출력과 같음(덧칸 밖 · 첫 값 v15) — 셈 바꿈 승인 요청은 도구가 start 줄만 다시 씀(D-492 덧 38)
    CODE_DEFAULT = True

    def test_default_options_equal_v15(self):
        want = {k: v for k, v in json.loads((ROOT / FINGERPRINTS).read_text(encoding="utf-8")).items() if not k.startswith("_")}   # '_rewrite' = 다시 쓴 기록
        reqs = _requests()
        self.assertEqual(len(reqs), len(want))
        diff = []
        for name, req in reqs:
            r = self.rt.run(req)
            self.assertEqual(r["status"], "succeeded", name)
            if fingerprint(r["output"]) != want[name]:
                diff.append(name)
        self.assertEqual(diff, [], f"지문과 다른 case {len(diff)}개")

    def test_preview_is_lock_precheck_decision(self):   # 앱 api/sessions.py preview와 같은 길(잠금 · 사전 확인을 했다고 놓음)
        for name, req in _requests():
            if req["kind"] != "start":
                continue
            out = self.rt.run(req)["output"]
            sim = [rules.Similar(s["record_id"], s.get("cause"), s["score"]) for s in out["similar"]]
            st = session.fold(judge.manual(), req["group"], sim, [*req.get("events", []), {"type": "lock"}, {"type": "precheck_done"}])
            self.assertEqual(out["preview"], judge.graph_query.with_desc(session.decide(st, judge.manual(), req.get("grades", {}), req.get("minutes", {}))), name)   # D-560 덧층 그대로
            self.assertNotIn("manual_support", out, name)   # 끔이면 칸 없음


class DeclaredRun(_NeedsVectorDB):   # B5 ①(013-129): Runtime 시험 기본은 코드 기본 — 선언 값 Runtime도 모든 요청이 succeeded(값 견줌 0)
    CODE_DEFAULT = False

    def test_declared_values_run(self):
        reqs = _requests()
        self.assertGreaterEqual(len(reqs), 60)
        bad = [name for name, req in reqs if self.rt.run(req)["status"] != "succeeded"]
        self.assertEqual(bad, [], f"선언 값 Runtime 실패 case {len(bad)}개")

    def test_declared_order_signal_used(self):   # K4 엇갈림(eval 129) 막음: 선언 값 Runtime의 start 확률 = pack.yaml order_signal(count_equipment) 셈 — 코드 기본(similar)이 덮지 않음
        from custom import graph_query
        self.assertEqual(DECLARED["order_signal"], "count_equipment")
        code = code_default(Runtime(PACK))
        differ = 0
        for name, req in _requests():
            if req["kind"] != "start":
                continue
            p = self.rt.run(req)["output"]["preview"]["probabilities"]
            n = graph_query.scope_counts(req["group"], req.get("equipment_id"), req.get("occurred_at"))
            want = rules.round_to_100({c: (n.get(c, 0) + 1) / (sum(n.get(x, 0) for x in p) + len(p)) + judge.NO_ZERO for c in p})   # D-642 원인마다 기본 1건
            self.assertEqual(p, want, name)
            differ += code.run(req)["output"]["preview"]["probabilities"] != p   # 실패 쪽: 코드 기본(similar)으로 돌면 확률이 다름 — 이 시험이 두 길을 가름
        self.assertGreater(differ, 0)


class Declared(unittest.TestCase):   # T9: V9 값(score_rule rank_weighted)이면 코드 기본 대비 probabilities · reason_facts만 다르고 다음 단계(kind · section · cause)는 같음 — 후보 값의 다음 단계는 자동 개선 평가 몫
    SAME = ("kind", "section", "cause")

    def test_declared_vs_code_default(self):
        decl = {k: V9[k] for k in JUDGE_OPTS}
        reqs = _requests()
        self.assertGreaterEqual(len(reqs), 60)
        moved, other, step = 0, set(), []
        for name, req in reqs:
            a = judge.judge(req, options=DEFAULTS)["result"]["decision"]
            b = judge.judge(req, options={**DEFAULTS, **decl})["result"]["decision"]
            keys = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            moved += bool(keys)
            other |= keys - {"probabilities", "reason_facts"}
            if any(a.get(k) != b.get(k) for k in self.SAME):
                step.append(name)
        self.assertEqual(other, set(), "확률 · 근거 글 밖 칸이 다름")
        self.assertEqual(step, [], f"다음 단계가 다른 case {len(step)}개")
        self.assertGreater(moved, 0)   # 선언 값이 실제로 셈을 바꿈(빈 시험 아님)


class Scoring(unittest.TestCase):   # T2: 꼴 셈(만든 값 — 정답 파일 0)
    SIM = [rules.Similar("H-1", "A", 0.9), rules.Similar("H-2", "B", 0.7), rules.Similar("H-3", "A", 0.4)]

    def test_rules(self):
        self.assertEqual(rules.cause_scores(["A", "B"], self.SIM), {"A": 0.5 + 0.9 + 0.4, "B": 0.5 + 0.7})
        self.assertEqual(rules.cause_scores(["A", "B"], self.SIM, rule="rank_weighted"), {"A": 0.5 + 1 + 1 / 3, "B": 0.5 + 1 / 2})
        self.assertEqual(rules.cause_scores(["A", "B"], self.SIM, rule="near_only", near_min=0.8), {"A": 0.5 + 0.9, "B": 0.5})
        self.assertEqual(rules.cause_scores(["A", "B"], self.SIM, alpha=0.0), {"A": 0.9 + 0.4, "B": 0.7})
        self.assertEqual(rules.cause_scores(["A", "B"], self.SIM, manual={"B": 2.0}, manual_weight=0.5), {"A": 0.5 + 0.9 + 0.4, "B": 0.5 + 0.7 + 1.0})

    def test_unknown_rule_refused(self):   # 앱 engine: 모르는 rule은 ValueError(c1a15-app 답)
        with self.assertRaises(ValueError):
            rules.cause_scores(["A"], self.SIM, rule="nope")

    def test_default_scoring_is_v15(self):
        self.assertEqual(judge.scoring(DEFAULTS, None), session.Scoring())


class Safety(_NeedsVectorDB):   # T3: 옵션 격자 × 다음 판정 요청 — 안전 규칙 불변식(첫 점검 고르는 단계를 넉넉히: start 37건의 상위 10건 ×
    # 잠금 · 사전 점검 뒤 / 첫 점검 '정상' 뒤 / 둘째 점검 '확인 불가' 2회 뒤 + judge 요청 23건)
    GRID = {"score_rule": ["sum", "rank_weighted", "near_only"], "base_score": [0.1, 0.5, 1, 2], "call_threshold": [30, 50, 70, 90],
            "near_min": [0.5, 0.8, 0.95], "use_manual": [False, True]}

    def test_grid_invariants(self):
        k = judge.manual()
        bad, n = [], 0
        reqs = [(name, r) for name, r in _requests() if r["kind"] == "judge"]
        self.assertGreaterEqual(len(reqs), 20)
        for name, r in _requests():
            if r["kind"] != "start":
                continue
            sim = self.rt.run(r)["output"]["similar"]
            checks = [c.section_id for c in k.checks[r["group"]]]
            base_req = {"kind": "judge", "session_id": "S-1", "group": r["group"], "similar": sim, "grades": r["grades"], "minutes": r["minutes"]}
            head = [{"type": "lock"}, {"type": "precheck_done"}]
            reqs += [(f"{name}/r5", {**base_req, "events": head}),
                     (f"{name}/r5-normal", {**base_req, "events": [*head, {"type": "result", "section": checks[0], "value": "정상"}]}),
                     (f"{name}/r5-unable2", {**base_req, "events": [*head, *[{"type": "result", "section": checks[1], "value": "확인 불가"}] * 2]})]
        for name, req in reqs:
            cands = [c.code for c in rules.ordered_causes(k, req["group"])]
            req = {**req, "manual_support": {c: 1.0 / (i + 1) for i, c in enumerate(cands)}}
            st = session.fold(k, req["group"], [rules.Similar(s["record_id"], s.get("cause"), s["score"]) for s in req.get("similar", [])],
                              req.get("events", []))
            base = judge.judge(req, options=DEFAULTS)["result"]["decision"]
            for over in [*(dict(zip(self.GRID, v)) for v in itertools.product(*self.GRID.values())), DECLARED]:   # B5 ②(013-129): 선언 값 한 점 — 격자 밖 값 · manual_weight도
                opts = {**DEFAULTS, **over}
                d = judge.judge(req, options=opts)["result"]["decision"]
                n += 1
                why = None
                if not st.locked and d["kind"] != "lock":
                    why = "잠금 전 지시"
                elif d["kind"] in {"instruct", "instruct_after_checklist"}:
                    g = rules.effective_grade(d["section"], req.get("grades", {}), st.approvals)
                    if g not in rules.INSTRUCTABLE or d["kind"] != ("instruct" if g == "A" else "instruct_after_checklist"):
                        why = "C · D 지시"
                    elif st.unable.get(d["section"], 0) >= 2 or d["section"] in st.done:
                        why = "확인 불가 2회 · 이미 한 점검"
                elif base["kind"] not in {"instruct", "instruct_after_checklist", "call"} and d["kind"] != base["kind"]:
                    why = "판정 밖 단계가 옵션으로 바뀜"   # 잠금 · 사전 점검 · 조치 뒤 단계 · 정정 · 인계는 옵션과 상관없음
                if why:
                    bad.append((name, why, over))
        self.assertGreater(n, 30000)
        self.assertEqual(bad[:5], [], f"불변식 어김 {len(bad)} / {n}")


class QueryText(unittest.TestCase):   # T6: ① 글 꼴 — 경계 · 이름 · 단위 = custom/alarm_rules.json(알람 node와 같은 표)
    REQ = {"kind": "start", "group": "LD", "query": {"direction": "경계 위", "sensor_snapshot": {
        "air_temperature_k": 300.0, "process_temperature_k": 310.0, "rotational_speed_rpm": 1400.0, "torque_nm": 61.7, "tool_wear_min": 50.0}}}

    def test_forms(self):
        t = {f: judge.history_query(self.REQ, options={"query_text": f})["history_query"]["text"] for f in ("group_direction", "sensor_words", "sensor_boundary")}
        self.assertEqual(t["group_direction"], "토크 과부하 경계 위")
        self.assertEqual(t["sensor_words"], "토크 과부하 경계 위 토크 61.7 Nm")
        self.assertEqual(t["sensor_boundary"], "토크 과부하 경계 위 토크 61.7 Nm, 경계 60.1 Nm")

    def test_default_same_as_v15_and_rest_unchanged(self):
        a = judge.history_query(self.REQ, options={"query_text": "sensor_boundary"})["history_query"]
        b = judge.history_query(self.REQ, options=DEFAULTS)["history_query"]
        self.assertEqual({k: v for k, v in a.items() if k != "text"}, {k: v for k, v in b.items() if k != "text"})   # 필터 · 재정렬 센서 그대로

    def test_no_boundary_side_falls_back_to_value(self):   # 온도 차는 아래 경계만 — '경계 위'면 값만
        req = {**self.REQ, "group": "CL"}
        self.assertEqual(judge.history_query(req, options={"query_text": "sensor_boundary"})["history_query"]["text"],
                         judge.history_query(req, options={"query_text": "sensor_words"})["history_query"]["text"])


class ManualSupport(unittest.TestCase):   # T7: ④ 매뉴얼 절 → 원인(점검 절 · 조치 절, knowledge 표)
    def test_section_to_cause(self):
        k = judge.manual()
        for group, causes in k.causes.items():
            for c in causes:
                hits = [{"locator": {"doc": "MG-MM", "section": c.action_section}, "score": 0.5}]
                share = {x.code for x in causes if x.action_section == c.action_section}   # 조치 절을 함께 쓰는 원인(공구 교체 2.4.2 = TW-1 · TW-2)
                self.assertEqual(judge.manual_support(group, hits), {x: 0.5 for x in share}, c.code)
            for chk in k.checks[group]:
                hits = [{"locator": {"doc": "MG-MM", "section": chk.section_id}, "score": 0.25}]
                self.assertEqual(judge.manual_support(group, hits), {chk.cause: 0.25}, chk.section_id)
        hits = [{"locator": {"doc": "MG-MM", "section": "1.2"}, "score": 0.9}]   # 공통 · 안내 · 사전 점검 절 = 원인 없음
        self.assertEqual(judge.manual_support("TW", hits), {})

    def test_off_or_zero_weight_same_as_off(self):
        req = {"kind": "judge", "group": "CL", "similar": [], "events": [{"type": "lock"}, {"type": "precheck_done"}], "grades": {}, "minutes": {},
               "manual_support": {"CL-1": 3.0}}
        k = judge.manual()
        g = {c.section_id: "A" for c in k.checks["CL"]} | {s: "A" for s in k.sections}
        m = {s: 10 for s in k.sections}
        req = {**req, "grades": g, "minutes": m}
        off = judge.judge(req, options=DEFAULTS)["result"]["decision"]
        self.assertEqual(judge.judge(req, options={**DEFAULTS, "use_manual": True, "manual_weight": 0})["result"]["decision"], off)
        self.assertNotEqual(judge.judge(req, options={**DEFAULTS, "use_manual": True, "manual_weight": 1})["result"]["decision"]["probabilities"],
                            off["probabilities"])


if __name__ == "__main__":
    unittest.main()
