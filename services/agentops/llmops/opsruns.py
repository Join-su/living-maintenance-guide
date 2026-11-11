"""EV-025 operational Runs (manual p.30, CHG-122 · 127 · 128): the deploy side's run records, read only.

An operational Run is another source of the existing Run, not a new object (impact map ③): it is read from the shared trace
(logDB, schema trace) every time it is shown and never copied into llmops' own tables. The trace is read with the
read-only account llmops_app (LLMOPS_APP_DSN in the DB env file, only SELECT on trace — migration 0004, answer 021-02); its raw values
are the redacted payload files the deploy side wrote (LLMOPS_OPS_PAYLOAD_ROOT, read only, sha-checked). llmops never
writes the trace, the payload folder or the deploy repository (PRES-128).

Two backends answer the same questions: PostgreSQL (the product) and a recorded-rows file (test harness only,
LLMOPS_FAKE_OPS read inside isolated-env runs, the same rows the trace tables hold). Everything above the backend — the
rows a screen shows — is the same code for both.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import node_detail

APP_DSN_ENV = "LLMOPS_APP_DSN"
PAGE = 50
MAX_PAYLOAD = 5 * 1024 * 1024        # the trace writer's own limit for one raw value


class OpsUnavailable(Exception):
    """The operational records cannot be read now: no_dsn | unreachable | denied | broken. Never carries the DSN."""
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


REASON = {"no_dsn": "연결 설정 없음", "unreachable": "logDB에 연결할 수 없음", "denied": "읽기 권한 없음", "broken": "기록을 읽다 멈춤"}


def app_dsn(db_env_file):
    """The read account's DSN (LLMOPS_APP_DSN) — this one line only, from the DB env file. Never printed or stored."""
    try:
        for line in Path(db_env_file).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(APP_DSN_ENV + "="):
                return line.split("=", 1)[1].strip() or None
    except (OSError, TypeError):
        return None
    return None


def _ts(v):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _iso(v):
    v = _ts(v)
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if v else None


# ---------------------------------------------------------------- backends: the trace rows, nothing interpreted

