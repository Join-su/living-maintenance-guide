"""EV-030 CHG-151 (supplement A1 · 04/26, A2 · 05/26; D-89 · D-90 · D-91): the automatic diagnosis of operational Runs.

While the server runs, the operational Runs of each Project's 운영 (deployed) Agent Pack version that have no diagnosis yet are
found at a fixed interval and diagnosed one by one — nobody presses anything. The Runs are read the way the 운영 Run screen reads them
(opsruns: logDB `trace` SELECT as llmops_app, payload files read only); nothing is written to the trace, the payloads, or the
evaluation Runs. Three signals:
  · one-case rules — the Run ended succeeded, no node error, no node left running, the Pack's schema checks passed (a validate
    node failing its own domain rule is that node doing its job, not an anomaly — A2 '진단 규칙대로 실패 ✓');
  · ratio — the batch's route shares and empty retrievals against the release's last evaluation-suite Run;
  · LLM judge — only nodes whose meaning has to be read (retrieve · validate · llm), against the node's expect.text and a rubric.
    Fake mode: the fake provider's fixed score is shown as 'judge(가짜)' and never used as a signal (Q-9). Real mode: skipped until
    the user approves real judge calls with Gate B. A test harness file (LLMOPS_FAKE_JUDGE, isolated-env only) may stand in for a judge.
Stored is the result only (Q-11): check names · ✓/✗ · ratio numbers · node names · rubric item names · short reasons with any piece of
the Run's raw values masked — never the raw values, node inputs or outputs.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
from pathlib import Path

from . import gateway, opsruns, packs
from .keys import project_llm

RUBRIC_DIR = Path(__file__).resolve().parent / "rubrics"
RUBRIC_VERSION = "auto-v1"
JUDGED_KINDS = ("retrieve", "validate", "llm")   # D-90: never router · custom_code · render (rules decide those)
GATE_B_OFF = "실제 judge는 Gate B 승인 뒤"   # EV-062: still the reason when the server is not set up for it (unlisted Project, no cap)
JUDGE_CAP = "비용 상한(서버 합계)"
JUDGE_CHECK_ONLY = "판정 모델은 사람 체크된 Run만"   # EV-065 CHG-264 (P-2): the reason on a general row of a real-mode server
DEFAULT_RUBRIC = {"retrieve": "입력의 증상 · 조건과 맞는 결과가 위에 있나"}   # EV-065 CHG-264: LLMOps' own rubric when the Pack gives none
JUDGE_UNPARSED = "judge 응답을 해석하지 못함"   # gateway.judge_node's answer when the model's reply could not be read (score 0, not a judgement)
CHECK_KINDS = {"S1": "헛걸음", "S2": "조치 미해결", "S3": "원인 다름", "S4": "파트장 수정", "S5": "목록에 없음"}   # EV-065 CHG-262: the app's signals
READ_LINE = 5   # D-597: how many versions back a pass reads Runs and checks (a check comes within a report or two of its Run)
CHECK_RUBRIC = "check-1:"   # EV-065 CHG-263: a person-check diagnosis row is 'check-1:<key>' — one per check (the store's (Project, Run, rubric) rule)
SOLVED_KEY = "/solved"      # EV-065 CHG-268: the key end of a record that names no signal (the problem was solved) — recurrence only
LABEL_KIND = "사람 ✗"       # EV-065 CHG-262: a person's ✗ label in LLMOps (EV-064) is a check of this kind
FROM_OTHER = "{name}에서 진단(같은 revision)"   # EV-088: a list cell's row made under the version operational then
LABEL_SETTLE_S = 30         # D-478 덧 28: a person's ✗ labels are counted once none came for this long (one wheel for the whole batch)
INTERVAL_S = 30          # how often new operational Runs are looked for (a code constant, not a product setting)
LINK_S = 2               # D-617: how often applied graphs are looked for, apart from the pass (a Run is refused until its ledger row is there)
FIRST_DELAY_S = 2        # the first look after the server starts
RATIO_GAP = 0.25         # a share differing this much from the evaluation Run's share is '다름'
RATIO_MIN_RUNS = 3       # fewer operational Runs in a batch are not compared ('표본 적음')
LIST_CAP = 200           # operational Runs looked at per release per pass (newest first)
MASK_SPAN = 20           # a reason piece this long that also appears in the Run's raw values is masked (V-DIAG-NOCOPY)
REASON_MAX = 200
UNREADABLE_TEXT = {"no_dsn": "연결 설정 없음", "unreachable": "logDB에 연결할 수 없음", "denied": "권한 없음", "broken": "기록을 읽다 멈춤"}


def kind_name(kind):
    return (kind or "").split("@")[0]


def rubric(version=RUBRIC_VERSION):
    return json.loads((RUBRIC_DIR / f"{version}.json").read_text(encoding="utf-8"))


def pack_rubric(node):
    """EV-065 CHG-264: the rubric text the Pack gives a node ('rubric': text or {"text"}), else None."""
    r = (node or {}).get("rubric")
    r = r.get("text") if isinstance(r, dict) else r
    return r.strip() if isinstance(r, str) and r.strip() else None


def node_rubric(node):
    """EV-065 CHG-264: (where from, text) of the rubric a node is judged by — the Pack's, else LLMOps' default (retrieve), else the
    node's expect (validate · llm); (None, None) when there is none."""
    if pack_rubric(node):
        return "Pack", pack_rubric(node)
    kind = kind_name((node or {}).get("kind"))
    if kind in DEFAULT_RUBRIC:
        return "LLMOps 기본", DEFAULT_RUBRIC[kind]
    text = ((node or {}).get("expect") or {}).get("text")
    return ("expect", text) if text else (None, None)


def judged(node):
    """D-90 · EV-065 P-5: retrieve · validate · llm nodes, and a custom_code node only with a Pack rubric (router · render never)."""
    kind = kind_name((node or {}).get("kind"))
    return kind in JUDGED_KINDS or (kind == "custom_code" and pack_rubric(node) is not None)


def _strings(value, out):
    if isinstance(value, str):
        if len(value) >= MASK_SPAN:
            out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _strings(v, out)
    return out


def mask_raw(text, raw_values):
    """The reason text with every MASK_SPAN-long piece that also appears in the Run's raw values replaced by '…(원문)'
    (Q-11: no raw text in the stored diagnosis). Also cut to REASON_MAX characters."""
    text = str(text or "")[:REASON_MAX]
    hay = _strings(raw_values, []) + [json.dumps(raw_values, ensure_ascii=False, default=str)]
    if not hay or len(text) < MASK_SPAN:
        return text
    hit = [False] * len(text)
    for i in range(len(text) - MASK_SPAN + 1):
        piece = text[i:i + MASK_SPAN]
        if any(piece in h for h in hay):
            for j in range(i, i + MASK_SPAN):
                hit[j] = True
    out, i = [], 0
    while i < len(text):
        if hit[i]:
            while i < len(text) and hit[i]:
                i += 1
            out.append("…(원문)")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ------------------------------------------------------------------ the three signals of one Run
def rule_check(detail):
    """One-case rules: [{'check', 'node', 'ok', 'why'}] and the first failing node (or None)."""
    items, first = [], None
    ended = detail.get("status")
    items.append({"check": "run_ended", "node": None, "ok": ended == "succeeded", "why": f"끝 상태 {ended or '없음'}"})
    if ended != "succeeded":
        first = first or "run"
    for v in detail.get("visits") or []:
        if v.get("status") == "fail" or v.get("error"):
            items.append({"check": "node_error", "node": v["node"], "ok": False, "why": "node 오류"})
            first = first or v["node"]
        elif v.get("status") == "live":
            items.append({"check": "node_unfinished", "node": v["node"], "ok": False, "why": "끝나지 않은 node"})
            first = first or v["node"]
        for c in v.get("checks") or []:
            if c.get("rule") == "schema" and c.get("passed") is False:
                items.append({"check": "schema", "node": v["node"], "ok": False, "why": "형식 검사 실패"})
                first = first or v["node"]
    if not detail.get("output_ref") and ended == "succeeded":
        items.append({"check": "output", "node": None, "ok": False, "why": "결과 기록 없음"})
        first = first or "run"
    return items, first


def _count_items(output, writes):
    """How many items a retrieve node returned: the length of the first list among the state keys it writes."""
    if not isinstance(output, dict):
        return None
    for k in writes or output.keys():
        v = output.get(k)
        if isinstance(v, list):
            return len(v)
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list):
                    return len(vv)
    return None


def route_shares(route_lists):
    """{'<router> → <target>': count} over runs, and the number of runs that visited each router."""
    counts, visited = {}, {}
    for routes in route_lists:
        seen = set()
        for node, target in routes:
            counts[(node, target)] = counts.get((node, target), 0) + 1
            seen.add(node)
        for n in seen:
            visited[n] = visited.get(n, 0) + 1
    return counts, visited


