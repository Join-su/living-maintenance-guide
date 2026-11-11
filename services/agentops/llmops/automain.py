"""EV-035 (approved E3, answer 045-01 · D-181): the improvement wheel's main line after ② — this file holds ⓪ the write gate,
the spend cap and ③ dataset (CHG-180 · CHG-187). ④ ~ ⑧ come in later slices.

⓪ The write gate runs before ③ does anything (before the automatic promotion): no wheel tables → wait; the shared records with
the fake provider → wait (a fake wheel writes only a SQLite store — tests and the local loop); real mode without a cap → wait;
real mode without a hypothesis source (the agent off and no start document for this base revision) → end with a notice; the
server's total spend plus ③ ~ ⑤'s careful estimate over the cap → stop. Any of these writes nothing but the wheel's own state.

③ puts the wheel's failing operational Runs up as evaluation cases (the same row and raw copy as a person's '평가 case로 올리기',
by '자동(바퀴 A-n)'; every third goes hidden), registers the work suite (the base suite without hidden cases + the new fix cases)
and the hidden suite (hidden only) by the usual registration, runs the 운영 version twice on the work suite (the flakiness
baseline, ordinary evaluation Runs) and marks the sets: failing in every baseline Run = 고칠, passing in every one = 지킬,
mixed = 흔들림 (left out of counts, D-97). A start document's sets win for its cases.

The spend cap is one total for every wheel while this server runs (CHG-187): the auto_spend rows written since the server
started, and a running total kept in memory too (a deleted wheel's rows never reopen the cap — critic E3-A4 N3). In real mode
the Agent calls its provider itself and LLMOps does not see those tokens (v24 '토큰 미측정(실제 모드)'), so a finished Run's
spend is a careful estimate from what it did (chat · embedding · judge calls × the plan's conservative units)."""
import copy
import hashlib
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import autodeviate, autocycle, autodiag, autogrid, autohyp, autoorder, fixedflow, graphrel, kglink

# USD per one call, conservative (plan 6절: Gate B 12-case Run 0.50 USD conservative = Pack chat 10 · embedding 10 · judge 123)
UNIT_CHAT, UNIT_EMBED, UNIT_JUDGE = 0.01, 0.0005, 0.003
UNIT_CASE = 0.05            # one alarm case with its judge calls, conservative (0.50 / 10)
UNIT_JUDGE_NODE, UNIT_AGENT = 0.008, 0.06   # one node judged against its expect.text · one hypothesis-agent call (plan 6절)
PRICED_MODELS = ("gpt-4.1-nano", "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "text-embedding-3-small")   # EV-069: nano (Q-EF-3) · D-442: 4o-mini   # a model outside this list makes no paid step
DOC_SETS = {"fix": "고칠", "keep": "지킬", "hidden": "숨김", "flaky": "흔들림", "excluded": "셈 밖"}   # D-477 덧 5 ①: excluded = counted nowhere

HOLD_TABLES = "자동 개선 저장 표 없음(migration 승인 대기)"
HOLD_FAKE_SHARED = "가짜 모드 · 공유 기록 — 바퀴는 ③ 앞에서 멈춥니다(실제 호출 승인 뒤 실제 모드로)"
HOLD_NO_CAP = "실제 호출 상한 없음 — 바퀴는 여기서 멈춥니다(서버 시작 설정)"
END_NO_SOURCE = "끝 · 알림 — 가설 출처 없음(가설 에이전트 꺼짐 · 시작 문서 {why})"
END_NO_SUITE = "끝 · 알림 — 평가 suite 없음"
END_NO_FIX = "끝 · 알림 — 고칠 case 없음"
END_NO_FIELD = "끝 · 알림 — 현장 정답 없음(사람 체크 기록에 확정 원인 · 절이 없음) — 멈춤"   # EV-065 CHG-266
END_NO_FIELD_FILE = "끝 · 알림 — 현장 정답 없음(넘김 파일 없음 · 사람 체크 기록에 확정 원인 · 절이 없음) — 멈춤"   # D-444 #11: waited FIELD_WAIT_S
FIELD_WAIT = "현장 정답 기다림"   # D-444 #11 (ORCH answer 235 Q-3): ③ waits for the hand-over file the eval tool makes after the record
FIELD_WAIT_S = 600
# D-498 (E1 105 · ORCH answer 105): the check kinds whose named Run should have chosen the confirmed section (S1 · S4); 원인 다름 (S3,
# D-498 덧 8) is about the cause ranking — its start Run never chooses a check — and no Pack declares where that ranking is: nothing added
SIGNAL_TRUTH_KINDS = ("헛걸음", "파트장 수정")
SIGNAL_CAUSE_KIND = "원인 다름"
SIGNAL_CAUSE_NONE = "원인 다름 정답 못 만듦 — Pack의 원인 순위 자리 없음"
SIGNAL_TRUTH = "체크 신호 정답 — {kinds}(고른 점검 = 확정 절 {secs})"
SIGNAL_TRUTH_NONE = "체크 신호 정답 못 만듦 — Pack의 고른 점검 자리(chosen_check) 없음"
# D-546 덧 23 ② (eval exception-label · llmops-2 143): a 헛걸음 on the session's start Run is about the exception guide it gave — its
# chosen check is the lock, so 'chosen = confirmed' could never be met there. Its truth: the start Run's exception check (the Pack's
# state-labels exception_check) is not each section the record's field_truth.exception_wasted names
EXC_TRUTH = "체크 신호 정답 — 헛걸음 · 시작 Run(예외 안내 ≠ {secs})"
EXC_TRUTH_NONE = "체크 신호 정답 못 만듦 — Pack의 예외 안내 자리(exception_check) 없음"
EXC_START_NONE = "체크 신호 정답 없음 — 시작 Run {n}건: 기록에 헛걸음 예외 절 없음"
# D-525 (packrt-1 110): a person-checked judge Run holds in its input what the session's start Run gave it (the 10 records) — its case
# runs the session's two steps again, start → judge, so a retriever change reaches it as it reaches the suite's two-step cases
TWO_STEP = "사람 체크 판정 Run case {n}건 — 같은 세션 시작 Run 입력으로 시작 → 판정 두 걸음 셈(시작 결과에서 받는 칸 {keys})"
TWO_STEP_NOT = "두 걸음 아님 — {cid}: {why} · 판정 Run 입력 그대로 셈"
TWO_NO_VALUE = "입력 · 출력이 객체가 아님"
TWO_NO_TAKE = "시작 결과에서 받은 칸 없음"
TWO_NOT_SAME = "두 걸음으로 같은 판정 입력이 되지 않음"
REPLAY_MAX = 200   # D-477 (1) Q-1: the operational Runs replayed at ⑥, newest first
# EV-051 (CHG-W2-05 · 09 · 10): the wheel's vector DB versions
END_NO_INDEX = "끝 · 알림 — 기준 버전의 벡터 DB version 없음({stores}: packrt로 만든 version을 먼저 등록)"
END_INDEX_CHECK = "끝 · 알림 — 기준 확인 실패({why})"
INDEX_CAP = 12   # new versions per wheel (D-250 (5)); a failed build counts too
DROP_INDEX = "버림 — 벡터 DB version을 만들지 못함({why})"
DROP_INDEX_CAP = "버림 — 벡터 DB version 상한(12)"
DROP_INDEX_REAL = "버림 — 멈춤: 실제 임베딩 build는 따로 승인"
# EV-051 CHG-W2-18 ~ 20 (D-250 (5) · D-346): a real-embedding build — reserved before, recorded after, 10 USD a wheel
EMBED_USD_PER_TOKEN = {"text-embedding-3-small": 0.02 / 1_000_000}   # price list (USD per token)
INDEX_USD_CAP, INDEX_MARGIN = 10.0, 1.2
DROP_INDEX_USD = "버림 — 벡터 DB build 비용 상한(10달러)"
DROP_INDEX_SERVER = "버림 — 서버 비용 상한(${spent:.2f} / ${cap:.2f})"
DROP_INDEX_NOKEY = "버림 — 벡터 DB version을 만들지 못함(실제 임베딩 키 이름 없음)"
STOP_EMBED_MODEL = "멈춤 — 가격표에 없는 임베딩 모델({name})"
# D-476 덧 20 (packrt E1 d476-wheel-stop · Q-1 (가) · Q-2 (나) · Q-3 (가)): a person stops one wheel
STOP_ASKED = "멈춤 요청({by})"
STOPPED_BY = "사람이 멈춤({by}) — {step} 중{reason}"
END_HUMAN_STOP = "끝 · 사람이 멈춤({by})"
STOP_LEFT = "취소 요청 {n}개 — 서버가 마저 정리"
STEP_CANCELLED = "Run(취소)"
STEP_CUT = "③ 흔들림 기준선(끊김)"
ROW_STOPPED = "stopped"
STOP_WAIT_S = 30
STOP_POLL_S = 0.1
STOP_CAP = "멈춤 · 비용 상한(${spent:.2f} / ${cap:.2f} — 이 서버 합계)"
STOP_DAY = "멈춤 · 하루 비용 상한(${spent:.2f} / ${cap:.2f} — 오늘 합계 · 다음 날 이어감)"   # D-470 ⓓ (answer 067 Q-3 (가))
ONE_AT_A_TIME = "실제 모드 — 측정 Run은 한 번에 하나(동시 수 같음)"   # EV-060 CHG-251
STOP_MODEL = "멈춤 — 가격표에 없는 모델({names}) · 돈이 드는 단계 거부"
END_NO_CONFIRM = "끝 · 알림 — 확인 suite(test) 지정 없음"
END_OVERLAP = "끝 · 알림 — 확인 suite와 작업 suite case 겹침({n})"
# EV-069 (D-433): the efficiency wheel — no judge on its Runs, so one case is the Pack's own calls (contract Q-EF-2 · A4-N4)
UNIT_EFF_CASE = 2 * UNIT_CHAT + 2 * UNIT_EMBED
# D-470 ③ (D-446 덧 7 · packrt 132): after the first baseline Run a case is estimated at what that Run spent a case, this many times
MEASURED_MARGIN = 2
# D-492 ② (D-446 덧 7 · E1 095 2절): a retriever visit's query embedding at its upper end — the price list's embedding price × one call's
# input limit (no usage of it reaches the Run), and the words a recorded line gets when part of its spend was measured
EMBED_QUERY_TOKENS = 8191
EMBED_QUERY_MAX = max(EMBED_USD_PER_TOKEN.values()) * EMBED_QUERY_TOKENS
MEASURED_PART = " · 실측 일부"
END_NO_STRUCT = "끝 · 알림 — 구조 가설 없음(바꿀 수 있는 llm · 표준 node 없음)"
END_EFF_NO_BASE = "끝 · 알림 — 기준이 통과한 지킬 case 없음(효율을 잴 기준 없음)"   # 041 Q-S4-1 (L1)
# D-462 A (D-460 · D-461): the structure candidate agent's ④ — one call a pass, its real price recorded (the estimate holds the cap)
UNIT_STRUCT_AGENT = 0.10
END_EFF_HUMAN = "끝 · 사람 승인 필요 — 구조 후보 {m}번 반복에서 판정 기준 통과 0{best}"   # D-464 (1): the best row and why it is out
WAIT_OVER = "대기 — 시험 6개 넘음(시험 안 함)"
# D-470 ⓒ (answer 068): the passing rows of other nodes put together — one per batch, outside the six tried
MERGED_SOURCE = "합침"
DROP_MERGED = "버림 — 합친 줄 {why}"
RULES_FALLBACK = "구조 후보 에이전트 {why} — 이번 반복은 규칙 틀 3개로"   # D-462 (방향, 고정 흐름 풀기 ④): the three rules stay as the fallback
FALLBACK = object()
END_ONCE = "끝 · 에이전트 한 번 시험 — 후보 검사까지 · 줄 시험 안 함"   # LLMOPS_AUTO_STRUCTURE=agent-once (rule 04: the first real call alone)


def day_start(now=None):
    """D-470 ⓓ: 00:00 of the day in Asia/Seoul (the daily cap's day) as a timestamp."""
    from zoneinfo import ZoneInfo
    t = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo("Asia/Seoul"))
    return t.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def left_at_dataset(wheels, state_of, w):
    """D-502 (2) · packrt-1 090: the promotion ids put up by the Project's other wheels that ended at ③ (cell 3) — the next wheel uses
    them again; a wheel past ③, an open wheel and a person's promotion keep theirs as already put up."""
    out = set()
    for x in wheels:
        if x["wheel_id"] != w["wheel_id"] and x.get("ended_at") and (x.get("cell") or 3) <= 3:
            out.update((state_of(x["wheel_id"]) or {}).get("promoted") or [])
    return out


def is_eff(w):
    return (w or {}).get("kind") == "efficiency"


BASE_SUITE_STATUSES = ("완료", "일부 실패")   # D-484 덧 7: the accuracy wheel's base suite Run — a failed case counts 'not right' (autocand.outcome)


def base_suite_run(store, w, near=()):
    """The wheel's base suite Run on its base version: the last '완료' one as before; an accuracy wheel on a version with none takes the
    last '일부 실패' one (#9 R-9: the failed cases are the ones to fix) — a '완료' one comes first, so no base that was picked moves. An
    efficiency wheel keeps '완료' only (its work suite is chosen at the start anyway). D-602: near = the versions joined to the base
    (only the graph differs) — their Run when the base has none; a version made by a new graph has never been evaluated."""
    for rid in (w["base_release_id"], *near):
        run = store.last_suite_run(rid)
        run = run if run or is_eff(w) else store.last_suite_run(rid, statuses=BASE_SUITE_STATUSES)
        if run:
            return run
    return None


def eff_of(w):
    return (json.loads(w["settings"]) or {}).get("efficiency") or {}


def embed_price(name):
    """USD per token of an embedding model of the price list, or its dated name; None for any other."""
    hits = [m for m in EMBED_USD_PER_TOKEN if isinstance(name, str) and (name == m or name.startswith(m + "-2"))]
    return EMBED_USD_PER_TOKEN[hits[0]] if hits else None


def priced(name):
    """A model of the price list, or its dated name (gpt-4.1-mini-2025-04-14 → gpt-4.1-mini)."""
    return isinstance(name, str) and any(name == m or name.startswith(m + "-2") for m in PRICED_MODELS)


def used_models(rel, judge_model):
    """The models a Run of this release calls: its llm nodes' models, its vector stores' embedding models and the judge."""
    names = {(n.get("params") or {}).get("model") or "(이름 없음)" for n in (rel.get("graph") or {}).get("nodes", []) if str(n.get("kind", "")).startswith("llm")}
    names |= {((s or {}).get("embedding") or {}).get("model") or "(이름 없음)" for s in (((rel.get("pack") or {}).get("bindings") or {}).get("stores") or {}).values()
              if (s or {}).get("type") == "vector" or (s or {}).get("embedding")}
    return sorted(names | {judge_model or "(이름 없음)"})   # an unknown name is refused like an unpriced one (review 2 N1)


class StartDoc:
    """The start-hypothesis document (LLMOPS_AUTO_START_HYPOTHESES, Q-2 A): {"name", "base_revision", "sets": {case: fix|keep|hidden|flaky},
    "hypotheses": [...]}. Read once; a missing or broken file counts as no document (with the reason)."""
    def __init__(self, path):
        self.path, self.doc, self.why, self.sha = path, None, "없음", None
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
            doc = json.loads(raw)
            if not isinstance(doc, dict) or not str(doc.get("base_revision", "")).startswith("sha256:"):
                raise ValueError("base_revision이 없음")
            if any(v not in DOC_SETS for v in (doc.get("sets") or {}).values()):
                raise ValueError("sets 값은 fix · keep · hidden · flaky · excluded")
            self.doc, self.sha = doc, hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError) as e:
            self.why = f"읽을 수 없음({type(e).__name__})"

    def for_wheel(self, w):
        """The document when it is for this wheel's base revision, else None; .why says which."""
        if self.doc and self.doc["base_revision"] == w["base_revision"]:
            return self.doc
        if self.doc:
            self.why = "기준 다름"
        return None

    def label(self):
        return f"시작 가설 문서 {self.doc.get('name') or Path(self.path).name} · sha256 {self.sha[:12]}" if self.doc else None


def agent_nodes(nodes, base_case, resp, limit=4, signal=None):
    """EV-079 (D-462 C): the knobbed nodes a baseline case passed before the responsible node, nearest first (at most `limit`) — the
    agent may propose on them too (a retriever's order decides what a judging node sees). signal (D-463 덧 24 (a)): the knobbed nodes
    on the chain up to the signal node instead, nearest the signal first (a step-1 signal reads the first step's visits) — the responsible
    node can sit at the head of that chain with no knob of its own (copy round A-9). D-525 덧 6: a two-step case's signal in the second
    step goes on into the first step through what 'then.take' carried (autohyp.take_chain) — the first-step nodes come before."""
    if signal:
        src = base_case or {}
        if signal not in [v["node"] for v in src.get("visits") or []]:
            src = ((src.get("steps") or [{}])[0] or {})
        before = autohyp.take_chain(src, nodes, signal)[0] + autohyp.chain(src, nodes, signal) + [signal]
    else:
        order = [v["node"] for v in (base_case or {}).get("visits") or []]
        before = order[:order.index(resp)] if resp in order else order
    out, seen = [], set()
    for name in reversed(before):
        n = nodes.get(name)
        if n and name != resp and name not in seen and autohyp.knobs_of(n):
            out.append(n); seen.add(name)
        if len(out) >= limit:
            break
    return out


def front_nodes(nodes, sigs, resp, checked, limit=4):
    """D-525 덧 28 (A-22): the nodes before the responsible node come first from the person-checked case that chose it when that case is
    two-step — its chain across the take, nearest first — and the places left (at most `limit`) from the other two-step cases' chains;
    a checked case whose first-step maker the record does not show gives its reason and the others fill. No checked two-step case → the
    first case with a signal on it, as before (D-525 덧 6). sigs: [(per_case row, baseline case)] in suite order.
    → (nodes, [(case, took, keys, why)] to say)."""
    chains = [(p, c, autohyp.take_chain(c or {}, nodes, p["signal"])) for p, c in sigs]
    head = next((x for x in chains if x[0]["case"] in checked and (x[2][0] or x[2][2])), None)
    if head is None:
        p, c, (took, keys, why) = chains[0]
        return agent_nodes(nodes, c, resp, limit, signal=p["signal"]), [(p["case"], took, keys, why)] if took or why else []
    p, c, (took, keys, why) = head
    out, said = agent_nodes(nodes, c, resp, limit, signal=p["signal"]), [(p["case"], took, keys, why)]
    for p, c, (took, keys, _) in [x for x in chains if x is not head and x[2][0]]:
        if len(out) >= limit:
            break
        names = {n["name"] for n in out}
        new = [n for n in agent_nodes(nodes, c, resp, limit, signal=p["signal"]) if n["name"] not in names][:limit - len(out)]
        out += new
        if added := [t for t in took if t in {n["name"] for n in new}]:
            said.append((p["case"], added, keys, None))
    return out, said


def shown_chain(nodes, sigs, checked):
    """D-525 덧 30 (A-23): ④'s table shows the case front_nodes takes first — the person-checked two-step case with a signal on the
    responsible node: its first-step nodes across the take (step 1, not judged) before its own rows. None → the first fix case's rows,
    as before. → {"case", "rows"} | None"""
    for p, c in sigs:
        took, _, why = autohyp.take_chain(c or {}, nodes, p["signal"]) if p["case"] in checked else ([], [], None)
        if took or why:
            return {"case": p["case"], "rows": [{"node": n, "ok": None, "input_ok": None, "step": 1} for n in took] + (p.get("rows") or [])}
    return None


def shown_rows(resp):
    """④'s table: the shown chain (D-525 덧 30), else a fix case that points to the concluded node (D-652 9-a: the first fix case may point
    to another node than the one most cases name), else the first fix case. → {"case", "rows"}"""
    if resp.get("shown"):
        return resp["shown"]
    per = resp.get("per_case") or []
    mine = next((p for p in per if p.get("node") == resp.get("node")), None)
    if mine:
        return {"case": mine.get("case"), "rows": mine.get("rows") or []}
    return {"case": None, "rows": (per[0].get("rows") if per else None) or []}


FAULT_NODES = "출력 꼴 어김 node도 가설 대상 — {names}"   # D-478 덧 27
TAKE_NODES = "앞 node에 첫 걸음 node 더함 — {names}(두 걸음 case {case}가 첫 걸음 결과에서 받은 칸 {keys})"   # D-525 덧 6
TAKE_NOT = "앞 node 그대로 — {case}: {why}"


def fault_nodes(per_case, resp):
    """D-478 덧 27: {node: fix cases whose output broke its schema there}, most first (the responsible node itself left out)."""
    count = {}
    for p in per_case or []:
        n = ((p.get("fault") or {}).get("node"))
        if n and n != resp:
            count[n] = count.get(n, 0) + 1
    return dict(sorted(count.items(), key=lambda kv: -kv[1]))


FAULT_QUOTA = "출력 꼴 어김 node 몫 — {node} {knob}(규칙){moved}"   # D-492 ② (packrt 189 제안 2)
FAULT_QUOTA_EMPTY = "출력 꼴 어김 node 몫 비움 — {names}(시험할 옵션 없음)"
FAULT_QUOTA_WHY = "규칙 가설 — 출력 꼴 어김 node 몫(D-492 ②)"


def fault_quota(ok, faulted, nodes, resp, fix_ids, tried, tried_knobs, vet):
    """D-492 ② (A-3: the agent's four all on draft, none on escalation_draft): ok = [(hypothesis, None, ops)] after the checks. When a
    fault node is there and none of them is on one, the most-faulted node's first untried rule step that passes vet(h) → (why, ops)
    goes in; with BREADTH or more already, the responsible node's last one (else the last one) waits ('_wait': the waiting batch, kept)
    — the iteration's breadth as before. → (ok, the log line or None)."""
    if not faulted or any(h.get("node") in faulted for h, _, _ in ok):
        return ok, None
    for name in faulted:
        firsts = {}
        for h in autohyp.rule_hypotheses(nodes[name], fix_ids, tried):
            firsts.setdefault(h["knob"], h)
        for knob, h in firsts.items():
            if (name, knob) in tried_knobs:
                continue
            h = {**h, "why": FAULT_QUOTA_WHY, "source": "rule"}
            why, ops = vet(h)
            if why or not ops:
                continue
            out, moved = list(ok), ""
            if len(out) >= autohyp.BREADTH:
                i = max((j for j, (x, _, _) in enumerate(out) if x.get("node") == resp), default=len(out) - 1)
                x = out.pop(i)
                out.insert(i, (h, None, ops))
                out.append(({**x[0], "_wait": True}, x[1], x[2]))
                moved = f" · {x[0].get('node')} {x[0].get('knob')} 대기로"
            else:
                out.append((h, None, ops))
            return out, FAULT_QUOTA.format(node=name, knob=knob, moved=moved)
    return ok, FAULT_QUOTA_EMPTY.format(names=" · ".join(faulted))


REFILL = "버림 {n}개 자리 다시 채움 — {names}(규칙)"   # D-525 덧 44 (A-22 · 24 · 25)
REFILL_NONE = "버림 {n}개 자리 — 더 시험할 옵션 없음"
REFILL_WHY = "규칙 가설 — 버린 가설 자리(D-525 덧 44)"


def refill(ok, fill_nodes, fix_ids, tried, tried_knobs, idle, vet, knobs=None, first=()):
    """D-525 덧 44: the places the checks dropped go to the first rule step of each option not covered yet (not kept in ok, not tried in
    this wheel, not idle), in autohyp.fill_order (A-27: the checked case's chain first · the options the agent may be shown) — each
    through vet(h) → (why, ops), up to BREADTH kept in all; tried (the triples of this wheel and the finished ones, already_tried) is
    never filled again. → (ok, the 'node knob' names filled)."""
    out, have, names = list(ok), {(h.get("node"), h.get("knob")) for h, _, _ in ok}, []
    for h in autohyp.fill_order(fill_nodes, fix_ids, tried, tried_knobs, idle, knobs, first):
        if len(out) >= autohyp.BREADTH:
            break
        if (h["node"], h["knob"]) in have:
            continue
        h = {**h, "why": REFILL_WHY, "source": "rule"}
        why, ops = vet(h)
        if why or not ops:
            continue
        out.append((h, None, ops))
        have.add((h["node"], h["knob"]))
        names.append(f"{h['node']} {h['knob']}")
    return out, names


MERGE_WHY = "규칙 가설 — 같은 node 옵션 합친 줄(A-28 · 함께만 시험)"
MERGE_LINE = "같은 node 옵션 합친 규칙 줄 — {node} {knobs}(함께만 시험)"
MERGE_TRIED = "같은 node 옵션 합친 규칙 줄 — {node} 같은 값 묶음을 이미 시험함 · 넣지 않음"


def merge_rule(fill_nodes, first, batch, fix_ids, tried, knobs, it, idle=(), size=3):
    """A-28 (A-23: the cooling node moved nothing alone, its three options together fixed 5/29): one merged rule row — on the first node
    in fill order (the checked case's chain first) whose batch has fewer than `size` options: those options as the batch has them plus
    the next rule steps (knobs: the options the agent may be shown) up to `size`; never a value tried before (tried). Its hypotheses
    carry '_merge' and are tried only together. [] when no node can make one."""
    by = {n["name"]: n for n in fill_nodes}
    order = [by[x] for x in dict.fromkeys(first) if x in by] + [n for n in fill_nodes if n["name"] not in first]
    for n in order:
        if n["name"] in idle:
            continue
        have = {}
        for x in batch:
            if x.get("node") == n["name"] and not x.get("_merge"):
                have.setdefault(x["knob"], x)
        if len(have) >= size:   # the batch's own merged row (autocand.combine) already has them
            continue
        picked = dict(have)
        for x in autohyp.fill_order([n], fix_ids, tried, set(), (), knobs):
            if len(picked) >= size:
                break
            picked.setdefault(x["knob"], x)
        if len(picked) >= 2 and len(picked) > len(have):
            return [{**x, "_merge": f"M{it}", "why": MERGE_WHY, "source": "rule", "predicts": x.get("predicts") or list(fix_ids)} for x in picked.values()]
    return []


def with_faults(per_case, ids, run, folder):
    """D-478 덧 26: the responsible-node record's cases, each with its node output's schema break (autohyp.schema_fault) when it has one."""
    by = dict(zip(ids, (run or {}).get("cases") or []))
    return [{**p, **({"fault": f} if (f := autohyp.schema_fault(by.get(p.get("case")) or {}, folder)) else {})} for p in per_case]


