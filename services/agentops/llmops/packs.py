"""Agent Pack releases (EV-002 CHG-01, CHG-02, CHG-04, CHG-08): detection, loading checks through packrt.ir, the static
run-flow graph read from pack.yaml, the execution config, and the unchangeable copy under <data dir>/packs/<release id>/.

The server only imports packrt.ir (no Pack code runs here). The work folder is read only; the copy holds the revision
files and the execution config, never .env, .venv or evals/."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import packrt
import tracewriter

from packrt import ir
from packrt.ir import releases

from . import graphread
from .graphread import END, START

PACK_FILE = "pack.yaml"
CONFIG_COPY = "execution-config.yaml"


class PackError(Exception):
    """A Pack that cannot be registered. kind: pack-incompatible | pack-coexist | pack-local-only | pack-config."""

    def __init__(self, kind, message, errors=()):
        self.kind, self.message, self.errors = kind, message, list(errors)
        super().__init__(message)

    def detail(self):
        return {"kind": self.kind, "message": self.message, "errors": self.errors}


def is_pack(folder):
    return (Path(folder) / PACK_FILE).is_file()


def check_coexist(folder):
    folder = Path(folder)
    if is_pack(folder):
        py = [n for n in graphread.ENTRY_FILES if (folder / n).is_file()]
        if py:
            raise PackError("pack-coexist", "등록 안 됨 — Agent Pack과 Python 진입점이 함께 있습니다 — 한 폴더에는 하나만 두세요")


def _kind(node):
    return node["kind"].split("@")[0]


def _fn_info(folder, fn):
    import ast
    import hashlib
    mod, func = fn.split(":")
    rel = Path(*mod.split(".")).with_suffix(".py")
    data = (Path(folder) / rel).read_bytes()
    line = next((x.lineno for x in ast.parse(data.decode("utf-8")).body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == func), None)
    return {"fn_loc": f"{rel.as_posix()}:{line}", "source_sha256": hashlib.sha256(data).hexdigest()}


def static_graph(doc, lines, entry, folder=None):
    """Run-flow graph of one entrypoint graph in the same shape as graphread.build (START/END not drawn)."""
    g = doc["graphs"][entry]
    gi = list(doc["graphs"]).index(entry)
    nodes = {n["id"]: (i, n) for i, n in enumerate(g["nodes"])}
    names = list(nodes)
    plain_out = {}
    for e in g["edges"]:
        if isinstance(e, list):
            plain_out.setdefault(e[0], []).append(e[1])
    raw = []   # (source, target, label, conditional, loop)
    for e in g["edges"]:
        if isinstance(e, list):
            raw.append((e[0], e[1], "", False, False))
        elif "join" in e:
            for s in e["join"]:
                raw.append((s, e["to"], "", False, False))
        else:
            raw.append((e["from"], e["to"], f"최대 {e['max_iterations']}회", False, True))
    for nid, (_, n) in nodes.items():
        p, k = n.get("params") or {}, _kind(n)
        if k == "router":
            for key, t in p["cases"].items():
                raw.append((nid, t, key, True, False))
            raw.append((nid, p["default"], "default", True, False))
        elif k == "human_review":
            for a, t in p["routes"].items():
                raw.append((nid, t, a, True, False))
        elif k == "validate" and p.get("on_fail"):
            raw.append((nid, p["on_fail"], "on_fail", True, False))
    linked = {(s, t) for s, t, *_ in raw}
    for nid, x in on_error_routes(doc, entry).items():   # EV-045 CHG-212: packrt's on_error route (a branch of its node)
        if (nid, x) not in linked:   # a pair already drawn keeps its line as it is (E3-A2: a Pack that registers today is unchanged)
            raw.append((nid, x, "on_error", True, False))
    merged = {}
    for s, t, label, cond, loop in raw:
        key = (s, t)
        if key in merged:
            m = merged[key]
            if label and label not in m["labels"]:
                m["labels"].append(label)
            m["cond"] |= cond; m["loop"] |= loop
        else:
            merged[key] = {"labels": [label] if label else [], "cond": cond, "loop": loop}
    pairs = list(merged)
    back = graphread.back_edges(names, pairs) | {k for k, m in merged.items() if m["loop"]}
    pos, h, w = graphread._layout(names, pairs, back)
    edges = []
    for (s, t), m in merged.items():
        if s not in nodes or t not in nodes:
            continue  # START/END edges are not drawn (same as legacy releases)
        kind = "loop" if (s, t) in back else ("cond" if m["cond"] else ("par" if len(plain_out.get(s, [])) > 1 else ""))
        edges.append({"id": f"{s}>{t}", "source": s, "target": t, "label": " · ".join(m["labels"]), "kind": kind, "conditional": m["cond"]})
    out_nodes = []
    for nid, (i, n) in nodes.items():
        line = lines.get(("graphs", entry, "nodes", i))
        item = {"id": nid, "name": nid, "x": pos[nid][0], "y": pos[nid][1], "loc": f"pack.yaml:{line}", "dynamic": False,
                "kind": n["kind"], "params": n.get("params") or {}, "reads": n.get("reads", []), "writes": n.get("writes", []), "expect": n.get("expect")}
        if _kind(n) == "custom_code" and folder is not None:   # function location and source hash only (CHG-08); the body is never rewritten
            item.update(_fn_info(folder, n["params"]["fn"]))
        joined = next((list(e["join"]) for e in g["edges"] if isinstance(e, dict) and "join" in e and e.get("to") == nid), None)
        if joined:   # merge 3 (manual p.15 · 17, fork B lead item 7): the sources a join gives this node (not part of the graph id)
            item["join"] = joined
        out_nodes.append(item)
    state = g["state"]
    ep = next(e for e in doc["entrypoints"] if e["graph"] == entry)
    input_key = next((k for k, t in state.items() if (t if isinstance(t, str) else t["type"]) == ep["input"]), None)
    output_key = next((k for k, t in state.items() if (t if isinstance(t, str) else t["type"]) == ep["output"]), None)
    structure = json.dumps([sorted(names), sorted((e["source"], e["target"], e["kind"]) for e in edges)])
    counts = {"node": len(out_nodes), "edge": len(edges), "조건부": sum(e["conditional"] for e in edges),
              "loop": sum(e["kind"] == "loop" for e in edges), "병렬": len({e["source"] for e in edges if e["kind"] == "par"})}
    return {"id": "g-" + graphread.sha(structure, 4), "nodes": out_nodes, "edges": edges, "entry": PACK_FILE, "width": w, "height": h,
            "input_field": input_key or "input", "output_field": output_key, "input_type": ep["input"], "output_type": ep["output"],
            "graph_name": entry, "graph_index": gi, "mismatch": [], "dynamic": [], "counts": counts,
            "start": [t for (s, t), m in merged.items() if s in (START, "START") and not m["cond"] and t in nodes]}   # EV-022 019d-1: START's plain targets


def on_error_routes(doc, entry):
    """EV-045 CHG-212: {node: target} of the graph's `on_error: {then: "route:<target>"}` — packrt makes it a branch of the node
    (run/engine.py: a deviation, so all of the node's plain edges branch too). `then: fail` (the default) is no branch."""
    out = {}
    for n in (doc.get("graphs") or {}).get(entry, {}).get("nodes") or []:
        then = (n.get("on_error") or {}).get("then", "fail") if isinstance(n, dict) else "fail"
        if isinstance(then, str) and then.startswith("route:"):
            out[n["id"]] = then[6:]
    return out


def _problems(res):
    return [{"stage": e["stage"], "line": e["line"], "message": e["message"]} for e in res.errors]


def origin_release(store, rel):
    """The release a confirmed version started from, followed back to the one registered from a work folder (EV-022)."""
    seen = set()
    while rel and (rel.get("pack") or {}).get("from") and rel["id"] not in seen:
        seen.add(rel["id"])
        up = store.release(rel["pack"]["from"].get("rid"))
        if not up:
            break
        rel = up
    return rel


def key_folder(store, rel):
    """EV-061 (137-02): the work folder whose .env holds the Pack's keys — the origin release's, since a data/edits copy leaves .env
    out (B-8). A release registered from its work folder is its own origin. The path only: the file is never opened here."""
    return Path(((origin_release(store, rel) or {}).get("pack") or {}).get("work_folder") or rel["pack"]["work_folder"])


KEY_FILE_MISSING = "키 파일 없음 — {path}"   # D-492 덧 24 (리드 295 ③ · packrt 196 (가)): the path only, never the file


def key_env_names(folder):
    """The key names (`env`) the Pack's models.yaml in `folder` declares — () without a readable file or a key name. Read like
    model_env_names; the Pack's .env is never opened."""
    try:
        from packrt.ir import strict_yaml
        models, _ = strict_yaml.load((Path(folder) / "models.yaml").read_text(encoding="utf-8"))
        providers = (models or {}).get("providers") or {}
        return tuple(sorted({c["env"] for c in providers.values() if isinstance(c, dict) and isinstance(c.get("env"), str)}))
    except Exception:  # noqa: BLE001 - a Pack without (readable) models.yaml declares no key here
        return ()


def missing_key_file(store, rel, mode):
    """D-492 덧 24: the `<key folder>/.env` path a real-mode start would hand the Pack (EV-061 key_folder — the origin release's work
    folder) when it is not there, else None. Only a real mode, only an Agent Pack release whose models.yaml declares a key name; a
    fake mode or a Pack with no key name is never stopped. Checked with is_file — the file is never opened. The three entrances (a Run ·
    the sealed check · an efficiency wheel's start) call it before anything starts; the executor itself still runs as before (EV-061)."""
    if mode == "fake" or not rel or rel.get("sourceKind") != "agent_pack":
        return None
    pack = rel.get("pack") or {}
    if not key_env_names(pack.get("copy") or pack.get("work_folder") or "."):
        return None
    path = key_folder(store, rel) / ".env"
    return None if path.is_file() else str(path)


def runtime_dsn(db_env_file):
    """The runtime account's DSN (LLMOPSAGENT_RUNTIME_DSN) — this one line only, from the DB env file (EV-020 CHG-97).
    The value is never printed or stored; None when there is none."""
    try:
        for line in Path(db_env_file).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(releases.RUNTIME_DSN_ENV + "="):
                return line.split("=", 1)[1].strip() or None
    except (OSError, TypeError):
        return None
    return None


def read_pack(folder, config_path=None, need_config=True, dsn=None):
    """Check a Pack work folder (and its execution config). Returns the release read result. Raises PackError.
    dsn: the runtime DSN for a config whose stores read PostgreSQL (EV-020 CHG-97); file configs never see it."""
    folder = Path(folder)
    check_coexist(folder)
    res = ir.load_pack(folder)
    if not res.ok:
        lines = [f"pack.yaml {e['line']}줄: {e['message']}" if e["line"] else e["message"] for e in res.errors]
        raise PackError("pack-incompatible", "호환 안 됨 — " + " / ".join(lines), _problems(res))
    cfg = None
    if config_path:
        try:
            cfg = releases.load_execution_config(config_path, res.doc, env={releases.RUNTIME_DSN_ENV: dsn} if dsn else {})
        except releases.ReleaseError as e:
            msg = str(e)
            if dsn:   # the whole value, then its password part (EV-020 CHG-97)
                from urllib.parse import urlsplit
                for s in (dsn, urlsplit(dsn).password):
                    msg = msg.replace(s, "[가림]") if s else msg
            raise PackError("pack-config", f"등록 안 됨 — {msg}")
    elif need_config:
        raise PackError("pack-config", "등록 안 됨 — Agent Pack은 실행 설정 파일이 필요합니다")
    entry = cfg["entrypoint"] if cfg else res.doc["entrypoints"][0]["graph"]
    graph = static_graph(res.doc, res.lines, entry, folder)
    pack = {"id": res.doc["pack"]["id"], "version": res.doc["pack"]["version"], "title": res.doc["pack"]["title"],
            "revision": res.revision, "raw_files": res.raw_files, "warnings": [w["message"] for w in res.warnings],
            "work_folder": str(folder.resolve()), "entrypoints": [e["graph"] for e in res.doc["entrypoints"]],
            "configs": execution_configs(folder)}
    line = git_line(folder)
    if line:
        pack["git"] = line
    if cfg:
        pack.update(entrypoint=cfg["entrypoint"], python=cfg["python"], config_path=str(Path(config_path).expanduser().resolve()),
                    config_sha256=cfg["config_sha256"], config_text=cfg["config_text"], bindings=cfg["bindings"],
                    config_paths=cfg["deny_paths"], uses_postgres=releases.uses_postgres(cfg["config"]["stores"]))
    return {"code_hash": res.revision.split(":", 1)[1], "code": res.revision.split(":", 1)[1][:4], "graph": graph,
            "sourceKind": "agent_pack", "pack": pack}


def execution_configs(folder):
    """Execution configs kept in the Pack folder (EV-003 CHG-18): regular *.yaml/*.yml files right under execution/, by name.
    Only names are read here; the chosen file goes through load_execution_config at registration. execution/ is not a
    revision file, so it is never in raw_files or the copy."""
    d = Path(folder) / "execution"
    if not d.is_dir():
        return []
    return sorted(f"execution/{p.name}" for p in d.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix in (".yaml", ".yml"))


REVISION_DIRS = ("schemas", "prompts", "templates", "custom")   # the revision folders a link may hide in (리드 210)


def links_in(folder, raw_files):
    """리드 210 (D-480): the links under the revision folders — every folder a revision file sits in and the four revision folders —
    and on the revision files' own paths, never followed; [] when there is none."""
    root = Path(folder)
    found = []
    for rel in raw_files:   # a link anywhere on a revision file's own path
        cur = root
        for part in Path(rel).parts:
            cur = cur / part
            if cur.is_symlink():
                found.append(str(cur.relative_to(root)))
                break
    tops = {Path(r).parts[0] for r in raw_files if len(Path(r).parts) > 1} | set(REVISION_DIRS)
    for top in sorted(tops):
        d = root / top
        if d.is_symlink():
            found.append(top)
            continue
        if not d.is_dir():
            continue
        for here, dirs, files in os.walk(d, followlinks=False):
            for n in dirs + files:
                p = Path(here) / n
                if p.is_symlink():
                    found.append(str(p.relative_to(root)))
    return sorted(set(found))


def make_copy(folder, data_dir, rid, pack):
    """Copy the revision files and the execution config into <data dir>/packs/<rid>/ and check the copy has the same revision. A link in
    a revision folder is refused before anything is copied (리드 210 — a link could pull a file from outside into the copy)."""
    dest = Path(data_dir) / "packs" / rid
    if dest.exists():
        raise PackError("pack-copy", f"사본 폴더가 이미 있습니다: {dest}")
    linked = links_in(folder, pack["raw_files"])
    if linked:
        raise PackError("pack-link", "등록 안 됨 — revision 폴더에 링크가 있습니다(" + " · ".join(linked[:3]) + (" 외" if len(linked) > 3 else "")
                        + ") — 링크를 실제 파일로 바꾸거나 빼고 다시 등록하세요")
    try:
        for rel in pack["raw_files"]:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(folder) / rel, target)
        dest.mkdir(parents=True, exist_ok=True)
        # the checked config, not the original file: comments never reach the copy, and later edits of the file do not either
        (dest / CONFIG_COPY).write_text(pack["config_text"], encoding="utf-8")
        from packrt.ir import strict_yaml
        copied, _ = strict_yaml.load((dest / CONFIG_COPY).read_text(encoding="utf-8"))
        rev, raw = ir.compute_revision(dest)
        if rev != pack["revision"] or raw != pack["raw_files"] or releases.config_sha256(copied) != pack["config_sha256"]:
            raise PackError("pack-copy", "사본이 검사한 내용과 다릅니다(등록 중 파일이 바뀌었을 수 있음) — 다시 등록하세요")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