def ratio_check(ops_routes, ops_empty, eval_run):
    """Ratio signal of a batch (D-90): route shares per router and empty retrievals, operational vs the evaluation Run."""
    n_ops = len(ops_routes)
    if not eval_run:
        return {"compared": False, "differ": False, "n_ops": n_ops, "why": "비교할 평가 Run 없음", "items": []}
    eval_routes, eval_empty = [], 0
    for c in eval_run.get("cases") or []:
        rs = []
        for v in c.get("visits") or []:
            r = v.get("route")
            if isinstance(r, dict) and r.get("target"):
                rs.append((v["node"], r["target"]))
        eval_routes.append(rs)
        eval_empty += sum(1 for v in c.get("visits") or [] if v.get("_empty"))
    n_eval = len(eval_routes)
    base = {"eval_run": eval_run.get("label"), "n_ops": n_ops, "n_eval": n_eval}
    if n_ops < RATIO_MIN_RUNS or not n_eval:
        return {**base, "compared": False, "differ": False, "why": f"표본 적음(운영 {n_ops}건)" if n_ops < RATIO_MIN_RUNS else "평가 Run에 case 없음", "items": []}
    oc, ov = route_shares(ops_routes)
    ec, ev = route_shares(eval_routes)
    items, differ = [], False
    for key in sorted(set(oc) | set(ec)):
        node, target = key
        a, b = oc.get(key, 0), ec.get(key, 0)
        pa, pb = a / n_ops, b / n_eval
        d = abs(pa - pb) >= RATIO_GAP
        differ = differ or d
        items.append({"what": f"{node} → {target}", "ops": f"{a}/{n_ops}", "eval": f"{b}/{n_eval}", "differ": d})
    items.append({"what": "빈 검색", "ops": f"{ops_empty}/{n_ops}", "eval": None, "differ": False, "note": "평가 Run 쪽은 node 출력이 없으면 세지 않음"})
    return {**base, "compared": True, "differ": differ, "items": items,
            "why": "fallback은 평가 기록에 없어 비교하지 않음"}


def ratio_by_kind(groups, eval_runs, spec):
    """EV-077 (D-454 덧): the ratio per kind — {kind: (routes of that kind's target Runs, empty count)} each against the cases of that
    kind in the newest suite Run that has any (eval_runs newest first); '기준 없음(<kind>)' when none has, never a difference."""
    out = {}
    for kind, (routes, empty) in groups.items():
        base = None
        if len(routes) >= RATIO_MIN_RUNS:
            for ev in eval_runs or []:
                cs = [c for c in ev.get("cases") or [] if opsruns.kind_of(c.get("input"), spec) == kind]
                if cs:
                    base = {**ev, "cases": cs}
                    break
        if len(routes) < RATIO_MIN_RUNS:
            out[kind] = {"compared": False, "differ": False, "n_ops": len(routes), "why": f"표본 적음(운영 {len(routes)}건)", "items": [], "kind": kind}
        elif base is None:
            out[kind] = {"compared": False, "differ": False, "n_ops": len(routes), "why": f"기준 없음({kind})", "items": [], "kind": kind}
        else:
            out[kind] = {**ratio_check(routes, empty, base), "kind": kind}
    return out


def before_targets(d):
    """packrt 138 ① (가): a finished general row diagnosed before EV-077 — its ratio was the batch's over every Run."""
    ratio = d.get("ratio_result") or {}
    return d.get("status") == "done" and not is_check(d) and "kind" not in ratio and not ratio.get("targets") \
        and (d.get("rule_result") or {}).get("scope") != "procedure"


def resignal(d, ratio):
    """The row with its ratio replaced and its signal decided again by _diagnose's rule (rules · judge as stored)."""
    judge, kind = d.get("judge_result") or {}, d.get("judge_kind")
    trusted = kind in ("harness", "real")
    failed = [n for n in judge.get("nodes") or [] if n.get("ok") is False] if trusted else []
    if (d.get("rule_result") or {}).get("first") or ratio.get("differ") or failed:
        signal = "fail"
    elif trusted and judge.get("normal") and any(n.get("ok") is not None for n in judge.get("nodes") or []):
        signal = "normal"
    elif d.get("signal") == "none":
        signal = "none"
    else:
        signal = "pass"
    return {**d, "ratio_result": ratio, "signal": signal}


def recount(mates, ops, graph, review, kspec, suite_runs):
    """packrt 138 ① (가): the rows of one batch diagnosed before EV-077, as the new rule shows them — {run_id: None for a procedure Run,
    else the row with the ratio of its batch's targets (by run_kind when there is one) and the signal decided again}. Read only."""
    nodes = {n["id"]: n for n in graph.get("nodes") or [] if "id" in n}
    groups, kinds, out = {}, {}, {}
    for d in mates:
        try:   # packrt 139 ①: a Run that cannot be read now keeps its stored row (the screen still answers)
            det = ops.detail(d["run_id"], graph)
            if det is None:
                continue
            value = ops.run_input(d["run_id"])
            target = ops.target(d["run_id"], value, review) if det.get("status") == "succeeded" else True
        except Exception:
            continue
        if not target:
            out[d["run_id"]] = None
            continue
        k = opsruns.kind_of(value, kspec) if kspec else None
        kinds[d["run_id"]] = k
        groups.setdefault(k, []).append([(v["node"], v["route"]["target"]) for v in det["visits"] if isinstance(v.get("route"), dict) and v["route"].get("target")])
    evs = [_with_empty(x, nodes) for x in suite_runs or []]
    ratios = ratio_by_kind({k: (g, 0) for k, g in groups.items() if k is not None}, evs, kspec) if kspec else {}
    if None in groups or not ratios:
        ratios[None] = ratio_check(groups.get(None, []), 0, evs[0] if evs else None)
    for d in mates:
        if d["run_id"] in kinds:
            out[d["run_id"]] = resignal(d, {**(ratios.get(kinds[d["run_id"]]) or ratios.get(None) or {}), "recounted": True, "targets": True})
    return out


