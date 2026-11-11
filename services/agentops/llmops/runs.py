"""Run executor (B-05). For each case of the chosen run input, start llmops/runner.py in the Agent sandbox (B-08),
read its JSON lines, redact (B-08) → store (B-03) → publish (the SSE stream reads the stored events, so the live view
and the record share one source). Case time limit, process-group stop on timeout/cancel, crash isolation per case."""
import json
import os
import queue
import re
import secrets
import signal
import sys
import threading
import time
from pathlib import Path

from packrt import trace as ptrace

from . import evaluation, gateway, guard, packs, tracing
from .keys import project_llm

RUNNER = Path(__file__).with_name("runner.py")
SCREEN = Path(__file__).with_name("screen_child.py")   # D-549 덧 13: the pre-screen, in the Pack's own process
CASE_LIMIT_S = 30  # UA-11
KEY_MISSING_RE = re.compile(r"(?i)(키를 찾을 수 없|api[_\s-]?key.{0,80}(not set|must be set|missing|not found|없)|(not set|missing|not found).{0,40}api[_\s-]?key)")
ADDRESS_MISSING_RE = re.compile(r"주소 환경변수 \S+가 없습니다")   # packrt's ProviderError for a provider address that is nowhere (EV-053 CHG-230)
PACK_ENV_GUIDE = "실제 모드 Pack의 키 · 주소는 Pack 작업 폴더 .env에 둡니다(서버 환경의 키 · 주소는 넘기지 않음)"   # EV-053 CHG-230 (D-353)
KEY_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:API_KEY|KEY|TOKEN))\b")
OUTPUT_KEYS = ("answer", "reply", "output", "out", "response", "result")


def key_missing_summary(text):
    if not text or not KEY_MISSING_RE.search(text):
        return None
    m = KEY_NAME_RE.search(text)
    return "API 키를 찾을 수 없음" + (f" ({m.group(1)})" if m else "")


STOPPED_STATE = "검토 전 멈춤(2차 범위)"   # EV-002 CHG-11: a human_review node is reached in a Run that is not an evaluation suite


def pack_text(value):
    """Final output of a Pack case: the entrypoint output value; an object becomes JSON text (keys sorted)."""
    if value is None:   # e.g. a rejected review ends the graph before the output is made
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def final_text(final, input_field):
    if isinstance(final, dict):
        for k in OUTPUT_KEYS:
            if isinstance(final.get(k), str):
                return final[k]
        rest = {k: v for k, v in final.items() if k != input_field}
        return json.dumps(rest, ensure_ascii=False, default=str)
    return str(final)



def _data_release_id(man):
    """The data bundle of a Pack Run from its execution manifest, by the same rule packrt reports at run start (trace contract 3):
    every store of the execution config, with the release id of its index or table release (None for a store without one).
    A Pack without stores still has one (data_release_id of {}), as packrt records it in trace.run."""
    stores = {k: None for k in ((man.get("execution_config") or {}).get("backends") or {})}
    for part in ("evidence_index_releases", "table_snapshots"):
        stores.update({k: (v or {}).get("release_id") for k, v in (man.get(part) or {}).items()})
    return ptrace.data_release_id(stores)

def run_sections(run, release):
    """D-492 덧 15: the section spans of the corpora a suite Run's vector DB versions came from (its manifest), under the Pack copy's
    release roots — None for a Run without them or a corpus that cannot be read (its fixed-size pieces are then not counted)."""
    man, copy = run.get("manifest") or {}, ((release or {}).get("pack") or {}).get("copy")
    if not man.get("evidence_index_releases") or not copy:
        return None
    try:
        roots = packs.config_of(copy).get("release_roots") or []
    except Exception:   # noqa: BLE001 - no readable execution config in the copy: nothing to resolve
        return None
    return packs.run_sections(man, roots)


