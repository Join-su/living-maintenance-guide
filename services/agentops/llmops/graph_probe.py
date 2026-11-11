"""Runtime graph probe (B-04). Runs INSIDE the Agent sandbox (llmops.guard.sandboxed_subprocess), cwd = release folder.

  python graph_probe.py <entry-file>
Imports the entry module, calls build_graph(), compiles it if needed and prints the runtime graph as JSON. It never runs
the graph. Errors are printed as JSON {"error": kind, "message": ...}."""
import importlib.util
import json
import os
import sys


def main(entry):
    folder = os.path.dirname(os.path.abspath(entry))
    sys.path.insert(0, folder)
    try:
        spec = importlib.util.spec_from_file_location("llmops_release_entry", entry)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except BaseException as e:  # noqa: BLE001 - user code may raise anything, including SystemExit
        return {"error": "import", "message": f"{type(e).__name__}: {e}"}
    fn = getattr(mod, "build_graph", None)
    if not callable(fn):
        return {"error": "no-entry", "message": "build_graph 없음"}
    try:
        g = fn()
        if hasattr(g, "compile") and not hasattr(g, "get_graph"):
            g = g.compile()
        drawable = g.get_graph()
    except BaseException as e:  # noqa: BLE001
        return {"error": "build", "message": f"{type(e).__name__}: {e}"}
    nodes = sorted(drawable.nodes)
    edges = sorted({(e.source, e.target, bool(e.conditional), str(e.data) if e.data is not None else "") for e in drawable.edges})
    return {"nodes": nodes, "edges": [{"source": s, "target": t, "conditional": c, "data": d} for s, t, c, d in edges],
            "input_field": getattr(mod, "INPUT_FIELD", None) if isinstance(getattr(mod, "INPUT_FIELD", None), str) else None}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False))
