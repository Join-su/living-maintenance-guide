"""IR patch (EV-017, option table v1.0 sections 0, 2-6, 9): turn edits of a Pack's nodes into new Pack files, check them and
compute the new revision. A pure function over files: the original Pack folder is only read.

    res = apply_patch(folder, ops, out_dir=None)
    res.ok, res.errors, res.warnings          # problems: {"stage", "line", "path", "message"}, stage "patch" | 0-2 | "flow"
    res.base_revision, res.revision           # revision of the original and of the patched Pack (when ok)
    res.changed_files, res.files              # revision files whose bytes changed, and their new bytes
    res.ir_diff                               # per graph: added/removed/changed nodes, edges added/removed; other top-level paths

Operations (anything else is refused):
    {"op": "set" | "unset", "graph", "node", "param", "value"}   editable params of the node's kind (✎ and section 9);
                                                                  validate: "on_fail" and "rules[i].<rule>" (basic rules);
                                                                  decide (agent-pack-runtime EV-001): provider, model, min_confidence,
                                                                  fallback and the question texts "questions.<q>.instructions",
                                                                  "questions.<q>.criteria.<key>", "questions.<q>.criteria[i]" only
    {"op": "add_rule", "graph", "node", "rule", "index"?} / {"op": "remove_rule", "graph", "node", "index"}   validate basic rules
    {"op": "write_file", "path", "text"}       the content of an existing prompts/ or templates/ file
    {"op": "create_file", "path", "text"}      a new file under prompts/ or templates/ (EV-022 CHG-117, option table 9.2);
                                               refused when the file already exists or the same ops already created it
    {"op": "add_node", "graph", "node", "after"?} / {"op": "remove_node", "graph", "node"}   standard nodes (section 6.1-6.3)
    {"op": "add_edge" | "remove_edge", "graph", "edge"}           an edges item: [a, b], {join, to}, {from, to, max_iterations}
    {"op": "set_option", "graph", "node", "option", "value"}   0.15.0 (D-476): one declared option value of a function node,
                                              inside its limits (the declaration — names, types, limits — stays); every node of
                                              the graph declaring the same name gets it (one name = one value, D-476 덧 6)
    {"op": "remove_unreachable", "graph"}     D-462 ②: every node START no longer reaches, with its edges — function and predict
                                               nodes too (they could not run); a join keeps its reached sources
The patched Pack is written to out_dir (or a temporary folder that is removed afterwards), loaded with stages 0-2 and checked
with the new flow checks (packrt.ir.flow).

    impact = delete_impact(folder, graph, node=..., edge=...)   # EV-022 CHG-117: what removing a node or an edge would do
    impact["ops"], impact["remove_edges"], impact["after"]       # read only: the original folder is not written

    b = bridge_ops(folder, graph, nodes, exit=None)  # D-462 ②: take nodes out and join the ways into them again
    b["ops"], b["exit"], b["pruned"], b["tried"], b["after"]     # read only, like delete_impact"""
import copy
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import flow, load_pack, semantic, strict_yaml, yaml_write
from . import options as _options
from . import revision as _revision

# editable params per kind (option table ✎ and section 9); everything else is view only (section 9.1)
EDITABLE = {
    "llm": {"provider", "model", "prompt", "temperature", "max_tokens"},
    "retrieve:vector": {"query", "top_k", "search", "mmr", "score_threshold", "filter", "fallback"},
    "retrieve:table": {"where", "select", "limit", "fallback"},
    # retrieve@2 (EV-006): the wheel's knobs (contract C) are inside these — index (chunk_size · overlap), method, fuse (k), top_k,
    # score_threshold, rerank (weights), filter, fallback (min_results); store fields (split · metadata · embedding) stay view only
    "retrieve@2": {"query", "filter", "method", "search", "mmr", "lexical", "fuse", "rerank", "top_k", "score_threshold", "fallback",
                   "return_metadata", "index"},
    "router": {"cases", "default"},
    "human_review": {"routes", "timeout"},
    "render": {"template"},
    "validate": {"on_fail"},
    "decide": {"provider", "model", "min_confidence", "fallback"},   # EV-001 CHG-105; question texts via DECIDE_TEXT_PARAM below
}
BASIC_RULES = {"evidence", "evidence_ids", "unsafe_assertion", "schema", "required", "range", "unit"}   # section 5 / 9.3
ADDABLE = {"llm@1", "retrieve@1", "retrieve@2", "validate@1", "validate@2", "router@1", "render@1"}                  # section 6.1 / 9.4
NODE_KEYS = {"id", "kind", "reads", "writes", "params", "expect"}
FILE_DIRS = ("prompts", "templates")                                                                   # section 9.2
RULE_PARAM = re.compile(r"^rules\[(\d+)\]\.([a-z_]+)$")
# decide@1 question texts only (spec 11 ✎: instructions and criteria descriptions); names, types, choices and levels stay as they are
DECIDE_TEXT_PARAM = re.compile(r"^questions\.([a-z][a-z0-9_]*)\.(?:(instructions)|criteria\.([^.\[\]]+)|criteria\[(\d+)\])$")
OPS = {"set", "unset", "add_rule", "remove_rule", "write_file", "create_file", "add_node", "remove_node", "add_edge", "remove_edge",
       "remove_unreachable", "set_option"}