def judged_by(summary, cases):
    """{llm_provider, model, served_model} of the judge that scored a Run — the scorecard's model reviewer for a suite Run,
    else the first case LLM score; None when nothing was judged ('가짜' names in fake mode, EV-036)."""
    mj = ((summary or {}).get("scorecard") or {}).get("model_judge") or {}
    src = mj if mj.get("model") else next((c["llm"] for c in cases if isinstance(c.get("llm"), dict) and c["llm"].get("model")), None)
    return {k: src.get(k) for k in ("llm_provider", "model", "served_model")} if src else None




def _path_get(value, path):
    cur = value
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _path_set(value, path, v):
    parts = path.split(".")
    cur = value
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = v


def _path_drop(value, path):
    parts = path.split(".")
    cur = value
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def next_input(first_input, then, first_output):
    """EV-079 (D-463 덧 5): a two-step case's second input — a copy of the first input, the 'drop' paths taken out, the 'set' values
    put in and each 'take' path given the value at its path in the first step's output (null when that output has none)."""
    out = json.loads(json.dumps(first_input))
    for p in then.get("drop") or []:
        _path_drop(out, p)
    for p, v in (then.get("set") or {}).items():
        _path_set(out, p, json.loads(json.dumps(v)))
    for p, src in (then.get("take") or {}).items():
        _path_set(out, p, _path_get(first_output, src))
    return out


VISIT_META_KEYS = ("kind", "retrievals", "checks", "expect_checks", "review", "route", "model_release", "probabilities", "alarm", "threshold",
                   "custom_source_hash", "artifact", "error",   # EV-039 0.11.0 predict values (EV-041 CHG-203)
                   "tokens_in", "tokens_out", "model",   # EV-069 CHG-EF-5: a llm node's provider usage (packrt meta) and model
                   "embed_tokens", "embedding_model")   # D-492 덧 9: a retriever node's query embedding tokens and model (packrt embed-meta)


def visit_meta(v, meta):
    """The Pack node's records (CHG-10) a visit keeps from its node meta — the keys above, a None left out, 'error' as node_error."""
    for k in VISIT_META_KEYS:
        if meta.get(k) is not None:
            v["node_error" if k == "error" else k] = meta[k]


class ScreenFailed(Exception):
    """D-549 덧 13: the pre-screen did not finish — a code and where only."""

    def __init__(self, code, where):
        super().__init__(f"{code} · {where}")
        self.code, self.where = code, where


