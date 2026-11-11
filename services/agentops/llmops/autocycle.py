"""EV-030 AC-4 (CHG-156 · CHG-157, supplement A5 · 08/26) · EV-065 (D-412): the improvement wheel that starts by itself.

One wheel at a time per Project (D-97). EV-065 CHG-263 (P-1): only a person's check starts it — an app person-check record
(CHG-262) or a person's ✗ label (EV-064) — on the 운영 version: ① 진단 (done) → ② 검토 (people's labels, not waited for) →
③ dataset → … A diagnosis batch alone (rules · ratio · judge) is shown and starts nothing; the normal-behaviour notice wheel (D-95)
is gone (P-6). While a wheel is open, a new check adds one line to its log instead of a new wheel. When the 운영 version changes, the
open wheel ends ('기준이 바뀜') and no new wheel waits (P-7) — the next person's check starts one.
Every step writes one log line (append only). Nothing here touches versions, verdicts, deployments, suites or promotions."""
import json
import sys
import time

STOPPING = "멈추는 중"   # D-476 덧 20: a person asked the wheel to stop; it ends before its next step
CELLS = ("① 진단", "② 검토", "③ dataset", "④ 가설", "⑤ 후보", "⑥ 비교", "⑦ 승인", "⑧ 배포")
MANUAL_3 = "③ 자동 올리기 꺼짐 — 평가 case는 손으로 올리기"   # the EV-030 path (no automain): a state, not a sentence (D-368 · R1)
WAIT_4 = "대기 — 가설은 Gate B 뒤에 열립니다"
END_DEPLOYED = "끝 · ⑧ 배포 — {name} 운영"   # EV-035 A11 (supplement 14/26): the wheel's own version became 운영 — the next wheel starts at ①
NORMAL_END = "끝 · 정상 동작(escalation이 맞음) — 알림"
SCOPE, GOAL = "옵션만", "정확도"           # automatic wheels have a fixed scope and goal (D-106)
DEFAULT_SETTINGS = {"max_hypotheses": 10, "rebuilds": 3, "parallel": "auto"}   # the settings screen comes with AC-6
# EV-069 CHG-EF-2 (D-106 · D-433): the efficiency wheel — a person starts it or the set time does; scope 구조까지; goals in the
# ranking order of Q-EF-5; a scheduled start keeps the three fixed (Q-EF-8, EV-030 C1 '자동 바퀴의 범위 · 목표는 고정')
EFF_SCOPE = "구조까지"
GOALS = {"cost": "비용", "calls": "LLM 호출", "latency": "지연"}
GOAL_ORDER = ("cost", "calls", "latency")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
BY_SCHEDULE = "정한 시각"
BY_TRACE = "운영 Run 쌓임"   # D-470 ⓓ


def goal_text(goals):
    return " · ".join(GOALS[g] for g in GOAL_ORDER if g in goals)


def efficiency_refusal(store, rel, goals, work_suite, confirm_suite):
    """None when an efficiency wheel may open on `rel`, else (HTTP status, reason) — the API's and the scheduled start's one rule."""
    from . import automain, fixedflow
    if not goals or any(g not in GOALS for g in goals):
        return 422, "목표는 비용 · LLM 호출 · 지연 중 하나 이상"
    if fixedflow.state(fixedflow.ensure(store, rel)) != fixedflow.STATE_DONE:
        return 409, "고정 흐름 확인 필요"
    work = store.suite(work_suite) if work_suite else None
    if not work:
        return 422, "작업 suite 없음"
    conf = store.suite(confirm_suite) if confirm_suite else None
    if not conf:
        return 422, "확인 suite 없음"
    seen = {automain.input_sha(c.get("input")) for c in work["cases"]}
    n = sum(automain.input_sha(c.get("input")) in seen for c in conf["cases"])
    if n:
        return 422, f"확인 suite 겹침 {n}"
    return None


def default_work_suite(store, rel):
    """The 운영 version's last suite Run's suite — the accuracy wheel's base suite rule (automain.dataset)."""
    run = store.last_suite_run(rel["id"])
    return run["input"]["id"] if run else None