GIT_LINE_MAX = 2000   # D-502: commits kept per release (c1a15-pack had 61 on 2026-11-01)


def git_line(folder):
    """D-502 (eval 104): the Pack folder's git line — {"head": its HEAD commit, "line": HEAD and the commits before it, newest first,
    at most GIT_LINE_MAX} — when pack.yaml is tracked by the git the folder sits in, read only; None otherwise (no git, an archive, a
    copy left untracked or ignored inside another repository, whose commits are not the Pack's). The sealed set's '앞 후보 계열' check
    reads it (autosealed._after_failed)."""
    try:
        tracked = subprocess.run(["git", "-C", str(folder), "ls-files", "--error-unmatch", "--", PACK_FILE], capture_output=True, text=True, timeout=30)
        if tracked.returncode != 0:
            return None
        r = subprocess.run(["git", "-C", str(folder), "rev-list", f"--max-count={GIT_LINE_MAX}", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = r.stdout.split()
    if r.returncode != 0 or not line or not all(re.fullmatch(r"[0-9a-f]{40}", c) for c in line):
        return None
    return {"head": line[0], "line": line}


def work_revision(pack):
    """The work folder's current revision (read only), or None when it cannot be read."""
    try:
        return ir.compute_revision(Path(pack["work_folder"]))[0]
    except Exception:  # noqa: BLE001 - an unreadable folder has no revision
        return None


def work_changed(pack):
    """True when the work folder's revision files differ from what was registered (or the folder is gone)."""
    folder = Path(pack["work_folder"])
    if not folder.is_dir():
        return True
    try:
        _, raw = ir.compute_revision(folder)
    except Exception:  # noqa: BLE001 - an unreadable work folder counts as changed
        return True
    return raw != pack["raw_files"]


def revisions_dir(data_dir, rid):
    """Where earlier revisions of a release copy are kept after '현재 버전 수정' (EV-022 S-14): <data dir>/revisions/<rid>."""
    d = (Path(data_dir) / "revisions" / rid).resolve()
    return d if d.parent == (Path(data_dir) / "revisions").resolve() else None


def copy_dir(data_dir, rid):
    """The only folder a delete may remove for a Pack release: <data dir>/packs/<rid>."""
    d = (Path(data_dir) / "packs" / rid).resolve()
    return d if d.parent == (Path(data_dir) / "packs").resolve() else None


# ---------------------------------------------------------------------------------------------- running in the Pack env
REPO_ROOT = Path(__file__).resolve().parents[1]


def runtime_dir(env):
    """The packrt folder a Pack process runs (EV-023 CHG-205): <repo>/.runtime (gitignored), or — test harness only (ISOLATED_ENV=1,
    D-503 덧 2) — LLMOPS_RUNTIME_DIR (verify-e2e.sh gives its own run's folder)."""
    v = env.get("LLMOPS_RUNTIME_DIR") if env.get("ISOLATED_ENV") == "1" else None
    return Path(v) if v else REPO_ROOT / ".runtime"


RUNTIME_DIR = runtime_dir(os.environ)
PROBE_TIMEOUT_S = 90


def env_folder(python):
    """The Pack Python environment folder: the nearest parent holding pyvenv.cfg, else the parent of the executable's folder."""
    p = Path(python)
    for d in p.parents:
        if (d / "pyvenv.cfg").is_file():
            return d
    return p.parent.parent


def installed_packrt():
    """The packrt package folder this server has installed (the pinned agent-pack-runtime tag, EV-023 CHG-204)."""
    return Path(packrt.__file__).resolve().parent


def installed_tracewriter():
    """The tracewriter package folder this server has installed (same tag as packrt); a Pack process must not write it."""
    return Path(tracewriter.__file__).resolve().parent


def package_digest(folder):
    """sha256 over every file of a package folder (relative path + content sha256), __pycache__ and .pyc left out."""
    folder = Path(folder)
    lines = []
    for f in sorted(folder.rglob("*")):
        rel = f.relative_to(folder)
        if f.is_file() and "__pycache__" not in rel.parts and f.suffix != ".pyc":
            lines.append(f"{rel.as_posix()} {hashlib.sha256(f.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _runtime_ok(root, digest):
    """The folder holds packrt and nothing else, with the installed content."""
    return (root / "packrt").is_dir() and sorted(p.name for p in root.iterdir()) == ["packrt"] and package_digest(root / "packrt") == digest


def packrt_root():
    """The folder put on a Pack process's PYTHONPATH (EV-023 CHG-205): it holds only packrt/, a copy of the installed packrt
    made once under <repo>/.runtime/packrt-<version>-<digest 12>/ (the installed site-packages would also bring this
    server's other packages in front of the Pack's own). A copy whose content differs is made again."""
    src = installed_packrt()
    digest = package_digest(src)
    root = RUNTIME_DIR / f"packrt-{packrt.__version__}-{digest[:12]}"
    if _runtime_ok(root, digest):
        return root
    RUNTIME_DIR.mkdir(exist_ok=True)
    tag = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp = RUNTIME_DIR / f".tmp-{tag}"
    try:
        shutil.copytree(src, tmp / "packrt", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if root.exists() and not _runtime_ok(root, digest):   # a wrong copy: moved aside first, so a right one made meanwhile is never removed
        old = RUNTIME_DIR / f".old-{tag}"
        try:
            os.replace(root, old)
        except OSError:
            pass
        shutil.rmtree(old, ignore_errors=True)
    try:
        os.replace(tmp, root)
    except OSError:   # made at the same time by another process: keep that one if it is right
        shutil.rmtree(tmp, ignore_errors=True)
        if not _runtime_ok(root, digest):
            raise
    return root


def write_deny(data_dir, pack):
    """Everything a Pack process must not write (CHG-09): data dir (copies included), work folder, Pack env folder,
    the config's release_roots and every release folder its numbers name (EV-004 CHG-22), bound table files."""
    out = [Path(data_dir), Path(pack["work_folder"]), env_folder(pack["python"]), RUNTIME_DIR, installed_packrt(), installed_tracewriter(), REPO_ROOT / "llmops"]   # nor the runtime/product it runs with
    out += [Path(p) for p in pack.get("config_paths", [])]
    for b in (pack.get("bindings") or {}).get("stores", {}).values():
        if b.get("path"):
            out.append(Path(b["path"]))
    for b in (pack.get("bindings") or {}).get("models", {}).values():
        out.append(Path(b["path"]))
    return sorted(set(out))


ORT_TELEMETRY_ENV = "ORT_DISABLE_TELEMETRY"


# EV-053 CHG-229: names a Pack's models.yaml may give as a key or address that are never taken out (the process needs them)
KEEP_NAMES = {"PATH", "HOME", "TMPDIR", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONHOME", "LANG", "ORT_DISABLE_TELEMETRY",
              "PACKRT_ENV_FILE", "LLMOPSAGENT_RUNTIME_DSN"}
KEEP_PREFIXES = ("LC_", "NETGUARD_", "ISOLATED_")


def model_env_names(folder):
    """EV-053 CHG-229: the key (`env`) and address (`base_url_env`) names of the Pack's models.yaml in `folder`, without the kept
    names — read like build_plan reads them; the Pack's .env is never opened. No file, or one that cannot be read: () (then only
    the key-shaped names go; packrt says what is missing in its own error)."""
    try:
        from packrt.ir import strict_yaml
        models, _ = strict_yaml.load((Path(folder) / "models.yaml").read_text(encoding="utf-8"))
        providers = (models or {}).get("providers") or {}
        names = {v for c in providers.values() if isinstance(c, dict) for v in (c.get("env"), c.get("base_url_env")) if isinstance(v, str)}
    except Exception:  # noqa: BLE001 - a Pack without (readable) models.yaml runs as before
        return ()
    return tuple(sorted(n for n in names if n not in KEEP_NAMES and not n.startswith(KEEP_PREFIXES)))


def pack_env(base_env, extra=None, dsn=None, strip=()):
    """Environment of a Pack process: the Agent env (no LLMOps key) plus packrt on PYTHONPATH (kept after the netguard path).
    Only a folder holding the packrt package is added (EV-019 CHG-89, EV-023 CHG-205), so the Pack process does not see the
    llmops product code or this server's other packages. onnxruntime telemetry is off in every Pack process (EV-048 CHG-217, D-315:
    packrt sets it before its own onnxruntime import too); set last, so no extra value turns it back on.
    EV-053 CHG-229 (D-351 · D-353): first every key-shaped variable (KEY_RE) and the `strip` names (the Pack's models.yaml key and
    address names, model_env_names) go — the server's provider keys never reach a Pack process; a real-mode Pack gets its keys and
    addresses from its .env only (PACKRT_ENV_FILE, B-8). Then the paths, extra (the fake provider's addresses), the DSN."""
    env, _ = strip_keys(dict(base_env), [n for n in strip if n not in KEEP_NAMES and not n.startswith(KEEP_PREFIXES)])
    env.pop("PACKRT_ENV_FILE", None)   # only the executor sets it, and only in real mode (B-8)
    paths = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join([*paths, str(packrt_root())])
    env.update(extra or {})
    if dsn:   # a config whose stores read PostgreSQL: the runtime account's DSN, nothing else (the spawn point must allow it too)
        env[releases.RUNTIME_DSN_ENV] = dsn
    env[ORT_TELEMETRY_ENV] = "1"
    return env


def probe(data_dir, pack, copy, secrets=()):
    """Build the graph in the Pack environment without running it (CHG-07). Returns the projection or raises PackError."""
    from . import guard
    try:
        r = guard.sandboxed_subprocess([pack["python"], "-m", "packrt.probe", str(copy)], cwd=str(copy), write_deny=write_deny(data_dir, pack),
                                       timeout=PROBE_TIMEOUT_S, env=pack_env(guard.agent_env(), strip=model_env_names(copy)))
    except Exception as e:  # noqa: BLE001 - timeout or a Python that cannot start
        raise PackError("pack-probe", f"등록 안 됨 — Pack 환경에서 그래프를 만들 수 없음 — {type(e).__name__}: {str(e)[:200]}")
    try:   # masked before parsing, like runner lines (the Pack Python is untrusted)
        out = json.loads(guard.redact(r.stdout.strip().splitlines()[-1], secrets))
    except (IndexError, json.JSONDecodeError):
        tail = (r.stderr or "출력 없음").strip().splitlines()[-1:] or ["출력 없음"]
        raise PackError("pack-probe", f"등록 안 됨 — Pack 환경에서 그래프를 만들 수 없음 — {guard.redact(tail[0])[:300]}")
    if isinstance(out, dict) and "error" in out:
        raise PackError("pack-probe", f"등록 안 됨 — Pack 환경에서 그래프를 만들 수 없음 — {guard.redact(str(out.get('message', '')))[:300]}")
    problem = _projection_shape(out)
    if problem:   # the Pack Python is user-chosen: its output is untrusted input
        raise PackError("pack-probe", f"등록 안 됨 — Pack 환경의 그래프 보고 형식이 틀립니다 — {guard.redact(problem)[:300]}")
    return out


def _projection_shape(out):
    if not isinstance(out, dict):
        return "JSON 객체가 아닙니다"
    need = {"runtime": dict, "pack_revision": str, "entrypoint": str, "nodes": list, "edges": list, "routers": dict, "reads_writes": dict}
    for k, typ in need.items():
        if not isinstance(out.get(k), typ):
            return f"'{k}' 항목이 없거나 형식이 다릅니다"
    if not all(isinstance(n, str) for n in out["nodes"]):
        return "'nodes' 항목이 글 목록이 아닙니다"
    for e in out["edges"]:
        if not (isinstance(e, dict) and isinstance(e.get("source"), str) and isinstance(e.get("target"), str) and isinstance(e.get("conditional"), bool)):
            return "'edges' 항목의 형식이 다릅니다"
    if not all(isinstance(v, list) for v in out["routers"].values()) or not all(isinstance(v, dict) and isinstance(v.get("reads"), list) and isinstance(v.get("writes"), list) for v in out["reads_writes"].values()):
        return "'routers'·'reads_writes' 항목의 형식이 다릅니다"
    return None


def compare_projection(graph, pack, proj, routes):
    """CHG-08: declared node ids, entrypoint, edge pairs, router targets, reads/writes — not the whole compiled graph.
    routes = on_error_routes of the registered copy (EV-045 CHG-212; read at registration, never stored)."""
    diff = []
    names = {n["name"] for n in graph["nodes"]}
    if set(proj["nodes"]) != names:
        diff.append(f"node 차이: pack.yaml에만 {sorted(names - set(proj['nodes']))}, 실행 그래프에만 {sorted(set(proj['nodes']) - names)}")
    if proj.get("entrypoint") != pack.get("entrypoint"):
        diff.append(f"entrypoint 차이: {pack.get('entrypoint')} / {proj.get('entrypoint')}")
    if proj.get("pack_revision") != pack["revision"]:
        diff.append("Pack 환경이 읽은 revision이 등록한 revision과 다릅니다")
    # a node that branches (an on_error route too) routes all its edges conditionally — but a join edge is added apart and never
    # branches (packrt run/engine.py; EV-045 CHG-212)
    cond_src = {e["source"] for e in graph["edges"] if e["conditional"]} | set(routes)
    joins = {(s, n["name"]) for n in graph["nodes"] for s in n.get("join") or []}
    static = {(e["source"], e["target"], e["source"] in cond_src and (e["source"], e["target"]) not in joins) for e in graph["edges"]}
    built = {(e["source"], e["target"], bool(e["conditional"])) for e in proj["edges"] if e["source"] in names and e["target"] in names}
    if static != built:
        diff.append(f"연결 차이(출발, 도착, 조건부): pack.yaml에만 {sorted(static - built)}, 실행 그래프에만 {sorted(built - static)}")
    routers = {n["name"]: sorted(set(n["params"]["cases"].values()) | {n["params"]["default"]}) for n in graph["nodes"] if n["kind"].startswith("router")}
    if {k: sorted(v) for k, v in (proj.get("routers") or {}).items()} != routers:
        diff.append(f"router 대상 차이: pack.yaml {routers} / 실행 그래프 {proj.get('routers')}")
    for n in graph["nodes"]:
        rw = (proj.get("reads_writes") or {}).get(n["name"])
        if not rw or rw["reads"] != n["reads"] or rw["writes"] != n["writes"]:
            diff.append(f"node '{n['name']}' reads·writes 차이(또는 보고 없음)")
    return diff


# ---------------------------------------------------------------- EV-051 W2-2a: the wheel's vector DB versions (packrt 0.12.0 index build)
# A candidate row that changes a retrieve@2 node's index options needs the vector DB version of the new recipe. The Pack's own
# packrt tells what is missing (probe index_missing, contract C-9); the version is looked for under the candidate config's
# release_roots and, only when none has the build hash, made by `packrt.index build` in the Pack sandbox (C-10). The build writes
# outside the data folder; the server copies the regular files into new files of its own, checks them and puts them in
# <data dir>/index-builds/<release_id> with one rename (CHG-W2-05 ~ 08, D-292 · D-293).
INDEX_BUILDS, INDEX_OUT, INDEX_STAGING = "index-builds", ".llmops-index-out", "staging"
BUILD_TIMEOUT_S = 300
BUILD_MAX_BYTES, BUILD_MAX_FILES = 200 * 1024 * 1024, 10_000
RELEASE_ID = __import__("re").compile(r"[a-z0-9][a-z0-9._-]{0,127}")   # a build's release id: one plain folder name


class IndexStepError(Exception):
    """Why a row's vector DB version could not be had — the row's discard reason."""


def index_missing_of(out):
    """[{store, node, index_recipe_sha256, build_sha256}] from a packrt probe answer: the projection's list, or the build error
    that carries it (0.12.0 prints IndexMissing on the error shape). Any other error raises IndexStepError."""
    if not isinstance(out, dict):
        raise IndexStepError("probe 답이 JSON 객체가 아님")
    missing = out.get("index_missing")
    if missing is None:
        if "error" in out:
            raise IndexStepError(f"probe {str(out.get('message', ''))[:160]}")
        return []
    if not isinstance(missing, list) or not all(isinstance(m, dict) and isinstance(m.get("store"), str) and isinstance(m.get("node"), str) for m in missing):
        raise IndexStepError("probe index_missing 형식이 다름")
    return missing


def index_unchecked_of(out):
    """EV-051 CHG-W2-16 (D-298 · D-308): [{store, node, …}] the packrt 0.12.1 probe could not choose for — pgvector retrieve@2 stores
    (the normal shape always has the list, the error shape only when it is not empty). None or no list = []."""
    unchecked = out.get("index_unchecked") if isinstance(out, dict) else None
    if unchecked is None:
        return []
    if not isinstance(unchecked, list) or not all(isinstance(u, dict) and isinstance(u.get("store"), str) and isinstance(u.get("node"), str) for u in unchecked):
        raise IndexStepError("probe index_unchecked 형식이 다름")
    return unchecked


def probe_answer(data_dir, pack, copy, secrets=()):
    """The packrt probe of a copy as it answered (both shapes), for the wheel's index step only — registration keeps probe()."""
    from . import guard
    try:
        r = guard.sandboxed_subprocess([pack["python"], "-m", "packrt.probe", str(copy)], cwd=str(copy), write_deny=write_deny(data_dir, pack),
                                       timeout=PROBE_TIMEOUT_S, env=pack_env(guard.agent_env(), strip=model_env_names(copy)))
        return json.loads(guard.redact(r.stdout.strip().splitlines()[-1], secrets))
    except (IndexError, json.JSONDecodeError):
        raise IndexStepError("probe 출력이 없음") from None
    except Exception as e:  # noqa: BLE001 - timeout or a Python that cannot start
        raise IndexStepError(f"probe {type(e).__name__}") from None


def _sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dir_hash(folder):
    """packrt 0.12.x content hash of a release folder (D-293): every file but the top manifest.json, none under __pycache__ (judged on
    the absolute path, as packrt does), in Path order, as 'relative path<TAB>file sha256' lines."""
    folder = Path(folder)
    lines = [f"{p.relative_to(folder).as_posix()}\t{_sha_file(p)}" for p in sorted(folder.rglob("*"))
             if p.is_file() and p.relative_to(folder).as_posix() != "manifest.json" and "__pycache__" not in p.parts]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _inside(name):
    s = str(name)
    return bool(s) and not s.startswith(("/", "~")) and ".." not in Path(s).parts and "\\" not in s


def _files_ok(folder, data):
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        return False
    return all(_inside(n) and (Path(folder) / n).is_file() and _sha_file(Path(folder) / n) == d for n, d in sorted(files.items()))


def release_folders(roots):
    """release_id → {real folder: folder} for every readable manifest.json one or two levels under the roots — the packrt 0.12.x
    rule (D-293: the same real folder counts once; packrt's own name is not called)."""
    found = {}
    for root in roots:
        for m in sorted(list(Path(root).glob("*/manifest.json")) + list(Path(root).glob("*/*/manifest.json"))):
            try:
                data = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rid = data.get("release_id") if isinstance(data, dict) else None
            if isinstance(rid, str) and rid:
                found.setdefault(rid, {}).setdefault(os.path.realpath(m.parent), m.parent)
    return found


def _one(found, rid, what):
    places = found.get(rid) or {}
    if not places:
        raise IndexStepError(f"{what} 찾을 수 없음 {rid}")
    if len(places) > 1:
        raise IndexStepError(f"{what} 번호 겹침 {rid}")
    return Path(next(iter(places.values())))


def find_corpus(roots, ref):
    """The corpus release folder of a base index's corpus_release {id, sha256} (D-293 · D-298): packrt 0.12.1's public
    packrt.index.find_corpus — found under the roots as packrt finds releases, read as a corpus (kind · format · files · content hash ·
    documents) and its sha256 == the base's. A missing or doubled id keeps its reason; any other refusal names packrt's."""
    from packrt.index import IndexBuildError, find_corpus as packrt_find_corpus
    if not isinstance(ref, dict) or not ref.get("id") or not ref.get("sha256"):
        raise IndexStepError("기준 version에 corpus 없음")
    try:
        return Path(packrt_find_corpus([str(r) for r in roots], {"id": ref["id"], "sha256": ref["sha256"]}))
    except IndexBuildError as e:
        why = {"corpus_missing": "찾을 수 없음", "corpus_ambiguous": "번호 겹침"}.get(e.code)
        raise IndexStepError(f"corpus {why} {ref['id']}" if why else f"corpus 검사 실패 {ref['id']} — {e}") from None
    except OSError:
        raise IndexStepError(f"corpus 검사 실패 {ref['id']} — 읽을 수 없음") from None


def find_index_version(roots, build_sha256):
    """(release_id, folder) of an index release whose manifest build_sha256 (all 64 characters) is this one, under any root; None."""
    for rid, places in release_folders(roots).items():
        for folder in places.values():
            try:
                data = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("kind") == "index" and data.get("build_sha256") == build_sha256:
                return rid, Path(folder)
    return None


def config_of(folder):
    from packrt.ir import strict_yaml
    cfg, _ = strict_yaml.load((Path(folder) / CONFIG_COPY).read_text(encoding="utf-8"))
    return cfg


def section_spans(corpus_folder):
    """D-492 덧 15 (contract F 33): {doc: {section id: (start, end)}} of a corpus release's documents.jsonl — read only."""
    out = {}
    for line in (Path(corpus_folder) / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            for sec in d.get("sections") or []:
                out.setdefault(d["doc"], {})[str(sec["id"])] = (sec["char_start"], sec["char_end"])
    return out


def run_sections(manifest, roots):
    """D-492 덧 15: the section spans of every corpus a Run's vector DB versions were built from (its execution manifest), found under
    the Pack copy's release roots as the rebuild finds them; None when there is none or one cannot be found or read (the Run's
    fixed-size pieces are then not counted, never a 0)."""
    out = {}
    for v in ((manifest or {}).get("evidence_index_releases") or {}).values():
        ref = (v or {}).get("corpus_release")
        if not ref:
            continue
        try:
            for doc, secs in section_spans(find_corpus(roots, ref)).items():
                out.setdefault(doc, {}).update(secs)
        except (IndexStepError, OSError, ValueError, KeyError, TypeError):
            return None
    return out or None


def base_corpus(folder, store):
    """The corpus_release of the store's base `release` in a copy's execution config (the base fixes the corpus, contract C-1)."""
    cfg = config_of(folder)
    rid = ((cfg.get("stores") or {}).get(store) or {}).get("release")
    base = _one(release_folders(cfg.get("release_roots") or []), rid, "기준 version")
    try:
        return json.loads((base / "manifest.json").read_text(encoding="utf-8")).get("corpus_release")
    except (OSError, ValueError):
        raise IndexStepError(f"기준 version manifest를 읽을 수 없음 {rid}") from None


def add_index_release(folder, store, rid, root=None):
    """Write the version into a copy's execution config (CHG-W2-08): the store's `releases` gets it, and `release_roots` the
    folder it lives in when that is not there yet. The Pack revision does not change (the config is outside it)."""
    import yaml
    cfg = config_of(folder)
    st = cfg["stores"][store]
    st["releases"] = [*(st.get("releases") or []), *([] if rid in (st.get("releases") or []) else [rid])]
    if root is not None and str(root) not in [str(r) for r in cfg.get("release_roots") or []]:
        cfg["release_roots"] = [*(cfg.get("release_roots") or []), str(root)]
    text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    from packrt.ir import strict_yaml
    if strict_yaml.load(text)[0] != cfg:   # a value the strict reader takes otherwise (slice critic N-8): refuse, never write it
        raise IndexStepError("실행 설정을 다시 쓸 수 없음")
    (Path(folder) / CONFIG_COPY).write_text(text, encoding="utf-8")


def _out_folder(data_dir):
    """A new folder outside the data folder for one build: under <data dir parent>/.llmops-index-out (0711 — not listable), with a
    random name; on Linux the evaluation account must write it (0777), on macOS the server account runs the sandbox (0700)."""
    from . import guard
    import stat
    parent = Path(data_dir).resolve().parent / INDEX_OUT
    try:
        parent.mkdir(exist_ok=True)
        st = os.lstat(parent)
    except OSError:
        raise IndexStepError("build 자리를 만들 수 없음") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():   # never a link or a folder of another account (N-A3-5)
        raise IndexStepError("build 자리가 서버 폴더가 아님")
    out = parent / uuid.uuid4().hex
    try:   # the descriptors are taken at once and the modes set through them (slice critic NB-9: no swap between mkdir and open)
        pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(pfd, 0o711)
            os.mkdir(out.name, 0o700, dir_fd=pfd)
            fd = os.open(out.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        finally:
            os.close(pfd)
        os.fchmod(fd, 0o777 if guard._platform().startswith("linux") else 0o700)
    except OSError:
        raise IndexStepError("build 자리를 만들 수 없음") from None
    return out, fd


def _drop_out(out, pack, data_dir):
    """Linux: the files are the evaluation account's, so that account removes them (under the same sandbox and write-deny list as the
    build); then the server."""
    from . import guard
    if guard._platform().startswith("linux"):
        try:
            guard.sandboxed_subprocess([pack["python"], "-c", "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)", str(out)],
                                       cwd=str(out.parent), write_deny=write_deny(data_dir, pack), timeout=60,
                                       env=pack_env(guard.agent_env()))   # EV-053 CHG-229: a Pack Python too — no key-shaped names (no copy here: KEY_RE only)
        except Exception:  # noqa: BLE001 - what is left is outside the data folder
            pass
    shutil.rmtree(out, ignore_errors=True)


WRONG_PLACE = "벡터 DB 자리가 서버 폴더가 아님"   # EV-051 W2-5b CHG-W2-31


def _copy_in(top, dest):
    """Copy the regular files of src into new server files under dest (0700 folders · 0600 files until checked). Every name is opened
    relative to its folder's descriptor without following links (a folder swapped for a link is never entered — slice critic W2-2a
    B-1); links, devices, FIFOs (opened non-blocking) and files with more than one link (a hard link to something else) are refused,
    and so are more than BUILD_MAX_FILES files or BUILD_MAX_BYTES bytes (counted on what is read, so a file that grows meanwhile is cut
    off too). top: a descriptor of the release folder, opened by the caller without following links. dest: a descriptor of the new
    stage folder — its folders and files are made relative to descriptors too (EV-051 W2-5b CHG-W2-31 ③)."""
    import stat
    total, count = 0, 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    made = {".": dest}
    try:
        for here, dirs, files, dfd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
            out = made[here]
            for d in dirs:
                if stat.S_ISLNK(os.stat(d, dir_fd=dfd, follow_symlinks=False).st_mode):
                    raise IndexStepError("build 결과에 링크가 있음")
                os.mkdir(d, 0o700, dir_fd=out)
                made[os.path.join(here, d)] = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=out)
            for f in files:
                fd = os.open(f, flags, dir_fd=dfd)
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise IndexStepError("build 결과에 일반 파일이 아닌 것이 있음")
                    count += 1
                    if count > BUILD_MAX_FILES:
                        raise IndexStepError("build 결과가 너무 큼")
                    target = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=out)
                    with os.fdopen(os.dup(fd), "rb") as r, open(target, "wb") as w:
                        while chunk := r.read(1 << 20):
                            total += len(chunk)
                            if total > BUILD_MAX_BYTES:
                                raise IndexStepError("build 결과가 너무 큼")
                            w.write(chunk)
                finally:
                    os.close(fd)
    except OSError:
        raise IndexStepError("build 결과를 읽을 수 없음") from None   # not the OS error's text, which names paths (N-9)
    finally:
        for k, fd in made.items():
            if k != ".":
                os.close(fd)


def _read_at(top, name, limit=None):
    """The bytes of a regular file under a folder descriptor, the last name opened without following a link."""
    import stat
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=top)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError("일반 파일 아님")
        return f.read(limit) if limit else f.read()


def dir_hash_at(top, folder):
    """dir_hash of the folder open as `top` (CHG-W2-31 ④): the same lines in the same order — regular files only, sorted as the
    Path objects folder/<relative path>, the top manifest.json left out, none under __pycache__ judged on that absolute path."""
    import stat
    folder = Path(folder)
    found = []
    for here, dirs, files, dfd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
        for f in files:
            if stat.S_ISREG(os.stat(f, dir_fd=dfd, follow_symlinks=False).st_mode):
                found.append(folder / os.path.normpath(os.path.join(here, f)))
    lines = [f"{p.relative_to(folder).as_posix()}\t{hashlib.sha256(_read_at(top, p.relative_to(folder).as_posix())).hexdigest()}" for p in sorted(found)
             if p.relative_to(folder).as_posix() != "manifest.json" and "__pycache__" not in p.parts]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _open_up(top):
    """After the check: the version is readable like any release (folders 0755, files 0644 — the evaluation account reads it), set
    through descriptors (CHG-W2-31 ⑤)."""
    for _, dirs, files, dfd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
        os.fchmod(dfd, 0o755)
        for f in files:
            fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
            try:
                os.fchmod(fd, 0o644)
            finally:
                os.close(fd)


def _server_dir(dfd, name, create):
    """A descriptor of <data dir>/<name> (dfd: the data folder): a plain folder of this account, made when missing (CHG-W2-31 ①).
    A link or another account's folder → IndexStepError."""
    import stat
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=dfd)
        except FileExistsError:
            pass
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
    except OSError:
        raise IndexStepError(WRONG_PLACE) from None
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise IndexStepError(WRONG_PLACE)
    return fd


def _rmtree_at(pfd, name):
    """Remove pfd/name through descriptors, files then folders; a link anywhere is unlinked, never followed (its target stays)."""
    import stat
    st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=pfd)
        return
    top = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    try:
        for _, dirs, files, dfd in os.fwalk(".", dir_fd=top, topdown=False, follow_symlinks=False):
            for f in files:
                os.unlink(f, dir_fd=dfd)
            for d in dirs:
                if stat.S_ISLNK(os.stat(d, dir_fd=dfd, follow_symlinks=False).st_mode):
                    os.unlink(d, dir_fd=dfd)
                else:
                    os.rmdir(d, dir_fd=dfd)
    finally:
        os.close(top)
    os.rmdir(name, dir_fd=pfd)


def clear_staging(data_dir):
    """Left-over index staging folders of an earlier pass (a stopped server, N-A3-7) — only inside the server's own staging folder
    (a link there is left alone, CHG-W2-31)."""
    try:
        dfd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        sfd = _server_dir(dfd, INDEX_STAGING, False)
    except IndexStepError:
        return
    finally:
        os.close(dfd)
    try:
        for name in os.listdir(sfd):
            if name.startswith("index-"):
                try:
                    _rmtree_at(sfd, name)
                except OSError:
                    pass
    finally:
        os.close(sfd)


def _check_built(top, folder, rid, build_sha256, corpus):
    """The copied release open as `top` (its path `folder` only names it for dir_hash's __pycache__ rule) against the build's answer."""
    try:
        data = json.loads(_read_at(top, "manifest.json"))
    except (OSError, ValueError):
        raise IndexStepError("build 결과 manifest를 읽을 수 없음") from None
    files = data.get("files") if isinstance(data, dict) else None
    try:
        files_ok = isinstance(files, dict) and bool(files) and all(_inside(n) and hashlib.sha256(_read_at(top, n)).hexdigest() == d for n, d in sorted(files.items()))
        same = data.get("index_sha256") == dir_hash_at(top, folder)
    except OSError:
        files_ok = same = False
    if (data.get("kind") != "index" or data.get("release_id") != rid or data.get("build_sha256") != build_sha256 or data.get("corpus_release") != corpus
            or not files_ok or not same):
        raise IndexStepError("build 결과 대조 다름")


def place_built(data_dir, src, rid, build_sha256, corpus, out_fd=None):
    """Copy · check · rename one built release into <data dir>/index-builds/<rid>. 'built' or 'exists' (a folder there already with
    the same build hash, also when made meanwhile); another version under the same name is refused. With out_fd (the OUT folder's
    descriptor taken when it was made) the release is opened inside that very folder, never through its path (slice critic B-4: on
    macOS a Pack runs as the server account and could swap OUT for a link). EV-051 W2-5b CHG-W2-31: every step goes through
    descriptors of the server's own index-builds and staging folders, and what was moved must be what was checked."""
    import stat
    name = f"index-{uuid.uuid4().hex}"
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    dfd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    bfd = sfd = stfd = None
    try:
        bfd = _server_dir(dfd, INDEX_BUILDS, True)
        os.fchmod(bfd, 0o755)
        sfd = _server_dir(dfd, INDEX_STAGING, True)
        os.mkdir(name, 0o700, dir_fd=sfd)
        stfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
        try:
            top = os.open(rid if out_fd is not None else src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, **({"dir_fd": out_fd} if out_fd is not None else {}))
        except OSError:
            raise IndexStepError("build 결과 폴더가 없음") from None
        try:
            _copy_in(top, stfd)
        finally:
            os.close(top)
        _check_built(stfd, Path(data_dir) / INDEX_STAGING / name, rid, build_sha256, corpus)
        _open_up(stfd)
        try:
            os.replace(name, rid, src_dir_fd=sfd, dst_dir_fd=bfd)   # a non-empty folder or a link there already makes this fail
        except OSError:
            pass
        else:
            moved, mine = os.stat(rid, dir_fd=bfd, follow_symlinks=False), os.fstat(stfd)
            if stat.S_ISDIR(moved.st_mode) and (moved.st_dev, moved.st_ino) == (mine.st_dev, mine.st_ino):
                return "built"
            if not stat.S_ISDIR(moved.st_mode):   # the stage name was swapped for a link meanwhile: take only the link back out
                os.unlink(rid, dir_fd=bfd)
                raise IndexStepError(WRONG_PLACE)
            _, _, inner = _scan_version(bfd, rid)
            if inner:
                raise IndexStepError(f"{WRONG_PLACE}({rid} — 남김: {inner})")
            _rmtree_at(bfd, rid)   # another folder moved in under the stage's name: taken back out
            raise IndexStepError(WRONG_PLACE)
        try:
            st = os.stat(rid, dir_fd=bfd, follow_symlinks=False)
        except FileNotFoundError:
            raise IndexStepError(f"벡터 DB version 번호 겹침 {rid}") from None
        if not stat.S_ISDIR(st.st_mode):
            raise IndexStepError(f"{WRONG_PLACE}({rid}가 링크 또는 폴더 아님)")
        try:
            rfd = os.open(rid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=bfd)
            try:
                same = json.loads(_read_at(rfd, "manifest.json")).get("build_sha256") == build_sha256
            finally:
                os.close(rfd)
        except (OSError, ValueError, AttributeError):
            same = False
        if not same:
            raise IndexStepError(f"벡터 DB version 번호 겹침 {rid}")
        return "exists"
    finally:
        if stfd is not None:
            os.close(stfd)
        if sfd is not None:
            try:
                _rmtree_at(sfd, name)
            except OSError:
                pass
            os.close(sfd)
        if bfd is not None:
            os.close(bfd)
        os.close(dfd)


# ---------------------------------------------------------------- EV-051 W2-5 (CHG-W2-26 ~ 29, D-357): removing unused versions
# Nothing else removes a folder under <data dir>/index-builds. A version is in use while an execution config the server runs from
# names it (any store, any backend) or a wheel row may run it again; everything else may go — the fake ones after a wheel ends,
# the real ones only by the admin command with --include-real (D-357 (2)).
SWEPT = "index_swept"   # the mark an ended wheel's state gets once its versions were swept


def config_release_ids(folder):
    """Every store's `releases` · `release` value in a copy's execution config, whatever the backend. A config that cannot be read,
    or a value there of another shape (stores not a mapping · releases not a list of names · release not a name), raises ValueError;
    a store without either value (a `type: files` store) adds nothing."""
    try:
        cfg = config_of(folder)
    except Exception:  # noqa: BLE001 - missing, unreadable or not strict YAML: the caller removes nothing
        raise ValueError("실행 설정을 읽을 수 없음") from None
    stores = (cfg or {}).get("stores") if isinstance(cfg, dict) else None
    if stores is None:
        return set()
    if not isinstance(stores, dict) or not all(isinstance(st, dict) for st in stores.values()):
        raise ValueError("실행 설정 stores 꼴이 다름")
    out = set()
    for st in stores.values():
        many, one = st.get("releases"), st.get("release")
        if many is not None:
            if not isinstance(many, list) or not all(isinstance(x, str) for x in many):
                raise ValueError("실행 설정 releases 꼴이 다름")
            out.update(many)
        if one is not None:
            if not isinstance(one, str):
                raise ValueError("실행 설정 release 꼴이 다름")
            out.add(one)
    return out


def _row_uids(row):
    ct, tr = row.get("cheap_test") or {}, row.get("table_result") or {}
    return [u for u in [*(ct.get("runs") or []), *(tr.get("runs") or []), *(tr.get("pending") or [])] if isinstance(u, str)]


def _row_builds(row):
    return [b["release_id"] for b in ((row.get("check_result") or {}).get("index_builds") or [])
            if isinstance(b, dict) and isinstance(b.get("release_id"), str)]


def index_builds_in_use(data_dir, store, unswept=(), row_alive=None):
    """CHG-W2-26: ({version id: [who uses it]}, None), or ({}, why) when it cannot be told (then nothing is removed). In use:
    the execution config of every Agent Pack release copy and of its kept revisions, the open edit's folder when it is there,
    the rows of every open wheel, and the rows of the ended wheels in `unswept` (not marked yet) whose Run is still going —
    row_alive(uid); without it (the admin command cannot see the server's memory) every row of those wheels."""
    used = {}

    def add(ids, who):
        for i in ids:
            used.setdefault(i, []).append(who)

    try:
        for p in store.projects():
            for rel in store.releases(p["id"]):
                if rel.get("sourceKind") != "agent_pack":
                    continue
                copy = (rel.get("pack") or {}).get("copy")
                if not copy:
                    raise ValueError("사본 자리 없음")
                try:
                    add(config_release_ids(copy), rel["name"])
                    kept = revisions_dir(data_dir, rel["id"])
                    for d in sorted(kept.iterdir()) if kept and kept.is_dir() else []:
                        if d.is_dir():
                            add(config_release_ids(d), rel["name"])
                except ValueError as e:
                    raise ValueError(f"{p['name']} {rel['name']} {e}") from None
            e = store.open_edit(p["id"])
            folder = Path(data_dir) / "edits" / str(e["edit_id"]) / "pack" if e else None
            if folder and (folder / CONFIG_COPY).is_file():   # an open edit has a folder only while it is being confirmed
                try:
                    add(config_release_ids(folder), "편집")
                except ValueError as err:
                    raise ValueError(f"{p['name']} 편집 {err}") from None
        for w in store.open_wheels():
            for r in store.candidates(w["wheel_id"]):
                add(_row_builds(r), f"바퀴 A-{w['seq']}")
        for wid in unswept:
            w = store.wheel(wid)
            for r in store.candidates(wid) if w else []:
                if row_alive is None or any(row_alive(u) for u in _row_uids(r)):
                    add(_row_builds(r), f"바퀴 A-{w['seq']}")
    except ValueError as e:
        return {}, str(e)
    except OSError as e:   # a folder that cannot be listed (permissions): blocked too, never a removal (slice critic N-W25-6)
        return {}, f"폴더를 읽을 수 없음({type(e).__name__})"
    return used, None


def _builds_fd(data_dir):
    """A descriptor of <data dir>/index-builds, or None when there is none. Not a plain folder of this account directly inside the
    data folder (a link, another owner) → ValueError (CHG-W2-29 ①)."""
    import stat
    path = Path(data_dir) / INDEX_BUILDS
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("index-builds가 서버 폴더가 아님") from None
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or Path(os.path.realpath(path)).parent != Path(os.path.realpath(data_dir)):
        os.close(fd)
        raise ValueError("index-builds가 서버 폴더가 아님")
    return fd


def _scan_version(bfd, name):
    """(bytes, mode, None) of one version folder, or (0, None, why) when it is not a plain tree of folders and regular files
    (a link anywhere → the whole folder stays, CHG-W2-29 ③). mode = the manifest's embedding.mode, None when unknown."""
    import stat
    if not RELEASE_ID.fullmatch(name):
        return 0, None, "이름이 version id 꼴이 아님"
    try:
        if not stat.S_ISDIR(os.stat(name, dir_fd=bfd, follow_symlinks=False).st_mode):
            return 0, None, "링크 또는 폴더가 아님"
        top = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=bfd)
    except OSError:
        return 0, None, "열 수 없음"
    try:
        total = 0
        for _, dirs, files, dfd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
            for n in dirs + files:
                st = os.stat(n, dir_fd=dfd, follow_symlinks=False)
                if n in dirs and stat.S_ISDIR(st.st_mode):
                    continue
                if not stat.S_ISREG(st.st_mode):
                    return 0, None, "안에 링크 · 일반 파일 아닌 것이 있음"
                total += st.st_size
        try:
            fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=top)
            with os.fdopen(fd, "rb") as f:
                emb = (json.loads(f.read(1 << 20)) or {}).get("embedding")
            mode = emb.get("mode") if isinstance(emb, dict) and emb.get("mode") in ("fake", "real") else None
        except (OSError, ValueError, AttributeError):
            mode = None
        return total, mode, None
    except OSError:
        return 0, None, "읽을 수 없음"
    finally:
        os.close(top)


