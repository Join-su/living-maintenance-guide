"""Graph assembly and the P0 node kinds (spec 9-12). A Runtime is built from a Pack folder that holds pack.yaml and the
execution config (the LLMOps copy), re-checks it (stages 0-3), and returns a compiled LangGraph graph whose node names are
the pack.yaml node ids. Node outputs carry the private keys `_meta` (records for the LLMOps run record: kind, retrievals,
checks, review), `_route` (chosen next node) and `_iter` (visit counts); the runner strips them before printing outputs."""
import copy
import hashlib
import importlib
import inspect
import json
import math
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Annotated, Any, TypedDict

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph

from .. import __version__, trace
from ..ir import load_pack, releases, strict_yaml
from ..ir import options as _options
from . import backends, templates
from .embedding import fake_embedding
from .providers import HttpProvider
from .resources import PgTableResource, ResourceError, TableResource

PRIVATE = ("_meta", "_route", "_iter")


def _public(d):
    return {k: v for k, v in d.items() if k not in PRIVATE} if isinstance(d, dict) else d


UNSAFE_DEFAULT = ["확실히", "틀림없이", "100%", "반드시 원인", "원인으로 확정", "확정적으로"]


def _evidence_ids(value):
    """Items of every `evidence_ids` list inside a value, at any depth (spec 11 supplement 4). Plain items count as they are
    (a number is one id, as before); an object or list item counts as its JSON text."""
    out = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "evidence_ids":   # only a list holds ids; any other value under this key holds none
                if isinstance(v, list):
                    out |= {x if isinstance(x, (str, int, float, bool)) or x is None else json.dumps(x, ensure_ascii=False, sort_keys=True) for x in v}
            else:
                out |= _evidence_ids(v)
    elif isinstance(value, list):
        for v in value:
            out |= _evidence_ids(v)
    return out


AUTO_REVIEW = (("approve", "자동 승인(사람 검토 아님)"), ("escalate", "자동 escalate(답 검사 실패 초안, 사람 검토 아님)"),
               ("reject", "자동 반려(사람 검토 아님)"))


def _auto_decision(params):
    """What an automatic review does (instructions 015b 3, 015c): the first of approve, escalate, reject the node allows, with a
    reason that says no person reviewed it. A node that allows none of them keeps failing on the action check (review_action)."""
    allowed = params.get("actions") or []
    for action, reason in AUTO_REVIEW:
        if action in allowed:
            return {"action": action, "reason": reason}
    return {"action": "approve", "reason": AUTO_REVIEW[0][1]}


def _finite_number(v):
    """A predict input value: int or float, not bool, finite once made a float — NaN, infinite or too large an int is not (EV-003 CHG-P05)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def _sha_or_none(value):
    """sha256 of a value's canonical JSON, or None when it cannot be serialized (the run must not fail over a hash)."""
    if value is None:
        return None
    try:
        return hashlib.sha256(trace.canonical(value)).hexdigest()
    except (TypeError, ValueError):
        return None


def _unfinished(open_spans, failed, exc):
    """Node executions that started but got no task_result before the graph raised (a step with one task raises directly)."""
    for span, name, it, nt0 in open_spans.values():
        failed.setdefault(name, (span, it, nt0, exc))
    open_spans.clear()


def _location(binding):
    """Where a store's release was found this time: its folder, or `postgresql:<schema>.<table>` (no DSN)."""
    return binding["path"] if binding.get("path") else f"postgresql:{binding.get('schema')}.{binding.get('table')}"


def _decide_normalize(path, kind, name, spec, answer):
    """One Jev answer -> the normal form (spec 7.1, judgement table in 11 — the fixed table wins over the 7.1 example for noul)."""
    if not isinstance(answer, dict):
        raise NodeError(path, kind, "decide_answer", f"decision 응답에 질문 '{name}'의 답이 없습니다")
    typ, crit = spec["type"], spec.get("criteria")
    if typ == "noul":
        p = answer.get("noul")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            raise NodeError(path, kind, "decide_answer", f"질문 '{name}'의 noul 확률이 0 ~ 1 숫자가 아닙니다: {p!r}")
        p = float(p)
        return {"value": p >= 0.5, "probabilities": {"true": round(p, 6), "false": round(1 - p, 6)}, "confidence": round(max(p, 1 - p), 6),
                "confidence_source": "runtime_derived", "confidence_method": "max_probability"}
    value = answer.get(typ)
    if typ == "choice" and not (isinstance(crit, dict) and value in crit):
        raise NodeError(path, kind, "decide_answer", f"질문 '{name}'의 choice가 선택지에 없습니다: {value!r}")
    if typ == "score" and not (isinstance(value, int) and not isinstance(value, bool) and isinstance(crit, list) and 1 <= value <= len(crit)):
        raise NodeError(path, kind, "decide_answer", f"질문 '{name}'의 score가 1 ~ {len(crit) if isinstance(crit, list) else '?'} 정수가 아닙니다: {value!r}")
    conf = answer.get("confidence")
    if conf is not None:
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not (0 <= conf <= 1):   # NaN fails this too
            raise NodeError(path, kind, "decide_answer", f"질문 '{name}'의 confidence가 0 ~ 1 숫자가 아닙니다: {conf!r}")
        conf = float(conf)
    return {"value": value, "probabilities": dict(answer.get("probabilities") or {}), "confidence": conf,
            "confidence_source": "provider" if conf is not None else "provider_absent"}


def _last_response(provider):
    """The provider's raw response for the call just made in this thread, when the provider keeps one (HttpProvider)."""
    fn = getattr(provider, "last_response", None)
    return fn() if callable(fn) else None


def _last_key_slot(provider):
    """EV-013: the key slot (1 · 2) of the call just made in this thread, when the provider names a second key; else None."""
    fn = getattr(provider, "last_key_slot", None)
    return fn() if callable(fn) else None


def _keep_key_slot(provider, meta):
    """EV-013 CHG-F04: put the slot of the call just made into the node meta (the runner line and node_ended carry it); no slot
    (one key, or another provider) leaves the meta without the key, as before. A retry keeps only its own slot."""
    slot = _last_key_slot(provider)
    if slot is None:
        meta.pop("key_slot", None)
    else:
        meta["key_slot"] = slot


def _llm_on(params, models=None):
    """EV-002: the strict · seed values an llm node turns on — `strict: true` and a seed; `strict: false` is the same as no key.
    0.14.0 (D-475): `reasoning_effort` too, only when models.yaml lists the node's model under its provider's `reasoning` (a model
    not listed never gets it — see _reasoning_skipped)."""
    on = {}
    if params.get("strict") is True:
        on["strict"] = True
    if "seed" in params:
        on["seed"] = params["seed"]
    if "reasoning_effort" in params and _reasoning_listed(params, models):
        on["reasoning_effort"] = params["reasoning_effort"]
    return on


def _reasoning_listed(params, models):
    """Whether models.yaml lists the node's model under its provider's `models.reasoning` (the chat models that take reasoning_effort)."""
    prov = ((models or {}).get("providers") or {}).get(params.get("provider")) or {}
    return params.get("model") in (((prov.get("models") or {}).get("reasoning")) or [])


def _reasoning_skipped(params, models):
    """The reasoning_effort a node set but did not send (its model is not in the `reasoning` list), else None."""
    if "reasoning_effort" in params and not _reasoning_listed(params, models):
        return params["reasoning_effort"]
    return None


def _takes(fn, name):
    """Whether a provider method takes the keyword `name` (by name or **kw) — a fake with a fixed signature may not."""
    try:
        ps = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(q.name == name or q.kind is inspect.Parameter.VAR_KEYWORD for q in ps)


class NodeError(RuntimeError):
    """Common node error (spec 9): {node_path, kind, code, message, retryable, attempt}."""

    def __init__(self, node_path, kind, code, message, retryable=False, attempt=1):
        self.info = {"node_path": node_path, "kind": kind, "code": code, "message": message, "retryable": retryable, "attempt": attempt}
        super().__init__(f"{node_path} [{code}] {message}")


class ReviewStop(Exception):
    """The run stops right before a human_review node (1st upgrade: no waiting/resume). Carries the review subject value."""

    def __init__(self, node, subject_key, value):
        self.node, self.subject_key, self.value = node, subject_key, value
        super().__init__(f"검토 전 멈춤: {node}")