def _ts(iso):
    """An ISO time of the logDB's Run list as a timestamp (0 when missing or unreadable)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def slot_of(day, hhmm, now):
    """The latest weekly slot (day · HH:MM, local time) at or before `now` (epoch seconds)."""
    t = time.localtime(now)
    h, m = (int(x) for x in hhmm.split(":"))
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    slot = midnight - ((t.tm_wday - DAYS.index(day)) % 7) * 86400 + h * 3600 + m * 60
    return slot if slot <= now else slot - 7 * 86400


def valid_hhmm(s):
    try:
        h, m = (int(x) for x in str(s).split(":"))
    except ValueError:
        return False
    return len(str(s)) == 5 and 0 <= h < 24 and 0 <= m < 60


def _counts(rows):
    """EV-065: '사람 체크 3건 · 사람 ✗ 라벨 1건' — the person's checks in rows (app records first)."""
    by = {"사람 체크": sum(1 for d in rows if d.get("check")), "사람 ✗ 라벨": sum(1 for d in rows if d.get("human") and not d.get("check"))}
    return " · ".join(f"{k} {n}건" for k, n in by.items() if n)


def _summary(rows, cell_of):
    """'사람 체크 5건 — 의심 node find_manuals' — what ① found: the person's checks (EV-065 CHG-263a: every check row, whatever its
    diagnosis found), else the diagnosis' failures ('judge 실패 5건' / '규칙 실패 2건 · 비율 다름 1건')."""
    checks = [d for d in rows if d.get("check") or d.get("human")]
    if checks:
        head, fails = _counts(checks), checks
    else:
        fails = [d for d in rows if d.get("signal") == "fail"]
        by = {}
        for d in fails:
            t = cell_of(d)["text"]
            k = "judge 실패" if t.startswith("judge") else "규칙 실패" if t.startswith("규칙") else "비율 다름"
            by[k] = by.get(k, 0) + 1
        head = " · ".join(f"{k} {n}건" for k, n in by.items())
    sus = [d["suspect_node"] for d in fails if d.get("suspect_node")]
    top = max(set(sus), key=sus.count) if sus else None
    return head + (f" — 의심 node {top}" if top else "")


RESUME_3 = "③ dataset 다시 — 본줄 열림"   # EV-035: a wheel that waited at ④ in the EV-030 version goes back to ③


