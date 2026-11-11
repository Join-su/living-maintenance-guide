"""D-543 ⑥ 1: approved exception records → the node lines of a new graph version, straight away (the default way, no LLMOps on the
way in — D-542). The graph's shape is c1a15-pack's (D-545 · orchestration/agents/c1a15/handoff/d545/graph-shape.md).

An exception record (the app's GET /api/internal/exception-records item: what happened · what was done, the technician's two texts,
and the eight facts the server adds) → the LLM writes one check · action as a know-how text in one piece (and its steps · criteria
when the text gives them) → one new node (section no = app_no = EX-<record>, kind '예외', grade C — the part lead changes the grade
later on the grade screen) and one relation with a condition:

  when  = the symptom group (its name) + the sensor sides the text names, at this case's own values (narrow at first; the values
          never come from the answer) · a condition seen only on site → a reference notice for the same equipment (mode '참고')
  where = the same sensor part in the rule-table string form ('temp_diff_k<=8.2') — read by people only

The output is the node-lines file {sections, exception_relations}; the Pack's loader adds it to the base graph (--nodes) and names the
new layer and release_id (minutes null → its rule). The answer's shape is the only check on the way in: a broken one is not put in and
gives one line (record id · code).

D-556 덧 7 · D-560 (contract v9 4절): a letter carrying the AI split's items (check · action · topic, Pack exception_split — the
'아님' ones, with item_index = their original order) is moved with no model call — one item = one section '예외상황-{topic}' (check ·
action · note as written) and one relation whose condition is the alarm's sensor at this case's own value on the alarm's side (no
equipment limit; a sensor the name table does not know → the same equipment's reference notice). Its id is EX-<record> for a letter
of one item, else EX-<record>-<item_index + 1> (deploy 413: the '같음' items keep their numbers). The old letters keep the model way.

D-566 (contract v10): a letter carrying exception_record.exception {topic, checks[], actions[]} — one exception, its check list and
action list in the order written — is moved as ONE section (topic · checks · actions · note as written) and ONE relation EX-<record>
with the same alarm condition; v9 item letters still go as above.

D-570 (c1a15 handoff/d570/recent-condition.md): a v10 letter whose context carries recent_maintenance asks the model three things only
(caused by recent maintenance? · which record · days of margin); the section and the days come from that record's own values, and the
relation reads when {group, sensors [], recent {section, days}} — any equipment. Anything else leaves the alarm-sensor relation.

Stands alone (no LLMOps import, standard library only) so the Pack's loader can run it:
  python -B llmops/exgraph.py --base <release.json> --records <items.json> --out <nodes.json> [--answers <fake.json> |
         --real --key-file <env file> [--key-name <variable>] --model gpt-4.1-mini --base-url <provider /v1 address> --cap 1.0]
  (neither = item letters only; an old letter then goes nowhere, 'no_extractor')
The key is read from the file into memory only; a provider error says its code, where and the HTTP status only."""
import argparse
import copy
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