BRANCH_KINDS = ("router", "validate", "human_review")   # nodes with more than one way out: taking one out needs an exit


@dataclass
class PatchResult:
    ok: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    base_revision: str = None
    revision: str = None
    changed_files: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    ir_diff: dict = field(default_factory=dict)
    out_dir: str = None


class _Refuse(Exception):
    def __init__(self, path, message):
        self.path, self.message = path, message


def _kind(node):
    return semantic.kind_of(node)[0]


def editable_params(doc, node):
    """The params a screen may change on this node (option table 9.3); public name since EV-023 CHG-201."""
    k = _kind(node)
    if k == "retrieve" and semantic.kind_of(node)[1] == 2:
        return EDITABLE["retrieve@2"]
    if k == "retrieve":
        store = (doc.get("stores") or {}).get((node.get("params") or {}).get("store")) or {}
        return EDITABLE["retrieve:table" if store.get("type") == "table" else "retrieve:vector"]
    return EDITABLE.get(k, set())


_editable = editable_params   # the old private name, kept for packrt's own callers


def _graph(doc, op):
    g = (doc.get("graphs") or {}).get(op.get("graph"))
    if not isinstance(g, dict):
        raise _Refuse(("graphs",), f"그래프 '{op.get('graph')}'가 없습니다")
    return g


def _node(g, op, gname):
    for i, n in enumerate(g.get("nodes", [])):
        if n.get("id") == op.get("node"):
            return i, n
    raise _Refuse(("graphs", gname, "nodes"), f"그래프 '{gname}'에 node '{op.get('node')}'가 없습니다")


def _no_function_node(n, path, what):
    if _kind(n) == "custom_code":
        raise _Refuse(path, f"함수 node '{n['id']}'는 {what}할 수 없습니다(연결 위치만 바꿀 수 있습니다)")