class Wheels:
    def __init__(self, store, cell_of, main=None):
        self.store, self.cell_of = store, cell_of
        self.main = main   # EV-035: automain.Main — ③ onward (None keeps the EV-030 behaviour, ④ waits)
        self.trace_tried = {}   # D-470 ⓓ: (pid, revision, since) → the 운영 Run count a start was refused at (waits for `after` more)

    def new_settings(self, **extra):
        """D-513: a new wheel's settings — its iterations from LLMOPS_AUTO_REBUILDS when set, kept on the wheel (3 otherwise)."""
        n = getattr(getattr(self.main, "settings", None), "auto_rebuilds", None)
        return {**DEFAULT_SETTINGS, **({"rebuilds": n} if n else {}), **extra}

    def _log(self, w, cell, text):
        self.store.wheel_log(w["wheel_id"], cell, text)

    def check_base(self, pid, rel, line=()):
        """The 운영 version moved: the open wheel ends ('기준이 바뀜'). EV-065 (P-7): no new wheel waits on the new base (D-97 gone).
        D-597: line = the versions that differ from rel only in the graph — a wheel based on one of them goes on, on its own base."""
        w = self.store.open_wheel(pid)
        if not w or (w["base_release_id"], w["base_revision"]) == (rel["id"], rel["pack"]["revision"]):
            return None
        old = self.store.release(w["base_release_id"])
        if ((self.store.wheel_state(w["wheel_id"]) or {}).get("cand") or {}).get("version") == rel["id"]:   # EV-035 A11: its own new version runs
            why = END_DEPLOYED.format(name=rel["name"])
            self._log(w, 8, why)
            self.store.wheel_update(w["wheel_id"], cell=8, outcome=why, cell_state="done", ended_at=time.time())
        elif any((r["id"], r["pack"]["revision"]) == (w["base_release_id"], w["base_revision"]) for r in line):
            st = self.store.wheel_state(w["wheel_id"]) or {}
            if st.get("graph_moved") != rel["id"]:
                self._log(w, w["cell"], f"그래프만 {rel['name']}(으)로 바뀜 — 기준 {(old or {}).get('name', '?')} 그대로 이어감")
                self.store.set_wheel_state(w["wheel_id"], {**st, "graph_moved": rel["id"]})
        else:
            why = f"끝 · 기준이 바뀜({(old or {}).get('name', '?')} → {rel['name']})"
            self._log(w, w["cell"], f"기준 운영 버전이 {rel['name']}(으)로 바뀜 — 이 바퀴는 끝")
            self.store.wheel_update(w["wheel_id"], outcome=why, cell_state="done", ended_at=time.time())
        return None   # EV-065 CHG-263 (D-412 decision 1 — P-7): no new wheel waits on the new base; the next person's check starts one

    def after_batch(self, pid, rel, rows):
        """One finished diagnosis batch (general rows) → one line on the open wheel, or nothing. Returns ('joined' | None, wheel).
        EV-065 CHG-263a (D-412 decision 1 — P-1 · P-6): a batch starts nothing (rule · ratio · judge signals are shown on the screen);
        the open wheel past ① keeps its '함께 진단' line; the 'normal behaviour only' notice wheel is gone."""
        if not any(d.get("signal") == "fail" for d in rows):
            return None, None
        w = self.store.open_wheel(pid)
        if w and not (w["cell"] == 1 and w["cell_state"] == "running"):
            self._log(w, w["cell"], f"새 운영 Run {len(rows)}건 진단 — 한 번에 한 바퀴")   # V-WHEEL
            return "joined", w
        return None, None

    def after_checks(self, pid, rel, rows):
        """EV-065 CHG-263a: the person-check diagnosis rows finished now → a new wheel or a line on the open one, whatever the diagnosis
        found (rows: 'check' = an app record's key, 'human' = a ✗ label's text). Returns ('started' | 'joined' | None, wheel)."""
        if not rows:
            return None, None
        w = self.store.open_wheel(pid)
        if w and not (w["cell"] == 1 and w["cell_state"] == "running"):   # an open wheel already past ①: one line, no new wheel
            self._log(w, w["cell"], f"{_counts(rows)} — 한 번에 한 바퀴")   # V-WHEEL
            return "joined", w
        if not w:
            why = f"자동(사람 체크) · {len(rows)}건" if any(d.get("check") for d in rows) else f"자동(사람 ✗ 라벨) · {len(rows)}건"
            w = self.store.add_wheel(pid, "performance", why, rel["id"], rel["pack"]["revision"], SCOPE, GOAL, 1, "running", json.dumps(self.new_settings()))
        self._log(w, 1, _summary(rows, self.cell_of))
        labels = [lb for lb in self.store.labels(pid, rel["id"]) if lb["run_id"] in {d["run_id"] for d in rows}]
        self.store.wheel_update(w["wheel_id"], cell=2)
        self._log(w, 2, f"사람 라벨 {len({lb['run_id'] for lb in labels})}건 — 기다리지 않고 진행")
        self.store.wheel_update(w["wheel_id"], cell=3)
        if self.main and self.main.on():   # EV-035 CHG-180: ③ goes on in advance() — the write gate first, then the automatic promotion
            self._log(w, 3, "③ dataset — 작업 suite를 만듦")
            self.advance(pid, rel)
            return "started", self.store.wheel(w["wheel_id"])
        self._log(w, 3, MANUAL_3)
        self.store.wheel_update(w["wheel_id"], cell=4, cell_state="waiting")
        self._log(w, 4, WAIT_4)
        return "started", self.store.wheel(w["wheel_id"])

    def start_efficiency(self, pid, rel, by, goals, work_suite, confirm_suite):
        """EV-069 CHG-EF-2: open an efficiency wheel on the 운영 version at ③ (no diagnosis · review — nothing failed), or a line on
        the open wheel (D-97). The caller checked efficiency_refusal first. Returns ('started' | 'joined', wheel)."""
        w = self.store.open_wheel(pid)
        if w:
            self._log(w, w["cell"], f"효율 개선 시작 요청({by}) — 한 번에 한 바퀴")
            return "joined", w
        goals = [g for g in GOAL_ORDER if g in goals]
        settings = self.new_settings(efficiency={"goals": goals, "work_suite": work_suite, "confirm_suite": confirm_suite})
        w = self.store.add_wheel(pid, "efficiency", by, rel["id"], rel["pack"]["revision"], EFF_SCOPE, goal_text(goals), 3, "running", json.dumps(settings))
        self._log(w, 3, f"효율 개선 — {goal_text(goals)} · ③ dataset")
        if self.main and self.main.on():
            self.advance(pid, rel)
        return "started", self.store.wheel(w["wheel_id"])

    def trace_efficiency(self, pid, rel, runs, after):
        """D-470 ⓓ (D-444 #5 · answer 067): `after` finished 운영 Runs of the 운영 revision (runs = the pass's list, newest first) since its
        last efficiency wheel opened open one with the set time's goals and suites (Q-EF-8). Not while a wheel is open or the daily
        cap is reached; a refusal is one server line and waits for `after` more. Returns 'started' | 'joined' | None."""
        if not after or self.store.open_wheel(pid):
            return None
        revision = rel["pack"]["revision"]
        since = max((w["started_at"] for w in self.store.wheels(pid) if w["kind"] == "efficiency" and w["base_revision"] == revision), default=0.0)
        n = sum(1 for r in runs or [] if r.get("status") == "succeeded" and _ts(r.get("started_at")) > since)
        key = (pid, revision, since)
        if n < after or n < self.trace_tried.get(key, 0) + after:
            return None
        main = getattr(self, "main", None)
        if main is not None and main.real and main.over_cap(0.0):
            self.trace_tried[key] = n
            return None
        eff = (self.store.auto_settings(pid).get("efficiency") or {})
        work = eff.get("work_suite") or default_work_suite(self.store, rel)
        why = efficiency_refusal(self.store, rel, list(GOAL_ORDER), work, eff.get("confirm_suite"))
        if why:
            import sys
            self.trace_tried[key] = n
            print(f"llmops 효율 개선 운영 Run {n}개 쌓임 — 열지 않음: {why[1]}", file=sys.stderr, flush=True)
            return None
        return self.start_efficiency(pid, rel, BY_TRACE, list(GOAL_ORDER), work, eff.get("confirm_suite"))[0]

    def scheduled_efficiency(self, pid, rel, now=None):
        """EV-069 CHG-EF-2: the set weekly time has come since the last slot → the efficiency wheel opens with the fixed goals
        (D-433 Q-EF-8). One try per slot: a refusal is one server line and waits for the next slot. Returns 'started' | 'joined' | None."""
        now = time.time() if now is None else now
        data = self.store.auto_settings(pid)
        eff = data.get("efficiency") or {}
        sched = eff.get("schedule")
        if not sched:
            return None
        slot = slot_of(sched["day"], sched["time"], now)
        if (eff.get("last_slot") or 0) >= slot:
            return None
        self.store.set_auto_settings(pid, {**data, "efficiency": {**eff, "last_slot": slot}}, BY_SCHEDULE)
        work = eff.get("work_suite") or default_work_suite(self.store, rel)
        why = efficiency_refusal(self.store, rel, list(GOAL_ORDER), work, eff.get("confirm_suite"))
        if why:
            import sys
            print(f"llmops 효율 개선 정한 시각 — 열지 않음: {why[1]}", file=sys.stderr, flush=True)
            return None
        return self.start_efficiency(pid, rel, BY_SCHEDULE, list(GOAL_ORDER), work, eff.get("confirm_suite"))[0]

    def advance(self, pid, rel, line=()):
        """EV-035: one step of the open wheel's main line on each diagnosis pass (③ now; ④ ~ ⑧ in later slices)."""
        if self.main:   # D-444 #10: records a person tied a version to follow it (lines on ⑦ · ⑧ only)
            try:
                self.main.follow_human(pid)
            except Exception as e:   # noqa: BLE001 - kept out of the main line
                print(f"llmops 사람 편집 따라가기 건너뜀 — {type(e).__name__}", file=sys.stderr, flush=True)
        if self.main:   # D-687: what a baseline cut off by a moved base really cost
            try:
                self.main.settle_cut()
            except Exception as e:   # noqa: BLE001 - kept out of the main line
                print(f"llmops 끊긴 기준선 기록 건너뜀 — {type(e).__name__}", file=sys.stderr, flush=True)
        if self.main:   # D-476 덧 20: a person's stop request ends the wheel before anything else (also after a restart)
            ow = self.store.open_wheel(pid)
            if ow and self.store.wheel_stop(ow["wheel_id"]):
                self.main.stop_wheel(ow)
                return self.store.wheel(ow["wheel_id"])
        if not self.main or not self.main.on():
            return None
        w = self.store.open_wheel(pid)
        if w and w["cell"] in (7, 8) and w["ended_at"] is None:   # EV-035 ML-5: ⑦ · ⑧ follow the new version, also once it runs
            try:
                self.main.deploy(w)
            except Exception as e:   # noqa: BLE001 - kept in the wheel's log; the next pass reads again
                self.main._hold(self.store.wheel(w["wheel_id"]), self.store.wheel_state(w["wheel_id"]), "stopped", f"단계 오류({type(e).__name__}) — 다음에 다시")
            return self.store.wheel(w["wheel_id"])
        rel = next((r for r in (rel, *line) if w and (r["id"], r["pack"]["revision"]) == (w["base_release_id"], w["base_revision"])), None)   # D-597: its own base
        if rel is None:
            return None
        if w["cell"] == 4 and w["cell_state"] == "waiting" and not self.store.wheel_state(w["wheel_id"]).get("suites"):
            self._log(w, 3, RESUME_3)   # an EV-030 wheel: no work suite, sets or baseline yet (critic E3-A1 B3)
            self.store.wheel_update(w["wheel_id"], cell=3, cell_state="running")
            w = self.store.wheel(w["wheel_id"])
        wid = w["wheel_id"]

        def halted():   # D-476 덧 20: asked while a step ran — the wheel ends before its next step
            if not self.store.wheel_stop(wid):
                return False
            self.main.stop_wheel(self.store.wheel(wid))
            return True
        try:
            if w["cell"] == 3 and w["cell_state"] in ("running", "waiting", "stopped"):
                self.main.dataset(w, rel)
                w = self.store.wheel(wid)
            if halted():
                return self.store.wheel(wid)
            if w["cell"] == 4 and w["cell_state"] in ("running", "waiting", "stopped") and w["ended_at"] is None and self.store.wheel_state(wid).get("suites"):
                self.main.hypotheses(w, rel)   # EV-035 ML-2
                w = self.store.wheel(wid)
            if halted():
                return self.store.wheel(wid)
            if w["cell"] == 5 and w["cell_state"] in ("running", "waiting", "stopped") and w["ended_at"] is None:
                self.main.candidates(w, rel)   # EV-035 ML-3
                w = self.store.wheel(wid)
            if halted():
                return self.store.wheel(wid)
            if w["cell"] == 6 and w["cell_state"] in ("running", "waiting", "stopped") and w["ended_at"] is None:
                self.main.compare(w, rel)   # EV-035 ML-4 (it writes the hidden suite's spend first, ML3-B2)
            if halted():
                return self.store.wheel(wid)
        except Exception as e:   # noqa: BLE001 - kept in the wheel's log (not swallowed — slice review note); the next pass tries again
            self.main._hold(self.store.wheel(w["wheel_id"]), self.store.wheel_state(w["wheel_id"]), "stopped", f"단계 오류({type(e).__name__}) — 다음에 다시")
        return self.store.wheel(w["wheel_id"])


