"""Stage 2 of loading (spec 16.1): checks JSON Schema cannot express (references, graph, state).

Each problem is (level, path, message) with level "error" (not loadable) or "warning" (loadable, shown on screen)."""
import ast
import json
import re
from pathlib import Path

from .. import __version__

P0_KINDS = {"predict", "retrieve", "llm", "decide", "validate", "router", "human_review", "render", "custom_code"}   # decide@1: EV-001 (0.10.0)
KIND_VERSIONS = {"custom_code": {3}, "validate": {1, 2}, "retrieve": {1, 2}}   # spec v0.5 supplement 3: plain functions; custom_code@1·@2 (the old tool object) removed; retrieve@2: EV-006 (0.12.0)
SUPPORTED = sorted([k + "@1" for k in P0_KINDS - {"custom_code"}] + ["custom_code@3", "validate@2", "retrieve@2"])
RUNTIME_MODULES = {"llmops", "packrt"}   # Pack code must run without LLMOps (spec 8 coupling check, supplement 3)
KIND_RE = re.compile(r"^([a-z_]+)(?:@(\d+))?$")
FN_RE = re.compile(r"^custom\.[a-z0-9_.]+:[a-z_][a-z0-9_]*$")   # the same rule as the custom_code fn pattern in pack.schema.json
START, END = "START", "END"
NET_MODULES = {"socket", "requests", "httpx", "urllib", "urllib3", "http", "aiohttp", "websockets", "ftplib", "smtplib"}
PROC_MODULES = {"subprocess", "multiprocessing"}
BASIC_TYPES = {"str", "int", "float", "bool", "dict"}


def _check_decide(nid, node, p, models, path, err):
    """decide@1 (spec 11, 7.1; agent-pack-runtime EV-001 CHG-101): one write, a provider whose models.yaml entry lists the model under
    `decision` (a chat model is not taken yet — H-1), fallback / min_confidence keys among the questions and fallback values that
    fit each question, a fallback for every question with a minimum, and a state key list read from the node's reads (templates go
    through the existing flow checks, as for llm prompts)."""
    writes, reads = node.get("writes", []), set(node.get("reads", []))
    if len(writes) != 1:
        err(path + ("writes",), f"decide node '{nid}'는 writes가 정확히 1개여야 합니다(질문별 결과를 한 state에 씀): {writes}")
    if models is None:
        err(path + ("params", "model"), "models.yaml이 없어 모델을 확인할 수 없습니다")
    else:
        prov = (models.get("providers") or {}).get(p.get("provider")) or {}
        listed = prov.get("models") or {}
        if p.get("model") not in (listed.get("decision") or []):
            if p.get("model") in (listed.get("chat") or []):
                err(path + ("params", "model"), f"이 runtime은 chat 모델로 decide를 아직 하지 않습니다: {p.get('provider')}/{p.get('model')}"
                                                f"(models.yaml decision 목록의 모델을 씁니다)")
            else:
                err(path + ("params", "model"), f"models.yaml의 {p.get('provider')} decision 목록에 '{p.get('model')}'가 없습니다")
    questions = p.get("questions") or {}
    minimum = p.get("min_confidence")
    with_min = set(questions) if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) else set(minimum or {})
    for q in sorted(with_min - set(questions)):
        err(path + ("params", "min_confidence", q), f"min_confidence의 '{q}'가 questions에 없습니다")
    fallback = p.get("fallback") or {}
    for q, v in fallback.items():
        spec = questions.get(q)
        if not isinstance(spec, dict):
            err(path + ("params", "fallback", q), f"fallback의 '{q}'가 questions에 없습니다")
            continue
        typ, crit = spec.get("type"), spec.get("criteria")
        fits = (typ == "noul" and isinstance(v, bool)
                or typ == "choice" and isinstance(crit, dict) and isinstance(v, str) and v in crit
                or typ == "score" and isinstance(crit, list) and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= len(crit))
        if not fits:
            want = {"noul": "true 또는 false", "choice": "criteria의 선택지 하나", "score": f"1 ~ {len(crit) if isinstance(crit, list) else '단계 수'}의 정수"}.get(typ, "질문 종류에 맞는 값")
            err(path + ("params", "fallback", q), f"fallback '{q}'의 값이 질문 종류({typ})에 맞지 않습니다: {v!r}({want})")
    for q in sorted((with_min & set(questions)) - set(fallback)):
        err(path + ("params", "fallback"), f"min_confidence가 걸린 질문 '{q}'에 fallback이 없습니다")
    st = p.get("state")
    if isinstance(st, list):
        for k in sorted(set(st) - reads):
            err(path + ("params", "state"), f"decide node '{nid}'의 state 키 목록에 reads에 없는 '{k}'가 있습니다")


RULE_FIELDS = {"evidence_ids": ("within",), "unsafe_assertion": ("deny",)}   # spec v0.5 supplement 4: a state key, or {field, <this key>?}


def _check_rule_value(nid, node, state, rule, path, err):
    """validate evidence_ids·unsafe_assertion value (spec 11, supplement 4): a state key the node reads, or a mapping with `field`
    and the rule's own optional key. Other shapes would fail at run time, so they are refused here."""
    name, value = next(iter(rule.items()))
    extra = RULE_FIELDS[name][0]
    reads = node.get("reads") or []
    if isinstance(value, str):
        field = value
    elif isinstance(value, dict) and "field" in value and isinstance(value["field"], str) and set(value) <= {"field", extra}:
        field = value["field"]
    else:
        err(path, f"node '{nid}'의 {name} 규칙 값은 state 키 이름 또는 {{field, {extra}}} mapping이어야 합니다: {value}")
        return
    if field not in reads:
        err(path, f"node '{nid}'의 {name} 규칙이 가리키는 '{field}'가 reads에 없습니다")
    if not isinstance(value, dict) or extra not in value:
        return
    if name == "evidence_ids":
        within = value["within"]
        if not (isinstance(within, str) and within in reads and str(_state_type(state.get(within, "")) or "").startswith("list[")):
            err(path, f"node '{nid}'의 evidence_ids 규칙 within '{within}'는 reads에 있는 목록 state여야 합니다")
    elif not (isinstance(value["deny"], list) and value["deny"] and all(isinstance(w, str) and w for w in value["deny"])):
        err(path, f"node '{nid}'의 unsafe_assertion 규칙 deny는 글 목록이어야 합니다")