def as_json(v):
    """D-549 덧 12: a Run's output as JSON — its JSON text read, anything else as it is (never a Python repr)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def fit_json(v, cut):
    """A JSON value within `cut` characters of JSON text: whole when it fits, else its longest lists shortened from the end (what is
    left keeps its addresses), else its JSON text cut (D-549 덧 12: an agent's evidence addresses the output as it was sent)."""
    text = lambda x: json.dumps(x, ensure_ascii=False, default=str)   # noqa: E731
    if isinstance(v, str):
        return v[:cut]
    if len(text(v)) <= cut:
        return v
    v = copy.deepcopy(v)

    def lists(x):
        if isinstance(x, list):
            yield x
            for y in x:
                yield from lists(y)
        elif isinstance(x, dict):
            for y in x.values():
                yield from lists(y)
    while len(text(v)) > cut:
        ls = [x for x in lists(v) if x]
        if not ls:
            return text(v)[:cut]
        max(ls, key=lambda x: len(text(x))).pop()
    return v


def agent_cases(per_case, ids, base_run, expect, field, cut=1500, folder=None):
    """EV-079 (D-462 C): the fix cases as the agent sees them — the baseline's output (and a two-step case's first output) as JSON
    (D-549 덧 12), the case's expected final checks and its field truth, each output within `cut` characters (fit_json); no hidden case,
    no person's reason (D-94). D-478 덧 26: a case whose node output broke its schema carries 'fault' — the field's path and the Pack's
    allowed values, never the value."""
    by = dict(zip(ids, (base_run or {}).get("cases") or []))
    out = []
    for p in per_case:
        c = by.get(p["case"]) or {}
        first = ((c.get("steps") or [{}])[0] or {}).get("final")
        out.append({"case": p["case"], "set": "고칠", "signal": p.get("signal"), "output": fit_json(as_json(c.get("final") or ""), cut),
                    **({"first_output": fit_json(as_json(first), cut)} if first else {}),
                    **({"expected": ((expect or {}).get(p["case"]) or {}).get("final")} if ((expect or {}).get(p["case"]) or {}).get("final") else {}),
                    **({"field_truth": (field or {})[p["case"]]} if (field or {}).get(p["case"]) else {}),
                    **({"fault": f} if (f := autohyp.schema_fault(c, folder)) else {})})
    return out


def gold_nodes(suite):
    """EV-043 CHG-211: the nodes with gold positions ({"evidence": …}) in a suite's expectations."""
    return {nid for c in (suite or {}).get("cases") or [] for nid, chks in ((c.get("expected") or {}).get("nodes") or {}).items()
            if any(isinstance(x, dict) and "evidence" in x for x in chks or [])}


def is_retriever(node, gold):
    """A retriever row's node: retrieve@2 with gold positions in the work suite (CHG-211 정의)."""
    return bool(node) and str(node.get("kind") or "").startswith("retrieve@2") and node["name"] in gold


def retriever_node(doc, rel, suite):
    """The node of a retriever wheel: the start document's first hypothesis on a retriever node of this suite; None otherwise."""
    nodes, gold = autohyp.nodes_of(rel), gold_nodes(suite)
    return next((h["node"] for h in autohyp.doc_hypotheses(doc) if is_retriever(nodes.get(h.get("node")), gold)), None)


def row_nodes(rel):
    """D-476 덧 18: the node map the rows are built on — ④'s own (autohyp.with_options: a function node's declared options attached),
    so an option hypothesis becomes its set_option line in ⑤ too. D-543 ⑥ ③: the node reading the graph file gets its relations (graphrel)."""
    folder = (rel.get("pack") or {}).get("copy")
    return graphrel.attach(autohyp.with_options(autohyp.nodes_of(rel), folder), folder)


def two_step(judge_in, start_in, start_out):
    """D-525: (then, None) — the EV-079 two-step 'then' that makes the judge Run's input from its session's start Run input and output
    (take = the judge input's keys the start output gave it as they are · set = the other keys the judge input has otherwise · drop =
    the start input's keys the judge input has not; the technician's inputs stay the judge Run's); the start output it had makes the
    judge input again byte for byte, else (None, why)."""
    from .runs import next_input
    if not all(isinstance(x, dict) for x in (judge_in, start_in, start_out)) or any(not k or "." in k for k in [*judge_in, *start_in]):
        return None, TWO_NO_VALUE
    gone = object()
    take = {k: k for k, v in judge_in.items() if k in start_out and start_out[k] == v and start_in.get(k, gone) != v}
    if not take:
        return None, TWO_NO_TAKE
    then = {"set": {k: v for k, v in judge_in.items() if k not in take and start_in.get(k, gone) != v}, "take": take,
            "drop": [k for k in start_in if k not in judge_in]}
    then = {k: v for k, v in then.items() if v}
    if next_input(start_in, then, start_out) != judge_in:
        return None, TWO_NOT_SAME
    return then, None


def input_sha(x):
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def case_ok(c, on_fail=None):
    """A case passed when it finished and no failed check stands (the '고침' / '망가짐' meaning of CHG-182 ③ without a judge) — EV-089
    (D-491): autocand.outcome's count, a failure its re-check on the on_fail branch passed is not one (on_fail = autocand.on_fail_of(graph))."""
    return autocand.outcome(c, on_fail=on_fail)


def _run_ids(x):
    """D-476 덧 20: every Run id (run-… · cand-…) in a wheel's state and rows, wherever a step kept it."""
    if isinstance(x, str):
        return [x] if x.startswith(("run-", "cand-")) else []
    if isinstance(x, dict):
        return [u for v in x.values() for u in _run_ids(v)]
    if isinstance(x, (list, tuple)):
        return [u for v in x for u in _run_ids(v)]
    return []


def visit_spend(graph, cases, price=None):
    """D-492 ② (D-486 덧 12's count, shared with the sealed test): (calls, measured, estimated) of the cases' visits — an llm visit with
    its provider usage (tokens_in · tokens_out, or one total at the output price) and a priced model (`price`; by default the count-only
    list the sealed count reads — the wheels' list, then autosealed.SEALED_EXTRA_USD_PER_1M, D-492 덧 22: gpt-6-luna priced for the count,
    never a model a wheel switches to) is measured; an llm visit without either is UNIT_CHAT, estimated; a retriever visit with its
    embed_tokens (D-492 덧 9) and a priced embedding_model is measured (0 tokens: no call, nothing), else EMBED_QUERY_MAX, estimated."""
    if price is None:
        from .autosealed import chat_price_sealed
        price = chat_price_sealed
    nodes = {n.get("name"): n for n in (graph or {}).get("nodes") or []}
    calls, measured, estimated = 0, 0.0, 0.0
    for c in cases or []:
        for v in (c or {}).get("visits") or []:
            n = nodes.get(v.get("node")) or {}
            kind = str(n.get("kind") or "")
            if kind.startswith("llm"):
                calls += 1
                p = price(v.get("model") or (n.get("params") or {}).get("model"))
                tin, tout = v.get("tokens_in"), v.get("tokens_out")
                if p and (tin is not None or tout is not None):
                    measured += ((tin or 0) * p[0] + (tout or 0) * p[1]) / 1_000_000
                elif p and v.get("tokens") is not None:
                    measured += v["tokens"] * max(p) / 1_000_000
                else:
                    estimated += UNIT_CHAT
            elif kind.startswith("retrieve"):   # D-492 덧 9: its own embed_tokens when packrt gives them (0 = no call), else the upper end
                t, ep = v.get("embed_tokens"), EMBED_USD_PER_TOKEN.get(v.get("embedding_model"))
                if t == 0:
                    continue
                calls += 1
                if isinstance(t, int) and ep is not None:
                    measured += t * ep
                else:
                    estimated += EMBED_QUERY_MAX
    return calls, measured, estimated


def spend_of(run):
    """{calls, measured, estimated, usd} of a finished Run: its visits (visit_spend) and its judged nodes (UNIT_JUDGE each, estimated —
    the judge's usage is not kept)."""
    calls, measured, estimated = visit_spend(run.get("graph"), run.get("cases"))
    mj = ((run.get("summary") or {}).get("scorecard") or {}).get("model_judge") or {}
    judged = sum(n.get("n", 0) for n in (mj.get("nodes") or {}).values())
    estimated += judged * UNIT_JUDGE
    return {"calls": calls + judged, "measured": measured, "estimated": estimated, "usd": measured + estimated}


def run_spend(run):
    """(calls, usd) a finished Run cost in real mode: measured where its usage came back, the careful units elsewhere (spend_of)."""
    sp = spend_of(run)
    return sp["calls"], sp["usd"]


def spent_note(note, runs):
    """A recorded line's model words: MEASURED_PART after them when part of the Runs' spend was measured (D-492 ②)."""
    return note + MEASURED_PART if any(spend_of(r or {})["measured"] > 0 for r in runs) else note


def kind_unit(graph, judge=True):
    """D-492 ② (D-446 덧 7): one case's careful spend before any Run, from the graph's paid nodes — an llm node UNIT_CHAT, a retriever
    node EMBED_QUERY_MAX, a node with expect.text UNIT_JUDGE (judge=False: the efficiency wheel's Runs have no judge); None without nodes."""
    nodes = (graph or {}).get("nodes") or []
    if not nodes:
        return None
    usd = 0.0
    for n in nodes:
        kind = str(n.get("kind") or "")
        usd += UNIT_CHAT if kind.startswith("llm") else EMBED_QUERY_MAX if kind.startswith("retrieve") else 0.0
        if judge and ((n.get("expect") or {}).get("text")):
            usd += UNIT_JUDGE
    return usd


class Main:
    """The wheel's main line, driven from the diagnosis loop (Wheels.advance). `ops` gives the app's own paths:
    promote(pid, rid, run_id, by, reason) · register_subset(pid, base_suite_id, keep, rows) → {"id", "new"} (an existing suite of the
    same content is used as it is and never marked) ·
    start_suite_run(pid, rid, suite_id, tag) → run uid · run(uid) → Run."""
    judge_mem = 0.0                 # EV-062: the diagnosis's real judge calls on this server (no wheel row to record them in before a wheel exists)
    spend_lock = threading.Lock()   # record (wheel steps) and add_judge_spend (the diagnosis) add from different threads; one server, one Main

    def __init__(self, store, settings, ops, judge_model=lambda pid: None):
        self.store, self.settings, self.ops, self.judge_model = store, settings, ops, judge_model
        self.started = time.time()
        self.spent_mem = 0.0
        self.reserved = {}   # (wheel id, step key) → the careful estimate of a paid run that started and has not been recorded yet
        #                      (critic ML1-B2; per run since ML3-B2 — recording one run leaves the others' estimates in the total)
        self.base_lowered = set()   # wheel ids whose second baseline share came down to the measured unit (D-470 ③)
        self.doc = StartDoc(getattr(settings, "auto_start_hypotheses", ""))

    def on(self):
        """False only when the test harness says so (LLMOPS_FAKE_AUTO {"main": "off"} — isolated-env only): the browser checks keep the
        EV-030 wheel for their earlier scenes and turn the main line on for its own scene. Read on every pass."""
        path = getattr(self.settings, "fake_auto", "")
        if not path:
            return True
        try:
            return (json.loads(Path(path).read_text(encoding="utf-8")) or {}).get("main", "on") != "off"
        except (OSError, ValueError):
            return True

    # -------------------------------------------------------------- spend
    @property
    def real(self):
        return self.settings.mode == "real"

    def server_spent(self):
        """Every wheel's spend while this server runs: the recorded rows (never below the in-memory total — a deleted wheel cannot
        lower it) plus the estimates of paid steps that are still running (held)."""
        done = max(self.store.spend(since=self.started)["usd"] if self.store.auto_main_ready else 0.0, self.spent_mem)
        return done + self.judge_mem + self.held()

    def held(self):
        """D-687: the estimates of paid steps of wheels still open. A wheel can end with one held and nothing to record it (its base moved
        while the baseline ran): it leaves the totals then, on every way a wheel ends. A wheel no longer there holds nothing."""
        rows = list(self.reserved.items())
        open_ids = {wid for wid in {k[0] for k, _ in rows} if (w := self.store.wheel(wid)) and not w.get("ended_at")}
        return sum(usd for k, usd in rows if k[0] in open_ids)

    def settle_cut(self):
        """D-687: a wheel that ended while its baseline ran never reaches _record_finished. Once the Runs it started have ended, what they
        cost goes on the ledger, one line, and the estimate is dropped. Kept in memory like the estimate: a restart forgets it."""
        for k in [k for k in list(self.reserved) if k[1] == "baseline"]:
            w = self.store.wheel(k[0])
            if w and not w.get("ended_at"):
                continue
            state = self.store.wheel_state(k[0]) if w else {}
            runs = [r for u in (state.get("baseline") or state.get("baseline_started") or []) if (r := self.ops.run(u))]
            if any(r["status"] in ("대기", "실행 중") for r in runs):
                continue
            if not runs:
                self.reserved.pop(k, None)
                continue
            spent = [run_spend(r) for r in runs]
            base = self.store.release(w["base_release_id"]) or {}
            self.record(w, STEP_CUT, sum(c for c, _ in spent), sum(u for _, u in spent),
                        spent_note("추정 · " + " · ".join(used_models(base, self.judge_model(w["project_id"]))), runs), key="baseline")
            self.store.set_wheel_state(k[0], {**state, "spent": True})

    def add_judge_spend(self, usd):
        """EV-062 CHG-258: one real judge call of the automatic diagnosis, counted in the server total at once."""
        with self.spend_lock:
            self.judge_mem += usd

    def reserve(self, w, key, usd):
        """A paid run is about to start: its careful estimate counts in the server total until it is recorded (key = the step's run)."""
        if self.real:
            k = (w["wheel_id"], key)
            self.reserved[k] = self.reserved.get(k, 0.0) + usd

    def fixed_unit(self, w, rel=None):
        """D-492 ②: the wheel's careful unit a case before any Run — its base graph's kinds (kind_unit) × MEASURED_MARGIN, never above
        the kind's fixed unit (UNIT_CASE · UNIT_EFF_CASE), which stays when the graph is not there or shows no paid node."""
        fixed = UNIT_EFF_CASE if is_eff(w) else UNIT_CASE
        rel = rel if rel is not None else (self.store.release(w["base_release_id"]) or {})
        k = kind_unit(rel.get("graph"), judge=not is_eff(w))
        return min(fixed, MEASURED_MARGIN * k) if k else fixed

    def case_unit(self, w, state):
        """One case's careful estimate (D-470 ③ · D-446 덧 7): the wheel kind's fixed unit until its first baseline Run has ended whole,
        then that Run's own spend a case (run_spend — its visits and judged nodes) × MEASURED_MARGIN, never above the fixed unit; a Run
        with no paid visit recorded measures nothing (the fixed unit stays)."""
        fixed = self.fixed_unit(w)
        uids = (state or {}).get("baseline") or (state or {}).get("baseline_started") or []
        first = self.ops.run(uids[0]) if uids else None
        if not first or first.get("status") != "완료" or not first.get("cases"):
            return fixed
        usd = run_spend(first)[1]
        return min(fixed, MEASURED_MARGIN * usd / len(first["cases"])) if usd > 0 else fixed

    def record(self, w, step, calls, usd, model, key=None, tokens_in=0, tokens_out=0):
        if key is not None:
            self.reserved.pop((w["wheel_id"], key), None)
        if not self.real:
            return
        self.store.add_spend(w["wheel_id"], step, calls, tokens_in, tokens_out, usd, model)
        with self.spend_lock:
            self.spent_mem += usd

    def _index_budget(self, wid, state, plan=None, w=None):
        """EV-051 CHG-W2-09 · 17 ~ 20: why one more vector DB version may not be built now, else None — counted into the wheel's own
        state and saved before the build starts (a restart never passes the cap; a failed build counts too). A fake-embedding store
        builds fake on any server; a real one only on a real-mode server with LLMOPS_AUTO_INDEX_REAL=on and a spend cap, with a key
        name, a priced model and its reservation inside the wheel's 10 USD and the server cap."""
        plan = plan or {"mode": "fake"}
        real = plan.get("mode") == "real"
        if real and not (self.real and self.settings.auto_index_real and self.settings.auto_budget_usd is not None):
            return DROP_INDEX_REAL
        n = int(state.get("index_builds", 0))
        if n >= INDEX_CAP:
            return DROP_INDEX_CAP
        if real:
            if not plan.get("key_env"):
                return DROP_INDEX_NOKEY
            price = embed_price(plan.get("model"))
            if price is None:
                return STOP_EMBED_MODEL.format(name=plan.get("model"))
            est = plan.get("chars", 0) * plan.get("factor", 1.0) * INDEX_MARGIN * price
            used = float(state.get("index_usd", 0.0))
            if used + est > INDEX_USD_CAP:
                return DROP_INDEX_USD
            if self.over_cap(est, day=False):   # D-470 ⓓ: a vector DB build keeps its own caps (the wheel's 10 USD · the server's)
                return DROP_INDEX_SERVER.format(spent=self.server_spent(), cap=self.settings.auto_budget_usd)
            plan["estimate"], plan["key"] = est, f"index-{n + 1}"
            state["index_usd"] = used + est
            self.reserve(w, plan["key"], est)
        state["index_builds"] = n + 1
        self.store.set_wheel_state(wid, state)
        return None

    def _index_spent(self, w, state, plan, report):
        """EV-051 CHG-W2-20: a real build's spend — its counts when it finished, else its reservation (packrt reports no counts on a
        failure, money may be spent already). Fake builds cost nothing."""
        if plan.get("mode") != "real" or "estimate" not in plan:
            return
        est, price = plan["estimate"], embed_price(plan.get("model")) or 0.0
        report = report or {"failed": True}
        calls, tokens = int(report.get("embed_calls") or 0), int(report.get("embed_tokens") or 0)
        counted = max(tokens * price, calls * UNIT_EMBED, 0.0)
        if report.get("failed") or report.get("status") not in ("built", "exists"):
            usd = max(est, counted)   # the reservation, or the counts when a build failed after reporting more (slice review N-W23-1)
        else:
            usd = counted
        if usd != est:
            state["index_usd"] = float(state.get("index_usd", 0.0)) - est + usd
            self.store.set_wheel_state(w["wheel_id"], state)
        self.record(w, "⑤ 벡터 DB build", calls, usd, plan.get("model"), key=plan["key"], tokens_in=tokens)

    def struct_agent(self):
        """D-462 A: the efficiency wheel's ④ asks the structure candidate agent — a real-mode server with LLMOPS_AUTO_STRUCTURE=agent."""
        return self.real and getattr(self.settings, "auto_structure", "off") in ("agent", "agent-once")

    def day_spent(self):
        """D-470 ⓓ: every wheel's recorded spend since today's 00:00 (Asia/Seoul) plus the estimates of paid steps still running."""
        done = self.store.spend(since=day_start())["usd"] if self.store.auto_main_ready else 0.0
        return done + self.held()

    def over_cap(self, estimate, day=True):
        """The server cap, then (day=True: a wheel's paid step) the daily cap; the one reached is kept for cap_hold's words."""
        cap = self.settings.auto_budget_usd
        if cap is not None and self.server_spent() + estimate > cap:
            self.cap_hit = "server"
            return True
        daily = getattr(self.settings, "auto_daily_usd", None)
        if day and daily is not None and self.day_spent() + estimate > daily:
            self.cap_hit = "day"
            return True
        return False

    def cap_hold(self):
        """The hold's words for the cap over_cap last reached: a daily one waits for the next day (the step checks again every pass)."""
        if getattr(self, "cap_hit", None) == "day":
            return STOP_DAY.format(spent=self.day_spent(), cap=self.settings.auto_daily_usd)
        return STOP_CAP.format(spent=self.server_spent(), cap=self.settings.auto_budget_usd)

    # -------------------------------------------------------------- ⓪ the write gate
    def gate(self, w, n_cases, rel=None, first=True):
        """None when ③ may go on, else (cell_state, text, ended) — nothing is written but the wheel's own state. Checked on every pass
        before any write or paid step, also after a restart (a wheel that passed it on one server is checked again on the next —
        critic ML1-B1)."""
        if not self.store.auto_main_ready:
            return "waiting", HOLD_TABLES, False
        if self.store.backend == "pg" and not self.real:
            return "waiting", HOLD_FAKE_SHARED, False
        if not self.real:
            return None
        if self.settings.auto_budget_usd is None:
            return "waiting", HOLD_NO_CAP, False
        doc = self.doc.for_wheel(w)
        if not is_eff(w) and self.settings.auto_hypothesis != "agent" and doc is None:   # EV-069: the efficiency wheel's rules need no source
            return "done", END_NO_SOURCE.format(why=self.doc.why), True
        if rel is not None:
            unknown = [m for m in used_models(rel, self.judge_model(w["project_id"])) if not priced(m)]
            if unknown:
                return "stopped", STOP_MODEL.format(names=", ".join(unknown)), False
        # the first paid step (the two baseline Runs, reserved together — A2-B1) before it starts; every later step checks its own
        # estimate, a case at what the first Run spent (D-470 ③ · D-446 덧 7 — the whole wheel at the fixed unit stopped an
        # embedding-only wheel at 10.5 USD for 0.0002)
        if first and self.over_cap(self.fixed_unit(w, rel) * n_cases * 2):
            return "stopped", self.cap_hold(), False
        return None

    # -------------------------------------------------------------- ③ dataset
    def _hold(self, w, state, cell_state, text, ended=False):
        if state.get("hold") == text and not ended:
            return
        self.store.wheel_log(w["wheel_id"], w.get("cell") or 3, text)
        if ended:
            self.store.wheel_update(w["wheel_id"], cell_state=cell_state, outcome=text, ended_at=time.time())
        else:
            self.store.wheel_update(w["wheel_id"], cell_state=cell_state)
            self.store.set_wheel_state(w["wheel_id"], {**state, "hold": text})

    def stop_wheel(self, w):
        """D-476 덧 20: a person's stop request, done by the worker before the wheel's next step — the finished paid step is recorded
        first, the wheel's Runs still going are cancelled (up to STOP_WAIT_S, each cancelled one's spend a 'Run(취소)' line; one not
        ended by then is left to the server, '취소 요청'), the open rows close as stopped, the estimates leave the server total and the
        wheel ends: done · '끝 · 사람이 멈춤(<by>)'. Nothing is removed — log, rows, ledger, versions stay; no decision row (Q-2 (나))."""
        if w is None or w.get("ended_at"):
            return
        wid = w["wheel_id"]
        req = self.store.wheel_stop(wid) or {}
        by = req.get("by") or "?"
        state = self.store.wheel_state(wid)
        self._record_finished(w, state)
        rows = self.store.candidates(wid)
        going = [u for u in dict.fromkeys(_run_ids([state, rows])) if self._going(w, u)]
        for u in going:
            getattr(self.ops, "cancel_run", lambda _u: False)(u)
        until = time.monotonic() + STOP_WAIT_S
        left = list(going)
        while left and time.monotonic() < until:
            time.sleep(STOP_POLL_S)
            left = [u for u in left if self._going(w, u)]
        if going:
            base = self.store.release(w["base_release_id"]) or {}
            note = "추정 · " + " · ".join(used_models(base, self.judge_model(w["project_id"])))
            for u in going:
                if u not in left:
                    calls, usd = run_spend(self._any_run(u) or {})
                    self.record(w, STEP_CANCELLED, calls, usd, spent_note(note, [self._any_run(u)]))
        if left:
            self.store.wheel_log(wid, w.get("cell") or 3, STOP_LEFT.format(n=len(left)))
        for r in rows:
            if not r.get("outcome"):
                self.store.candidate_update(r["candidate_id"], outcome=ROW_STOPPED)
        for k in [k for k in self.reserved if k[0] == wid]:
            self.reserved.pop(k, None)
        cell = w.get("cell") or 3
        why = (req.get("reason") or "").strip()
        self.store.wheel_log(wid, cell, STOPPED_BY.format(by=by, step=autocycle.CELLS[cell - 1], reason=f" · {why}" if why else ""))
        self.store.wheel_update(wid, cell_state="done", outcome=END_HUMAN_STOP.format(by=by), ended_at=time.time())

    def _any_run(self, uid):
        """An ordinary Run (run-…) or a row run in memory (cand-…, the shadow store)."""
        if str(uid).startswith("cand-"):
            return getattr(self.ops, "shadow_run", lambda _u: None)(uid)
        return self.ops.run(uid)

    def _going(self, w, uid):
        """The wheel's own Run still waiting or running (an ordinary Run carries the wheel's 'auto' mark; a row run is the wheel's)."""
        r = self._any_run(uid)
        if not r or r.get("status") not in ("대기", "실행 중"):
            return False
        return str(uid).startswith("cand-") or (r.get("auto") or {}).get("wheel") == w["seq"]

    def dataset(self, w, rel):
        wid, pid, rid = w["wheel_id"], w["project_id"], w["base_release_id"]
        state = self.store.wheel_state(wid)
        near = [r["id"] for r in kglink.joined_of(self.settings.data_dir, self.store.releases(pid), rel)] if getattr(self.settings, "kg_link", None) else []
        base_run = base_suite_run(self.store, w, near)
        base = self.store.suite(base_run["input"]["id"]) if base_run else None
        if is_eff(w):   # EV-069 CHG-EF-3: the work suite chosen at the start
            base = self.store.suite(eff_of(w).get("work_suite") or "")
        self._record_finished(w, state)   # a finished paid step is recorded before anything can stop the wheel (review 2 B1)
        g = self.gate(w, len(base["cases"]) if base else 0, rel, first="suites" not in state)   # every pass (critic ML1-B1)
        if g:
            return self._hold(w, state, *g)
        if not base:
            return self._hold(w, state, "done", END_NO_SUITE, True)
        if state.get("hold") or not state.get("gate_ok"):
            state = {k: v for k, v in state.items() if k != "hold"}
            state["gate_ok"] = True
            self.store.wheel_update(wid, cell_state="running")
            self.store.set_wheel_state(wid, state)
        if "base_index" not in state:   # EV-051 CHG-W2-10: the base runs on packrt-made versions — checked once per wheel
            try:
                miss = getattr(self.ops, "base_index_missing", lambda r: [])(rel)
            except Exception as e:  # noqa: BLE001 - a probe error or timeout ends the wheel with its reason
                return self._hold(w, state, "done", END_INDEX_CHECK.format(why=str(e)[:120]), True)
            if miss:
                return self._hold(w, state, "done", END_NO_INDEX.format(stores=" · ".join(sorted({m["store"] for m in miss}))), True)
            state["base_index"] = "ok"
            self.store.set_wheel_state(wid, state)
        if "suites" not in state:
            self._make_suites(w, rel, base, state)
            return
        if "baseline" not in state:
            self._start_baseline(w, state)
            return
        self._finish(w, state)

    def _make_suites(self, w, rel, base, state):
        wid, pid, rid = w["wheel_id"], w["project_id"], w["base_release_id"]
        if is_eff(w):   # EV-069 CHG-EF-3: the chosen suite as it is (지킬 = 전부) · no case put up · no hidden suite · the confirm suite
            conf = self.store.suite(eff_of(w).get("confirm_suite") or "")
            if not conf or conf.get("project_id") != pid or conf["id"] == base["id"]:
                return self._hold(w, state, "done", END_NO_CONFIRM, True)
            seen = {input_sha(c.get("input")) for c in base["cases"]}
            n = sum(input_sha(c.get("input")) in seen for c in conf["cases"])
            if n:
                return self._hold(w, state, "done", END_OVERLAP.format(n=n), True)
            state.update(suites={"base": base["id"], "work": base["id"], "hidden": None, "confirm": conf["id"]}, marked=[], sets={}, doc=None)
            self.store.set_wheel_state(wid, state)
            self.store.wheel_log(wid, 3, f"작업 suite {base['name']}(지킬 = 전부) · 확인 suite {conf['name']}")
            return
        doc = self.doc.for_wheel(w)
        ret = retriever_node(doc, rel, base) if doc else None   # EV-043 CHG-211: a retriever wheel has a confirm suite and no hidden cases
        conf = self.store.suite(str(doc.get("confirm_suite") or "")) if ret and doc.get("confirm_suite") else None
        if ret and (not conf or conf.get("project_id") != pid or conf["id"] == base["id"]):
            return self._hold(w, state, "done", END_NO_CONFIRM, True)
        labels = {}
        for lb in self.store.labels(pid, rid):
            labels[lb["run_id"]] = lb["label"]
        # D-502 (2): what a wheel that ended at ③ put up is left to the next wheel — used again, not skipped as already put up
        again = left_at_dataset(self.store.wheels(pid), lambda x: self.store.wheel_state(x), w)
        done = {x["source_run_id"] for x in self.store.promotions(pid) if x["promotion_id"] not in again}
        reuse = {x["source_run_id"]: x for x in self.store.promotions(pid) if x["promotion_id"] in again}
        promoted = [p for p in self.store.promotions(pid) if p["promotion_id"] in state.get("promoted", [])]   # a retry keeps what went up
        rows = [d for d in self.store.diagnoses(pid, rid) if d.get("pack_revision") == w["base_revision"] and d.get("status") == "done"]
        # EV-065 CHG-263 · 266 (D-412 decision 1): only the Runs a person checked go up — the input is the person-check diagnosis rows
        # ('check-1:'): an app record's target Run or a Run whose latest label is ✗ (EV-064 CHG-CF-5b); a diagnosis signal alone does not
        checks = [d for d in rows if autodiag.is_check(d) and not d["rubric_version"].endswith(autodiag.SOLVED_KEY)]
        checked = person_checked(checks)
        fails = list({d["run_id"]: d for d in checks}.values())
        by = f"자동(바퀴 A-{w['seq']})"
        for d in fails:
            if d["run_id"] in done or labels.get(d["run_id"]) in autocycle_ok_labels():   # a person's later ✓ in LLMOps wins (EV-064)
                continue
            if d["run_id"] in reuse:
                if reuse[d["run_id"]]["promotion_id"] not in [x["promotion_id"] for x in promoted]:
                    promoted.append(reuse[d["run_id"]])
                    state["promoted"] = [x["promotion_id"] for x in promoted]
                    self.store.set_wheel_state(wid, state)
                done.add(d["run_id"])
                continue
            node = autodiag.check_node(d) or d.get("suspect_node")
            why = "사람 체크" + (f" — 의심 node {node}" if node else "") if d["run_id"] in checked else f"사람 ✗ — {labels.get(d['run_id'])}"
            try:
                p = self.ops.promote(pid, rid, d["run_id"], by, why)
            except Exception as e:   # noqa: BLE001 - one Run that cannot be put up (no raw value · logDB down) is skipped with its reason
                self.store.wheel_log(wid, 3, f"올리지 못함 {d['run_id']} — {str(getattr(e, 'detail', e))[:120]}")
                continue
            done.add(d["run_id"])
            promoted.append(p)
            state["promoted"] = [x["promotion_id"] for x in promoted]
            self.store.set_wheel_state(wid, state)
        # EV-065 CHG-266 · 266a: an app record's field-truth expected value goes with its Run's case, inline in the work suite (refs only);
        # a wheel started by app records none of which has one stops here (a label-only wheel keeps EV-064's way: labels carry none)
        truth = {}
        node_ids = {n.get("id") or n.get("name") for n in (rel.get("graph") or {}).get("nodes") or []}
        gdir, missing, bad = self.settings.field_gold_dir, set(), list(state.get("field_bad") or [])
        for d in checks:
            ck = (d.get("ratio_result") or {}).get("check") or {}
            # D-444 #11: the hand-over file first (D-413), then EV-066 CHG-272 (D-419 (1)): the record's eval line, else the start
            # document's rule over the record's refs
            exp, rk = None, check_record_key(d)
            if gdir and rk and d["run_id"] in checked:
                exp, why = autodiag.field_gold(gdir, rk, node_ids)
                if why and rk not in bad:
                    bad.append(rk)
                    self.store.wheel_log(wid, 3, f"현장 정답 파일 못 씀 — {rk}: {why}")
            exp = exp or ck.get("expected") or autodiag.rule_expected((doc or {}).get("field_truth"), ck.get("field_ref"), node_ids)
            if exp and d["run_id"] in checked:
                truth.setdefault(d["run_id"], exp)
            elif gdir and rk and d["run_id"] in checked and not (Path(gdir) / f"{rk}.json").exists():
                missing.add(rk)
        # D-498 (E1 105): a 헛걸음 · 원인 다름 · 파트장 수정 check also wants the Run it names to have chosen the confirmed section — one
        # final check (the Pack's chosen_check = the record's confirmed_sections[0]) added to that Run's truth, nothing else changed
        sig, wasted, bare, cause = {}, {}, set(), False
        for d in checks:
            ck = (d.get("ratio_result") or {}).get("check") or {}
            sec = ((ck.get("field_ref") or {}).get("confirmed_sections") or [None])[0]
            kinds = set(ck.get("kinds") or [ck.get("kind")])   # every kind the record gave this Run (one check row per record/Run)
            if ck.get("start") and kinds & set(SIGNAL_TRUTH_KINDS) and d["run_id"] in checked:   # D-546 덧 23 ②: no chosen-check line here
                ex = (ck.get("field_ref") or {}).get("exception_wasted") or []
                if "헛걸음" in kinds and ex:
                    wasted.setdefault(d["run_id"], set()).update(ex)
                else:
                    bare.add(d["run_id"])
            elif kinds & set(SIGNAL_TRUTH_KINDS) and isinstance(sec, str) and sec and d["run_id"] in checked:
                sig.setdefault(d["run_id"], (sec, set()))[1].update(kinds & set(SIGNAL_TRUTH_KINDS))
            cause = cause or (SIGNAL_CAUSE_KIND in kinds and bool((ck.get("field_ref") or {}).get("confirmed_cause")) and d["run_id"] in checked)
        if cause and not state.get("signal_cause"):
            state["signal_cause"] = SIGNAL_CAUSE_NONE
            self.store.wheel_log(wid, 3, SIGNAL_CAUSE_NONE)

        def final_path(spec):   # a Pack case's final is its entrypoint output value (runs.pack_text), so "<output state>.<path>" is read as <path> there
            st_name, rest = spec.split(".", 1)
            return rest if st_name == (rel.get("graph") or {}).get("output_field") else spec

        def add_final(run_id, chks):
            exp = dict(truth.get(run_id) or {})
            exp["final"] = list(exp.get("final") or []) + [c for c in chks if c not in (exp.get("final") or [])]
            truth[run_id] = exp
        if sig:
            chosen = getattr(self.ops, "chosen_spec", lambda rid: None)(w["base_release_id"])
            if chosen:
                path = final_path(chosen)
                for run_id, (sec, _) in sig.items():
                    add_final(run_id, [{"path": path, "equals": sec}])
            said = SIGNAL_TRUTH.format(kinds=" · ".join(k for k in SIGNAL_TRUTH_KINDS if any(k in ks for _, ks in sig.values())),
                                       secs=" · ".join(sorted({s for s, _ in sig.values()}))) if chosen else SIGNAL_TRUTH_NONE
            if state.get("signal_truth") != said:
                state["signal_truth"] = said
                self.store.wheel_log(wid, 3, said)
        if wasted or bare:
            spec = getattr(self.ops, "exception_spec", lambda rid: None)(w["base_release_id"]) if wasted else None
            if spec:
                for run_id, secs in wasted.items():
                    add_final(run_id, [{"path": final_path(spec), "not_equals": s} for s in sorted(secs)])
            said = " · ".join(([EXC_TRUTH.format(secs=" · ".join(sorted(set().union(*wasted.values())))) if spec else EXC_TRUTH_NONE] if wasted else []) +
                              ([EXC_START_NONE.format(n=len(bare))] if bare else []))
            if state.get("exception_truth") != said:
                state["exception_truth"] = said
                self.store.wheel_log(wid, 3, said)
        if bad != list(state.get("field_bad") or []):
            state["field_bad"] = bad
            self.store.set_wheel_state(wid, state)
        if missing:   # D-444 #11 (Q-3): the file comes after the record — wait up to FIELD_WAIT_S, then go on with what there is
            state.setdefault("field_wait", time.time())
            if time.time() - state["field_wait"] < FIELD_WAIT_S:
                self.store.set_wheel_state(wid, state)
                return self._hold(w, state, "waiting", f"{FIELD_WAIT} — 넘김 파일 {' · '.join(sorted(missing)[:3])} · 다음에 다시")
        if checked and not truth:
            return self._hold(w, state, "done", END_NO_FIELD_FILE if missing else END_NO_FIELD, True)
        sets, fix_rows, hidden_rows = {}, [], []
        two, not_two = [], []

        def checked_row(p, row):   # D-525: a person-checked judge Run's case runs its session's start → judge again
            got = getattr(self.ops, "session_start", lambda *a: None)(pid, rid, p["source_run_id"])
            if got is None:
                return row
            then, why = two_step(p["input"], got.get("input"), got.get("output")) if "why" not in got else (None, got["why"])
            if not then:
                not_two.append((row["case_id"], why))
                return row
            two.append(then)
            return {**row, "input": got["input"], "then": then}
        for i, p in enumerate(promoted):
            cid = f"op-{p['source_run_id']}"
            hid = i % 3 == 2 and not ret and p["source_run_id"] not in checked   # EV-065 CHG-265b: a checked Run stays in the fix set
            row = {"case_id": cid, "input": p["input"], **({"expected": truth[p["source_run_id"]]} if p["source_run_id"] in truth else {})}
            row = checked_row(p, row) if p["source_run_id"] in checked else row
            (hidden_rows if hid else fix_rows).append(row)
            sets[cid] = "hidden" if hid else "fix"
        bundle = {}
        if gdir:   # D-444 #11 (가, a promise change approved): the Project's earlier field lines join the fix set (the field bundle)
            lines = autodiag.field_gold_lines(gdir, node_ids)
            for p in self.store.promotions(pid):
                cid = f"op-{p['source_run_id']}"
                if p["source_run_id"] in lines and cid not in sets and cid not in bundle:
                    bundle[cid] = lines[p["source_run_id"]]
                    fix_rows.append(checked_row(p, {"case_id": cid, "input": p["input"], "expected": bundle[cid]}))
                    sets[cid] = "fix"
        ids = [c["id"] for c in base["cases"]]
        doc_sets = {str(k): v for k, v in ((doc or {}).get("sets") or {}).items() if not (ret and v == "hidden")}   # no hidden mark in a retriever wheel
        missing = [k for k in doc_sets if k not in ids]
        if missing:
            return self._hold(w, state, "done", f"끝 · 알림 — 시작 문서의 case가 기준 suite에 없음({', '.join(missing[:3])})", True)
        hidden_ids = [k for k in ids if doc_sets.get(k) == "hidden"]
        if ret:   # the confirm suite (test) shares no case input with the work suite (val + the cases put up)
            seen = {input_sha(c.get("input")) for c in conf["cases"]}
            n = sum(input_sha(x) in seen for x in [c.get("input") for c in base["cases"]] + [r["input"] for r in fix_rows])
            if n:
                return self._hold(w, state, "done", END_OVERLAP.format(n=n), True)
        try:   # a registered work suite is kept in the state, so a retry after a hidden-suite failure keeps its mark (review 2 N3 a)
            work = state.get("work_reg") or self.ops.register_subset(pid, base["id"], [k for k in ids if k not in hidden_ids], fix_rows)
            state["work_reg"] = work
            self.store.set_wheel_state(wid, state)
            hidden = self.ops.register_subset(pid, base["id"], hidden_ids, hidden_rows) if hidden_ids or hidden_rows else None
        except Exception as e:   # noqa: BLE001 - kept in the wheel's log; the next pass tries again with the same cases
            return self._hold(w, state, "waiting", f"평가 suite를 등록하지 못함 — {str(getattr(e, 'detail', e))[:120]} · 다음에 다시")
        sets.update(doc_sets)
        state.update(field={f"op-{r}": e for r, e in truth.items() if sets.get(f"op-{r}") == "fix"}, field_bundle=bundle,
                     suites={"base": base["id"], "work": work["id"], "hidden": hidden and hidden["id"], **({"confirm": conf["id"]} if ret else {})},
                     marked=[x["id"] for x in (work, hidden) if x and x["new"]], sets=sets, doc=self.doc.label() if doc else None, **({"retriever": ret} if ret else {}))
        work, hidden = work["id"], hidden and hidden["id"]
        self.store.set_wheel_state(wid, state)
        tag = lambda sid: (self.store.suite(sid) or {}).get("name", sid)
        self.store.wheel_log(wid, 3, f"평가 case 자동 올림 {len(promoted)}건(숨김 {len(hidden_rows)}) · 작업 suite {tag(work)}"
                                     + (f" · 숨긴 suite {tag(hidden)}" if hidden else "") + (f" · 확인 suite {tag(conf['id'])}(리트리버 {ret})" if ret else "")
                                     + (f" · 세트 = {state['doc']}" if doc else ""))
        if two:
            self.store.wheel_log(wid, 3, TWO_STEP.format(n=len(two), keys=" · ".join(dict.fromkeys(k for t in two for k in t["take"]))))
        for cid, why in not_two:
            self.store.wheel_log(wid, 3, TWO_STEP_NOT.format(cid=cid, why=why))

    def _start_baseline(self, w, state):
        wid = w["wheel_id"]
        work = self.store.suite(state["suites"]["work"])
        uids = list(state.get("baseline_started") or [])
        if self.real and uids:   # EV-060 CHG-249: in real mode the second Run starts after the first ended, on a later pass
            first = self.ops.run(uids[0])
            if first is None or first["status"] in ("대기", "실행 중"):
                return
        held = (wid, "baseline") in self.reserved
        # the first start reserves both Runs' shares; a second start finds them held unless the server started again — then it checks
        # and reserves what is left, as before (A4-B1); D-470 ③: once the first Run has ended, the second share comes down to what the
        # first spent a case (never up), and after a restart what is left is reserved at that
        n = len(work["cases"])
        if self.real and not (uids and held):
            estimate = self.case_unit(w, state) * n * (2 - len(uids))
            if self.over_cap(estimate):
                return self._hold(w, state, "stopped", self.cap_hold())
            self.reserve(w, "baseline", estimate)   # counted in the server total from now (critic ML1-B2)
            if uids:
                self.base_lowered.add(wid)
        elif self.real and wid not in self.base_lowered:
            fixed = self.fixed_unit(w)
            self.reserved[(wid, "baseline")] -= (fixed - self.case_unit(w, state)) * n
            self.base_lowered.add(wid)
        try:
            for k in range(len(uids) + 1, 3 if not self.real else len(uids) + 2):   # real mode: one start per pass (A4-N3)
                uids.append(self.ops.start_suite_run(w["project_id"], w["base_release_id"], work["id"],
                                                     {"wheel": w["seq"], "role": "baseline", "text": f"자동 · 바퀴 A-{w['seq']} · 흔들림 기준선 {k}/2"},
                                                     **({"judge": False} if is_eff(w) else {})))   # EV-069: no judge on the efficiency wheel's Runs
                state["baseline_started"] = uids
                self.store.set_wheel_state(wid, state)
        except Exception as e:   # noqa: BLE001 - e.g. a person's open edit of the 운영 version: the missing one starts on a later pass
            if self.real and not uids:   # a failed first start leaves nothing reserved; a failed second start keeps what it had (A3-B1)
                self.reserved[(wid, "baseline")] = 0.0
            return self._hold(w, state, "waiting", f"기준선 Run을 시작하지 못함({len(uids)} / 2) — {str(getattr(e, 'detail', e))[:120]} · 다음에 다시")
        if len(uids) < 2:
            return
        state = {k: v for k, v in state.items() if k not in ("hold", "baseline_started")}
        state["baseline"] = uids
        self.store.set_wheel_state(wid, state)
        self.store.wheel_update(wid, cell_state="running")
        self.store.wheel_log(wid, 3, f"흔들림 기준선 — 운영 버전으로 작업 suite {len(uids)}번 실행" + (f" · {ONE_AT_A_TIME}" if self.real else ""))

    def _record_finished(self, w, state):
        """Writes the baseline Runs' spend once, as soon as both have finished (also when the wheel is about to be held)."""
        if state.get("spent") or not state.get("baseline"):
            return
        runs = [self.ops.run(u) for u in state["baseline"]]
        if any(r is None or r["status"] in ("대기", "실행 중") for r in runs):
            return
        calls = usd = 0
        for r in runs:
            c, u = run_spend(r)
            calls, usd = calls + c, usd + u
        base = self.store.release(w["base_release_id"]) or {}
        self.record(w, "③ 흔들림 기준선", calls, usd, spent_note("추정 · " + " · ".join(used_models(base, self.judge_model(w["project_id"]))), runs), key="baseline")
        state["spent"] = True
        self.store.set_wheel_state(w["wheel_id"], state)

    def _finish(self, w, state):
        wid = w["wheel_id"]
        runs = [self.ops.run(u) for u in state["baseline"]]
        if any(r is None or r["status"] in ("대기", "실행 중") for r in runs):
            return
        self._record_finished(w, state)
        sets = dict(state.get("sets") or {})
        by_case, eff_ok = {}, {}
        for r in runs:
            of = autocand.on_fail_of(r.get("graph"))   # EV-089: the Run's rewrite branches
            for c, sc in zip(r["cases"], self.store.suite(state["suites"]["work"])["cases"]):
                by_case.setdefault(sc["id"], []).append(case_ok(c, of) and (is_eff(w) or autocand.final_ok(c, sc)))   # EV-079: accuracy reads final checks
                eff_ok.setdefault(sc["id"], []).append(case_ok(c, of) and autocand.final_ok(c, sc))
        for cid, oks in by_case.items():
            if cid not in sets:   # EV-069 CHG-EF-3: an efficiency wheel fixes nothing — every steady case is kept
                sets[cid] = "flaky" if any(oks) and not all(oks) else "keep" if is_eff(w) or all(oks) else "fix"
        state["sets"] = sets
        for role in ("work", "hidden"):   # only a suite this wheel registered carries its marks (a person's suite stays as it is)
            if state["suites"].get(role) and state["suites"][role] in state.get("marked", []):
                self.store.set_suite_auto(state["suites"][role], {"wheel": w["seq"], "role": role, "sets": sets})
        self.store.set_wheel_state(wid, state)
        count = {v: sum(1 for x in sets.values() if x == v) for v in DOC_SETS}
        self.store.wheel_log(wid, 3, "③ 끝 — " + " · ".join(f"{DOC_SETS[k]} {count[k]}" for k in DOC_SETS if k != "excluded" or count.get(k)))   # 셈 밖 only when there is one
        if not count["fix"] and not is_eff(w):
            self.store.wheel_log(wid, 3, END_NO_FIX)
            self.store.wheel_update(wid, cell_state="done", outcome=END_NO_FIX, ended_at=time.time())
            return
        if is_eff(w) and not any(v == "keep" and all(eff_ok.get(c) or [False]) for c, v in sets.items()):
            self.store.wheel_log(wid, 3, END_EFF_NO_BASE)   # '지킬 망가짐 0' would measure nothing (041 Q-S4-1, L1)
            self.store.wheel_update(wid, cell_state="done", outcome=END_EFF_NO_BASE, ended_at=time.time())
            return
        self.store.wheel_update(wid, cell=4, cell_state="running")   # EV-035 ML-2: ④ goes on (책임 node · 가설)
        self.store.wheel_log(wid, 4, "④ 가설 — 구조 가설(규칙)" if is_eff(w) else "④ 가설 — 책임 node 찾는 중")


def autocycle_ok_labels():
    from .autodiag import OK_LABELS
    return OK_LABELS


# ---------------------------------------------------------------- ④ the responsible node and the hypotheses (ML-2, CHG-181)
HOLD_5 = "⑤ 후보 표 — 다음 단계(ML-3)에서"
END_AGENT_OFF = "끝 · 알림 — 가설 에이전트 꺼짐"
END_NO_HYP = "끝 · 알림 — 반복 {m}번 뒤에도 시험할 가설 없음"
# D-513: a change tried once — in this wheel or a finished one on the same base version and base suite — is never tried again
DROP_TRIED = "버림 — 앞 반복에서 시험한 가설"
DROP_SAME_ANSWER = "버림 — 같은 답에 겹친 가설"
DROP_EARLIER = "버림 — 지난 자동 개선 {seq}에서 시험한 가설"
DROP_ROW_TRIED = "버림 — 앞에서 시험한 변경 묶음"
DROP_ROW_EARLIER = "버림 — 지난 자동 개선 {seq}에서 시험한 변경 묶음"
REPEATS = (DROP_TRIED, DROP_SAME_ANSWER, DROP_EARLIER.split("{")[0])
DUP_LINE = "반복 {it} · 이미 시험한 가설 {n}개 버림 — {parts}"
EARLY_END = "반복 {it}에서 새 가설 없음 — 반복 상한 {m} 전에 끝(이미 시험한 가설 {n}개 버림)"
END_NO_NEW = "끝 · 알림 — 반복 {it}에서 새 가설 없음(이미 시험한 가설 {n}개 버림)"
RESP_RET = "책임 node {node} — 시작 문서 가설의 리트리버 node(리트리버 바퀴)"
RESP_CHECK = "책임 node {node} — 사람 체크가 가리킨 node"   # EV-065 CHG-265b ③
RESP_DEV = "책임 node {node} — 실행 기록에서 처음 어긋난 node · {why}"   # D-549 덧 3 A ①: the Pack's declaration (label file 'diagnose'), cost 0
RESP_PROBE = "실행 기록으로 판정 안 남(Pack 선언 기준 · 고칠 case {n}개) — 노드 하나씩 바꿔 봄: {names}(벡터 DB 다시 만드는 옵션 · AI 부르는 노드 뺌)"   # A ③
RESP_PROBE_NONE = "실행 기록으로 판정 안 남 · 하나씩 바꿔 볼 노드도 없음 — 사람 체크 · judge 사슬로"
PROBE_NODE = "반복 {it} — 하나씩 바꿔 보는 노드 {node}({k}/{n})"


def probe_nodes(case, nodes):
    """A ③: the nodes to try one at a time when the record gives no verdict — in the Run's node order, each with an option left after
    the vector-DB rebuild options are taken out, never a node that calls a model (llm)."""
    out = []
    for n in dict.fromkeys(v.get("node") for v in autodeviate.case_visits(case)):   # A-3: a two-step case's first step too
        node = nodes.get(n)
        if node and autohyp.kind_of(node) != "llm" and any(not k.startswith("index.") for k in autohyp.knobs_of(node)):
            out.append(n)
    return out
NO_FIX_NODE = "고칠 수 있는 노드 없음 — 책임 node {node}에 바꿀 수 있는 옵션이 없음 → 사람에게"   # D-549 덧 3 A-2
COVERING = "덮는 고침 — 책임 node {node} 뒤의 {nodes}만 고친 줄(앞 node 고침이 모두 떨어질 때만 1등)"   # D-549 덧 3 A-2
RESP_CHECKED = " · 사람이 체크한 case {n}개가 가리킴(고칠 case {m}개 다수결은 {most})"   # D-506
MIX_NOT_RET = "버림 — 리트리버 바퀴에서 리트리버 줄이 아님"
MIX_NO_CONFIRM = "버림 — 확인 suite가 없는 바퀴의 리트리버 줄"


def _harness(settings):
    path = getattr(settings, "fake_auto", "")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) or {} if path else {}
    except (OSError, ValueError):
        return {}


