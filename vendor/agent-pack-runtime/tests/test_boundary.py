"""Import boundary (llmops-lite-v2 PRES-91 · CHG-91, EV-023 PRES-204): packrt never imports llmops; tracewriter imports neither
llmops nor packrt — in the source (static) and after importing every module in a fresh interpreter (run time)."""
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = {"packrt": {"llmops"}, "tracewriter": {"llmops", "packrt"}}


def static_imports(pkg_dir):
    out = set()
    files = list(pkg_dir.rglob("*.py"))
    assert files, f"no Python files under {pkg_dir}"   # a moved folder must not make the check pass silently
    for f in files:
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                out |= {(f.relative_to(ROOT).as_posix(), a.name.split(".")[0]) for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                out.add((f.relative_to(ROOT).as_posix(), n.module.split(".")[0]))
            elif isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in ("import_module", "__import__") \
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.add((f.relative_to(ROOT).as_posix(), n.args[0].value.split(".")[0]))
    return out


def test_static_boundary():
    bad = [(f, m) for top, deny in RULES.items() for f, m in static_imports(ROOT / top / top) if m in deny]
    assert not bad, bad


def test_runtime_boundary(tmp_path):
    for top, deny in RULES.items():   # run outside the repository root: its packrt/ project folder would shadow the package
        code = (f"import pkgutil, importlib, sys, json, {top}\n"
                f"mods = [m.name for m in pkgutil.walk_packages({top}.__path__, '{top}.')]\n"
                "[importlib.import_module(m) for m in mods]\n"
                "assert len(mods) >= 5 and getattr(sys.modules[mods[0]], '__file__', None), mods\n"
                f"print({top}.__file__)\n"
                "print(json.dumps(sorted({k.split('.')[0] for k in sys.modules})))")
        lines = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path).stdout.splitlines()
        assert Path(lines[0]).resolve().is_relative_to(ROOT / top), lines[0]   # this repository's package, not another installed copy
        loaded = set(json.loads(lines[-1]))
        assert not (loaded & deny), (top, loaded & deny)


def test_the_static_check_catches_a_planted_import(tmp_path):
    (tmp_path / "x.py").write_text("import llmops.store\n", encoding="utf-8")
    global ROOT
    old, ROOT = ROOT, tmp_path
    try:
        assert ("x.py", "llmops") in static_imports(tmp_path)
    finally:
        ROOT = old