FIELDS = ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min", "temp_diff_k", "power_w")
SIDES = {"high": ">=", "low": "<="}
MAX_FIELDS = 2
GRADE = "C"
KIND = "예외"
TITLE_MAX, GUIDE_MAX, STEPS_MAX, STEP_MAX, CRITERIA_MAX = 40, 1000, 10, 100, 300
USD_PER_1M = {"gpt-4.1-nano": (0.10, 0.40), "gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00)}
MAX_OUT = 600
WHERE = "exgraph.openai"
SYSTEM = """You turn one maintenance exception record into one know-how step for a maintenance guide.
The input is JSON: situation (what the technician saw), action (what they did), context (facts the app added: equipment, symptom group,
time, sensor values, previous maintenance, the AI's first check, the confirmed cause).
Answer with one JSON object only:
{"title": short Korean name of the check and action (at most 40 characters),
 "guide": Korean know-how text in one piece: what to check, then what to do (at most 1000 characters),
 "steps": the procedure as short Korean lines, only when the input gives them, else [],
 "criteria": the Korean criterion for normal and abnormal, only when the input gives it, else null,
 "fields": the sensor fields of context.sensor_values.snapshot under which this step applies, at most 2, each {"field": name, "side": "high" or "low"},
 "site_only": true when the condition can be seen only on site (no sensor shows it), else false,
 "this_equipment_only": true when the input says the cause belongs to this equipment only (not the same kind of machine in general), else false}
Use only what the input says. Write only the actions the record states: no sentence of effects, reasons or explanations
not stated in the input (for example what the step prevents or improves). Do not give numbers for the fields; the case's own values are used."""
RECENT_SYSTEM = """A technician's approved exception record and the maintenance done on this equipment in the last days are given as JSON.
Work in this order. First, find in "text" a statement that something was done or checked N days ago (for example "3일 전"). Second, compare N with
days_before of each row of recent_maintenance. If a row's days_before is within 1 of N, the answer is recent true with that row, whatever its
sections or outcome say: the technician is pointing at that visit. Only when the text gives no such N, decide whether the record says or clearly
implies the fault came from recent work (for example a bolt left loose at the last service).
Answer with one JSON object and nothing else:
{"recent": true or false,
 "record_id": the record_id of that maintenance from the list, null when recent is false,
 "margin_days": whole days of margin to add to how long ago it was, 1 to 7}
Do not name sections or dates; the record's own values are used."""
RECENT_DAYS_MAX = 30   # the Pack's range for when.recent.days
RECENT_KINDS = ("점검", "조치")   # recent-condition.md 1절: the group's check · action sections only
TOPIC = "예외상황-"   # contract v9 4절 (D-560): the section name of an item
ALARM_FIELD = {"온도 차": "temp_diff_k", "토크": "torque_nm", "회전력": "torque_nm", "회전 속도": "rotational_speed_rpm", "공구 마모": "tool_wear_min",
               "공구 마모 시간": "tool_wear_min", "출력": "power_w", "기계 출력": "power_w", "공정 온도": "process_temperature_k",
               "공기 온도": "air_temperature_k"}   # the app's alarm head · sensor table · report names (app 001-zzz56)
UNIT_FIELD = {"K": "temp_diff_k", "rpm": "rotational_speed_rpm", "Nm": "torque_nm", "분": "tool_wear_min", "W": "power_w"}   # a value with no name (app 001-zzz56)
EFFECT_WORDS = ("통해", "예방", "개선", "방지", "효과", "도움", "위해")   # D-545 덧 3: 137's last sentence — kept narrow on purpose


class ExtractError(RuntimeError):
    """The extractor could not ask — its code and where, and the HTTP status when there is one; never the provider's message."""

    def __init__(self, code, where=WHERE, status=None):
        self.code, self.where, self.status = code, where, status
        super().__init__(" · ".join([code, where] + ([f"HTTP {status}"] if status is not None else [])))


class BudgetCap(RuntimeError):
    """The next call could pass the cap — stopped before it."""


class Budget:
    def __init__(self, cap_usd):
        self.cap, self.calls, self.input_tokens, self.output_tokens, self.usd = float(cap_usd), 0, 0, 0, 0.0

    def before(self, model, chars):
        pin, pout = USD_PER_1M[model]
        if self.usd + ((chars // 2 + 50) * pin + MAX_OUT * pout) / 1e6 > self.cap:
            raise BudgetCap()

    def add(self, model, tin, tout):
        pin, pout = USD_PER_1M[model]
        self.calls, self.input_tokens, self.output_tokens = self.calls + 1, self.input_tokens + tin, self.output_tokens + tout
        self.usd += (tin * pin + tout * pout) / 1e6

    def summary(self):
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "usd": round(self.usd, 6)}


def fields_of(e):
    """D-554 ④ (c1a15 handoff/d554/fields-gate.md): a letter's check · action · note fields, or None for the old two-text letter."""
    if isinstance(e.get("check"), dict) or isinstance(e.get("action"), dict):
        return {"check": e.get("check"), "action": e.get("action") if isinstance(e.get("action"), dict) else None, "note": e.get("note")}
    return None


def _fields_ok(f):
    """fields-gate 1절: check {what, how, criteria} non-empty texts and minutes an int ≥ 1 or null; action {what} a non-empty text,
    parts a list of texts, minutes the same; note a text or null — else the letter goes nowhere (its code)."""
    ok_min = lambda m: m is None or (isinstance(m, int) and not isinstance(m, bool) and m >= 1)   # noqa: E731
    text = lambda x: isinstance(x, str) and x.strip() != ""   # noqa: E731
    c, a = f.get("check"), f.get("action")
    if c is not None and not (isinstance(c, dict) and set(c) <= {"what", "how", "criteria", "minutes"} and all(text(c.get(k)) for k in ("what", "how", "criteria")) and ok_min(c.get("minutes"))):
        return False
    if a is not None and not (isinstance(a, dict) and set(a) <= {"what", "parts", "minutes"} and text(a.get("what"))
                              and isinstance(a.get("parts", []), list) and all(text(x) for x in a.get("parts", [])) and ok_min(a.get("minutes"))):
        return False
    return c is not None and (f.get("note") is None or isinstance(f["note"], str))


def as_texts(item):
    """A fielded letter read as the two texts the extractor asks with (its fields kept aside, moved as written into the section); the
    old letter as it is. The context is the letter's facts (context, or situation when that is the facts object)."""
    e = item["exception_record"]
    f = fields_of(e)
    if not f:
        return item
    ctx = e.get("context") if isinstance(e.get("context"), dict) else e.get("situation") if isinstance(e.get("situation"), dict) else {}
    c, a = f["check"] or {}, f["action"] or {}
    sit = " · ".join(x for x in [c.get("what"), c.get("how"), f"기준 {c['criteria']}" if c.get("criteria") else None, f.get("note")] if isinstance(x, str) and x)
    act = " · ".join(x for x in [a.get("what"), f"부품 {', '.join(a['parts'])}" if a.get("parts") else None] if isinstance(x, str) and x)
    return {**item, "exception_record": {"situation": sit, "action": act, "context": ctx, "fields": f}}


def text_sha(item):
    e = item["exception_record"]
    return hashlib.sha256(f"{e['situation']}\n{e['action']}".encode("utf-8")).hexdigest()


def messages(item):
    """The LLM gets the two texts and the eight facts only."""
    e = item["exception_record"]
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"situation": e["situation"], "action": e["action"], "context": e["context"]}, ensure_ascii=False)}]