def person_checked(rows):
    """EV-065 CHG-263 · 266: the Runs a person's app record checked — their check rows, not a ✗ label's or a solved record's."""
    return {d["run_id"] for d in rows if autodiag.is_check(d) and not d["rubric_version"].endswith(autodiag.SOLVED_KEY)
            and not d["rubric_version"].startswith(autodiag.CHECK_RUBRIC + "label-")}


def check_error(e):
    """D-525 덧 31 (122-1): a refused structure check as a person reads it after '검사 ' — an error list by its messages, a one-line
    reason (ValueError · HTTPException detail) as it is, anything else '실패(<exception name>)': no Python text on screen."""
    got = getattr(e, "detail", None) if hasattr(e, "detail") else e.args[0] if isinstance(e, ValueError) and len(e.args) == 1 else None
    if isinstance(got, list):
        msgs = [str(x["message"]) for x in got if isinstance(x, dict) and x.get("message")]
        if msgs and len(msgs) == len(got):
            return "; ".join(msgs)[:120]
    elif isinstance(got, str) and got:
        return got[:120]
    return f"실패({type(e).__name__})"


def checked_cheap(ids, fix, checked, base_run, names):
    """D-525 덧 34 (A-24): the person-checked fix cases whose baseline case passed a node the row changes (either step) — they always go
    to its cheap test, whatever the hypotheses predicted (the agent named seven other cases for the checked case's retriever: 0/7)."""
    by = dict(zip(ids, (base_run or {}).get("cases") or []))
    path = lambda c: {v.get("node") for s in [c, *[x or {} for x in c.get("steps") or []]] for v in s.get("visits") or []}   # noqa: E731
    return [cid for cid in ids if cid in checked and cid in fix and set(names) & path(by.get(cid) or {})]


def checked_cases(store, pid, w):
    """The work suite's case ids of the Runs a person checked on the wheel's base revision (op-<run id>)."""
    return {f"op-{r}" for r in person_checked(d for d in store.diagnoses(pid, w["base_release_id"])
                                              if d.get("pack_revision") == w["base_revision"] and d.get("status") == "done")}


def dropped_detail(h, nodes):
    """D-546 덧 23 ③ (143): a dropped hypothesis row keeps the option's name and limits too — the table read the raw knob
    (call_threshold) where the kept rows of the same option read by the Pack's label."""
    node = nodes.get(h["node"]) if isinstance(h.get("node"), str) else None
    opt = autohyp.option_view(node, h["knob"]) if node and isinstance(h.get("knob"), str) else None
    return {"predicts": h.get("predicts") or [], "why": h.get("why"), **({"option": opt} if opt else {})}


def lead_node(per, checked):
    """D-506 (A-17): the node the person-checked fix cases point to comes before the majority of all fix cases — their own majority, a
    tie going to the node more fix cases name, then the first named; no checked case among them → the majority of all, as before.
    D-506 덧 2: a tie of all goes to the node named first in the fix cases (a set's order changed with each process's hash seed)."""
    names = [p["node"] for p in per]
    mine = [p["node"] for p in per if p["case"] in checked]
    if not mine:
        return max(dict.fromkeys(names), key=names.count) if names else None
    return max(dict.fromkeys(mine), key=lambda n: (mine.count(n), names.count(n)))