def index_build_versions(data_dir):
    """[{id, mode, bytes, skip}] of what is under <data dir>/index-builds (skip = why it is never removed). ValueError when the
    folder itself is not the server's."""
    bfd = _builds_fd(data_dir)
    if bfd is None:
        return []
    try:
        out = []
        for name in sorted(os.listdir(bfd)):
            size, mode, why = _scan_version(bfd, name)
            out.append({"id": name, "mode": mode, "bytes": size, "skip": why})
        return out
    finally:
        os.close(bfd)


def drop_index_builds(data_dir, ids, still_unused=None):
    """CHG-W2-29: remove the named versions — each one checked again just before (a plain tree, nothing linked; still_unused(id)
    when given) and removed through descriptors, files then folders, never following a link. {removed, kept: {id: why}}; a version
    already gone is neither."""
    removed, kept = [], {}
    if not ids:
        return {"removed": removed, "kept": kept}
    bfd = _builds_fd(data_dir)
    if bfd is None:
        return {"removed": removed, "kept": kept}
    try:
        for rid in ids:
            if still_unused is not None and not still_unused(rid):
                kept[rid] = "지우기 직전 다시 보니 쓰는 중"
                continue
            try:
                os.stat(rid, dir_fd=bfd, follow_symlinks=False)
            except FileNotFoundError:
                continue   # removed meanwhile (the server and the admin command both sweep)
            except OSError:
                kept[rid] = "열 수 없음"
                continue
            _, _, why = _scan_version(bfd, rid)
            if why:
                kept[rid] = why
                continue
            try:
                top = os.open(rid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=bfd)
                try:
                    for _, dirs, files, dfd in os.fwalk(".", dir_fd=top, topdown=False, follow_symlinks=False):
                        for f in files:
                            os.unlink(f, dir_fd=dfd)
                        for d in dirs:
                            os.rmdir(d, dir_fd=dfd)
                finally:
                    os.close(top)
                os.rmdir(rid, dir_fd=bfd)
                removed.append(rid)
            except FileNotFoundError:
                continue
            except OSError:
                kept[rid] = "지우지 못함"
        return {"removed": removed, "kept": kept}
    finally:
        os.close(bfd)