def _ver(s):
    return tuple(int(x) for x in s.split("."))


def kind_of(node):
    m = KIND_RE.match(node.get("kind", ""))
    return (m.group(1), int(m.group(2) or 1)) if m else (None, None)


class Graph:
    """Successor relation of one graph (pair edges, joins, loops, router/review/validate/on_error routes)."""

    def __init__(self, name, g):
        self.name, self.g = name, g
        self.nodes = {n["id"]: n for n in g.get("nodes", []) if isinstance(n, dict) and "id" in n}
        self.succ, self.loops, self.plain = {}, [], {}
        add = lambda a, b, how: self.succ.setdefault(a, []).append((b, how))
        for i, e in enumerate(g.get("edges", [])):
            if isinstance(e, list):
                add(e[0], e[1], ("edge", i)); self.plain.setdefault(e[0], []).append(e[1])
            elif "join" in e:
                for s in e["join"]:
                    add(s, e["to"], ("join", i))
            else:
                add(e["from"], e["to"], ("loop", i)); self.loops.append((i, e))
        for nid, n in self.nodes.items():
            kind, _ = kind_of(n)
            p = n.get("params") or {}
            if kind == "router":
                for k, t in (p.get("cases") or {}).items():
                    add(nid, t, ("case", k))
                if "default" in p:
                    add(nid, p["default"], ("default", None))
            elif kind == "human_review":
                for a, t in (p.get("routes") or {}).items():
                    add(nid, t, ("route", a))
            elif kind == "validate" and p.get("on_fail"):
                add(nid, p["on_fail"], ("on_fail", None))
            then = (n.get("on_error") or {}).get("then", "")
            if isinstance(then, str) and then.startswith("route:"):
                add(nid, then[6:], ("on_error", None))

    def targets(self):
        for a, lst in self.succ.items():
            for b, how in lst:
                yield a, b, how

    def reach(self, start, stop=frozenset()):
        seen, todo = set(), [start]
        while todo:
            x = todo.pop()
            for y, _ in self.succ.get(x, []):
                if y not in seen and y not in stop:
                    seen.add(y); todo.append(y)
        return seen

    def preds(self):
        p = {}
        for a, b, _ in self.targets():
            p.setdefault(b, set()).add(a)
        return p


