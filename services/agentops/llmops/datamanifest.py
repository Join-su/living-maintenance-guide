"""data-manifest (EV-VPS-01 CHG-V06): the list of files of a data folder (relative path · size · sha256), read only, to compare the
Mac copy with the VPS volume after moving it. Left out: the pre-move SQLite file and its marks (the records live in PostgreSQL since
A5) and staging/ (temporary)."""
import hashlib
import json
from pathlib import Path

FORMAT = "llmops/data-manifest@1"
SKIP_FILES = ("llmops.db", "llmops.db-wal", "llmops.db-shm", "llmops.db-journal", "llmops.db.moved-to-pg", "move-record.json")
SKIP_DIRS = ("staging",)


def build(root):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"데이터 폴더가 없습니다: {root}")
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] in SKIP_DIRS or (len(rel.parts) == 1 and rel.name in SKIP_FILES) or "__pycache__" in rel.parts:
            continue
        if p.is_file() and not p.is_symlink():
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            files.append({"path": rel.as_posix(), "size": p.stat().st_size, "sha256": h.hexdigest()})
        elif p.is_symlink():
            files.append({"path": rel.as_posix(), "symlink": True})
    return {"format": FORMAT, "files": files}


def compare(here, there):
    """{"only_here", "only_there", "changed"} — every list empty when the two folders hold the same files."""
    a = {f["path"]: f for f in here["files"]}
    b = {f["path"]: f for f in there["files"]}
    return {"only_here": sorted(set(a) - set(b)), "only_there": sorted(set(b) - set(a)),
            "changed": sorted(p for p in set(a) & set(b) if a[p] != b[p])}


def load(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"data-manifest 파일이 아닙니다: {path}")
    return doc
