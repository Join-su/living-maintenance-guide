"""D-566 · 약속 v10 kind exception_split — 보고서 예외 칸 자유 글 → 예외 하나(주제 + 점검 목록 + 조치 목록 · 짝 꼴 없음 · AI 호출 1번 · 이 시험은 가짜 provider · 망 0).
꼴 = orchestration agents/c1a15 handoff/d566/contract-v10.md 1 · 2절. 가짜 정해진 답 = tools/fake/split_known.json(점검 둘 · 조치 둘인 예외 하나)."""
import json
import sys
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "pack"
sys.path.insert(0, str(PACK))
from custom import split  # noqa: E402
from packrt.run.engine import Runtime  # noqa: E402
from test_pack import FakeProvider, fake_rule, need_vector_db  # noqa: E402

V10 = ROOT / "contracts/v10"
KNOWN = json.loads((ROOT / "tools/fake/split_known.json").read_text(encoding="utf-8"))["texts"][0]
RES = jsonschema.Draft202012Validator(json.loads((V10 / "run_result.json").read_text(encoding="utf-8")))
REQ = jsonschema.Draft202012Validator(json.loads((V10 / "run_request.json").read_text(encoding="utf-8")))


def run(text, provider=None):
    p = provider or FakeProvider()
    out = Runtime(PACK, provider=p).run({"kind": "exception_split", "session_id": "S-1", "group": "CL", "text": text})
    return out, p.calls


def bare(out):
    """결과에서 줄마다 instruction(D-642 C)만 뺀 것."""
    e = out.get("exception") if isinstance(out, dict) else None
    if not e:
        return out
    return {**out, "exception": {**e, **{k: [{x: v for x, v in r.items() if x != "instruction"} for r in e[k]] for k in ("checks", "actions")}}}


def chk(what="필터 덮개 고정 나사", how="손으로 돌려 본다", criteria="모두 체결 = 정상", m=None):
    return {"what": what, "how": how, "criteria": criteria, "minutes": m}


def act(what="다시 조임", parts=(), m=None):
    return {"what": what, "parts": list(parts), "minutes": m}


def exc(checks=None, actions=None, topic="필터 덮개 나사"):
    return {"topic": topic, "checks": [chk()] if checks is None else checks, "actions": [act()] if actions is None else actions}