def judge_spend(spent, r, asked):
    """D-506 (packrt-1 102): one judge_node call into the step's line — its own tokens × the price list when the reply gave usage and a
    priced model (the served one first), else UNIT_JUDGE_NODE counted as an estimate."""
    from .autocand import chat_price
    model = r.get("served_model") or r.get("model") or asked
    if isinstance(r.get("usd"), (int, float)):   # D-568 B1: a Jev judge gives its own price (decide@1 tokens × Jev's price)
        u = r.get("usage") or {}
        spent["usd"] += r["usd"]
        spent["in"] += int(u.get("input_tokens") or 0)
        spent["out"] += int(u.get("output_tokens") or 0)
        spent["model"] = spent["model"] or model
        return
    usd, tin, tout = autohyp.agent_cost(model, r.get("usage"))
    if usd is None or chat_price(model) is None:
        usd, tin, tout = UNIT_JUDGE_NODE, 0, 0
        spent["est"] += 1
    spent["usd"] += usd
    spent["in"] += tin
    spent["out"] += tout
    spent["model"] = spent["model"] or model


def diagnosis_of(resp):
    """D-549 덧 3 0단계: ④'s diagnosis read as 'target kind + target + evidence' — today the target is always a node (the state's resp:
    a start document's retriever, the node a person's check names, or the judged chain). dev-2's relation diagnosis (the relation a Run
    used, its graph.used) is meant to come in as target kind "relation" beside it."""
    if not resp:
        return None
    return {"target_kind": "node", "target": resp.get("node"), "decided": bool(resp.get("decided")),
            "evidence": {"basis": resp.get("basis"), "signal": resp.get("signal"), "cases": [p.get("case") for p in resp.get("per_case") or []],
                         **({"text": resp["evidence"]} if resp.get("evidence") else {})}}


def covering(row, resp):
    """D-549 덧 3 A-2: a row is a covering fix when ④ read the responsible node from the record (basis 'deviation') and the row changes
    only nodes after it in the Run's node order — it hides the front node's miss instead of fixing it."""
    if not resp or resp.get("basis") != "deviation":
        return False
    order, rn = resp.get("order") or [], resp.get("node")
    touched = {ln.get("node") for ln in row.get("lines") or [] if ln.get("node")}
    if not touched or rn in touched or rn not in order:
        return False
    return all(n in order and order.index(n) > order.index(rn) for n in touched)


def covering_last(rows, resp):
    """A-2: the table's rows in their rank order, the covering fixes moved after every other row (their own order kept) — one comes up
    only when every row on the responsible node or before it failed the criteria."""
    return sorted(rows, key=lambda r: covering(r, resp))


def _find_responsible(self, w, state, case_ids, fix, run, nodes, harness):
    """④'s responsible node, once a wheel (state['resp']): the start document's retriever, else the node the people's checks of the fix
    cases name, else the judged chain of each failing fix case. → None to go on, or (True, the hold's result) when ④ stops here."""
    wid, pid = w["wheel_id"], w["project_id"]
    if "resp" not in state and state.get("retriever"):   # EV-043 CHG-211: a missed gold position is no failed check — the document's retriever node
        rn = state["retriever"]
        state["resp"] = {"node": rn, "decided": True, "signal": None, "basis": "retriever",
                         "per_case": [{"case": cid, "node": rn, "signal": None, "decided": True, "rows": []} for cid in case_ids if cid in fix]}
        self.store.set_wheel_state(wid, state)
        self.store.wheel_log(wid, 4, RESP_RET.format(node=rn))
    spec = getattr(self.ops, "diagnose_spec", lambda rid: None)(w["base_release_id"]) if "resp" not in state else None
    if spec:   # D-549 덧 3 A ①: the first node of each fix case's Run that deviates by the Pack's declaration (cost 0)
        expect, out_field = _expect(self, state, w) or {}, (run.get("graph") or {}).get("output_field")
        per, orders = [], {}
        for cid, c in zip(case_ids, run["cases"]):
            gold = autodeviate.gold_cause((expect.get(cid) or {}).get("final"), spec, out_field) if cid in fix else None
            r = autodeviate.first_deviation(c, spec, gold) if gold else None
            if r and r["node"]:
                per.append({"case": cid, "node": r["node"], "signal": None, "decided": True, "rows": r["rows"], "why": r["why"]})
                orders[cid] = r["order"]
        if per:
            rn = lead_node(per, checked_cases(self.store, pid, w))
            lead = next(p for p in per if p["node"] == rn)
            state["resp"] = {"node": rn, "decided": True, "signal": None, "basis": "deviation", "evidence": lead["why"], "order": orders[lead["case"]], "per_case": per}
            self.store.set_wheel_state(wid, state)
            self.store.wheel_log(wid, 4, RESP_DEV.format(node=rn, why=lead["why"]))
        else:   # A ③: no verdict from the record — the nodes one at a time (an iteration each), else the ways before
            first = next((c for cid, c in zip(case_ids, run["cases"]) if cid in fix), None)
            probe = probe_nodes(first, nodes)
            if probe:
                state["resp"] = {"node": probe[0], "decided": False, "signal": None, "basis": "probe", "probe": probe, "per_case": []}
                self.store.set_wheel_state(wid, state)
                self.store.wheel_log(wid, 4, RESP_PROBE.format(n=len(fix), names=" → ".join(probe)))
            elif not state.get("dev_none"):
                state["dev_none"] = True
                self.store.set_wheel_state(wid, state)
                self.store.wheel_log(wid, 4, RESP_PROBE_NONE)
    if "resp" not in state:   # EV-065 CHG-265b ③: the node the person's checks of the fix cases name (calibrated judge ② after 10-29)
        hints = {f"op-{d['run_id']}": autodiag.check_node(d) for d in self.store.diagnoses(pid, w["base_release_id"])
                 if autodiag.is_check(d) and d["status"] == "done" and d.get("pack_revision") == w["base_revision"] and autodiag.check_node(d)}
        per = [{"case": cid, "node": hints[cid], "signal": None, "decided": True, "rows": []} for cid in case_ids if cid in fix and cid in hints]
        if per:
            rn = lead_node(per, set())   # D-506 덧 2: a tie to the node named first
            state["resp"] = {"node": rn, "decided": True, "signal": None, "basis": "check", "per_case": per}
            self.store.set_wheel_state(wid, state)
            self.store.wheel_log(wid, 4, RESP_CHECK.format(node=rn))
    if "resp" not in state:
        per = []
        if self.real:
            n_judge = sum(len(autohyp.chain(c, nodes, autohyp.signal_node(c, autocand.on_fail_of(run.get("graph"))) or "")) for cid, c in zip(case_ids, run["cases"]) if cid in fix)
            if self.over_cap(UNIT_JUDGE_NODE * n_judge):
                return True, self._hold(w, state, "stopped", self.cap_hold())
        calls, spent = 0, {"usd": 0.0, "in": 0, "out": 0, "est": 0, "model": None}

        def judge(node, visit):
            nonlocal calls
            if visit is None:
                return None
            if self.settings.mode == "fake":
                v = (harness.get("judge_nodes") or {}).get(node)
                return None if v is None else bool(v)
            text = ((nodes.get(node) or {}).get("expect") or {}).get("text")
            if not text:
                return None
            calls += 1
            r = self.ops.judge_node(pid, text, visit.get("input"), visit.get("output"), **gatewaymod.node_kw(self.settings, node))   # D-604
            judge_spend(spent, r if isinstance(r, dict) else {}, self.judge_model(pid))
            return (r.get("score", 0) >= 0.5) if isinstance(r, dict) else None
        expect = _expect(self, state, w) or {}
        for cid, c in zip(case_ids, run["cases"]):
            if cid in fix:
                r = autohyp.responsible(c, nodes, judge)
                if not r and (expect.get(cid) or {}).get("final"):   # EV-079 (A-8): a case failing only its final (order) checks
                    src, sig = autohyp.final_signal(c, expect[cid]["final"], nodes)
                    r = autohyp.responsible(src, nodes, judge, signal=sig) if sig else None
                if r:
                    per.append({"case": cid, **r})
        if self.real:
            m = str(spent["model"] or self.judge_model(pid))
            note = m if not spent["est"] else "추정 · " + m + (MEASURED_PART if spent["est"] < calls else "")
            self.record(w, "④ 책임 node", calls, spent["usd"], note, tokens_in=spent["in"], tokens_out=spent["out"])
        checked = checked_cases(self.store, pid, w)
        node, most = lead_node(per, checked), lead_node(per, set())
        decided = any(p["decided"] for p in per)
        lead = next((p for p in per if p["node"] == node and p["case"] in checked), per[0] if per else None)
        state["resp"] = {"node": node, "decided": decided, "signal": lead["signal"] if lead else None, "per_case": per}
        self.store.set_wheel_state(wid, state)
        why = RESP_CHECKED.format(n=sum(p["case"] in checked for p in per), m=len(per), most=most) if node != most else ""
        self.store.wheel_log(wid, 4, ((f"책임 node {node}" if decided else autohyp.UNDECIDED.format(node=node)) + why) if node else "책임 node 없음 — 고칠 case에 실패한 검사가 없음")
        if not node:
            return True, self._hold(w, state, "done", END_NO_HYP.format(m=w["iteration"]), True)
    return None


def grid_cases(ids, visible, base_run, name):
    """D-549 B: the shown cases (fix · keep) whose baseline case passed the node (either step) — what each combination's cheap test runs."""
    by = dict(zip(ids, (base_run or {}).get("cases") or []))
    path = lambda c: {v.get("node") for s in [c, *[x or {} for x in c.get("steps") or []]] for v in s.get("visits") or []}   # noqa: E731
    return [cid for cid in ids if cid in visible and name in path(by.get(cid) or {})]