def _apply(doc, files, folder, op):
    name = op.get("op") if isinstance(op, dict) else None
    if name not in OPS:
        raise _Refuse((), f"지원하지 않는 수정입니다: {name!r} (할 수 있는 수정: {', '.join(sorted(OPS))})")
    if name == "write_file":
        return _write_file(files, folder, op)
    if name == "create_file":
        return _create_file(files, folder, op)
    gname = op.get("graph")
    g = _graph(doc, op)
    if name == "remove_unreachable":
        return _remove_unreachable(g, gname)
    if name in ("add_edge", "remove_edge"):
        edge = op.get("edge")
        edges = g.setdefault("edges", [])
        if name == "add_edge":
            edges.append(copy.deepcopy(edge))
        elif edge in edges:
            edges.remove(edge)
        else:
            raise _Refuse(("graphs", gname, "edges"), f"그래프 '{gname}'에 그런 연결이 없습니다: {edge}")
        return
    if name == "add_node":
        return _add_node(doc, g, gname, op)
    i, n = _node(g, op, gname)
    path = ("graphs", gname, "nodes", i)
    if name == "remove_node":
        _no_function_node(n, path, "삭제")
        del g["nodes"][i]
        return
    if name == "set_option":
        return _set_option(g, n, path, op)
    _no_function_node(n, path, "수정")
    p = n.setdefault("params", {})
    if name in ("add_rule", "remove_rule"):
        if _kind(n) != "validate":
            raise _Refuse(path, f"node '{n['id']}'는 validate node가 아닙니다")
        rules = p.setdefault("rules", [])
        if name == "add_rule":
            _basic_rule(op.get("rule"), path + ("params", "rules"))
            at = op.get("index", len(rules))
            if not (isinstance(at, int) and 0 <= at <= len(rules)):
                raise _Refuse(path + ("params", "rules"), f"규칙 자리 {at!r}가 범위 밖입니다")
            rules.insert(at, copy.deepcopy(op["rule"]))
        else:
            j = op.get("index")
            if not (isinstance(j, int) and 0 <= j < len(rules)):
                raise _Refuse(path + ("params", "rules"), f"규칙 {j!r}번이 없습니다")
            _basic_rule(rules[j], path + ("params", "rules", j), "삭제")
            del rules[j]
        return
    param = op.get("param")
    m = RULE_PARAM.match(param or "")
    if _kind(n) == "validate" and m:
        j, rk = int(m.group(1)), m.group(2)
        rules = p.get("rules") or []
        if not (j < len(rules) and isinstance(rules[j], dict) and rk in rules[j]):
            raise _Refuse(path + ("params", "rules"), f"node '{n['id']}'에 {param} 규칙이 없습니다")
        if rk not in BASIC_RULES:
            raise _Refuse(path + ("params", "rules", j), f"'{rk}' 규칙은 보기만 합니다(Pack 함수 규칙은 고칠 수 없습니다)")
        if name == "unset":
            raise _Refuse(path + ("params", "rules", j), "규칙을 지우려면 remove_rule을 씁니다")
        rules[j] = {rk: copy.deepcopy(op.get("value"))}
        return
    m = DECIDE_TEXT_PARAM.match(param or "")
    if _kind(n) == "decide" and m:
        q, instr, key, idx = m.groups()
        spec = (p.get("questions") or {}).get(q)
        if not isinstance(spec, dict):
            raise _Refuse(path + ("params", "questions"), f"node '{n['id']}'에 질문 '{q}'가 없습니다(질문을 더하거나 이름을 바꿀 수는 없습니다)")
        value = op.get("value")
        if instr:
            if name == "unset":
                spec.pop("instructions", None)
            elif isinstance(value, str):
                spec["instructions"] = value
            else:
                raise _Refuse(path + ("params", "questions", q), "instructions는 글이어야 합니다")
            return
        crit = spec.get("criteria")
        if name == "unset":
            raise _Refuse(path + ("params", "questions", q), "criteria 설명은 지울 수 없습니다(글만 바꿀 수 있습니다)")
        if key is not None:
            if not (isinstance(crit, dict) and key in crit):
                raise _Refuse(path + ("params", "questions", q), f"질문 '{q}'에 선택지 '{key}'가 없습니다(선택지를 더하거나 바꿀 수는 없습니다)")
            if not (isinstance(value, str) or (value is None and spec.get("type") == "choice")):
                raise _Refuse(path + ("params", "questions", q), "criteria 설명은 글이어야 합니다")
            crit[key] = value
        else:
            j = int(idx)
            if not (isinstance(crit, list) and j < len(crit)):
                raise _Refuse(path + ("params", "questions", q), f"질문 '{q}'에 {j}번 단계가 없습니다(단계 수는 바꿀 수 없습니다)")
            if not isinstance(value, str):
                raise _Refuse(path + ("params", "questions", q), "단계 설명은 글이어야 합니다")
            crit[j] = value
        return
    if param not in _editable(doc, n):
        raise _Refuse(path + ("params",), f"node '{n['id']}'({n.get('kind')})의 '{param}'는 수정할 수 없는 항목입니다(보기만)")
    if name == "set":
        p[param] = copy.deepcopy(op.get("value"))
    else:
        p.pop(param, None)


def _set_option(g, n, path, op):
    """0.15.0 (D-476): the value of one declared option of a function node, inside its limits; the declaration stays. One option
    name in one graph is one value (D-476 덧 6): every function node of the graph that declares it gets the new value."""
    decl = (n.get("params") or {}).get("options")
    if _kind(n) != "custom_code" or not isinstance(decl, dict):
        raise _Refuse(path + ("params",), f"node '{n['id']}'에는 선언된 옵션이 없습니다")
    name = op.get("option")
    if name not in decl:
        raise _Refuse(path + ("params", "options"), f"node '{n['id']}'에 옵션 '{name}'가 없습니다(옵션을 더하거나 이름을 바꿀 수는 없습니다)")
    why = _options.value_problem(decl[name], op.get("value"))
    if why:
        raise _Refuse(path + ("params", "options", name), f"옵션 '{name}' — {why}")
    for other in g.get("nodes") or []:
        od = (other.get("params") or {}).get("options") if _kind(other) == "custom_code" else None
        if isinstance(od, dict) and name in od:
            od[name]["value"] = copy.deepcopy(op.get("value"))


def _basic_rule(rule, path, what="추가"):
    if not (isinstance(rule, dict) and len(rule) == 1 and next(iter(rule)) in BASIC_RULES):
        raise _Refuse(path, f"기본 규칙({', '.join(sorted(BASIC_RULES))})만 {what}할 수 있습니다: {rule!r}")


