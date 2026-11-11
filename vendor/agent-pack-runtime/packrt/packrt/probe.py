"""Pack graph probe (EV-002 CHG-07, CHG-08): builds the graph in the Pack environment without running it and prints the
projection LLMOps compares with the static graph from pack.yaml. Errors are printed as JSON {"error", "message"}.
EV-006 N-3 (contract C · D-267 C-9): "index_missing" lists the retrieve@2 stores with no vector DB version of their node's recipe
([{store, node, index_recipe_sha256, build_sha256}]) so LLMOps knows what to build first — always on the projection ([] when none),
and on the error shape when the list is not empty (the build then fails with that IndexMissing).
EV-008 CHG-P01 7 (D-308): "index_unchecked" lists the retrieve@2 pgvector stores the probe cannot choose a version for without a
database ([{store, node, index_recipe_sha256}]) — the same rule: always on the projection, on the error shape only when not empty."""
import json
import sys


def _index_lists(pack_dir):
    """The execution config read once more with collect_missing (no database, like the build below): (index_missing,
    index_unchecked); ([], []) when it cannot be read — the graph build then reports that error itself."""
    try:
        from packrt.ir import load_pack, releases
        res = load_pack(pack_dir)
        if not res.ok:
            return [], []
        from pathlib import Path
        out = releases.load_execution_config(Path(pack_dir) / "execution-config.yaml", res.doc, pg_lookup=False, collect_missing=True)
        return out["index_missing"], out["index_unchecked"]
    except Exception:  # noqa: BLE001 - any other config error is the build's to report
        return [], []


def main(pack_dir):
    missing, unchecked = _index_lists(pack_dir)
    try:
        from packrt.run.engine import Runtime
        rt = Runtime(pack_dir, pg_lookup=False)   # building the graph reads no data: no database, no DSN (EV-020 CHG-97)
        return {**rt.projection(), "index_missing": missing, "index_unchecked": unchecked}
    except BaseException as e:  # noqa: BLE001 - a broken Pack environment must be reported, not crash
        out = {"error": "build", "message": f"{type(e).__name__}: {e}"[:500]}
        if missing:   # an error with empty lists keeps its 0.11.0 shape
            out["index_missing"] = missing
        if unchecked:
            out["index_unchecked"] = unchecked
        return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False, default=str))