class Split(unittest.TestCase):
    def test_split_then_similar(self):   # 사람 손 길: 글 → 나눔(호출 1번) → 예외 하나 그대로 exception_similar 요청 → 비슷한 것 한 번(실제 Pack Runtime 두 번)
        need_vector_db(self)
        out, calls = run("  " + KNOWN["text"].replace(" ", "\n", 1) + "  ")   # 앞뒤 · 줄바꿈 섞여도 같은 글
        self.assertEqual((out["status"], calls), ("succeeded", 1), out.get("error"))
        self.assertEqual([e.message for e in RES.iter_errors(out["output"])], [])
        got = bare(out["output"]["exception_split"])
        self.assertEqual((got["exception"], got["reason"]), (KNOWN["exception"], None))
        self.assertEqual((len(got["exception"]["checks"]), len(got["exception"]["actions"])), (2, 2))   # 점검 둘 · 조치 둘인 예외 하나(짝 둘 아님)
        sim = {"kind": "exception_similar", "session_id": "S-1", "group": "CL", "exception": got["exception"]}
        self.assertTrue(REQ.is_valid(sim))
        s = Runtime(PACK).run(sim)
        self.assertEqual(s["status"], "succeeded", s.get("error"))
        self.assertEqual(set(s["output"]["exception_similar"]), {"matches"})

    def test_cannot_split(self):   # 실패 쪽: 모르는 글(가짜는 지어내지 않음) · 둘 다 0 = not_split · 꼴 밖뿐 = invalid_output — 모두 Run 성공 + exception null
        for text, reason in (("오늘 라인 점검 없음", "not_split"), ("[시험:나눔없음]", "not_split"), ("[시험:꼴깨짐]", "invalid_output")):
            out, calls = run(text)
            self.assertEqual((out["status"], calls, out["output"]), ("succeeded", 1, {"exception_split": {"exception": None, "reason": reason}}), text)
            self.assertTrue(RES.is_valid(out["output"]))
        self.assertEqual(fake_rule.respond("x\n## 예외 칸 글\n모르는 글")[1], ["fake_unknown_text"])

    def test_provider_error_fails_run(self):   # 실패 쪽: provider 오류 = Run 실패(다시 묻기 0 · 호출 1번) — 앱은 나누지 못함으로 다룸
        out, calls = run(KNOWN["text"], FakeProvider(fail_at=1))
        self.assertEqual((out["status"], calls), ("failed", 1))
        self.assertIn("split_exception", json.dumps(out["error"], ensure_ascii=False))

    def test_request_shape(self):   # 실패 쪽: text 없음 · 빈 글 → input_invalid(호출 0)
        for bad in ({"kind": "exception_split", "session_id": "S-1", "group": "CL"}, {"kind": "exception_split", "session_id": "S-1", "group": "CL", "text": ""}):
            p = FakeProvider()
            out = Runtime(PACK, provider=p).run(bad)
            self.assertEqual((out["error"]["code"], p.calls), ("input_invalid", 0))

    def test_prompt_has_text_last(self):   # 모델에 가는 글: 증상군 · 규칙(예외 하나 · 목록 · 적은 순서) · 끝 칸에 글 그대로(여러 줄도) · strict 구조화 출력
        seen = []

        class Spy(FakeProvider):
            def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, **kw):
                seen.append((model, messages[-1]["content"], schema["title"], kw.get("strict")))
                return super().chat(provider, model, messages, schema, temperature, max_tokens, **kw)
        run("첫 줄\n둘째 줄", Spy())
        model, prompt, title, strict = seen[0]
        self.assertEqual((model, title, strict), ("gpt-4.1-mini-2025-04-14", "ExceptionSplit", True))
        self.assertIn("## 증상군\nCL\n", prompt)
        self.assertIn("1. 글 하나 = 예외 하나입니다. 점검과 조치를 짝으로 묶지 않고, 예외를 여럿으로 나누지 않습니다.", prompt)   # D-566
        self.assertIn("**적은 순서대로**", prompt)
        self.assertIn("4. 재가동 · 재가동 뒤 확인, 잠금 확인, 파트장 호출 · 승인, 숙련자 인계, 감시 운전 · 감시로 마감, 확인 요청은 checks · actions에 넣지 않습니다(매뉴얼 1.2 · 1.4 · 6.2 — 점검 · 조치 절이 아님).", prompt)   # eval 203 빈틈
        self.assertIn("checks[i].what은 정비자가 실제로 본 부품 · 곳입니다.", prompt)
        self.assertIn('본 방법이 없으면 how도 빈 글("")입니다', prompt)
        self.assertIn("언제 했는지(3일 전 · 어제 · 날짜)는 어느 칸에도 쓰지 않습니다", prompt)   # D-597
        self.assertIn("'기준 없음' 같은 말도 쓰지 않습니다", prompt)
        self.assertIn("'예외상황' 머리는 붙이지 않습니다.", prompt)
        self.assertIn("instruction = 그 줄을 정비자에게 시키는 한 문장", prompt)   # D-642 C
        self.assertNotIn("짝 둘", prompt)
        self.assertTrue(prompt.endswith("## 예외 칸 글\n첫 줄\n둘째 줄\n"))


SRC = "필터 덮개 고정 나사를 손으로 돌려 본다. 하나 · 나사에 와셔를 끼우고 다시 조임. 냉각수 보충 3분 4분 5분 " + " ".join(f"부품{i}" for i in range(12))


