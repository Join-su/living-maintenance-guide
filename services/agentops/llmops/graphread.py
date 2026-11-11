"""Read a release's LangGraph code (B-04): static ast read of the entry file + runtime graph from the sandboxed probe,
compared; stable node/edge IDs; source hash; layout for the run-flow canvas.

Entry convention: graph.py or agent.py in the release folder defines build_graph() returning a StateGraph or a compiled
graph; optional constant INPUT_FIELD. The original folder is only read (the probe runs in the Agent sandbox with the
store and the folder write-denied)."""
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

from . import guard

ENTRY_FILES = ("graph.py", "agent.py")
START, END = "__start__", "__end__"
MSG = {
    "no-entry": "그래프를 돌려주는 진입점을 찾을 수 없습니다 (StateGraph를 만드는 함수가 없음)",
    "import": "코드를 불러오는 중 오류가 났습니다 — {detail}",
    "build": "build_graph()를 부르는 중 오류가 났습니다 — {detail}",
    "path-missing": "경로를 찾을 수 없습니다 — {detail}",
    "path-not-folder": "폴더가 아닙니다 — {detail}",
    "path-unreadable": "폴더를 읽을 수 없습니다 — {detail} (읽기 권한을 확인하세요)",
    "upload-empty": "올린 파일이 없습니다",
    "timeout": "코드를 읽는 데 시간이 너무 오래 걸립니다 (제한 {detail}초)",
    "path-invalid": "이 경로는 쓸 수 없습니다 — 큰따옴표·백슬래시·줄바꿈이 든 경로는 sandbox에 넣을 수 없습니다: {detail}",
}
PROBE = Path(__file__).with_name("graph_probe.py")
PROBE_TIMEOUT_S = 30


class ReadError(Exception):
    def __init__(self, kind, detail=""):
        self.kind, self.detail = kind, detail
        super().__init__(MSG[kind].format(detail=detail))


def sha(text, n=8):
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def source_hash(folder):
    """SHA-256 over the release's .py source files (relative path + bytes), the code identity of a release."""
    h = hashlib.sha256()
    for p in sorted(Path(folder).rglob("*.py")):
        if "__pycache__" in p.parts or any(part.startswith(".") for part in p.relative_to(folder).parts):
            continue
        h.update(str(p.relative_to(folder)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def check_folder(path):
    p = Path(path).expanduser()
    if not p.exists():
        raise ReadError("path-missing", str(path))
    if not p.is_dir():
        raise ReadError("path-not-folder", str(path))
    try:
        list(p.iterdir())
    except OSError:
        raise ReadError("path-unreadable", str(path))
    if not os.access(p, os.R_OK | os.X_OK):
        raise ReadError("path-unreadable", str(path))
    return p.resolve()


def find_entry(folder):
    for name in ENTRY_FILES:
        f = Path(folder) / name
        if f.is_file():
            try:
                tree = ast.parse(f.read_text(errors="replace"))
            except SyntaxError as e:
                raise ReadError("import", guard.redact(f"SyntaxError: {e}"))
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "build_graph" for n in tree.body):
                return f, tree
    raise ReadError("no-entry")


def _name(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return {"START": START, "END": END}.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal(node):
    """A node reference that can be read without running the code: a string, or START/END."""
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)) or (isinstance(node, ast.Name) and node.id in ("START", "END"))


def declared(tree):
    """Static read: nodes (with line ranges), plain edges, conditional edges (router name), dynamic (in loops)."""
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    def in_loop(n):
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return True
        return False
    out = {"nodes": {}, "edges": [], "conditional": [], "dynamic_calls": 0, "input_field": None}
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "INPUT_FIELD" for t in n.targets) and isinstance(n.value, ast.Constant):
            out["input_field"] = n.value.value
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        m, args = call.func.attr, call.args
        if m == "add_node" and args:
            name = _name(args[0])
            if in_loop(call) or name is None:
                out["dynamic_calls"] += 1
                continue
            out["nodes"][name] = {"line": call.lineno, "end_line": call.end_lineno}
        elif m == "add_edge" and len(args) >= 2:
            srcs = args[0].elts if isinstance(args[0], (ast.List, ast.Tuple)) else [args[0]]
            if in_loop(call) or not all(_literal(x) for x in [*srcs, args[1]]):
                out["dynamic_calls"] += 1  # edge built from variables: not comparable statically
                continue
            for s in srcs:
                out["edges"].append((_name(s), _name(args[1]), call.lineno))
        elif m == "add_conditional_edges" and len(args) >= 2:
            out["conditional"].append({"source": _name(args[0]), "router": _name(args[1]), "line": call.lineno})
        elif m == "set_entry_point" and args:
            out["edges"].append((START, _name(args[0]), call.lineno))
        elif m == "set_finish_point" and args:
            out["edges"].append((_name(args[0]), END, call.lineno))
    return out


def runtime(entry, folder, write_deny, timeout=PROBE_TIMEOUT_S):
    try:
        r = guard.sandboxed_subprocess([sys.executable, str(PROBE), str(entry)], cwd=folder, write_deny=write_deny, timeout=timeout)
    except Exception as e:  # subprocess.TimeoutExpired
        if type(e).__name__ == "TimeoutExpired":
            raise ReadError("timeout", str(timeout))
        if isinstance(e, guard.IsolationRefused):   # EV-VPS-01 CHG-V03
            raise ReadError("import", str(e))
        raise
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise ReadError("import", guard.redact((r.stderr or "출력 없음").strip().splitlines()[-1][:300] if (r.stderr or "").strip() else "출력 없음"))
    if "error" in res:
        kind = res["error"] if res["error"] in ("import", "build") else "no-entry"
        raise ReadError(kind, guard.redact(res.get("message", ""))[:300])
    return res


