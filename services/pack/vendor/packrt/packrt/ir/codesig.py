"""The code signature of a Pack function rule (D-462 ①, packrt E1 142 · 덧 1): what a `custom: "<module>:<function>"` rule runs.

    function_sig(folder, ref) -> "sha256:…" | None   (None: the module or the function cannot be read)

The function and what it uses inside the Pack are collected by reading the code (never run): top-level functions, classes and
module constants of the same file it names, attributes of a Pack module alias (`rg.is_grounded`, `rg._SECTION`) and names
imported from a Pack module (`from custom.llm_base import Draft`), followed in turn. Each piece counts as its AST without
docstrings, so comments, blank lines and docstrings do not change the signature, and other functions of the same file are not in
it. The AST is written out by `_canon`, not `ast.dump`, so the signature is the same on every Python version (0.13.2: `ast.dump`
of 3.13 and later leaves out empty fields, so 3.12 and 3.14 gave different signatures for the same function). A use that cannot be followed — a Pack module alias used as a value, `import *` from a Pack module, a name a Pack module does
not define — counts that whole module file (the strict side). A data file named in the collected code (.json · .yaml · .yml ·
.txt · .csv, found next to the module or under the Pack folder) counts by its bytes. Names outside the Pack (standard library,
installed packages) count only as the names in the AST."""
import ast
import copy
import hashlib
import json
from pathlib import Path

DATA_EXT = (".json", ".yaml", ".yml", ".txt", ".csv")


def _module_file(folder, dotted):
    base = Path(folder, *dotted.split("."))
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.is_file():
            return p
    return None


def _strip_doc(node):
    node = copy.deepcopy(node)
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            n.body = body[1:] or [ast.Pass()]
    return node


def _canon(node):
    """The AST as plain lists: node type, then [field, value] for each field that is not None or empty. Field names and constant
    values are written the same way on every Python version; fields a version adds stay empty for code that does not use them."""
    if isinstance(node, ast.AST):
        out = [type(node).__name__]
        for field in node._fields:
            value = getattr(node, field, None)
            if value is None or (isinstance(value, list) and not value):
                continue
            out.append([field, _canon(value)])
        return out
    if isinstance(node, list):
        return [_canon(v) for v in node]
    return [type(node).__name__, repr(node)]


class _Module:
    def __init__(self, folder, dotted, path):
        self.dotted, self.path = dotted, path
        self.raw = path.read_bytes()
        tree = ast.parse(self.raw.decode("utf-8"))
        self.defs, self.mods, self.names, self.star = {}, {}, {}, []
        package = dotted if path.name == "__init__.py" else dotted.rpartition(".")[0]

        def absolute(module, level):
            if not level:
                return module or ""
            parts = package.split(".") if package else []
            parts = parts[:len(parts) - (level - 1)] if level > 1 else parts
            return ".".join([*parts, *([module] if module else [])])

        def collect(stmts):
            for s in stmts:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.defs[s.name] = s
                elif isinstance(s, ast.Assign):
                    for t in s.targets:
                        for n in ast.walk(t):
                            if isinstance(n, ast.Name):
                                self.defs[n.id] = s
                elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                    self.defs[s.target.id] = s
                elif isinstance(s, ast.Import):
                    for a in s.names:
                        if _module_file(folder, a.name):
                            if a.asname:
                                self.mods[a.asname] = a.name
                            else:
                                self.mods[a.name.split(".")[0]] = a.name.split(".")[0]
                elif isinstance(s, ast.ImportFrom):
                    mod = absolute(s.module, s.level)
                    if not mod or not _module_file(folder, mod):
                        continue
                    for a in s.names:
                        local = a.asname or a.name
                        if a.name == "*":
                            self.star.append(mod)
                        elif _module_file(folder, f"{mod}.{a.name}"):
                            self.mods[local] = f"{mod}.{a.name}"
                        else:
                            self.names[local] = (mod, a.name)
                elif isinstance(s, (ast.If, ast.Try, ast.With)):
                    collect(getattr(s, "body", []))
                    collect(getattr(s, "orelse", []))
                    for h in getattr(s, "handlers", []):
                        collect(h.body)
                    collect(getattr(s, "finalbody", []))

        collect(tree.body)


class _Collector:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.modules, self.parts, self.seen = {}, set(), set()

    def module(self, dotted):
        if dotted not in self.modules:
            path = _module_file(self.folder, dotted)
            try:
                self.modules[dotted] = _Module(self.folder, dotted, path) if path else None
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                self.modules[dotted] = None
        return self.modules[dotted]

    def whole(self, dotted):
        m = self.module(dotted)
        if m is None:
            raise LookupError(dotted)
        self.parts.add(("file", dotted, hashlib.sha256(m.raw).hexdigest()))

    def visit(self, dotted, name):
        if (dotted, name) in self.seen:
            return
        self.seen.add((dotted, name))
        m = self.module(dotted)
        if m is None:
            raise LookupError(dotted)
        if name in m.names:
            return self.visit(*m.names[name])
        if name in m.mods:
            return self.whole(m.mods[name])
        node = m.defs.get(name)
        if node is None:
            for star in m.star:
                self.whole(star)
            return self.whole(dotted)   # a name the module does not define where we can see it
        self.parts.add(("def", dotted, name, json.dumps(_canon(_strip_doc(node)), ensure_ascii=False)))
        self.scan(m, node)

    def attr(self, dotted, name):
        m = self.module(dotted)
        if m is not None and _module_file(self.folder, f"{dotted}.{name}") and name not in m.defs:
            return self.whole(f"{dotted}.{name}")
        return self.visit(dotted, name)

    def data(self, m, text):
        for base in (m.path.parent, self.folder):
            p = base / text
            if p.is_file():
                self.parts.add(("data", text, hashlib.sha256(p.read_bytes()).hexdigest()))
                return

    def scan(self, m, node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in m.mods:
            return self.attr(m.mods[node.value.id], node.attr)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in m.mods:
                self.whole(m.mods[node.id])   # the module itself as a value: cannot be followed
            elif node.id in m.names:
                self.visit(*m.names[node.id])
            elif node.id in m.defs:
                self.visit(m.dotted, node.id)
            elif m.star:
                for star in m.star:
                    self.whole(star)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(DATA_EXT):
            self.data(m, node.value)
        for child in ast.iter_child_nodes(node):
            self.scan(m, child)


def function_sig(folder, ref):
    """The signature of the Pack function `ref` ("custom.report:grounded"), or None when it cannot be read."""
    if not isinstance(ref, str) or ref.count(":") != 1:
        return None
    dotted, name = ref.split(":")
    c = _Collector(folder)
    try:
        m = c.module(dotted)
        if m is None or not isinstance(m.defs.get(name), (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        c.visit(dotted, name)
    except (LookupError, RecursionError):
        return None
    text = json.dumps(sorted(c.parts), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