def check(doc, folder, lines_of):
    """Return list of (level, path, message)."""
    out = []
    err = lambda path, msg: out.append(("error", path, msg))
    warn = lambda path, msg: out.append(("warning", path, msg))
    folder = Path(folder)

    # 1 runtime_contract
    need = (doc.get("runtime_contract") or {}).get("min_runtime_version", "0.0.0")
    if _ver(need) > _ver(__version__):
        err(("runtime_contract", "min_runtime_version"), f"runtime {need} 이상이 필요합니다(지금 runtime {__version__})")

    schemas = doc.get("schemas") or {}
    stores = doc.get("stores") or {}
    tools = doc.get("tools") or {}
    graphs = doc.get("graphs") or {}
    # 2 references: schema files
    for name, rel in schemas.items():
        p = folder / rel
        if not p.is_file():
            err(("schemas", name), f"schema 파일이 없습니다: {rel}")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            err(("schemas", name), f"schema 파일이 JSON이 아닙니다: {rel} ({e})")
    for sname, s in stores.items():
        if s.get("type") == "table" and s.get("schema") not in schemas:
            err(("stores", sname, "schema"), f"store '{sname}'의 schema '{s.get('schema')}'가 schemas에 없습니다")
    for tname, t in tools.items():
        for k in ("input", "output"):
            if t.get(k) not in schemas:
                err(("tools", tname, k), f"tool '{tname}'의 {k} 타입 '{t.get(k)}'가 schemas에 없습니다")
        if t.get("effect") == "write" and not (t.get("adapters") or {}).get("mock"):
            warn(("tools", tname, "adapters"), f"write 도구 '{tname}'에 mock adapter가 없어 평가 Run을 시작할 수 없습니다")
        mock = (t.get("adapters") or {}).get("mock")
        if mock:
            _check_fn(folder, mock, ("tools", tname, "adapters", "mock"), err)

    # 3 entrypoints
    for i, ep in enumerate(doc.get("entrypoints") or []):
        if ep.get("graph") not in graphs:
            err(("entrypoints", i, "graph"), f"entrypoint의 그래프 '{ep.get('graph')}'가 없습니다")
        for k in ("input", "output"):
            if ep.get(k) not in schemas:
                err(("entrypoints", i, k), f"entrypoint의 {k} 타입 '{ep.get(k)}'가 schemas에 없습니다")

    models = _models(folder, err)
    _check_reasoning_lists(models, err)
    for gname, g in graphs.items():
        _check_graph(doc, folder, gname, g, schemas, stores, graphs, models, err, warn)

    # env vs .env.example (spec 4): names only, never values
    ex = folder / ".env.example"
    if ex.is_file():
        names = {l.split("=", 1)[0].strip() for l in ex.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")}
        env = doc.get("env") or {}
        want = set(env.get("required", [])) | set(env.get("optional", []))
        if names != want:
            warn(("env",), f".env.example의 이름 목록이 env와 다릅니다(빠짐: {sorted(want - names)}, 더 있음: {sorted(names - want)})")
    return out


def _models(folder, err):
    p = folder / "models.yaml"
    if not p.is_file():
        return None
    from . import strict_yaml
    try:
        m, _ = strict_yaml.load(p.read_text(encoding="utf-8"))
    except strict_yaml.StrictYAMLError as e:
        err(("models.yaml",), f"models.yaml {e}")
        return None
    return m if isinstance(m, dict) else None


def _check_reasoning_lists(models, err):
    """0.14.0 (D-475): a provider's `models.reasoning` lists the chat models that take reasoning_effort — names from its chat list."""
    for name, prov in ((models or {}).get("providers") or {}).items():
        listed = ((prov or {}).get("models") or {}).get("reasoning") if isinstance(prov, dict) else None
        if listed is None:
            continue
        if not isinstance(listed, list) or not all(isinstance(m, str) for m in listed):
            err(("models.yaml", "providers", name, "models", "reasoning"), f"models.yaml의 {name} reasoning은 모델 이름 목록이어야 합니다")
            continue
        chat = ((prov.get("models") or {}).get("chat")) or []
        out = [m for m in listed if m not in chat]
        if out:
            err(("models.yaml", "providers", name, "models", "reasoning"), f"models.yaml의 {name} reasoning 목록에 chat 목록 밖 모델이 있습니다: {', '.join(out)}")


def _check_llm_reasoning(nid, p, prov, path, warn):
    """0.14.0 (D-475): a node's reasoning_effort goes only to a model in the provider's reasoning list; with temperature the model
    may refuse the call (whether it does is found by one real call, not guessed here)."""
    if "reasoning_effort" not in p:
        return
    if p.get("model") not in ((((prov or {}).get("models") or {}).get("reasoning")) or []):
        warn(path + ("params", "reasoning_effort"), f"node '{nid}'의 reasoning_effort는 보내지 않습니다: models.yaml {p.get('provider')} "
                                                    f"reasoning 목록에 '{p.get('model')}'가 없습니다")
    elif p["reasoning_effort"] != "none" and "temperature" in p:
        warn(path + ("params", "temperature"), f"node '{nid}'는 reasoning_effort {p['reasoning_effort']}와 temperature를 함께 보냅니다 — "
                                               f"모델이 temperature를 거절할 수 있습니다")


def _check_shared_options(gname, g, err):
    """0.15.0 (D-476 덧 6): one option name in one graph is one value — every function node that declares it declares it the same
    way (type, limits, label, loosen and the current value); set_option changes them together."""
    first = {}
    for i, n in enumerate(g.get("nodes") or []):
        decl = (n.get("params") or {}).get("options") if str(n.get("kind", "")).startswith("custom_code") else None
        for name, opt in (decl.items() if isinstance(decl, dict) else ()):
            if name not in first:
                first[name] = (n.get("id"), opt)
            elif opt != first[name][1]:
                err(("graphs", gname, "nodes", i, "params", "options", name),
                    f"그래프 '{gname}'의 옵션 '{name}'는 한 값이어야 합니다 — node '{first[name][0]}'와 '{n.get('id')}'의 선언 · 값이 다릅니다")


def _check_options(nid, n, p, tree, path, err):
    """0.15.0 (D-476): a function node's option declaration — names, types, limits, current values — and a function that takes
    them (a parameter `options` or **kw; read from the code, never run). `options` cannot also be a read or a table store name."""
    from . import options as _options
    for name, msg in _options.problems(p.get("options")):
        err(path + ("params", "options") + ((name,) if isinstance(name, str) else ()), msg)
    clash = "options" in (n.get("reads") or []) or "options" in (p.get("stores") or [])
    if clash:
        err(path + ("reads",), f"node '{nid}'의 읽기 · 표 이름 options가 옵션 칸과 겹칩니다(함수는 options= 로 옵션 값을 받습니다)")
    func = (p.get("fn") or ":").split(":")[1]
    fdef = next((x for x in (tree.body if tree is not None else []) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == func), None)
    if fdef is not None:
        a = fdef.args
        if not (a.kwarg or any(x.arg == "options" for x in a.posonlyargs + a.args + a.kwonlyargs)):
            err(path + ("params", "fn"), f"node '{nid}'는 옵션을 선언했으므로 함수 '{func}'에 매개변수 options(또는 **kw)가 있어야 합니다")


def _check_fn(folder, fn, path, err):
    mod, func = fn.split(":")
    rel = Path(*mod.split(".")).with_suffix(".py")
    p = folder / rel
    if not p.is_file():
        err(path, f"함수 파일이 없습니다: {rel.as_posix()}")
        return None
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        err(path, f"{rel.as_posix()} 구문 오류: {e.msg} ({e.lineno}줄)")
        return None
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func for n in tree.body):
        err(path, f"{rel.as_posix()}에 함수 '{func}'가 없습니다")
        return None
    return tree


def _imports(tree, absolute_only=False):
    """Top-level module names a file imports. The capability warnings count every `from x import` (relative ones too, as
    before); the coupling check counts absolute imports only (absolute_only=True)."""
    mods = set()
    for x in ast.walk(tree):
        if isinstance(x, ast.Import):
            mods |= {a.name.split(".")[0] for a in x.names}
        elif isinstance(x, ast.ImportFrom) and x.module and not (absolute_only and x.level):
            mods.add(x.module.split(".")[0])
    return mods


def _pack_module(folder, parts, rel_to=None):
    """The Pack file a module path points to — <folder>/a/b.py or <folder>/a/b/__init__.py, or the same under rel_to for a
    relative import — when it exists inside the Pack folder; else None."""
    base = rel_to if rel_to is not None else folder
    for cand in (base.joinpath(*parts).with_suffix(".py") if parts else None, base.joinpath(*parts, "__init__.py")):
        if cand is not None and cand.is_file():
            try:
                cand.resolve().relative_to(folder.resolve())
            except ValueError:
                return None
            return cand
    return None