def back_edges(names, edges):
    """Edges that close a cycle (DFS from START, then from any unvisited node)."""
    out = {}
    for s, t in edges:
        out.setdefault(s, []).append(t)
    back, state = set(), {}
    def dfs(n):
        state[n] = 1
        for t in out.get(n, []):
            if state.get(t) == 1:
                back.add((n, t))
            elif t not in state:
                dfs(t)
        state[n] = 2
    for root in [START, *names]:
        if root not in state:
            dfs(root)
    return back


def _layout(names, edges, back):
    """Left-to-right layers by longest path over the graph without back edges, in canvas coordinates."""
    fwd = [(s, t) for s, t in edges if (s, t) not in back and s in names and t in names]
    depth = {n: 0 for n in names}
    for _ in range(len(names)):
        changed = False
        for s, t in fwd:
            if depth[t] < depth[s] + 1:
                depth[t] = depth[s] + 1; changed = True
        if not changed:
            break
    cols = {}
    for n in sorted(names, key=lambda n: (depth[n], n)):
        cols.setdefault(depth[n], []).append(n)
    pos = {n: (20 + d * 130, 20 + i * 50) for d, col in cols.items() for i, n in enumerate(col)}
    height = max(150, 40 + 50 * max((len(c) for c in cols.values()), default=1))
    width = max(680, 140 + 130 * max(cols, default=0))
    return pos, height, width


def build(folder, entry_rel, decl, rt):
    rt_nodes = [n for n in rt["nodes"] if n not in (START, END)]
    rt_edges = [(e["source"], e["target"], e["conditional"], e["data"]) for e in rt["edges"]]
    declared_nodes = decl["nodes"]
    dynamic = sorted(n for n in rt_nodes if n not in declared_nodes)
    plain_decl = {(s, t) for s, t, _ in decl["edges"]}
    plain_rt = {(s, t) for s, t, c, _ in rt_edges if not c}
    mismatch = []
    missing_rt = sorted(set(declared_nodes) - set(rt_nodes))
    if missing_rt:
        mismatch.append("코드에 선언됐지만 실행 그래프에 없는 node: " + ", ".join(missing_rt))
    if dynamic and not decl["dynamic_calls"]:
        mismatch.append("실행 그래프에만 있는 node: " + ", ".join(dynamic))
    known = set(declared_nodes) | {START, END}
    extra_edges = sorted(f"{s}→{t}" for s, t in plain_rt - plain_decl if s in known and t in known)
    missing_edges = sorted(f"{s}→{t}" for s, t in plain_decl - plain_rt)
    if extra_edges or missing_edges:
        mismatch.append("edge 차이: " + ", ".join([f"실행에만 {e}" for e in extra_edges] + [f"코드에만 {e}" for e in missing_edges]))
    routers = {c["source"]: c["router"] for c in decl["conditional"]}
    pairs = [(s, t) for s, t, _, _ in rt_edges]
    back = back_edges(rt_nodes, pairs)
    pos, h, w = _layout(rt_nodes, pairs, back)
    out_plain = {}
    for s, t, c, _ in rt_edges:
        if not c and s in rt_nodes and t in rt_nodes:
            out_plain.setdefault(s, []).append(t)
    edges = []
    for s, t, c, data in rt_edges:
        if s not in rt_nodes or t not in rt_nodes:
            continue
        kind = "loop" if (s, t) in back else ("par" if not c and len(out_plain.get(s, [])) > 1 else ("cond" if c else ""))
        label = (routers.get(s) or "조건") + (f": {data}" if data and data != t else "") if c else ""
        edges.append({"id": "e-" + sha(f"{entry_rel}:{s}>{t}:{label}"), "source": s, "target": t, "label": label, "kind": kind, "conditional": c})
    nodes = []
    for n in rt_nodes:
        d = declared_nodes.get(n)
        nodes.append({"id": "n-" + sha(f"{entry_rel}:{n}"), "name": n, "x": pos[n][0], "y": pos[n][1],
                      "loc": f"{entry_rel}:{d['line']}" + (f"-{d['end_line']}" if d and d["end_line"] != d["line"] else "") if d else None,
                      "dynamic": n in dynamic})
    structure = json.dumps([sorted(rt_nodes), sorted((e["source"], e["target"], e["kind"]) for e in edges)])
    counts = {"node": len(nodes), "edge": len(edges), "조건부": sum(e["conditional"] for e in edges),
              "loop": sum(e["kind"] == "loop" for e in edges), "병렬": len({e["source"] for e in edges if e["kind"] == "par"})}
    return {"id": "g-" + sha(structure, 4), "nodes": nodes, "edges": edges, "entry": entry_rel, "width": w, "height": h,
            "input_field": rt.get("input_field") or decl["input_field"] or "input", "mismatch": mismatch, "dynamic": dynamic, "counts": counts,
            "start": [t for s, t, c, _ in rt_edges if s == START and not c and t in rt_nodes]}   # EV-022 019d-1: START's plain targets (not drawn)


def read_release(folder, write_deny):
    """Full read of a release folder. Raises ReadError with a user-facing reason."""
    if any(ch in str(folder) for ch in '"\\\n'):
        raise ReadError("path-invalid", str(folder))
    folder = check_folder(folder)
    entry, tree = find_entry(folder)
    decl = declared(tree)
    try:
        rt = runtime(entry, folder, write_deny=[*write_deny, folder])
    except ValueError as e:  # a resolved path the sandbox profile cannot hold
        raise ReadError("path-invalid", str(e))
    code = source_hash(folder)
    graph = build(folder, entry.name, decl, rt)
    return {"code_hash": code, "code": code[:4], "graph": graph}