def runtime_digest():
    h = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".json"):
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def _merge_dict(a, b):
    return {**(a or {}), **(b or {})}


def _add_counts(a, b):
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v
    return out


def _sort_key(fields):
    def key(item):
        out = []
        for f in fields:
            desc = f.endswith("_desc")
            name = f[:-5] if desc else f
            v = item.get(name) if isinstance(item, dict) else None
            if isinstance(v, (int, float)):
                out.append((0, -v if desc else v, ""))
            else:
                out.append((1, 0, str(v)))
        return out
    return key


def _append(order_by):
    def reducer(a, b):
        merged = list(a or []) + list(b or [])
        return sorted(merged, key=_sort_key(order_by))
    return reducer


def _merge_meta(a, b):
    out = dict(a or {})
    for k, v in (b or {}).items():
        out.setdefault(k, []).append(v)
    return out


EDIT_PATH = re.compile(r"[^.\[\]]+(?:\.[^.\[\]]+|\[\d+\])+")
EDIT_STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")   # the same notation as the suite check paths (llmops/suites.py STEP)


def _edit_path(key):
    """(editable key, [name | index, ...]) of a nested review edit key such as `answer.uncertainty` or `answer.steps[0]`."""
    if not isinstance(key, str) or not EDIT_PATH.fullmatch(key):
        return None, []
    steps = [int(i) if i else name for name, i in EDIT_STEP.findall(key)]
    return steps[0], steps[1:]


def _set_at(value, steps, new):
    """Set one place: missing object keys are made, a list position past the end is an error."""
    cur = value
    for i, s in enumerate(steps):
        last = i == len(steps) - 1
        if isinstance(s, int):
            if not isinstance(cur, list):
                raise TypeError(f"[{s}] 앞이 목록이 아닙니다")
            if s >= len(cur):
                raise IndexError(f"목록 길이 {len(cur)}를 넘는 위치 [{s}]")
            if last:
                cur[s] = new
            else:
                cur = cur[s]
        else:
            if not isinstance(cur, dict):
                raise TypeError(f"'{s}' 앞이 객체가 아닙니다")
            if last:
                cur[s] = new
            else:
                cur = cur.setdefault(s, {})
    return value