def _add_node(doc, g, gname, op):
    node = op.get("node")
    path = ("graphs", gname, "nodes")
    if not isinstance(node, dict):
        raise _Refuse(path, "추가할 node는 mapping이어야 합니다")
    extra = sorted(set(node) - NODE_KEYS)
    if extra:
        raise _Refuse(path, f"새 node에 쓸 수 없는 키입니다: {', '.join(extra)}")
    kind = node.get("kind")
    if kind not in ADDABLE:
        raise _Refuse(path, f"'{kind}' node는 추가할 수 없습니다(추가할 수 있는 종류: {', '.join(sorted(ADDABLE))})")
    state = g.get("state") or {}
    for k in ("reads", "writes"):
        new = [x for x in node.get(k, []) if x not in state]
        if new:
            raise _Refuse(path, f"새 state 키는 만들 수 없습니다({k}: {', '.join(map(str, new))}). 그래프 state에 있는 키만 고릅니다")
    p = node.get("params") or {}
    if kind == "llm@1" and p.get("output") not in ("text", *(doc.get("schemas") or {})):
        raise _Refuse(path, f"새 schema는 만들 수 없습니다(output '{p.get('output')}'). text 또는 schemas에 있는 이름을 고릅니다")
    if kind.startswith("validate"):
        for rule in p.get("rules") or []:
            _basic_rule(rule, path)
    nodes = g.setdefault("nodes", [])
    at = len(nodes)
    if op.get("after") is not None:
        ids = [n.get("id") for n in nodes]
        if op["after"] not in ids:
            raise _Refuse(path, f"'{op['after']}' 뒤에 넣으려 했지만 그 node가 없습니다")
        at = ids.index(op["after"]) + 1
    nodes.insert(at, copy.deepcopy(node))


def _write_file(files, folder, op):
    rel = op.get("path")
    parts = Path(rel).parts if isinstance(rel, str) else ()
    if not (len(parts) >= 2 and parts[0] in FILE_DIRS and ".." not in parts and not Path(rel).is_absolute()):
        raise _Refuse(("files",), f"prompts/·templates/ 안의 파일만 고칠 수 있습니다: {rel!r}")
    f = Path(folder) / rel
    if f.is_symlink() or not f.is_file():
        raise _Refuse(("files",), f"없는 파일입니다(파일은 고르기만 하고 새로 만들지 않습니다): {rel}")
    if not isinstance(op.get("text"), str):
        raise _Refuse(("files",), "파일 내용은 글이어야 합니다")
    files[Path(rel).as_posix()] = op["text"].encode("utf-8")


def _create_file(files, folder, op):
    """A new prompts/ or templates/ file (EV-022 CHG-117, S-5): the name must be free in the Pack and in this ops list."""
    rel = op.get("path")
    parts = Path(rel).parts if isinstance(rel, str) else ()
    if not (len(parts) >= 2 and parts[0] in FILE_DIRS and ".." not in parts and not Path(rel).is_absolute()):
        raise _Refuse(("files",), f"prompts/·templates/ 안에만 새 파일을 만들 수 있습니다: {rel!r}")
    if any(p.startswith(".") or p == "__pycache__" for p in parts):
        raise _Refuse(("files",), f"숨김 파일·폴더 이름은 쓸 수 없습니다: {rel}")
    key = Path(rel).as_posix()
    f = Path(folder) / rel
    for i in range(1, len(parts) - 1):   # every folder on the way must be a real folder (or not there yet), not a file or link
        up = Path(folder).joinpath(*parts[:i + 1])
        if up.is_symlink() or (up.exists() and not up.is_dir()):
            raise _Refuse(("files",), f"경로 중간의 '{Path(*parts[:i + 1]).as_posix()}'가 폴더가 아니어서 새 파일을 만들 수 없습니다: {rel}")
    if f.is_symlink() or f.exists():
        raise _Refuse(("files",), f"이미 있는 파일입니다(새 파일은 없는 이름으로 만듭니다 — 있는 파일은 write_file로 고칩니다): {rel}")
    # compared without letter case: on a case-insensitive file system (macOS) two such names are the same file
    if key.casefold() in {k.casefold() for k in files}:
        raise _Refuse(("files",), f"같은 수정 목록에서 이미 만든 파일입니다: {rel}")
    if not isinstance(op.get("text"), str):
        raise _Refuse(("files",), "파일 내용은 글이어야 합니다")
    files[key] = op["text"].encode("utf-8")


def _problem(stage, lines, path, message):
    return {"stage": stage, "line": strict_yaml.line_of(lines, path) if path else None, "path": "/".join(map(str, path)) if path else "",
            "message": message}


def _linked(folder):
    """Paths of the files a patch writes (pack.yaml, prompts/, templates/) that are links or lie under a linked folder: a write
    through them would land outside the copy, so such a Pack is refused (the original is only read)."""
    folder = Path(folder)
    found = [p for p in [folder / "pack.yaml", *(folder / d for d in FILE_DIRS)] if p.is_symlink()]
    for d in FILE_DIRS:
        if (folder / d).is_dir() and not (folder / d).is_symlink():
            found += [p for p in (folder / d).rglob("*") if p.is_symlink()]
    return [p.relative_to(folder).as_posix() for p in found]