# ---------------------------------------------------------------- EV-051 W2-5b (CHG-W2-30 · 32, D-362): one lock · row copies
LOCK_NAME = ".index-builds.lock"
LOCK_WAIT_SERVER, LOCK_WAIT_ADMIN, LOCK_STEP = 60, 120, 0.2   # seconds (tests make them short)


class LockBusy(Exception):
    """The lock is held elsewhere for longer than the wait."""


class LockUnusable(Exception):
    """The lock file itself cannot serve (a link, another account's or not a plain file, a file system without flock)."""


def take_index_lock(data_dir, wait):
    """A descriptor holding <data dir>/.index-builds.lock (flock, exclusive) — the diagnosis pass and the admin command's --apply.
    LockBusy after `wait` seconds; LockUnusable for the lock file's own reasons (N-J3-2); any other OSError goes to the caller."""
    import errno
    import fcntl
    import stat
    import time
    dfd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EACCES, errno.EPERM, errno.EISDIR):
            raise LockUnusable("잠금 파일이 링크이거나 열 수 없음") from None
        raise
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise LockUnusable("잠금 파일이 서버 계정의 일반 파일이 아님")
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN) and time.monotonic() < end:
                time.sleep(LOCK_STEP)
                continue
            os.close(fd)
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise LockBusy() from None
            if e.errno in (errno.ENOLCK, errno.EOPNOTSUPP):
                raise LockUnusable(f"파일 시스템이 잠금을 못 함({errno.errorcode.get(e.errno, e.errno)})") from None
            raise