class Finish(unittest.TestCase):   # 마무리 node 거르기(순수 함수)
    def f(self, e, text=SRC):   # 지시 문장(D-642 C)은 test_instructions에서만 봄 — 나머지는 칸 거르기만
        return bare(split.finish({"text": text}, {"exception": e})["result"]["exception_split"])

    def fi(self, e, text=SRC):
        return split.finish({"text": text}, {"exception": e})["result"]["exception_split"]

    def test_order_kept(self):   # D-566: 적은 순서 그대로 · 점검 둘 · 조치 둘이면 그대로 둘씩(짝으로 묶지 않음)
        got = self.f(exc(checks=[chk(what="하나"), chk()], actions=[act(what="냉각수 보충"), act()]))
        self.assertEqual([c["what"] for c in got["exception"]["checks"]], ["하나", "필터 덮개 고정 나사"])
        self.assertEqual([a["what"] for a in got["exception"]["actions"]], ["냉각수 보충", "다시 조임"])
        self.assertTrue(RES.is_valid({"exception_split": got}))

    def test_one_side_zero_not_split(self):   # D-566 덧 1 Q3: 점검 0 또는 조치 0 = 나누지 못함(not_split) · 냈지만 모두 걸러지면 invalid_output
        self.assertEqual(self.f(exc(checks=[])), {"exception": None, "reason": "not_split"})
        self.assertEqual(self.f(exc(actions=[])), {"exception": None, "reason": "not_split"})
        self.assertEqual(self.f(exc(checks=[chk(what="유압 펌프 압력")])), {"exception": None, "reason": "invalid_output"})
        self.assertEqual(self.f(exc(actions=[act(what="")])), {"exception": None, "reason": "invalid_output"})
        self.assertEqual(split.finish({"text": SRC}, {})["result"]["exception_split"], {"exception": None, "reason": "not_split"})
        self.assertTrue(RES.is_valid({"exception_split": {"exception": None, "reason": "not_split"}}))

    def test_trim_and_minutes(self):   # 칸 앞뒤 빈칸 · 분 1 이상 정수만 · 부품 빈 이름 뺌 · 방법 · 기준 빈 글 허용(D-556 덧 12)
        got = self.f(exc(checks=[chk(what="  나사 ", how=" ", criteria="", m=0)], actions=[act(parts=[" 와셔 ", " "], m=5), act(what="냉각수 보충", m=True)]))["exception"]
        self.assertEqual(got["checks"], [chk(what="나사", how="", criteria="", m=None)])
        self.assertEqual(got["actions"], [act(parts=["와셔"], m=5), act(what="냉각수 보충", m=None)])

    def test_drop_blank_and_cap(self):   # what 빈 칸은 뺌 · 목록마다 앞에서 10
        got = self.f(exc(checks=[chk(what=" "), chk(what="하나")], actions=[act(what=""), act()]))["exception"]
        self.assertEqual(([c["what"] for c in got["checks"]], [a["what"] for a in got["actions"]]), (["하나"], ["다시 조임"]))
        many = self.f(exc(checks=[chk(what=f"부품{i}") for i in range(12)], actions=[act(what=f"부품{i}") for i in range(12)]))["exception"]
        self.assertEqual((len(many["checks"]), len(many["actions"])), (split.MAX, split.MAX))

    def test_drop_made_up(self):   # 원문에 없는 what은 그 칸만 뺌(eval 153 셈) · 방법 · 기준은 대조 밖
        got = self.f(exc(checks=[chk(what="유압 펌프 압력"), chk(how="원문에 전혀 없는 말로 정리한 방법")], actions=[act(what="펌프 교체"), act()]))["exception"]
        self.assertEqual([c["how"] for c in got["checks"]], ["원문에 전혀 없는 말로 정리한 방법"])
        self.assertEqual([a["what"] for a in got["actions"]], ["다시 조임"])
        self.assertLess(split._ratio("유압 펌프 압력", split._norm(SRC)), split.SRC_MIN)
        k = KNOWN["exception"]   # 정해진 답(끝말 바꿔 씀)은 그대로 통과
        self.assertEqual(bare(split.finish({"text": KNOWN["text"]}, {"exception": k})["result"]["exception_split"])["exception"], k)

    def test_when_not_in_how(self):   # D-597 앱 15:17 실제 나눔 그대로: '언제'는 칸이 없음(원문에 남음) → how에서 뺌 · 기준 없음을 적은 글은 기준이 아님 · 실패 쪽: 방법 · 기준 글은 그대로
        text = "3일 전 점검한 흡기 필터 덮개 볼트가 덜 조여져 있었음 · 다시 조임"
        got = self.f(exc(checks=[chk(what="흡기 필터 덮개 볼트", how="3일 전 점검", criteria="덜 조여져 있었음 여부 확인용 기준 없음")], topic="흡기 필터 덮개 볼트"), text)["exception"]
        self.assertEqual((got["checks"][0]["how"], got["checks"][0]["criteria"]), ("", ""))
        for how, want in (("3일 전 손으로 돌려 봄", "손으로 돌려 봄"), ("어제 정비", ""), ("지난주에 점검함", ""), ("2026-11-03 확인", ""), ("10월 31일에 교체", ""), ("며칠 전 점검", ""), ("일주일 전 점검", ""), ("일주일 전에 손으로 돌려 봄", "손으로 돌려 봄"), ("한 달 전 정비", ""), ("두 주 전 점검", ""), ("하루 전 확인", ""), ("이틀 전 점검", ""), ("사흘 전 교체", ""), ("보름 전 점검", ""), ("작년 점검", ""), ("두 달 전 점검", ""), ("30분 전 확인", ""), ("어제 오후 점검", ""), ("오전 10시 점검", ""),
                          ("5분 뒤 다시 봄", "5분 뒤 다시 봄"), ("3/4 바퀴 돌려 봄", "3/4 바퀴 돌려 봄"), ("한 바퀴 돌려 봄", "한 바퀴 돌려 봄"),
                          ("손으로 돌려 본다", "손으로 돌려 본다"), ("토크 렌치로 5분 조임 확인", "토크 렌치로 5분 조임 확인"), ("점검", "점검"), ("", "")):
            self.assertEqual(split._how(how), want, how)
        for crit, want in (("기준 없음", ""), ("판정 기준이 없음", ""), ("기준 미기재", ""), ("없음", ""), ("모두 체결 = 정상", "모두 체결 = 정상"), ("누유 없음", "누유 없음"), ("이상 없음 = 정상", "이상 없음 = 정상"), ("", "")):
            self.assertEqual(split._criteria(crit), want, crit)

    def test_minutes_only_written(self):   # D-597 실제 한 번 출력 그대로: '3일 전'의 3이 분 칸에 옴 → 글에 'N분'이 없으면 null · 실패 쪽: '5분'이 있으면 그대로 · '15분'은 5분이 아님
        text = "3일 전 점검한 흡기 필터 덮개 볼트가 덜 조여져 있었음 · 다시 조임"
        got = self.f({"topic": "흡기 필터 덮개 볼트", "checks": [{"what": "흡기 필터 덮개 볼트", "how": "", "criteria": "덜 조여져 있었음", "minutes": 3}], "actions": [{"what": "다시 조임", "parts": [], "minutes": None}]}, text)["exception"]
        self.assertIsNone(got["checks"][0]["minutes"])
        src = split._norm("덮개 볼트 5 분 걸려 봄 · 다시 조임 15분")
        self.assertEqual((split._written(5, src), split._written(15, src), split._written(3, src), split._written(None, src), split._written(4, split._norm("14분"))), (5, 15, None, None, None))

    def test_instructions(self):   # D-642 C(사용자 R4 · 앱 칸 이름): 점검 · 조치 줄마다 instruction(무엇을 · 어떻게 · 판정 기준) — 모델 문장은 원문 밖 수 · 언제 말이 없을 때만 · 아니면 칸으로 짬 · 실패 쪽: 걸러진 줄은 문장도 같이 빠짐
        text = "필터 덮개 고정 볼트 네 개를 손으로 돌려 봄. 두 개가 덜 조여져 있으면 이상. 다시 조임"
        good = "필터 덮개 고정 볼트 네 개를 손으로 돌려 보고, 덜 조여진 것이 있으면 이상으로 봅니다."
        c1 = {**chk(what="필터 덮개 고정 볼트", how="네 개를 손으로 돌려 봄", criteria="두 개가 덜 조여져 있으면 이상"), "instruction": good}
        e = exc(checks=[c1, {**chk(what="유압 펌프 압력", how="", criteria=""), "instruction": "유압 펌프 압력을 봅니다."}], actions=[{**act(), "instruction": "풀린 볼트를 다시 조입니다."}])
        out = self.fi(e, text)["exception"]
        self.assertEqual([c["instruction"] for c in out["checks"]], [good])   # 원문 밖 점검(유압 펌프)이 빠지면 그 문장도 빠짐
        self.assertEqual(out["actions"][0]["instruction"], "풀린 볼트를 다시 조입니다.")
        for bad in ("필터 덮개 고정 볼트를 토크 12 Nm로 조입니다.", "3일 전처럼 필터 덮개 고정 볼트를 돌려 봅니다.", "", "x" * 130):
            got = self.fi({**e, "checks": [{**c1, "instruction": bad}]}, text)["exception"]["checks"][0]["instruction"]
            self.assertEqual(got, "필터 덮개 고정 볼트 확인 · 네 개를 손으로 돌려 봄 · 판정: 두 개가 덜 조여져 있으면 이상", bad)   # 칸으로 짬(근거 없는 값 0)
        old = self.fi(exc(checks=[chk(what="필터 덮개 고정 볼트", how="", criteria="")], actions=[act(parts=["와셔"])]), text)["exception"]   # 문장 칸이 없는 옛 모델 답
        self.assertEqual((old["checks"][0]["instruction"], old["actions"][0]["instruction"]), ("필터 덮개 고정 볼트 확인", "다시 조임 · 부품: 와셔"))

    def test_merge_same(self):   # 같은 점검(what · how · 기준) · 같은 조치(what)는 앞 하나로(빈칸 · 문장부호만 달라도 같음) · 부품 합침 · 분은 앞 값 없을 때만 뒤 값
        got = self.f(exc(checks=[chk(), chk(what="필터 덮개 고정나사", m=3)], actions=[act(parts=["와셔"]), act(what="다시  조임", parts=["와셔", "나사"], m=4), act(what="하나")]))["exception"]
        self.assertEqual(got["checks"], [chk(m=3)])
        self.assertEqual(got["actions"], [act(parts=["와셔", "나사"], m=4), act(what="하나")])

    def test_topic(self):   # D-560: 예외 하나의 짧은 주제 — 앞뒤 빈칸 · 머리 떼기 · 15자 · 비면 첫 조치 what 앞 15자 · v10 결과 필수
        t = lambda topic, actions=None: self.f(exc(topic=topic, actions=actions))["exception"]["topic"]
        self.assertEqual(t(" 덮개 나사 "), "덮개 나사")
        for raw in ("예외상황-필터 덮개 나사", "예외 상황 - 필터 덮개 나사", "예외상황: 필터 덮개 나사", "예외상황 필터 덮개 나사"):
            self.assertEqual(t(raw), "필터 덮개 나사", raw)
        self.assertEqual(t("냉각 팬 모듈 고정 나사 네 개 풀림"), "냉각 팬 모듈 고정 나사 네")   # 16번째가 띄어쓰기 → 15자 그대로
        self.assertEqual(t("냉각수 탱크 앞 커버 고정나사 풀림"), "냉각수 탱크 앞 커버")   # 15자가 낱말 중간 → 15자 안 마지막 띄어쓰기
        nospace = "냉각팬모듈고정나사네개풀림확인함"
        self.assertEqual(t(nospace), nospace[:15])
        long_act = "필터 덮개 고정 나사를 손으로 돌려 본다"   # 원문(SRC)에 있는 글
        self.assertEqual(t("", actions=[act(what=long_act), act()]), long_act[:15].strip())   # 비면 첫 조치 what 앞 15자
        self.assertEqual(t("예외상황-"), "다시 조임")
        e = self.f(exc())["exception"]
        self.assertFalse(RES.is_valid({"exception_split": {"exception": {k: v for k, v in e.items() if k != "topic"}, "reason": None}}))   # 실패 쪽: 주제 없음
        self.assertEqual(KNOWN["exception"]["topic"], "필터 덮개 나사")


if __name__ == "__main__":
    unittest.main()