class Executor:
    def __init__(self, settings, store, keys, fake, case_limit_s=None, gw=None, writer=None):
        self.settings, self.store, self.keys, self.fake, self.gw = settings, store, keys, fake, gw
        self.writer = writer   # shared trace writer (EV-019 CHG-93) or None (trace off)
        self.case_limit_s = case_limit_s or settings.case_limit_s
        self.cancels, self.threads, self.pgids = {}, {}, {}
        self.case_ids = {}   # run uid → each case's suite case id (None outside a suite), for evaluation_case (EV-020 CHG-98)
        self.fake_ctx = {}   # run uid → {"revision", "folder", "suite", "releases"} of a suite Run of an Agent Pack release (EV-022 S-10)
        self.rule_packs = {}   # run uid → Pack id of an Agent Pack release Run: the fake rule answers that Pack's Runs only (EV-SUI-01 CHG-S05)

    # ---------------------------------------------------------------- public
    def start(self, project, release, run_input, cases):
        meta = {"rel_name": release["name"], "code": release["code"], "code_hash": release["code_hash"], "graph": release["graph"],
                "graphId": release["graph"]["id"], "input": run_input, "mode": self.settings.mode, "mode_label": self.settings.mode_label,
                "limit": self.case_limit_s, "llmKey": self.keys.status(project_llm(project["scoring"])[0]) != "none", "path": release["path"], "how": release["how"]}
        rows = [{"i": i, "input": c["input"], "expected": c.get("expected"), "forbidden": c.get("forbidden"), "state": "대기",
                 "visits": [], "passed": [], "tools": [], "error": None, "final": None, "ms": None, "tokens": 0,
                 **({"then": c["then"]} if c.get("then") else {})} for i, c in enumerate(cases)]   # EV-079: a two-step case keeps its 'then'
        uid = self.store.create_run(project["id"], release["id"], meta, rows)
        self.case_ids[uid] = [c.get("id") for c in cases]
        self.store.set_last_run_code(release["id"], release["code_hash"])
        self.cancels[uid] = threading.Event()
        t = threading.Thread(target=self._run, args=(uid, release, project), daemon=True)
        self.threads[uid] = t
        t.start()
        return uid

    def cancel(self, uid):
        ev = self.cancels.get(uid)
        if ev:
            ev.set()
        return ev is not None

    def wait(self, uid, timeout=None):
        t = self.threads.get(uid)
        if t:
            t.join(timeout)

    # ---------------------------------------------------------------- internals
    def _publish(self, uid, event):
        self.store.add_event(uid, event)

    def _trace_refused(self, uid, c, t0, e):
        """The trace refused this case's run_started (or the writer is down): nothing runs, the case fails (trace contract 8)."""
        c["state"], c["ms"] = "실패", round((time.monotonic() - t0) * 1000)
        c["error"] = {"node": "기록", "summary": guard.redact(f"실행 기록 실패({e.code}) — {e.message}", self.keys.known_secrets())}
        self.store.update_case(uid, c["i"], c)
        self._publish(uid, {"type": "case_end", "case": c["i"], "state": c["state"], "error": c["error"], "final": c["final"]})

    def _agent_env(self, uid, i, pack=None, folder=None, release=None):
        extra = {}
        if self.settings.mode == "fake":
            base = f"http://127.0.0.1:{self.settings.port}/fake/v1/{uid}/{i}"
            extra = {"OPENAI_BASE_URL": base, "OPENAI_API_BASE": base, "ANTHROPIC_BASE_URL": base}
        if pack is None:
            return guard.agent_env(extra=extra)
        if self.settings.mode != "fake":   # EV-061: a copy release's keys are in its origin's work folder (only real mode reads the chain)
            env_file = (packs.key_folder(self.store, release) if release else Path(pack["work_folder"])) / ".env"
            if env_file.is_file():
                extra["PACKRT_ENV_FILE"] = str(env_file)   # the path only: the runtime reads it, LLMOps never opens it (B-8)
        dsn = packs.runtime_dsn(self.settings.db_env_file) if pack.get("uses_postgres") else None
        self.keys.add_dsn(dsn)   # the value this run hands over (read now, so a changed file is masked too) joins the masked values
        # EV-053 CHG-229: no key-shaped server variable and none of the names this folder's models.yaml gives (the folder the case runs in)
        return packs.pack_env(guard.agent_env(), extra, dsn=dsn, strip=packs.model_env_names(folder or pack.get("copy") or "."))

    def fake_context(self, run, case):
        """EV-022 S-10 · answer 019-04: the Run context the fake provider answers from the demo answers file — only for a suite
        Run whose revision, case and data releases the file covers (llmops/fake_answers.applies); otherwise None (old fake)."""
        from . import fake_answers
        f = self.fake_ctx.get(run)
        if not f:
            return None
        try:
            i = int(case)
        except (TypeError, ValueError):
            return None
        ids = self.case_ids.get(run) or []
        cid = ids[i] if 0 <= i < len(ids) else None
        # a set answers file that cannot be read is not passed over silently (review E-S-10-R1-02): FakeAnswersError reaches the fake
        # provider, which refuses the call with the reason (the case fails and says why)
        if not fake_answers.applies(f["revision"], cid, f["releases"], f.get("suite")):
            return None
        return {"revision": f["revision"], "case_id": cid, "pack_dir": f["folder"]}

    def fake_rule_pack(self, run):
        """EV-SUI-01 CHG-S05: the Pack id of a running Agent Pack release Run (the fake rule's applies-to check), else None."""
        return self.rule_packs.get(run)

    def _run(self, uid, release, project):
        run = self.store.run(uid)
        cancel = self.cancels[uid]
        pack = release.get("pack")
        folder = release["path"] if not pack else pack["copy"]
        if pack:
            self.rule_packs[uid] = pack.get("id")
        if pack and (run.get("input") or {}).get("kind") == evaluation.SUITE:   # EV-022 S-10: what the demo answers file is checked against
            stores = (pack.get("bindings") or {}).get("stores") or {}
            su = self.store.suite(run["input"]["id"]) or {}
            self.fake_ctx[uid] = {"revision": pack["revision"], "folder": str(folder), "suite": su.get("folder"),
                                  "releases": {k: (v or {}).get("release_id") for k, v in stores.items()}}
        entry = Path(folder) / release["graph"]["entry"]
        write_deny = [self.settings.data_dir, folder] if not pack else packs.write_deny(self.settings.data_dir, pack)
        graph_edges = {(e["source"], e["target"]) for e in release["graph"]["edges"]}
        for c in run["cases"]:
            if cancel.is_set():
                break
            self._case(uid, c, entry, folder, write_deny, release["graph"]["input_field"], graph_edges, cancel, pack=pack, review=run["input"].get("review"),
                       release=release)
            if c.get("then") and c.get("state") == "완료" and not cancel.is_set():   # EV-079 (D-463 덧 5): the second step of a two-step case
                from . import suites
                first = {k: c.get(k) for k in ("input", "final", "state", "ms", "tokens", "visits", "passed")}
                second = next_input(c["input"], c["then"], suites._final_value(c))
                c.update(steps=[first], input=second, visits=[], passed=[], tools=[], final=None, state="대기", error=None)
                self._case(uid, c, entry, folder, write_deny, release["graph"]["input_field"], graph_edges, cancel, pack=pack, review=run["input"].get("review"),
                           release=release, step=2)
                c["input"] = first["input"]   # the case keeps its own input; the second one is in steps
                c["steps"] = [first, {"input": second}]
                c["ms"] = (c.get("ms") or 0) + (first["ms"] or 0)
                c["tokens"] = None if c.get("tokens") is None or first["tokens"] is None else c["tokens"] + first["tokens"]
                self.store.update_case(uid, c["i"], c)
        cases = self.store.run(uid)["cases"]
        if cancel.is_set():
            for c in cases:
                if c["state"] in ("대기", "실행 중"):
                    c["state"] = "실행 안 됨"
                    self.store.update_case(uid, c["i"], c)
            status, notice = "취소됨", None
        else:
            bad = [c for c in cases if c["state"] in ("실패", "timeout")]
            status = "일부 실패" if bad else "완료"
            notice = "API 키를 찾을 수 없음" if cases and all(c["state"] == "실패" and (c["error"] or {}).get("key_missing") for c in cases) else None
        # scores, diagnoses and the recommendation are computed once here and stored with the Run (P-07): reopening shows the same
        final = {**self.store.run(uid), "status": status}
        suite = self.store.suite(run["input"]["id"]) if run["input"].get("kind") == evaluation.SUITE else None
        if suite is not None and run["input"].get("no_judge"):   # EV-069: an efficiency wheel's Run is scored without the judge
            suite = {**suite, "judge": False}
        proj = self.store.project(project["id"])
        prov, model = project_llm(proj["scoring"])   # the Project's provider and model (EV-005 CHG-28, D-5)
        summary = evaluation.evaluate(proj, final, lambda c: gateway.judge(self.gw, c, prov, model), suite=suite,
                                      graph=release["graph"], node_judge=lambda t, i, o: gateway.judge_node(self.gw, t, i, o, prov, model),
                                      sections=run_sections(final, release) if suite is not None else None)
        who = judged_by(summary, final["cases"])
        if who:   # EV-036 (answer 036-01): which judge provider · model scored this Run, kept with the Run record
            self.store.set_run_meta(uid, "judge", who)
        self.store.finish_run(uid, status, notice, summary=summary, cases=final["cases"])
        self.case_ids.pop(uid, None)
        self.fake_ctx.pop(uid, None)
        self.rule_packs.pop(uid, None)
        self._publish(uid, {"type": "run_end", "status": status, "notice": notice})

    def screen(self, release, cases, combos, timeout, parts=4):
        """D-549 덧 13: every combination's change lines over the cases, in a few Pack processes (screen_child) — the same sandbox · key
        path as a case. → {"rows": [{"i", "cases": [{"state", "final", "first"?, "records"?, "visits"?, "key"}]}], "embed_calls", "embed_tokens", "embed_model"};
        raises ScreenFailed with a code · where only (never a provider's text)."""
        done = [None] * parts
        threads = [threading.Thread(target=lambda k=k: done.__setitem__(k, self._screen_part(release, cases, combos[k::parts], timeout)), daemon=True) for k in range(parts)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids, rows, end = {}, [], {"embed_calls": 0, "embed_tokens": 0, "embed_model": None}
        for k, part in enumerate(done):
            got, vals, last = part or ([], {}, None)
            if not last or last["t"] != "end":
                raise ScreenFailed((last or {}).get("code") or "stopped", (last or {}).get("where") or "run")
            end.update(embed_calls=end["embed_calls"] + (last.get("embed_calls") or 0), embed_tokens=end["embed_tokens"] + (last.get("embed_tokens") or 0),
                       embed_model=end["embed_model"] or last.get("embed_model"))
            at = lambda n: ids.setdefault(json.dumps(vals.get(n), ensure_ascii=False, sort_keys=True, default=str), len(ids))   # noqa: E731
            for r in got:
                for c in r["cases"]:
                    c["key"] = (c["state"], at(c["final"]), at(c["first"]) if "first" in c else None,   # the same finals, records and visits score the same
                                c.get("records"), at(c["visits"]) if "visits" in c else None)
                    c["final"] = vals.get(c["final"])
                    if "first" in c:
                        c["first"] = vals.get(c["first"])
                    if "visits" in c:
                        c["visits"] = vals.get(c["visits"])
                rows.append({**r, "i": r["i"] * parts + k})
        return {"rows": sorted(rows, key=lambda r: r["i"]), **end}

    def _screen_part(self, release, cases, combos, timeout):
        pack, nonce = release["pack"], secrets.token_hex(16)
        folder = pack["copy"]
        env = self._agent_env("screen", 0, pack, folder, release)
        allow_db = (guard.RUNTIME_DSN,) if pack.get("uses_postgres") else ()
        proc = guard.sandboxed_popen([pack["python"], str(SCREEN), folder], cwd=folder, write_deny=packs.write_deny(self.settings.data_dir, pack), env=env, allow_db=allow_db)
        threading.Thread(target=proc.stderr.read, daemon=True).start()
        timer = threading.Timer(timeout, lambda: os.killpg(proc.pid, signal.SIGKILL))
        timer.start()
        vals, rows, last = {}, [], None
        try:
            proc.stdin.write(json.dumps({"nonce": nonce, "expect_revision": pack["revision"], "cases": cases, "combos": combos}, ensure_ascii=False))
            proc.stdin.close()
            for line in proc.stdout:
                if not line.startswith(nonce + " "):
                    continue
                ev = json.loads(line[len(nonce) + 1:])
                if ev["t"] == "val":
                    vals[ev["n"]] = ev["v"]
                elif ev["t"] == "row":
                    rows.append(ev)
                else:
                    last = ev
        except (BrokenPipeError, ValueError):
            pass
        finally:
            timer.cancel()
            proc.wait()
        return rows, vals, last

    def _case(self, uid, c, entry, folder, write_deny, input_field, graph_edges, cancel, pack=None, review=None, release=None, step=1):
        i, secret_values = c["i"], self.keys.known_secrets()
        c["state"] = "실행 중"
        self.store.update_case(uid, i, c)
        self._publish(uid, {"type": "case_start", "case": i})
        nonce = secrets.token_hex(16)  # only lines carrying this case's token are runner events
        measured = self.settings.mode == "fake"
        t0 = time.monotonic()
        if pack:   # the Pack's own Python runs the packrt runner on the copy (CHG-07, CHG-09)
            cmd = [pack["python"], "-m", "packrt.runner", folder]
            payload = {"nonce": nonce, "input": c["input"], "expect_revision": pack["revision"],
                       "review": (review or {}).get("case_policy", {}).get(str(i)) if review else {"policy": "stop_before"}}
        else:
            cmd = [sys.executable, str(RUNNER), str(entry), input_field]
            payload = {"nonce": nonce, "input": c["input"]}
        ct = None
        if self.writer is not None:   # EV-019 CHG-93: this case is one trace run; a record failure is a case failure
            try:
                ct = tracing.CaseTrace(self.writer, uid, release, pack)
                ct.start(c["input"])
            except tracing.CaseTraceError as e:
                return self._trace_refused(uid, c, t0, e)
            ids = self.case_ids.get(uid) or []   # evaluation case ↔ trace run, one append-only row per traced case (EV-020 CHG-98)
            if step == 1:   # EV-079: a two-step case keeps its first step's row (one row per case)
                self.store.add_evaluation_case(uid, i, ids[i] if i < len(ids) else None, ct.ctx["run_id"])
            if pack:
                payload["trace"] = ct.ctx
        allow_db = (guard.RUNTIME_DSN,) if pack and pack.get("uses_postgres") else ()   # EV-020 CHG-97: one name, PostgreSQL configs only
        env = self._agent_env(uid, i, pack, folder, release)
        secret_values = self.keys.known_secrets()   # again after _agent_env: a runtime DSN this case hands over is masked in its lines too
        try:
            proc = guard.sandboxed_popen(cmd, cwd=folder, write_deny=write_deny, env=env, allow_db=allow_db)
        except guard.IsolationRefused as e:   # EV-VPS-01 CHG-V03: nothing started; the case fails with the reason
            c["state"], c["ms"] = "실패", round((time.monotonic() - t0) * 1000)
            c["error"] = {"node": "격리", "summary": str(e)}
            if ct is not None:
                ct.close(None, str(e))   # the trace run opened for this case is closed as failed with the reason
            self.store.update_case(uid, i, c)
            self._publish(uid, {"type": "case_end", "case": i, "state": c["state"], "error": c["error"], "final": c["final"]})
            return
        self.pgids.setdefault(uid, []).append(proc.pid)  # own session: pgid = pid
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)], daemon=True).start()
        stderr = []
        threading.Thread(target=lambda: stderr.extend(proc.stderr.readlines()), daemon=True).start()
        ended_by_step, iters, started, node_t0 = {}, {}, {}, {}
        trace_code = None
        outcome, final, error = None, None, None
        deadline = t0 + self.case_limit_s
        while True:
            if cancel.is_set():
                outcome = "cancelled"; break
            if time.monotonic() > deadline:
                outcome = "timeout"; break
            try:
                raw = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                break
            if not raw.startswith(nonce + " "):
                continue  # not a runner event (the runner sends only token-prefixed lines on its private pipe)
            line = guard.redact(raw[len(nonce) + 1:].strip(), secret_values)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("t")
            if kind in ("trace_payload", "trace_event"):   # the Pack runtime's trace events (EV-019): relayed, not part of the case record
                if ct is not None:
                    try:
                        ct.pack_line(ev)
                    except tracing.CaseTraceError as e:
                        outcome, error = "trace_error", {"node": "기록", "summary": f"실행 기록 실패({e.code}) — {e.message}"}
                        trace_code = e.code
                        break
                continue
            if ct is not None and not pack:
                try:
                    ct.legacy_line(ev)
                except tracing.CaseTraceError as e:
                    outcome, error = "trace_error", {"node": "기록", "summary": f"실행 기록 실패({e.code}) — {e.message}"}
                    trace_code = e.code
                    break
            if kind == "manifest":   # execution manifest reported by the Pack runtime (CHG-04, B-5)
                man = {k: v for k, v in ev.items() if k != "t"}
                run_in = self.store.run(uid)["input"]
                if run_in.get("kind") == evaluation.SUITE:   # the evaluation part of the execution manifest (spec 3.2)
                    man["evaluation"] = {"suite_id": run_in["id"], "suite_revision": run_in.get("suite_revision"), "dataset_hash": run_in.get("dataset_hash")}
                c["manifest_revision"] = man.get("pack_revision")
                if i == 0 or not self.store.run(uid).get("manifest"):
                    self.store.set_run_meta(uid, "manifest", man)
                    self.store.set_run_meta(uid, "data_release_id", _data_release_id(man))   # for the evaluation_run view (EV-020 CHG-98)
                continue
            if kind == "node_start":
                node, step = ev["node"], ev.get("step") or 0
                iters[node] = iters.get(node, 0) + 1
                started[ev.get("task")] = (node, iters[node])
                for prev in ended_by_step.get(step - 1, []):
                    if (prev, node) in graph_edges:
                        e = f"{prev}>{node}"
                        if e not in c["passed"]:
                            c["passed"].append(e)
                        self._publish(uid, {"type": "edge", "case": i, "source": prev, "target": node})
                u0 = self.fake.usage.get((uid, str(i)))
                node_t0[ev.get("task")] = (ev.get("ts"), (u0["prompt_tokens"] + u0["completion_tokens"]) if u0 else 0)
                c["visits"].append({"node": node, "iter": iters[node], "input": ev.get("input"), "output": None, "status": "live", "ms": None, "tokens": None})
                self.store.update_case(uid, i, c)
                self._publish(uid, {"type": "node_start", "case": i, "node": node, "iter": iters[node], "input": ev.get("input")})
            elif kind == "node_end":
                node, step = ev["node"], ev.get("step") or 0
                it = started.get(ev.get("task"), (node, iters.get(node, 1)))[1]
                status = "fail" if ev.get("error") else "done"
                ts0, tok0 = node_t0.get(ev.get("task"), (ev.get("ts"), 0))
                u1 = self.fake.usage.get((uid, str(i)))
                tok1 = (u1["prompt_tokens"] + u1["completion_tokens"]) if u1 else 0
                self.fake.claim_limits(uid, str(i), node, ts0, ev.get("ts"))   # EV-SUI-01 CHG-S05: fake-rule limits of calls made while it ran
                meta = ev.get("meta") or {}
                for v in reversed(c["visits"]):
                    if v["node"] == node and v["iter"] == it:
                        v.update(output=ev.get("output"), status=status, ms=round(((ev.get("ts") or ts0) - ts0) * 1000), tokens=tok1 - tok0 if measured else None)
                        visit_meta(v, meta)
                        break
                if isinstance(ev.get("tools"), list):
                    c["tools"] = ev["tools"]  # the case's tool calls so far (name, args, result), redacted with the line
                if status == "done":
                    ended_by_step.setdefault(step, []).append(node)
                else:
                    error = {"node": node, "summary": ev["error"][:500]}
                self.store.update_case(uid, i, c)
                self._publish(uid, {"type": "node_end", "case": i, "node": node, "iter": it, "output": ev.get("output"), "status": status, "error": ev.get("error")})
            elif kind == "case_end":
                outcome, final = ("stopped" if ev.get("stopped") else "ok"), ev.get("final")
                if ev.get("stopped"):
                    c["stopped"] = ev["stopped"]
                break  # the runner's last line: do not wait for children that keep stdout open
            elif kind == "case_error":
                outcome = "error"
                running = next((v["node"] for v in reversed(c["visits"]) if v["status"] == "live"), None)
                where = {"input": "입력", "revision": "revision"}.get(ev.get("stage"), "—" if ev.get("stage") == "run" else "불러오기")
                error = error or {"node": running or where, "summary": ev.get("error", "")[:500]}
                break
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # after every case: the Agent and anything it started (incl. background children)
        except (ProcessLookupError, PermissionError):
            pass
        rc = proc.wait()
        time.sleep(0.05)
        running = next((v["node"] for v in reversed(c["visits"]) if v["status"] == "live"), None)
        if running:   # EV-SUI-01 CHG-S05: a node that never ended (a refused call fails it) takes the calls not yet given
            self.fake.claim_limits(uid, str(i), running)
        for v in c["visits"]:
            if v["status"] == "live":  # the node that was running ends here, live on the screen too (AC-30-C4)
                v["status"] = "timeout" if outcome == "timeout" else ("cancelled" if outcome == "cancelled" else ("stopped" if outcome == "stopped" else "fail"))
                self.store.update_case(uid, i, c)
                self._publish(uid, {"type": "node_end", "case": i, "node": v["node"], "iter": v["iter"], "output": None, "status": v["status"], "error": None})
        if ct is not None:   # a case that ended without run_ended gets one (timeout, cancel, crash, refused line)
            ct.close(outcome, (error or {}).get("summary"), trace_code)
        c["ms"] = round((time.monotonic() - t0) * 1000)
        usage = self.fake.usage.get((uid, str(i)))
        # tokens are measured only on the fake provider the Agent calls through the product; in real mode the Agent calls its
        # provider directly, so the count is unknown (None, shown as no value) rather than 0
        c["tokens"] = (usage["prompt_tokens"] + usage["completion_tokens"]) if usage else (0 if measured else None)
        if c["tokens"] is None:   # EV-069 CHG-EF-5 (A3-N3 · PRES-EF-14): real mode — packrt's llm usage when every llm visit has it
            llm = [v for v in c["visits"] if str(v.get("kind") or "").startswith("llm")]
            if llm and all(v.get("tokens_in") is not None for v in llm):
                c["tokens"] = sum((v.get("tokens_in") or 0) + (v.get("tokens_out") or 0) for v in llm)
        limits = self.fake.take_limits(uid, str(i))
        if limits:   # EV-SUI-01 CHG-S05: the fake answers' limits of this case (only when there are some)
            c["fake_limits"] = limits
        if outcome == "ok":
            c["state"], c["final"] = "완료", guard.redact(pack_text(final) if pack else final_text(final, input_field), secret_values)
        elif outcome == "stopped":
            c["state"], c["final"] = STOPPED_STATE, guard.redact(pack_text(final), secret_values)
        elif outcome == "timeout":
            c["state"], c["error"] = "timeout", {"node": running or "—", "summary": f"case 실행 시간이 제한 {self.case_limit_s}초를 넘음"}
        elif outcome == "cancelled":
            c["state"] = "실행 안 됨"
        else:
            if not error:
                tail = guard.redact("".join(stderr)[-400:].strip(), secret_values)
                error = {"node": running or "—", "summary": f"Agent 프로세스가 비정상 종료됨 (종료 코드 {rc})" + (f" — {tail}" if tail else "")}
            km = key_missing_summary(error["summary"])
            if km:
                error = {**error, "summary": f"{km} — {error['summary']}", "key_missing": True}
            if pack and self.settings.mode != "fake" and (km or ADDRESS_MISSING_RE.search(error["summary"])):   # EV-053 CHG-230: key_missing stays as it was
                error = {**error, "summary": f"{PACK_ENV_GUIDE} — {error['summary']}"}
            c["state"], c["error"] = "실패", {**error, "summary": guard.redact(error["summary"], secret_values)}
        self.store.update_case(uid, i, c)
        self._publish(uid, {"type": "case_end", "case": i, "state": c["state"], "error": c["error"], "final": c["final"]})