def cells(w):
    """The 8-cell bar of one wheel: done · now · human · stopped · waiting · idle (초록 · 주황 · 파랑 · 빨강 · 회색 · 빈 칸)."""
    out = []
    for i in range(1, 9):
        if w.get("kind") == "efficiency" and i < 3:   # EV-069 (preview P-1): an efficiency wheel has no diagnosis · review — ① ② stay empty
            out.append("idle")
        elif i < w["cell"]:
            out.append("done")
        elif i > w["cell"]:
            out.append("idle")
        else:
            out.append({"running": "now", "waiting": "waiting", "human": "human", "stopped": "stopped", "done": "done"}[w["cell_state"]])
    return out


def far_text(w, logs, rows):
    """D-659 ③: the cell an earlier iteration reached past where the wheel stands now ('가장 멀리 간 칸(⑥ 권고 버림)'), else None — the
    list row's last words are the last iteration's only (A-24: '끝 · 바꿀 노드 없음' after ⑥ recommended 버림)."""
    top = max([lg.get("cell") or 0 for lg in logs or []] or [0])
    if top <= (w.get("cell") or 0):
        return None
    rec = [r["recommendation"] for r in rows or [] if r.get("recommendation")]
    if top >= 6 and rec:
        return f"가장 멀리 간 칸(⑥ 권고 {VERDICT_WORDS.get(rec[-1].get('decision'), rec[-1].get('decision'))})"
    return f"가장 멀리 간 칸({CELLS[min(top, 8) - 1]})"


VERDICT_WORDS = {"adopt": "채택", "hold": "보류", "reject": "버림"}   # app.VERDICT_WORD the same


def status_text(w):
    if w.get("outcome"):
        return w["outcome"]
    if (json.loads(w.get("settings") or "{}") or {}).get("_stop"):   # D-476 덧 20: asked, the worker ends it before the next step
        return STOPPING
    hold = (json.loads(w.get("settings") or "{}") or {}).get("_main", {}).get("hold")
    if hold:   # EV-035: the write gate's or a step's reason to wait (회색) or stop (빨강)
        return f"{CELLS[w['cell'] - 1]} {hold}"
    if w["cell"] == 4 and w["cell_state"] == "waiting":
        return f"④ {WAIT_4}"
    return f"{CELLS[w['cell'] - 1]} " + {"running": "중", "waiting": "대기", "human": "사람 차례", "stopped": "멈춤", "done": "끝"}[w["cell_state"]]