def _grid_hypotheses(self, w, state, rel, node, ids, visible, run, seen):
    """D-549 B: ④'s option bundles of the node's small space (autogrid), once a node a wheel — each combination one hypothesis (kind
    'bundle', source 'grid'), all in the first batch; None when the space is not tried whole (said why) and ④ goes the way before.
    Only for a node read from the Run record (A: the first deviation · one node an iteration) — there the fix is that node's options
    alone (A ②); a node a person's check names or a judged chain also offers the nodes before it, no one node's space.
    seen: the ops keys tried before (this wheel · finished wheels on the same base) — such a combination is left out."""
    wid = w["wheel_id"]
    cap = self.settings.auto_grid_max
    if node is None or cap <= 0 or state.get("retriever") or not autohyp.HYP_KINDS["option"]["exhaustive"]:
        return None
    done = state.get("grid_done") or []
    if node["name"] in done:
        return None
    state["grid_done"] = done + [node["name"]]
    self.store.set_wheel_state(wid, state)
    limits = autohyp.pack_limits((rel.get("pack") or {}).get("copy") or "")
    combos = autogrid.space(node, autogrid.knobs(node, limits), limits)
    if not combos:
        return None
    graph_id = (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    cases = grid_cases(ids, visible, run, node["name"])
    if not cases:   # no shown case passed the node: nothing a cheap test could count
        return None
    hyps = []
    for combo in combos:
        h = {"kind": "bundle", "node": node["name"], "knob": autogrid.knob_text(combo), "value": combo, "predicts": cases, "source": "grid", "_grid": True}
        if ops_key(autohyp.ops_of(h, node, graph_id, autohyp.prompt_text_of(rel, node))) not in seen:
            hyps.append(h)
    why = autogrid.small(len(hyps), len(cases), cap)
    if why:
        self.store.wheel_log(wid, 4, why)
        return _screen(self, w, state, rel, node, hyps, ids, cases, run, graph_id, cap)   # D-549 덧 13: a large space, fast, when no model is on the way
    if self.real and self.over_cap(UNIT_SHORT * len(hyps) * len(cases)):
        self.store.wheel_log(wid, 4, autogrid.OVER_CAP.format(n=len(hyps)))
        return None
    for i, h in enumerate(hyps, 1):
        h["why"] = f"전부 시험 {i} / {len(hyps)}"
    self.store.wheel_log(wid, 4, autogrid.START.format(n=len(hyps), m=len(cases), node=node["name"]))
    return hyps


def model_on_path(cases, by, nodes):
    """The first node of the cases' Runs that calls a model, or None — a pre-screen runs every combination, so only a path that costs nothing."""
    return next((v.get("node") for c in cases for v in autodeviate.case_visits(by.get(c)) if autohyp.kind_of(nodes.get(v.get("node")) or {}) == "llm"), None)


def _screen(self, w, state, rel, node, hyps, ids, cases, run, graph_id, cap):
    """D-549 덧 13 (packrt-1 167): a space too large for the cheap test — every combination run fast in the Pack's own process (Executor.screen:
    the Pack's nodes, no model on the way, the query embedded once) and counted the table's way; the first few by the 10-30 order are the
    hypotheses, still cheap-tested. None (said why) when there is no such way — ④ then goes the way before."""
    from . import suites
    from .runs import ScreenFailed
    wid, pid = w["wheel_id"], w["project_id"]
    screen = getattr(self.ops, "screen", None)
    if screen is None:
        return None
    x = len(hyps) * len(cases)
    if x > autogrid.SCREEN_RUNS:
        self.store.wheel_log(wid, 4, autogrid.SCREEN_BIG.format(n=len(hyps), m=len(cases), x=x, cap=autogrid.SCREEN_RUNS))
        return None
    nodes, by = row_nodes(rel), dict(zip(ids, (run or {}).get("cases") or []))
    model = model_on_path(cases, by, nodes)
    if model:
        self.store.wheel_log(wid, 4, autogrid.SCREEN_MODEL.format(node=model))
        return None
    if self.real and self.over_cap(UNIT_SHORT * len(cases)):
        self.store.wheel_log(wid, 4, autogrid.OVER_CAP.format(n=len(hyps)))
        return None
    self.store.wheel_log(wid, 4, autogrid.SCREEN_START.format(n=len(hyps), m=len(cases)))
    prompt = autohyp.prompt_text_of(rel, node)
    combos = [autohyp.ops_of(h, node, graph_id, prompt) for h in hyps]
    other = next((o.get("op") for ops in combos for o in ops or [] if o.get("op") not in ("set", "unset")), None)
    if other or not all(combos):   # the child sets a node's params; another line (a function's option, a file) is the Pack copy's to make
        self.store.wheel_log(wid, 4, autogrid.SCREEN_OPS.format(op=other or "없음"))
        return None
    su = self.store.suite(state["suites"]["work"])
    sub = {**su, "cases": [c for cid in cases for c in su["cases"] if c["id"] == cid]}
    field = state.get("field") or {}
    payload = [{"input": c["input"], "review": suites.case_review(sub, i) if su.get("review_policy") else {"policy": "stop_before"}, **({"then": c["then"]} if c.get("then") else {}),
                "second_free": not any(v.get("node") == node["name"] for v in (by.get(c["id"]) or {}).get("visits") or []), "records": node["name"],
                **({"watch": list(field[c["id"]].get("nodes") or {})} if c["id"] in field else {})} for i, c in enumerate(sub["cases"])]
    try:
        got = screen(pid, rel, payload, combos, autogrid.SCREEN_TIMEOUT)
    except ScreenFailed as e:
        self.store.wheel_log(wid, 4, autogrid.SCREEN_FAILED.format(code=e.code, where=e.where))
        return None
    if got["rows"] and all(c["state"] != "완료" for r in got["rows"] for c in r["cases"]):   # no key, no address: never 'nothing fixes'
        self.store.wheel_log(wid, 4, autogrid.SCREEN_FAILED.format(code="case 모두 실패", where="run"))
        return None
    if self.real:
        tokens, price = int(got.get("embed_tokens") or 0), EMBED_USD_PER_TOKEN.get(got.get("embed_model"))
        self.record(w, "④ 미리 거르기", int(got.get("embed_calls") or 0), tokens * price if price else UNIT_SHORT, str(got.get("embed_model") or "임베딩") if price else "추정 · 임베딩", tokens_in=tokens)
    _, sets, base_ok, *_ = _base_measures(self, state, w)
    expect, seen, rows = _expect(self, state, w), {}, []
    had = {c: autogrid.records_of(by.get(c), node["name"]) for c in cases}
    for r in got["rows"]:
        key = tuple(c["key"] for c in r["cases"])
        if key not in seen:
            pseudo = [{"state": c["state"], "final": c["final"], "visits": c.get("visits") or [], **({"steps": [{"final": c["first"]}]} if "first" in c else {})} for c in r["cases"]]
            g = autocand.summarize({"cases": pseudo}, cases, sets, base_ok, final=expect)
            seen[key] = {"fixed": g["fixed"], "broken": len(g["broken"]), "cut": autogrid.cut_records(cases, [c.get("records") for c in r["cases"]], had),
                         "field": len(autocand.field_fixed({"cases": pseudo}, cases, field))}
        rows.append({"combo": hyps[r["i"]]["value"], "h": hyps[r["i"]], **seen[key]})
    if any(r["cut"] for r in rows):
        self.store.wheel_log(wid, 4, autogrid.SCREEN_CUT.format(n=sum(1 for r in rows if r["cut"])))
    folder = self.settings.data_dir / "auto" / str(wid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / autogrid.SCREEN_FILE).write_text("".join(json.dumps({"rank": i, **{k: v for k, v in r.items() if k != "h"}}, ensure_ascii=False) + "\n"
                                                       for i, r in enumerate(sorted(rows, key=autogrid.rank), 1)), encoding="utf-8")
    self.store.wheel_log(wid, 4, autogrid.SCREEN_SAVED.format(n=len(rows)))
    top, n = autogrid.screen_pick(rows, min(autogrid.SCREEN_TOP, max(cap // max(len(cases), 1), 1)))
    if not top:
        self.store.wheel_log(wid, 4, autogrid.SCREEN_NONE.format(n=len(rows)))
        return None
    self.store.wheel_log(wid, 4, autogrid.SCREEN_END.format(k=n, t=len(top), combo=autogrid.combo_text(top[0]["combo"]), f=top[0]["fixed"], b=top[0]["broken"], c=top[0]["cut"], d=top[0]["field"]))
    return [{**r["h"], "why": autogrid.SCREEN_WHY.format(i=i, t=len(top), f=r["fixed"], b=r["broken"], c=r["cut"], d=r["field"])} for i, r in enumerate(top, 1)]


def _hypotheses_step(self, w, rel):
    """④ on one pass: the responsible node once, then this iteration's hypotheses (checked, in batches of N). Paid calls (judge_node ·
    the agent) only in real mode inside the cap; the agent and the judge see no hidden case, judge reason or label reason."""
    wid, pid = w["wheel_id"], w["project_id"]
    state = self.store.wheel_state(wid)
    settings = json.loads(w["settings"]) or {}
    n_max, m_max = int(settings.get("max_hypotheses", 10)), int(settings.get("rebuilds", 3))
    g = self.gate(w, 0, rel, first=False)   # the write gate again (tables · shared fake · cap · source · models)
    if g:
        return self._hold(w, state, *g)
    if state.get("hold"):
        state = {k: v for k, v in state.items() if k != "hold"}
        self.store.set_wheel_state(wid, state)
        self.store.wheel_update(wid, cell_state="running")
    if is_eff(w):   # EV-069 CHG-EF-4: structure hypotheses by rules — no responsible node, no knob check (A2-B1)
        return _eff_hypotheses(self, w, rel, state)
    sets = state.get("sets") or {}
    fix = [k for k, v in sets.items() if v == "fix"]
    visible = {k for k, v in sets.items() if v in ("fix", "keep")}
    work = self.store.suite(state["suites"]["work"])
    case_ids = [c["id"] for c in work["cases"]]
    run = self.ops.run(state["baseline"][0])
    nodes = row_nodes(rel)   # D-476: a function node's declared options
    nodes = graphrel.with_cases(nodes, case_ids, run, set(fix), _expect(self, state, w) or {})   # D-543 ⑥ ②: the relations the fix cases used · missed
    nodes = autoorder.attach(nodes, autoorder.pairs(case_ids, run, set(fix), _expect(self, state, w) or {},   # D-565 #7: the 헛걸음 check-order pairs
                                                    getattr(self.ops, "chosen_spec", lambda rid: None)(w["base_release_id"]), (run.get("graph") or {}).get("output_field")))
    harness = _harness(self.settings) if self.settings.mode == "fake" else {}
    held = _find_responsible(self, w, state, case_ids, fix, run, nodes, harness)   # D-549 덧 3 0단계: ④'s diagnosis in one place
    if held:
        return held[1]
    if not state["resp"].get("faults"):   # D-478 덧 26: each fix case's broken output field, read once into the diagnosis record
        state["resp"] = {**state["resp"], "faults": True, "per_case": with_faults(state["resp"].get("per_case") or [], case_ids, run, (rel.get("pack") or {}).get("copy"))}
        self.store.set_wheel_state(wid, state)
    if "shown" not in state["resp"]:   # D-525 덧 30: ④'s table shows the chain the agent's front nodes come from, once
        by_case = dict(zip(case_ids, run["cases"]))
        sigs = [(p, by_case.get(p["case"])) for p in state["resp"].get("per_case") or [] if p.get("signal") and p.get("node") == state["resp"]["node"]]
        state["resp"] = {**state["resp"], "shown": shown_chain(nodes, sigs, checked_cases(self.store, pid, w))}
        self.store.set_wheel_state(wid, state)
    it = w["iteration"]
    if state.get("hyp_iter") == it:
        return
    probe = state["resp"].get("probe") if state["resp"].get("basis") == "probe" else None   # A ③: this iteration's node
    node = nodes.get(probe[(it - 1) % len(probe)] if probe else state["resp"]["node"])
    if probe and state.get("probe_said") != it:
        state["probe_said"] = it
        self.store.set_wheel_state(wid, state)
        self.store.wheel_log(wid, 4, PROBE_NODE.format(it=it, node=node["name"], k=(it - 1) % len(probe) + 1, n=len(probe)))
    by_record = state["resp"].get("basis") in ("deviation", "probe")   # D-549 덧 3 A ②: read from the record → that node's options only
    if by_record and node is not None and not autohyp.knobs_of(node):   # A-2: nothing to change there — a person's turn, no cover-up
        return self._hold(w, state, "done", NO_FIX_NODE.format(node=node["name"]), True)
    doc = self.doc.for_wheel(w)
    tried = {(h["node"], h["knob"], json.dumps(h["value"])) for h in self.store.hypotheses(wid)}
    # D-513: what was tried as sets of ops — this wheel's rows and kept hypotheses, and the finished wheels' on the same base
    before, said = earlier_tried(self.store, w, state)
    earlier = {}
    for x in said:
        earlier.setdefault((x["node"], x["knob"], json.dumps(x["value"])), x["wheel"])
    mine = ({row_key(r) for r in self.store.candidates(wid)}
            | {ops_key((h["detail"] or {}).get("ops")) for h in self.store.hypotheses(wid) if not h["discarded_reason"]}) - {frozenset()}
    tried_all, taken = tried | set(earlier), (set(), set())   # taken: this answer's kept ones (triples, ops keys)

    def repeat(h, ops=None):   # → why it is a change tried before, or None
        t, k = (h.get("node"), h.get("knob"), json.dumps(h.get("value"))), ops_key(ops) if ops else None
        if t in tried or k in mine:
            return DROP_TRIED
        if t in earlier or k in before:
            return DROP_EARLIER.format(seq=earlier.get(t) or before[k])
        return DROP_SAME_ANSWER if t in taken[0] or k in taken[1] else None
    repeated = []
    quota = False   # D-492 ②: the agent's and the rules' iterations keep a place for a fault node; a document's or the harness's never
    fill_from = None   # D-525 덧 44: the agent's iteration fills what the checks dropped
    if doc and it == 1 and autohyp.doc_hypotheses(doc):   # D-463 덧 23: a document with no hypotheses (field_truth only) holds no one back
        hyps = autohyp.doc_hypotheses(doc)
    elif harness.get("hypotheses") is not None and it == 1:
        hyps = [{**h, "source": "harness"} for h in harness["hypotheses"]]
    elif by_record and (grid := _grid_hypotheses(self, w, state, rel, node, case_ids, visible, run, mine | set(before))) is not None:
        hyps = grid   # D-549 B: the node read from the record — its own option space tried whole when small
    elif self.real and self.settings.auto_hypothesis == "agent":
        if self.over_cap(UNIT_AGENT):
            return self._hold(w, state, "stopped", self.cap_hold())
        ids = [c["id"] for c in self.store.suite(state["suites"]["work"])["cases"]]
        base_run = self.ops.run(state["baseline"][0]) if state.get("baseline") else None
        cases = agent_cases(state["resp"]["per_case"], ids, base_run, _expect(self, state, w), state.get("field"),
                            folder=(rel.get("pack") or {}).get("copy"))   # EV-079: outputs · expected; D-478 덧 26: the broken field
        first = next((c for c in (base_run or {}).get("cases") or [] if c.get("visits")), None)
        by_case = dict(zip(ids, (base_run or {}).get("cases") or []))
        sigs = [(p, by_case.get(p["case"])) for p in state["resp"]["per_case"] if p.get("signal") and p.get("node") == node["name"]]   # D-463 덧 24 (a)
        checked = checked_cases(self.store, pid, w)
        lim = autohyp.pack_limits((rel.get("pack") or {}).get("copy") or "")   # D-469 (4): the agent sees top_k within the Pack schema limit
        others, said_take = front_nodes(nodes, sigs, node["name"], checked) if sigs else (agent_nodes(nodes, first, node["name"]), [])
        if it == 1:   # D-525 덧 6: said once — the first-step nodes a two-step case's take brought in, or why not
            for cid, took, keys, why in said_take:
                self.store.wheel_log(wid, 4, TAKE_NODES.format(names=" · ".join(took), case=cid, keys=" · ".join(keys)) if took
                                     else TAKE_NOT.format(case=cid, why=why))
        faulted = {n: k for n, k in fault_nodes(state["resp"].get("per_case"), node["name"]).items() if nodes.get(n) and autohyp.knobs_of(nodes[n])}
        if faulted:   # D-478 덧 27: the node whose output broke its schema is one the agent may propose on, said once an iteration
            others = [{**nodes[n], "_fault": k} for n, k in faulted.items()] + [o for o in others if o["name"] not in faulted]
            self.store.wheel_log(wid, 4, FAULT_NODES.format(names=" · ".join(f"{n} {k}" for n, k in faulted.items())))
        if by_record:   # A ②: the agent proposes on the responsible node only (the front nodes · broken-output nodes are not offered)
            others = []
        chain = next((took for cid, took, _, _ in said_take if cid in checked and took), [])   # A-27: the checked case's chain fills first
        idle = {n: k for n, k in idle_nodes(self.store.hypotheses(wid), self.store.candidates(wid)).items()
                if n not in chain}   # D-478 덧 17: told, not hidden · A-28: never on the checked case's chain (it may move only in combination)
        mark = lambda n: {**n, "_limits": lim, **({"_idle": idle[n["name"]]} if n["name"] in idle else {})}
        hyps, usage = self.ops.ask_agent(pid, mark(node), autohyp.prompt_text_of(rel, node), cases,
                                         [mark(o) for o in others],
                                         history=agent_history(self.store.hypotheses(wid), self.store.candidates(wid)) + said)   # D-463 덧 26 ① · D-513: already_tried
        am = self.settings.auto_agent_model   # D-469 덧 3: the agent's own model (it named the judge's)
        usd, tin, tout = autohyp.agent_cost(am, usage)   # D-506: its tokens × the price list, like the structure agent's
        self.record(w, "④ 가설 에이전트", 1, UNIT_AGENT if usd is None else usd, am if usd is not None else f"추정 · {am}", tokens_in=tin, tokens_out=tout)
        tried_knobs = measured_knobs(self.store.hypotheses(wid))   # D-469 덧 6 (가)
        knobs = lambda n: {k: v for k, v in autohyp.fill_knobs(n, self.settings.auto_index_real, lim).items()
                           if not (probe and k.startswith("index."))}   # noqa: E731 - A-27: the fill steps what the agent may be shown · A ③: no rebuild option
        fresh, seen = [], set()
        for h in [autohyp.fix_value(x, nodes) for x in hyps or []]:   # A-27: a one-key filter as []/null → that key; D-513: a repeat goes before the fill
            t = (h.get("node"), h.get("knob"), json.dumps(h.get("value")))
            why = repeat(h) or (DROP_SAME_ANSWER if t in seen else None)
            seen.add(t)
            (repeated.append((h, why, None)) if why else fresh.append(h))
        weak = [h for h in fresh if h.get("basis_missing")]   # D-549 덧 12: kept, marked, after the hypotheses whose basis was found
        if weak:
            fresh = [h for h in fresh if not h.get("basis_missing")] + [{**h, "why": " · ".join(x for x in (h.get("why"), autogrid.no_basis(h)) if x)} for h in weak]
            self.store.wheel_log(wid, 4, autogrid.NO_BASIS_LINE.format(n=len(weak), items=" · ".join(
                f"{h.get('knob')}={json.dumps(h.get('value'), ensure_ascii=False)}" for h in weak)))
        hyps = autohyp.fill_breadth(fresh, [node] + list(others), tried_knobs, fix, tried_all, idle=set(idle), knobs=knobs, first=chain)   # D-469 덧 3: up to BREADTH options an iteration
        quota, fill_from = True, ([node] + list(others), tried_knobs, set(idle), knobs, chain)
    elif self.real:
        return self._hold(w, state, "done", END_AGENT_OFF, True)
    else:
        hyps, quota = autohyp.rule_hypotheses(node, fix, tried_all), True
    if probe:   # A ③: one node an iteration, never a vector-DB rebuild option
        hyps = [h for h in hyps or [] if h.get("node") == node["name"] and not str(h.get("knob") or "").startswith("index.")]
    hyps = with_order_steps(hyps, nodes, fix, tried_all)   # D-565 #7: a 헛걸음's check-order steps, whatever node ④ chose
    graph_id = (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    rn, gold = state.get("retriever"), gold_nodes(work)

    def mixed(h):   # one kind of row per table (CHG-211): only the retriever node's rows in a retriever wheel
        if rn:
            return None if h.get("node") == rn else MIX_NOT_RET
        return None   # EV-079 (D-462 덧 4 Q-1): elsewhere a retriever row stays and goes by the accuracy rules (before: MIX_NO_CONFIRM)
    limits = autohyp.pack_limits((rel.get("pack") or {}).get("copy") or "")   # D-463 덧 29: the Pack schema's own limits
    def vet(h):   # → (why it is dropped or None, its ops)
        why = (autohyp.DROP_ANSWERS if autohyp.reads_answers(h) else None) or mixed(h) or autohyp.check(h, nodes, visible, self.real, limits=limits)   # D-463 덧 24 (c′) · 덧 29
        why = why or repeat(h)
        n = nodes.get(h.get("node"))
        if not why and h["knob"] == "prompt" and autohyp.shares_prompt(nodes, n):
            why = f"버림 — 프롬프트 파일을 다른 node도 씀({', '.join(autohyp.shares_prompt(nodes, n))}, D-99)"
        ops = None if why else autohyp.ops_of(h, n, graph_id, autohyp.prompt_text_of(rel, n))
        if not why and not ops:
            why = "버림 — 변경 줄을 만들 수 없음"
        why = why or repeat(h, ops)   # D-513: another way to the same ops
        if not why:
            taken[0].add((h["node"], h.get("knob"), json.dumps(h.get("value"))))
            taken[1].add(ops_key(ops))
        return why, ops
    ok, out = [], list(repeated)
    for h in hyps:
        why, ops = vet(h)
        (out if why else ok).append((h, why, ops))
    if fill_from and len(out) > len(repeated) and len(ok) < autohyp.BREADTH:   # D-525 덧 44: a dropped one's place, in the same iteration
        n_out = len(out) - len(repeated)
        ok, names = refill(ok, fill_from[0], fix, tried_all, fill_from[1], fill_from[2], vet, knobs=fill_from[3], first=fill_from[4])
        self.store.wheel_log(wid, 4, REFILL.format(n=n_out, names=" · ".join(names)) if names else REFILL_NONE.format(n=n_out))
    if fill_from:   # A-28: one merged rule row, tried only together — each option checked, the set never one tried before
        group, got = merge_rule(fill_from[0], fill_from[4], [h for h, _, _ in ok], fix, tried_all, fill_from[3], it, idle=fill_from[2]), []
        for h in group:
            n = nodes.get(h["node"])
            why = mixed(h) or autohyp.check(h, nodes, visible, self.real, limits=limits)
            ops = None if why else autohyp.ops_of(h, n, graph_id, autohyp.prompt_text_of(rel, n))
            if why or not ops:
                got = []
                break
            got.append((h, None, ops))
        if got:
            k = ops_key([o for _, _, x in got for o in x])
            seen_row = k in mine or k in before
            ok += [] if seen_row else got
            self.store.wheel_log(wid, 4, MERGE_TRIED.format(node=group[0]["node"]) if seen_row
                                 else MERGE_LINE.format(node=group[0]["node"], knobs=" · ".join(h["knob"] for h in group)))
    if quota:
        faulted = {n: k for n, k in fault_nodes(state["resp"].get("per_case"), node["name"]).items() if nodes.get(n) and autohyp.knobs_of(nodes[n])}
        ok, line = fault_quota(ok, faulted, nodes, node["name"], fix, tried_all, measured_knobs(self.store.hypotheses(wid)), vet)
        if line:
            self.store.wheel_log(wid, 4, line)
    n_first, merge_at = 0, None
    for i, (h, why, ops) in enumerate(ok):
        batch, no = ("first", 1) if i < n_max else ("waiting", 2 + (i - n_max) // n_max)
        if h.get("_wait") and batch == "first":   # D-492 ②: the one the fault node's place moved on
            batch, no = "waiting", 2
        if h.get("_merge"):   # A-28: a merged row's hypotheses in one batch
            batch, no = merge_at = merge_at or (batch, no)
        if h.get("_grid"):   # D-549 B: a small space is tried whole — every bundle in the first batch
            batch, no = "first", 1
        n_first += batch == "first"
        opt = autohyp.option_view(nodes.get(h["node"]) or {}, h["knob"])   # D-476: the table's mouse-over
        self.store.add_hypothesis(wid, it, batch, no, h["node"], h["knob"], h["value"],
                                  {"predicts": h.get("predicts") or [], "why": h.get("why"), "ops": ops, **({"option": opt} if opt else {}),
                                   **({"merge": h["_merge"]} if h.get("_merge") else {}), **({"kind": "bundle", "grid": True} if h.get("_grid") else {})},
                                  "대기", None, h.get("source") or "agent")
    for h, why, _ in out:
        self.store.add_hypothesis(wid, it, "first", 0, str(h.get("node")), str(h.get("knob")), h.get("value"),
                                  dropped_detail(h, nodes), None, why, h.get("source") or "agent")
    src = {"document": self.doc.label() or "시작 가설 문서", "agent": "가설 에이전트", "rule": "규칙", "harness": "시험 장치", "grid": "전부 시험"}
    self.store.wheel_log(wid, 4, f"반복 {it} · 가설 {len(ok)}개(출처 {source_words([x[0] for x in ok], src)}) · 버림 {len(out)} · 처음 {n_first} · 대기 {len(ok) - n_first}")
    reps = [why for _, why, _ in out if why.startswith(REPEATS)]
    if reps:   # D-513: one line an iteration for what was dropped as tried before
        names = {DROP_TRIED: "앞 반복", DROP_SAME_ANSWER: "같은 답"}
        names.update({DROP_EARLIER.format(seq=q): f"지난 자동 개선 {q}" for q in sorted(set(earlier.values()) | set(before.values()))})
        parts = " · ".join(f"{names.get(r, r)} {reps.count(r)}" for r in dict.fromkeys(reps))
        self.store.wheel_log(wid, 4, DUP_LINE.format(it=it, n=len(reps), parts=parts))
    state["hyp_iter"] = it
    self.store.set_wheel_state(wid, state)
    if not ok and quota and it < m_max and reps and len(reps) == len(out):   # D-513: all they brought was tried before — the wheel ends here
        #                                                                       (an iteration with nothing at all goes on as before)
        self.store.wheel_log(wid, 4, EARLY_END.format(it=it, m=m_max, n=len(reps)))
        end = None if is_eff(w) else acc_end(self.store.candidates(wid), self.store.hypotheses(wid), it)
        return self._hold(w, state, "done", end or END_NO_NEW.format(it=it, n=len(reps)), True)
    if not ok:
        if it >= m_max:
            end = None if is_eff(w) else acc_end(self.store.candidates(wid), self.store.hypotheses(wid), m_max)   # D-464 · D-463 덧 26 ②
            return self._hold(w, state, "done", end or END_NO_HYP.format(m=m_max), True)
        self.store.wheel_update(wid, iteration=it + 1)
        self.store.wheel_log(wid, 4, f"반복 {it + 1} — 가설을 새로 세움")
        return
    self.store.wheel_update(wid, cell=5, cell_state="running")   # ML-3: ⑤ builds the rows
    self.store.wheel_log(wid, 5, "⑤ 후보 줄 — 가설로 변경 줄을 만들고 같은 구조 검사")


Main.hypotheses = _hypotheses_step


def _eff_hypotheses(self, w, rel, state):
    """④ of an efficiency wheel (CHG-EF-4): the three structure shapes on the base (autohyp.structure_hypotheses), each with its ops and
    the node set they touch (Q-EF-7) — the fixed-flow check comes in ⑤ on the row's Pack. Rules are fixed, so one iteration."""
    wid, it = w["wheel_id"], w["iteration"]
    if state.get("hyp_iter") == it:
        return
    if self.struct_agent() and _eff_agent_hypotheses(self, w, rel, state) is not FALLBACK:   # D-462 A: the agent first; the rules when it is off or fails
        return
    ids = [c["id"] for c in self.store.suite(state["suites"]["work"])["cases"]]
    tried = {(h["node"], h["knob"], json.dumps(h["value"])) for h in self.store.hypotheses(wid)}   # slice review 1 B1: never twice
    ok, out = [], []
    for h in autohyp.structure_hypotheses(rel, autohyp.pack_chat_models(rel)):
        why = autohyp.structure_check(h, rel, lambda m: autocand.chat_price(m) is not None)
        if not why and (h["node"], h["knob"], json.dumps(h["value"])) in tried:
            why = "버림 — 앞 반복에서 시험한 가설"
        try:
            ops, touched = (None, None) if why else autohyp.structure_ops(rel, h)
        except Exception as e:   # noqa: BLE001 - packrt's preview could not run on the base copy: that hypothesis only
            ops, touched = None, f"버림 — 변경 줄을 만들 수 없음({type(e).__name__})"
        if not why and ops is None:
            why = touched
        (out if why else ok).append((h, why, ops, touched))
    for h, _, ops, touched in ok:
        self.store.add_hypothesis(wid, it, "first", 1, h["node"], h["knob"], h["value"],
                                  {"predicts": ids, "why": h.get("from"), "ops": ops, "nodes": touched}, "대기", None, "rule")
    for h, why, _, _ in out:
        self.store.add_hypothesis(wid, it, "first", 0, h["node"], h["knob"], h["value"], {"predicts": []}, None, why, "rule")
    self.store.wheel_log(wid, 4, f"구조 가설 {len(ok)}개(규칙) · 버림 {len(out)}")
    state["hyp_iter"] = it
    self.store.set_wheel_state(wid, state)
    if not ok:
        return self._hold(w, state, "done", END_NO_STRUCT, True)
    self.store.wheel_update(wid, cell=5, cell_state="running")
    self.store.wheel_log(wid, 5, "⑤ 후보 줄 — 구조 가설의 변경 줄 · 고정 흐름 검사")


def _row_results(self, wid):
    """{hypothesis id: what became of its row} — the drop reason, the table's failed criteria, or the outcome (the agent's history)."""
    out = {}
    for r in self.store.candidates(wid):
        tr = r.get("table_result") or {}
        text = (r.get("check_result") or {}).get("why") or " · ".join(tr.get("criteria") or []) or r.get("outcome") or "시험 중"
        for hid in r.get("hypothesis_ids") or []:
            out[hid] = text
    return out


def _eff_agent_hypotheses(self, w, rel, state):
    """D-462 A ④: one call to the structure candidate agent (the graph · fixed flow · the base Runs' per-node measures · prices · allowed
    models · the earlier iterations' results) → the first 10 candidates through the rules (autohyp.check_candidate), packrt's structure
    check, the confirmed fixed flow and the reached check → up to 6 go to ⑤, the rest wait untried. None passing: the next iteration
    asks again; after the last one the wheel ends for a person."""
    wid, it, pid = w["wheel_id"], w["iteration"], w["project_id"]
    m_max = int((json.loads(w["settings"]) or {}).get("rebuilds", 3))
    if self.over_cap(UNIT_STRUCT_AGENT):
        self.store.wheel_log(wid, 4, RULES_FALLBACK.format(why=f"비용 상한(${self.server_spent():.2f} / ${self.settings.auto_budget_usd:.2f})"))
        return FALLBACK
    ids = [c["id"] for c in self.store.suite(state["suites"]["work"])["cases"]]
    base_runs = [self.ops.run(u) for u in state.get("baseline") or []]
    prior, results = self.store.hypotheses(wid), _row_results(self, wid)
    tried = {(h["detail"] or {}).get("sha") for h in prior if h.get("discarded_reason") != WAIT_OVER} - {None}
    history = [{"iteration": h["iteration"], "title": h["value"], "shape": h["knob"].replace("구조:", ""),
                "result": h.get("discarded_reason") or results.get(h["hypothesis_id"], "시험 중")} for h in prior if h.get("source") == "agent"]
    allowed = [m for m in autohyp.pack_chat_models(rel) if autocand.chat_price(m) is not None]
    items = ((self.store.fixed_flow(rel["id"]) or {}).get("items") or {}).get("items") or []   # the row keeps packrt's read {items, errors}
    model = self.settings.auto_agent_model
    payload = autohyp.structure_agent_input(rel, items, autocand.eff_by_node(base_runs), allowed, history, _removable(self, rel, base_runs))
    try:
        cands, usage = self.ops.ask_structure_agent(pid, payload)
    except Exception as e:   # noqa: BLE001 - a provider error: its kind and HTTP status only (the text may carry key pieces); not called again this iteration
        http = re.search(r"\b([45]\d\d)\b", str(e))
        self.record(w, "④ 구조 후보 에이전트", 1, UNIT_STRUCT_AGENT, f"추정 · {model}")
        self.store.wheel_log(wid, 4, RULES_FALLBACK.format(why=f"호출 실패({type(e).__name__}{f' {http.group(1)}' if http else ''})"))
        return FALLBACK
    usd, tin, tout = autohyp.agent_cost(model, usage)
    self.record(w, "④ 구조 후보 에이전트", 1, UNIT_STRUCT_AGENT if usd is None else usd, model if usd is not None else f"추정 · {model}", tokens_in=tin, tokens_out=tout)
    ok, out, n_model = [], [], 0
    for i, c in enumerate(cands):
        why, info = (("버림 — 제안 10개 넘음", None) if i >= autohyp.STRUCT_PROPOSALS else autohyp.check_candidate(c, rel, allowed))
        if not why and info["model_only"]:
            n_model += 1
            why = "버림 — 모델만 바꾸는 후보 2개 넘음" if n_model > autohyp.STRUCT_MODEL_ONLY else None
        if not why and info["sha"] in tried:
            why = "버림 — 앞 반복에서 시험한 후보"
        if not why:
            try:
                info, why = autohyp.join_removals(rel, info)   # D-463 ②: a removal is joined again by packrt
            except Exception as e:   # noqa: BLE001 - packrt's preview could not run on the base copy: that candidate only
                why = f"버림 — 변경 줄을 만들 수 없음({type(e).__name__})"
        if not why:
            why = _eff_row_check(self, rel, info, base_runs)
        (out if why else ok).append((c, why, info))
    for k, (c, _, info) in enumerate(ok):
        h = _agent_row(rel, c, info)
        self.store.add_hypothesis(wid, it, "first", 1 if k < autohyp.STRUCT_TESTS else 0, h["node"], h["knob"], h["value"],
                                  {"predicts": ids if k < autohyp.STRUCT_TESTS else [], "why": c.get("why"), "expect": c.get("expect"), "ops": info["ops"],
                                   "nodes": info["nodes"], "sha": info["sha"]}, "대기" if k < autohyp.STRUCT_TESTS else None,
                                  None if k < autohyp.STRUCT_TESTS else WAIT_OVER, c.get("source") or "agent")
    for c, why, info in out:
        h = _agent_row(rel, c, info)
        self.store.add_hypothesis(wid, it, "first", 0, h["node"], h["knob"], h["value"],
                                  {"predicts": [], "why": c.get("why"), "ops": (info or {}).get("ops") or c.get("ops"), **({"sha": info["sha"]} if info else {})},
                                  None, why, c.get("source") or "agent")
    n_try = min(len(ok), autohyp.STRUCT_TESTS)
    self.store.wheel_log(wid, 4, f"반복 {it} · 구조 후보 {len(cands)}개(출처 에이전트) · 시험 {n_try} · 대기 {len(ok) - n_try} · 버림 {len(out)}")
    state["hyp_iter"], state["struct_agent"] = it, True
    self.store.set_wheel_state(wid, state)
    if self.settings.auto_structure == "agent-once":
        return self._hold(w, state, "done", END_ONCE, True)
    if not ok:
        if it >= m_max:
            return self._hold(w, state, "done", END_EFF_HUMAN.format(m=m_max, best=_best_row(self, wid)), True)
        self.store.wheel_update(wid, iteration=it + 1)
        self.store.wheel_log(wid, 4, f"반복 {it + 1} — 구조 후보를 새로 받음")
        return
    self.store.wheel_update(wid, cell=5, cell_state="running")
    self.store.wheel_log(wid, 5, "⑤ 후보 줄 — 구조 가설의 변경 줄 · 고정 흐름 검사")


def _best_row(self, wid):
    """D-464 (1) for the efficiency wheel: ' · 최선 줄 <n> <node> — <what kept it out>' — the measured row with the fewest failed
    criteria, then the lowest cost a case, then the first; '' when no row was measured."""
    rows = [r for r in self.store.candidates(wid) if ((r.get("table_result") or {}).get("columns") or {}).get("efficiency")]
    if not rows:
        return ""
    cost = lambda r: (r["table_result"]["columns"]["efficiency"].get("cost") or 0)
    b = min(rows, key=lambda r: (len(r["table_result"].get("criteria") or []), cost(r), r["candidate_id"]))
    return f" · 최선 줄 {b['candidate_id']} {b['node']} — " + (" · ".join(b["table_result"].get("criteria") or []) or "기준 통과")


def _removable(self, rel, base_runs):
    """D-462 A (③ again, A-10): the node groups packrt can take out and join again, measured before the agent is asked — each standard
    node the work suite reaches, one by one, and the rule merge groups — through the same removal_ops · structure check · fixed flow a
    candidate meets. [{nodes, ok, why}] (no call; a tool result handed to the agent)."""
    seen = {v.get("node") for r in base_runs if r for c in r.get("cases") or [] for v in c.get("visits") or []}
    nodes = autohyp.nodes_of(rel)
    groups = [[n["name"]] for n in (rel.get("graph") or {}).get("nodes") or [] if n["name"] in seen and autohyp.kind_of(n) in autohyp.STANDARD_KINDS]
    for h in autohyp.structure_hypotheses(rel, []):
        if h["knob"] == autohyp.EFF_MERGE and autohyp.merge_nodes(rel, h) not in groups:
            groups.append(autohyp.merge_nodes(rel, h))
    out = []
    for g in groups:
        try:
            ops, touched = autohyp.removal_ops(rel, g)
            why = touched if ops is None else _eff_row_check(self, rel, {"ops": ops, "nodes": touched}, base_runs)
        except Exception as e:   # noqa: BLE001 - one group's preview could not run: that group only
            why = f"버림 — 변경 줄을 만들 수 없음({type(e).__name__})"
        out.append({"nodes": g, "ok": why is None, "why": why})
    return [x for x in out if all(n in nodes for n in x["nodes"])]


def _agent_row(rel, c, info):
    """The hypothesis row of an agent candidate: its first node of this Pack (else its first touched one), the shape as the knob
    (a model-only one is '구조:모델 교체' with the model as the value) and the title."""
    nodes = autohyp.nodes_of(rel)
    names = (info or {}).get("nodes") or []
    node = next((n for n in names if n in nodes), names[0] if names else "—")
    if info and info["model_only"]:
        return {"node": info["ops"][0].get("node") or node, "knob": autohyp.EFF_MODEL, "value": str(c.get("title") or info["ops"][0].get("value"))[:80]}
    return {"node": node, "knob": "구조:" + ((info or {}).get("shape") or autohyp.STRUCT_SHAPES.get(c.get("shape"), "구조")), "value": str(c.get("title") or "")[:80]}


def _eff_row_check(self, rel, info, base_runs):
    """packrt's structure check, the confirmed fixed flow and the reached check on a candidate's ops (the ones ⑤ makes on its row —
    here first, so the 6 tries go to candidates that can run). None, or '버림 — …'."""
    try:
        lines, _, view = self.ops.candidate_lines(rel, info["ops"])
    except Exception as e:   # noqa: BLE001 - the structure check refused the change
        return f"버림 — 검사 {check_error(e)}"
    if not view.get("ok"):
        return "버림 — 구조 검사 " + "; ".join(e.get("message", "") for e in view.get("errors", []))[:120]
    check = getattr(self.ops, "fixed_flow_check", None)
    try:
        broken = check(rel, lines) if check else [{"message": "고정 흐름 검사 길 없음"}]
    except Exception as e:   # noqa: BLE001 - a check that cannot run keeps the candidate out
        broken = [{"message": f"고정 흐름 검사 실패({type(e).__name__})"}]
    if broken:
        return "버림 — " + broken[0].get("message", "")[:160]
    seen = {v.get("node") for r in base_runs if r for c in r.get("cases") or [] for v in c.get("visits") or []}
    return None if seen & set(info["nodes"]) else DROP_UNREACHED


# ---------------------------------------------------------------- ⑤ the rows, the table and the winner (ML-3, CHG-182 · 183)
from . import autocand, autoreview, opsruns   # noqa: E402
from . import gateway as gatewaymod   # noqa: E402

UNIT_SHORT = 0.012                    # one predicted case run without its judge, conservative (plan 6절)
START_6 = "⑥ 비교 — 숨긴 case 실행 뒤 채점 에이전트가 기준 표와 scorecard로 판정 권고(D-98)"
START_6_RET = "⑥ 비교 — 확인 suite(test) 한 번 실행 뒤 채점 에이전트가 기준 표와 scorecard로 판정 권고(D-98) · test 결과는 보기만"
START_6_EFF = "⑥ 비교 — 확인 suite(test)를 운영 · 새 버전에서 한 번씩 · 기준 표로 판정 권고(judge 없음)"   # EV-069 (slice review 2 B4)
HUMAN_7 = "⑦ 사람 차례 — {name} ✦ 자동 · 판정 권고 {rec} · 할 일: 배포 승인 ▶ 한 번"   # ORCH 240: the recommendation's word
REC_WORD = {"adopt": "채택", "hold": "보류", "reject": "버림"}   # the screen's words (web VERDICT_WORD)
STOP_VERSION = "멈춤 · 새 버전을 등록하지 못함 — {why}"
STOP_REVISION = "멈춤 · 새 버전 revision이 1등 줄과 다름 — 평가 Run을 쓰지 않음"
NOT_NEXT = ("table_out", "rec_reject", "winner", "discarded")   # rows that cannot come up as the table's next row (D-122)
LOST = object()                       # a row's run that is gone from memory (the server started again)
TOLERANCE = 0.20                      # tokens · latency within the baseline + 20% (Q-9 A; settings key 'tolerance')
END_NO_ROW = "끝 · 알림 — 반복 {m}번 뒤에도 판정 기준을 통과한 후보 줄 없음"
# D-655 (llmops-2 268 · A-20): the base's own shake is measured with the same suite, never counted as a row's break
BASE_AGAIN = "줄 {row} 반복에서 지킬 case {n}건 깨짐({cases}) — 기준선 한 번 더(작업 suite {k}/{m}) · 기준선에서도 깨지는지 봄"
BASE_AGAIN_STEP = "⑤ 흔들림 기준선 반복"
NOISE_LINE = "줄 {row} 반복 — {words} · 기준선 {n}번 중 깨진 적 있음(나빠짐으로 안 셈)"
FF_NONE = "목록 없음"   # D-463 덧 15 ⓐ: the Pack lists no fixed flow
NOTE_NO_FIXED_FLOW = "고정 흐름 목록 없음 · 검사 안 함"
HOLD_FIXED_FLOW = "사람 확인 필요 — 고정 흐름 다시 확인({state}) · 이 버전의 고정 흐름 창에서 확인하면 이어 감"
FF_HOLD = HOLD_FIXED_FLOW.split("(")[0]
DROP_FIXED_FLOW = "버림 — 고정 흐름 걸림 · "   # D-463 덧 9
END_ACC_HUMAN = "끝 · 사람 승인 필요 — 가설 {m}번 반복에서 판정 기준 통과 0 · 최선 줄 {row} {node} — 고침 {fixed}/{fix} · 망가짐 {broken}"   # D-464
END_HUMAN = "끝 · 알림 — 사람 확인 정답에서 recall@10이 내려감(반자동과 반대) · 줄 {row} · 사람 판단"
CHEAP_SKIPPED = "리트리버 줄"
CHEAP_SKIPPED_EFF = "효율 줄"


END_ACC_TRIED = "끝 · 사람 승인 필요 — 가설 {m}번 반복에서 판정 기준 통과 0 · 표까지 간 줄 0 · 시험한 가설 {n} — {tried}"   # D-463 덧 26 ②


def row_result(r):
    """What became of a row, in a few words: its drop reason, its cheap test 'changed / predicted', the table's failed criteria."""
    ct, tr = r.get("cheap_test") or {}, r.get("table_result") or {}
    return ((r.get("check_result") or {}).get("why") or (f"{len(ct.get('changed') or [])} / {len(ct.get('cases') or [])} 바뀜" if "changed" in ct else None)
            or " · ".join(tr.get("criteria") or []) or r.get("outcome") or "시험 중")


def agent_history(hyps, rows):
    """D-463 덧 26 ① (A's shape): the agent's earlier hypotheses and what became of each — the drop reason or its row's result."""
    by_hyp = {hid: row_result(r) for r in rows for hid in r.get("hypothesis_ids") or []}
    return [{"iteration": h["iteration"], "node": h["node"], "knob": h["knob"], "value": h["value"],
             "result": h.get("discarded_reason") or by_hyp.get(h["hypothesis_id"], "시험 중"),
             "measured": not h.get("discarded_reason")} for h in hyps if h.get("source") == "agent"]   # D-469 덧 6 (가): dropped in ④ = not measured


def ops_key(ops):
    """D-513: a change as the set of its ops (node · option · value group) — the same whichever hypothesis or row asked for it."""
    return frozenset(json.dumps(o, sort_keys=True, ensure_ascii=False) for o in ops or [])


def row_key(r):
    return ops_key([o for ln in r.get("lines") or [] for o in ln.get("ops") or []])


def earlier_tried(store, w, state):
    """D-513 ③: the finished accuracy wheels on the same base version and base suite → ({ops key of each row they made: 'A-n'},
    [the single hypotheses those rows tried, with what became of each — the agent's already_tried]). A row with no change line (dropped
    before it) tried nothing."""
    keys, said, base = {}, [], (state.get("suites") or {}).get("base")
    for x in sorted(store.wheels(w["project_id"]), key=lambda x: x["seq"]):
        if x["wheel_id"] == w["wheel_id"] or not x.get("ended_at") or is_eff(x) or is_eff(w) or x["base_revision"] != w["base_revision"] \
                or (store.wheel_state(x["wheel_id"]).get("suites") or {}).get("base") != base:
            continue
        hyps = {h["hypothesis_id"]: h for h in store.hypotheses(x["wheel_id"])}
        for r in store.candidates(x["wheel_id"]):
            k, hs = row_key(r), [hyps[i] for i in r.get("hypothesis_ids") or [] if i in hyps]
            if not k:
                continue
            keys.setdefault(k, f"A-{x['seq']}")
            if len(hs) == 1:
                said.append({"wheel": f"A-{x['seq']}", "node": hs[0]["node"], "knob": hs[0]["knob"], "value": hs[0]["value"], "result": row_result(r)})
    return keys, said


def idle_nodes(hyps, rows):
    """D-478 덧 17 (packrt-1 070): {node: rows} for a node whose every single-node row measured by the cheap test moved no case (e.g. a
    manual retriever while the judging node reads no manuals) — a skipped test or a row over several nodes says nothing about one."""
    by = {h["hypothesis_id"]: h.get("node") for h in hyps}
    seen, moved = {}, set()
    for r in rows:
        nodes = {by.get(i) for i in r.get("hypothesis_ids") or []} - {None}
        ct = r.get("cheap_test") or {}
        if len(nodes) != 1 or "changed" not in ct:
            continue
        n = next(iter(nodes))
        if ct.get("changed"):
            moved.add(n)
        else:
            seen[n] = seen.get(n, 0) + 1
    return {n: k for n, k in seen.items() if n not in moved}


def measured_knobs(hyps):
    """D-469 덧 6 (가): the (node, option) pairs some hypothesis went on to measure with — one dropped in ④ (the value now · no change
    line · no chunk size) leaves its option untried."""
    return {(h["node"], h["knob"]) for h in hyps if not h.get("discarded_reason")}


def acc_tried_end(m, rows, hyps):
    """D-463 덧 26 ②: no row reached the table — the hypotheses its rows tried and each one's changed count in the best row's place;
    None when no row was tried at all."""
    if not rows:
        return None
    by = {h["hypothesis_id"]: h for h in hyps}
    said = [" · ".join(filter(None, [" ".join(f"{h['node']} {h['knob']} {json.dumps(h['value'], ensure_ascii=False) if not isinstance(h['value'], (int, float, str)) else h['value']}"
                                              for h in [by[i] for i in r.get("hypothesis_ids") or [] if i in by]), row_result(r)])) for r in rows]
    tried = "; ".join(said[:3]) + (f" 외 {len(said) - 3}" if len(said) > 3 else "")
    return END_ACC_TRIED.format(m=m, n=len(rows), tried=tried)


def acc_end(rows, hyps, m):
    """D-464 · D-463 덧 26: the accuracy wheel's end after m iterations with no passing row — the best measured row (most fixed · fewest
    broken · fewest tokens), else what its rows tried (no row reached the table), else None (nothing was tried: the old end)."""
    best = best_acc_row(rows)
    if best:
        col = best["table_result"]["columns"]
        return END_ACC_HUMAN.format(m=m, row=best["candidate_id"], node=best["node"], fixed=col.get("fixed") or 0, fix=col.get("fix") or 0,
                                    broken=len(col.get("broken") or []))
    return acc_tried_end(m, rows, hyps)


def cheap_changed(case_ids, cases, base_ok, final_of, on_fail=None):
    """D-463 덧 28: the cheap test's changed cases — by (case, rule) where the base was read that way (any rule changed), else by case."""
    out = []
    for cid, c in zip(case_ids, cases):
        b = base_ok.get(cid)
        now = (autocand.rule_outcomes(c, final=final_of(cid), on_fail=on_fail) if isinstance(b, dict)
               else autocand.outcome(c, final=final_of(cid), on_fail=on_fail))
        if now != b:
            out.append(cid)
    return out


def best_acc_row(rows):
    """D-464: the accuracy wheel's best measured row when none passed — most fixed, then fewest broken, then fewest tokens; None when no
    row was measured."""
    cols = lambda r: (r.get("table_result") or {}).get("columns")
    done = [r for r in rows if cols(r)]
    return min(done, key=lambda r: (-(cols(r).get("fixed") or 0), len(cols(r).get("broken") or []), cols(r).get("tokens") or 0)) if done else None


def _expect(self, state, w):
    """EV-079 (D-463 덧 6): an accuracy wheel's work-suite expectations by case — its cases pass only with their final checks too (the
    order checks of a suite like c1a15_order); None for an efficiency wheel (it reads them its own way, CHG-EF-5)."""
    if w is None or is_eff(w):
        return None
    return {c["id"]: c.get("expected") or {} for c in self.store.suite(state["suites"]["work"])["cases"]}


def _base_measures(self, state, w=None):
    """The baseline's outcome per work case (run 1), its tokens · latency (mean of the two runs) and judge range (real mode only)."""
    runs = [self.ops.run(u) for u in state["baseline"]]
    ids = [c["id"] for c in self.store.suite(state["suites"]["work"])["cases"]]
    sets = state.get("sets") or {}
    sums = [autocand.summarize(r, ids, sets, {}, real=self.real, final=_expect(self, state, w)) for r in runs]
    ok = sums[0].get("by_rule") or sums[0]["by_case"]   # D-463 덧 28: an accuracy wheel's base by (case, rule)
    tok = sum(s["tokens"] for s in sums) / len(sums)
    lat = [s["latency"] for s in sums if s["latency"] is not None]
    judges = [s["judge"] for s in sums if s["judge"] is not None]
    return ids, sets, ok, tok, (sum(lat) / len(lat) if lat else None), (min(judges) if judges else None)


def check_record_key(d):
    """D-444 #11: the app record a person-check diagnosis row came from ('check-1:<record>/<run>'), None for a label row."""
    rv = str(d.get("rubric_version") or "")[len(autodiag.CHECK_RUBRIC):]
    return rv.rsplit("/", 1)[0] if "/" in rv and not rv.startswith("label-") else None


def field_all(state):
    """D-444 #11 (가): every field case of the wheel — the field bundle and the trigger cases — for ⑥ '현장 점수 · <node>'."""
    return {**(state.get("field_bundle") or {}), **(state.get("field") or {})}


def _criteria(self, s, base_tok, base_lat, base_judge, tolerance):
    """CHG-182 ④ (D-93) on accuracy only (EV-065 CHG-267, D-412): fixed ≥ 1 · no keep case broken · no case or node error · the judge
    mean not below the baseline's lowest (real mode only — the fake judge is never a criterion, EV-030 Q-9). Tokens and latency are
    shown (⑥ '효율(보기만)'), never a reason to drop a row."""
    why = []
    if s.get("field") and not s.get("field_fixed"):   # EV-065 CHG-266a: the case a person checked must be fixed (retriever rows too)
        why.append("현장 정답 못 고침")
    if s["fixed"] < 1:
        why.append("고친 case 0")
    if s["broken"]:
        why.append(f"지킬 세트 망가짐 {len(s['broken'])}")
    if s["errors"]:
        why.append(f"case 오류 {s['errors']}")
    if self.real and base_judge is not None and s["judge"] is not None and s["judge"] < base_judge:
        why.append(f"채점 평균 {s['judge']:.3f} < 기준 최저 {base_judge:.3f}")
    return why


DROP_UNREACHED = "버림 — 바꾼 node가 작업 suite에서 안 불림(이 suite로 잴 수 없음)"   # S-4 (D-438 덧 7, CHG-EF-4 덧)


def _eff_unreached(runs, node):
    """DROP_UNREACHED when no base Run visits `node` (its change cannot show on this suite), else None."""
    seen = {v.get("node") for r in runs if r for c in r.get("cases") or [] for v in c.get("visits") or []}
    return None if node in seen else DROP_UNREACHED


def _eff_criteria(s, ebase, goals, tolerance):
    """EV-069 CHG-EF-5 (D-433 Q-EF-1): an efficiency row — every case the base kept still passes (judge-free, case by case), no case
    error, and one chosen goal better than the 운영 version's baseline with none worse than the tolerance."""
    why = []
    if s["broken"]:
        why.append(f"지킬 세트 망가짐 {len(s['broken'])}")
    if s["errors"]:
        why.append(f"case 오류 {s['errors']}" + (f" — {s['error_tag']}" if s.get("error_tag") else ""))
    if not autocand.eff_improved(s.get("efficiency") or {}, ebase or {}, goals, tolerance):
        why.append("효율 나아짐 없음")
    return why


def _merge_row(self, wid, it, rel, passing):
    """D-470 ⓒ (answer 068 Q-1 ~ Q-4 (가)): one row of the passing rows that change no node in common (autocand.merge_pick), their
    change lines one after another, through the same checks as any row (change lines · packrt view · the fixed flow); True when it
    waits for its table Run, False when there is none or the checks dropped it ('버림 — 합친 줄 …')."""
    picked = autocand.merge_pick(passing)
    if not picked:
        return False
    ops = [op for r in picked for l in r["lines"] for op in l.get("ops") or []]
    hids = [i for r in picked for i in r["hypothesis_ids"]]
    allowed = {l.get("node") for r in picked for l in r["lines"] if l.get("node")}
    cid = self.store.add_candidate(wid, it, hids, picked[0]["node"], [])

    def drop(why, **more):
        self.store.candidate_update(cid, outcome="discarded", check_result={"why": DROP_MERGED.format(why=why), "merged": True, **more})
        self.store.wheel_log(wid, 5, f"합친 줄 {cid} — {' + '.join(f'H-{i}' for i in hids)} · {DROP_MERGED.format(why=why)}")
        return False
    try:
        lines, revision, view = self.ops.candidate_lines(rel, ops)
    except Exception as e:   # noqa: BLE001 - the structure check refused the change
        return drop(f"검사 {check_error(e)}")
    changed = {l.get("node") for l in lines if l.get("node")}
    if not changed <= allowed or not view.get("ok"):
        return drop("가설 node 밖을 바꿈(D-99)" if not changed <= allowed else "구조 검사 " + "; ".join(e.get("message", "") for e in view.get("errors", []))[:120], **view)
    check = getattr(self.ops, "fixed_flow_check", None)
    try:
        broken = check(rel, lines) if check else [{"message": "고정 흐름 검사 길 없음"}]
    except Exception as e:   # noqa: BLE001 - a check that cannot run keeps the row out
        broken = [{"message": f"고정 흐름 검사 실패({type(e).__name__})"}]
    if broken:
        return drop(broken[0].get("message", "")[:160], fixed_flow=broken, **view)
    self.store.candidate_update(cid, revision=revision, check_result={**view, "merged": True}, cheap_test={"skipped": MERGED_SOURCE})
    self.store.candidate_update(cid, lines=lines)
    self.store.wheel_log(wid, 5, f"합친 줄 {cid} — {' + '.join(f'H-{i}' for i in hids)} · 작업 suite로 1번")
    return True


def _quality_review(self, w, state, r, run, ids, wcases):
    """D-462 B: the row's first Run against the 운영 baseline, case by case (llmops.autoreview) → {checked, findings, verdict, model},
    {skipped} when off or in fake mode without the harness' answers, or None when the spend cap stops it. The harness answers by
    output fingerprint (LLMOPS_FAKE_AUTO "review": {sha: {q1, q2, q3}}); a real server asks Jev through the gateway's decide."""
    if self.settings.auto_review == "off":
        return {"skipped": autoreview.SKIP_OFF}
    harness = _harness(self.settings).get("review")
    if harness is None and not self.real:
        return {"skipped": autoreview.SKIP_FAKE}
    by_id = {c["id"]: c.get("input") for c in wcases or []}
    its = autoreview.items(self.ops.run(state["baseline"][0]), run, r["node"], ids, inputs=[by_id.get(i) for i in ids])
    model = self.settings.auto_review_model
    if harness is None and self.over_cap(autoreview.UNIT_REVIEW * 2 * len(its)):
        return None
    fs, calls, usd, tokens = [], 0, 0.0, 0
    for it in its:
        if harness is not None:
            ba, ra = (autoreview.read(harness.get(autoreview.out_sha(it[k]))) for k in ("base", "row"))
        else:
            (ba, ub), (ra, ur) = (autoreview.ask(self.ops.decide, model, it["input"], it[k]) for k in ("base", "row"))
            calls, usd = calls + 2, usd + autoreview.usd(ub) + autoreview.usd(ur)
            tokens += (ub.get("input_tokens") or 0) + (ur.get("input_tokens") or 0)
        fs += autoreview.findings(it["case"], it["input"], it["base"], it["row"], ba, ra)
    if calls:
        self.record(w, "⑤ 품질 검토", calls, usd, model, tokens_in=tokens)
    return {"checked": len(its), "findings": fs, "verdict": autoreview.verdict(fs), "model": model}


def _ret_criteria(self, s, run, node, rbase, base_tok, base_lat, base_judge, tolerance, by_budget=False):
    """EV-043 CHG-211: a retriever row's conditions — recall@10 up · expectation agreement final · nodes not down in place of '고친 case
    ≥ 1', then the rest of CHG-182 ④ (keep set · errors · the real judge — efficiency is shown only, EV-065)."""
    # D-492 덧 16: a row changing the cut · chunk size · overlap is judged by the budget recall on the base Runs' budget
    own = [c["why"] for c in autocand.ret_checks([autocand.ret_of(run, node, rbase.get("budget_chars"))], rbase, by_budget) if c["ok"] is False]
    return own + [x for x in _criteria(self, s, base_tok, base_lat, base_judge, tolerance) if x != "고친 case 0"]


def _grid_first(self, wid, ids, hs):
    """D-549 B: of a batch's option bundles (cheap tests counted), only the first by the 10-30 order goes on to the table — every other
    one is discarded with its count; one line says the first, or that none fixes. Nothing for a batch without bundles."""
    rows = [r for r in self.store.candidates(wid) if r["candidate_id"] in set(ids)
            and any(((hs.get(i) or {}).get("detail") or {}).get("grid") for i in r["hypothesis_ids"])]
    if not rows:
        return
    scored = [{"combo": hs[r["hypothesis_ids"][0]]["value"], "row": r, **((r["cheap_test"] or {}).get("grid") or {"fixed": 0, "broken": 0})} for r in rows]
    best = autogrid.first(scored)
    for x in scored:
        if x is not best and x["row"]["outcome"] != "discarded":
            self.store.candidate_update(x["row"]["candidate_id"], outcome="discarded",
                                        check_result={**(x["row"].get("check_result") or {}), "why": autogrid.NOT_FIRST.format(f=x["fixed"], b=x["broken"])})
    n = len({i for r in rows for i in r["hypothesis_ids"]})
    self.store.wheel_log(wid, 5, autogrid.FIRST.format(n=n, combo=autogrid.combo_text(best["combo"]), f=best["fixed"], b=best["broken"]) if best
                         else autogrid.NONE_FIXES.format(n=n))


def _candidates_step(self, w, rel):
    """⑤ on one pass: build the batch's rows → cheap test → table → the winner twice more → the version (then ⑥ waits for ML-4)."""
    wid, pid = w["wheel_id"], w["project_id"]
    state = self.store.wheel_state(wid)
    g = self.gate(w, 0, rel, first=False)
    if g:
        return self._hold(w, state, *g)
    ff_held = str(state.get("hold") or "").startswith(FF_HOLD)   # D-613: kept until it is confirmed — cleared here it was said again on every pass
    if state.get("hold") and not ff_held:   # a step error or a cap hold earlier: going on clears it
        state = {k: v for k, v in state.items() if k != "hold"}
        self.store.set_wheel_state(wid, state)
        self.store.wheel_update(wid, cell_state="running")
    settings = json.loads(w["settings"]) or {}
    m_max, tol = int(settings.get("rebuilds", 3)), float(settings.get("tolerance", TOLERANCE))
    it = w["iteration"]
    cs = state.get("cand") or {}
    if cs.get("iteration") != it or cs.get("phase") is None:
        cs = {"iteration": it, "batch_no": 0, "phase": "rows"}
    ids, sets, base_ok, base_tok, base_lat, base_judge = _base_measures(self, state, w)
    visible = {k for k, v in sets.items() if v in ("fix", "keep")}
    rows = [r for r in self.store.candidates(wid) if r["iteration"] == it and (r["hypothesis_ids"] or [None])[0] in cs.get("hyp_ids", [])]
    rnode = state.get("retriever")   # EV-043 CHG-211: a retriever wheel's rows are all the retriever node's (④ drops the others)
    is_ret = lambda r: bool(rnode) and r["node"] == rnode
    eff = is_eff(w)   # EV-069 CHG-EF-5: the efficiency wheel's rows
    goals = eff_of(w).get("goals") or list(autocand.GOAL_ORDER)
    chars = {n["name"]: len(autohyp.prompt_text_of(rel, n) or "") for n in (rel.get("graph") or {}).get("nodes") or [] if autohyp.kind_of(n) == "llm"} if eff else {}
    bms = [autocand.eff_measures(self.ops.run(u), chars) for u in state["baseline"]] if eff else []
    ebase = {**autocand.eff_mean(bms), "noise": autocand.eff_noise(bms)} if eff else None   # S-4: a row beats the base Runs' spread
    wcases = self.store.suite(state["suites"]["work"])["cases"] if eff else None
    shaky, base_n = [], len(state.get("baseline") or [])
    if eff:   # the base's kept cases with their final expectations (E4 L0); D-655: passed in every base Run, the extra ones too
        more = [x for x in (self.ops.run(u) for u in cs.get("base_more") or []) if x and x["status"] not in ("대기", "실행 중")]
        base_n += len(more)
        base_ok, shaky = autocand.eff_base_ok([self.ops.run(u) for u in state["baseline"]] + more, ids, sets, wcases)
    if rnode:   # D-492 덧 16 (Q-1 (가)): the budget B is the base Runs' own top-10 characters a case
        bruns = [self.ops.run(u) for u in state["baseline"]]
        rbase = autocand.ret_base(bruns, rnode, budget=autocand.ret_budget(bruns, rnode))
    else:
        rbase = None
    hyp_by = {h["hypothesis_id"]: h for h in self.store.hypotheses(wid)}
    by_budget = lambda r: autocand.budget_row([hyp_by.get(i) for i in r.get("hypothesis_ids") or []])   # noqa: E731

    def save(**kw):
        cs.update(kw)
        state["cand"] = cs
        self.store.set_wheel_state(wid, state)

    if cs["phase"] == "rows" and not eff:   # EV-079 (D-463 덧 15): the accuracy rows keep the base's fixed flow — a person confirms it first
        ffs = getattr(self.ops, "fixed_flow_state", None)
        ff = ffs(rel) if ffs else None
        if ff is not None and ff not in (FF_NONE, fixedflow.STATE_DONE):
            return self._hold({**w, "cell": 5}, state, "waiting", HOLD_FIXED_FLOW.format(state=ff))
        if ff_held:
            state = {k: v for k, v in state.items() if k != "hold"}
            self.store.set_wheel_state(wid, state)
            self.store.wheel_update(wid, cell_state="running")
        if ff == FF_NONE and not state.get("ff_none_said"):   # ⓐ not silently: said once
            self.store.wheel_log(wid, 5, NOTE_NO_FIXED_FLOW)
            state["ff_none_said"] = True
            self.store.set_wheel_state(wid, state)
    if cs["phase"] == "rows":
        batch = sorted({h["batch_no"] for h in self.store.hypotheses(wid) if h["iteration"] == it and h["batch_no"] > cs["batch_no"] and not h["discarded_reason"]})
        if not batch:
            if it >= m_max:
                end = None if eff else acc_end(self.store.candidates(wid), self.store.hypotheses(wid), m_max)   # D-464: the best row · D-463 덧 26 ②: what was tried
                return self._hold(w, state, "done", end or (END_EFF_HUMAN.format(m=m_max, best=_best_row(self, wid)) if eff and state.get("struct_agent")
                                                            else END_NO_ROW.format(m=m_max)), True)
            self.store.wheel_update(wid, iteration=it + 1, cell=4, cell_state="running")
            self.store.wheel_log(wid, 4, f"반복 {it + 1} — 가설을 새로 세움(반복 {it}의 처음 · 대기 묶음이 모두 떨어짐)")
            state.pop("cand", None)
            self.store.set_wheel_state(wid, state)
            return
        no = batch[0]
        hyps = [h for h in self.store.hypotheses(wid) if h["iteration"] == it and h["batch_no"] == no and not h["discarded_reason"]]
        nodes = autoorder.with_twins(row_nodes(rel))   # D-476 덧 18: the same map as ④ (a function node's declared options attached) · D-565 #7: the twins
        merged = lambda h: (h.get("detail") or {}).get("merge")   # noqa: E731 - A-28: a merged rule row's hypotheses, tried only together
        groups = [[h] for h in hyps if not merged(h)]
        by_node, by_merge = {}, {}
        for h in hyps:
            if autohyp.hyp_kind(h) == "bundle":   # D-549 B: a bundle is its own row, never merged with others
                continue
            (by_merge.setdefault(merged(h), []) if merged(h) else by_node.setdefault(h["node"], [])).append(h)
        if not eff:   # EV-069 (A2-B2): no combined row in an efficiency wheel this time (Q-EF-6)
            groups += [c for c in (autocand.combine(v) for v in by_node.values()) if c]
            groups += [v for v in by_merge.values() if len(v) >= 2]
        made = []
        before = {} if eff else earlier_tried(self.store, w, state)[0]   # D-513: no row's change twice
        seen = {row_key(r) for r in self.store.candidates(wid)} - {frozenset()}
        idx_made = {"built": 0, "reused": 0}
        budget = lambda plan=None: self._index_budget(wid, state, plan, w)
        spent = lambda plan, report: self._index_spent(w, state, plan, report)
        for grp in groups:
            node = nodes.get(grp[0]["node"])
            text = autohyp.prompt_text_of(rel, node)
            ops = [] if eff else autohyp.change_bundle(grp, node, (rel.get("pack") or {}).get("entrypoint"), text)   # D-549 덧 3 0단계: the row's change lines, one bundle
            if eff:   # EV-069 CHG-EF-4: the ops ④ saved (A3-N2) and the node set they touch (Q-EF-7)
                ops = (grp[0]["detail"] or {}).get("ops")
            allowed = set((grp[0]["detail"] or {}).get("nodes") or []) if eff else {grp[0]["node"]}
            cid = self.store.add_candidate(wid, it, [h["hypothesis_id"] for h in grp], grp[0]["node"], [])
            if not ops:
                self.store.candidate_update(cid, outcome="discarded", check_result={"why": "버림 — 변경 줄을 만들 수 없음"})
                continue
            k = ops_key(ops)
            if not eff and (k in seen or k in before):
                self.store.candidate_update(cid, outcome="discarded", check_result={"why": DROP_ROW_TRIED if k in seen else DROP_ROW_EARLIER.format(seq=before[k])})
                continue
            seen.add(k)
            try:
                lines, revision, view = self.ops.candidate_lines(rel, ops)
            except Exception as e:   # noqa: BLE001 - the structure check refused the change
                self.store.candidate_update(cid, outcome="discarded", check_result={"why": f"버림 — 검사 {check_error(e)}"})
                continue
            changed = {l.get("node") for l in lines if l.get("node")}
            if not changed <= allowed or not view.get("ok"):
                why = "버림 — 가설 node 밖을 바꿈(D-99)" if not changed <= allowed else "버림 — 구조 검사 " + "; ".join(e.get("message", "") for e in view.get("errors", []))[:120]
                self.store.candidate_update(cid, outcome="discarded", check_result={"why": why, **view})
                continue
            if eff:   # EV-069 CHG-EF-4: the row's Pack against the base's confirmed fixed flow (packrt check_fixed_flow) — no check, no row
                check = getattr(self.ops, "fixed_flow_check", None)
                try:
                    broken = check(rel, lines) if check else [{"message": "고정 흐름 검사 길 없음"}]
                except Exception as e:   # noqa: BLE001 - a check that cannot run keeps the row out
                    broken = [{"message": f"고정 흐름 검사 실패({type(e).__name__})"}]
                if broken:
                    self.store.candidate_update(cid, outcome="discarded", check_result={"why": "버림 — " + broken[0].get("message", "")[:160], "fixed_flow": broken, **view})
                    continue
                if not state.get("struct_agent") and _eff_unreached([self.ops.run(u) for u in state.get("baseline") or []], grp[0]["node"]):   # S-4 (D-462 A: ④ checked its node set)
                    self.store.candidate_update(cid, outcome="discarded", check_result={"why": DROP_UNREACHED, **view})
                    continue
            else:   # EV-079 (D-463 덧 9): fixed n · broken m count only rows that keep the base's confirmed fixed flow (none confirmed: nothing to check)
                check = getattr(self.ops, "fixed_flow_check", None)
                try:
                    broken = check(rel, lines, unconfirmed=[]) if check else []
                except Exception as e:   # noqa: BLE001 - a check that cannot run keeps the row out
                    broken = [{"message": f"검사 실패({type(e).__name__})"}]
                if broken:
                    self.store.candidate_update(cid, outcome="discarded", check_result={"why": DROP_FIXED_FLOW + broken[0].get("message", "")[:160], "fixed_flow": broken, **view})
                    continue
            step = getattr(self.ops, "index_step", None)
            if step is not None:   # EV-051 CHG-W2-05: the row's vector DB version, inside rows (D-270)
                try:
                    got = step(rel, {"wheel_id": wid, "candidate_id": cid, "lines": lines, "revision": revision}, budget, spent)
                except Exception as e:  # noqa: BLE001 - packs.IndexStepError or a step failure: the row is discarded with its reason
                    why = str(e)
                    self.store.candidate_update(cid, outcome="discarded", check_result={"why": why if why.startswith("버림") else DROP_INDEX.format(why=why[:120]), **view})
                    continue
                if got:
                    view = {**view, **got}
                    for b in got["index_builds"]:
                        idx_made["built" if b["status"] == "built" else "reused"] += 1   # 'exists' = there already: 다시 씀
            self.store.candidate_update(cid, revision=revision, check_result=view)
            self.store.candidate_update(cid, lines=lines)
            made.append(cid)
        save(batch_no=no, phase="cheap", hyp_ids=[h["hypothesis_id"] for h in hyps])
        idx = f" · 벡터 DB version 만듦 {idx_made['built']} · 다시 씀 {idx_made['reused']}" if any(idx_made.values()) else ""
        self.store.wheel_log(wid, 5, f"반복 {it} · 묶음 {'처음' if no == 1 else f'대기 {no - 1}'} — 후보 줄 {len(groups)}개(시험 {len(made)} · 버림 {len(groups) - len(made)}){idx}"
                                     + (f" · {ONE_AT_A_TIME}" if self.real else ""))
        return

    live = [r for r in rows if r["outcome"] not in ("discarded", "table_out")]
    harness = _harness(self.settings) if self.settings.mode == "fake" else {}

    def improved(r):   # the test harness only (isolated-env): which rows make their predicted fix cases pass in fake mode
        hs = {h["hypothesis_id"]: h for h in self.store.hypotheses(wid)}
        return any(any(x.get("node") == hs[i]["node"] and x.get("knob") == hs[i]["knob"] and x.get("value") == hs[i]["value"]
                       for x in harness.get("improve") or []) for i in r["hypothesis_ids"] if i in hs)

    def finished(uids):
        """The row's runs once all ended; None while one runs; LOST when one is gone (the server started again — ML3-B1 (e))."""
        runs = [self.ops.shadow_run(u) for u in uids]
        alive = getattr(self.ops, "shadow_alive", lambda u: True)
        if any(x is None or (x["status"] in ("대기", "실행 중") and not alive(u)) for u, x in zip(uids, runs)):
            return LOST   # gone from memory (a restart) or its run thread ended without finishing the run
        return None if any(x["status"] in ("대기", "실행 중") for x in runs) else runs

    def lost(r, what):
        self.store.wheel_log(wid, 5, f"줄 {r['candidate_id']} {what} 실행 기록 잃음(서버가 다시 뜸) — 다시 실행")

    def apply_harness(r, run):
        if improved(r):
            for cid_, c in zip(ids if len(run["cases"]) == len(ids) else (r["cheap_test"] or {}).get("cases", []), run["cases"]):
                if sets.get(cid_) == "fix":
                    c["state"] = "완료"
                    for v in c.get("visits") or []:
                        for chk in (v.get("checks") or []):
                            chk["passed"] = True
        return run

    kept = []

    def keep(uid, run):   # the run as it was judged goes into the row's record (CHG-188); its in-memory copy goes once that is saved
        kept.append(uid)
        return run

    def drop_kept():   # after the row record is written (critic ML-3 review 2: save first, then let the memory copy go)
        for uid in kept:
            getattr(self.ops, "shadow_drop", lambda u: None)(uid)
        kept.clear()

    field = state.get("field") or {}   # EV-065 CHG-266a: the trigger cases' field-truth expected values
    expect = _expect(self, state, w)   # EV-079: an accuracy wheel's final checks
    summ = lambda run: {**autocand.summarize(run, ids, sets, base_ok, real=self.real, final=expect),
                        **({"field": len(field), "field_fixed": len(autocand.field_fixed(run, ids, field))} if field else {})}

    if cs["phase"] == "cheap":
        pending, base_run = False, None
        hs_all = {h["hypothesis_id"]: h for h in self.store.hypotheses(wid)}
        grid_of = lambda r: any(((hs_all.get(i) or {}).get("detail") or {}).get("grid") for i in r["hypothesis_ids"])   # noqa: E731 - D-549 B
        busy = sum(1 for r in live if grid_of(r) and "runs" in (r["cheap_test"] or {}) and "changed" not in (r["cheap_test"] or {}))
        for r in live:
            ct = r["cheap_test"] or {}
            if is_ret(r) or eff:   # recall can go up while every case's outcome stays: a retriever row goes to the table untested
                if "skipped" not in ct:   # EV-069 (A1-B1): an efficiency row is meant to change no outcome — the same
                    self.store.candidate_update(r["candidate_id"], cheap_test={"skipped": CHEAP_SKIPPED_EFF if eff else CHEAP_SKIPPED})
                continue
            if "runs" not in ct:
                if grid_of(r) and busy >= autogrid.PARALLEL:   # D-549 B: a space's cheap tests a few at a time
                    pending = True
                    continue
                busy += grid_of(r)
                hs = {h["hypothesis_id"]: h for h in self.store.hypotheses(wid)}
                pred = [c for c in ids if c in visible and any(c in ((hs[i]["detail"] or {}).get("predicts") or []) for i in r["hypothesis_ids"] if i in hs)]
                if base_run is None:
                    base_run = self.ops.run(state["baseline"][0]) if state.get("baseline") else {}
                more = [c for c in checked_cheap(ids, {k for k, v in sets.items() if v == "fix"}, checked_cases(self.store, pid, w), base_run,
                                                 {hs[i]["node"] for i in r["hypothesis_ids"] if i in hs}) if c not in pred]   # D-525 덧 34
                pred += more
                if self.real and self.over_cap(UNIT_SHORT * len(pred)):
                    return self._hold(w, state, "stopped", self.cap_hold())
                self.reserve(w, f"cheap:{r['candidate_id']}", UNIT_SHORT * len(pred))
                uid = self.ops.run_row(pid, rel, r, state["suites"]["work"], pred, judge=False)
                self.store.candidate_update(r["candidate_id"], cheap_test={"cases": pred, "runs": [uid], **({"checked": more} if more else {})})   # D-525 덧 36
                pending = True
                continue
            if "changed" in ct:
                continue
            runs = finished(ct["runs"])
            if runs is LOST:
                lost(r, "싼 시험")
                self.store.candidate_update(r["candidate_id"], cheap_test={"cases": ct["cases"]})
                busy -= grid_of(r)
                pending = True
                continue
            if runs is None:
                pending = True
                continue
            run = keep(ct["runs"][0], apply_harness(r, runs[0]))
            changed = cheap_changed(ct["cases"], run["cases"], base_ok, lambda cid_: (expect or {}).get(cid_) or None,   # D-463 덧 28
                                    autocand.on_fail_of(run.get("graph")))
            paid, pusd = run_spend(run)   # D-506: that Run's own llm and retriever visits (measured where usage came back), as the table rows
            self.record(w, "⑤ 싼 시험", paid, pusd, spent_note("추정 · Pack", [run]), key=f"cheap:{r['candidate_id']}")
            ct.update(changed=changed, record=autocand.compact(run, ct["cases"]))
            if grid_of(r):   # D-549 B: fixed · broken the table's way, on the cases the cheap test ran
                busy -= 1
                g = autocand.summarize(run, ct["cases"], sets, base_ok, final=expect)
                if base_run is None:
                    base_run = self.ops.run(state["baseline"][0]) if state.get("baseline") else {}
                had = {c: autogrid.records_of(x, r["node"]) for c, x in zip(ids, base_run.get("cases") or [])}
                ct["grid"] = {"fixed": g["fixed"], "broken": len(g["broken"]), "cut": autogrid.cut_records(ct["cases"], [autogrid.records_of(x, r["node"]) for x in run["cases"]], had),
                              "field": len(autocand.field_fixed(run, ct["cases"], field))}
            self.store.candidate_update(r["candidate_id"], cheap_test=ct, **({"outcome": "discarded"} if not changed else {}))
            drop_kept()
        if pending:
            return
        _grid_first(self, wid, [r["candidate_id"] for r in rows], hs_all)   # the batch's rows, the ones discarded on an earlier pass too
        save(phase="table")
        return

    if cs["phase"] == "table":
        pending = False
        targets = [x for x in live if (x["cheap_test"] or {}).get("changed") or (x["cheap_test"] or {}).get("skipped")]
        # EV-060 CHG-250: in real mode one table Run at a time — counted once before the rows (A2-B2); a row emptied as lost counts
        # as started on this pass
        running = self.real and any("runs" in (x["table_result"] or {}) and "columns" not in x["table_result"] and finished(x["table_result"]["runs"]) is None
                                    for x in targets)
        started = False
        for r in targets:
            tr = r["table_result"] or {}
            if "columns" in tr:
                continue
            if "runs" not in tr:
                if self.real and (running or started):   # before the cost check (A3-N2): the row waits its turn
                    pending = True
                    continue
                started = True
                unit = self.case_unit(w, state)
                if self.real and self.over_cap(unit * len(ids)):
                    return self._hold(w, state, "stopped", self.cap_hold())
                self.reserve(w, f"table:{r['candidate_id']}", unit * len(ids))
                uid = self.ops.run_row(pid, rel, r, state["suites"]["work"], ids, judge=not eff)
                self.store.candidate_update(r["candidate_id"], table_result={"runs": [uid]}, executions=1)
                pending = True
                continue
            runs = finished(tr["runs"])
            if runs is LOST:
                lost(r, "표")
                self.store.candidate_update(r["candidate_id"], table_result={}, executions=0)
                pending = started = True
                continue
            if runs is None:
                pending = True
                continue
            run = keep(tr["runs"][0], apply_harness(r, runs[0]))
            s = summ(run)
            c, u = run_spend(run)
            self.record(w, "⑤ 후보 표", c, u, spent_note("추정 · Pack · 채점", [run]), key=f"table:{r['candidate_id']}")
            if eff:   # EV-069 CHG-EF-5: judge-free case outcomes · the efficiency columns
                s = {**autocand.eff_summarize(run, ids, sets, base_ok, wcases), "efficiency": autocand.eff_measures(run, chars), "error_tag": autocand.error_tag(run)}
                why = _eff_criteria(s, ebase, goals, tol)
            elif is_ret(r):
                why = _ret_criteria(self, s, run, rnode, rbase, base_tok, base_lat, base_judge, tol, by_budget(r))
                s = {**s, "retrieval": {"node": rnode, "row": autocand.ret_of(run, rnode, rbase.get("budget_chars")), "base": rbase, "by_budget": by_budget(r)}}
            else:
                why = _criteria(self, s, base_tok, base_lat, base_judge, tol)
            tr.update(columns={**{k: v for k, v in s.items() if k != "by_case"}, "calls": c}, criteria=why, records=[run])   # LLM 호출 (CHG-182 ③)
            self.store.candidate_update(r["candidate_id"], table_result=tr, **({"outcome": "table_out"} if why else {}))
            if why:
                order_not_applied(self, w, r, why, f"줄 {r['candidate_id']}", cell=5)   # eval T9: a check-order row out of the table
            drop_kept()
        if pending:
            return
        rows = [r for r in self.store.candidates(wid) if r["candidate_id"] in {x["candidate_id"] for x in rows}]

        def rank_key(r):   # a retriever row by recall@10, then tokens (CHG-211); other rows by cases fixed, then tokens (D-121)
            col = r["table_result"]["columns"]
            if eff:   # EV-069: the chosen goals (Q-EF-5 order), then fewer change lines
                return autocand.eff_rank_key(col.get("efficiency") or {}, goals, len(r.get("lines") or []))
            if is_ret(r):
                ret = col.get("retrieval") or {}   # D-492 덧 16: a cut · chunk row by its budget recall
                return (-((ret.get("row") or {}).get("budget" if ret.get("by_budget") else "recall") or {"v": -1})["v"], col["tokens"])
            return (-col["fixed"], col["tokens"])
        passing = sorted([r for r in rows if (r["table_result"] or {}).get("columns") and not (r["table_result"] or {}).get("criteria") and r["outcome"] != "table_out"],
                         key=rank_key)
        resp = state.get("resp") or {}
        if not eff and any(covering(r, resp) for r in passing):   # D-549 덧 3 A-2
            passing = covering_last(passing, resp)
            for r in passing:
                if covering(r, resp):
                    nodes_of_row = sorted({ln.get("node") for ln in r.get("lines") or [] if ln.get("node")})
                    self.store.candidate_update(r["candidate_id"], table_result={**(r["table_result"] or {}), "covering": COVERING.format(node=resp["node"], nodes=" · ".join(nodes_of_row))})
            if covering(passing[0], resp):
                self.store.wheel_log(wid, 5, COVERING.format(node=resp["node"], nodes=" · ".join(sorted({ln.get("node") for ln in passing[0].get("lines") or [] if ln.get("node")}))))
        for k, r in enumerate(passing, 1):
            self.store.candidate_update(r["candidate_id"], rank=k)
        for r in rows:   # a row out of the table keeps the case outcome of its run (paths · checks · state · tokens · ms · output sha), not the whole run
            tr = r["table_result"] or {}
            if tr.get("records") and r not in passing:
                self.store.candidate_update(r["candidate_id"], table_result={**tr, "records": [autocand.compact(x, ids) for x in tr["records"]]})
        if eff and len(passing) >= 2 and not cs.get("merged"):   # D-470 ⓒ: once per batch, before the ranks are kept
            save(merged=True)
            if _merge_row(self, wid, it, rel, passing):
                return   # its table Run on a later pass, then the ranks with it
        if not passing:
            save(phase="rows")
            self.store.wheel_log(wid, 5, "이 묶음에서 판정 기준을 통과한 후보 줄 없음 — 다음 묶음으로")
            return
        save(phase="confirm", winner=passing[0]["candidate_id"])
        self.store.wheel_log(wid, 5, f"후보 표 1등 — 줄 {passing[0]['candidate_id']} · 같은 작업 suite로 2번 더(D-97)")
        return

    if cs["phase"] == "confirm":
        r = next(x for x in self.store.candidates(wid) if x["candidate_id"] == cs["winner"])
        tr = r["table_result"]
        recs = tr.get("records") or []
        if len(recs) < 3 and not tr.get("pending"):   # one repeat run at a time (the row's copy is not run twice at once)
            unit = self.case_unit(w, state)
            if self.real and self.over_cap(unit * len(ids)):
                return self._hold(w, state, "stopped", self.cap_hold())
            self.reserve(w, f"confirm:{r['candidate_id']}", unit * len(ids))
            tr["pending"] = [self.ops.run_row(pid, rel, r, state["suites"]["work"], ids, judge=not eff)]
            self.store.candidate_update(r["candidate_id"], table_result=tr, executions=len(recs) + 1)
            return
        if tr.get("pending"):
            runs = finished(tr["pending"])
            if runs is LOST:
                lost(r, "반복")
                tr["pending"] = []
                self.store.candidate_update(r["candidate_id"], table_result=tr)
                return
            if runs is None:
                return
            calls = usd = 0
            for uid, run in zip(tr["pending"], runs):
                recs.append(keep(uid, apply_harness(r, run)))
                c, u = run_spend(run)
                calls, usd = calls + c, usd + u
            self.record(w, "⑤ 1등 반복", calls, usd, spent_note("추정 · Pack · 채점", runs), key=f"confirm:{r['candidate_id']}")
            tr.update(records=recs, pending=[])
            self.store.candidate_update(r["candidate_id"], table_result=tr)
            drop_kept()
            if len(recs) < 3:
                return
        if eff:   # D-655: a kept case the row broke in a repeat — the base runs again with the same suite up to the row's Runs first
            hit = sorted({c for run in recs[1:] for c in autocand.eff_summarize(run, ids, sets, base_ok, wcases)["broken"]})
            more = cs.get("base_more") or []
            if more and any((self.ops.run(u) or {}).get("status") in ("대기", "실행 중") for u in more):
                return
            if more and not cs.get("base_more_spent"):
                bruns = [self.ops.run(u) for u in more]
                calls = usd = 0
                for x in bruns:
                    c, u = run_spend(x)
                    calls, usd = calls + c, usd + u
                self.record(w, BASE_AGAIN_STEP, calls, usd, spent_note("추정 · Pack", bruns), key="base-again")
                save(base_more_spent=True)
                return   # the next pass counts the base with it
            if hit and base_n < len(recs):
                unit = self.case_unit(w, state)
                if self.real and self.over_cap(unit * len(ids)):
                    return self._hold(w, state, "stopped", self.cap_hold())
                try:
                    uid = self.ops.start_suite_run(pid, w["base_release_id"], state["suites"]["work"],
                                                   {"wheel": w["seq"], "role": "baseline", "text": f"자동 · 바퀴 A-{w['seq']} · 흔들림 기준선 {base_n + 1}/{len(recs)}(반복 판정)"},
                                                   judge=False)
                except Exception as e:   # noqa: BLE001 - e.g. a person's open edit of the 운영 version: tried again on a later pass
                    return self._hold(w, state, "waiting", f"기준선 Run을 다시 시작하지 못함 — {str(getattr(e, 'detail', e))[:120]} · 다음에 다시")
                self.reserve(w, "base-again", unit * len(ids))
                save(base_more=more + [uid], base_more_spent=False)
                self.store.wheel_log(wid, 5, BASE_AGAIN.format(row=r["candidate_id"], n=len(hit), cases=", ".join(map(str, hit)), k=base_n + 1, m=len(recs)))
                return
            if shaky:
                tr["noise"] = {"cases": shaky, "base_runs": base_n}
                self.store.candidate_update(r["candidate_id"], table_result=tr)
                if not cs.get("noise_said") == r["candidate_id"]:
                    save(noise_said=r["candidate_id"])
                    self.store.wheel_log(wid, 5, NOISE_LINE.format(row=r["candidate_id"], words=autocand.noise_text(shaky), n=base_n))
        whys = []
        for run in recs[1:]:
            if eff:
                whys += _eff_criteria({**autocand.eff_summarize(run, ids, sets, base_ok, wcases), "efficiency": autocand.eff_measures(run, chars), "error_tag": autocand.error_tag(run)}, ebase, goals, tol)
                continue
            whys += (_ret_criteria(self, summ(run), run, rnode, rbase, base_tok, base_lat, base_judge, tol, by_budget(r)) if is_ret(r)
                     else _criteria(self, summ(run), base_tok, base_lat, base_judge, tol))
        def out_and_next(criteria, line):   # the row out of the table and the table's next row up (D-122)
            tr.update(criteria=criteria, records=[autocand.compact(x, ids) for x in recs])
            self.store.candidate_update(r["candidate_id"], table_result=tr, outcome="table_out")
            order_not_applied(self, w, r, criteria, f"줄 {r['candidate_id']}", cell=5)
            self.store.wheel_log(wid, 5, f"줄 {r['candidate_id']} {line} · 다음 줄을 올림(D-122)")
            nxt = [x for x in self.store.candidates(wid) if x.get("rank") and x["outcome"] not in NOT_NEXT and x["candidate_id"] != r["candidate_id"]
                   and x["candidate_id"] in {y["candidate_id"] for y in rows}]
            nxt.sort(key=lambda x: x["rank"])
            if nxt:
                save(winner=nxt[0]["candidate_id"])
            else:
                save(phase="rows")

        if whys:
            criteria = sorted(set(whys))
            return out_and_next(criteria, f"판정 기준 탈락(반복 실행) — {', '.join(criteria)}")
        if eff and ("quality" not in tr or tr["quality"].get("failed")):   # D-462 B: the quality review before the row becomes a version
            # (⑥ 앞); D-495 덧 20: a failed one is asked again on every pass and holds the stop — never read as done
            try:
                q = _quality_review(self, w, state, r, recs[0], ids, wcases)
            except (autoreview.ReviewError, gatewaymod.DecideError) as e:
                text, again = autoreview.FAILED.format(code=e.code), (tr.get("quality") or {}).get("failed") == e.code
                tr["quality"] = {"failed": e.code}
                self.store.candidate_update(r["candidate_id"], table_result=tr)
                if again:   # D-495 덧 20: still failing the same way — the same stop held again, said once
                    self.store.set_wheel_state(wid, {**state, "hold": text})
                    self.store.wheel_update(wid, cell_state="stopped")
                    return
                return self._hold(w, state, "stopped", text)
            if q is None:
                return self._hold(w, state, "stopped", self.cap_hold())
            tr["quality"] = q
            self.store.candidate_update(r["candidate_id"], table_result=tr)
            if q.get("skipped") == autoreview.SKIP_OFF:   # D-495 덧 20: switched off on purpose — the wheel log says so too
                self.store.wheel_log(wid, 5, f"줄 {r['candidate_id']} 품질 검토 {autoreview.SKIP_OFF}")
            if q.get("verdict") == "drop":
                line = autoreview.reason(q["findings"])
                return out_and_next([line], line)
            if q.get("verdict") == "human":
                return self._hold(w, state, "done", autoreview.HUMAN.format(row=r["candidate_id"]), True)
        qh = (tr.get("quality") or {}).get("human") if eff else None   # D-652 다섯째: the person's pass or drop of a held row
        if eff and (tr.get("quality") or {}).get("verdict") == "human" and not qh:
            return self._hold(w, state, "done", autoreview.HUMAN.format(row=r["candidate_id"]), True)
        if qh and qh.get("decision") == "drop":
            line = autoreview.HUMAN_DROP.format(by=qh.get("by"), reason=qh.get("reason"))
            return out_and_next([line], line)
        if is_ret(r) and rbase["human"]:   # D-265 Q-5: the person-checked gold goes the other way in any of the three Runs → a person decides
            low = [k for k, run in enumerate(recs, 1) if (autocand.ret_of(run, rnode)["human"] or {"v": -1})["v"] < rbase["human"]["v"]]
            if low:
                tr.update(human_low=low, records=[autocand.compact(x, ids) for x in recs])
                self.store.candidate_update(r["candidate_id"], table_result=tr)
                return self._hold(w, state, "done", END_HUMAN.format(row=r["candidate_id"]), True)
        save(phase="version")
        return

    if cs["phase"] == "version":
        r = next(x for x in self.store.candidates(wid) if x["candidate_id"] == cs["winner"])
        recs = (r["table_result"] or {}).get("records") or []
        if len(recs) < 3 or any("summary" not in x for x in recs):   # ML3-B1 (c): the three run records first, then the registration
            save(phase="confirm")
            self.store.wheel_log(wid, 5, f"줄 {r['candidate_id']} 실행 기록이 3개가 아님 — 반복 실행부터 다시")
            return
        label = " + ".join(f"H-{i}" for i in r["hypothesis_ids"])
        if not cs.get("version") and r.get("release_id"):   # registered, then stopped before the state was saved: go on with that version
            if eff:   # slice review 1 N3: the inherited fixed flow on this path too
                fixedflow.inherit(self.store, self.store.release(r["release_id"]), rel)
            save(version=r["release_id"], written=[], hidden_runs=[])
        if not cs.get("version"):   # ML3-B1 (b): registered once — a later pass after an error goes on from what was written
            try:
                new = self.ops.make_version(pid, rel, r, {"wheel": w["seq"], "hypotheses": r["hypothesis_ids"], "row": r["candidate_id"], "label": label},
                                            f"✦ 자동 바퀴 A-{w['seq']} · 가설 {label}")
            except Exception as e:   # noqa: BLE001 - nothing of a failed registration is kept (PRES-181); the wheel stops with the reason, no retry (F-4)
                return self._hold(w, state, "stopped", STOP_VERSION.format(why=str(getattr(e, "detail", e))[:120]), True)
            self.store.candidate_update(r["candidate_id"], release_id=new["id"])
            if eff:   # EV-069 CHG-EF-1: the new version keeps the base's confirmed fixed flow (the maker cannot change it)
                fixedflow.inherit(self.store, self.store.release(new["id"]), rel)
            save(version=new["id"], written=[], hidden_runs=[])
        new = self.store.release(cs["version"])
        if not new or (new.get("pack") or {}).get("revision") != r["revision"]:   # CHG-183: the version is the row's revision, or nothing is written
            return self._hold(w, state, "stopped", STOP_REVISION, True)
        written = list(cs.get("written") or [])
        for k in range(len(written), 3):
            written.append(self.ops.write_run(pid, new, recs[k], {"wheel": w["seq"], "role": "row", "text": f"⑤ 자동 표 실행을 다시 씀 · 바퀴 A-{w['seq']}"}))
            save(written=written)
        hidden = state["suites"].get("hidden")
        hruns = list(cs.get("hidden_runs") or [])
        conf = state["suites"].get("confirm")
        if conf and not hruns:   # EV-043 CHG-211: a retriever wheel runs its confirm suite (test) once on the new version, never to choose
            n_c = len(self.store.suite(conf)["cases"])
            unit = self.case_unit(w, state)
            if self.real and self.over_cap(unit * n_c * (2 if eff else 1)):
                return self._hold(w, state, "stopped", self.cap_hold())
            self.reserve(w, "hidden:1", unit * n_c)
            kw = {"judge": False} if eff else {}
            hruns.append(self.ops.start_suite_run(pid, new["id"], conf, {"wheel": w["seq"], "role": "confirm", "text": f"자동 · 바퀴 A-{w['seq']} · 확인 suite(test) 한 번"}, **kw))
            save(hidden_runs=hruns)   # slice review 1 B3: kept before the 운영 side starts (a failed second start never orphans the first)
        if eff and conf and len(hruns) == 1 and not cs.get("confirm_base_missing"):   # EV-069 CHG-EF-3 (A1-B7): the 운영 version's confirm Run —
            prev = _confirm_run_of(self, w["project_id"], w["base_release_id"], conf)   # a finished judge-free one of the same suite is written again
            if prev:                                                                    # (D-122), else once now
                hruns.append(prev)
            else:
                n_c = len(self.store.suite(conf)["cases"])
                if self.real and self.over_cap(self.case_unit(w, state) * n_c):
                    return self._hold(w, state, "stopped", self.cap_hold())
                self.reserve(w, "hidden:2", self.case_unit(w, state) * n_c)
                try:
                    hruns.append(self.ops.start_suite_run(pid, w["base_release_id"], conf, {"wheel": w["seq"], "role": "confirm",
                                                                                           "text": f"자동 · 바퀴 A-{w['seq']} · 확인 suite(test) 운영 버전 한 번"}, judge=False))
                except Exception as e:   # noqa: BLE001 - slice review 2: the reservation goes back and ⑥ reads '확인 검증 없음' (contract CHG-EF-6)
                    self.reserved[(wid, "hidden:2")] = 0.0
                    save(confirm_base_missing=True)
                    self.store.wheel_log(wid, 5, f"확인 suite 운영 버전 Run을 시작하지 못함 — {str(getattr(e, 'detail', e))[:120]} · 확인 검증 없음")
            save(hidden_runs=hruns)
        if hidden and len(hruns) < 3:
            n_h = len(self.store.suite(hidden)["cases"])
            unit = self.case_unit(w, state)
            if self.real and self.over_cap(unit * n_h * (3 - len(hruns))):
                return self._hold(w, state, "stopped", self.cap_hold())
            for k in range(len(hruns) + 1, 4):
                self.reserve(w, f"hidden:{k}", unit * n_h)
                hruns.append(self.ops.start_suite_run(pid, new["id"], hidden, {"wheel": w["seq"], "role": "hidden", "text": f"자동 · 바퀴 A-{w['seq']} · 숨긴 case {k}/3"}))
                save(hidden_runs=hruns)
        if not eff and "replay" not in cs and getattr(self.ops, "replay_inputs", None):   # D-477 (1): the operational record replayed
            got = _replay_start(self, w, state, new, save)
            if got is not None:
                return got
        self.store.candidate_update(r["candidate_id"], outcome="winner")
        save(phase="done", version_runs=written)
        self.store.wheel_log(wid, 5, f"⑤ 1등 후보 줄 → 새 버전 {new['name']} ✦ 자동 · 가설 {label} · 평가 {' · '.join(written)}"
                             + (" · 확인 suite(test) 한 번" if conf else f" · 숨긴 case {len(hruns)}번" if hruns else " · 숨긴 case 검증 없음"))
        self.store.wheel_update(wid, cell=6, cell_state="running")
        self.store.wheel_log(wid, 6, START_6_EFF if eff else START_6_RET if conf else START_6)


def _replay_start(self, w, state, new, save):
    """D-477 (1) at ⑤'s winner: every operational input of the wheel's 운영 version (newest first, REPLAY_MAX) as one registered suite
    (like the hidden one; no promotion row) — the new version runs it once, the 운영 version twice (its own spread). An input without a
    raw value is kept as 비교 불가. The cost goes into the wheel's count (the same cap). Returns a hold to stop, or None to go on."""
    wid, pid = w["wheel_id"], w["project_id"]
    try:
        got = self.ops.replay_inputs(pid, w["base_release_id"], REPLAY_MAX) or []
    except Exception as e:   # noqa: BLE001 - no operational record (logDB down · not deployed): ⑥ says so, the wheel goes on
        save(replay={"error": str(getattr(e, "detail", e))[:120], "inputs": []})
        return None
    inputs = [x for x in got if x.get("target", True) is not False]   # D-476 덧 7: only the label file's review targets (EV-077)
    skipped = len(got) - len(inputs)
    rows = [{"case_id": f"rp-{x['run_id']}", "input": x["input"]} for x in inputs if x.get("input") is not None]
    rp = {"inputs": [{"run_id": x["run_id"], "group": x.get("group")} for x in inputs], "runs": [], "base_runs": []}
    if not rows:
        save(replay=rp)
        return None
    unit = self.case_unit(w, state)
    if self.real and self.over_cap(unit * len(rows) * 3):
        return self._hold(w, state, "stopped", self.cap_hold())
    reg = self.ops.register_subset(pid, state["suites"]["base"], [], rows)
    rp["suite"] = reg["id"]
    tag = lambda role, k, what: {"wheel": w["seq"], "role": role, "text": f"자동 · 바퀴 A-{w['seq']} · 운영 기록 다시 돌리기 {what}"}
    self.reserve(w, "replay:1", unit * len(rows))
    rp["runs"].append(self.ops.start_suite_run(pid, new["id"], reg["id"], tag("replay", 1, "새 버전")))
    save(replay=rp)
    for k in (1, 2):
        self.reserve(w, f"replay:{k + 1}", unit * len(rows))
        rp["base_runs"].append(self.ops.start_suite_run(pid, w["base_release_id"], reg["id"], tag("replay-base", k, f"운영 버전 {k}/2")))
        save(replay=rp)
    self.store.wheel_log(wid, 5, f"운영 기록 다시 돌리기 — 운영 Run {len(inputs)}건(원문 {len(rows)})" + (f" · 검토 대상 아님 {skipped}건 뺌" if skipped else "")
                         + " · 새 버전 1번 · 운영 버전 2번")
    return None


def _replay_spend(self, w):
    """D-477 (1): the replay Runs' spend, written once per Run as each ends. True when all have ended (or there are none)."""
    wid = w["wheel_id"]
    state = self.store.wheel_state(wid)
    cs = state.get("cand") or {}
    rp = cs.get("replay") or {}
    done, all_ended = set(rp.get("spent") or []), True
    for k, uid in enumerate((rp.get("runs") or []) + (rp.get("base_runs") or []), 1):
        run = self.ops.run(uid)
        if run is None or run["status"] in ("대기", "실행 중"):
            all_ended = False
            continue
        if uid not in done:
            c, u = run_spend(run)
            self.record(w, "⑥ 운영 기록 다시 돌리기", c, u, spent_note("추정 · Pack · 채점", [run]), key=f"replay:{k}")
            done.add(uid)
            rp["spent"] = sorted(done)
            cs["replay"] = rp
            state["cand"] = cs
            self.store.set_wheel_state(wid, state)
    return all_ended


def _replay_rows(self, w, state, rec, ids, sets):
    """D-477 (1) · (3): the two ⑥ rows from the replay Runs and the work suite — the replay row by case (trace of that time · the 운영
    version's two Runs · the new version's), and 증거 세기 over the inputs once each (keep cases not excluded · the replay's 같음 · 바뀜 with
    truth; by input sha). Pack names: none (kinds from the graph, groups from the label file)."""
    pid, base_rid = w["project_id"], w["base_release_id"]
    rp = (state.get("cand") or {}).get("replay") or {}
    rel = self.store.release(base_rid) or {}
    nodes = (rel.get("graph") or {}).get("nodes") or []
    kinds = {n.get("id") or n.get("name"): n.get("kind") for n in nodes}
    gdir = self.settings.field_gold_dir
    gold = autodiag.field_gold_lines(gdir, set(kinds)) if gdir else {}
    new_run = self.ops.run(rp["runs"][0]) if rp.get("runs") else None
    b_runs = [self.ops.run(u) for u in rp.get("base_runs") or []]
    idx = {c["id"]: i for i, c in enumerate(self.store.suite(rp["suite"])["cases"])} if rp.get("suite") else {}
    case_at = lambda run, i: ((run or {}).get("cases") or [])[i] if run and i < len((run or {}).get("cases") or []) else None
    results = []
    for x in rp.get("inputs") or []:
        i = idx.get(f"rp-{x['run_id']}")
        try:
            trace = self.ops.ops_outputs(pid, base_rid, x["run_id"]) if i is not None else None
        except Exception:   # noqa: BLE001 - that Run cannot be read now: 비교 불가
            trace = None
        res = autocand.replay_case(trace, [case_at(b, i) for b in b_runs] if i is not None else [], case_at(new_run, i) if i is not None else None,
                                   kinds, gold=gold.get(x["run_id"]))
        results.append({**res, "run": x["run_id"], "group": x.get("group") or opsruns.GROUP_ALL, "i": i})
    rrow = autocand.replay_row(results) if not rp.get("error") else {"name": autocand.REPLAY_ROW, "ok": None, "text": f"운영 기록 읽지 못함 — {rp['error']}"}
    # 증거 세기: one item per input
    gspec = getattr(self.ops, "group_spec", lambda rid: None)(base_rid)
    work = {c["id"]: c for c in self.store.suite(state["suites"]["work"])["cases"]}
    cols = (rec.get("columns") or {}).get("new") or {}
    broken = set(cols.get("broken") or [])
    items, seen = [], set()
    for cid in ids:
        if sets.get(cid) != "keep":
            continue
        inp = (work.get(cid) or {}).get("input")
        seen.add(input_sha(inp))
        items.append({"group": opsruns.group_of(inp, gspec), "ok": not autocand.broken_case(broken, cid)})
    rcases = self.store.suite(rp["suite"])["cases"] if rp.get("suite") else []
    for r in results:
        if r["cls"] in (autocand.REPLAY_SAME, autocand.REPLAY_SAME_TRUTH, autocand.REPLAY_BETTER, autocand.REPLAY_WORSE) and r["i"] is not None:
            sha = input_sha(rcases[r["i"]].get("input"))
            if sha in seen:
                continue
            seen.add(sha)
            items.append({"group": r["group"], "ok": r["cls"] != autocand.REPLAY_WORSE})
    erow = autocand.evidence_row(items, fixed=cols.get("fixed", 0))
    return rrow, erow


Main.replay_spend = _replay_spend


def _confirm_run_of(self, pid, rid, sid):
    """A finished judge-free Run of `sid` on release `rid` (the same suite revision, the runtime the server runs now — a Run of an
    older packrt is not the same condition) — the efficiency wheel's 운영-side confirm Run to write again, or None (slice review 1 B3)."""
    import packrt
    su = self.store.suite(sid) or {}
    for r in reversed((self.store.project(pid) or {}).get("runs") or []):
        run = self.ops.run(r["uid"]) if r.get("rel") == rid else None
        inp = (run or {}).get("input") or {}
        if run and run["status"] == "완료" and inp.get("id") == sid and inp.get("no_judge") and inp.get("suite_revision", su.get("suite_revision")) == su.get("suite_revision") \
                and ((run.get("manifest") or {}).get("runtime") or {}).get("version") == packrt.__version__:
            return run["uid"]
    return None


def _hidden_spend(self, w):
    """The hidden suite's spend, written once per Run as each ends (reserved when it started — ML3-B2). True when all have ended."""
    wid = w["wheel_id"]
    state = self.store.wheel_state(wid)
    cs = state.get("cand") or {}
    done = set(cs.get("hidden_spent") or [])
    all_ended = True
    for k, uid in enumerate(cs.get("hidden_runs") or [], 1):
        run = self.ops.run(uid)
        if run is None or run["status"] in ("대기", "실행 중"):
            all_ended = False
            continue
        if uid not in done:
            c, u = run_spend(run)
            self.record(w, "⑤ 확인 suite" if (state.get("suites") or {}).get("confirm") else "⑤ 숨긴 suite", c, u, spent_note("추정 · Pack · 채점", [run]), key=f"hidden:{k}")
            done.add(uid)
            cs["hidden_spent"] = sorted(done)
            state["cand"] = cs
            self.store.set_wheel_state(wid, state)
    return all_ended


Main.candidates = _candidates_step
Main.hidden_spend = _hidden_spend


def _hidden_basis(self, w, state, case_ids):
    """What a hidden case is held against (CHG-184): one put up from an operational Run was failing when it went up (its signal) —
    False; a case from the base suite has the base version's latest finished evaluation of that suite at the wheel's revision — its
    outcome there, or None when there is none."""
    base_sid = state["suites"]["base"]
    runs = [self.ops.run(r["uid"]) for r in self.store.project(w["project_id"])["runs"]
            if r["rel"] == w["base_release_id"] and (r.get("input") or {}).get("id") == base_sid and r["status"] in ("완료", "일부 실패")]
    runs = [r for r in runs if r and (r.get("manifest") or {}).get("pack_revision") in (None, w["base_revision"])]
    last = runs[-1] if runs else None
    cases = self.store.suite(base_sid)["cases"]
    if is_eff(w):   # an efficiency wheel reads no final checks
        cases = [{**c, "expected": None} for c in cases]
    return autocand.hidden_basis(cases, (last or {}).get("cases") or [], case_ids, graph=(last or {}).get("graph"))   # D-476 덧 5: per (case, rule)


def _compare_step(self, w, rel):
    """⑥ on one pass (CHG-184, B-REC): once the hidden suite's Runs ended, the grading agent compares the new version's Runs (R-n) with
    the baseline on the criteria table and the scorecards — never the hypotheses, the change lines or the wheel's log (D-98), no LLM
    call. The recommendation is kept on the row (it is not a verdict — PRES-182). adopt → ⑦ the person's turn; reject → the version
    stays 평가 중 with the mark, and the table's next row comes up (D-122)."""
    wid, pid = w["wheel_id"], w["project_id"]
    state = self.store.wheel_state(wid)
    cs = state.get("cand") or {}
    if cs.get("phase") != "done" or not cs.get("version"):
        return
    row = next((x for x in self.store.candidates(wid) if x["candidate_id"] == cs.get("winner")), None)
    if not row or row.get("recommendation"):
        return
    if not self.hidden_spend(w):   # the hidden suite's Runs end first (their spend is written as each ends)
        return
    if cs.get("replay") and not self.replay_spend(w):   # D-477 (1): and the replay Runs
        return
    hruns = [self.ops.run(u) for u in cs.get("hidden_runs") or []]
    ids, sets, *_ = _base_measures(self, state, w)
    tol = float((json.loads(w["settings"]) or {}).get("tolerance", TOLERANCE))
    base_runs = [self.ops.run(u) for u in state["baseline"]]
    new_runs = [self.ops.run(r["uid"]) for r in self.store.project(pid)["runs"]
                if r["rel"] == cs["version"] and (r.get("auto") or {}).get("role") == "row"]
    hidden = None
    if state["suites"].get("hidden") and hruns:
        hcases = [c["id"] for c in self.store.suite(state["suites"]["hidden"])["cases"]]
        hidden = autocand.hidden_result(hruns, _hidden_basis(self, w, state, hcases),
                                        None if is_eff(w) else autocand.hidden_finals(self.store.suite(state["suites"]["hidden"])["cases"]))   # D-476 덧 5
    if is_eff(w):   # EV-069 CHG-EF-6: the efficiency criteria · the confirm suite on both versions (new first, then the 운영 one)
        rel_base = self.store.release(w["base_release_id"]) or {}
        chars = {n["name"]: len(autohyp.prompt_text_of(rel_base, n) or "") for n in (rel_base.get("graph") or {}).get("nodes") or [] if autohyp.kind_of(n) == "llm"}
        rec = autocand.recommend_eff(base_runs, new_runs, hruns[1] if len(hruns) > 1 else None, hruns[0] if hruns else None, ids, sets,
                                     eff_of(w).get("goals") or list(autocand.GOAL_ORDER), tol, chars,
                                     work_cases=self.store.suite(state["suites"]["work"])["cases"], conf_cases=self.store.suite(state["suites"]["confirm"])["cases"],
                                     shake_runs=[self.ops.run(u) for u in cs.get("base_more") or []])   # D-655: the base Runs of the repeat judgment
    elif state.get("retriever"):   # EV-043 CHG-211: the retriever conditions per Run; the confirm suite (test) is shown only
        win = next((x for x in self.store.candidates(wid) if x["candidate_id"] == cs.get("winner")), {})
        hyp_by = {h["hypothesis_id"]: h for h in self.store.hypotheses(wid)}
        rec = autocand.recommend_retriever(base_runs, new_runs, hruns[0] if hruns else None, ids, sets, self.real, tol, run_spend, state["retriever"],
                                           field=state.get("field"), field_all=field_all(state), final=_expect(self, state, w),
                                           by_budget=autocand.budget_row([hyp_by.get(i) for i in win.get("hypothesis_ids") or []]))   # D-492 덧 16
    else:
        rec = autocand.recommend(base_runs, new_runs, hidden, ids, sets, self.real, tol, run_spend, field=state.get("field"), field_all=field_all(state),
                                 final=_expect(self, state, w))   # D-476 덧 22: by (case, rule) as ⑤ counted
    if cs.get("replay") and not is_eff(w):   # D-477 (1) · (3): the two rows; a failed replay or thin evidence holds an adopt (the person)
        rec = autocand.with_replay(rec, *_replay_rows(self, w, self.store.wheel_state(wid), rec, ids, sets))
    mspec = getattr(self.ops, "minutes_spec", lambda rid: None)(w["base_release_id"]) if not is_eff(w) else None
    if mspec:   # D-478 #8 (덧 8 (가)): 첫 걸음 헛걸음 분 — a reference row (never a criterion; 확정까지 분 stays the eval tool's)
        wc = self.store.suite(state["suites"]["work"])["cases"]
        mrow = autocand.minutes_row(base_runs[0] if base_runs else None, new_runs[0] if new_runs else None, ids,
                                    {c["id"]: c.get("input") for c in wc}, {c["id"]: c.get("expected") or {} for c in wc}, mspec)
        if mrow:
            rec["criteria"] = list(rec.get("criteria") or []) + [mrow]
    if rec.get("decision") == "adopt" and not is_eff(w):   # D-477 덧 6 · 8 · 9: the sealed test, last, only when every criterion passed
        from . import autosealed
        d = autosealed.check(self, w, row) or {"state": "none"}
        st_ = d.get("state")
        rec["criteria"] = list(rec.get("criteria") or []) + [{"name": autosealed.ROW, "ok": True if st_ == "passed" else None if st_ == "none" else False,
                                                              "text": autosealed.line(d)}]
        if st_ in ("failed", "stopped"):
            rec["decision"] = "hold"
            rec["reasons"] = [autocand.HOLD_SEALED, autosealed.line(d)] + list(rec.get("reasons") or [])
    new = self.store.release(cs["version"]) or {}
    rec.update(base={"release": w["base_release_id"], "run": base_runs[0]["uid"], "label": base_runs[0].get("label")},
               new={"release": cs["version"], "run": new_runs[0]["uid"] if new_runs else None, "label": new_runs[0].get("label") if new_runs else None},
               at=time.time())
    self.store.candidate_update(row["candidate_id"], recommendation=rec)
    if rec["decision"] in ("adopt", "hold"):   # D-463 덧 31 Q-1 (가): a hold (small gain) goes to the person as an adopt does
        self.store.wheel_update(wid, cell=7, cell_state="human")
        self.store.wheel_log(wid, 6, f"⑥ 판정 권고 {rec['decision']} (자동 · {'기준 표' if is_eff(w) else '채점 에이전트'}) — " + " · ".join(rec["reasons"]))
        self.store.wheel_log(wid, 7, HUMAN_7.format(name=new.get("name", "새 버전"), rec=REC_WORD.get(rec["decision"], rec["decision"])))
        if rec.get("confirm"):   # the person sees the test result here, after the choice was made without it
            self.store.wheel_log(wid, 7, "⑦ " + rec["confirm"]["text"])
        auto_apply(self, w, row, rec, cs["version"], new.get("name", "새 버전"))   # D-565 (dev-2): a check-order version asks for its deploy itself
        return
    self.store.candidate_update(row["candidate_id"], outcome="rec_reject")
    order_not_applied(self, w, row, rec.get("failed"), new.get("name", "새 버전"))   # D-565: the alert for a check-order row
    self.store.wheel_log(wid, 6, f"⑥ 판정 권고 reject (자동) — {', '.join(rec['failed'])} · {new.get('name', '새 버전')}은 평가 중 · 권고 reject로 남김 · 표의 다음 줄을 올림(D-122)")
    mine = set(cs.get("hyp_ids") or [])
    nxt = sorted([x for x in self.store.candidates(wid) if x.get("rank") and x["outcome"] not in NOT_NEXT and x["iteration"] == w["iteration"]
                  and (x["hypothesis_ids"] or [None])[0] in mine], key=lambda x: x["rank"])
    cs.update(version=None, written=[], hidden_runs=[], hidden_spent=[], version_runs=[])
    if nxt:
        cs.update(phase="confirm", winner=nxt[0]["candidate_id"])
        self.store.wheel_log(wid, 5, f"표의 다음 줄 {nxt[0]['candidate_id']} — 같은 작업 suite로 2번 더(D-97)")
    else:
        cs.update(phase="rows")
        self.store.wheel_log(wid, 5, "이 묶음에 다음 줄 없음 — 다음 묶음으로")
    state["cand"] = cs
    self.store.set_wheel_state(wid, state)
    self.store.wheel_update(wid, cell=5, cell_state="running")


def source_words(hs, src):
    """D-597: who made an iteration's hypotheses — the source's word alone, or each with its count when more than one made them."""
    n = Counter(h.get("source") or "agent" for h in hs)
    if len(n) < 2:
        k = next(iter(n), "rule")
        return src.get(k, k)
    return " · ".join(f"{src.get(k, k)} {c}" for k, c in n.most_common())


def with_order_steps(hyps, nodes, fix, tried):
    """D-565 #7 (D-560: 헛걸음 진단 = 노드 · 점검 순서): the check-order steps of every node declaring check_first_* first in the
    iteration's hypotheses, whatever node ④ chose (the 헛걸음's own fix is tried before the other options) — one per option and value
    (two judge nodes share one value a graph), none twice."""
    key = lambda h: (h.get("knob"), json.dumps(h.get("value")))   # noqa: E731
    order, seen = [], set()
    for name in nodes:
        for h in autohyp.HYP_KINDS["order"]["make"](nodes[name], fix, tried, None):
            if key(h) not in seen:
                seen.add(key(h))
                order.append(h)
    return order + [h for h in hyps or [] if key(h) not in seen]


def _row_kinds(self, w, row):
    hs = {h["hypothesis_id"]: h for h in self.store.hypotheses(w["wheel_id"])}
    return {autohyp.hyp_kind(hs[i]) for i in row.get("hypothesis_ids") or [] if i in hs}


def auto_apply(self, w, row, rec, rid, name):
    """D-565 · D-560 덧 9 (dev-2): at ⑦ (every criterion passed — no keep case broken) a version whose row is the kind '점검 순서' only
    asks for its deploy itself through the path a person's '배포 승인 ▶' takes (the adopt verdict · decision(deploy_approved) · the
    request file); the deploy side's dev-2 watch approves it. Off (LLMOPS_AUTO_APPLY) or any other kind → False, ⑦ waits for a person."""
    if not getattr(self.settings, "auto_apply", False) or rec.get("decision") not in ("adopt", "hold") or _row_kinds(self, w, row) != {"order"}:
        return False
    view = getattr(self.ops, "version_view", None)
    dep = (((view(w["project_id"], rid) if view else None) or {}).get("await") or {}).get("deployment_id")   # the window's own default target
    try:
        self.ops.auto_apply(w["project_id"], rid, {"decided_by": AUTO_BY, "reason": "자동 반영 — 점검 순서 · 지킬 망가짐 0(D-565 · dev-2)",
                                                    **({"deployment_id": dep} if dep else {})})
    except Exception as e:   # noqa: BLE001 - a refused request (409 · 422) is the person's to see, never a crash of the wheel
        d = getattr(e, "detail", None)
        why = " · ".join(d.get("reasons") or []) if isinstance(d, dict) else str(d) if d else type(e).__name__
        self.store.wheel_log(w["wheel_id"], 7, AUTO_REFUSED.format(name=name, why=why))
        return False
    self.store.wheel_log(w["wheel_id"], 7, AUTO_APPLIED.format(name=name))
    return True


def order_not_applied(self, w, row, failed, name, cell=6):
    """D-565: a check-order row the ⑥ criteria reject (a keep case broken) is not applied — one alert line for the LLMOps owner; eval T9:
    a row the ⑤ table drops (cell 5) never reaches ⑥, so the line is left there."""
    if _row_kinds(self, w, row) == {"order"}:
        self.store.wheel_log(w["wheel_id"], cell, (ORDER_OUT_5 if cell == 5 else ORDER_NOT_APPLIED).format(
            name=name, why=" · ".join(failed or []) or "판정 기준 탈락"))


Main.compare = _compare_step

END_DEPLOYED = autocycle.END_DEPLOYED
AUTO_BY = "auto-apply"   # D-565 (dev-2): the decided_by of a deploy the wheel asked for itself (an alias id, deploying.alias_error)
AUTO_APPLIED = "⑦ 자동 반영 — {name} 점검 순서 · 지킬 망가짐 0 · 배포 승인 요청(D-565 · 사람 0)"
AUTO_REFUSED = "⑦ 자동 반영 못 함 — {name} · {why} → LLMOps 알림 · 사람 차례"
ORDER_NOT_APPLIED = "⑥ 점검 순서 반영 안 됨 — {name} · {why} → LLMOps 알림"
ORDER_OUT_5 = "⑤ 점검 순서 반영 안 됨 — {name} · {why} → LLMOps 알림"
END_REJECTED = "끝 · ⑦ 사람 거절 — {name} reject"   # EV-074 (D-449 · CHG-186 덧): the person's reject at ⑦ ends the wheel
STOP_DEPLOY = "실패({why})"   # shown after the cell name: '⑧ 배포 실패(<이유>)'


def _deploy_step(self, w):
    """⑦ → ⑧ (CHG-186, A11): the person's one deploy approval moves the wheel to ⑧; a failed rollout is ⑧ red; the person's reject of
    the version at ⑦ ends the wheel (EV-074, D-449 — a hold keeps it waiting). The green end when
    the new version becomes 운영 is check_base's (Wheels) — it also opens the next wheel at ① on that version (D-96). The version's state
    comes from the same view the window reads (reading `tagged` there may add LLMOps' deployment row, S-20 — as on the screen)."""
    wid = w["wheel_id"]
    cs = (self.store.wheel_state(wid) or {}).get("cand") or {}
    vid = cs.get("version")
    read = getattr(self.ops, "version_view", None)   # the app's path (a test's stand-in paths may not give it)
    view = read(w["project_id"], vid) if vid and read else None
    if not view:
        return
    name = (self.store.release(vid) or {}).get("name", "새 버전")
    ro = view.get("rollout") or {}
    if w["cell"] == 7 and view.get("state") == "거절" and not view.get("rollout"):   # EV-074: a person's reject ends it (a hold keeps it)
        why = END_REJECTED.format(name=name)
        self.store.wheel_log(wid, 7, why)
        self.store.wheel_update(wid, outcome=why, cell_state="done", ended_at=time.time())
        return
    if w["cell"] == 7 and (view.get("rollout") or view.get("state") == "운영"):
        self.store.wheel_update(wid, cell=8, cell_state="running")
        self.store.wheel_log(wid, 8, f"⑧ 배포 — {name} 배포 승인됨({ro.get('decided_by') or '사람'}) · 4단계 진행")
        w = self.store.wheel(wid)
    if w["cell"] != 8:
        return
    if ro.get("failed") and view.get("state") != "운영":   # the green end belongs to check_base alone (it also opens the next wheel at ①, D-96)
        self._hold(w, self.store.wheel_state(wid), "stopped", STOP_DEPLOY.format(why=ro.get("fail_reason") or ro.get("reason") or "이유 없음"))


Main.deploy = _deploy_step


def _follow_human(self, pid):
    """D-444 #10 (E3): a record a person tied a version to follows the newest such version with lines only — ⑦ when its deploy is
    approved, ⑧ when it becomes 운영 — each once; the record's own cell and end stay as they were."""
    read = getattr(self.ops, "version_view", None)
    if not read:
        return
    for w in self.store.wheels(pid):   # no release list read on a pass (UF-AC1-4): the record keeps its tied versions
        st = self.store.wheel_state(w["wheel_id"]) or {}
        r = self.store.release(st["tied"][-1]) if st.get("tied") else None   # several tied versions: the newest
        if not r:
            continue
        view = read(pid, r["id"]) or {}
        seen = st.setdefault("human", {}).setdefault(r["id"], [])
        ro = view.get("rollout") or {}
        if "7" not in seen and (ro or view.get("state") == "운영"):
            self.store.wheel_log(w["wheel_id"], 7, f"사람 편집 {r['name']} 배포 승인됨({ro.get('decided_by') or '사람'})")
            seen.append("7")
        if "8" not in seen and view.get("state") == "운영":
            self.store.wheel_log(w["wheel_id"], 8, f"사람 편집 {r['name']} 운영")
            seen.append("8")
        self.store.set_wheel_state(w["wheel_id"], st)


Main.follow_human = _follow_human
