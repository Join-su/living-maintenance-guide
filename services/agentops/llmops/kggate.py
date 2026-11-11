"""D-554 ① (llmops-2 · dev-2): the exception gate — an approved exception record's new graph is checked by LLMOps before the deploy
side swaps it in (the user's change of D-542 '들어올 때 시험 0', 11-04 20:4x). One call judges once; the deploy side counts the rounds
(kg-gate/input@1 'round' · 'max_rounds' · 'previous') and applies a candidate it is given (orchestration agents/deploy handoff/d554
gate-signal.md).

  python -m llmops.kggate --input <kg-gate/input@1 file> [--suites c1a15-start,…]
  → the last stdout line, kg-gate/result@1: {format, pass, checks{extract_match, impact, no_break}, candidate | null, reason, cand_revision?}
  exit 0 = judged (pass or not) · other = the gate itself could not run (the deploy side holds: gate_error)

The three checks:
  extract_match — each new relation (and its exception section) against the letter it came from: record id · equipment · time · AI
                  first check · previous record · sensors · symptom group, and the check · action · note fields moved as written
  impact        — the Pack's tools/graph_gate.py (Pack .venv, its own process, no graph DB, no network): the past alarms and the suite
                  cases whose matching exception relation differs between the running and the new graph (numbers; it never fails)
  no_break      — the same cases' outputs (graph_gate runs the Pack on both graphs) scored by LLMOps against the suites'
                  expectations: a case right on the running graph and wrong on the new one is broken (the Pack never reads them)
On a failure the candidate is a graph-side fix only (D-554: 'KG단만'): the relation the broken cases got, narrowed to its evidence
equipment, else to a sensor side its evidence keeps, else off — never one an earlier round applied. No candidate → the deploy side
holds. A pass carries cand_revision, the new graph's Pack revision graph_gate counted (the deploy side compares it before its commit).
No model call, no key read; the Pack repository is only read (graph_gate copies what it runs)."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import exgraph, graphrel, suites

FORMAT_IN, FORMAT_OUT = "kg-gate/input@1", "kg-gate/result@1"
SUITES = ("c1a15-start", "c1a15-start-v8c", "c1a15-start-v8bc")   # D-560 (eval 170 · Pack b71299d): the base suites the eval side named —
# start-v8 stays out (D-554 덧 6: its inputs have no equipment, so an equipment narrow could never be told from it)
CHANGE_ORDER = ("equipment", "narrow", "off")
WHY = {"equipment": "기존 시험 {cases}에 끼어듦 — 다른 설비 알람 · 관계를 근거 설비 {eq}로 한정",
       "narrow": "기존 시험 {cases}에 끼어듦 — 근거 값은 그대로, 그 case는 빠지게 조건 좁힘({where})",
       "off": "기존 시험 {cases}에 끼어듦 — 좁힐 조건이 없어 이 관계를 끔"}


class GateError(Exception):
    """The gate could not judge (a bad input file · graph_gate did not run · its output unreadable)."""


def read_input(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GateError(f"입력 파일을 못 읽음({type(e).__name__})") from None
    if not isinstance(d, dict) or d.get("format") != FORMAT_IN:
        raise GateError(f"입력 꼴이 {FORMAT_IN}가 아님")
    for k in ("pack_repo", "running_graph", "new_graph"):
        if not isinstance(d.get(k), str) or not d[k]:
            raise GateError(f"입력 칸 없음({k})")
    return d


def _graph(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GateError(f"그래프 파일을 못 읽음({type(e).__name__})") from None


# ---------------------------------------------------------------- extract_match
def _ctx(record):
    er = (record or {}).get("exception_record") or {}
    c = er.get("context") if isinstance(er.get("context"), dict) else er.get("situation")
    return er, c if isinstance(c, dict) else {}


def _snapshot(ctx):
    sv = ctx.get("sensor_values") or {}
    return sv.get("snapshot") if isinstance(sv, dict) else None


def moved(section_part, letter_part):
    """A check or action moved as written — a null minutes and an empty how · criteria are the loader's to fill (fields-gate 1절 ·
    Pack 43025a5), so they are not compared; every given text (what · how · criteria · parts · instruction) and a given minutes is."""
    if not isinstance(letter_part, dict) or not isinstance(section_part, dict):
        return section_part == letter_part
    fill = {k for k, v in letter_part.items() if v is None or (k in ("how", "criteria") and v == "")
            or (k == "instruction" and isinstance(v, str) and not v.strip())}   # D-642 C: a blank instruction is dropped, never made
    return all(section_part.get(k) == v for k, v in letter_part.items() if k not in fill)   # the loader may add a field (parts [])


def one_of(section, one, many):
    """A single check · action of the section — its old field, or (Pack 1a15039's loader writes lists for every exception section) the
    list's one item; None when there is neither or the list is not one item."""
    s = section or {}
    if s.get(one) is not None:
        return s[one]
    xs = s.get(many)
    return xs[0] if isinstance(xs, list) and len(xs) == 1 else None


EDIT_KEPT = ("no", "app_no", "kind", "grade", "name", "group", "minutes", "records")   # D-560 7: the target section's fields an edit keeps


def edit_fields(rel, section, letter, base):
    """D-560 7 (same-edit.md 2 · 4절): an edited target on the loaded graph — the lists as written, this record among the relation's
    records, and (with the running graph) the target's own condition and topic kept."""
    er = (letter or {}).get("exception_record") or {}
    ed, sec = er["exception_review"]["edit"], section or {}
    out = {"record_in": letter.get("record_id") in (rel.get("records") or []), "section": sec.get("no") == rel.get("step")}
    for k in ("checks", "actions"):
        mine, theirs = sec.get(k), ed.get(k)
        out[k] = isinstance(mine, list) and isinstance(theirs, list) and len(mine) == len(theirs) and all(moved(a, b) for a, b in zip(mine, theirs))
    if base:
        brel = next((x for x in base.get("exception_relations") or [] if x.get("id") == rel.get("id")), None)
        bsec = next((s for s in base.get("sections") or [] if s.get("no") == rel.get("step")), None)
        out["when"] = brel is not None and brel.get("when") == rel.get("when")
        out["topic"] = bsec is not None and bsec.get("topic") == sec.get("topic")
        # eval 181: the loader swaps the whole section line in — a field the extractor did not carry is gone; the kept ones stay as they were
        out["kept"] = bsec is not None and all(sec.get(k) == bsec.get(k) for k in EDIT_KEPT if k in bsec)
    return out


MISSED_KEPT = ("step", "mode", "grade", "cause")   # D-560 #5: a widened relation's fields that stay as they were


def is_missed(rv):
    """D-560 #5: a '같음' with no edit on an exception of the graph (놓침)."""
    return isinstance(rv, dict) and rv.get("decision") == "same" and rv.get("edit") is None and (rv.get("match") or {}).get("kind") == "예외" \
        and bool((rv.get("match") or {}).get("no"))


def missed_fields(rel, letter, base):
    """D-560 #5 (extractor-rows.md 1 · 2절): a widened or added relation on the loaded graph — this record in it, its step the target, the
    alarm inside its condition, n = its records; widened (the running graph has that id): every base record kept, the group · equipment ·
    cause · mode as they were and the same sensors."""
    er, ctx = _ctx(letter)
    snap = _snapshot(ctx) or {}
    grp = ctx.get("symptom_group")
    name = grp.get("name") if isinstance(grp, dict) else None
    when, recs = rel.get("when") or {}, rel.get("records") or []
    out = {"record_in": letter.get("record_id") in recs, "section": rel.get("step") == er["exception_review"]["match"]["no"],
           "holds": exgraph._holds(when, name if name else when.get("group"), snap, ctx.get("equipment_id")), "n": rel.get("n") == len(recs)}
    brel = next((x for x in (base or {}).get("exception_relations") or [] if x.get("id") == rel.get("id")), None) if base else None
    if brel is not None:
        bw = brel.get("when") or {}
        out["base_records"] = all(r in recs for r in brel.get("records") or [])
        out["kept"] = all(rel.get(k) == brel.get(k) for k in MISSED_KEPT) and when.get("group") == bw.get("group") \
            and when.get("equipment_id") == bw.get("equipment_id") and [c[:2] for c in when.get("sensors") or []] == [c[:2] for c in bw.get("sensors") or []]
    return out


def recent_fields(rel, ctx):
    """D-570 (recent-condition.md 1 · 5절): when.recent against the letter's own maintenance list — the record named is on it, the
    section is one it did or checked, the days take it in and stay in range, and the condition has no sensor and no equipment."""
    when, rc = rel.get("when") or {}, rel.get("context") or {}
    r = when.get("recent") or {}
    row = next((x for x in ctx.get("recent_maintenance") or [] if x.get("record_id") == rc.get("recent_record")), None)
    days = r.get("days")
    return {"recent_record": row is not None, "recent_section": row is not None and r.get("section") in list(row.get("did") or []) + list(row.get("checked") or []),
            "recent_days": row is not None and isinstance(days, int) and not isinstance(days, bool) and row.get("days_before", days) < days <= exgraph.RECENT_DAYS_MAX,
            "recent_any": not when.get("sensors") and not when.get("equipment_id")}


def relation_fields(rel, section, letter, base=None):
    """{field: True/False} — the relation (and its section) against its letter; a field the letter does not carry is left out."""
    er, ctx = _ctx(letter)
    rv = er.get("exception_review") if isinstance(er.get("exception_review"), dict) else {}
    if rv.get("decision") == "same" and isinstance(rv.get("edit"), dict):   # an edited target keeps its own evidence: not the letter's facts
        return edit_fields(rel, section, letter, base)
    if is_missed(rv):   # D-560 #5: a widened relation keeps its own facts · an added one steps to the target
        return missed_fields(rel, letter, base)
    rc, out = rel.get("context") or {}, {}
    out["record_id"] = letter.get("record_id") in (rel.get("records") or [])
    pairs = (("equipment_id", ctx.get("equipment_id")), ("occurred_at", ctx.get("occurred_at")),
             ("ai_first_check", (ctx.get("ai_first_check") or {}).get("section") if isinstance(ctx.get("ai_first_check"), dict) else ctx.get("ai_first_check")),
             ("previous_record", (ctx.get("previous_maintenance") or {}).get("record_id") if isinstance(ctx.get("previous_maintenance"), dict) else None))
    for k, v in pairs:
        if v is not None:
            out[k] = rc.get(k) == v
    snap = _snapshot(ctx)
    if isinstance(snap, dict):
        out["sensors"] = all(rc.get("sensors", {}).get(k) == v for k, v in snap.items())
    grp = ctx.get("symptom_group")
    name = grp.get("name") if isinstance(grp, dict) else None
    if name:
        out["group"] = (rel.get("when") or {}).get("group") == name
    out["section"] = section is not None and section.get("no") == rel.get("step")
    if exgraph.fields_of(er):   # fields-gate.md 4절: a fielded letter's check · action · note moved as written, never rewritten
        for k in ("check", "action", "note"):
            if er.get(k) is not None:
                out[k] = moved(one_of(section, k, k + "s") if k != "note" else (section or {}).get(k), er[k])
    if isinstance(er.get("exception"), dict):   # D-566 (v10): the check list and action list moved as written, in order, item by item
        x, sec = er["exception"], section or {}
        for k in ("checks", "actions"):
            mine, theirs = sec.get(k), x.get(k)
            out[k] = isinstance(mine, list) and isinstance(theirs, list) and len(mine) == len(theirs) and all(moved(a, b) for a, b in zip(mine, theirs))
        out["note"] = sec.get("note") == er.get("note")
        if (x.get("topic") or "").strip():
            out["name"] = sec.get("name") == (exgraph.TOPIC + x["topic"].strip())[:exgraph.TITLE_MAX]
        if (rel.get("when") or {}).get("recent"):
            out.update(recent_fields(rel, ctx))
        return out
    it = exgraph.item_of(er, rel.get("id"), letter.get("record_id"))
    if isinstance(er.get("items"), list):   # D-560: an item letter — the relation's own item, moved as written
        out["item"] = it is not None
        if it is not None:
            out["check"], out["action"] = moved(one_of(section, "check", "checks"), it.get("check")), moved(one_of(section, "action", "actions"), it.get("action"))
            out["note"] = (section or {}).get("note") == er.get("note")
            if (it.get("topic") or "").strip():
                out["name"] = (section or {}).get("name") == (exgraph.TOPIC + it["topic"].strip())[:exgraph.TITLE_MAX]
    return out


def _lists_moved(mine, theirs):
    return isinstance(mine, list) and isinstance(theirs, list) and len(mine) == len(theirs) and all(moved(a, b) for a, b in zip(mine, theirs))


def candidate_match(graph, candidate, base=None):
    """deploy candidate-graph-inlet.md 6절: a candidate graph's rows moved as given — per relation (a row's own id, or the relation a
    section row's exception is the step of): a whole relation row as it is on the graph ('row'); an edit row (edit: true) this
    record in and the base condition kept; a section row's lists in order, an edited one keeping the target's fields. Rows not passed
    on (the deploy side's input without them) are 'missing' and counted nowhere."""
    rows = (candidate or {}).get("rows")
    if not isinstance(rows, dict):
        return {"pass": True, "rows": "missing", "relations": {}}
    rels = {r["id"]: r for r in graph.get("exception_relations") or []}
    secs = {s["no"]: s for s in graph.get("sections") or [] if s.get("kind") == "예외"}
    brels = {r["id"]: r for r in (base or {}).get("exception_relations") or []}
    bsecs = {s["no"]: s for s in (base or {}).get("sections") or []}
    out = {}
    for row in rows.get("exception_relations") or []:
        r, f = rels.get(row.get("id")), out.setdefault(row.get("id"), {})
        if r is None:
            f["relation"] = False
        elif row.get("edit"):
            f["record_in"] = all(x in (r.get("records") or []) for x in row.get("records") or [])
            f["when"] = row.get("id") in brels and brels[row["id"]].get("when") == r.get("when")
        else:
            f["row"] = all(r.get(k) == v for k, v in row.items())
    for row in rows.get("sections") or []:
        sec = secs.get(row.get("no"))
        targets = [r["id"] for r in rels.values() if r.get("step") == row.get("no")] or [row.get("no")]
        for rid in targets:
            f = out.setdefault(rid, {})
            f["section"] = sec is not None
            if sec is None:
                continue
            f["checks"], f["actions"] = _lists_moved(sec.get("checks"), row.get("checks")), _lists_moved(sec.get("actions"), row.get("actions"))
            if row.get("edit_record"):
                b = bsecs.get(row.get("no"))
                f["kept"] = b is not None and all(sec.get(k) == b.get(k) for k in EDIT_KEPT if k in b)
    res = {rid: {"fields": f, "ok": all(f.values())} for rid, f in out.items()}
    return {"pass": all(v["ok"] for v in res.values()), "rows": "given", "relations": res}


def extract_match(graph, letters, new_relations, base=None, candidate=None):
    if candidate is not None and not letters:   # a candidate graph: its rows, not letters
        return candidate_match(graph, candidate, base)
    rels = {r["id"]: r for r in graph.get("exception_relations") or []}
    secs = {s["no"]: s for s in graph.get("sections") or [] if s.get("kind") == "예외"}
    by_record = {x.get("record_id"): x for x in letters or [] if isinstance(x, dict)}
    edited = [rv["match"]["no"] for x in letters or [] if isinstance(x, dict)
              for rv in [((x.get("exception_record") or {}).get("exception_review") or {})]
              if isinstance(rv, dict) and rv.get("decision") == "same" and isinstance(rv.get("edit"), dict) and (rv.get("match") or {}).get("no")]
    missed = [r["id"] for x in letters or [] if isinstance(x, dict) and is_missed((x.get("exception_record") or {}).get("exception_review"))
              for r in rels.values() if x.get("record_id") in (r.get("records") or [])]   # D-560 #5: the relation a missed letter went into
    rows = {}
    for rid in list(dict.fromkeys(list(new_relations or []) + edited + missed)):   # D-560 7: an edited target is checked though it is not new
        r = rels.get(rid)
        if not r:
            rows[rid] = {"fields": {"relation": False}, "ok": False}
            continue
        letter = next((by_record[x] for x in r.get("records") or [] if x in by_record), None)
        if letter is None:
            rows[rid] = {"fields": {}, "ok": None}   # no letter for it: not counted
            continue
        f = relation_fields(r, secs.get(r.get("step")), letter, base)
        rows[rid] = {"fields": f, "ok": all(f.values())}
    return {"pass": all(v["ok"] is not False for v in rows.values()), "relations": rows}


# ---------------------------------------------------------------- graph_gate (the Pack's) and no_break
def graph_gate(pack_repo, base, cand, datasets, out):
    """Run the Pack's tools/graph_gate.py with the Pack's own python — no model key passed on (the environment loses every *_API_KEY)."""
    py = Path(pack_repo) / ".venv" / "bin" / "python"
    tool = Path(pack_repo) / "tools" / "graph_gate.py"
    if not py.exists() or not tool.exists():
        raise GateError("Pack 관문 길(tools/graph_gate.py · .venv)이 없음")
    cmd = [str(py), "-B", str(tool), "--base", str(base), "--cand", str(cand), "--out", str(out)]
    for d in datasets:
        cmd += ["--cases", str(d)]
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    r = subprocess.run(cmd, cwd=str(pack_repo), env=env, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        why = last_error(getattr(r, "stderr", "") or "")
        raise GateError(f"graph_gate 실패(종료 {r.returncode})" + (f" — {why}" if why else ""))
    try:
        return json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GateError("graph_gate 출력을 못 읽음") from None


def last_error(stderr):
    """The Pack tool's last error line for the deploy record (deploy 425): the exception's own name and message, each absolute path cut
    to its last part; the isolated-env notice and traceback frames are left out."""
    lines = [x.strip() for x in stderr.splitlines() if x.strip() and not x.startswith(("isolated-env:", "  ", "Traceback"))]
    if not lines:
        return ""
    head, _, msg = lines[-1].partition(": ")
    line = f"{head.rsplit('.', 1)[-1]}: {msg}" if msg and " " not in head else lines[-1]
    return re.sub(r"/(?:[^/\s]+/)+([^/\s]+)", r"…/\1", line)[:300]


def suite_files(pack_repo, names):
    """{suite: (dataset path, {case_id: expectation})} for the suites the Pack has; the expectations are read here only (LLMOps)."""
    out = {}
    for n in names:
        d = Path(pack_repo) / "pack" / "evals" / n
        ds, ex = d / "dataset.jsonl", d / "expectations.jsonl"
        if ds.exists() and ex.exists():
            exp = {}
            for line in ex.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    x = json.loads(line)
                    exp[x["case_id"]] = x
            out[n] = (ds, exp)
    return out


def _final_ok(value, expectation):
    checks = (expectation or {}).get("final") or []
    if not checks:
        return None
    return all(suites.check(value, c)[0] is True for c in checks)


def _suite_of(row, names):
    s = str(row.get("suite") or "")
    return next((n for n in names if s == n or s.endswith("/" + n) or s.endswith(n)), s)


def _listed(value, expectation):
    """D-586: the exception the case's expectation names is in this output's list (a row with 'relation' · Pack ef9ec74 candidate-output.md)."""
    want = {c["equals"] for c in (expectation or {}).get("final") or [] if c.get("path") == "exception_guide.relation_id" and c.get("equals")}
    rows = [x for k in ("preview", "decision") for x in (((value or {}).get(k) or {}).get("reason_facts") or {}).get("alternatives") or []]
    return bool(want) and any(isinstance(x, dict) and x.get("relation") in want for x in rows)


def no_break(gate_out, files):
    """Each case right on the running graph and wrong on the new one is broken; the rest are counted, never shown by value. D-586: a case
    wrong on both that the new graph now lists the wanted exception for is 'listed' (a C exception is no first check: eval 189)."""
    broken, fixed, listed, n = [], [], [], 0
    for row in gate_out.get("cases") or []:
        suite = _suite_of(row, list(files))
        exp = (files.get(suite) or (None, {}))[1].get(row.get("case_id"))
        a, b = _final_ok(row.get("base"), exp), _final_ok(row.get("cand"), exp)
        if a is None or b is None:
            continue
        n += 1
        if a and not b:
            broken.append(f"{suite}/{row['case_id']}")
        elif b and not a:
            fixed.append(f"{suite}/{row['case_id']}")
        elif not a and not b and _listed(row.get("cand"), exp) and not _listed(row.get("base"), exp):
            listed.append(f"{suite}/{row['case_id']}")
    return {"pass": not broken, "broken": broken, "fixed": fixed, "listed": listed, "scored": n}


# ---------------------------------------------------------------- the graph-side candidate
def _relation_of(output):
    g = (output or {}).get("exception_guide") if isinstance(output, dict) else None
    return g.get("relation_id") if isinstance(g, dict) else None


def candidates(cand_graph, gate_out, files, broken, previous):
    """The graph-side fixes for the relation the broken cases got, in CHANGE_ORDER (its evidence equipment · a sensor side · off), none an
    earlier round applied. [] when no broken case got a relation."""
    rows = {f"{_suite_of(r, list(files))}/{r.get('case_id')}": r for r in gate_out.get("cases") or []}
    by_rel = {}
    for key in broken:
        rid = _relation_of((rows.get(key) or {}).get("cand"))
        if rid:
            by_rel.setdefault(rid, []).append(key)
    if not by_rel:
        return []
    rid, keys = max(by_rel.items(), key=lambda kv: len(kv[1]))
    rel = next((r for r in cand_graph.get("exception_relations") or [] if r["id"] == rid), None)
    if not rel:
        return []
    inputs = {}
    for suite, (ds, _) in files.items():
        for line in Path(ds).read_text(encoding="utf-8").splitlines():
            if line.strip():
                x = json.loads(line)
                inputs[f"{suite}/{x['case_id']}"] = x.get("input")
    wasted = []
    for k in keys:
        q = graphrel._query(inputs.get(k) or {})
        if q:
            wasted.append({"case": k, "sensors": q["sensors"], **({"equipment": q["equipment_id"]} if q.get("equipment_id") else {})})
    sig = lambda c: json.dumps([c.get("change"), c.get("when")], sort_keys=True, ensure_ascii=False)   # noqa: E731
    tried = {sig(p.get("applied_candidate") or {}) for p in previous or [] if isinstance(p, dict) and (p.get("applied_candidate") or {}).get("relation_id") == rid}
    cases, out = " · ".join(keys), []
    for way in CHANGE_ORDER:
        when = graphrel.narrow_equipment(rel, wasted) if way == "equipment" else graphrel.narrow(rel, wasted) if way == "narrow" else None
        if way != "off" and not when:
            continue
        c = {"relation_id": rid, "change": "off" if way == "off" else "narrow"}
        if when:
            c.update(when=when, where=graphrel.where_text(when))
        if sig(c) in tried:
            continue
        c["why"] = WHY[way].format(cases=cases, eq=(rel.get("context") or {}).get("equipment_id"), where=c.get("where", ""))
        out.append(c)
    return out


def apply_candidate(graph, c):
    """The graph with the candidate applied — as the deploy side applies it (gate-signal.md 2절): the relation's condition, or the
    relation taken out for 'off'."""
    g = json.loads(json.dumps(graph))
    if c["change"] == "off":
        g["exception_relations"] = [r for r in g.get("exception_relations") or [] if r["id"] != c["relation_id"]]
    else:
        for r in g.get("exception_relations") or []:
            if r["id"] == c["relation_id"]:
                r["when"], r["where"] = c["when"], ";".join(f"{f}{op}{v}" for f, op, v in c["when"].get("sensors") or [])
    return g


def pick(inp, cand_graph, cands, files, runner, tmp):
    """D-554 덧 6: each candidate tried on the same suites in this round (graph_gate once each) — the one with nothing broken and the
    most fixed (D-586: fixed + listed) first; with none breaking nothing, the fewest broken. Every try's counts go into the result."""
    tried = []
    for i, c in enumerate(cands):
        f = Path(tmp) / f"cand-{i}.json"
        f.write_text(json.dumps(apply_candidate(cand_graph, c), ensure_ascii=False), encoding="utf-8")
        nb = no_break(runner(inp["pack_repo"], inp["running_graph"], str(f), [ds for ds, _ in files.values()], Path(tmp) / f"gate-{i}.json"), files)
        tried.append({**c, "broken": len(nb["broken"]), "fixed": len(nb["fixed"]), "listed": len(nb["listed"])})
    if not tried:
        return None, []
    best = min(tried, key=lambda t: (t["broken"], -t["fixed"] - t["listed"], tried.index(t)))
    return {k: v for k, v in best.items() if k not in ("broken", "fixed", "listed")} | {"tried": {k: best[k] for k in ("broken", "fixed", "listed")}}, tried


# ---------------------------------------------------------------- one judgement
MOVES = """import json, sys
sys.path.insert(0, "pack")
from custom import exception
base, cand = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
hit = lambda g, r: (exception.match(g, r["group"], r["sensors"], r.get("equipment")) or [{}])[0].get("id")
print(json.dumps([[a, b] for r in cand.get("records") or [] for a, b in [(hit(base, r), hit(cand, r))] if a != b]))
"""


def pack_moves(pack_repo, base, cand):
    """[[from, to]] per past alarm (the candidate graph's records) whose exception relation differs between the two graphs — the Pack's
    own exception.match with the Pack's python (no key passed on), as its graph_gate counts the impact."""
    py = Path(pack_repo) / ".venv" / "bin" / "python"
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    r = subprocess.run([str(py), "-B", "-c", MOVES, str(base), str(cand)], cwd=str(pack_repo), env=env, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        why = last_error(getattr(r, "stderr", "") or "")
        raise GateError("겹침 셈 실패" + (f" — {why}" if why else ""))
    return json.loads(r.stdout.strip().splitlines()[-1])


def _side(graph, rid):
    rel = next((x for x in graph.get("exception_relations") or [] if x.get("id") == rid), {})
    sec = next((s for s in graph.get("sections") or [] if s.get("no") == rel.get("step")), {})
    return rel, {"no": sec.get("no"), "topic": sec.get("topic"), "name": sec.get("name"), "checks": sec.get("checks"), "actions": sec.get("actions"),
                 "records": rel.get("records") or [], "line": {"section": sec, "relation": rel}}   # line: the whole rows a 바꿈 · 합침 carries on


def overlap(moves, base_g, cand_g, touched, decided):
    """D-560 겹침 (정답지 31쪽): past alarms another exception's relation took that a new or changed relation takes now — per (from, to)
    the count and the two exceptions side by side. Held unless a person decided the graph (a 바꿈 · 합침 candidate)."""
    by = {}
    for a, b in moves or []:
        if a and b and a != b and b in touched:
            (ra, sa), (rb, sb) = _side(base_g, a), _side(cand_g, b)
            if ra.get("step") != rb.get("step"):   # the same exception's two relations are no overlap
                by.setdefault((a, b), {"from": a, "to": b, "n": 0, "from_section": sa, "to_section": sb})["n"] += 1
    pairs = sorted(by.values(), key=lambda p: (-p["n"], p["from"], p["to"]))
    return {"pairs": pairs, "held": bool(pairs) and not decided, "read": moves is not None}


def count_base(base_g, cand_g):
    """D-570 덧 6 (deploy 450): what the no-break count compares with — the new graph with the running graph's exceptions: the running
    graph plus the new history. A record that joins the history moves cases on either side; only the exception relations' change counts."""
    exc = lambda g: [s for s in g.get("sections") or [] if s.get("kind") == "예외"]   # noqa: E731
    return {**cand_g, "sections": [s for s in cand_g.get("sections") or [] if s.get("kind") != "예외"] + exc(base_g),
            "exception_relations": base_g.get("exception_relations") or []}


def judge(inp, names=SUITES, runner=graph_gate, matcher=None):
    with tempfile.TemporaryDirectory() as tmp:
        return _judge(inp, names, runner, matcher, tmp)


def _judge(inp, names, runner, matcher, tmp):
    base_g, cand_g = _graph(inp["running_graph"]), _graph(inp["new_graph"])
    em = extract_match(cand_g, inp.get("letters"), inp.get("new_relations"), base_g, inp.get("candidate"))
    files = suite_files(inp["pack_repo"], names)
    count = count_base(base_g, cand_g)
    base_file = inp["running_graph"]
    if count != base_g:   # new history in the new graph: the count starts from it
        base_file = str(Path(tmp) / "count-base.json")
        Path(base_file).write_text(json.dumps(count, ensure_ascii=False), encoding="utf-8")
    gate_out = runner(inp["pack_repo"], base_file, inp["new_graph"], [ds for ds, _ in files.values()], Path(tmp) / "gate.json")
    imp = gate_out.get("impact") or {}
    impact = {"pass": True, "alarms": (imp.get("alarms") or {}).get("n"), "cases": (imp.get("cases") or {}).get("n"),
              "by_relation": (imp.get("alarms") or {}).get("by_relation") or {}}
    nb = no_break(gate_out, files)
    matcher = matcher or (pack_moves if runner is graph_gate else None)   # a stand-in runner reads no moves unless given its own
    rels = gate_out.get("relations") or {}
    touched = set(rels.get("added") or []) | set(rels.get("changed") or []) | set(inp.get("new_relations") or [])
    ov = overlap(matcher(inp["pack_repo"], inp["running_graph"], inp["new_graph"]) if matcher else None, base_g, cand_g, touched,
                 bool((inp.get("candidate") or {}).get("decided_by")))
    checks = {"extract_match": em, "impact": impact, "no_break": nb, "overlap": ov}
    ok = em["pass"] and not ov["held"] and nb["pass"]
    out = {"format": FORMAT_OUT, "pass": ok, "checks": checks, "candidate": None, "reason": None}
    if ok:
        rev = (gate_out.get("cand") or {}).get("revision")
        if rev:
            out["cand_revision"] = rev
        return out
    out["reason"] = "extract_match" if not em["pass"] else "overlap" if ov["held"] else "no_break"   # an overlap is a person's (31쪽)
    rounds_left = int(inp.get("round") or 1) < int(inp.get("max_rounds") or 4)
    if out["reason"] == "no_break" and rounds_left:   # a wrong extraction is never fixed on the graph side (extraction runs once)
        cands = candidates(cand_g, gate_out, files, nb["broken"], inp.get("previous"))
        out["candidate"], out["candidates_tried"] = pick({**inp, "running_graph": base_file}, cand_g, cands, files, runner, tmp)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="예외 관문 — 새 그래프를 배포 전에 점검(kg-gate/input@1 → kg-gate/result@1)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--suites", help="쉼표로 · 기본 " + ",".join(SUITES))
    a = ap.parse_args(argv)
    try:
        inp = read_input(a.input)
        res = judge(inp, tuple(x for x in (a.suites or "").split(",") if x) or SUITES)
    except GateError as e:
        print(json.dumps({"format": FORMAT_OUT, "error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