def _inside(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or b in a.parents


def apply_patch(folder, ops, out_dir=None):
    folder = Path(folder)
    res = PatchResult()
    base = load_pack(folder)
    res.base_revision = base.revision
    if not base.ok:
        res.errors = list(base.errors) or [_problem("patch", {}, (), "원본 Pack을 읽을 수 없습니다")]
        return res
    if out_dir is not None:
        out = Path(out_dir)
        if _inside(out, folder):
            res.errors.append(_problem("patch", {}, (), f"사본 폴더가 원본 Pack 안에 있습니다: {out}"))
            return res
        if out.exists() and any(out.iterdir()):
            res.errors.append(_problem("patch", {}, (), f"사본 폴더가 비어 있지 않습니다: {out}"))
            return res
    linked = _linked(folder)
    if linked:
        res.errors.append(_problem("patch", {}, (), f"링크(symlink)인 Pack 파일·폴더는 고칠 수 없습니다: {', '.join(linked)}"))
        return res
    text = (folder / "pack.yaml").read_text(encoding="utf-8")
    doc, files = copy.deepcopy(base.doc), {}
    for i, op in enumerate(ops or []):
        try:
            _apply(doc, files, folder, op)
        except _Refuse as r:
            res.errors.append({**_problem("patch", base.lines, r.path, r.message), "op": i})
    if res.errors:
        return res
    try:
        new_text = yaml_write.rewrite(text, doc)
    except (yaml_write.WriteError, strict_yaml.StrictYAMLError) as e:
        res.errors.append(_problem("patch", {}, (), f"pack.yaml을 쓸 수 없습니다 — {e}"))
        return res
    new_files = {"pack.yaml": new_text.encode("utf-8"), **files}
    tmp = None
    if out_dir is None:
        tmp = tempfile.mkdtemp(prefix="packrt-patch-")
        out = Path(tmp) / folder.name
    try:
        # links are copied as plain files (their content), so nothing written below can reach the original through a link
        shutil.copytree(folder, out, dirs_exist_ok=True, symlinks=False, ignore_dangling_symlinks=True,
                        ignore=lambda d, names: [n for n in names if n == ".env" or n == "__pycache__" or (n.startswith(".") and (Path(d) / n).is_dir())])
        for rel, data in new_files.items():
            target = out / rel
            created = not (folder / rel).exists()   # only create_file names a file the original does not have
            if target.is_symlink() or not _inside(target.parent if created else target, out) or (not created and not target.is_file()) \
                    or (created and target.exists()):
                raise RuntimeError(f"사본 밖으로 쓰려 했습니다: {rel}")   # cannot happen after the checks above; never write through
            if created:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not _inside(target.parent, out):
                    raise RuntimeError(f"사본 밖으로 쓰려 했습니다: {rel}")
            target.write_bytes(data)
        new = load_pack(out)
        res.errors += new.errors
        res.warnings += new.warnings
        if new.ok:
            res.errors += [_problem("flow", new.lines, path, msg) for level, path, msg in flow.check(new.doc, out) if level == "error"]
        if not res.errors:
            res.revision = new.revision
        res.changed_files = sorted(rel for rel in _revision.revision_files(out)
                                   if not (folder / rel).is_file() or (folder / rel).read_bytes() != (out / rel).read_bytes())
        res.files = {rel: (out / rel).read_bytes() for rel in res.changed_files}
        res.ir_diff = ir_diff(base.doc, new.doc if new.doc is not None else doc)
        res.ok = not res.errors
        res.out_dir = None if tmp else str(out)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


def _remove_unreachable(g, gname):
    reached = semantic.Graph(gname, g).reach(semantic.START)
    dead = {n.get("id") for n in g.get("nodes", []) if n.get("id") not in reached}
    if not dead:
        return
    g["nodes"] = [n for n in g.get("nodes", []) if n.get("id") not in dead]
    edges = []
    for e in g.get("edges", []):
        if isinstance(e, list):
            if not set(e) & dead:
                edges.append(e)
        elif isinstance(e, dict) and "join" in e:
            if e.get("to") in dead:
                continue
            rest = [s for s in e["join"] if s not in dead]
            if len(rest) >= 2:
                edges.append({**e, "join": rest} if len(rest) < len(e["join"]) else e)
            elif rest:
                edges.append([rest[0], e["to"]])
        elif isinstance(e, dict) and not {e.get("from"), e.get("to")} & dead:
            edges.append(e)
    g["edges"] = edges


def _touches(e, node):
    if isinstance(e, list):
        return node in e
    if isinstance(e, dict) and "join" in e:
        return node in e["join"] or e.get("to") == node
    return isinstance(e, dict) and node in (e.get("from"), e.get("to"))


def delete_impact(folder, graph, node=None, edge=None):
    """What removing a node (with every edge that touches it) or one edge would do (EV-022 CHG-117, manual pages 17, 18, 34).
    Read only: the check runs apply_patch on a temporary copy, the original folder is not written.

    Returns {"ops": the remove_node/remove_edge/add_edge ops to send, "remove_edges": the edges taken away with the node
             (pack.yaml form), "add_edges": what a join keeps when the node was one of its sources (the join without the node,
             or a plain [source, to] when one source is left),
             "after": {"ok", "errors": the same problems apply_patch reports, "no_end": nodes that would not reach END,
                       "unreachable": nodes START would not reach}}.
    A function node is refused before anything is checked ("함수 node '…'는 삭제할 수 없습니다(연결 위치만 바꿀 수 있습니다)");
    a router case pointing at the node is kept, so the check reports "'route_evidence'에서 가는 대상 'draft'가 없습니다"."""
    folder = Path(folder)
    base = load_pack(folder)
    after = {"ok": False, "errors": [], "no_end": [], "unreachable": []}
    out = {"ops": [], "remove_edges": [], "add_edges": [], "after": after}
    if not base.ok:
        after["errors"] = list(base.errors) or [_problem("patch", {}, (), "원본 Pack을 읽을 수 없습니다")]
        return out
    try:
        if (node is None) == (edge is None):
            raise _Refuse((), "지울 node 또는 연결 하나를 고릅니다")
        g = _graph(base.doc, {"graph": graph})
        if node is not None:
            i, n = _node(g, {"node": node}, graph)
            _no_function_node(n, ("graphs", graph, "nodes", i), "삭제")
            out["remove_edges"] = [copy.deepcopy(e) for e in g.get("edges", []) if _touches(e, node)]
            for e in out["remove_edges"]:   # a join keeps its other sources (manual page 17: the evidence join of find_safety)
                if isinstance(e, dict) and "join" in e and e.get("to") != node:
                    rest = [s for s in e["join"] if s != node]
                    if len(rest) >= 2:
                        out["add_edges"].append({**copy.deepcopy(e), "join": rest})
                    elif rest:
                        out["add_edges"].append([rest[0], e["to"]])
            out["ops"] = [{"op": "remove_node", "graph": graph, "node": node}] + \
                         [{"op": "remove_edge", "graph": graph, "edge": e} for e in out["remove_edges"]] + \
                         [{"op": "add_edge", "graph": graph, "edge": e} for e in out["add_edges"]]
        else:
            if edge not in g.get("edges", []):
                raise _Refuse(("graphs", graph, "edges"), f"그래프 '{graph}'에 그런 연결이 없습니다: {edge}")
            out["ops"] = [{"op": "remove_edge", "graph": graph, "edge": copy.deepcopy(edge)}]
    except _Refuse as r:
        after["errors"] = [_problem("patch", base.lines, r.path, r.message)]
        return out
    res = apply_patch(folder, out["ops"])
    after["ok"], after["errors"] = res.ok, list(res.errors)
    g2 = copy.deepcopy(_graph(base.doc, {"graph": graph}))
    if node is not None:
        g2["nodes"] = [x for x in g2.get("nodes", []) if x.get("id") != node]
    g2["edges"] = [e for e in g2.get("edges", []) if not (e in out["remove_edges"] or (edge is not None and e == edge))] + \
                  copy.deepcopy(out["add_edges"])
    G = semantic.Graph(graph, g2)
    from_start = G.reach(semantic.START)
    after["unreachable"] = [nid for nid in G.nodes if nid not in from_start]
    after["no_end"] = [nid for nid in G.nodes if nid in from_start and semantic.END not in G.reach(nid)]
    return out


def _paths(a, b, prefix=""):
    """Dotted paths where b differs from a (rules[0].evidence.min_score)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in list(a) + [k for k in b if k not in a]:
            sub = f"{prefix}.{k}" if prefix else str(k)
            if k not in a or k not in b:
                out.append(sub)
            else:
                out += _paths(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _paths(x, y, f"{prefix}[{i}]")]
    return [] if a == b else [prefix or "(전체)"]


def ir_diff(old, new):
    """Node-level summary of what a patch changed."""
    out = {"graphs": {}, "other": []}
    og, ng = old.get("graphs") or {}, new.get("graphs") or {}
    for gname in list(og) + [g for g in ng if g not in og]:
        a, b = og.get(gname) or {}, ng.get(gname) or {}
        an = {n["id"]: n for n in a.get("nodes", [])}
        bn = {n["id"]: n for n in b.get("nodes", [])}
        ae, be = a.get("edges", []), b.get("edges", [])
        d = {"added_nodes": [k for k in bn if k not in an], "removed_nodes": [k for k in an if k not in bn],
             "changed_nodes": {k: _paths(an[k], bn[k]) for k in an if k in bn and an[k] != bn[k]},
             "edges_added": [e for e in be if e not in ae], "edges_removed": [e for e in ae if e not in be]}
        rest = _paths({k: v for k, v in a.items() if k not in ("nodes", "edges")}, {k: v for k, v in b.items() if k not in ("nodes", "edges")})
        if rest:
            d["other"] = rest
        if any(d.values()):
            out["graphs"][gname] = d
    out["other"] = _paths({k: v for k, v in old.items() if k != "graphs"}, {k: v for k, v in new.items() if k != "graphs"})
    return out


def bridge_ops(folder, graph, nodes, exit=None):
    """Take a group of nodes out and join every way into them to one exit again (D-462 ②, packrt E1 142). Read only, like
    delete_impact: apply_patch runs on a temporary copy.

    Ways in from outside the group are moved to the exit: a pair edge [a, x] → [a, exit], a router case or default, a validate
    on_fail and a review route are set to the exit, a join or loop going to x goes to the exit. When the group only leaves through
    joins (it is a join source), each of its places in the join takes its one outside predecessor (none or several: the place is
    dropped) and the pair edges into it go, so the join target still runs once. Nodes START no longer reaches afterwards are
    taken out with their edges (remove_unreachable), function nodes too. The exit is `exit` when given; else (a) the one node the
    group leaves to, then (b) the other way of a check whose on_fail led into the group — the first that passes apply_patch.
    A router, validate or review node in the group needs `exit`; a function node in the group, or a way in through on_error, is
    refused, and so is the start of a loop edge without its target (the loop's bound would be lost). The fixed flow is not judged
    here (check_fixed_flow does that).

    Returns {"ops", "exit", "pruned": nodes taken out with the group, "tried": [{"exit", "ok", "errors"}],
             "after": {"ok", "errors", "no_end", "unreachable"}}."""
    folder = Path(folder)
    after = {"ok": False, "errors": [], "no_end": [], "unreachable": []}
    out = {"ops": [], "exit": None, "pruned": [], "tried": [], "after": after}
    base = load_pack(folder)
    if not base.ok:
        after["errors"] = list(base.errors) or [_problem("patch", {}, (), "원본 Pack을 읽을 수 없습니다")]
        return out
    try:
        g = _graph(base.doc, {"graph": graph})
        group = list(dict.fromkeys(nodes or []))
        if not group or not all(isinstance(n, str) for n in group):
            raise _Refuse(("graphs", graph, "nodes"), "뺄 node를 하나 이상 고릅니다")
        G = semantic.Graph(graph, g)
        inside = set(group)
        for nid in group:
            i, n = _node(g, {"node": nid}, graph)
            _no_function_node(n, ("graphs", graph, "nodes", i), "삭제")
            ways = {y for y, _ in G.succ.get(nid, []) if y not in inside}
            if _kind(n) in BRANCH_KINDS and exit is None and len(ways) > 1:   # its ways out go to more than one place
                raise _Refuse(("graphs", graph, "nodes", i), f"갈래 node '{nid}'를 빼려면 이어 갈 곳(exit)을 줍니다")
        ins = [(a, b, how) for a, b, how in G.targets() if a not in inside and b in inside]
        outs = [(a, b, how) for a, b, how in G.targets() if a in inside and b not in inside]
        if any(how[0] == "on_error" for _, _, how in ins):
            raise _Refuse(("graphs", graph, "nodes"), "on_error 갈래로 들어오는 node는 다시 이을 수 없습니다")
        for i, e in G.loops:   # the bound of a loop lives on its edge: taking out its start alone would leave the cycle unbounded
            if e.get("from") in inside and e.get("to") not in inside:
                raise _Refuse(("graphs", graph, "edges", i), f"반복 연결 {e['from']} → {e['to']}의 시작 node만 빼면 반복 한도가 사라집니다 — "
                                                             "반복 묶음 전체를 함께 뺍니다")
        if exit is None and outs and all(how[0] == "join" for _, _, how in outs):
            plans = [("join", None)]
        else:
            if any(how[0] == "join" for _, _, how in outs):
                raise _Refuse(("graphs", graph, "edges"), "빼는 node가 join 원천이면서 다른 곳으로도 나갑니다 — 나눠서 뺍니다")
            if exit is not None:
                if exit != semantic.END and exit not in G.nodes or exit in inside:
                    raise _Refuse(("graphs", graph, "nodes"), f"이어 갈 곳 '{exit}'가 그래프에 없거나 빼는 묶음 안에 있습니다")
                exits = [exit]
            else:
                leave = list(dict.fromkeys(b for _, b, _ in outs))
                exits = leave if len(leave) == 1 else []
                for a, _, how in ins:   # (b) the other way of a check whose failure led into the group
                    if how[0] == "on_fail":
                        exits += [y for y, h in G.succ.get(a, []) if h[0] == "edge" and y not in inside]
                exits = list(dict.fromkeys(exits))
            if not exits:
                raise _Refuse(("graphs", graph, "nodes"), "빼는 묶음에서 나가는 곳이 하나가 아니라 이어 갈 곳(exit)을 줍니다")
            plans = [("exit", e) for e in exits]
    except _Refuse as r:
        after["errors"] = [_problem("patch", base.lines, r.path, r.message)]
        return out
    for mode, e in plans:
        try:
            ops = _bridge_plan(g, graph, group, ins, mode, e)
            doc = copy.deepcopy(base.doc)
            for op in ops:
                _apply(doc, {}, folder, op)
        except _Refuse as r:
            out["tried"].append({"exit": e, "ok": False, "errors": [_problem("patch", base.lines, r.path, r.message)]})
            continue
        res = apply_patch(folder, ops)
        target = e if mode == "exit" else next(b for _, b, _ in outs)
        out["tried"].append({"exit": target, "ok": res.ok, "errors": list(res.errors)})
        if res.ok or (mode, e) == plans[-1]:
            left = {n["id"] for n in _graph(doc, {"graph": graph}).get("nodes", [])}
            g2 = _graph(doc, {"graph": graph})
            G2 = semantic.Graph(graph, g2)
            from_start = G2.reach(semantic.START)
            out.update(ops=ops if res.ok else [], exit=target if res.ok else None,
                       pruned=[n["id"] for n in g.get("nodes", []) if n["id"] not in left and n["id"] not in inside] if res.ok else [])
            after.update(ok=res.ok, errors=list(res.errors), unreachable=[n for n in G2.nodes if n not in from_start],
                         no_end=[n for n in G2.nodes if n in from_start and semantic.END not in G2.reach(n)])
            if res.ok:
                break
    if not out["tried"]:
        return out
    if not after["ok"] and not after["errors"]:
        after["errors"] = out["tried"][-1]["errors"]
    return out


def _bridge_plan(g, gname, group, ins, mode, exit):
    """The ops of one bridge: ways in moved (or, in join mode, join places taken by the outside predecessor), the group's edges
    and nodes taken out, then remove_unreachable."""
    inside = set(group)
    edges = copy.deepcopy(g.get("edges", []))
    ops = []
    nodes = {n["id"]: n for n in g.get("nodes", [])}

    def remove(e):
        if e in edges:
            edges.remove(e)
            ops.append({"op": "remove_edge", "graph": gname, "edge": copy.deepcopy(e)})

    def add(e):
        if e not in edges:
            edges.append(e)
            ops.append({"op": "add_edge", "graph": gname, "edge": copy.deepcopy(e)})

    if mode == "join":
        preds = {}
        for a, b, how in ins:
            if how[0] != "edge":
                raise _Refuse(("graphs", gname, "nodes"), f"join 원천 '{b}'로 갈래({how[0]})가 들어옵니다 — 이어 갈 곳(exit)을 줍니다")
            preds.setdefault(b, []).append(a)
        for a, b, _ in ins:
            remove([a, b])
        for e in [e for e in g.get("edges", []) if isinstance(e, dict) and "join" in e and set(e["join"]) & inside]:
            rest = []
            for s in e["join"]:
                for x in ([s] if s not in inside else (preds.get(s, []) if len(preds.get(s, [])) == 1 else [])):
                    if x not in rest:
                        rest.append(x)
            remove(e)
            if len(rest) >= 2:
                add({**copy.deepcopy(e), "join": rest})
            elif rest:
                add([rest[0], e["to"]])
    else:
        set_ops = {}
        for a, b, how in ins:
            kind = how[0]
            if kind == "edge":
                remove([a, b])
                if a != exit:
                    add([a, exit])
            elif kind == "join":
                j = g["edges"][how[1]]
                remove(j)
                add({**copy.deepcopy(j), "to": exit})
            elif kind == "loop":
                lp = g["edges"][how[1]]
                remove(lp)
                if a != exit:
                    add({**copy.deepcopy(lp), "to": exit})
            elif kind in ("case", "default"):
                p = set_ops.setdefault((a, "cases"), copy.deepcopy((nodes[a].get("params") or {}).get("cases") or {}))
                if kind == "case":
                    p[how[1]] = exit
                else:
                    set_ops[(a, "default")] = exit
            elif kind == "on_fail":
                set_ops[(a, "on_fail")] = exit
            elif kind == "route":
                r = set_ops.setdefault((a, "routes"), copy.deepcopy((nodes[a].get("params") or {}).get("routes") or {}))
                r[how[1]] = exit
        for (a, param), value in set_ops.items():
            if param == "cases" and value == ((nodes[a].get("params") or {}).get("cases") or {}):
                continue
            ops.append({"op": "set", "graph": gname, "node": a, "param": param, "value": value})
    for e in copy.deepcopy(edges):
        if isinstance(e, list) and set(e) & inside:
            remove(e)
        elif isinstance(e, dict) and "join" in e and (set(e["join"]) & inside or e.get("to") in inside):
            remove(e)
        elif isinstance(e, dict) and "from" in e and {e.get("from"), e.get("to")} & inside:
            remove(e)
    ops += [{"op": "remove_node", "graph": gname, "node": n} for n in group]
    ops.append({"op": "remove_unreachable", "graph": gname})
    return ops