class Runtime:
    def __init__(self, pack_dir, provider=None, env=None, config_name="execution-config.yaml", pg_lookup=True):
        self.dir = Path(pack_dir).resolve()
        if str(self.dir) not in sys.path:   # custom functions and `module:Class` backends live in the Pack
            sys.path.insert(0, str(self.dir))
        for name in [m for m in sys.modules if m == "custom" or m.startswith("custom.")]:   # a new Runtime always loads the Pack's current custom code
            del sys.modules[name]
        importlib.invalidate_caches()
        res = load_pack(self.dir)
        if not res.ok:
            raise RuntimeError("호환 안 됨 — " + " / ".join(f"pack.yaml {e['line']}줄: {e['message']}" if e["line"] else e["message"] for e in res.errors))
        self.doc, self.revision, self.raw_files = res.doc, res.revision, res.raw_files
        cfg = releases.load_execution_config(self.dir / config_name, self.doc, pg_lookup=pg_lookup)   # stage 3: releases still match their manifests
        self.cfg, self.bindings, self.entry = cfg, cfg["bindings"], cfg["entrypoint"]
        self.graph_doc = self.doc["graphs"][self.entry]
        self.ep = next(e for e in self.doc["entrypoints"] if e["graph"] == self.entry)
        models_file = self.dir / "models.yaml"
        self.models = strict_yaml.load(models_file.read_text(encoding="utf-8"))[0] if models_file.is_file() else {}
        self.provider = provider or HttpProvider(self.models, env)
        self.schemas = {n: json.loads((self.dir / rel).read_text(encoding="utf-8")) for n, rel in self.doc["schemas"].items()}
        self.state_types = {k: (t if isinstance(t, str) else t["type"]) for k, t in self.graph_doc["state"].items()}
        self.input_key = next((k for k, t in self.state_types.items() if t == self.ep["input"]), None)
        self.output_key = next((k for k, t in self.state_types.items() if t == self.ep["output"]), None)
        self._stores, self._models = {}, {}
        self._store_lock = threading.Lock()   # parallel branches opening the same store get one object (one connection per store)
        self._captures = {}   # (node id, visit) → raw values a node saw (rendered prompt, provider response, query) for run()'s events

    # ------------------------------------------------------------------ resources
    def store(self, name):
        with self._store_lock:
            if name not in self._stores:
                b = dict(self.bindings["stores"][name], name=name)
                self._stores[name] = backends.open_store(b, self.doc["stores"][name])
            return self._stores[name]

    def close(self):
        """Close the stores' database connections (PostgreSQL backends); file stores hold none. Returns how many were closed."""
        with self._store_lock:
            stores, self._stores = list(self._stores.values()), {}
        closed = 0
        for s in stores:
            reader = getattr(s, "reader", None)
            if reader is not None and reader.close():
                closed += 1
        return closed

    def model(self, name):
        if name not in self._models:
            b = self.bindings["models"][name]
            manifest = json.loads((Path(b["path"]) / "manifest.json").read_text(encoding="utf-8"))
            self._models[name] = backends.open_model(b, manifest)
        return self._models[name]

    def custom_fn(self, fn):
        mod, func = fn.split(":")
        if str(self.dir) not in sys.path:
            sys.path.insert(0, str(self.dir))
        return getattr(importlib.import_module(mod), func)

    def manifest(self):
        import importlib.metadata as md
        import platform
        dists = sorted(f"{d.metadata['Name']}=={d.version}" for d in md.distributions() if d.metadata.get("Name"))
        return {"pack_revision": self.revision, "runtime": {"version": __version__, "digest": runtime_digest()},
                "pack_env": {"python": platform.python_version(), "dists_sha256": hashlib.sha256("\n".join(dists).encode()).hexdigest()},
                "execution_config": {"sha256": self.cfg["config_sha256"], "config_version": self.cfg["config"].get("config_version"),
                                     "entrypoint": self.entry, "backends": {k: v["backend"] for k, v in self.bindings["stores"].items()}},
                # release_id·hash identify a release; location is where it was found this time (spec v0.5 3.2·3.4)
                "model_releases": {k: {**{kk: v.get(kk) for kk in ("release_id", "manifest_sha256", "artifact_sha256", "format", "feature_schema_version")}, "location": v["path"],
                                       **({"threshold_config_sha256": v["threshold"]["sha256"]} if v.get("threshold") else {})} for k, v in self.bindings["models"].items()},
                "evidence_index_releases": {k: {**{kk: v.get(kk) for kk in ("release_id", "manifest_sha256", "index_sha256", "embedding")}, "location": _location(v),
                                                **self._recipe_fields(k)} for k, v in self.bindings["stores"].items() if v["type"] == "vector"},
                "table_snapshots": {k: {"release_id": v.get("release_id"), "manifest_sha256": v.get("manifest_sha256"), "sha256": v.get("sha256"),
                                        "path_name": Path(v["path"]).name if v.get("path") else v.get("table"), "location": str(Path(v["path"]).parent) if v.get("path") else _location(v)}
                                    for k, v in self.bindings["stores"].items() if v["type"] == "table"}}

    def _check_predict(self):
        """EV-003 CHG-P04: every predict node of the entry graph gives its model all the release's input fields, and an output that
        names alarm · threshold has a release with threshold_config. Raised as ReleaseError (a ValueError): run() reports load_failed."""
        for n in self.graph_doc["nodes"]:
            if n["kind"].split("@")[0] != "predict":
                continue
            p = n["params"]
            b = self.bindings["models"][p["model"]]
            feats = p["features"]
            if set(feats) == {"from"}:   # {from: <state key>}: the fields that state type's schema declares, when it is a schema
                t = self.state_types.get(feats["from"])
                have = (self.schemas.get(t) or {}).get("properties") if isinstance(t, str) else None
            else:
                have = feats
            if isinstance(have, dict):
                missing = [f for f in b["feature_fields"] if f not in have]
                if missing:
                    raise releases.ReleaseError(f"node '{n['id']}'의 features에 모델 입력 칸이 없습니다: {', '.join(missing)} (모델 release {b['release_id']})")
            props = self.schemas[p["output"]].get("properties") or {}
            if ("alarm" in props or "threshold" in props) and not b.get("threshold"):
                raise releases.ReleaseError(f"node '{n['id']}'의 output {p['output']}이 alarm · threshold를 쓰는데 모델 release {b['release_id']}에 threshold_config가 없습니다")

    # ------------------------------------------------------------------ graph
    def build(self, review=None):
        """review: {"policy": "fixture"|"stop_before"|"auto_approve", "decision": {...}} for human_review nodes."""
        self.review = review or {"policy": "stop_before"}
        self._check_predict()
        fields = {}
        for k, t in self.graph_doc["state"].items():
            if isinstance(t, dict) and t.get("merge") == "append":
                fields[k] = Annotated[Any, _append(t["order_by"])]
            elif isinstance(t, dict) and t.get("merge") == "merge_dict":
                fields[k] = Annotated[Any, _merge_dict]
            else:
                fields[k] = Any
        fields.update({"_meta": Annotated[dict, _merge_meta], "_route": Annotated[dict, _merge_dict], "_iter": Annotated[dict, _add_counts]})
        S = TypedDict("PackState", fields, total=False)
        g = StateGraph(S)
        nodes = {n["id"]: n for n in self.graph_doc["nodes"]}
        loops = {e["to"]: e["max_iterations"] for e in self.graph_doc["edges"] if isinstance(e, dict) and "from" in e}
        plain, joins = {}, []
        for e in self.graph_doc["edges"]:
            if isinstance(e, list):
                plain.setdefault(e[0], []).append(e[1])
            elif "join" in e:
                joins.append(e)
            else:
                plain.setdefault(e["from"], []).append(e["to"])
        for nid, n in nodes.items():
            g.add_node(nid, self._wrap(n, loops.get(nid)))
        conv = lambda x: END if x == "END" else (START if x == "START" else x)
        for nid, n in nodes.items():
            kind = n["kind"].split("@")[0]
            p = n.get("params") or {}
            deviations = []
            if kind == "router":
                deviations = list(p["cases"].values()) + [p["default"]]
            elif kind == "human_review":
                deviations = list(p["routes"].values())
            elif kind == "validate" and p.get("on_fail"):
                deviations = [p["on_fail"]]
            then = (n.get("on_error") or {}).get("then", "fail")
            if then.startswith("route:"):
                deviations.append(then[6:])
            if deviations:
                targets = sorted({conv(x) for x in deviations + plain.get(nid, [])} | ({END} if not plain.get(nid) and kind not in ("router", "human_review") else set()))
                g.add_conditional_edges(nid, self._chooser(nid, [conv(x) for x in plain.get(nid, [])]), targets)
            else:
                for t in plain.get(nid, []):
                    g.add_edge(nid, conv(t))
        for s in plain.get("START", []):
            g.add_edge(START, conv(s))
        for j in joins:
            g.add_edge(list(j["join"]), conv(j["to"]))
        return g.compile()

    def _chooser(self, nid, fallthrough):
        def choose(state):
            r = (state.get("_route") or {}).get(nid)
            if r:
                return [END if x == "END" else x for x in (r if isinstance(r, list) else [r])]
            return fallthrough or [END]
        return choose

    def _wrap(self, n, loop_max):
        nid, kind = n["id"], n["kind"].split("@")[0]
        impl = getattr(self, "_n_" + kind)
        on_error = n.get("on_error") or {}
        retries, then = int(on_error.get("retry", 0)), on_error.get("then", "fail")

        def run(state):
            visits = (state.get("_iter") or {}).get(nid, 0) + 1
            if loop_max is not None and visits > loop_max + 1:
                raise NodeError(f"{self.entry}.{nid}", n["kind"], "max_iterations", f"반복 한도 {loop_max}회를 넘었습니다")
            meta = {"kind": n["kind"]}
            reads = {k: copy.deepcopy(state.get(k)) for k in n.get("reads", [])}
            last = None
            for attempt in range(1, retries + 2):
                try:
                    out, route = impl(n, state, reads, meta)
                    break
                except ReviewStop:
                    raise
                except Exception as e:  # noqa: BLE001 - node code and providers may raise anything
                    last = e if isinstance(e, NodeError) else NodeError(f"{self.entry}.{nid}", n["kind"], type(e).__name__, str(e)[:400], attempt <= retries, attempt)
                    last.info["attempt"] = attempt
            else:
                if then.startswith("route:"):
                    meta["error"] = last.info
                    self._captures[(nid, visits)] = meta.pop("_cap", {})
                    return {"_meta": {nid: meta}, "_route": {nid: then[6:]}, "_iter": {nid: 1}}
                meta.pop("_cap", None)
                raise last
            self._captures[(nid, visits)] = meta.pop("_cap", {})
            allowed = set(n.get("writes", []))
            if kind == "human_review":   # edits of editable keys and the revalidated nodes' results (spec 11 human_review)
                p = n.get("params") or {}
                allowed |= set(p.get("editable", []))
                for vid in p.get("revalidate", []):
                    allowed |= set(next((x.get("writes", []) for x in self.graph_doc["nodes"] if x["id"] == vid), []))
            extra = set(out) - allowed
            if extra:
                raise NodeError(f"{self.entry}.{nid}", n["kind"], "undeclared_write", f"선언하지 않은 state를 썼습니다: {sorted(extra)}")
            checks = self._expect_checks(n, state, out)
            if checks:
                meta["expect_checks"] = checks
            return {**out, "_meta": {nid: meta}, "_route": {nid: route}, "_iter": {nid: 1}}
        return run

    # ------------------------------------------------------------------ expect.checks (spec 15.4)
    def _expect_checks(self, n, state, out):
        exp = n.get("expect") or {}
        res = []
        first = next(iter(out.values()), None) if out else None
        merged = {**state, **out}
        for chk in exp.get("checks") or []:
            name, arg = next(iter(chk.items()))
            ok, detail = None, ""
            if name == "min_items":
                val = first if isinstance(first, list) else []
                ok, detail = len(val) >= int(arg), f"{len(val)}개"
            elif name == "max_items":
                val = first if isinstance(first, list) else []
                ok, detail = len(val) <= int(arg), f"{len(val)}개"
            elif name == "field_equals":
                want = templates.render(arg.get("value"), merged)
                items = first if isinstance(first, list) else [first]
                path = arg.get("path", "").removeprefix("item.")
                vals = [templates.lookup(it, "." + path) if isinstance(it, dict) else None for it in items]
                ok, detail = bool(vals) and all(v == want for v in vals), f"{vals} vs {want}"
            elif name == "values_in_allowed":
                items = first if isinstance(first, list) else [first]
                path = arg.get("path", "").removeprefix("item.")
                vals = [templates.lookup(it, "." + path) if isinstance(it, dict) else it for it in items]
                ok, detail = all(v in arg.get("allowed", []) for v in vals), str(vals)
            elif name == "output_schema_valid":
                errs = []
                for k, v in out.items():
                    t = self.state_types.get(k)
                    if t in self.schemas:
                        errs += [e.message for e in Draft202012Validator(self.schemas[t]).iter_errors(v)]
                ok, detail = not errs, "; ".join(errs)[:200]
            else:
                ok, detail = None, "지원하지 않는 검사"
            res.append({"check": name, "passed": ok, "detail": detail})
        return res

    # ------------------------------------------------------------------ node kinds (P0)
    def _n_router(self, n, state, reads, meta):
        p = n["params"]
        val = templates.by_path(state, p["by"])
        key = str(val).lower() if isinstance(val, bool) else str(val)
        target = p["cases"].get(key, p["default"])
        meta["route"] = {"value": key, "target": target}
        return {}, target

    def _n_predict(self, n, state, reads, meta):
        p = n["params"]
        path = f"{self.entry}.{n['id']}"
        b = self.bindings["models"][p["model"]]
        src = templates.render(p["features"], state)
        feats = state.get(src["from"]) if isinstance(src, dict) and "from" in src else src
        if not isinstance(feats, dict):
            raise NodeError(path, n["kind"], "features_not_object", "predict features가 객체가 아닙니다")
        fields = b["feature_fields"]   # EV-003 CHG-P05: every input field, a finite number — no 0 for a missing one
        missing = [f for f in fields if feats.get(f) is None]
        if missing:
            raise NodeError(path, n["kind"], "feature_missing", f"모델 입력 칸이 없습니다: {', '.join(missing)}")
        bad = [f"{f}({type(feats[f]).__name__})" for f in fields if not _finite_number(feats[f])]
        if bad:
            raise NodeError(path, n["kind"], "feature_not_number", f"모델 입력 칸이 숫자가 아닙니다: {', '.join(bad)}")
        try:
            model = self.model(p["model"])
        except backends.ModelLoadError as e:   # EV-003 CHG-P06
            raise NodeError(path, n["kind"], "model_load", str(e))
        try:
            probs = model.predict([float(feats[f]) for f in fields])
        except backends.ModelOutputError as e:
            raise NodeError(path, n["kind"], "model_output", str(e))
        classes = b["classes"]
        i = max(range(len(probs)), key=lambda k: probs[k])   # the first largest, before rounding (as 0.10.1)
        rounded = [round(v, 6) for v in probs]
        full = {"type": classes[i], "probability": rounded[i], "probabilities": dict(zip(classes, rounded)), "model_release": b.get("release_id")}
        th = b.get("threshold")
        if th:   # EV-003 CHG-P03
            full["alarm"] = full["type"] not in th["normal_classes"] and full["probability"] >= th["min_probability"]
            full["threshold"] = th["min_probability"]
        schema = self.schemas[p["output"]]
        props = schema.get("properties") or {}
        value = {"type": full["type"], "probability": full["probability"]}   # EV-003 CHG-P07 (N1): more only when the output schema names it
        value.update({k: full[k] for k in ("probabilities", "model_release", "alarm", "threshold") if k in props and k in full})
        errs = [e.message for e in Draft202012Validator(schema).iter_errors(value)]
        if errs:
            raise NodeError(path, n["kind"], "output_schema", f"예측 출력이 {p['output']} 형식에 맞지 않습니다: {errs[0][:160]}")
        meta["model_release"] = b.get("release_id")
        meta["_cap"] = {"features": feats}
        meta["probabilities"] = full["probabilities"]
        if th:
            meta["alarm"], meta["threshold"] = full["alarm"], full["threshold"]
        return {n["writes"][0]: value} if n.get("writes") else {}, None

    def _n_retrieve(self, n, state, reads, meta):
        if n["kind"] == "retrieve@2":
            return self._n_retrieve2(n, state, reads, meta)
        p = n["params"]
        name = p["store"]
        sdoc = self.doc["stores"][name]
        b = self.bindings["stores"][name]
        fb = p.get("fallback") or {}
        rel = b.get("release_id")
        def run_once(drop):
            if sdoc["type"] == "vector":
                flt = {k: v for k, v in (templates.render(p.get("filter") or {}, state)).items() if k not in drop}
                q = templates.render(p["query"], state)
                emb = sdoc["index"]["embedding"]
                text = q if isinstance(q, str) else json.dumps(q, ensure_ascii=False)
                if b.get("embedding_mode") == "fake":   # pack resource contract E-1: the runtime's own deterministic embedding
                    qvec = fake_embedding(text)
                else:
                    try:
                        qvec = self.provider.embed(emb["provider"], emb["model"], [text])[0]
                    finally:   # E5-N1: kept when the call failed too
                        _keep_key_slot(self.provider, meta)
                if b.get("embedding_dim") and len(qvec) != int(b["embedding_dim"]):
                    raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "embedding_mismatch",
                                    f"검색어 벡터 차원 {len(qvec)}이 release '{rel}'의 차원 {b['embedding_dim']}과 다릅니다")
                raw = None if b.get("embedding_mode") == "fake" else _last_response(self.provider)   # fake mode: no provider call, no tokens
                cap.update(query=q, filter=flt, embedding_model=emb["model"],
                           embed_tokens=((raw or {}).get("usage") or {}).get("prompt_tokens") if isinstance(raw, dict) else None)
                hits = self.store(name).search(qvec, int(p.get("top_k", 4)), p.get("search", "similarity"), p.get("mmr"), p.get("score_threshold"), flt)
                return [{"source_type": (h.get("metadata") or {}).get("evidence_type", "vector"), "store": name, "id": h["id"], "index_release": rel,
                         "score": h["score"], "text": h.get("text", "")} for h in hits]
            where = {k: v for k, v in (templates.render(p.get("where") or {}, state)).items() if k not in drop}
            cap.update(query=where, filter=where)
            rows = self.store(name).query(where, p.get("select"), int(p.get("limit", 5)))
            key = sdoc.get("key")
            return [{"source_type": name, "store": name, "id": str(r.get(key, i)), "score": 1.0, "text": json.dumps(r, ensure_ascii=False)} for i, r in enumerate(rows)]
        cap = {"embedding_mode": b.get("embedding_mode") or ((self.cfg["config"].get("stores") or {}).get(name, {}).get("embedding") or {}).get("mode") or (b.get("embedding") or {}).get("mode")}
        meta["_cap"] = cap   # the last search's query and filter (a fallback search replaces them)
        hits, used, reason = run_once(set()), False, None
        if fb and len(hits) < int(fb.get("min_results", 1)):
            reason = f"결과 {len(hits)}개 < {fb.get('min_results', 1)} → {fb.get('relax', [])} 조건을 빼고 다시 검색"
            hits, used = run_once(set(fb.get("relax", []))), True
        for h in hits:
            h["fallback_used"] = used
        meta["retrievals"] = {"store": name, "index_release": rel, "results": [{"id": h["id"], "score": h["score"], "source_type": h["source_type"]} for h in hits],
                              "fallback_used": used, "fallback_reason": reason}
        return {n["writes"][0]: hits} if n.get("writes") else {}, None

    def _recipe_fields(self, name):
        """EV-006 CHG-R11: a vector release built from a recipe (manifest index_recipe_sha256) adds corpus_release · index_recipe ·
        index_recipe_sha256 · build_sha256 · builder to its run manifest entry; any other release keeps its five keys (PRES-R12)."""
        b = self.bindings["stores"][name]
        if not b.get("path") and not (b.get("backend") == "pgvector" and b.get("manifest")):   # EV-008 CHG-P01 5: a pg release keeps its manifest
            return {}
        man = self._release_manifest(name)
        if not man.get("index_recipe_sha256"):
            return {}
        return {k: man.get(k) for k in ("corpus_release", "index_recipe", "index_recipe_sha256", "build_sha256", "builder")}

    def _release_manifest(self, name):
        """The bound release's manifest (retrieve@2 reads index_recipe_sha256 · rerank_norm from it), read once: a flat-local release's
        manifest.json, a pgvector release's data_release.manifest (EV-008 CHG-P01 5)."""
        cache = self.__dict__.setdefault("_manifests", {})
        if name not in cache:
            b = self.bindings["stores"][name]
            path = b.get("path")
            cache[name] = (json.loads((Path(path) / "manifest.json").read_text(encoding="utf-8")) if path
                           else dict(b.get("manifest") or {}) if b.get("backend") == "pgvector" else {})
        return cache[name]

    def _n_retrieve2(self, n, state, reads, meta):
        """retrieve@2 (EV-006 CHG-R01 ~ R04 · R09; flat-local and, from 0.12.1, pgvector stores — EV-008 CHG-P01): filter → lexical (BM25) and/or vector →
        RRF → top_k → sensor re-rank → final score · threshold, fallback as @1. One embedding call per search for vector · hybrid (fake
        mode: none), none for lexical."""
        p = n["params"]
        name = p["store"]
        sdoc = self.doc["stores"][name]
        b = self.bindings["stores"][name]
        rel = b.get("release_id")
        if b.get("backend") not in releases.R2_BACKENDS:
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "backend_unsupported",
                            f"retrieve@2는 flat-local · pgvector만 씁니다(store '{name}': {b.get('backend')})")
        man = self._release_manifest(name)
        fb = p.get("fallback") or {}
        method = p.get("method", "vector")
        cap = {"embedding_mode": b.get("embedding_mode") or ((self.cfg["config"].get("stores") or {}).get(name, {}).get("embedding") or {}).get("mode") or (b.get("embedding") or {}).get("mode")}
        meta["_cap"] = cap

        def run_once(drop):
            flt = {k: v for k, v in (templates.render(p.get("filter") or {}, state)).items() if k not in drop}
            q = templates.render(p["query"], state)
            text = q if isinstance(q, str) else json.dumps(q, ensure_ascii=False)
            emb = sdoc["index"]["embedding"]
            qvec, tokens = None, None
            if method != "lexical":
                if b.get("embedding_mode") == "fake":
                    qvec = fake_embedding(text)
                else:
                    try:
                        qvec = self.provider.embed(emb["provider"], emb["model"], [text])[0]
                    finally:   # E5-N1: kept when the call failed too
                        _keep_key_slot(self.provider, meta)
                if b.get("embedding_dim") and len(qvec) != int(b["embedding_dim"]):
                    raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "embedding_mismatch",
                                    f"검색어 벡터 차원 {len(qvec)}이 release '{rel}'의 차원 {b['embedding_dim']}과 다릅니다")
                raw = None if b.get("embedding_mode") == "fake" else _last_response(self.provider)
                tokens = ((raw or {}).get("usage") or {}).get("prompt_tokens") if isinstance(raw, dict) else None
            cap.update(query=q, filter=flt, embedding_model=emb["model"] if method != "lexical" else None, embed_tokens=tokens)
            rr = p.get("rerank")
            rq = templates.render(rr["query"], state) if rr else None
            try:
                hits = self.store(name).search2(qvec, text, p, flt, man.get("rerank_norm"), rq)
            except backends.RerankInvalid as e:   # D-273 R-1
                raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "rerank_invalid", str(e))
            out = []
            for h in hits:
                md_all = h.pop("_metadata_all", {})
                out.append({"source_type": md_all.get("evidence_type", "vector"), "store": name, "id": h["id"], "index_release": rel,
                            "score": h["score"], "text": h.get("text", ""), "locator": h.get("locator"), "ranks": h["ranks"],
                            "metadata": h["metadata"]})
            return out
        hits, used, reason = run_once(set()), False, None
        if fb and len(hits) < int(fb.get("min_results", 1)):
            reason = f"결과 {len(hits)}개 < {fb.get('min_results', 1)} → {fb.get('relax', [])} 조건을 빼고 다시 검색"
            hits, used = run_once(set(fb.get("relax", []))), True
        for h in hits:
            h["fallback_used"] = used
        meta["retrievals"] = {"store": name, "index_release": rel, "method": method, "index_recipe_sha256": man.get("index_recipe_sha256"),
                              "results": [{"id": h["id"], "score": h["score"], "source_type": h["source_type"], "locator": h["locator"], "ranks": h["ranks"]}
                                          for h in hits],
                              "fallback_used": used, "fallback_reason": reason}
        return {n["writes"][0]: hits} if n.get("writes") else {}, None

    def _n_llm(self, n, state, reads, meta):
        p = n["params"]
        prompt = templates.render((self.dir / p["prompt"]).read_text(encoding="utf-8"), state)
        schema = None if p["output"] == "text" else self.schemas[p["output"]]
        on = _llm_on(p, self.models)   # EV-002 CHG-L04: only a node that turns strict or seed on passes them; others call with the six positions
        if "reasoning_effort" in on and not _takes(self.provider.chat, "reasoning_effort"):
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "provider_option",
                            "provider가 reasoning_effort를 받지 않습니다(packrt 0.14.0 칸 — 가짜 provider chat에 reasoning_effort=None을 더해야 합니다)")
        try:
            text, usage = self.provider.chat(p["provider"], p["model"], [{"role": "user", "content": prompt}], schema, p.get("temperature"), p.get("max_tokens"), **on)
        finally:   # E5-N1: this attempt's slot, also when the call failed (a retry never keeps the previous attempt's)
            _keep_key_slot(self.provider, meta)
        raw = _last_response(self.provider)
        meta["_cap"] = {"prompt": prompt, "provider_raw": raw}   # what was sent and what came back (trace contract 4)
        meta.update(provider=p["provider"], model=p["model"], tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"))
        for src, key in (("system_fingerprint", "system_fingerprint"), ("model", "response_model")):
            meta.pop(key, None)   # a retry records its own answer, not the previous attempt's
            if on and isinstance(raw, dict) and raw.get(src) is not None:   # EV-002 CHG-L05: which backend and model answered
                meta[key] = raw[src]
        if schema is None:
            value = text
        else:
            path, strict = f"{self.entry}.{n['id']}", on.get("strict") is True
            choices = raw.get("choices") if strict and isinstance(raw, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            cut = " (finish_reason length — max_tokens에 닿음)" if choice.get("finish_reason") == "length" else ""
            if strict and text is None:   # EV-002 CHG-L06: a strict answer the model refused carries no content
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                if message.get("refusal"):
                    raise NodeError(path, n["kind"], "output_refused", f"모델이 답을 거부했습니다: {str(message['refusal'])[:120]}")
                raise NodeError(path, n["kind"], "output_not_json", f"모델 출력이 비었습니다{cut}")
            try:
                value = json.loads(text)
            except ValueError:
                raise NodeError(path, n["kind"], "output_not_json", f"모델 출력이 JSON이 아닙니다: {text[:120]}{cut}")
            errs = [e.message for e in Draft202012Validator(schema).iter_errors(value)]
            if errs:
                raise NodeError(path, n["kind"], "output_schema", f"모델 출력이 {p['output']} 형식에 맞지 않습니다: {errs[0][:160]}{cut}")
        return {n["writes"][0]: value} if n.get("writes") else {}, None

    def _n_decide(self, n, state, reads, meta):
        """decide@1 (spec 11, 7.1; EV-001 CHG-102): ask the decision provider the node's questions about the rendered state, write
        {question: {value, probabilities, confidence, confidence_source}} to the node's one write, with fallback values below the
        minimum or when the provider gives no confidence. The provider's raw answer and the rendered state are kept for the record."""
        p = n["params"]
        path = f"{self.entry}.{n['id']}"
        st = p["state"]
        rendered = {k: copy.deepcopy(state.get(k)) for k in st} if isinstance(st, list) else templates.render(st, state)
        questions = copy.deepcopy(p["questions"])
        if not callable(getattr(self.provider, "decide", None)):
            raise NodeError(path, n["kind"], "provider", "이 실행의 provider는 decide를 부를 수 없습니다(Jev 형식 provider 필요)")
        raw, usage = self.provider.decide(p["provider"], p["model"], rendered, questions)
        meta["_cap"] = {"decide_state": rendered, "provider_raw": raw}
        meta.update(provider=p["provider"], model=p["model"], tokens_in=(usage or {}).get("input_tokens"),
                    tokens_out=(usage or {}).get("output_tokens"))   # recorded before the answer checks: the call is paid even if it fails
        answers = (raw or {}).get("answers") if isinstance(raw, dict) else None
        if not isinstance(answers, dict):
            raise NodeError(path, n["kind"], "decide_answer", "decision 응답에 answers가 없습니다")
        minimum, fallback = p.get("min_confidence"), p.get("fallback") or {}
        out, qmeta, used = {}, {}, []
        for q, spec in p["questions"].items():
            norm = _decide_normalize(path, n["kind"], q, spec, answers.get(q))
            limit = minimum if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) else (minimum or {}).get(q)
            below = norm["confidence"] is None or (limit is not None and norm["confidence"] < limit)
            if q in fallback and (norm["confidence_source"] == "provider_absent" or (limit is not None and below)):
                norm["value"] = copy.deepcopy(fallback[q])
                used.append(q)
            out[q] = norm
            qmeta[q] = {k: norm[k] for k in ("confidence_source", "confidence_method") if k in norm}
            qmeta[q]["type"] = spec["type"]
        meta["questions"] = qmeta
        if used:
            meta["fallback_used"] = used
        if "fake_decision" in ((raw or {}).get("limits") or []):
            meta["limits"] = ["fake_decision"]
        return {n["writes"][0]: out} if n.get("writes") else {}, None

    def _n_validate(self, n, state, reads, meta):
        p = n["params"]
        failed, checks, details, seen, reason = [], [], {}, {}, None
        first_custom_fail = True
        for rule in p["rules"]:
            name, arg = next(iter(rule.items()))
            ok, d = self._rule(n, state, name, arg)
            label = arg if name == "custom" else name   # spec v0.5 supplement 11: a custom rule is named by its function
            seen[label] = seen.get(label, 0) + 1
            if seen[label] > 1:
                label = f"{label}#{seen[label]}"
            if name == "custom":
                details[label] = {"passed": ok, **d}
                text = " — ".join(str(d[k]) for k in ("reason", "message") if d.get(k))[:200]   # custom rows were cut at 200 before too
                if not ok and first_custom_fail:
                    reason, first_custom_fail = d.get("reason"), False
            else:
                details[label] = {"passed": ok, "detail": d}
                text = d   # built-in rule rows keep their detail as before (no cut)
            checks.append({"rule": name, "passed": ok, "detail": text})   # node record shape unchanged
            if not ok:
                failed.append(label)
        result = {"passed": not failed, "failed_rules": failed, "reason": reason, "details": details}
        meta["checks"] = checks
        route = p.get("on_fail") if failed and p.get("on_fail") else None
        return {n["writes"][0]: result} if n.get("writes") else {}, route

    def _rule(self, n, state, name, arg):
        reads = n.get("reads", [])
        def list_key():
            return next((k for k in reads if str(self.state_types.get(k, "")).startswith("list[")), reads[0] if reads else None)
        if name == "evidence":
            ev = state.get(list_key()) or []
            low = float(arg.get("min_score", 0))   # spec 11 supplement 5: min_score applies only to evidence that has a score
            good = [e for e in ev if e.get("score") is None or e["score"] >= low]
            need = set(arg.get("require_source_types", []))
            have = {e.get("source_type") for e in good}
            ok = len(good) >= int(arg.get("min_items", 0)) and need <= have
            return ok, f"점수 기준 통과 {len(good)}개(필요 {arg.get('min_items', 0)}), 출처 {sorted(have)}(필요 {sorted(need)})"
        if name == "schema":
            key = next((k for k in reads if self.state_types.get(k) == arg), reads[0] if reads else None)
            errs = [e.message for e in Draft202012Validator(self.schemas[arg]).iter_errors(state.get(key))]
            return not errs, "; ".join(errs)[:200] or "형식 맞음"
        if name == "evidence_ids":   # spec 11 supplement 4: a state key or {field, within?}; ids anywhere inside the field value
            field, within = (arg, None) if isinstance(arg, str) else (arg["field"], arg.get("within"))
            ids = _evidence_ids(state.get(field))
            ev_key = within or next((k for k in reads if k != field and str(self.state_types.get(k, "")).startswith("list[")), None)
            known = {e.get("id") for e in (state.get(ev_key) or [])}
            try:
                missing = sorted(ids - known)
            except TypeError:   # ids of mixed types (text and numbers): sort by their text, show them as they are
                missing = sorted(ids - known, key=str)
            return bool(ids) and not missing, f"근거 ID {len(ids)}개, 검색 결과에 없는 ID {missing}"
        if name == "unsafe_assertion":   # a state key or {field, deny?}
            key, terms = (arg, UNSAFE_DEFAULT) if isinstance(arg, str) else (arg["field"], arg.get("deny", UNSAFE_DEFAULT))
            text = json.dumps(state.get(key), ensure_ascii=False)
            hit = [t for t in terms if t in text]
            return not hit, f"확정 표현 {hit}" if hit else "확정 표현 없음"
        if name == "required":
            keys = arg if isinstance(arg, list) else [arg]
            missing = [k for k in keys if state.get(k) in (None, "", [], {})]
            return not missing, f"빈 값 {missing}"
        if name == "range":
            v = templates.by_path(state, arg["path"])
            ok = v is not None and float(arg.get("min", float("-inf"))) <= float(v) <= float(arg.get("max", float("inf")))
            return ok, f"값 {v}"
        if name == "custom":   # returns {passed, reason?, message?, details?} or a bool (spec v0.5 supplement 11; validate@2 only)
            r = self.custom_fn(arg)(**{k: state.get(k) for k in reads})   # validate@2: the node reads by name (supplement 3)
            if not isinstance(r, dict):
                return bool(r), {}
            info = {k: r[k] for k in ("reason", "message", "details") if k in r}
            if "passed" not in r and not info.get("message"):
                info["message"] = "custom 규칙이 passed를 돌려주지 않았습니다"
            return bool(r.get("passed", False)), info
        raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "unsupported_rule", f"이 runtime이 지원하지 않는 규칙입니다: {name}")

    def _n_human_review(self, n, state, reads, meta):
        p = n["params"]
        subj = p["subject"]
        policy = self.review.get("policy", "stop_before")
        if policy == "stop_before":
            raise ReviewStop(n["id"], subj, state.get(subj))
        if policy == "auto_approve":
            decision, actor = _auto_decision(p), "auto"
        elif policy == "fixture":
            decision, actor = self.review.get("decision"), "fixture"
            if not decision:
                if self.review.get("missing_fixture", "stop_before") == "auto_approve":
                    decision, actor = _auto_decision(p), "auto"
                else:
                    raise ReviewStop(n["id"], subj, state.get(subj))
        else:
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "review_policy", f"지원하지 않는 검토 처리: {policy}")
        by_node = decision.get("by_node") if isinstance(decision, dict) else None
        if isinstance(by_node, dict) and isinstance(by_node.get(n["id"]), dict):   # EV-007 CHG-40: this node's own decision first
            decision = {**by_node[n["id"]], "reason": by_node[n["id"]].get("reason", decision.get("reason", ""))}
        action = decision.get("action")
        if action not in p["actions"]:
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "review_action", f"허용되지 않은 검토 동작: {action}")
        out, edited, reval = {}, [], []
        if action == "edit":
            for k, v in (decision.get("edits") or {}).items():
                if k in p.get("editable", []):
                    out[k] = v
                else:   # EV-007 CHG-41: `<editable key>.<path>` changes one place in a copy of that state value
                    head, steps = _edit_path(k)
                    if head not in p.get("editable", []):
                        raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "review_edit", f"고칠 수 없는 state입니다: {k}")
                    try:
                        out[head] = _set_at(copy.deepcopy(out[head] if head in out else state.get(head)), steps, v)
                    except (TypeError, IndexError) as e:
                        raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "review_edit", f"고칠 수 없는 위치입니다: {k} ({e})")
                edited.append(k)
            trial = {**state, **out}
            nodes = {x["id"]: x for x in self.graph_doc["nodes"]}
            for vid in p.get("revalidate", []):
                res, _ = self._n_validate(nodes[vid], trial, {}, {})
                r = next(iter(res.values()), {"passed": True})
                reval.append({"node": vid, "passed": r["passed"], "failed_rules": r["failed_rules"]})
                for w in nodes[vid].get("writes", []):
                    out[w] = r
            if not all(x["passed"] for x in reval):
                raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "revalidate_failed", f"수정본이 재검사를 통과하지 못했습니다: {reval}")
        record = {"action": action, "actor_kind": actor, "reason": decision.get("reason", ""), "edited_fields": edited, "revalidation": reval}
        meta["review"] = record
        writes = [w for w in n.get("writes", []) if w not in out]
        if writes:
            out[writes[0]] = record
        return out, p["routes"][action]

    def _n_render(self, n, state, reads, meta):
        p = n["params"]
        text = templates.render((self.dir / p["template"]).read_text(encoding="utf-8"), state)
        if p["format"] == "json":
            text = json.dumps(json.loads(text), ensure_ascii=False)
        elif p["format"] not in ("md", "csv", "ddl", "mermaid"):
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "format", f"P0 runtime이 만들 수 없는 형식입니다: {p['format']}")
        if p["to"] not in self.state_types:
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "render_target", "P0 runtime은 state로만 산출물을 씁니다")
        meta["artifact"] = {"format": p["format"], "sha256": hashlib.sha256(text.encode()).hexdigest()}
        return {p["to"]: text}, None

    def _n_custom_code(self, n, state, reads, meta):
        fn = n["params"]["fn"]
        mod = fn.split(":")[0]
        src = self.dir / Path(*mod.split(".")).with_suffix(".py")
        meta["custom_source_hash"] = hashlib.sha256(src.read_bytes()).hexdigest()
        args = dict(reads)   # custom_code@3: a plain function — the node reads (None when missing) and its table stores by name
        try:
            for s in n["params"].get("stores") or []:
                bound = self.bindings["stores"].get(s) or {}
                args[s] = (PgTableResource(s, self.doc["stores"][s], bound, table=self.store(s)) if bound.get("backend") == "postgres-table"
                           else TableResource(s, self.doc["stores"][s], self.cfg["store_folders"][s]))
        except (ResourceError, OSError, ValueError, KeyError) as e:
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "resource", f"표 자원을 만들 수 없습니다: {e}")
        if "options" in n["params"]:   # 0.15.0 (D-476): only a node that declares options passes them — a fresh dict per call
            args["options"] = _options.values(n["params"])
        out = self.custom_fn(fn)(**args)
        if not isinstance(out, dict):
            raise NodeError(f"{self.entry}.{n['id']}", n["kind"], "custom_return", "custom 함수는 dict를 돌려줘야 합니다")
        return out, None

    # ------------------------------------------------------------------ one run with trace events (trace contract 2, 7)
    def run(self, input_value, *, context=None, sink=None, review=None, on_debug=None, on_artifact=None):
        """Run the entrypoint graph once and report every step to `sink` (a TraceSink) as contract-2 events: seq from 0 in memory,
        a span per run and per node execution, raw values through put_payload before the event that refers to them.
        A failure of the sink is a run error: no further node runs and the result says failed with error.code trace_*.
        on_debug(ev) receives every LangGraph debug event first (the runner prints its line protocol from it).
        on_artifact(node_path, format, text) -> {"location", "sha256", "size"}: when the entrypoint output is made by a render node,
        the server writes the report file through this callback before run_ended and the run records artifact_written (015a).
        Returns {"status", "outcome", "output", "error", "run_id", "detail"}; detail carries what the runner's line protocol needs.
        One Runtime runs one run at a time (per-node raw values are kept on the instance between a node and its event)."""
        ctx = context or {"run_id": "run_local", "trace_id": "0" * 31 + "1", "origin": "evaluation", "evaluation_run_id": None,
                          "deployment_id": None, "retention_class": "standard"}
        start = getattr(self.provider, "start_run", None)   # EV-013 Q-5 (가): every Run starts on the first key
        if callable(start):
            start()
        em = trace.Emitter(ctx, sink or trace.NullSink())
        t0 = time.monotonic()
        nodes = {n["id"]: n for n in self.graph_doc["nodes"]}
        raw_rc = "dev" if ctx.get("retention_class") == "dev" else "provider_raw"
        tokens = {"in": 0, "out": 0}
        reviews = []

        def result(status, outcome=None, output=None, error=None, **detail):
            return {"status": status, "outcome": outcome, "output": output, "error": error, "run_id": ctx["run_id"], "detail": detail}

        def end(status, outcome=None, output=None, error=None):
            out_ref = em.payload(output, "final") if output is not None else None
            em.event("run_ended", {"status": status, "outcome": outcome, "output_ref": out_ref, "error": error,
                                   "ms": round((time.monotonic() - t0) * 1000), "tokens_in": tokens["in"], "tokens_out": tokens["out"]})

        def trace_failed(e, **detail):
            err = {"code": e.code, "message": e.message}
            try:   # one more try to close the record; if that fails too, the caller logs it
                end("failed", None, None, err)
            except trace.TraceFailure:
                pass
            return result("failed", None, None, err, stage="trace", **detail)

        # run_started: refused here means no node runs at all (trace contract 8)
        stores = {k: v.get("release_id") for k, v in self.bindings["stores"].items()}
        try:
            em.event("run_started", {
                "origin": ctx["origin"], "source_kind": "agent_pack", "evaluation_run_id": ctx.get("evaluation_run_id"),
                "deployment_id": ctx.get("deployment_id"), "pack_id": self.doc["pack"]["id"], "pack_revision": self.revision,
                "data_release_id": trace.data_release_id(stores),
                "release_bindings": {k: {"release_id": v.get("release_id"), "sha256": v.get("manifest_sha256")} for k, v in self.bindings["stores"].items()},
                "runtime_version": __version__, "graph": self.entry, "manifest": self.manifest(),
                "input_ref": em.payload(input_value, "input"), "retention_class": ctx["retention_class"]})
        except trace.TraceFailure as e:
            return result("failed", None, None, {"code": e.code, "message": e.message}, stage="trace")
        try:
            try:
                state = self.start_state(input_value)
                graph = self.build(review)
            except ValueError as e:
                stage = "input" if "형식에 맞지 않음" in str(e) else "load"
                err = {"code": "input_invalid" if stage == "input" else "load_failed", "message": str(e)[:500]}
                end("failed", None, None, err)
                return result("failed", None, None, err, stage=stage, text=str(e)[:500])
            except BaseException as e:  # noqa: BLE001 - building must never crash silently
                err = {"code": "load_failed", "message": f"{type(e).__name__}: {e}"[:500]}
                end("failed", None, None, err)
                return result("failed", None, None, err, stage="load", text=err["message"])
            open_spans, iters, failed, render_span = {}, {}, {}, {}
            final = dict(state)
            try:
                for mode, ev in graph.stream(state, stream_mode=["debug", "values"]):
                    if mode == "values":
                        final = ev
                        continue
                    if on_debug:
                        on_debug(ev)
                    p = ev.get("payload", {})
                    name, tid = p.get("name"), p.get("id")
                    n = nodes.get(name)
                    if n is None:
                        continue
                    if ev.get("type") == "task":
                        it = iters[name] = iters.get(name, 0) + 1
                        span = trace.new_span_id()
                        open_spans[tid] = (span, name, it, time.monotonic())
                        inp = p.get("input") or {}
                        em.event("node_started", {"node_path": f"{self.entry}.{name}", "iteration": it,
                                                  "input_ref": em.payload({k: inp.get(k) for k in n.get("reads", [])}, "input")},
                                 node_id=name, span_id=span)
                        if n["kind"].split("@")[0] == "human_review":
                            prm = n.get("params") or {}
                            em.event("review_requested", {"node_path": f"{self.entry}.{name}", "review_id": f"{ctx['run_id']}:{name}:{it}",
                                                          "subject_ref": em.payload(inp.get(prm.get("subject")), "review_subject"),
                                                          "allowed_actions": list(prm.get("actions", [])), "editable": list(prm.get("editable", []))},
                                     node_id=name, span_id=span)
                    elif ev.get("type") == "task_result":
                        span, _, it, nt0 = open_spans.pop(tid, (trace.new_span_id(), name, iters.get(name, 1), time.monotonic()))
                        cap = self._captures.pop((name, it), {})
                        if p.get("error"):
                            failed[name] = (span, it, nt0, p.get("error"))
                            continue
                        res = p.get("result")
                        res = dict(res) if isinstance(res, (list, tuple)) and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in res) else res
                        res = res if isinstance(res, dict) else {}
                        meta = (res.get("_meta") or {}).get(name) or {}
                        self._node_ended(em, n, span, it, nt0, res, meta, cap, raw_rc, tokens, reviews, ctx)
                        if n["kind"].split("@")[0] == "render":
                            render_span[name] = span
            except trace.TraceFailure as e:
                return trace_failed(e, state=_public(final))
            except ReviewStop as s:
                _unfinished(open_spans, failed, s)
                for nm, (span, it, nt0, _) in failed.items():
                    em.event("node_ended", {"node_path": f"{self.entry}.{nm}", "iteration": it, "status": "skipped", "output_ref": None,
                                            "ms": round((time.monotonic() - nt0) * 1000), "error": None, "meta": {"kind": nodes[nm]["kind"]}},
                             node_id=nm, span_id=span)
                end("succeeded", "stopped_before_review", s.value)
                return result("succeeded", "stopped_before_review", s.value, None, state=_public(final),
                              stopped={"reason": "review", "node": s.node, "subject": s.subject_key})
            except NodeError as e:
                _unfinished(open_spans, failed, e)
                self._close_failed(em, failed, nodes, e.info)
                err = {**e.info}
                end("failed", None, None, err)
                return result("failed", None, None, err, stage="run", node_error=e.info, text=str(e)[:500], trace=traceback.format_exc()[-1500:])
            except BaseException as e:  # noqa: BLE001 - Pack code may raise anything, SystemExit included
                _unfinished(open_spans, failed, e)
                self._close_failed(em, failed, nodes, None)
                err = {"code": type(e).__name__, "message": f"{type(e).__name__}: {e}"[:500]}
                end("failed", None, None, err)
                return result("failed", None, None, err, stage="run", text=err["message"], trace=traceback.format_exc()[-1500:])
            output = final.get(self.output_key) if self.output_key else _public(final)
            outcome = "rejected_by_review" if any(r == "reject" for r in reviews) else "completed"
            maker = next((nodes[nm] for nm in reversed(list(render_span)) if (nodes[nm].get("params") or {}).get("to") == self.output_key), None)
            if on_artifact and maker is not None and isinstance(output, str):
                fmt = maker["params"]["format"]
                try:
                    art = on_artifact(f"{self.entry}.{maker['id']}", fmt, output)
                    body = {"node_path": f"{self.entry}.{maker['id']}", "format": fmt, "sha256": art["sha256"], "location": art["location"], "size": int(art["size"])}
                except trace.TraceFailure:
                    raise
                except BaseException as e:  # noqa: BLE001 - the server's file write failed: no result without its report
                    err = {"code": "artifact_failed", "message": f"{type(e).__name__}: {e}"[:300]}
                    end("failed", None, None, err)
                    return result("failed", None, None, err, stage="artifact", state=_public(final))
                em.event("artifact_written", body, node_id=maker["id"], span_id=render_span[maker["id"]])
            end("succeeded", outcome, output)
            return result("succeeded", outcome, output, None, state=_public(final))
        except trace.TraceFailure as e:
            return trace_failed(e)

    def _node_ended(self, em, n, span, it, nt0, res, meta, cap, raw_rc, tokens, reviews, ctx):
        """node_ended (and review_recorded) for one finished node execution. Raw values go to put_payload first."""
        name, kind = n["id"], n["kind"].split("@")[0]
        prm = n.get("params") or {}
        out = _public(res)
        m = {"kind": meta.get("kind", n["kind"])}
        for k in ("expect_checks", "custom_source_hash"):
            if meta.get(k) is not None:
                m[k] = meta[k]
        if kind == "llm":
            m.update(provider=meta.get("provider"), model=meta.get("model"),
                     request_config={"temperature": prm.get("temperature"), "max_tokens": prm.get("max_tokens"), **_llm_on(prm, self.models),
                                     **({"reasoning_effort_skipped": _reasoning_skipped(prm, self.models)}
                                        if _reasoning_skipped(prm, self.models) is not None else {})},
                     tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"))
            if meta.get("key_slot") is not None:   # EV-013: only for a provider that names a second key
                m["key_slot"] = meta["key_slot"]
            for k in ("system_fingerprint", "response_model"):   # EV-002 CHG-L05: only on a node that turned strict or seed on
                if meta.get(k) is not None:
                    m[k] = meta[k]
            tokens["in"] += meta.get("tokens_in") or 0
            tokens["out"] += meta.get("tokens_out") or 0
            if "prompt" in cap:
                m["prompt_ref"] = em.payload(cap["prompt"], "prompt")
            if cap.get("provider_raw") is not None:
                m["provider_raw_ref"] = em.payload(cap["provider_raw"], "provider_raw", raw_rc)
        elif kind == "custom_code" and "options" in prm:   # 0.15.0 (D-476): the option values this run used
            m["request_config"] = {"options": _options.values(prm)}
        elif kind == "decide":
            m.update(provider=meta.get("provider"), model=meta.get("model"), tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"),
                     questions=meta.get("questions"))
            for k in ("fallback_used", "limits"):
                if meta.get(k):
                    m[k] = meta[k]
            tokens["in"] += meta.get("tokens_in") or 0
            tokens["out"] += meta.get("tokens_out") or 0
            if "decide_state" in cap:
                m["state_ref"] = em.payload(cap["decide_state"], "decide_state")
            if cap.get("provider_raw") is not None:
                m["provider_raw_ref"] = em.payload(cap["provider_raw"], "provider_raw", raw_rc)
        elif kind == "retrieve" and isinstance(meta.get("retrievals"), dict):
            r = meta["retrievals"]
            q = cap.get("query")
            m.update(store=r.get("store"), release_id=r.get("index_release"),
                     query_ref=em.payload(q, "query") if q is not None else None,
                     query_sha256=_sha_or_none(q),
                     filter_applied=cap.get("filter"), results=r.get("results"), fallback_used=r.get("fallback_used"),
                     fallback_reason=r.get("fallback_reason"), embedding_mode=cap.get("embedding_mode"),
                     embedding_model=cap.get("embedding_model"), embed_tokens=cap.get("embed_tokens"))
            if "method" in r:   # retrieve@2 only (CHG-R09); retrieve@1 meta keys stay as they were
                m.update(method=r["method"], index_recipe_sha256=r.get("index_recipe_sha256"))
            if meta.get("key_slot") is not None:   # EV-013: a real query embedding through a provider that names a second key
                m["key_slot"] = meta["key_slot"]
        elif kind == "validate":
            m["checks"] = meta.get("checks")
        elif kind == "router":
            m["route"] = meta.get("route")
        elif kind == "predict":
            m["model_release"] = meta.get("model_release")
            if "features" in cap:
                m["features_ref"] = em.payload(cap["features"], "features")
            for k in ("probabilities", "alarm", "threshold"):   # EV-003 CHG-P07 · P03: after the 0.10.1 keys
                if meta.get(k) is not None:
                    m[k] = meta[k]
        elif kind == "render":
            m["artifact"] = meta.get("artifact")
        error = meta.get("error")   # an on_error route: the node failed, the run goes on along the route
        em.event("node_ended", {"node_path": f"{self.entry}.{name}", "iteration": it, "status": "error" if error else "done",
                                "output_ref": em.payload(out, "output"), "ms": round((time.monotonic() - nt0) * 1000),
                                "error": error, "meta": m}, node_id=name, span_id=span)
        rv = meta.get("review")
        if kind == "human_review" and isinstance(rv, dict):
            reviews.append(rv.get("action"))
            edited = rv.get("edited_fields") or []
            em.event("review_recorded", {"review_id": f"{ctx['run_id']}:{name}:{it}", "action": rv.get("action"),
                                         "actor": rv.get("actor_kind") or "auto", "actor_kind": rv.get("actor_kind") or "auto", "role": None,
                                         "reason": rv.get("reason") or "", "edited_fields": edited,
                                         "edited_value_ref": em.payload({k: out.get(k.split(".")[0].split("[")[0]) for k in edited}, "review_edit") if edited else None,
                                         "revalidation": rv.get("revalidation") or []}, node_id=name, span_id=span)

    def _close_failed(self, em, failed, nodes, info):
        """node_ended(error) for the executions that failed in the last step; the NodeError's own info goes to its node."""
        for nm, (span, it, nt0, err) in failed.items():
            mine = info if info and info.get("node_path") == f"{self.entry}.{nm}" else None
            em.event("node_ended", {"node_path": f"{self.entry}.{nm}", "iteration": it, "status": "error", "output_ref": None,
                                    "ms": round((time.monotonic() - nt0) * 1000),
                                    "error": mine or {"node_path": f"{self.entry}.{nm}", "kind": nodes[nm]["kind"], "code": type(err).__name__ if isinstance(err, BaseException) else "error",
                                                      "message": str(err)[:400], "retryable": False, "attempt": 1},
                                    "meta": {"kind": nodes[nm]["kind"]}}, node_id=nm, span_id=span)

    # ------------------------------------------------------------------ inputs
    def start_state(self, case_input):
        """The case input becomes the entrypoint input state key; it must match the entrypoint input schema (CHG-16)."""
        schema = self.schemas[self.ep["input"]]
        errs = list(Draft202012Validator(schema).iter_errors(case_input))
        if errs:
            e = errs[0]
            where = "$" + "".join(f".{p}" if isinstance(p, str) else f"[{p}]" for p in e.absolute_path)
            raise ValueError(f"입력이 {self.ep['input']} 형식에 맞지 않음 — {where}: {e.message}")
        return {self.input_key: case_input}

    def projection(self):
        """What the probe reports (CHG-08 comparison): node ids, entrypoint, edges by meaning, router targets, reads/writes."""
        g = self.build()
        drawable = g.get_graph()
        names = sorted(n for n in drawable.nodes if n not in ("__start__", "__end__"))
        edges = sorted({(e.source, e.target, bool(e.conditional)) for e in drawable.edges})
        nodes = {n["id"]: n for n in self.graph_doc["nodes"]}
        return {"runtime": {"version": __version__, "digest": runtime_digest()}, "pack_revision": self.revision, "entrypoint": self.entry,
                "nodes": names, "edges": [{"source": s, "target": t, "conditional": c} for s, t, c in edges],
                "routers": {k: sorted(set(v["params"]["cases"].values()) | {v["params"]["default"]}) for k, v in nodes.items() if v["kind"].startswith("router")},
                "reads_writes": {k: {"reads": v.get("reads", []), "writes": v.get("writes", [])} for k, v in nodes.items()},
                "input_key": self.input_key, "output_key": self.output_key}