def release_index_lock(fd):
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


ROWS = "auto"   # <data dir>/auto/<wheel id>/<row id>/pack — the wheel's row copies (app.row_folder)


def row_copies(data_dir):
    """{wheel id: bytes} of the row copy folders under <data dir>/auto (folder names that are wheel ids only), read through
    descriptors; {} when there is none. ValueError when auto itself is not the server's plain folder."""
    import stat
    try:
        dfd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return {}
    try:
        try:
            afd = _server_dir(dfd, ROWS, False)
        except IndexStepError:
            if os.path.lexists(Path(data_dir) / ROWS):
                raise ValueError("auto가 서버 폴더가 아님") from None
            return {}
    finally:
        os.close(dfd)
    try:
        out = {}
        for name in sorted(os.listdir(afd)):
            if not re.fullmatch(r"[0-9]{1,18}", name) or not stat.S_ISDIR(os.stat(name, dir_fd=afd, follow_symlinks=False).st_mode):
                continue   # a wheel id folder only (ASCII digits)
            try:
                top = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=afd)
            except OSError:
                continue
            try:
                out[int(name)] = sum(os.stat(f, dir_fd=dfd2, follow_symlinks=False).st_size
                                     for _, _, files, dfd2 in os.fwalk(".", dir_fd=top, follow_symlinks=False) for f in files)
            except OSError:
                continue
            finally:
                os.close(top)
        return out
    finally:
        os.close(afd)