def fake_extractor(answers):
    """messages → (the fixed answer for the texts' sha256 or None, served model None) — no model call."""
    def ext(msgs):
        u = json.loads(msgs[1]["content"])
        a = answers.get(hashlib.sha256(f"{u['situation']}\n{u['action']}".encode("utf-8")).hexdigest())
        if a is None and u.get("recent_maintenance") and isinstance(answers.get("recent"), dict):   # D-570: a live text is not known beforehand
            a = {"record_id": u["recent_maintenance"][0]["record_id"], **answers["recent"]}
        return (copy.deepcopy(a) if a is not None else None), None
    ext.mode, ext.model, ext.budget = "fake", None, None
    return ext


def read_key(path, name="OPENAI_API_KEY"):
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        if sep and k.strip() == name and v.strip().strip("'\""):
            return v.strip().strip("'\"")
    return None


def _post(url, body, headers, timeout):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST", headers=headers), timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, {}
    except (OSError, ValueError):
        raise ExtractError("extract_network") from None


def strip_fence(s):
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s.strip()


def openai_extractor(key_file, model, base_url, budget, post=None, key_name="OPENAI_API_KEY"):
    """messages → (answer or None, served model): one chat-completions call (temperature 0, JSON answer) under the budget."""
    key = read_key(key_file, key_name)
    if not key:
        raise ExtractError("extract_no_key")
    if model not in USD_PER_1M:
        raise ExtractError("extract_no_price")
    post = post or _post

    def ext(msgs):
        body = json.dumps({"model": model, "messages": msgs, "temperature": 0, "max_tokens": MAX_OUT, "response_format": {"type": "json_object"}},
                          ensure_ascii=False)
        budget.before(model, len(body))
        status, out = post(base_url.rstrip("/") + "/chat/completions", body.encode("utf-8"),
                           {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, 60)
        if status != 200:
            raise ExtractError("extract_http", status=status)
        u = out.get("usage") or {}
        budget.add(model, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        try:
            ans = json.loads(strip_fence(out["choices"][0]["message"]["content"]))
        except (KeyError, IndexError, TypeError, ValueError):
            ans = None
        return (ans if isinstance(ans, dict) else None), (out.get("model") if isinstance(out.get("model"), str) else None)
    ext.mode, ext.model, ext.budget = "real", model, budget
    return ext


def sensors(ctx):
    """The case's snapshot with the Pack's two derived fields made when missing (custom/alarm.py)."""
    s = dict((ctx.get("sensor_values") or {}).get("snapshot") or {})
    if "temp_diff_k" not in s and {"process_temperature_k", "air_temperature_k"} <= set(s):
        s["temp_diff_k"] = round(s["process_temperature_k"] - s["air_temperature_k"], 1)
    if "power_w" not in s and {"torque_nm", "rotational_speed_rpm"} <= set(s):
        s["power_w"] = round(s["torque_nm"] * s["rotational_speed_rpm"] * 2 * 3.141592653589793 / 60)
    return s


def _extra(sentence, said):
    return any(w in sentence and w not in said for w in EFFECT_WORDS)


def strip_extra(ans, item):
    """(the answer without the guide sentences · step lines that state an effect · reason the record does not say, {field: n taken
    out}). Narrow: a sentence goes only when it has one of EFFECT_WORDS and the record's texts and facts have not."""
    if not isinstance(ans, dict):
        return ans, {}
    e = item["exception_record"]
    said = " ".join([e.get("situation") or "", e.get("action") or "", json.dumps(e.get("context") or {}, ensure_ascii=False)])
    out, cut = dict(ans), {}
    if isinstance(ans.get("guide"), str):
        parts = [p for p in re.split(r"(?<=[.!?])\s+", ans["guide"].strip()) if p]
        keep = [p for p in parts if not _extra(p, said)]
        if len(keep) < len(parts):
            out["guide"], cut["guide"] = " ".join(keep), len(parts) - len(keep)
    if isinstance(ans.get("steps"), list):
        keep = [x for x in ans["steps"] if not (isinstance(x, str) and _extra(x, said))]
        if len(keep) < len(ans["steps"]):
            out["steps"], cut["steps"] = keep, len(ans["steps"]) - len(keep)
    return out, cut


def check_answer(ans, snap):
    """None when the answer can go in, else its code."""
    if not isinstance(ans, dict):
        return "no_answer"
    if not isinstance(ans.get("title"), str) or not ans["title"].strip() or len(ans["title"]) > TITLE_MAX:
        return "answer_title"
    if not isinstance(ans.get("guide"), str) or not ans["guide"].strip() or len(ans["guide"]) > GUIDE_MAX:
        return "answer_guide"
    steps = ans.get("steps") if ans.get("steps") is not None else []
    if not isinstance(steps, list) or len(steps) > STEPS_MAX or any(not isinstance(x, str) or not x.strip() or len(x) > STEP_MAX for x in steps):
        return "answer_steps"
    crit = ans.get("criteria")
    if crit is not None and (not isinstance(crit, str) or len(crit) > CRITERIA_MAX):
        return "answer_criteria"
    fields = ans.get("fields") or []
    if not isinstance(fields, list):
        return "answer_field"
    if len(fields) > MAX_FIELDS:
        return "answer_fields_many"
    for f in fields:
        if not isinstance(f, dict) or f.get("field") not in FIELDS or f["field"] not in snap:
            return "answer_field"
        if f.get("side") not in SIDES:
            return "answer_side"
    if not fields and ans.get("site_only") is not True:
        return "answer_no_condition"
    if ans.get("this_equipment_only") not in (None, True, False):
        return "answer_equipment"
    return None


def build(item, ans, base, extractor):
    """(section, relation) of one record and its checked answer."""
    e, rid = item["exception_record"], item["record_id"]
    ctx, snap = e["context"], sensors(e["context"])
    code = (ctx.get("symptom_group") or {}).get("code")
    group = next((g["name"] for g in base["groups"] if g["code"] == code), (ctx.get("symptom_group") or {}).get("name"))
    no = f"EX-{rid}"
    crit = ans.get("criteria")
    section = {"no": no, "app_no": no, "kind": KIND, "grade": GRADE, "name": ans["title"].strip(), "guide": ans["guide"].strip(),
               "steps": [x.strip() for x in ans.get("steps") or []], "criteria": crit.strip() if isinstance(crit, str) and crit.strip() else None,
               "group": group, "minutes": None, "checklist": [], "records": [rid]}
    if e.get("fields"):   # D-554 ④: the letter's fields as written; the loader fills guide · criteria · minutes · steps from check
        f = e["fields"]
        section = {k: v for k, v in section.items() if k not in ("guide", "steps", "criteria")}
        section.update(check=f["check"], **({"action": f["action"]} if f["action"] else {}), note=f.get("note"))
    parts = sorted([f["field"], SIDES[f["side"]], snap[f["field"]]] for f in ans.get("fields") or [])
    when = {"group": group, "sensors": parts} if parts else {"group": group, "sensors": [], "equipment_id": ctx.get("equipment_id")}
    if parts and ans.get("this_equipment_only") is True and ctx.get("equipment_id"):   # D-546 K7: the cause is this machine's own
        when["equipment_id"] = ctx["equipment_id"]
    cause = next((c["code"] for c in base["causes"] if c["name"] == ctx.get("confirmed_cause")), None)
    prev, first = ctx.get("previous_maintenance") or {}, ctx.get("ai_first_check") or {}
    relation = {"id": no, "step": no, "mode": "안내" if parts else "참고", "grade": GRADE, "when": when,
                "where": ";".join(f"{f}{op}{v}" for f, op, v in parts), "cause": cause, "cause_name": ctx.get("confirmed_cause"),
                "records": [rid], "n": 1, "text_sha256": text_sha(item),
                "context": {"equipment_id": ctx.get("equipment_id"), "occurred_at": ctx.get("occurred_at"), "ai_first_check": first.get("section"),
                            "previous_record": prev.get("record_id"), "sensors": {f: snap[f] for f in FIELDS if f in snap}},   # a later narrowing keeps these
                "extractor": extractor}
    return section, relation


def facts_of(e):
    """The letter's facts: its context, or its situation when that is the facts object."""
    return e.get("context") if isinstance(e.get("context"), dict) else e.get("situation") if isinstance(e.get("situation"), dict) else {}


def _said_ok(x):
    """D-642 C: the optional instruction sentence the Pack wrote when it split the case — a text (blank = none) or null."""
    return x.get("instruction") is None or isinstance(x["instruction"], str)


def _check_ok(c):
    ok_min = lambda m: m is None or (isinstance(m, int) and not isinstance(m, bool) and m >= 1)   # noqa: E731
    return (isinstance(c, dict) and set(c) <= {"what", "how", "criteria", "minutes", "instruction"} and isinstance(c.get("what"), str) and c["what"].strip() != ""
            and all(isinstance(c.get(k, ""), str) for k in ("how", "criteria")) and ok_min(c.get("minutes")) and _said_ok(c))


def _action_ok(a):
    ok_min = lambda m: m is None or (isinstance(m, int) and not isinstance(m, bool) and m >= 1)   # noqa: E731
    text = lambda x: isinstance(x, str) and x.strip() != ""   # noqa: E731
    return (isinstance(a, dict) and set(a) <= {"what", "parts", "minutes", "instruction"} and text(a.get("what"))
            and isinstance(a.get("parts", []), list) and all(text(x) for x in a.get("parts", [])) and ok_min(a.get("minutes")) and _said_ok(a))


def as_said(rows):
    """D-642 C: check · action lines as written, a blank or null instruction dropped (no sentence is ever made)."""
    return [{k: v for k, v in r.items() if k != "instruction" or (isinstance(v, str) and v.strip())} for r in rows]


def _exception_ok(x):
    """Contract v10 1절 (D-566): {topic, checks[1 ~ 10], actions[1 ~ 10]} — each check · action as v9's (what required, how · criteria
    texts that may be empty, minutes an int ≥ 1 or null, parts a list of texts); one side empty = 나누지 못함, never moved."""
    return (isinstance(x, dict) and set(x) <= {"topic", "checks", "actions"} and isinstance(x.get("topic", ""), str)
            and isinstance(x.get("checks"), list) and 1 <= len(x["checks"]) <= 10 and all(_check_ok(c) for c in x["checks"])
            and isinstance(x.get("actions"), list) and 1 <= len(x["actions"]) <= 10 and all(_action_ok(a) for a in x["actions"]))


def _item_ok(it):
    """Contract v9 4절 (덧 12): check.what · action.what non-empty texts, how · criteria texts (empty allowed), minutes an int ≥ 1 or
    null, parts a list of texts, topic a text — no other field."""
    ok_min = lambda m: m is None or (isinstance(m, int) and not isinstance(m, bool) and m >= 1)   # noqa: E731
    text = lambda x: isinstance(x, str) and x.strip() != ""   # noqa: E731
    c, a = it.get("check") if isinstance(it, dict) else None, it.get("action") if isinstance(it, dict) else None
    return (isinstance(c, dict) and isinstance(a, dict) and set(it) <= {"check", "action", "topic"} and isinstance(it.get("topic", ""), str)
            and set(c) <= {"what", "how", "criteria", "minutes"} and text(c.get("what")) and all(isinstance(c.get(k, ""), str) for k in ("how", "criteria"))
            and ok_min(c.get("minutes")) and set(a) <= {"what", "parts", "minutes"} and text(a.get("what"))
            and isinstance(a.get("parts", []), list) and all(text(x) for x in a.get("parts", [])) and ok_min(a.get("minutes")))


def item_ids(rid, e):
    """The ids of a letter's items in order: EX-<record> for a letter of one item, else EX-<record>-<n>, n = item_index + 1 (the
    original order; without item_index the list order). item_count, when the letter gives it, is the original number of items."""
    items, idx = e.get("items") or [], e.get("item_index")
    ns = [i + 1 for i in idx] if isinstance(idx, list) and len(idx) == len(items) and all(isinstance(i, int) and i >= 0 for i in idx) else list(range(1, len(items) + 1))
    count = e.get("item_count") if isinstance(e.get("item_count"), int) else None
    one = count == 1 if count else ns == [1]
    return [f"EX-{rid}"] if one else [f"EX-{rid}-{n}" for n in ns]


def item_of(e, rel_id, rid):
    """The item a relation id was made from, or None."""
    if not isinstance(e.get("items"), list):
        return None
    return next((it for it, i in zip(e["items"], item_ids(rid, e)) if i == rel_id), None)


def alarm_parts(ctx, snap):
    """[[field, op, value]] — the alarm's sensor (by its name, or by its unit when it has no name) at this case's own value on the
    alarm's side, or [] when the field or the side is unknown."""
    al = (ctx.get("sensor_values") or {}).get("alarm") or {}
    field, d = ALARM_FIELD.get(al.get("sensor")) or (None if al.get("sensor") else UNIT_FIELD.get(al.get("unit"))), str(al.get("direction") or "")
    op = "<=" if "아래" in d else ">=" if "위" in d else None
    v = snap.get(field, al.get("value")) if field else None
    return [[field, op, v]] if field and op and isinstance(v, (int, float)) and not isinstance(v, bool) else []


def _facts(item, base):
    """The letter's facts for its relation: (ctx, snapshot, group name, alarm parts, cause code)."""
    ctx = facts_of(item["exception_record"])
    snap = sensors(ctx)
    code = (ctx.get("symptom_group") or {}).get("code")
    group = next((g["name"] for g in base["groups"] if g["code"] == code), (ctx.get("symptom_group") or {}).get("name"))
    cause = next((c["code"] for c in base["causes"] if c["name"] == ctx.get("confirmed_cause")), None)
    return ctx, snap, group, alarm_parts(ctx, snap), cause


def _alarm_relation(no, rid, facts, sha, mode):
    """One relation for a moved exception: the alarm's sensor condition (or the same equipment's reference notice) and the facts."""
    ctx, snap, group, parts, cause = facts
    prev, first = ctx.get("previous_maintenance") or {}, ctx.get("ai_first_check") or {}
    when = {"group": group, "sensors": parts} if parts else {"group": group, "sensors": [], "equipment_id": ctx.get("equipment_id")}
    return {"id": no, "step": no, "mode": "안내" if parts else "참고", "grade": GRADE, "when": when,
            "where": ";".join(f"{f}{op}{v}" for f, op, v in parts), "cause": cause, "cause_name": ctx.get("confirmed_cause"),
            "records": [rid], "n": 1, "text_sha256": sha,
            "context": {"equipment_id": ctx.get("equipment_id"), "occurred_at": ctx.get("occurred_at"), "ai_first_check": first.get("section"),
                        "previous_record": prev.get("record_id"), "sensors": {f: snap[f] for f in FIELDS if f in snap}},
            "extractor": {"mode": mode, "model": None}}


EDIT_DROP = ("previous", "check", "action", "guide", "steps", "criteria", "edit_record")   # the loader keeps previous · refills the old fields


def build_edit(item, base):
    """D-560 7 (same-edit.md 2절): (section, relation) of a '같음 + 고침' — the target exception's own lines with the edited check list
    and action list as written (edit_record = this record) and its relation marked edit with this record; None when the graph has no such
    exception (the loader swaps it in: previous · records + 1 · n + 1)."""
    e, rid = item["exception_record"], item["record_id"]
    r = e["exception_review"]
    no, ed = r["match"]["no"], r["edit"]
    sec = next((s for s in base.get("sections") or [] if s.get("no") == no and s.get("kind") == KIND), None)
    rel = next((x for x in base.get("exception_relations") or [] if x.get("id") == no or x.get("step") == no), None)
    if not sec or not rel:
        return None
    section = {**{k: v for k, v in sec.items() if k not in EDIT_DROP}, "checks": as_said(ed["checks"]), "actions": as_said(ed["actions"]), "edit_record": rid}
    return section, {**{k: v for k, v in rel.items() if k != "extractor"}, "records": [rid], "n": 1, "edit": True,
                     "extractor": {"mode": "edit", "model": None}}


def _holds(when, group, snap, equipment):
    """The alarm (its group, its snapshot, its equipment) inside a relation's condition."""
    w = when or {}
    if w.get("group") != group or (w.get("equipment_id") and w.get("equipment_id") != equipment):
        return False
    for f, op, b in w.get("sensors") or []:
        v = snap.get(f)
        if not isinstance(v, (int, float)) or not (v <= b if op == "<=" else v >= b if op == ">=" else False):
            return False
    return True


def _widened(when, snap):
    """The condition with its bounds moved to take the alarm's values in (each sensor of the condition), or None when one is unknown."""
    out = []
    for f, op, b in (when or {}).get("sensors") or []:
        v = snap.get(f)
        if not isinstance(v, (int, float)) or op not in ("<=", ">="):
            return None
        out.append([f, op, max(b, v) if op == "<=" else min(b, v)])
    return {**when, "sensors": out} if out else None


def build_missed(item, base):
    """D-560 #5 (extractor-rows.md 1 · 2절): (code, relation) of a '같음' with no edit on an exception of the graph — 놓침. The target's
    relation of the alarm's kind (same group, its equipment or none) that did not hold → that relation's whole line widened (no edit
    field · records = the base's + this one · n their count); none of that kind → a new relation EX-<record> stepping to the target at the
    alarm's condition. One that holds already moves nothing (same_only)."""
    e, rid = item["exception_record"], item["record_id"]
    no = e["exception_review"]["match"]["no"]
    if not any(s.get("no") == no and s.get("kind") == KIND for s in base.get("sections") or []):
        return "edit_target_missing", None
    facts = _facts(item, base)
    ctx, snap, group, parts, _ = facts
    if not parts:
        return "no_alarm_sensor", None
    eq = ctx.get("equipment_id")
    mine = [r for r in base.get("exception_relations") or [] if r.get("step") == no and r.get("mode") == "안내"
            and (r.get("when") or {}).get("group") == group and (r.get("when") or {}).get("equipment_id") in (None, eq)]
    if any(rid in (r.get("records") or []) for r in mine):
        return "already_in", None
    if any(_holds(r.get("when"), group, snap, eq) for r in mine):
        return "same_only", None
    for r in mine:
        when = _widened(r.get("when"), snap)
        if when and _holds(when, group, snap, eq):
            records = list(dict.fromkeys(list(r.get("records") or []) + [rid]))
            return None, {**{k: v for k, v in r.items() if k not in ("edit", "extractor")}, "when": when,
                          "where": ";".join(f"{f}{op}{v}" for f, op, v in when["sensors"]), "records": records, "n": len(records),
                          "extractor": {"mode": "widen", "model": None}}
    sha = hashlib.sha256(json.dumps({"record": rid, "match": no}, sort_keys=True).encode("utf-8")).hexdigest()
    return None, {**_alarm_relation(f"EX-{rid}", rid, facts, sha, "add"), "step": no}


def build_v10(item, base):
    """D-566 (contract v10): (section, relation) of one exception — one topic, its check list and action list as written."""
    e, rid = item["exception_record"], item["record_id"]
    x = e["exception"]
    no, facts = f"EX-{rid}", _facts(item, base)
    topic = (x.get("topic") or "").strip() or x["actions"][0]["what"].strip()[:15]
    section = {"no": no, "app_no": no, "kind": KIND, "grade": GRADE, "name": (TOPIC + topic)[:TITLE_MAX], "topic": topic, "group": facts[2],
               "minutes": None, "checklist": [], "records": [rid], "checks": as_said(x["checks"]), "actions": as_said(x["actions"]), "note": e.get("note")}
    sha = hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return section, _alarm_relation(no, rid, facts, sha, "v10")


def recent_messages(item, base=None):
    """D-570: the record's texts and its recent maintenance — what the model decides from. D-586: each row names its sections from the
    graph ('did 4.3.2' alone says nothing of the work)."""
    e = item["exception_record"]
    x, ctx = e["exception"], facts_of(e)
    names = {s.get("app_no"): s.get("name") for s in (base or {}).get("sections") or [] if s.get("app_no")}
    rows = [{**{k: r.get(k) for k in ("record_id", "days_before", "did", "checked", "outcome")},
             "sections": {n: names[n] for n in list(r.get("did") or []) + list(r.get("checked") or []) if n in names}} for r in ctx.get("recent_maintenance") or []]
    sit = e.get("situation") if isinstance(e.get("situation"), str) else ""
    text = e.get("text") if isinstance(e.get("text"), str) else ""   # D-578: the technician's own words — the AI split leaves out when it happened
    return [{"role": "system", "content": RECENT_SYSTEM},
            {"role": "user", "content": json.dumps({"text": text, "situation": sit, "action": " · ".join(a["what"] for a in x["actions"]), "topic": x.get("topic"), "checks": x["checks"],
                                                    "actions": x["actions"], "note": e.get("note"), "recent_maintenance": rows}, ensure_ascii=False)}]


def _margin_ok(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def recent_of(ans, rows, group, base):
    """(when.recent + the record, None) from the model's answer — the section and the days from the record's own values; (None, None)
    for a no; (None, code) for an answer off its shape or a record with no check · action section of the group."""
    if not isinstance(ans, dict) or set(ans) - {"recent", "record_id", "margin_days"} or not isinstance(ans.get("recent"), bool):
        return None, "recent_answer"
    if not ans["recent"]:
        return None, None
    row = next((r for r in rows if r.get("record_id") == ans.get("record_id")), None)
    if row is None or not _margin_ok(ans.get("margin_days")) or not isinstance(row.get("days_before"), int):
        return None, "recent_answer"
    mine = {s.get("app_no") for s in base.get("sections") or [] if s.get("group") == group and s.get("kind") in RECENT_KINDS}
    section = next((x for x in list(row.get("did") or []) + list(row.get("checked") or []) if x in mine), None)
    if section is None:
        return None, "recent_no_section"
    return {"section": section, "days": min(row["days_before"] + max(ans["margin_days"], 1), RECENT_DAYS_MAX), "record": row["record_id"]}, None


def recent_relation(rel, got, group, extractor):
    """The relation of a recent-maintenance exception: the same line with the condition of recent-condition.md 1절 (any equipment)."""
    return {**rel, "when": {"group": group, "sensors": [], "recent": {"section": got["section"], "days": got["days"]}}, "mode": "안내",
            "where": f"최근 {got['days']}일 안 {got['section']} 정비", "context": {**rel["context"], "recent_record": got["record"]}, "extractor": extractor}


def build_items(item, base):
    """[(section, relation)] of a letter's items — moved as written, no model."""
    e, rid = item["exception_record"], item["record_id"]
    facts = _facts(item, base)
    out = []
    for it, no in zip(e["items"], item_ids(rid, e)):
        topic = (it.get("topic") or "").strip() or it["action"]["what"].strip()[:15]
        section = {"no": no, "app_no": no, "kind": KIND, "grade": GRADE, "name": (TOPIC + topic)[:TITLE_MAX], "group": facts[2], "minutes": None,
                   "checklist": [], "records": [rid], "check": it["check"], "action": it["action"], "note": e.get("note")}
        sha = hashlib.sha256(json.dumps(it, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        out.append((section, _alarm_relation(no, rid, facts, sha, "items")))
    return out


def make_nodes(base, items, extract):
    """(the node-lines file {sections, exception_relations} or None when nothing went in, {added · skipped [{record_id, code}] · usage}).
    base = the graph in use (read only): its symptom group names · cause codes · the records already in it."""
    have = {r for x in base.get("exception_relations") or [] for r in x.get("records") or []} | set((base.get("source") or {}).get("exception_records") or [])
    sections, relations, added, skipped, notices = [], [], [], [], []
    budget = getattr(extract, "budget", None)
    for item in items:
        rid = item["record_id"]
        e = item["exception_record"]
        review = e.get("exception_review") if isinstance(e.get("exception_review"), dict) else {}
        if review.get("decision") == "same":   # D-560 7: '같음' edits an exception of the graph · #5: with no edit it is 놓침
            ed, m = review.get("edit"), review.get("match") or {}
            if ed is None and m.get("kind") == KIND and m.get("no"):   # D-560 #5: widen its relation or add one (no model)
                code, rel = ("already_in", None) if rid in added else build_missed(item, base)
                if code:
                    skipped.append({"record_id": rid, "code": code})
                    continue
                relations.append(rel)
                added.append(rid)
                continue
            code = ("same_only" if not (isinstance(ed, dict) and m.get("kind") == KIND and m.get("no")) else
                    "letter_fields" if not _exception_ok({"checks": ed.get("checks"), "actions": ed.get("actions")}) or set(ed) - {"checks", "actions"} else
                    "already_in" if rid in added else None)
            built = None if code else build_edit(item, base)
            if code or not built:
                skipped.append({"record_id": rid, "code": code or "edit_target_missing"})
                continue
            sections.append(built[0])
            relations.append(built[1])
            added.append(rid)
            continue
        if "exception" in e:   # D-566 (contract v10): one exception — a topic, a check list, an action list
            code = "letter_fields" if not _exception_ok(e["exception"]) else "already_in" if rid in have or rid in added else None
            if code:
                skipped.append({"record_id": rid, "code": code})
                continue
            s, r = build_v10(item, base)
            rows = facts_of(e).get("recent_maintenance") or []
            if extract is not None and rows:   # D-570: the model decides three things; the record gives the values
                try:
                    ans, served = extract(recent_messages(item, base))
                except BudgetCap:
                    skipped.append({"record_id": rid, "code": "budget_cap"})
                    continue
                got, why = recent_of(ans, rows, s["group"], base)
                if why:
                    notices.append({"record_id": rid, "code": why})
                if got:
                    r = recent_relation(r, got, s["group"], {"mode": extract.mode, "model": served or extract.model})
            sections.append(s)
            relations.append(r)
            added.append(rid)
            continue
        if isinstance(e.get("items"), list):   # D-560: the AI split's items — moved as written, no model
            code = "letter_fields" if not all(_item_ok(it) for it in e["items"]) else "no_items" if not e["items"] else \
                "already_in" if rid in have or rid in added else None
            if code:
                skipped.append({"record_id": rid, "code": code})
                continue
            for s, r in build_items(item, base):
                sections.append(s)
                relations.append(r)
            added.append(rid)
            continue
        if extract is None:   # an old letter needs the model way
            skipped.append({"record_id": rid, "code": "no_extractor"})
            continue
        f = fields_of(item["exception_record"])
        if f is not None and not _fields_ok(f):   # D-554 ④: a fielded letter off fields-gate's shape goes nowhere
            skipped.append({"record_id": rid, "code": "letter_fields"})
            continue
        item = as_texts(item)
        if rid in have or rid in added:
            skipped.append({"record_id": rid, "code": "already_in"})
            continue
        try:
            ans, served = extract(messages(item))
        except BudgetCap:
            skipped.append({"record_id": rid, "code": "budget_cap"})
            continue
        ans, cut = strip_extra(ans, item)   # D-545 덧 3: no sentence the record does not say
        why = check_answer(ans, sensors(item["exception_record"]["context"]))
        if why:
            skipped.append({"record_id": rid, "code": why})
            continue
        s, r = build(item, ans, base, {"mode": extract.mode, "model": served or extract.model})
        sections.append(s)
        relations.append(r)
        added.append(rid)
        if cut:
            notices.append({"record_id": rid, "code": "extra_text_out", "fields": cut})
    rep = {"added": added, "skipped": skipped, "notices": notices, "usage": budget.summary() if budget else None}
    return ({"sections": sections, "exception_relations": relations} if added else None), rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="승인된 예외 상황 기록 → 새 점검 · 조치 노드(C) + 조건 붙은 관계 → 줄 파일(적재기 --nodes)")
    ap.add_argument("--base", required=True, help="지금 쓰는 그래프 release 파일(읽기만 · 증상군 이름 · 원인 코드 · 이미 든 기록)")
    ap.add_argument("--records", required=True, help="앱 /api/internal/exception-records 응답 JSON({items}) 또는 목록")
    ap.add_argument("--out", required=True, help="줄 파일(있으면 쓰지 않음)")
    ap.add_argument("--answers", help="가짜 추출 답 {글 sha256: 답} JSON(AI 호출 0)")
    ap.add_argument("--real", action="store_true", help="실제 호출(오케 go 뒤에만)")
    ap.add_argument("--key-file", help="키 줄이 있는 env 파일(값 출력 0)")
    ap.add_argument("--key-name", default="OPENAI_API_KEY", help="그 파일의 키 변수 이름")
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--base-url", help="provider /v1 주소")
    ap.add_argument("--cap", type=float, default=1.0, help="이 실행 상한(USD)")
    a = ap.parse_args(argv)
    if a.answers and a.real:
        print("exgraph: --answers · --real 중 하나만(둘 다 없으면 items 승인 기록 파일만 옮김)", file=sys.stderr)
        return 2
    out = Path(a.out)
    if out.exists():
        print("exgraph: 쓰지 않음 — --out 파일이 이미 있음", file=sys.stderr)
        return 2
    base = json.loads(Path(a.base).read_text(encoding="utf-8"))
    recs = json.loads(Path(a.records).read_text(encoding="utf-8"))
    items = recs["items"] if isinstance(recs, dict) else recs
    try:
        if a.real:
            if not (a.key_file and a.base_url):
                print("exgraph: --real에는 --key-file · --base-url", file=sys.stderr)
                return 2
            ext = openai_extractor(a.key_file, a.model, a.base_url, Budget(a.cap), key_name=a.key_name)
        elif a.answers:
            ext = fake_extractor(json.loads(Path(a.answers).read_text(encoding="utf-8")))
        else:
            ext = None
        nodes, rep = make_nodes(base, items, ext)
    except ExtractError as e:
        print(f"exgraph: 멈춤 — {e}", file=sys.stderr)
        return 1
    if nodes is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(nodes, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