class Harness:
    """LLMOPS_FAKE_JUDGE (isolated-env only): {"delay_ms": n, "runs": {run_id: {"normal": bool, "nodes": {node: {"ok": bool,
    "items": [{"name", "ok", "why"}]}}}}} — runs not listed pass every judged node."""
    def __init__(self, path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None

    def __bool__(self):
        return self.data is not None

    def run(self, run_id):
        return (self.data or {}).get("runs", {}).get(run_id, {})

    @property
    def delay(self):
        return (self.data or {}).get("delay_ms", 0) / 1000


def _josa(name):
    """이 / 가 after a name ending in a digit, a Latin letter or a Hangul syllable."""
    last = (name or "")[-1:]
    if "가" <= last <= "힣":
        return "이" if (ord(last) - 0xAC00) % 28 else "가"
    return "이" if last.lower() in "013678lmn" else "가"


def kept_text(left, used, failed=()):
    """'남김 m개(V1이 씀 · 실제 임베딩 k)' — who uses what stays, and how many stay for being real or unknown or not removed
    (failed: the ids the removal kept, EV-051 CHG-W2-27)."""
    names = sorted({who for v in left for who in used.get(v["id"], []) if not who.startswith(("바퀴 ", "편집"))})
    parts = [f"{' · '.join(names)}{_josa(names[-1])} 씀"] if names else []
    free = [v for v in left if v["id"] not in used and v["id"] not in failed]
    rows = sum(1 for v in left if v["id"] in used and all(who.startswith(("바퀴 ", "편집")) for who in used[v["id"]]))
    for label, n in (("바퀴 줄 · 편집이 씀", rows), ("실제 임베딩", sum(1 for v in free if v["mode"] == "real")), ("mode 모름", sum(1 for v in free if v["mode"] is None and not v["skip"])),
                     ("안 지우는 꼴", sum(1 for v in free if v["skip"])), ("지우지 못함", sum(1 for v in left if v["id"] in failed))):
        if n:
            parts.append(f"{label} {n}")
    return f"남김 {len(left)}개" + (f"({' · '.join(parts)})" if parts else "")


class Diagnoser:
    """targets() → [{"project_id", "release": rel, "deployment_ids": [...], "spec": result-cell spec, "eval_run": run view, None, or a
    function giving it (called only when a batch has Runs to diagnose — the read of every Run is expensive)}]
    for every Project's 운영 Agent Pack version (the 운영 Run list's own condition, KD-3)."""
    def __init__(self, store, settings, gw, targets, reader=opsruns.reader, wheels=None, kg_link=None):
        self.store, self.settings, self.gw, self.targets, self.reader = store, settings, gw, targets, reader
        self.kg_link = kg_link     # D-588: applied graphs become versions before the pass reads its targets
        self.wheels = wheels       # AC-4: autocycle.Wheels — a finished batch starts or joins the improvement wheel
        self.harness = Harness(getattr(settings, "fake_judge", "") or "")
        self.stopped = {}          # release id → reason kind while the logDB cannot be read at all (kept in memory only, B1 ①)
        self.running = {}          # release id → {"batch", "k", "n"} while a batch is being diagnosed
        self.listed = {}           # D-470 ⓓ: release id → this pass's 운영 Run list (newest first) for the efficiency start
        self._stop = threading.Event()
        self._kick = threading.Event()   # EV-064 CHG-CF-5: a person's label wakes the next pass now
        self._thread = None
        self._pass = threading.Lock()
        self._linking = threading.Lock()
        self._swept_since = None   # EV-051 CHG-W2-27: ended wheels are looked for from here (None = all, the first pass after a start)
        self._unswept = {}         # wheel id → versions removed for it so far, while its versions are not swept yet
        self._blocked = {}         # wheel id → the last reason logged for it (once each)
        self._rows_cleared = False  # EV-051 CHG-W2-32: old row copies looked at once after a start
        self._lock_note = None      # EV-051 CHG-W2-30: the last lock line written to stderr (once while it lasts)
        self._judge_stopped = None  # EV-062: why real judging stopped on this server (a key or call failure), else None

    # -------------------------------------------------------------- the loop
    def start(self):
        if self._thread or not self.store.auto_ready:
            return
        self._thread = threading.Thread(target=self._loop, name="auto-diagnosis", daemon=True)
        self._thread.start()
        if self.kg_link:
            threading.Thread(target=self._link_loop, name="kg-link", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._kick.set()

    def kick(self):
        """EV-064 CHG-CF-5: run the next pass now (a person left a label) — nothing when the automatic diagnosis is not running."""
        if self._thread:
            self._kick.set()

    def link_once(self):
        """D-617: the applied graphs become versions now, apart from the pass — one at a time. True when it ran."""
        if not self.kg_link or not self._linking.acquire(blocking=False):
            return False
        try:
            self.kg_link()
            return True
        finally:
            self._linking.release()

    def _link_loop(self):
        while not self._stop.wait(LINK_S):
            try:
                self.link_once()
            except Exception:   # the next look tries again
                pass

    def _loop(self):
        if self._stop.wait(FIRST_DELAY_S):
            return
        while True:
            try:
                self.run_once()
            except Exception:   # one bad pass never stops the loop; the next pass tries again
                pass
            self._kick.wait(INTERVAL_S)
            self._kick.clear()
            if self._stop.is_set():
                return

    def run_once(self):
        """One pass over every target. Returns the number of Runs diagnosed."""
        if not self.store.auto_ready:
            return 0
        with self._pass:
            lock = self._take_lock()
            if lock is False:   # the admin command holds it: this whole pass waits for the next one (its Runs stay undiagnosed)
                return 0
            try:
                n = 0
                self.link_once()
                for t in self.targets():
                    line = t.get("line") or []     # D-597: the versions just before that differ only in the graph
                    near = [{**t, "release": r, "since": None} for r in near_line(line, t.get("prev_op"))]
                    if self.wheels:                # the 운영 version moved: the open wheel ends and a new one waits on the new base (D-97)
                        self.wheels.check_base(t["project_id"], t["release"], line)
                    for x in (t, *near):
                        n += self._release(x)
                    if self.wheels:                # EV-065 CHG-262 · 263 (D-412 decision 1): only a person's check starts the wheel — an
                        for x in (t, *near):       # app person-check record or a ✗ label in LLMOps (EV-064 CHG-CF-5, D-182 Q-L3)
                            try:
                                self._checks(x)
                            except Exception:   # never the pass's result; the next pass looks again
                                pass
                    if self.wheels:                # EV-069 CHG-EF-2: the efficiency row's set time (one try per slot)
                        try:
                            self.wheels.scheduled_efficiency(t["project_id"], t["release"])
                        except Exception:   # never the pass's result; the next pass looks again
                            pass
                    after = getattr(self.settings, "auto_eff_after_runs", None)
                    if self.wheels and after:      # D-470 ⓓ: N finished 운영 Runs open the efficiency wheel (off by default)
                        try:
                            self.wheels.trace_efficiency(t["project_id"], t["release"], self.listed.pop(t["release"]["id"], []), after)
                        except Exception:   # never the pass's result; the next pass looks again
                            pass
                    if self.wheels:                # EV-035: the open wheel's main line takes one step on every pass
                        try:
                            self.wheels.advance(t["project_id"], t["release"], line)
                        except Exception:   # one bad step never stops the pass; the wheel tries again next time
                            pass
                return n
            finally:
                if self.wheels:   # EV-051 CHG-W2-27: after every pass, also one that stopped or had no target
                    try:
                        self.sweep_index_builds()
                    except Exception:   # never the pass's result; the next pass sweeps again
                        pass
                if lock is not None:
                    packs.release_index_lock(lock)

    def _note(self, text):
        if text != self._lock_note:
            self._lock_note = text
            if text:
                print(text, file=sys.stderr)

    def _take_lock(self):
        """EV-051 CHG-W2-30: the descriptor holding the index lock for this pass; False when the admin command holds it past the wait
        or it fails otherwise (the pass is skipped); None when the lock file cannot serve (the pass goes on without it)."""
        try:
            fd = packs.take_index_lock(self.settings.data_dir, packs.LOCK_WAIT_SERVER)
        except packs.LockBusy:
            self._note("자동 진단: 정리 잠금을 기다리다 이번 진단을 건너뜀(관리 명령이 잠금을 잡음)")
            return False
        except packs.LockUnusable as e:
            self._note(f"자동 진단: 정리 잠금 파일을 쓸 수 없음({e}) — 잠금 없이 진행")
            return None
        except OSError as e:
            self._note(f"자동 진단: 정리 잠금을 잡지 못해 이번 진단을 건너뜀({type(e).__name__})")
            return False
        self._note(None)
        return fd

    # -------------------------------------------------------------- EV-051 CHG-W2-27 (D-357): the versions of an ended wheel
    def sweep_index_builds(self):
        """Once a wheel ended: remove every unused fake-embedding version under <data dir>/index-builds (that wheel's, rows dropped
        before, releases deleted since). The wheel is marked, with one log line, when none of its row Runs is still going and nothing
        blocked the sweep; until then the next pass sweeps again. Real-embedding and unknown versions stay (admin --include-real)."""
        now = time.time() - 1
        for w in self.store.ended_wheels(self._swept_since):
            if w["wheel_id"] not in self._unswept and not self.store.wheel_state(w["wheel_id"]).get(packs.SWEPT):
                self._unswept[w["wheel_id"]] = 0
        self._swept_since = now   # only once read: a failed read looks again from the same time (slice critic N-W25-1)
        if not self._rows_cleared:   # CHG-W2-32: row copies of wheels marked before (or gone from the store), once after a start
            self._rows_cleared = True
            try:   # an odd auto folder never stops the version sweep below (slice review N-W25B-1)
                old = [wid for wid in packs.row_copies(self.settings.data_dir)
                       if wid not in self._unswept and (self.store.wheel(wid) is None or self.store.wheel_state(wid).get(packs.SWEPT))]
                packs.drop_row_copies(self.settings.data_dir, old)
            except (ValueError, OSError):
                pass
        if not self._unswept:
            return
        ops = getattr(getattr(self.wheels, "main", None), "ops", None)

        def alive(uid):
            x = getattr(ops, "shadow_run", lambda u: None)(uid)
            return bool(x) and x["status"] in ("대기", "실행 중") and getattr(ops, "shadow_alive", lambda u: True)(uid)

        data_dir = self.settings.data_dir
        try:
            versions, why = packs.index_build_versions(data_dir), None
        except (ValueError, OSError) as e:
            versions, why = [], str(e) if isinstance(e, ValueError) else f"index-builds를 읽을 수 없음({type(e).__name__})"
        if not versions and not why:   # nothing under index-builds (a wheel without vector DB versions): marked without a line
            for wid in list(self._unswept):   # W2-5b CHG-W2-32: once its row Runs ended, as its row copies go too
                if any(alive(u) for r in self.store.candidates(wid) for u in packs._row_uids(r)):
                    continue
                self.store.set_wheel_state(wid, {**self.store.wheel_state(wid), packs.SWEPT: time.time()})
                packs.drop_row_copies(data_dir, [wid])
                del self._unswept[wid]
            return
        used = {}
        if not why:
            used, why = packs.index_builds_in_use(data_dir, self.store, list(self._unswept), alive)
        if why:
            for wid in self._unswept:
                if self._blocked.get(wid) != why:
                    self._blocked[wid] = why
                    self.store.wheel_log(wid, self._cell(wid), f"벡터 DB version 정리 안 함 — {why}")
            return
        gone = packs.drop_index_builds(data_dir, [v["id"] for v in versions if v["id"] not in used and v["mode"] == "fake" and not v["skip"]])
        for wid in self._unswept:
            self._unswept[wid] += len(gone["removed"])
        left = [v for v in versions if v["id"] not in gone["removed"]]
        for wid in list(self._unswept):
            w = self.store.wheel(wid)
            if w and any(alive(u) for r in self.store.candidates(wid) for u in packs._row_uids(r)):
                continue
            if w:
                self.store.set_wheel_state(wid, {**self.store.wheel_state(wid), packs.SWEPT: time.time()})
                self.store.wheel_log(wid, w["cell"], f"벡터 DB version 정리 {self._unswept[wid]}개(가짜, 쓰지 않음) · " + kept_text(left, used, gone["kept"]))
            packs.drop_row_copies(data_dir, [wid])   # CHG-W2-32: no row Run reads them any more
            del self._unswept[wid]
            self._blocked.pop(wid, None)

    def _cell(self, wid):
        return (self.store.wheel(wid) or {}).get("cell") or 1

    # -------------------------------------------------------------- one release
    def _release(self, t):
        pid, rel = t["project_id"], t["release"]
        rid, revision = rel["id"], rel["pack"]["revision"]
        try:
            ops = self.reader(self.settings)
            runs, cur = [], None
            while len(runs) < LIST_CAP:
                page, nxt = ops.list(t["deployment_ids"], revision, cur, spec=t.get("spec"))
                runs += page
                if not nxt:
                    break
                cur = tuple(nxt.split("|", 1))
        except opsruns.OpsUnavailable as e:   # the whole logDB unreadable: no rows, the reason kind in memory only
            self.stopped[rid] = e.kind
            return 0
        except Exception:
            self.stopped[rid] = "broken"
            return 0
        self.stopped.pop(rid, None)
        self.listed[rid] = runs
        if not runs:
            return 0
        batch = time.strftime("%Y%m%d-%H%M%S")
        for r in reversed(runs):              # oldest first, so a batch reads in time order
            self.store.diagnosis_waiting(pid, rid, r["run_id"], r["deployment_id"], revision, batch, RUBRIC_VERSION)
        pending = general(self.store.diagnoses(pid, rid, statuses=("waiting", "running", "error")))
        if not pending:
            return 0
        graph = rel.get("graph") or {}
        nodes = {n["id"]: n for n in graph.get("nodes") or []}
        self.running[rid] = {"batch": pending[-1]["batch"], "k": 0, "n": len(pending)}
        details, empties, targets, kinds = {}, {}, {}, {}
        for row in pending:                    # read every Run first: the ratio is a batch signal; a Run whose record cannot be
            try:                               # read (its detail or a node's trace) is that Run's error row only (B1 ②)
                d = ops.detail(row["run_id"], graph, t.get("spec"))
                if d is None:
                    raise ValueError("no run")
                empty = 0
                for v in d["visits"]:
                    if kind_name((nodes.get(v["node"]) or {}).get("kind")) == "retrieve" and v.get("status") == "done":
                        val = ops.node_values(row["run_id"], graph, v["node"], v.get("iter") or 1)
                        if _count_items((val or {}).get("output"), (nodes.get(v["node"]) or {}).get("writes")) == 0:
                            empty += 1
                details[row["diagnosis_id"]], empties[row["diagnosis_id"]] = d, empty
                if t.get("review") or t.get("run_kind"):   # EV-077: the target and the kind come from the Run input (and output)
                    value = ops.run_input(row["run_id"])
                    targets[row["diagnosis_id"]] = ops.target(row["run_id"], value, t.get("review")) if d.get("status") == "succeeded" else True
                    kinds[row["diagnosis_id"]] = opsruns.kind_of(value, t.get("run_kind"))
            except opsruns.OpsUnavailable as e:
                details[row["diagnosis_id"]] = e.kind
            except Exception:
                details[row["diagnosis_id"]] = "broken"
        groups = {}   # EV-077: the targets only, by kind when the Pack names one (one group otherwise)
        for row in pending:
            d = details[row["diagnosis_id"]]
            if isinstance(d, dict) and targets.get(row["diagnosis_id"], True):
                routes, empty = groups.setdefault(kinds.get(row["diagnosis_id"]) if t.get("run_kind") else None, ([], 0))
                routes.append([(v["node"], v["route"]["target"]) for v in d["visits"] if isinstance(v.get("route"), dict) and v["route"].get("target")])
                groups[kinds.get(row["diagnosis_id"]) if t.get("run_kind") else None] = (routes, empty + (1 if empties[row["diagnosis_id"]] else 0))
        ev = t.get("eval_run")
        ratios = {}
        if t.get("run_kind") and any(k is not None for k in groups):
            evs = t.get("eval_runs")
            evs = [_with_empty(x, nodes) for x in ((evs() if callable(evs) else evs) or [])]
            ratios = ratio_by_kind({k: g for k, g in groups.items() if k is not None}, evs, t["run_kind"])
        if None in groups or not ratios:   # no kind (or none readable): the batch's targets against the last suite Run, as before
            routes, empty = groups.get(None, ([], 0))
            ratios[None] = ratio_check(routes, empty, _with_empty(ev() if callable(ev) else ev, nodes))
        done = 0
        for k, row in enumerate(pending, start=1):
            self.running[rid] = {"batch": row["batch"], "k": k - 1, "n": len(pending)}
            self.store.diagnosis_update(row["diagnosis_id"], status="running")
            d = details[row["diagnosis_id"]]
            if not isinstance(d, dict):        # one Run unreadable (B1 ②): this row only, retried next pass on the same row
                self.store.diagnosis_update(row["diagnosis_id"], status="error", error_kind=d if d in UNREADABLE_TEXT else "broken",
                                            finished_at=time.time())
                continue
            if not targets.get(row["diagnosis_id"], True):   # EV-077: a procedure record — done, no signal, nothing judged (no DDL)
                self.store.diagnosis_update(row["diagnosis_id"], status="done", rule_result={"items": [], "first": None, "scope": "procedure"},
                                            ratio_result={"compared": False, "differ": False, "why": "절차 기록", "items": []},
                                            signal="none", suspect_node=None, error_kind=None, finished_at=time.time())
                done += 1
                continue
            ratio = ratios.get(kinds.get(row["diagnosis_id"]) if t.get("run_kind") else None) or ratios.get(None) or {}
            if t.get("review") or t.get("run_kind"):
                ratio = {**ratio, "targets": True}   # EV-077: this batch's ratio is over its targets (not a row from before)
            try:
                self._diagnose(row, d, ops, graph, nodes, ratio)
            except opsruns.OpsUnavailable as e:   # a node's trace read failed mid-way: this Run is unreadable, retried next pass
                self.store.diagnosis_update(row["diagnosis_id"], status="error", error_kind=e.kind, finished_at=time.time())
                continue
            except Exception:
                self.store.diagnosis_update(row["diagnosis_id"], status="error", error_kind="broken", finished_at=time.time())
                continue
            done += 1
            if self.harness and self.harness.delay:
                time.sleep(self.harness.delay)
        self.running.pop(rid, None)
        if self.wheels and done:               # AC-4 (CHG-157) · EV-065 P-1: the batch's finished Runs only add a line to an open wheel
            ids = {row["diagnosis_id"] for row in pending}
            self.wheels.after_batch(pid, rel, [x for x in self.store.diagnoses(pid, rid) if x["diagnosis_id"] in ids and x["status"] == "done"])
        return done

    def check_items(self, pid, rel, runs, labels=None):
        """EV-065 CHG-262 · 263b (D-412 decision 2): the person's checks about the given Runs (run_id → its general diagnosis row) as
        {key: {"key", "run_id", "kind", "node", "label"}}. From the app's records (c1a15.check-record.v1, one per session): each signal
        of a kind in CHECK_KINDS — key '<record>/<run>'; a record with no signal — key '<record>/solved', kind '해결됨', its first Run
        here. From LLMOps: a Run whose latest label is ✗ — key 'label-<id>', kind '사람 ✗' (a label EV-064 already counted, kept in a
        wheel's human_labels, is not counted again). Records that cannot be read count as none this time."""
        items = {}
        try:
            records = self.reader(self.settings).check_records() or []
        except Exception:   # noqa: BLE001 - the next pass reads again
            records = []
        for rec in sorted(records, key=lambda r: r.get("recorded_at") or ""):
            key = rec.get("record_key")
            if not key:
                continue
            sigs = rec.get("signals") or []
            truth = field_truth(rec)
            refs = field_refs(rec)   # EV-066 CHG-272: the confirmed section · cause refs (the start document's rule makes the value)
            # EV-066 CHG-271: the record's own signal kind codes and target Runs, kept on each of its items (per-signal recurrence)
            codes = sorted({s for s in rec.get("signal_ids") or [] if s in CHECK_KINDS} | {s.get("kind") for s in sigs if s.get("kind") in CHECK_KINDS})
            whole = {"record": key, "codes": codes, "sig_runs": sorted({s.get("run_id") for s in sigs if s.get("kind") in CHECK_KINDS and s.get("run_id")})}
            per_run = {}   # D-498 덧 8: every kind a record gives one Run (the item keeps the last as its kind)
            for s in sigs:
                if s.get("kind") in CHECK_KINDS:
                    per_run.setdefault(s.get("run_id"), []).append(CHECK_KINDS[s["kind"]])
            for s in sigs:
                if s.get("run_id") in runs and s.get("kind") in CHECK_KINDS:
                    items[f"{key}/{s['run_id']}"] = {"key": f"{key}/{s['run_id']}", "run_id": s["run_id"], "kind": CHECK_KINDS[s["kind"]],
                                                     "kinds": sorted(set(per_run[s["run_id"]])),
                                                     "node": s.get("node") or None, "label": None, **({"expected": truth} if truth else {}),
                                                     **({"field_ref": refs} if refs else {}), **({"start": True} if s["run_id"] == rec.get("start") else {}), **whole}
            here = [r for r in rec.get("runs") or [] if r in runs]
            if not sigs and not rec.get("signal_ids") and here:   # a solved session — not a line whose document could not be read
                items[key + SOLVED_KEY] = {"key": key + SOLVED_KEY, "run_id": here[0], "kind": "해결됨", "node": None, "label": None, **whole}
        counted = set()
        for w in self.store.wheels(pid):
            counted |= set(self.store.wheel_state(w["wheel_id"]).get("human_labels") or [])
        last = {}
        for lb in (labels if labels is not None else self.store.labels(pid, rel["id"])):   # label order: the latest per Run counts (EV-088: given)
            last[lb["run_id"]] = lb
        for r, lb in last.items():
            if r in runs and lb["label"] not in OK_LABELS and lb["label_id"] not in counted:
                k = f"label-{lb['label_id']}"
                items[k] = {"key": k, "run_id": r, "kind": LABEL_KIND, "node": lb.get("node") or None, "label": lb["label"]}
        return items

    def check_kinds(self, pid, rel):
        """EV-065 CHG-263a (E4-F1 B3-c): {run_id: kind} of the Runs that have an app check row on the version's current revision (the
        later row wins) — for the list cell '사람 체크 · <종류>'; read from the store, never the records; LLMOps labels keep their own
        cell (사람 ✗ … · 사람 ✓)."""
        out = {}
        for d in self.store.diagnoses(pid, rel["id"]):
            ck = (d.get("ratio_result") or {}).get("check") or {}
            if is_check(d) and d.get("pack_revision") == rel["pack"]["revision"] and ck.get("kind") and not ck.get("label") \
                    and not d["rubric_version"].endswith(SOLVED_KEY):
                out[d["run_id"]] = ck["kind"]
        return out

    def _checks(self, t):
        """EV-065 CHG-263 · 263a · 268 (D-412 decisions 1 · 8): one more diagnosis row per person's check ('check-1:<key>'), also for a
        Run diagnosed before — that row keeps the check counted once. The rows diagnosed now go to after_checks, which starts or joins
        the wheel whatever the diagnosis found; a row that could not be read stays an error row (not counted) and is retried. A
        '해결됨' record is recorded only (no diagnosis) and writes '재발 확인 ✓' on the wheel whose new version this is."""
        pid, rel = t["project_id"], t["release"]
        rid, revision = rel["id"], rel["pack"]["revision"]
        drs_of = {r["run_id"]: r.get("data_release_id") for r in self.listed.get(rid) or []}   # EV-088: this pass's trace rows
        runs, labels = version_rows(self.store, pid, rel, drs_of, t.get("since"), t.get("releases"))   # EV-088: Runs diagnosed before a same-revision switch too
        if not runs:
            return
        if labels_settling(self.store, pid, rid):   # D-478 덧 28: labels still coming in — the next pass counts them all together
            return
        items = self.check_items(pid, rel, runs, labels=labels)
        shown = self._signal_recurrence(pid, rel, runs, items)
        batch = time.strftime("%Y%m%d-%H%M%S")
        for it in items.values():
            if it.get("record") in shown:   # EV-066 CHG-271: shown per signal (or waiting for its Runs) — no check row
                continue
            did = self.store.diagnosis_waiting(pid, rid, it["run_id"], runs[it["run_id"]]["deployment_id"], revision, batch, CHECK_RUBRIC + it["key"])
            if did and it["key"].endswith(SOLVED_KEY):
                self.store.diagnosis_update(did, status="done", signal="none", finished_at=time.time())
                self._recurrence(pid, rel, it["key"][: -len(SOLVED_KEY)])
        pending = [d for d in self.store.diagnoses(pid, rid, statuses=("waiting", "running", "error")) if is_check(d)]
        if not pending:
            return
        ops = self.reader(self.settings)
        graph = rel.get("graph") or {}
        nodes = {n["id"]: n for n in graph.get("nodes") or []}
        done = []
        for row in pending:
            it = items.get(row["rubric_version"][len(CHECK_RUBRIC):])
            self.store.diagnosis_update(row["diagnosis_id"], status="running")
            try:
                d = ops.detail(row["run_id"], graph, t.get("spec"))
                if d is None:
                    raise ValueError("no run")
                # a check row has no ratio (a batch signal): its ratio slot keeps what the person said — kind and node hint (no DDL)
                self._diagnose(row, d, ops, graph, nodes, {"check": {k: (it or {}).get(k) for k in ("kind", "kinds", "node", "label", "expected", "field_ref", "start") if k not in ("kinds", "expected", "field_ref", "start") or (it or {}).get(k)}})
            except opsruns.OpsUnavailable as e:
                self.store.diagnosis_update(row["diagnosis_id"], status="error", error_kind=e.kind, finished_at=time.time())
                continue
            except Exception:
                self.store.diagnosis_update(row["diagnosis_id"], status="error", error_kind="broken", finished_at=time.time())
                continue
            done.append((row["diagnosis_id"], it))
        if done and self.wheels:
            by = {x["diagnosis_id"]: x for x in self.store.diagnoses(pid, rid)}
            rows = []
            for did, it in done:
                d, kind = by[did], (it or {}).get("kind")
                d = {**d, "suspect_node": check_node(d) or d.get("suspect_node")}   # the person's node hint first (CHG-265b ③)
                rows.append({**d, **({"human": it["label"]} if it and it["label"] else {"check": d["rubric_version"][len(CHECK_RUBRIC):], "kind": kind})})
            end, w = self.wheels.after_checks(pid, rel, rows)
            if end == "started" and w:   # EV-066 CHG-271: the kinds that opened it — every signal code of the app records it came from
                opening = sorted({c for _, it in done if it and not it.get("label") for c in it.get("codes") or []})
                if opening:
                    st = self.store.wheel_state(w["wheel_id"])
                    st["opening_kinds"] = opening
                    self.store.set_wheel_state(w["wheel_id"], st)

    def _signal_recurrence(self, pid, rel, runs, items):
        """EV-066 CHG-271 (D-418 scene ④): while this release is the version an improvement W made (state cand.version), the first app
        check record about its Runs that has no check row yet is K. K is written only once all its signal Runs are diagnosed here
        (until then it waits — no check row). With W's opening kinds O and K's kinds I: some gone and some still there → one line on W
        '재발 확인 — S1 헛걸음 사라짐 ✓ · S3 원인 다름 남음(…)' and K makes no check row (D-418 '표시'); I = ∅ → the solved line (EV-065);
        nothing gone or only new kinds → EV-065 (a check row, an improvement). K is kept in W's state 'recurrence' (once). Returns the
        record keys that make no check row this pass."""
        own = self._own_wheel(pid, rel)
        if own is None:
            return set()
        st = self.store.wheel_state(own["wheel_id"])
        opening = set(st.get("opening_kinds") or [])
        if st.get("recurrence"):   # K was taken: a K shown per signal never makes a check row later either
            return {st["recurrence"]} if st.get("recurrence_shown") else set()
        if not opening:
            return set()
        have = {d["rubric_version"][len(CHECK_RUBRIC):] for d in self.store.diagnoses(pid, rel["id"]) if is_check(d)}
        recs = []
        for it in items.values():
            if it.get("record") and it["record"] not in recs:
                recs.append(it["record"])
        for rk in recs:
            group = [it for it in items.values() if it.get("record") == rk]
            if any(it["key"] in have for it in group):
                continue   # counted before (EV-065)
            if any(r not in runs for r in group[0]["sig_runs"]):
                return {rk}   # waits for its Runs — nothing of it this pass
            st["recurrence"] = rk
            self.store.set_wheel_state(own["wheel_id"], st)
            got = set(group[0]["codes"])
            gone, still, new = sorted(opening - got), sorted(opening & got), sorted(got - opening)
            if got and gone and still:
                st["recurrence_shown"] = True
                self.store.set_wheel_state(own["wheel_id"], st)
                name = lambda cs: " · ".join(f"{c} {CHECK_KINDS[c]}" for c in cs)
                self.store.wheel_log(own["wheel_id"], 8, f"재발 확인 — {name(gone)} 사라짐 ✓ · {name(still)} 남음" + (f" · {name(new)} 새로" if new else "")
                                     + f"({rel['name']} 운영 · 기록 {rk})")
                return {rk}
            return set()   # solved (the solved line below) · nothing gone · new kinds only: as EV-065
        return set()

    def _own_wheel(self, pid, rel):
        """The improvement record this release belongs to: the one whose new version (state cand.version) it is, else the one a person
        tied it to when confirming an edit (D-444 #10: pack.from.wheel)."""
        ws = self.store.wheels(pid)
        own = next((w for w in ws if ((self.store.wheel_state(w["wheel_id"]) or {}).get("cand") or {}).get("version") == rel["id"]), None)
        seq = ((rel.get("pack") or {}).get("from") or {}).get("wheel")
        return own or (next((w for w in ws if w["seq"] == seq), None) if seq is not None else None)

    def _recurrence(self, pid, rel, record_key):
        """CHG-268: the record of this release (_own_wheel) gets '재발 확인 ✓' — no judge is called."""
        w = self._own_wheel(pid, rel)
        if w:
            self.store.wheel_log(w["wheel_id"], 8, f"재발 확인 ✓ — 사람 체크 해결됨({rel['name']} 운영 · 기록 {record_key})")

    def _diagnose(self, row, d, ops, graph, nodes, ratio):
        rules, rule_node = rule_check(d)
        judge = self._judge(row["run_id"], d, ops, graph, nodes, row.get("project_id"), check=is_check(row))
        kind = judge["kind"]
        trusted = kind in ("harness", "real")
        failed = [n for n in judge["nodes"] if n.get("ok") is False] if trusted else []
        suspect = failed[0]["node"] if failed else (rule_node if rule_node and rule_node != "run" else None)
        any_visit = bool(d.get("visits"))
        if rule_node or ratio.get("differ") or failed:
            signal = "fail"
        elif trusted and judge.get("normal") and any(n.get("ok") is not None for n in judge["nodes"]):   # something was judged
            signal = "normal"
        elif not any_visit:
            signal = "none"
        else:
            signal = "pass"
        self.store.diagnosis_update(row["diagnosis_id"], status="done", rule_result={"items": rules, "first": rule_node},
                                    ratio_result=ratio, judge_result=judge, judge_kind=kind, judge_model=judge.get("model"),
                                    signal=signal, suspect_node=suspect, error_kind=None, finished_at=time.time())

    @staticmethod
    def _raw(ops, run_id, graph, visit):
        """[input, output] raw values of one node visit, or None when either is missing (v24 '원문 없음': no file, expired, another
        sha) — then the node is not judged, so no reason is stored that could not be masked (Q-11). Reading the node's trace record
        failing is not '원문 없음': it raises, and that Run becomes an error row retried in place (change contract v4 CHG-151 · 152)."""
        val = ops.node_values(run_id, graph, visit["node"], visit.get("iter") or 1) or {}   # a trace read failure is raised: the Run
        if val.get("input") is None or val.get("output") is None:                         # becomes an error row (one Run unreadable)
            return None
        return [val["input"], val["output"]]

    def _judge(self, run_id, d, ops, graph, nodes, pid=None, check=False):
        """EV-065 CHG-264 · 269 (P-2): the test judge and a real judge look at check rows only (blind: raw values and the node's rubric);
        a general row keeps the fake provider's score (shown only) or, on a real-mode server, 'skipped' with JUDGE_CHECK_ONLY."""
        rb = rubric()
        targets = [v for v in d["visits"] if v.get("status") == "done" and judged(nodes.get(v["node"]))]
        if self.harness and check:
            h = self.harness.run(run_id)
            out = []
            for v in targets:
                raw = self._raw(ops, run_id, graph, v)
                if raw is None:   # no raw input or output (v24 '원문 없음', expired, unreadable): not judged, not a signal
                    out.append({"node": v["node"], "kind": nodes[v["node"]]["kind"], "ok": None, "items": [], "why": "judge 대상 아님(원문 없음)"})
                    continue
                hn = (h.get("nodes") or {}).get(v["node"])
                items = (hn or {}).get("items") or [{"name": i["name"], "ok": True, "why": ""} for i in _items(rb, nodes[v["node"]])]
                out.append({"node": v["node"], "kind": nodes[v["node"]]["kind"], "ok": (hn or {}).get("ok", True),
                            "items": [{"name": i["name"], "ok": i.get("ok", True), "why": mask_raw(i.get("why", ""), raw)} for i in items]})
            return {"kind": "harness", "model": "harness", "rubric": rb["version"], "nodes": out, "normal": bool(h.get("normal"))}
        if self.settings.mode != "fake":    # real judge calls: the user's Gate B (D-90, CHG-151 → D-405) — listed Projects only (EV-062)
            if not check:
                return {"kind": "skipped", "model": None, "rubric": rb["version"], "nodes": [], "why": JUDGE_CHECK_ONLY}
            return self._real_judge(run_id, ops, graph, nodes, pid, rb, targets)
        out = []
        for v in targets:
            node = nodes[v["node"]]
            raw = self._raw(ops, run_id, graph, v)
            if raw is None:
                out.append({"node": v["node"], "kind": node["kind"], "ok": None, "items": [], "why": "judge 대상 아님(원문 없음)"})
                continue
            val = {"input": raw[0], "output": raw[1]}
            # the fake provider's own fixed score for this content (gateway.fake_reply → fake_judge_score), computed here without a
            # key or a call: it is shown as 'judge(가짜)' only and never used as a signal (Q-9)
            score = gateway.fake_judge_score(json.dumps({"expect": (node.get("expect") or {}).get("text", ""), "input": val["input"],
                                                         "answer": val["output"]}, ensure_ascii=False, default=str)[:6000])
            ok = score >= 0.5
            why = gateway.FAKE_JUDGE_WHY
            out.append({"node": v["node"], "kind": node["kind"], "ok": ok, "score": round(score, 3),
                        "items": [{"name": i["name"], "ok": ok, "why": why} for i in _items(rb, node)]})
        return {"kind": "fake", "model": gateway.FAKE_MODEL, "rubric": rb["version"], "nodes": out,
                "why": "가짜 provider의 고정 점수 — 신호로 쓰지 않음"}


    def _real_judge(self, run_id, ops, graph, nodes, pid, rb, targets):
        """EV-062 (D-405): the real judge of one operational Run — on a real-mode server, for a Project listed in LLMOPS_OPS_JUDGE, with
        the wheel's cap, until a key or call failure stops it for the server's life. Every raw value is read before the first call (a
        read failure is that Run's error row, before any spend); every call counts in the server total at once; an answer the model
        did not judge is never a signal (D-405 4). Not judged → kind skipped with the reason (shown '통과 · judge 꺼짐')."""
        from .automain import UNIT_JUDGE_NODE, priced
        main = getattr(self.wheels, "main", None)
        skipped = lambda why: {"kind": "skipped", "model": None, "rubric": rb["version"], "nodes": [], "why": why}
        if pid not in (getattr(self.settings, "ops_judge", ()) or ()) or main is None or main.settings.auto_budget_usd is None:
            return skipped(GATE_B_OFF)
        if self._judge_stopped:
            return skipped(self._judge_stopped)
        provider, model = project_llm((self.store.project(pid) or {}).get("scoring"))
        if not priced(model):
            return skipped(f"가격표에 없는 모델({model})")
        read = [(v, nodes[v["node"]], self._raw(ops, run_id, graph, v)) for v in targets]   # all read first (A1-B1)
        out, calls, partial = [], 0, False
        for v, node, raw in read:
            base = {"node": v["node"], "kind": node["kind"]}
            src, text = node_rubric(node)   # EV-065 CHG-264: the Pack's rubric, else LLMOps' default (retrieve), else the expect
            if raw is None or not text:
                out.append({**base, "ok": None, "items": [], "why": "judge 대상 아님(원문 없음)" if raw is None else "judge 대상 아님(expect 없음)"})
                continue
            base["rubric"] = src
            if partial or main.over_cap(UNIT_JUDGE_NODE, day=False):   # D-470 ⓓ: the daily cap is the wheels' only
                partial = True
                out.append({**base, "ok": None, "items": [], "why": JUDGE_CAP})
                continue
            calls += 1
            try:
                r = gateway.judge_node(self.gw, text, raw[0], raw[1], provider, model, **gateway.node_kw(self.settings, v["node"]))   # D-604
            except Exception as e:   # noqa: BLE001 - a key, address or call failure: the server stops real judging ('키 1 실패면 멈춤')
                main.add_judge_spend(UNIT_JUDGE_NODE)
                code = getattr(e, "code", None) if isinstance(e, urllib.error.HTTPError) else None   # EV-063: the status only, never the body
                self._judge_stopped = f"실제 judge 멈춤 — 키 · 호출 실패({type(e).__name__}{f' {code}' if isinstance(code, int) else ''})"
                print(f"자동 진단: {self._judge_stopped} — 이 서버 동안 실제 judge를 부르지 않음", file=sys.stderr)
                return skipped(self._judge_stopped)
            main.add_judge_spend(UNIT_JUDGE_NODE)
            if not isinstance(r, dict) or not isinstance(r.get("score"), (int, float)) or r.get("why") == JUDGE_UNPARSED:
                out.append({**base, "ok": None, "items": [], "why": "judge 응답 해석 못 함"})
                continue
            ok = r["score"] >= 0.5
            why = mask_raw(str(r.get("why") or ""), raw)
            out.append({**base, "ok": ok, "score": round(float(r["score"]), 3),
                        "items": [{"name": i["name"], "ok": ok, "why": why} for i in _items(rb, node)]})
        if partial and not calls:
            return skipped(JUDGE_CAP)
        return {"kind": "real", "model": model, "rubric": rb["version"], "nodes": out, "normal": False, "calls": calls,
                "usd": round(calls * UNIT_JUDGE_NODE, 6), "partial": partial, "items_from": "node_score"}


def _items(rb, node):
    """The rubric's items for the node's kind; a custom_code node judged by its Pack rubric has one item."""
    return rb["kinds"].get(kind_name(node.get("kind"))) or [{"name": "Pack 기준"}]


def _with_empty(eval_run, nodes):
    """The evaluation Run with each retrieve visit marked when it returned nothing (for the ratio's '빈 검색')."""
    if not eval_run:
        return None
    cases = []
    for c in eval_run.get("cases") or []:
        vs = []
        for v in c.get("visits") or []:
            v = dict(v)
            if kind_name((nodes.get(v.get("node")) or {}).get("kind")) == "retrieve":
                v["_empty"] = _count_items(v.get("output"), (nodes.get(v["node"]) or {}).get("writes")) == 0
            vs.append(v)
        cases.append({**c, "visits": vs})
    return {**eval_run, "cases": cases}


# ------------------------------------------------------------------ what the screen shows
# CHG-154 (Q-7 A): the fixed failure label list v1 — a person's label comes before every other cell value (D-92)
LABEL_LIST_VERSION = "labels-v1"
LABELS = ("검색 못 함", "근거 부족", "규칙 실패", "LLM 출력 틀림", "정상 동작(escalation 맞음)", "Pack 밖 원인")
OK_LABELS = ("정상 동작(escalation 맞음)",)
# D-550 (llmops-2 151 · dev-2 only): the words a label shows — the stored value (the key above) stays; '검색' is not a word people read
LABEL_SHOWN = {"검색 못 함": "리트리버 결과 없음"}


def label_word(value):
    return LABEL_SHOWN.get(value, value)


def run_raw(ops, run_id, graph, detail):
    """Every raw value of one operational Run that can be read (its visits' inputs and outputs) — what a person's label reason is
    masked against before it is stored (Q-11 · V-DIAG-NOCOPY). A node whose values cannot be read adds nothing."""
    out = []
    for v in (detail or {}).get("visits") or []:
        try:
            val = ops.node_values(run_id, graph, v["node"], v.get("iter") or 1) or {}
        except Exception:
            continue
        out += [x for x in (val.get("input"), val.get("output")) if x is not None]
    return out


def settle_s():
    """LABEL_SETTLE_S, or — test harness only (ISOLATED_ENV=1) — LLMOPS_FAKE_LABEL_SETTLE_S read on every call (verify-e2e.sh sets 0)."""
    v = os.environ.get("LLMOPS_FAKE_LABEL_SETTLE_S") if os.environ.get("ISOLATED_ENV") == "1" else None
    try:
        return float(v) if v not in (None, "") else LABEL_SETTLE_S
    except ValueError:
        return LABEL_SETTLE_S


def near_line(line, prev_op=None):
    """D-597 · D-675: the versions a pass reads besides the 운영 one — five deep on the graph-only line, and the version 운영 just
    before (prev_op) when it stands further back on that line (a demo reset brings an old version back while the list keeps the newer
    ones: the next graph's check arrives on the reset version's Runs). Not on the line: as it was."""
    out = list(line[:READ_LINE])
    if prev_op and all(r["id"] != prev_op for r in out):
        out += [r for r in line[READ_LINE:] if r["id"] == prev_op]
    return out


def version_rows(store, pid, rel, drs_of=None, since=None, releases=None):
    """EV-088 (D-484 덧 14 · 덧 17 Q-1 · Q-2 · Q-3 (가)): ({run_id: its general diagnosis row}, labels) of this version's operational Runs.
    Among the same Pack · revision versions a Run is the one whose registered data bundle (versions.release_drs) is the Run's trace drs
    (drs_of: run_id → drs); none matching or no drs: every version of the revision (as before). The rows are read where they were made
    — a Run diagnosed under the version operational then keeps that one row (no new diagnosis, nothing written); this version's own
    row first. Labels on those Runs from any of these versions; with no drs, a label put on another version counts only when made at or
    after `since` (when this version became operational)."""
    from . import versions
    pk = rel.get("pack") or {}
    same = [r for r in (releases if releases is not None else store.releases(pid)) if (r.get("pack") or {}).get("id") == pk.get("id") and (r.get("pack") or {}).get("revision") == pk.get("revision")]
    ids = {r["id"] for r in same} | {rel["id"]}
    regs = {r["id"]: versions.release_drs(r) for r in same}

    def belongs(run):
        if len(ids) <= 1:
            return True
        d = (drs_of or {}).get(run)
        if not d:
            return None
        owners = [k for k, v in regs.items() if v == d]
        return (rel["id"] in owners) if owners else True
    runs = {}
    for d in general(store.diagnoses(pid)):
        if d["release_id"] in ids and d.get("pack_revision") == pk.get("revision") and belongs(d["run_id"]) is not False:
            if d["run_id"] not in runs or d["release_id"] == rel["id"]:
                runs[d["run_id"]] = d
    labels = []
    for lb in store.labels(pid):
        if lb["release_id"] not in ids or lb["run_id"] not in runs:
            continue
        if belongs(lb["run_id"]) is None and lb["release_id"] != rel["id"] and since is not None and (lb.get("created_at") or 0) < since:
            continue
        labels.append(lb)
    return runs, labels


def labels_settling(store, pid, rid, now=None):
    """D-478 덧 28: a person's ✗ label on the release came less than the settle time ago (so its batch may still be coming in)."""
    times = [lb.get("created_at") or 0 for lb in store.labels(pid, rid)]
    return bool(times) and ((now if now is not None else time.time()) - max(times)) < settle_s()


def is_check(d):
    """EV-065 CHG-263a: a person-check diagnosis row (its rubric 'check-1:…'). The 0007 signal check allows no other value, so the
    row is told apart by its rubric and its signal stays what the diagnosis found (no DDL)."""
    return str(d.get("rubric_version") or "").startswith("check-")


def field_truth(rec):
    """EV-065 CHG-266 (D-412 decision 7): the field-truth expected value of an app record — the c1a15-eval rule's line for a solved
    session, in the expectations shape {"final"?: [checks], "nodes": {node: [checks]}} (gold positions as 'evidence' checks, the
    confirmed cause as a node or final check): refs only, never raw text. Until the eval side's hand-over shape is set (question
    160) it is read from the record's field_truth.expected; None when there is none."""
    exp = ((rec or {}).get("field_truth") or {}).get("expected")
    if not isinstance(exp, dict) or not (exp.get("nodes") or exp.get("final")):
        return None
    nodes = {str(n): [c for c in chks if isinstance(c, dict)] for n, chks in (exp.get("nodes") or {}).items() if isinstance(chks, list)}
    out = {"nodes": {n: c for n, c in nodes.items() if c}}
    if isinstance(exp.get("final"), list) and exp["final"]:
        out["final"] = [c for c in exp["final"] if isinstance(c, dict)]
    return out if out["nodes"] or out.get("final") else None


FIELD_GOLD_MAX = 256 * 1024   # D-444 #11: a hand-over line bigger than this is not read
FIELD_GOLD_KEY = re.compile(r"[A-Za-z0-9._-]{1,128}")
FIELD_GOLD_SCHEMA = re.compile(r"[a-z0-9_.-]+\.field-gold\.v1")   # the eval tool's line (c1a15-eval field_gold.py) — no Pack name kept here


def field_gold(folder, key, node_ids):
    """D-444 #11 (D-413 · ORCH answer 235 · D-470 덧 6): the field-truth expected value of record `key` from the hand-over folder —
    `<folder>/<key>.json`, the eval tool's line as it is, read only. (expected, None) when it can be used; (None, why) when it cannot —
    a conflict mark `<key>.conflict.json` beside it (the deploy side: the same record_key came with another content), too big, not
    the line's shape, another record_key, gate.ok not true (the eval gates: approved · solved · …), a node not in the graph;
    (None, None) when there is no file (yet)."""
    if not folder or not isinstance(key, str) or not FIELD_GOLD_KEY.fullmatch(key) or key.startswith("."):
        return None, None
    f = Path(folder) / f"{key}.json"
    try:
        if (Path(folder) / f"{key}.conflict.json").exists():
            return None, "충돌 표시(같은 기록 번호 다른 내용)"
        if not f.is_file():
            return None, None
        if f.stat().st_size > FIELD_GOLD_MAX:
            return None, "너무 큼"
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "형식 다름"
    if not isinstance(doc, dict) or not isinstance(doc.get("schema"), str) or not FIELD_GOLD_SCHEMA.fullmatch(doc["schema"]):
        return None, "형식 다름"
    if doc.get("record_key") != key:
        return None, "기록 번호 다름"
    if not (isinstance(doc.get("gate"), dict) and doc["gate"].get("ok") is True):
        return None, "문 통과 안 함(gate)"
    exp = rule_expected({"expected": doc.get("expected")}, None, node_ids) if isinstance(doc.get("expected"), dict) else None
    if not exp:
        return None, "그래프에 없는 node"
    return exp, None


FIELD_GOLD_FILES = 1000   # D-444 #11 (가): the most hand-over lines read for a Project's field bundle


def field_gold_lines(folder, node_ids):
    """D-444 #11 (가): the hand-over lines that can be used, by the Run they were made from — {start_run_id: expected}."""
    out = {}
    try:
        names = sorted(p.name for p in Path(folder).glob("*.json") if not p.name.endswith(".conflict.json"))[:FIELD_GOLD_FILES]
    except OSError:
        return out
    for n in names:
        exp, _ = field_gold(folder, n[: -len(".json")], node_ids)
        if not exp:
            continue
        try:
            run = ((json.loads((Path(folder) / n).read_text(encoding="utf-8")).get("provenance") or {}).get("start_run_id"))
        except (OSError, ValueError):
            continue
        if isinstance(run, str) and run:
            out.setdefault(run, exp)
    return out


REF_MAX = 120   # EV-066 CHG-272: a field-truth ref longer than this is not a ref (dropped)


def field_refs(rec):
    """EV-066 CHG-272: the app document's field-truth refs {confirmed_sections: [...], confirmed_cause} — short strings only (a longer
    one is dropped, never stored); None when there is none. D-546 덧 23 ② (eval exception-label): exception_wasted [...] — the
    exception check sections a person found 정상 (the start Run's exception guide was a 헛걸음)."""
    ft = (rec or {}).get("field_truth") or {}
    if not isinstance(ft, dict):
        return None
    secs = [s for s in ft.get("confirmed_sections") or [] if isinstance(s, str) and 0 < len(s) <= REF_MAX]
    cause = ft.get("confirmed_cause") if isinstance(ft.get("confirmed_cause"), str) and 0 < len(ft["confirmed_cause"]) <= REF_MAX else None
    wasted = [s for s in ft.get("exception_wasted") or [] if isinstance(s, str) and 0 < len(s) <= REF_MAX] if isinstance(ft.get("exception_wasted"), list) else []
    out = {**({"confirmed_sections": secs} if secs else {}), **({"confirmed_cause": cause} if cause else {}), **({"exception_wasted": wasted} if wasted else {})}
    return out or None


def rule_expected(rule, refs, node_ids):
    """EV-066 CHG-272 (D-419 (1) · 덧 (다)): the trigger case's expected value from the start document's field_truth — either the eval
    rule's line handed over as it is ({"expected": {"nodes": {node: [checks]}}}, every node in the graph) or a rule {node, path, from:
    confirmed_sections | confirmed_cause, check: equals | contains} over the record's refs → {"nodes": {node: [{path, check: value}]}};
    None when it is missing or wrong (node not in the graph, another from · check) or the ref is missing."""
    if isinstance(rule, dict) and isinstance(rule.get("expected"), dict):   # D-419 덧 (다): the eval rule's line handed over as it is
        nodes = (rule["expected"].get("nodes") or {}) if isinstance(rule["expected"].get("nodes"), dict) else {}
        ok = nodes and all(n in node_ids and isinstance(c, list) and c and all(isinstance(x, dict) for x in c) for n, c in nodes.items())
        return {"nodes": nodes} if ok else None
    if not isinstance(rule, dict) or not isinstance(refs, dict):
        return None
    node, path, src, chk = rule.get("node"), rule.get("path"), rule.get("from"), rule.get("check")   # no default (E4-F1 B2)
    if node not in node_ids or not isinstance(path, str) or src not in ("confirmed_sections", "confirmed_cause") or chk not in ("equals", "contains"):
        return None
    val = refs.get(src)
    val = val[0] if isinstance(val, list) and val else val
    return {"nodes": {node: [{"path": path, chk: val}]}} if isinstance(val, str) and val else None


def check_node(d):
    """EV-065 CHG-265b ③: the node the person's check names (None when it names none) — kept in the check row's ratio slot."""
    return (((d.get("ratio_result") or {}).get("check")) or {}).get("node")


def general(rows):
    """EV-065 CHG-263a: the general diagnosis rows only — the list cell, the detail, the band and the ratio read these."""
    return [d for d in rows if not is_check(d)]


def cell(diag, label=None, check=None):
    """The 진단(자동) cell of one operational Run (change contract v4 '진단(자동) 칸 값 표', priority 사람 > judge ✗ > 규칙 ✗ >
    비율 다름 > 정상 동작 > 통과). EV-065 CHG-263: check = the kind of the app person-check signal naming this Run ('사람 체크 ·
    헛걸음'), after a label in this Project (the label is the later, closer word) and before everything the diagnosis found."""
    if label:
        return {"text": "사람 ✓" if label["label"] in OK_LABELS else f"사람 ✗ {label_word(label['label'])}", "signal": "human"}
    if check:
        return {"text": f"사람 체크 · {check}", "signal": "human"}
    if not diag:
        return {"text": "대기", "signal": None}
    st = diag["status"]
    if st == "waiting":
        return {"text": "대기", "signal": None}
    if st == "running":
        return {"text": "진단 중…", "signal": None}
    if st == "error":
        return {"text": f"진단 못 함 — {UNREADABLE_TEXT.get(diag.get('error_kind'), '기록을 읽다 멈춤')}", "signal": "error"}
    kind, sig = diag.get("judge_kind"), diag.get("signal")
    judge_failed = kind in ("harness", "real") and any(n.get("ok") is False for n in (diag.get("judge_result") or {}).get("nodes") or [])
    if sig == "fail":
        if judge_failed:
            return {"text": f"judge ✗ {diag.get('suspect_node') or ''}".strip(), "signal": "fail"}
        rule_first = (diag.get("rule_result") or {}).get("first")
        if rule_first:
            return {"text": f"규칙 ✗ {rule_first if rule_first != 'run' else 'Run'}", "signal": "fail"}
        return {"text": "비율 다름", "signal": "fail"}
    if sig == "normal":
        return {"text": "정상 동작", "signal": "normal"}
    if sig == "none":
        return {"text": "", "signal": "none"}
    if kind == "fake":
        return {"text": "통과 · judge(가짜)", "signal": "pass"}
    if kind == "skipped":
        return {"text": "통과 · judge 꺼짐", "signal": "pass"}
    return {"text": "통과", "signal": "pass"}