def _net_imports(folder, fn):
    """EV-005 CHG-K01 · K02 (D-276): the network modules a Pack function's file imports, and those of the Pack modules it
    imports in turn (absolute custom.* and relative imports that resolve to a file inside the Pack folder, each file once). Counted as
    the 0.11.0 warning counted them (the first name of `import x` / `from x import`); a relative import that resolves to a Pack file is
    followed instead of counted (D-5). Dynamic imports are not seen. Returns [(chain of Pack-relative paths, sorted modules)]."""
    start = folder / Path(*fn.split(":")[0].split(".")).with_suffix(".py")
    rel = lambda f: f.relative_to(folder).as_posix()
    queue, seen, found = [(start, [rel(start)])], set(), []
    while queue:
        f, chain = queue.pop(0)
        if f in seen:
            continue
        seen.add(f)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        nets, nexts = set(), []
        for x in ast.walk(tree):
            if isinstance(x, ast.Import):
                for a in x.names:
                    top = a.name.split(".")[0]
                    if top in NET_MODULES:
                        nets.add(top)
                    elif top == "custom":
                        nexts.append(_pack_module(folder, a.name.split(".")))
            elif isinstance(x, ast.ImportFrom):
                parts = x.module.split(".") if x.module else []
                if x.level:
                    base = f.parent
                    for _ in range(x.level - 1):
                        base = base.parent
                    target = _pack_module(folder, parts, base) if parts else None
                    subs = [_pack_module(folder, parts + [a.name], base) for a in x.names]
                    if target is None and parts and parts[0] in NET_MODULES and not any(subs):
                        nets.add(parts[0])
                    nexts += [target] + subs
                elif parts and parts[0] in NET_MODULES:
                    nets.add(parts[0])
                elif parts and parts[0] == "custom":
                    nexts += [_pack_module(folder, parts)] + [_pack_module(folder, parts + [a.name]) for a in x.names]
        if nets:
            found.append((chain, sorted(nets)))
        for t in nexts:
            if t is not None and t not in seen:
                queue.append((t, chain + [rel(t)]))
    return found


def _check_net(folder, fn, path, err):
    for chain, mods in _net_imports(folder, fn):
        err(path, f"{' → '.join(chain)}이 망 모듈을 import합니다(Pack 코드는 망을 직접 쓰지 않습니다 — provider · store는 packrt가 부릅니다): {', '.join(mods)}")


def _check_coupling(fn, mods, path, err):
    bad = sorted(mods & RUNTIME_MODULES)
    if bad:
        rel = Path(*fn.split(":")[0].split(".")).with_suffix(".py").as_posix()
        err(path, f"{rel}이 LLMOps 실행 모듈을 import합니다(Pack 코드는 LLMOps 없이 돌아야 합니다): {', '.join(bad)}")


def state_type(t):
    """The type string of a state declaration (a string, or a mapping with `type`); public name since EV-023 CHG-201."""
    return t if isinstance(t, str) else t.get("type")


_state_type = state_type   # the old private name, kept for packrt's own callers


UNSTRUCTURED = ("allOf", "if", "then", "else", "not", "oneOf", "dependentRequired", "dependentSchemas")


