"""Pack revision (spec 3.1.1): canonical content hash of the revision files.

pack.yaml, models.yaml: strict read -> ir_version 1 defaults -> kind@N normalization -> canonical JSON.
schemas/*.json: JSON -> canonical JSON. prompts/, templates/, custom/, .env.example, requirements.txt: raw bytes (never normalized;
requirements.txt since spec v0.5 supplement 3).
pack_revision = "sha256:" + sha256 of sorted lines "path\\tfile_sha256". raw_files keeps each file's raw byte hash."""
import copy
import hashlib
import json
from pathlib import Path

from . import strict_yaml

STRUCTURAL = ("pack.yaml", "models.yaml")
BYTE_DIRS = ("prompts", "templates", "custom")
SKIP_PARTS = {"__pycache__"}


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def apply_defaults(doc):
    """Fill the ir_version 1 default table (spec 3.1.1). Never uses runtime-specific defaults."""
    doc = copy.deepcopy(doc)
    env = doc.get("env")
    if isinstance(env, dict):
        env.setdefault("optional", [])
    for store in (doc.get("stores") or {}).values():
        if isinstance(store, dict) and store.get("type") == "vector":
            store.setdefault("metadata_required", [])
    for graph in (doc.get("graphs") or {}).values():
        for node in (graph or {}).get("nodes", []) if isinstance(graph, dict) else []:
            if not isinstance(node, dict):
                continue
            kind = node.get("kind")
            if isinstance(kind, str) and "@" not in kind:
                node["kind"] = kind + "@1"
            node.setdefault("on_error", {"retry": 0, "then": "fail"})
            if isinstance(node["on_error"], dict):
                node["on_error"].setdefault("retry", 0)
                node["on_error"].setdefault("then", "fail")
            node.setdefault("idempotent", False)
    return doc


def revision_files(folder):
    """Relative paths (posix) of the revision files that exist in folder."""
    folder = Path(folder)
    out = [n for n in (*STRUCTURAL, ".env.example", "requirements.txt") if (folder / n).is_file()]
    for d in ("schemas", *BYTE_DIRS):
        base = folder / d
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(folder)
                if p.is_file() and not (set(rel.parts) & SKIP_PARTS) and not any(part.startswith(".") for part in rel.parts):
                    if d == "schemas" and p.suffix != ".json":
                        continue
                    out.append(rel.as_posix())
    return sorted(out)


def file_hash(folder, rel):
    data = (Path(folder) / rel).read_bytes()
    if rel in STRUCTURAL:
        value, _ = strict_yaml.load(data.decode("utf-8"))
        if rel == "pack.yaml":
            value = apply_defaults(value)
        return _sha(canonical_json(value))
    if rel.startswith("schemas/"):
        return _sha(canonical_json(json.loads(data.decode("utf-8"))))
    return _sha(data)


def compute(folder):
    """Return (pack_revision, raw_files). Raises strict_yaml.StrictYAMLError / ValueError on unreadable files."""
    files = revision_files(folder)
    lines = [f"{rel}\t{file_hash(folder, rel)}" for rel in files]
    raw = {rel: _sha((Path(folder) / rel).read_bytes()) for rel in files}
    return "sha256:" + _sha("\n".join(lines).encode("utf-8")), raw