class PgTrace:
    """trace.* through llmops_app (SELECT only). Each call opens and closes its own connection."""
    def __init__(self, dsn):
        self.dsn = dsn

    def _rows(self, sql, args):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise OpsUnavailable("broken")
        try:
            with psycopg.connect(self.dsn, connect_timeout=3, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(sql, args)
                    return cur.fetchall()
        except psycopg.errors.InsufficientPrivilege:
            raise OpsUnavailable("denied") from None
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise OpsUnavailable("unreachable") from None
        except psycopg.Error:
            raise OpsUnavailable("broken") from None

    def runs(self, deployment_ids, revision, before, limit):
        sql = ("SELECT r.run_id, r.deployment_id, r.pack_id, r.pack_revision, r.data_release_id, r.graph, r.started_at,"
               " s.body AS started, e.body AS ended, e.at AS ended_at"
               " FROM trace.run r"
               " LEFT JOIN LATERAL (SELECT body FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_started' ORDER BY seq LIMIT 1) s ON true"
               " LEFT JOIN LATERAL (SELECT body, at FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_ended' ORDER BY seq DESC LIMIT 1) e ON true"
               " WHERE r.origin = 'deployment' AND r.deployment_id = ANY(%s) AND r.pack_revision = %s"
               " AND (%s::timestamptz IS NULL OR (r.started_at, r.run_id) < (%s::timestamptz, %s))"
               " ORDER BY r.started_at DESC, r.run_id DESC LIMIT %s")
        b_at, b_id = before if before else (None, "")
        return self._rows(sql, (list(deployment_ids), revision, b_at, b_at, b_id, limit))

    def run(self, run_id):
        rows = self._rows("SELECT r.run_id, r.origin, r.deployment_id, r.pack_id, r.pack_revision, r.data_release_id, r.graph, r.started_at,"
                          " s.body AS started, e.body AS ended, e.at AS ended_at FROM trace.run r"
                          " LEFT JOIN LATERAL (SELECT body FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_started' ORDER BY seq LIMIT 1) s ON true"
                          " LEFT JOIN LATERAL (SELECT body, at FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_ended' ORDER BY seq DESC LIMIT 1) e ON true"
                          " WHERE r.run_id = %s", (run_id,))
        return rows[0] if rows else None

    def last_succeeded(self, deployment_ids):
        """EV-054 CHG-232: per deployment id its last completed deployment Run — run_ended status succeeded (tracewriter 0.1.3), by
        recorded_at (the database clock) — with the run_started manifest's model releases."""
        return self._rows("SELECT DISTINCT ON (r.deployment_id) r.run_id, r.deployment_id, r.pack_revision, r.data_release_id, r.recorded_at,"
                          " s.body->'manifest'->'model_releases' AS model_releases FROM trace.run r"
                          " JOIN LATERAL (SELECT body FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_ended' ORDER BY seq DESC LIMIT 1) e ON true"
                          " LEFT JOIN LATERAL (SELECT body FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_started' ORDER BY seq LIMIT 1) s ON true"
                          " WHERE r.origin = 'deployment' AND r.deployment_id = ANY(%s) AND e.body->>'status' = 'succeeded'"
                          " ORDER BY r.deployment_id, r.recorded_at DESC, r.run_id DESC", (list(deployment_ids),))

    def node_events(self, run_id):
        return self._rows("SELECT * FROM trace.node_event WHERE run_id = %s ORDER BY seq", (run_id,))

    def event_body(self, run_id, seq):
        """One event's body (EV-041 CHG-204: a predict node's node_ended meta lives only in trace.event, not in the node_event row)."""
        rows = self._rows("SELECT body FROM trace.event WHERE run_id = %s AND seq = %s", (run_id, seq))
        return rows[0]["body"] if rows else None

    def reviews(self, run_id):
        return self._rows("SELECT * FROM trace.review WHERE run_id = %s ORDER BY seq", (run_id,))

    def check_records(self):
        """EV-065 CHG-262: the person-check record lines of trace.feedback (kind 'check_record', deploy answer 160 · DDL 0012): record_key ·
        session_id · signals (the signal ids) · at · edited_value_ref (the document, payload role check_record) · run_id, oldest first.
        Before the columns exist (0012 not applied) there are none — the check is a read of the column names, never an error."""
        have = {r["column_name"] for r in self._rows("SELECT column_name FROM information_schema.columns WHERE table_schema = 'trace'"
                                                     " AND table_name = 'feedback'", ())}
        if not set(CHECK_RECORD_COLUMNS) <= have:
            return []
        return self._rows("SELECT f.record_key, f.session_id, f.signals, f.at, f.edited_value_ref, f.run_id FROM trace.feedback f"
                          " WHERE f.kind = 'check_record' ORDER BY f.at", ())

    def expired(self, shas):
        if not shas:
            return set()
        return {r["sha256"] for r in self._rows("SELECT DISTINCT sha256 FROM trace.payload_expiration WHERE sha256 = ANY(%s)", (list(shas),))}


CHECK_RECORD_COLUMNS = ("kind", "record_key", "session_id", "signals")   # EV-065: trace.feedback columns of the check-record line (DDL 0012, deploy)


class FixtureTrace:
    """Test harness only (LLMOPS_FAKE_OPS): a JSON file of the same rows the trace tables hold —
    {"run": [...], "event": [...], "node_event": [...], "review": [...], "payload_expiration": [...]}."""
    def __init__(self, path):
        try:
            self.d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise OpsUnavailable("broken") from None

    def _with_ends(self, r):
        evs = [e for e in self.d.get("event", []) if e["run_id"] == r["run_id"]]
        s = next((e for e in sorted(evs, key=lambda e: e["seq"]) if e["type"] == "run_started"), None)
        e = next((e for e in sorted(evs, key=lambda e: -e["seq"]) if e["type"] == "run_ended"), None)
        return {**r, "started_at": _ts(r["started_at"]), "started": s and s["body"], "ended": e and e["body"], "ended_at": e and _ts(e["at"])}

    def last_succeeded(self, deployment_ids):
        """The same answer as PgTrace.last_succeeded from the fixture's run · event rows."""
        out = {}
        for r in self.d.get("run", []):
            if r.get("origin") != "deployment" or r.get("deployment_id") not in set(deployment_ids):
                continue
            x = self._with_ends(r)
            if (x.get("ended") or {}).get("status") != "succeeded":
                continue
            at = _ts(r.get("recorded_at") or r["started_at"])
            cur = out.get(r["deployment_id"])
            if cur is None or (at, r["run_id"]) > (cur["recorded_at"], cur["run_id"]):
                out[r["deployment_id"]] = {"run_id": r["run_id"], "deployment_id": r["deployment_id"], "pack_revision": r["pack_revision"],
                                           "data_release_id": r.get("data_release_id"), "recorded_at": at,
                                           "model_releases": ((x.get("started") or {}).get("manifest") or {}).get("model_releases")}
        return list(out.values())

    def runs(self, deployment_ids, revision, before, limit):
        rows = [self._with_ends(r) for r in self.d.get("run", [])
                if r.get("origin") == "deployment" and r.get("deployment_id") in set(deployment_ids) and r.get("pack_revision") == revision]
        rows.sort(key=lambda r: (r["started_at"], r["run_id"]), reverse=True)
        if before:
            b_at, b_id = _ts(before[0]), before[1]
            rows = [r for r in rows if (r["started_at"], r["run_id"]) < (b_at, b_id)]
        return rows[:limit]

    def run(self, run_id):
        r = next((r for r in self.d.get("run", []) if r["run_id"] == run_id), None)
        return self._with_ends(r) if r else None

    def node_events(self, run_id):
        return sorted([{**x, "started_at": _ts(x.get("started_at")), "ended_at": _ts(x.get("ended_at"))} for x in self.d.get("node_event", []) if x["run_id"] == run_id],
                      key=lambda x: x["seq"])

    def event_body(self, run_id, seq):
        return next((x.get("body") for x in self.d.get("event", []) if x["run_id"] == run_id and x["seq"] == seq), None)

    def reviews(self, run_id):
        return sorted([x for x in self.d.get("review", []) if x["run_id"] == run_id], key=lambda x: x["seq"])

    def expired(self, shas):
        return {x["sha256"] for x in self.d.get("payload_expiration", []) if x["sha256"] in set(shas)}

    def check_records(self):
        """EV-065 CHG-262: the person-check records the fixture holds — the trace.feedback lines of kind check_record (DDL 0012 shape, the
        document under payloads/) and, for the tests of the steps after, records already in the read shape ('check_records')."""
        return [r for r in self.d.get("feedback") or [] if r.get("kind") == "check_record"] + list(self.d.get("check_records") or [])


# ---------------------------------------------------------------- raw values (payload files)

def read_payload(root, ref, expired=frozenset()):
    """(value, None) or (None, reason) for one 'sha256:<hex>' ref under the payload root: the file <root>/<hex[:2]>/<hex>
    whose bytes must hash to the ref. Reasons: 설정 없음 · 만료 · 파일 없음 · hash 다름 · 너무 큼 · 형식 다름."""
    if not ref:
        return None, "기록 없음"
    hexd = ref.split(":", 1)[1] if isinstance(ref, str) and ref.startswith("sha256:") else ""
    if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
        return None, "형식 다름"
    if hexd in expired or ref in expired:
        return None, "만료"
    if not root:
        return None, "설정 없음"
    base = Path(root).resolve()
    f = (base / hexd[:2] / hexd).resolve()
    if base not in f.parents:
        return None, "형식 다름"
    try:
        if f.stat().st_size > MAX_PAYLOAD:
            return None, "너무 큼"
        data = f.read_bytes()
    except OSError:
        return None, "파일 없음"
    if hashlib.sha256(data).hexdigest() != hexd:
        return None, "hash 다름"
    try:
        return json.loads(data), None
    except ValueError:
        return None, "형식 다름"


def payload_source(root):
    """A short name of where the raw values are read from (p.30 '원문 = 공유 볼륨 payloads'), never a full path."""
    if not root:
        return "설정 없음"
    p = Path(root)
    return "공유 볼륨 payloads" if p.name == "payloads" and p.parent == Path("/var/lib") else f"{p.parent.name}/{p.name}"


def input_label(value):
    """The 입력 cell of an operational Run (p.30 'EVT-03530'): an event_id (or id) within two levels of the input."""
    if isinstance(value, dict):
        for key in ("event_id", "id"):
            if isinstance(value.get(key), str):
                return value[key]
        for v in value.values():
            if isinstance(v, dict):
                for key in ("event_id", "id"):
                    if isinstance(v.get(key), str):
                        return v[key]
    return None


def _ref(v):
    return v.get("ref") if isinstance(v, dict) else v


def result_label(ended):
    """The 결과 cell from the run's end record (run_ended): failed → '실패 · <code>', otherwise the outcome."""
    if not ended:
        return "실행 중 또는 끝 기록 없음"
    if ended.get("status") != "succeeded":
        err = ended.get("error") or {}
        return f"실패 · {err.get('code') or ended.get('status')}"
    return {"completed": "완료", "rejected_by_review": "검토에서 거절", "stopped_before_review": "검토 전 멈춤"}.get(ended.get("outcome"), ended.get("outcome") or "완료")


def input_spec(raw):
    """EV-075 F-06 (D-446 덧 9, lead answer 045 (가)): the label file's optional 'input_summary' — {"state": <input state key>,
    "fields": [<field or dotted path>], "names": {<field>: {<value>: <word>}}} — the alarm cell of an operational Run; None for any
    other shape (the cell then stays '—')."""
    if not isinstance(raw, dict):
        return None
    st, fs, names = raw.get("state"), raw.get("fields"), raw.get("names", {})
    if not (isinstance(st, str) and st and isinstance(fs, list) and fs and all(isinstance(f, str) and f for f in fs) and isinstance(names, dict)):
        return None
    return {"state": st, "fields": list(dict.fromkeys(fs)), "names": {k: v for k, v in names.items() if isinstance(v, dict)}}


def input_summary(value, spec):
    """'<word> · <word> …' of one Run input: the named fields present (under the state key, or the input itself when it has none),
    each value through the label file's names, else as it is; None when nothing is present — nothing is made up."""
    if not spec or not isinstance(value, dict):
        return None
    s = value.get(spec["state"]) if isinstance(value.get(spec["state"]), dict) else value
    words = []
    for f in spec["fields"]:
        v = s
        for part in f.split("."):
            v = v.get(part) if isinstance(v, dict) else None
        if isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != "":
            name = (spec["names"].get(f.split(".")[-1]) or spec["names"].get(f) or {}).get(str(v))
            words.append(f"{v} {name}" if name and f.split(".")[-1] == "group" else name or str(v))
    return " · ".join(words) or None


def summary_spec(raw, graph):
    """EV-025 OPS-1b (amendment A1, answer 021-03): the label file's optional 'result_summary' — {"state": <state key>, "fields":
    [<field names>]} — with the nodes of this version's graph that write that state; None for any other shape or when no node
    writes it (the 결과 cell then keeps the run_ended value)."""
    if not isinstance(raw, dict):
        return None
    st, fs = raw.get("state"), raw.get("fields")
    if not (isinstance(st, str) and st and isinstance(fs, list) and fs and all(isinstance(f, str) and f for f in fs)):
        return None
    writers = {n.get("name") for n in (graph or {}).get("nodes") or [] if st in (n.get("writes") or [])}
    return {"state": st, "fields": list(dict.fromkeys(fs)), "writers": writers} if writers else None


def summary_values(value, spec):
    """The named fields of the state in one node output, only those present ('있는 칸만'); None when the output has no such state,
    a field has another shape (not text or a number) or none is present — nothing is made up."""
    s = value.get(spec["state"]) if isinstance(value, dict) else None
    if not isinstance(s, dict):
        return None
    got = {}
    for f in spec["fields"]:
        v = s.get(f)
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            return None
        got[f] = v
    return got or None


def _path(value, state, path):
    """A dotted path under the state key (or the value itself when it has no such key — input_summary's way); None when missing."""
    v = value.get(state) if isinstance(value, dict) and isinstance(value.get(state), dict) else value
    for part in path.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def _word(v):
    return None if v is None or isinstance(v, (bool, dict, list)) else str(v)


def review_spec(raw, graph):
    """EV-077 (D-454): the label file's optional 'review_target' — {"any": [{"<state>.<dotted path>": [values]}]}: a Run is a review
    target when any condition is met (every key of it matches one of its values, compared as text). A state some node of this
    version writes is read from that node's last output, any other state from the Run input. None for any other shape (then every
    Run is a target, as before)."""
    conds = raw.get("any") if isinstance(raw, dict) else None
    if not isinstance(conds, list) or not conds:
        return None
    out = []
    for c in conds:
        if not isinstance(c, dict) or not c:
            return None
        for k, vs in c.items():
            if not (isinstance(k, str) and "." in k and k.split(".", 1)[0] and k.split(".", 1)[1] and isinstance(vs, list) and vs
                    and all(_word(x) is not None for x in vs)):
                return None
        out.append({k: {_word(x) for x in vs} for k, vs in c.items()})
    states = {k.split(".", 1)[0] for c in out for k in c}
    nodes = (graph or {}).get("nodes") or []
    writers = {st: {n.get("name") for n in nodes if st in (n.get("writes") or [])} for st in states}
    return {"any": out, "writers": {st: w for st, w in writers.items() if w}}


def is_target(value, outputs, spec):
    """EV-077: whether one Run is a review target — value = its input, outputs = {written state: that state's last output value or
    None}. True without a spec, and whenever a value a condition needs cannot be read (nothing is hidden for being unreadable)."""
    if not spec:
        return True
    if not isinstance(value, dict):
        return True
    for c in spec["any"]:
        ok = True
        for k, allowed in c.items():
            st, path = k.split(".", 1)
            if st in spec["writers"]:
                out = outputs.get(st)
                if not isinstance(out, dict):
                    return True
                got = _word(_path(out, st, path))
            else:
                got = _word(_path(value, st, path))
            if got not in allowed:
                ok = False
                break
        if ok:
            return True
    return False


def kind_spec(raw):
    """EV-077 (D-454 덧): the label file's optional 'run_kind' — "<input state>.<dotted path>", the kind the ratio compares by."""
    return raw if isinstance(raw, str) and "." in raw and all(raw.split(".", 1)) else None


TRIAL = "trial"   # D-476 덧 9: the deploy server's X-Run-Purpose for an app's trial Run (never a review target)


def run_purpose(r):
    """D-476 덧 12: a Run's purpose as the deploy server keeps X-Run-Purpose — the run_started event's body.meta.purpose (deploy 04a47ab);
    None when unmarked (no meta · no purpose)."""
    meta = (r.get("started") or {}).get("meta")
    p = meta.get("purpose") if isinstance(meta, dict) else None
    return p if isinstance(p, str) and p else None


def minutes_spec(label):
    """D-478 #8 (state-labels@1): the label file's three optional keys — check_minutes ("<input state>.<path>": minutes by check),
    chosen_check ("<state>.<path>": the check a Run chose) and check_cause ({check: cause}); None unless all three are there."""
    if not isinstance(label, dict):
        return None
    mins, chosen, cause = kind_spec(label.get("check_minutes")), kind_spec(label.get("chosen_check")), label.get("check_cause")
    if not (mins and chosen and isinstance(cause, dict) and cause and all(isinstance(k, str) and isinstance(v, str) for k, v in cause.items())):
        return None
    return {"minutes": mins, "chosen": chosen, "cause": dict(cause)}


def chosen_spec(label):
    """D-498: the label file's chosen_check alone ("<state>.<path>": the check a Run chose — c1a15 result.decision.section), for the
    check signals' truth; None without it (check_minutes · check_cause are not needed here)."""
    return kind_spec(label.get("chosen_check")) if isinstance(label, dict) else None


def exception_spec(label):
    """D-546 덧 23 ② (eval exception-label): the label file's exception_check ("<state>.<path>": the exception check a start Run chose —
    e.g. result.exception_guide.section), for a 헛걸음 check's truth on that start Run; None without it."""
    return kind_spec(label.get("exception_check")) if isinstance(label, dict) else None


def group_spec(raw):
    """D-477 덧 4 · 5 (state-labels@1): the label file's optional 'group' — "<input state>.<dotted path>", the 증거 세기 groups; None = one group."""
    return kind_spec(raw)


GROUP_ALL, GROUP_EMPTY = "전체", "(빈 값)"


def group_of(value, spec):
    """The 증거 세기 group of one input: GROUP_ALL without a spec, the value at the path, GROUP_EMPTY when it is empty there."""
    if not spec:
        return GROUP_ALL
    got = kind_of(value, spec)
    return got if got not in (None, "") else GROUP_EMPTY


def kind_of(value, spec):
    """The kind of one Run input (or one suite case input) by run_kind; None when it cannot be read."""
    if not spec or not isinstance(value, dict):
        return None
    st, path = spec.split(".", 1)
    return _word(_path(value, st, path))


# ---------------------------------------------------------------- what the screen reads

OFFER_HOW = ("switched", "followed")   # D-633 (app 001-zzz114): not picked — another check by [점검 바꾸기] · went the recommended check


def exception_offer(doc):
    """D-633 (app 001-zzz114): the record's optional exception_offer — an exception recommendation the person saw and whether they
    picked it — as {relation_id, run_id, adopted, picked_section, how}; None when the record has none or it cannot be read. A field off
    its type reads as None (the record still counts)."""
    x = (doc or {}).get("exception_offer") if isinstance(doc, dict) else None
    if not isinstance(x, dict):
        return None
    text = lambda v: v if isinstance(v, str) and v.strip() else None
    return {"relation_id": text(x.get("relation_id")), "run_id": text(x.get("run_id")),
            "adopted": x.get("adopted") if isinstance(x.get("adopted"), bool) else None,
            "picked_section": text(x.get("picked_section")), "how": x.get("how") if x.get("how") in OFFER_HOW else None}


def check_record(row, doc):
    """EV-065 CHG-262 (deploy answer 160): one check_record line and its document (c1a15.check-record.v1, None when it cannot be read)
    in the read shape {record_key, session_id, recorded_at, runs, start, judge, signals: [{kind, run_id, node}], signal_ids, outcome,
    field_truth}. The line's signal ids say which signals count; their target Runs are the document's signals[].runs; node is never in the
    document (None). A line with signal ids whose document cannot be read names no Run — and is not a solved session (signal_ids).
    start · judge (D-525): the session's start Run (None) and judge Runs as the document names them."""
    doc = doc if isinstance(doc, dict) else {}
    doc = doc["record"] if isinstance(doc.get("record"), dict) and "signals" not in doc else doc   # the feedback body's own wrapper
    ids = [s for s in (row.get("signals") or []) if isinstance(s, str)]
    sigs = [{"kind": s["id"], "run_id": r, "node": None} for s in doc.get("signals") or [] if isinstance(s, dict) and s.get("id") in ids
            for r in s.get("runs") or [] if isinstance(r, str)]
    runs = doc.get("runs") if isinstance(doc.get("runs"), dict) else {}
    every = [runs.get("start"), *(runs.get("judge") or []), runs.get("report"), row.get("run_id")]
    return {"record_key": row.get("record_key"), "session_id": row.get("session_id"), "recorded_at": str(row.get("at") or ""),
            "runs": list(dict.fromkeys(r for r in every if isinstance(r, str))), "start": runs.get("start") if isinstance(runs.get("start"), str) else None,
            "judge": [r for r in runs.get("judge") or [] if isinstance(r, str)], "signals": sigs, "signal_ids": ids,
            "outcome": doc.get("outcome") or {}, "field_truth": doc.get("field_truth") or {}, "exception_offer": exception_offer(doc)}


class OpsRuns:
    def __init__(self, trace, payload_root):
        self.trace, self.root = trace, payload_root

    def check_records(self):
        """EV-065 CHG-262: the person-check records in the read shape — a trace line's document read from the payload files (sha-checked,
        expiry respected); a record already in that shape (test fixtures) as it is."""
        out = []
        for row in self.trace.check_records():
            if "edited_value_ref" not in row:
                out.append(row)
                continue
            ref = row.get("edited_value_ref")
            expired = self.trace.expired([ref.split(":", 1)[1]]) if isinstance(ref, str) and ":" in ref else set()
            doc, _ = read_payload(self.root, ref, expired)
            out.append(check_record(row, doc))
        return out

    def node_values(self, run_id, graph, node, iteration=1):
        """EV-025 OPS-2 (CHG-123 · 124 · 128, manual p.30 ②): one node of one operational Run — its visits with the raw input and
        output read from the payload files (sha-checked, expiry respected), the review rows of that node and its record columns.
        None when the Run is not here; {'visited': False} when the node did not run."""
        d = self.detail(run_id, graph)
        if d is None:
            return None
        vs = [v for v in d["visits"] if v["node"] == node]
        if not vs:
            return {"run": d, "visited": False, "iters": 0}
        it = max(1, min(int(iteration or 1), len(vs)))
        v = vs[it - 1]
        refs = [r for r in (v.get("input_ref"), v.get("output_ref")) if r]
        expired = self.trace.expired([r.split(":", 1)[1] for r in refs if ":" in r])
        inp, in_why = read_payload(self.root, v.get("input_ref"), expired)
        out, out_why = read_payload(self.root, v.get("output_ref"), expired) if v.get("status") != "live" else (None, "아직 끝나지 않음")
        reviews = [{k: r.get(k) for k in ("event", "action", "actor", "actor_kind", "role", "reason", "edited_fields", "at")}
                   for r in self.trace.reviews(run_id) if r.get("node_id") == node]
        for r in reviews:
            r["at"] = _iso(r.get("at"))
        meta = {k: v.get(k) for k in ("kind", "provider", "model", "tokens_in", "tokens_out", "custom_source_hash", "started_at", "ended_at", "ms", "input_ref", "output_ref",
                                      "prompt_ref", "provider_raw_ref")}
        predict = None
        if str(v.get("kind") or "").split("@")[0] == "predict" and v.get("end_seq") is not None:   # EV-041 CHG-204: from the trace record
            body = self.trace.event_body(run_id, v["end_seq"]) or {}
            predict = node_detail.predict_view(body.get("meta"), node_detail.prediction_of(out))
        return {"run": d, "visited": True, "iter": it, "iters": len(vs), "status": v.get("status"), "ms": v.get("ms"),
                "input": inp, "input_missing": in_why, "output": out, "output_missing": out_why, "predict": predict,
                "route": v.get("route"), "checks": v.get("checks"), "expect_checks": v.get("expect_checks"), "error": v.get("error"),
                "reviews": reviews, "meta": meta}

    def summary(self, run_id, ended, spec):
        """(결과 text, result_fields) of one Run: the label file's fields from the last output of a node that writes that state,
        read from its payload (sha-checked, expiry respected); the run_ended value when the Run did not succeed, there is no
        result_summary, or that output is missing, expired or of another shape."""
        generic = (result_label(ended), None)
        if not spec or not ended or ended.get("status") != "succeeded":
            return generic
        outs = [ev for ev in self.trace.node_events(run_id) if ev.get("node_id") in spec["writers"] and ev.get("status") != "started" and ev.get("output_ref")]
        if not outs:
            return generic
        ref = _ref(outs[-1]["output_ref"])
        value, _ = read_payload(self.root, ref, self.trace.expired([ref.split(":", 1)[1]] if ref and ":" in ref else []))
        got = summary_values(value, spec)
        return (" · ".join(str(v) for v in got.values()), got) if got else generic

    def last_outputs(self, run_id, writers):
        """EV-077: {state: the last output value of a node that writes it, or None (missing · expired · another sha)}."""
        out = {}
        if not writers:
            return out
        events = [ev for ev in self.trace.node_events(run_id) if ev.get("status") != "started" and ev.get("output_ref")]
        for st, names in writers.items():
            outs = [ev for ev in events if ev.get("node_id") in names]
            if not outs:
                out[st] = None
                continue
            ref = _ref(outs[-1]["output_ref"])
            out[st], _ = read_payload(self.root, ref, self.trace.expired([ref.split(":", 1)[1]] if ref and ":" in ref else []))
        return out

    def target(self, run_id, value, rspec):
        """EV-077: whether this Run is a review target (True without a spec — read nothing more then)."""
        return True if not rspec else is_target(value, self.last_outputs(run_id, rspec["writers"]), rspec)

    def list(self, deployment_ids, revision, before=None, limit=None, spec=None, ispec=None, rspec=None):
        limit = limit or PAGE
        rows = self.trace.runs(deployment_ids, revision, before, limit + 1)
        more, rows = len(rows) > limit, rows[:limit]
        out = []
        for r in rows:
            started, ended = r.get("started") or {}, r.get("ended") or {}
            in_ref = _ref(started.get("input_ref"))
            value, why = read_payload(self.root, in_ref, self.trace.expired([in_ref.split(":", 1)[1]] if in_ref and ":" in in_ref else []))
            text, fields = self.summary(r["run_id"], r.get("ended"), spec)
            out.append({"run_id": r["run_id"], "where": "deployment", "deployment_id": r["deployment_id"], "pack_revision": r["pack_revision"],
                        "data_release_id": r.get("data_release_id"), "started_at": _iso(r["started_at"]), "ended_at": _iso(r.get("ended_at")),
                        "status": ended.get("status"), "outcome": ended.get("outcome"), "result": text, "result_fields": fields,
                        "ms": ended.get("ms"), "input": input_label(value) if value is not None else None,
                        "input_summary": input_summary(value, ispec), "input_ref": in_ref, "input_missing": why,
                        "purpose": run_purpose(r),
                        "target": False if run_purpose(r) == TRIAL else self.target(r["run_id"], value, rspec) if ended.get("status") == "succeeded" else True})
        nxt = f"{out[-1]['started_at']}|{out[-1]['run_id']}" if more and out else None
        return out, nxt

    def run_input(self, run_id):
        """EV-077: one Run's input value (None when missing · expired · another sha) — for the diagnosis's target and kind only."""
        r = self.trace.run(run_id)
        if not r:
            return None
        started = r.get("started") or {}
        in_ref = _ref(started.get("input_ref"))
        value, _ = read_payload(self.root, in_ref, self.trace.expired([in_ref.split(":", 1)[1]] if in_ref and ":" in in_ref else []))
        return value

    def detail(self, run_id, graph, spec=None, ispec=None):
        """One operational Run: its record and the node flow (visits merged from node_started/node_ended rows, the edges
        it passed) in the shape the graph colours of an evaluation Run use."""
        r = self.trace.run(run_id)
        if not r or r.get("origin", "deployment") != "deployment":
            return None
        started, ended = r.get("started") or {}, r.get("ended") or {}
        visits, by = [], {}
        for ev in self.trace.node_events(run_id):
            k = (ev["node_id"], ev.get("iteration") or 1)
            if ev.get("status") == "started":
                v = {"node": ev["node_id"], "iter": k[1], "status": "live", "started_at": ev.get("started_at"), "ended_at": None,
                     "input_ref": ev.get("input_ref"), "output_ref": None, "seq": ev["seq"]}
                by[k] = v
                visits.append(v)
            else:
                v = by.get(k)
                if v is None:                                  # an end without its start (should not happen): keep it visible
                    v = {"node": ev["node_id"], "iter": k[1], "started_at": None, "input_ref": None, "seq": ev["seq"]}
                    by[k] = v
                    visits.append(v)
                v.update(status={"done": "done", "error": "fail", "skipped": "skipped"}.get(ev.get("status"), "done"), ended_at=ev.get("ended_at"),
                         output_ref=ev.get("output_ref"), kind=ev.get("kind"), route=ev.get("route"), checks=ev.get("checks"),
                         expect_checks=ev.get("expect_checks"), error=ev.get("error"), provider=ev.get("provider"), model=ev.get("model"),
                         tokens_in=ev.get("tokens_in"), tokens_out=ev.get("tokens_out"), custom_source_hash=ev.get("custom_source_hash"),
                         prompt_ref=ev.get("prompt_ref"), provider_raw_ref=ev.get("provider_raw_ref"),
                         end_seq=ev["seq"])
        for v in visits:
            a, b = _ts(v.get("started_at")), _ts(v.get("ended_at"))
            v["ms"] = round((b - a).total_seconds() * 1000) if a and b else None
            v["started_at"], v["ended_at"] = _iso(a), _iso(b)
        passed = passed_edges(visits, graph)
        recorded = [x for x in self.trace.reviews(run_id) if x.get("event") == "review_recorded"]
        review = {"action": recorded[-1].get("action"), "actor_kind": recorded[-1].get("actor_kind"), "node": recorded[-1].get("node_id")} if recorded else None
        in_ref = _ref(started.get("input_ref"))
        value, why = read_payload(self.root, in_ref, self.trace.expired([in_ref.split(":", 1)[1]] if in_ref and ":" in in_ref else []))
        text, fields = self.summary(run_id, r.get("ended"), spec)
        return {"run_id": r["run_id"], "where": "deployment", "deployment_id": r.get("deployment_id"), "pack_revision": r.get("pack_revision"),
                "data_release_id": r.get("data_release_id"), "graph": r.get("graph"), "started_at": _iso(r["started_at"]), "ended_at": _iso(r.get("ended_at")),
                "status": ended.get("status"), "outcome": ended.get("outcome"), "result": text, "result_fields": fields, "ms": ended.get("ms"),
                "input": input_label(value) if value is not None else None, "input_summary": input_summary(value, ispec), "input_ref": in_ref, "input_missing": why,
                "output_ref": _ref(ended.get("output_ref")), "visits": visits, "passed": passed, "review": review,
                "payload_source": payload_source(self.root)}


def not_recorded(kind_name, meta):
    """OPS-2 review-2: what the operational record of one node does not hold ('기록에 없음' on the Metadata tab) — judged from that
    node_event row, not from the evaluation Run's list: provider · model · tokens are node_event columns (never listed); an llm node's
    rendered prompt and the provider's raw answer only when their refs are empty."""
    if kind_name != "llm":
        return []
    return [name for key, name in (("prompt_ref", "렌더된 프롬프트"), ("provider_raw_ref", "provider 원본 응답")) if not (meta or {}).get(key)]


def passed_edges(visits, graph):
    """'a>b' for every graph edge both ends of which ran, b starting after a ended; a router's edge only to its chosen target."""
    ends, starts, route = {}, {}, {}
    for v in visits:
        if v.get("status") == "done" and v.get("end_seq") is not None:
            ends.setdefault(v["node"], []).append(v["end_seq"])
        if v.get("seq") is not None:
            starts.setdefault(v["node"], []).append(v["seq"])
        if isinstance(v.get("route"), dict) and v["route"].get("target"):
            route.setdefault(v["node"], set()).add(v["route"]["target"])
    out = []
    for e in (graph or {}).get("edges") or []:
        a, b = e.get("source"), e.get("target")
        if a not in ends or b not in starts:
            continue
        if a in route and b not in route[a]:
            continue
        if any(s > min(ends[a]) for s in starts[b]):
            k = f"{a}>{b}"
            if k not in out:
                out.append(k)
    return out


RUNNING_HARNESS_ENV = "LLMOPS_FAKE_RUNNING"   # EV-054 CHG-234: test harness only (ISOLATED_ENV=1), read from os.environ on every call


def last_succeeded(settings, deployment_ids):
    """EV-054 CHG-232 · 234: ({deployment id: {run_id, revision, drs, mrs, recorded_at (epoch)}}, confirm_all) — the deploy side's
    signal of the row each deployment runs now. Inside isolated-env: the harness file (LLMOPS_FAKE_RUNNING, which alone answers when
    set), else the LLMOPS_FAKE_OPS fixture, else no_dsn — a test never opens the shared trace (PRES-255). Outside: the shared trace
    as llmops_app (the harness is never read). Raises OpsUnavailable."""
    from packrt.trace import model_release_id
    isolated = os.environ.get("ISOLATED_ENV") == "1"
    if isolated and os.environ.get(RUNNING_HARNESS_ENV):
        try:
            doc = json.loads(Path(os.environ[RUNNING_HARNESS_ENV]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise OpsUnavailable("broken") from None
        runs = {k: {"run_id": v.get("run_id"), "revision": v.get("revision"), "drs": v.get("drs"), "mrs": v.get("mrs"),
                    "recorded_at": _epoch(v.get("recorded_at"))} for k, v in (doc.get("runs") or {}).items() if k in set(deployment_ids)}
        return runs, bool(doc.get("confirm_all"))
    if settings.fake_ops:
        src = FixtureTrace(settings.fake_ops)
    elif isolated:
        raise OpsUnavailable("no_dsn")
    else:
        dsn = app_dsn(settings.db_env_file)
        if not dsn:
            raise OpsUnavailable("no_dsn")
        src = PgTrace(dsn)
    out = {}
    for r in src.last_succeeded(deployment_ids):
        models = r.get("model_releases") or {}
        out[r["deployment_id"]] = {"run_id": r["run_id"], "revision": r["pack_revision"], "drs": r.get("data_release_id"),
                                   "mrs": model_release_id({k: (v or {}).get("release_id") for k, v in models.items()}) if isinstance(models, dict) else None,
                                   "recorded_at": _epoch(r["recorded_at"])}
    return out, False


def _epoch(at):
    """A datetime (database) or ISO text as epoch seconds; None when it cannot be read."""
    from datetime import datetime
    if isinstance(at, (int, float)):
        return float(at)
    if isinstance(at, datetime):
        return at.timestamp()
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp() if at else None
    except (ValueError, TypeError):
        return None


def reader(settings):
    """The operational records' reader for these settings, or raises OpsUnavailable."""
    if settings.fake_ops:
        root = settings.ops_payload_root or Path(settings.fake_ops).parent / "payloads"
        return OpsRuns(FixtureTrace(settings.fake_ops), root)
    dsn = app_dsn(settings.db_env_file)
    if not dsn:
        raise OpsUnavailable("no_dsn")
    return OpsRuns(PgTrace(dsn), settings.ops_payload_root)