def _schema_json(folder, rel):
    try:
        return json.loads((folder / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None   # reported by the schema file check


def _structured_output_problems(schema):
    """Keywords structured output does not take, found in keyword positions only (a property named `if` is a name, not a keyword)."""
    found = set()

    def walk(s):
        if not isinstance(s, dict):
            return
        found.update(k for k in UNSTRUCTURED if k in s)
        for sub in (s.get("properties") or {}).values() if isinstance(s.get("properties"), dict) else ():
            walk(sub)
        for k in ("items", "additionalProperties"):
            walk(s.get(k))
        for sub in s.get("anyOf") or [] if isinstance(s.get("anyOf"), list) else ():
            walk(sub)
        for k in ("$defs", "definitions"):
            for sub in (s.get(k) or {}).values() if isinstance(s.get(k), dict) else ():
                walk(sub)
    walk(schema)
    return sorted(found)


# EV-002 (agent-pack-runtime, instruction 015 · D-165): an llm node with `strict: true` sends its output schema in OpenAI's strict
# structured-output mode. The shape rules, the keywords the provider documents and its limits were read from its docs on 2026-10-20
# (run EV-002 evidence/e0/external-docs.md); a keyword the docs do not name is a warning, not an error. title · description are
# annotations kept without a warning; $schema · $id at the root are left out of the request (providers.HttpProvider.chat).
STRICT_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf", "$defs", "$ref", "pattern",
                   "format", "multipleOf", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "minLength",
                   "maxLength", "patternProperties", "title", "description"}
STRICT_ROOT_ONLY = ("$schema", "$id")
STRICT_MAX_DEPTH, STRICT_MAX_PROPERTIES, STRICT_MAX_ENUM = 10, 5000, 1000
STRICT_OPTIONAL_HINT = '선택 칸은 ["타입","null"]로 쓰고 required에 넣습니다'


def _strict_problems(schema):
    """(errors, structured-output keyword errors, warnings) — the second list is what CHG-36 already reports for every llm output."""
    errors, unstructured, warnings, total = [], [], [], [0]
    if not isinstance(schema, dict):
        return ["$: schema가 object가 아닙니다"], [], []
    t = schema.get("type")
    if t != "object":
        errors.append("$: 뿌리 type이 object가 아닙니다")
    if "anyOf" in schema:
        errors.append("$: 뿌리에 anyOf가 있습니다")

    def is_object(s):
        t = s.get("type")
        return t == "object" or (isinstance(t, list) and "object" in t) or isinstance(s.get("properties"), dict)

    def walk(s, path, depth, root=False):
        if not isinstance(s, dict):
            return
        for k in s:
            if k in UNSTRUCTURED:
                unstructured.append(f"{path}: {k}는 구조화 출력이 받지 않는 키워드")
            elif k not in STRICT_KEYWORDS and not (root and k in STRICT_ROOT_ONLY) and not (root and k in ("$defs", "definitions")):
                warnings.append(f"{path}.{k}")
        if root and "definitions" in s:
            warnings.append(f"{path}.definitions")
        if is_object(s):
            depth += 1
            if depth == STRICT_MAX_DEPTH + 1:
                errors.append(f"{path}: object 깊이가 {STRICT_MAX_DEPTH}을 넘습니다")
            if s.get("additionalProperties") is not False:
                errors.append(f"{path}: additionalProperties false 아님")
            props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
            total[0] += len(props)
            req = s.get("required") if isinstance(s.get("required"), list) else []
            missing = [k for k in props if k not in req]
            if missing:
                errors.append(f"{path}: required에 없는 칸 {', '.join(missing)}")
        if isinstance(s.get("enum"), list) and len(s["enum"]) > STRICT_MAX_ENUM:
            errors.append(f"{path}: enum 값이 {len(s['enum'])}개로 {STRICT_MAX_ENUM}개를 넘습니다")
        for name, sub in (s.get("properties") or {}).items() if isinstance(s.get("properties"), dict) else ():
            walk(sub, f"{path}.{name}", depth)
        walk(s.get("items"), f"{path}[]", depth)
        if isinstance(s.get("additionalProperties"), dict):
            walk(s["additionalProperties"], f"{path}.additionalProperties", depth)
        for i, sub in enumerate(s.get("anyOf") or [] if isinstance(s.get("anyOf"), list) else ()):
            walk(sub, f"{path}|anyOf{i}", depth)
        for k in ("$defs", "definitions"):
            for name, sub in (s.get(k) or {}).items() if isinstance(s.get(k), dict) else ():
                walk(sub, f"{k}.{name}", 0)

    walk(schema, "$", 0, root=True)
    if total[0] > STRICT_MAX_PROPERTIES:
        errors.append(f"$: 속성이 모두 {total[0]}개로 {STRICT_MAX_PROPERTIES}개를 넘습니다")
    return errors, unstructured, warnings


def strict_schema_problems(schema):
    """What OpenAI strict structured output would not take in this schema dict: (errors, warnings), each line '<path>: <reason>' or
    '<path>.<keyword>'. Reads no file, so a fake provider can call it on the schema of a request body (EV-002 CHG-L03)."""
    errors, unstructured, warnings = _strict_problems(schema)
    return errors + unstructured, warnings


def _check_llm_strict(nid, p, folder, schemas, path, err, warn):
    """EV-002 CHG-L02: only a node that turns strict or seed on is checked; `strict: false` alone is the same as no key."""
    on = p.get("strict") is True or "seed" in p
    if on and p.get("provider") != "openai":
        err(path + ("params", "provider"), f"node '{nid}'의 strict · seed는 OpenAI 형식 provider에서만 보냅니다(받은 것: {p.get('provider')})")
    if p.get("strict") is not True:
        return
    out_t = p.get("output")
    if out_t == "text":
        err(path + ("params", "strict"), f"node '{nid}'는 output이 text라 strict를 쓸 수 없습니다(strict는 schema 출력에만 씁니다)")
        return
    if out_t not in schemas:
        return   # reported by the output check
    schema = _schema_json(folder, schemas[out_t])
    if schema is None:
        return   # reported by the schema file check
    errors, _, warnings = _strict_problems(schema)
    if errors:
        shown = " · ".join(errors[:10]) + (f" 외 {len(errors) - 10}개" if len(errors) > 10 else "")
        hint = f" — {STRICT_OPTIONAL_HINT}" if any(": required에 없는 칸 " in e for e in errors) else ""
        err(path + ("params", "output"), f"node '{nid}'의 output 스키마 {out_t}이 strict 구조화 출력 조건에 맞지 않습니다: {shown}{hint} ({schemas[out_t]})")
    if warnings:
        warn(path + ("params", "output"), f"node '{nid}'의 output 스키마 {out_t}에 strict 구조화 출력 문서로 확인하지 못한 키워드가 있습니다: "
                                          f"{', '.join(warnings)} — 실제 호출에서 거부될 수 있습니다 ({schemas[out_t]})")


def _check_retrieve2(nid, p, stores, path, err, warn):
    """retrieve@2 (EV-006 CHG-R01 · R03 · R05 · R07, D-250 · D-266 · D-267): a vector store with index.split (fixed by people), the
    node's chunk_size · overlap only on a store that is cut into chunks (the history DB, split record, is kept whole), no chunker yet
    (C-5: the name is kept for later), filter keys inside the store's filterable list when it has one, and re-rank weights that add up to
    1 (C-7: retrieval = 1 − sensor)."""
    name = p.get("store")
    if name not in stores:
        err(path + ("params", "store"), f"store '{name}'가 stores에 없습니다")
        return
    sdoc = stores[name]
    if sdoc.get("type") != "vector":
        err(path + ("params", "store"), f"retrieve@2는 vector store만 씁니다(0.12.0): '{name}'({sdoc.get('type')})")
        return
    idx = sdoc.get("index") or {}
    split = idx.get("split")
    if split is None:
        err(("stores", name, "index", "split"), f"retrieve@2 node '{nid}'가 쓰는 store '{name}'에 index.split(record · section · chars)이 없습니다 — 나누기 기준은 사람이 정합니다")
    if "chunker" in idx:
        err(("stores", name, "index", "chunker"), f"store '{name}'의 index.chunker는 0.12.0에서 쓸 수 없습니다(이름만 예약, 부르는 길은 다음 판)")
    ni = p.get("index") or {}
    size, overlap = ni.get("chunk_size"), ni.get("overlap", 0)
    if split == "record" and ("chunk_size" in ni or "overlap" in ni):
        err(path + ("params", "index"), f"node '{nid}' index: store '{name}'는 split record라 청크 · 오버랩을 쓸 수 없습니다(건 그대로)")
    if split == "chars" and not size:
        err(path + ("params", "index", "chunk_size"), f"node '{nid}' index: store '{name}'는 split chars라 chunk_size가 필요합니다")
    if size and overlap >= size:
        err(path + ("params", "index", "overlap"), f"node '{nid}' index.overlap {overlap}은 chunk_size {size}보다 작아야 합니다")
    if ni and isinstance(idx.get("chunk"), dict):
        warn(("stores", name, "index", "chunk"), f"store '{name}'의 index.chunk는 retrieve@2 index가 있을 때 쓰지 않습니다(node '{nid}')")
    allowed = sdoc.get("filterable")
    if allowed is not None:
        for k in (p.get("filter") or {}):
            if k not in allowed:
                err(path + ("params", "filter", k), f"node '{nid}' filter 키 '{k}'는 store '{name}' filterable 밖입니다({', '.join(allowed) or '없음'})")
    rr = p.get("rerank") or {}
    if rr:   # D-273 R-1: the sensor norm comes from the store's rerank_norm, which must cover the re-rank fields
        norm_fields = ((idx.get("rerank_norm") or {}).get("fields")) or []
        if not idx.get("rerank_norm"):
            err(path + ("params", "rerank"), f"node '{nid}' rerank: store '{name}'에 index.rerank_norm이 없습니다(센서 정규화 기준)")
        else:
            miss = [f for f in rr.get("fields") or [] if f not in norm_fields]
            if miss:
                err(path + ("params", "rerank", "fields"), f"node '{nid}' rerank.fields {miss}가 store '{name}' index.rerank_norm.fields 밖입니다")
        pool, top_k = rr.get("pool"), p.get("top_k", 4)
        if isinstance(pool, int) and not isinstance(pool, bool) and isinstance(top_k, int) and pool < top_k:   # EV-015 (D-414): the pool holds the top_k
            err(path + ("params", "rerank", "pool"), f"node '{nid}' rerank.pool {rr['pool']}는 top_k {p.get('top_k', 4)}보다 작을 수 없습니다")
    w = ((p.get("rerank") or {}).get("weights")) or {}
    if w and round(float(w.get("retrieval", 0)) + float(w.get("sensor", 0)), 6) != 1:
        err(path + ("params", "rerank", "weights"), f"node '{nid}' rerank.weights 합이 1이 아닙니다(retrieval {w.get('retrieval')} + sensor {w.get('sensor')})")


def _check_retrieve_stores(g, gname, err):
    """EV-006 C-8 · CHG-R11: in one graph a store is read by retrieve@1 nodes or by retrieve@2 nodes, not both (one version is picked per
    store and the run manifest keeps one entry per store), and every retrieve@2 node on a store has the same index values."""
    by_store = {}
    for i, n in enumerate(g.get("nodes", [])):
        kind, ver = kind_of(n)
        if kind == "retrieve" and isinstance((n.get("params") or {}).get("store"), str):
            by_store.setdefault(n["params"]["store"], []).append((i, n, ver))
    for name, ns in by_store.items():
        vers = {v for _, _, v in ns}
        if vers >= {1, 2}:
            one = next(n["id"] for _, n, v in ns if v == 1)
            two = next(n["id"] for _, n, v in ns if v == 2)
            err(("graphs", gname, "nodes", ns[0][0]), f"store '{name}'를 retrieve@1 '{one}'와 retrieve@2 '{two}'가 함께 씁니다 — 0.12.0에서는 하나만")
        twos = [(i, n) for i, n, v in ns if v == 2]
        first = (twos[0][1].get("params") or {}).get("index") if twos else None
        for i, n in twos[1:]:
            if (n.get("params") or {}).get("index") != first:
                err(("graphs", gname, "nodes", i, "params", "index"),
                    f"store '{name}'를 쓰는 retrieve@2 node '{twos[0][1]['id']}' · '{n['id']}'의 index 값이 다릅니다 — 한 그래프에서는 같아야 합니다")


def _check_graph(doc, folder, gname, g, schemas, stores, graphs, models, err, warn):
    G = Graph(gname, g)
    base = ("graphs", gname)
    state = g.get("state") or {}
    _check_shared_options(gname, g, err)
    # 4 node id unique
    seen = {}
    for i, n in enumerate(g.get("nodes", [])):
        if n["id"] in seen:
            err(base + ("nodes", i, "id"), f"그래프 '{gname}'에 node id '{n['id']}'가 두 번 있습니다")
        seen.setdefault(n["id"], i)
        if n["id"] in (START, END):
            err(base + ("nodes", i, "id"), f"node id로 '{n['id']}'는 쓸 수 없습니다")
    idx = seen
    for key, t in state.items():
        typ = _state_type(t)
        inner = typ[5:-1] if typ.startswith("list[") and typ.endswith("]") else typ
        if inner not in BASIC_TYPES and inner not in schemas:
            err(base + ("state", key), f"state '{key}'의 타입 '{typ}'를 알 수 없습니다(schemas나 기본 타입이 아님)")
    _check_retrieve_stores(g, gname, err)
    for nid, n in G.nodes.items():
        path = base + ("nodes", idx[nid])
        kind, ver = kind_of(n)
        # 5 kind supported by this runtime
        if kind not in P0_KINDS or ver not in KIND_VERSIONS.get(kind, {1}):
            err(path + ("kind",), f"이 runtime이 구현하지 않은 종류입니다: {n.get('kind')} (지원: {', '.join(SUPPORTED)})")
        # 6 reads/writes are state keys
        for k in ("reads", "writes"):
            for j, key in enumerate(n.get(k, [])):
                if key not in state:
                    err(path + (k, j), f"node '{nid}'의 {k} '{key}'가 그래프 state에 없습니다")
        p = n.get("params") or {}
        if kind == "llm":
            if not (folder / p.get("prompt", "")).is_file():
                err(path + ("params", "prompt"), f"프롬프트 파일이 없습니다: {p.get('prompt')}")
            out_t = p.get("output")
            if out_t != "text" and out_t not in schemas:
                err(path + ("params", "output"), f"output 타입 '{out_t}'가 schemas에 없습니다")
            elif out_t != "text":   # spec v0.5 supplement 2, 11 llm@1: the output schema goes to structured output as is (EV-007 CHG-36)
                bad = _structured_output_problems(_schema_json(folder, schemas[out_t]))
                if bad:
                    err(path + ("params", "output"), f"node '{nid}'의 output 스키마 {out_t}에 구조화 출력이 받지 않는 키워드가 있습니다: "
                                                     f"{', '.join(bad)} ({schemas[out_t]})")
            _check_llm_strict(nid, p, folder, schemas, path, err, warn)
            for w in n.get("writes", []):
                st = _state_type(state.get(w, "")) if w in state else None
                if out_t != "text" and st and st in schemas and st != out_t:
                    err(path + ("writes",), f"node '{nid}'의 output 타입 '{out_t}'와 state '{w}'의 타입 '{st}'가 다릅니다")
            # 11 models
            if models is None:
                err(path + ("params", "model"), "models.yaml이 없어 모델을 확인할 수 없습니다")
            else:
                prov = (models.get("providers") or {}).get(p.get("provider")) or {}
                chat = ((prov.get("models") or {}).get("chat")) or []
                if p.get("model") not in chat:
                    err(path + ("params", "model"), f"models.yaml의 {p.get('provider')} chat 목록에 '{p.get('model')}'가 없습니다")
                _check_llm_reasoning(nid, p, prov, path, warn)
        elif kind == "decide":
            _check_decide(nid, n, p, models, path, err)
        elif kind == "render":
            if not (folder / p.get("template", "")).is_file():
                err(path + ("params", "template"), f"템플릿 파일이 없습니다: {p.get('template')}")
            if p.get("to") not in state and p.get("to") not in stores:
                err(path + ("params", "to"), f"render 대상 '{p.get('to')}'가 state나 stores에 없습니다")
        elif kind == "retrieve" and ver == 2:
            _check_retrieve2(nid, p, stores, path, err, warn)
        elif kind == "retrieve":
            if p.get("store") not in stores:
                err(path + ("params", "store"), f"store '{p.get('store')}'가 stores에 없습니다")
            else:
                st = stores[p["store"]].get("type")
                if st == "vector" and "query" not in p:
                    err(path + ("params", "query"), "vector store 검색에는 query가 필요합니다")
                if st == "table" and any(k in p for k in ("query", "top_k", "search", "mmr", "score_threshold")):
                    err(path + ("params",), "table store 검색에는 where·select·limit만 씁니다")
        elif kind == "custom_code" and ver == 3:   # spec 11 custom_code@3: a plain Pack function, table stores as arguments
            names = []
            for j, s in enumerate(p.get("stores") or []):
                if s not in stores:
                    err(path + ("params", "stores", j), f"custom_code@3 stores의 '{s}'가 stores에 없습니다")
                elif stores[s].get("type") != "table":
                    err(path + ("params", "stores", j), f"custom_code@3 stores에는 table 저장소만 적을 수 있습니다: {s}({stores[s].get('type')})")
                names.append(s)
            clash = sorted(set(names) & set(n.get("reads", [])))
            if clash:
                err(path + ("params", "stores"), f"node '{nid}'의 reads와 stores 이름이 겹칩니다: {', '.join(clash)}")
            tree = _check_fn(folder, p.get("fn", ""), path + ("params", "fn"), err)
            if "options" in p:
                _check_options(nid, n, p, tree, path, err)
            caps = n.get("capabilities") or {}
            if tree is not None:
                mods = _imports(tree)
                _check_coupling(p["fn"], _imports(tree, absolute_only=True), path + ("params", "fn"), err)
                _check_net(folder, p["fn"], path + ("params", "fn"), err)   # EV-005 (D-276): an error whatever capabilities.network says
                if caps.get("side_effect") == "none" and mods & PROC_MODULES:
                    warn(path + ("capabilities",), f"side_effect: none인데 프로세스 모듈을 씁니다: {sorted(mods & PROC_MODULES)}")
        elif kind == "validate":
            for j, rule in enumerate(p.get("rules") or []):
                if isinstance(rule, dict) and len(rule) == 1 and next(iter(rule)) in RULE_FIELDS:
                    _check_rule_value(nid, n, state, rule, path + ("params", "rules", j), err)
                    continue
                if not (isinstance(rule, dict) and "custom" in rule):
                    continue
                if ver == 1:   # spec 11 supplement 3: custom rules are called by name only in validate@2
                    err(path + ("params", "rules", j), f"node '{nid}': custom 규칙은 validate@2에서 씁니다")
                elif ver == 2:
                    if not isinstance(rule["custom"], str) or not FN_RE.match(rule["custom"]):
                        err(path + ("params", "rules", j), f"node '{nid}'의 custom 규칙 함수 이름은 custom.<모듈>:<함수> 형식이어야 합니다: {rule['custom']}")
                        continue
                    tree = _check_fn(folder, rule["custom"], path + ("params", "rules", j), err)
                    if tree is not None:
                        _check_coupling(rule["custom"], _imports(tree, absolute_only=True), path + ("params", "rules", j), err)
                        _check_net(folder, rule["custom"], path + ("params", "rules", j), err)
        elif kind == "predict":   # the model release is bound by the execution config (spec 3.2), checked at stage 3 and in Runtime.build
            if p.get("output") not in schemas:   # EV-003 CHG-P04
                err(path + ("params", "output"), f"node '{nid}'의 output '{p.get('output')}'이 schemas에 없습니다")
            feats = p.get("features")
            if isinstance(feats, dict) and "from" in feats:
                if set(feats) != {"from"}:
                    err(path + ("params", "features"), f"node '{nid}'의 features는 {{from: <state 키>}} 하나이거나 칸 이름 mapping입니다")
                elif not isinstance(feats["from"], str) or feats["from"] not in state:
                    err(path + ("params", "features"), f"node '{nid}'의 features.from '{feats['from']}'가 state에 없습니다")
            elif isinstance(feats, dict) and not feats:
                err(path + ("params", "features"), f"node '{nid}'의 features가 비었습니다")
        # {state.key...} references and router `by` must name a state key
        for ref in _state_refs(p):
            if ref not in state:
                err(path + ("params",), f"node '{nid}'의 params가 state에 없는 '{ref}'를 가리킵니다")
        # 9 retry needs idempotent for side effects
        retry = (n.get("on_error") or {}).get("retry", 0)
        side = (n.get("capabilities") or {}).get("side_effect", "none")
        if retry and side not in ("none", "dry_run") and not n.get("idempotent"):
            err(path + ("on_error",), f"부작용이 있는 node '{nid}'의 재시도에는 idempotent: true가 필요합니다")

    # 8 connectivity
    for a, b, how in G.targets():
        src_ok = a == START or a in G.nodes
        if not src_ok:
            err(_edge_path(base, G, a, how, idx), f"연결의 출발 '{a}'가 없습니다")
        if b != END and b not in G.nodes:
            err(_edge_path(base, G, a, how, idx), f"'{a}'에서 가는 대상 '{b}'가 없습니다")
        if b == START or a == END:
            err(_edge_path(base, G, a, how, idx), "START로 들어가거나 END에서 나가는 연결은 쓸 수 없습니다")
    for nid, n in G.nodes.items():
        kind, _ = kind_of(n)
        if kind == "router":
            for e in g.get("edges", []):
                if isinstance(e, list) and e[0] == nid:
                    err(base + ("nodes", idx[nid]), f"router '{nid}'의 나가는 연결은 params에만 적습니다(edges에 다시 적지 않음)")
    from_start = G.reach(START)
    if END not in from_start:
        err(base + ("edges",), f"그래프 '{gname}'에서 START부터 END까지 가는 경로가 없습니다")
    for nid in G.nodes:
        if nid not in from_start:
            err(base + ("nodes", idx[nid]), f"node '{nid}'는 START에서 닿지 않습니다")
        elif END not in G.reach(nid):
            err(base + ("nodes", idx[nid]), f"node '{nid}'에서 END로 가는 경로가 없습니다")
    # loops: exit path inside the cycle
    for i, e in G.loops:
        cyc = {e["from"], e["to"]} | {x for x in G.reach(e["to"]) if e["from"] in G.reach(x) or x == e["from"]}
        exits = [x for x in cyc if any(y not in cyc for y, _ in G.succ.get(x, []))]
        if not exits:
            err(base + ("edges", i), f"반복 {e['from']}→{e['to']}에 빠져나가는 경로가 없습니다")
    # 7 concurrent writers without merge
    merge = {k: (t.get("merge") if isinstance(t, dict) else None) for k, t in state.items()}
    for src, tg in G.plain.items():
        if len(tg) < 2:
            continue
        sets = [G.reach(t) | {t} for t in tg]
        common = set.intersection(*sets)
        branches = [s - common for s in sets]
        for a in range(len(branches)):
            for b in range(a + 1, len(branches)):
                for x in branches[a]:
                    for y in branches[b]:
                        if x in G.nodes and y in G.nodes:
                            for key in set(G.nodes[x].get("writes", [])) & set(G.nodes[y].get("writes", [])):
                                if not merge.get(key):
                                    err(base + ("state", key), f"동시에 실행될 수 있는 '{x}'와 '{y}'가 merge 규칙 없는 state '{key}'를 함께 씁니다")
    # human_review: subject always written; revalidate targets are validate nodes
    input_types = {ep.get("input") for ep in doc.get("entrypoints") or [] if ep.get("graph") == gname}
    initial = {k for k, t in state.items() if _state_type(t) in input_types}
    written = _definitely_written(G, initial)
    for nid, n in G.nodes.items():
        kind, _ = kind_of(n)
        if kind != "human_review":
            continue
        p = n.get("params") or {}
        subj = p.get("subject")
        if subj not in state:
            err(base + ("nodes", idx[nid], "params", "subject"), f"검토 대상 '{subj}'가 state에 없습니다")
        elif subj not in written.get(nid, set()):
            err(base + ("nodes", idx[nid], "params", "subject"), f"'{nid}'에 도착하는 경로 중 검토 대상 '{subj}' 값이 없는 경로가 있습니다")
        for j, v in enumerate(p.get("revalidate", [])):
            if v not in G.nodes or kind_of(G.nodes[v])[0] != "validate":
                err(base + ("nodes", idx[nid], "params", "revalidate", j), f"revalidate 대상 '{v}'는 validate node여야 합니다")
        for j, k in enumerate(p.get("editable", [])):
            if k not in state:
                err(base + ("nodes", idx[nid], "params", "editable", j), f"editable '{k}'가 state에 없습니다")


STATE_REF = re.compile(r"\{state\.([a-z_][a-z0-9_]*)")


def _state_refs(params):
    refs = set()
    def walk(v):
        if isinstance(v, str):
            refs.update(STATE_REF.findall(v))
            if v.startswith("state."):
                refs.add(v.split(".")[1])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(params)
    return refs


def _edge_path(base, G, a, how, idx):
    kind, ref = how
    if kind in ("edge", "join", "loop"):
        return base + ("edges", ref)
    if a in idx:
        return base + ("nodes", idx[a], "params")
    return base


def _definitely_written(G, initial):
    """State keys certainly written on every path from START to each node (before the node runs)."""
    preds = G.preds()
    allkeys = set(initial)
    for n in G.nodes.values():
        allkeys |= set(n.get("writes", []))
    before = {nid: set(allkeys) for nid in G.nodes}
    after = lambda x: set(initial) if x == START else before[x] | set(G.nodes[x].get("writes", []))
    changed = True
    while changed:
        changed = False
        for nid in G.nodes:
            ps = [p for p in preds.get(nid, set()) if p == START or p in G.nodes]
            new = set.intersection(*[after(p) for p in ps]) if ps else set()
            if new != before[nid]:
                before[nid] = new; changed = True
    return before