def drop_row_copies(data_dir, wheel_ids):
    """CHG-W2-32: remove <data dir>/auto/<id>/ for these wheels — through descriptors, links unlinked and never followed; auto
    itself a link or another account's folder → nothing. Returns the ids removed."""
    try:
        dfd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return []
    try:
        afd = _server_dir(dfd, ROWS, False)
    except IndexStepError:
        return []
    finally:
        os.close(dfd)
    gone = []
    try:
        for wid in wheel_ids:
            try:
                _rmtree_at(afd, str(int(wid)))
                gone.append(int(wid))
            except OSError:
                pass   # not there (a wheel without rows) or not removable now: the next sweep tries again
        return gone
    finally:
        os.close(afd)


# EV-051 CHG-W2-19 (D-346): key-shaped names never reach a real build — the same list as scripts/isolated-env KEY_RE
KEY_RE = re.compile(r"(_API_KEY$|_TOKEN$|_SECRET$|^OPENAI_|^ANTHROPIC_|^LLMOPS_API_KEY$|^AWS_|^GH_|^GITHUB_|^LANGSMITH_|^LANGCHAIN_)")


def strip_keys(env, names=()):
    """(env without key-shaped variables, the names taken out — names only, never values). Key-shaped = KEY_RE (scripts/isolated-env's
    list); `names` adds other variables to take out (a key_env or models.yaml names that are not key-shaped). EV-051 CHG-W2-19,
    shared with the Pack process environment (D-351 (4))."""
    gone = set(names)
    removed = sorted(k for k in env if k in gone or KEY_RE.search(k))
    return {k: v for k, v in env.items() if k not in removed}, removed


def build_plan(copy, store, node, corpus_folder, real_server, work_folder):
    """EV-051 CHG-W2-17 ~ 20: how the wheel would build this store's version — {mode, store, model, chars, factor, key_env, env_file,
    drop}. mode = the store's configured embedding mode; a store without one counts as real on a real-mode server only (0.12.1 embeds
    its queries for real there). Only Pack files are read for names (models.yaml, pack.yaml) — never the Pack's .env."""
    from packrt.ir import strict_yaml
    st = ((config_of(copy).get("stores") or {}).get(store)) or {}
    emb = st.get("embedding") if isinstance(st.get("embedding"), dict) else {}
    configured = emb.get("mode")
    mode = "fake" if configured == "fake" else "real" if real_server or configured == "real" else "fake"
    plan = {"mode": mode, "store": store}
    if mode == "fake":
        return plan
    doc, _ = strict_yaml.load((Path(copy) / "pack.yaml").read_text(encoding="utf-8"))
    decl = ((((doc.get("stores") or {}).get(store) or {}).get("index") or {}).get("embedding")) or {}
    models = {}
    if (Path(copy) / "models.yaml").is_file():
        models, _ = strict_yaml.load((Path(copy) / "models.yaml").read_text(encoding="utf-8"))
    providers = (models or {}).get("providers") or {}
    index = next(((n.get("params") or {}).get("index") or {} for g in (doc.get("graphs") or {}).values() for n in g.get("nodes", []) if n.get("id") == node), {})
    size, overlap = index.get("chunk_size"), index.get("overlap") or 0
    chars = 0
    for line in (Path(corpus_folder) / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            chars += len(str((json.loads(line) or {}).get("text") or ""))
    env_file = Path(work_folder) / ".env"
    plan.update(model=decl.get("model"), chars=chars, factor=size / (size - overlap) if size and size > overlap else 1.0,
                key_env=emb.get("key_env") or ((providers.get(decl.get("provider")) or {}).get("env")),
                env_file=str(env_file) if env_file.is_file() else None,
                strip=sorted({v for c in providers.values() if isinstance(c, dict) for v in (c.get("env"), c.get("base_url_env")) if isinstance(v, str)}))
    return plan


def index_build(data_dir, pack, copy, graph, node, corpus_folder, build_sha256, corpus, secrets=(), plan=None, report=None):
    """`packrt.index build` for one node of a copy, in the Pack sandbox, into a new OUT folder; then place_built. Fake embedding unless
    plan says real (EV-051 CHG-W2-19): then --embedding real --key-env <name>, every key-shaped and models.yaml-named variable taken
    out of the environment and the key reachable only through PACKRT_ENV_FILE (the Pack's .env path, never opened here). report (a
    dict) gets the build's embed_calls · embed_tokens. Returns (release_id, 'built' | 'exists')."""
    from . import guard
    plan = plan or {"mode": "fake"}
    out, out_fd = _out_folder(data_dir)   # the folder made just now and its descriptor, whatever its path becomes
    try:
        cmd = [pack["python"], "-m", "packrt.index", "build", "--pack", str(copy), "--graph", graph, "--node", node, "--corpus", str(corpus_folder),
               "--out", str(out), "--embedding", "fake", "--json"]
        env = pack_env(guard.agent_env(), strip=model_env_names(copy))   # EV-053 CHG-229: a fake build too
        if plan["mode"] == "real":
            cmd[cmd.index("fake")] = "real"
            cmd[-1:-1] = ["--key-env", plan["key_env"]]
            env, _ = strip_keys(env, {plan["key_env"], *plan.get("strip", ())})
            if plan.get("env_file"):
                env["PACKRT_ENV_FILE"] = plan["env_file"]   # the path only: packrt reads it, LLMOps never opens it (B-8)
        try:
            r = guard.sandboxed_subprocess(cmd, cwd=str(copy), write_deny=write_deny(data_dir, pack), timeout=BUILD_TIMEOUT_S, env=env)
        except Exception as e:  # noqa: BLE001
            raise IndexStepError("build 시간 초과" if "Timeout" in type(e).__name__ else f"build {type(e).__name__}") from None
        try:
            ans = json.loads(guard.redact(r.stdout.strip().splitlines()[-1], secrets))
        except (IndexError, json.JSONDecodeError):
            raise IndexStepError(f"build 종료 {r.returncode}") from None
        if r.returncode != 0 or not isinstance(ans, dict) or ans.get("status") not in ("built", "exists"):
            code = ((ans or {}).get("error") or {}).get("code") if isinstance(ans, dict) else None
            raise IndexStepError(f"build {code or f'종료 {r.returncode}'}")
        if report is not None:
            report.update(status=ans.get("status"), embed_calls=ans.get("embed_calls"), embed_tokens=ans.get("embed_tokens"))
        rid = ans.get("release_id")
        if not isinstance(rid, str) or not RELEASE_ID.fullmatch(rid) or ans.get("build_sha256") != build_sha256:
            raise IndexStepError("build 결과 대조 다름")
        return rid, place_built(data_dir, out / rid, rid, build_sha256, corpus, out_fd=out_fd)
    finally:
        os.close(out_fd)
        _drop_out(out, pack, data_dir)
