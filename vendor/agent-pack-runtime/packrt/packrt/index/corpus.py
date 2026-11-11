"""Corpus releases (EV-006 CHG-R08 · D-266 (2), contract C · D-267 C-4): `packrt.index corpus` writes a documents-1 corpus release
from a JSON-lines file of documents, under an id the caller gives (people, the deployment server for R12). Same id and same content
is `exists` whatever the parent (the first record stays); same id and other content is `corpus_conflict`. Written atomically under
--out only. The sample is tests/fixtures/index_h0/corpus."""
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..ir.releases import ReleaseError, check_files, dir_hash
from .errors import IndexBuildError

FORMAT = "documents-1"
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")   # used with fullmatch (a trailing newline is not an id)


def line(row):
    """One document as one line (the corpus content hash is over these bytes)."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"


_UMASK = os.umask(0)   # read once at import (os.umask can only be read by setting it); not changed again while building
os.umask(_UMASK)


def open_mode(folder):
    """A temporary folder is made 0700; the finished release is readable like any other folder here (the umask decides)."""
    os.chmod(folder, 0o777 & ~_UMASK)


def write_manifest(folder, manifest):
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")


def check_documents(rows):
    """Input rules: doc ids unique, text a string, metadata a mapping; record_id unique when written; sections inside the text, in
    order and not overlapping. Raises IndexBuildError(index_invalid) naming the line."""
    docs, records = set(), set()
    for i, r in enumerate(rows, 1):
        where = f"documents {i}번째 줄"
        if not isinstance(r, dict) or not isinstance(r.get("doc"), str) or not r["doc"]:
            raise IndexBuildError("index_invalid", f"{where}: doc(문서 id 글)이 없습니다")
        if r["doc"] in docs:
            raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'가 겹칩니다")
        docs.add(r["doc"])
        if not isinstance(r.get("text"), str):
            raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'의 text가 글이 아닙니다")
        if not isinstance(r.get("metadata", {}), dict):
            raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'의 metadata는 mapping이어야 합니다")
        if "record_id" in r:
            if not isinstance(r["record_id"], str) or not r["record_id"]:
                raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'의 record_id가 글이 아닙니다")
            if r["record_id"] in records:
                raise IndexBuildError("index_invalid", f"{where}: record_id '{r['record_id']}'가 겹칩니다")
            records.add(r["record_id"])
        if "sections" in r:
            secs, end = r["sections"], 0
            if not isinstance(secs, list) or not secs:
                raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'의 sections는 절 목록이어야 합니다")
            ids = set()
            for s in secs:
                ok = isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"] and all(
                    isinstance(s.get(k), int) and not isinstance(s.get(k), bool) for k in ("char_start", "char_end"))
                if not ok or s["id"] in ids:
                    raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}'의 절은 겹치지 않는 id와 char_start · char_end 정수가 필요합니다")
                ids.add(s["id"])
                if not (end <= s["char_start"] < s["char_end"] <= len(r["text"])):
                    raise IndexBuildError("index_invalid", f"{where}: doc '{r['doc']}' 절 '{s['id']}'의 경계가 글 밖이거나 앞 절과 겹칩니다")
                end = s["char_end"]


def read_documents(path):
    p = Path(path)
    if not p.is_file():
        raise IndexBuildError("corpus_missing", f"문서 파일이 없습니다: {path}")
    rows = []
    for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not l.strip():
            continue
        try:
            rows.append(json.loads(l))
        except ValueError:
            raise IndexBuildError("index_invalid", f"documents {i}번째 줄을 JSON으로 읽을 수 없습니다") from None
    if not rows:
        raise IndexBuildError("index_invalid", "문서가 하나도 없습니다")
    check_documents(rows)
    return rows


def read_corpus(folder):
    """A corpus release folder as (manifest, rows, {id, sha256}); checks kind, format, file hashes and the content hash."""
    folder = Path(folder)
    m = folder / "manifest.json"
    if not m.is_file():
        raise IndexBuildError("corpus_missing", f"corpus release가 없습니다(manifest.json 없음): {folder}")
    try:
        data = json.loads(m.read_text(encoding="utf-8"))
    except ValueError:
        raise IndexBuildError("index_invalid", f"corpus release manifest.json을 읽을 수 없습니다: {folder}") from None
    if data.get("kind") != "corpus" or data.get("format") != FORMAT:
        raise IndexBuildError("index_invalid", f"corpus release '{data.get('release_id')}'는 kind corpus · format {FORMAT}가 아닙니다")
    try:
        check_files(folder, data)
    except ReleaseError as e:
        raise IndexBuildError("index_invalid", str(e)) from None
    if data.get("sha256") != dir_hash(folder):
        raise IndexBuildError("index_invalid", f"corpus release '{data.get('release_id')}'의 sha256이 파일과 다릅니다")
    if not isinstance(data.get("release_id"), str) or not data["release_id"]:
        raise IndexBuildError("index_invalid", f"corpus release manifest에 release_id가 없습니다: {folder}")
    try:
        rows = [json.loads(l) for l in (folder / "documents.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        raise IndexBuildError("index_invalid", f"corpus release '{data['release_id']}'의 documents.jsonl을 읽을 수 없습니다") from None
    check_documents(rows)
    return data, rows, {"id": data["release_id"], "sha256": data["sha256"]}


def place(tmp, out, rid, same):
    """Move a finished temporary folder to out/rid in one rename. When the name is taken (before or during this call) the folder there
    is kept: same(existing folder) says whether it is the same content. Returns "built" or "exists"; the temporary folder is gone."""
    dest = out / rid
    try:
        if dest.exists():
            raise FileExistsError
        os.rename(tmp, dest)
        return "built"
    except OSError:
        if not dest.is_dir():
            raise
        shutil.rmtree(tmp, ignore_errors=True)
        return "exists" if same(dest) else None


def make(documents, rid, out, parent=None):
    """Write the corpus release; returns the stdout result {status, release_id, sha256, documents, location, error}."""
    if not ID_RE.fullmatch(rid or ""):
        raise IndexBuildError("index_invalid", f"corpus id '{rid}'는 {ID_RE.pattern} 모양이어야 합니다")
    rows = read_documents(documents)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-corpus-", dir=out))
    try:
        (tmp / "documents.jsonl").write_text("".join(line(r) for r in rows), encoding="utf-8")
        sha = dir_hash(tmp)
        manifest = {"kind": "corpus", "release_id": rid, "format": FORMAT, "documents": len(rows), "parent_release": parent,
                    "files": {"documents.jsonl": hashlib.sha256((tmp / "documents.jsonl").read_bytes()).hexdigest()},
                    "sha256": sha}
        write_manifest(tmp, manifest)
        open_mode(tmp)

        def same(dest):
            try:
                return json.loads((dest / "manifest.json").read_text(encoding="utf-8")).get("sha256") == sha == dir_hash(dest)
            except (OSError, ValueError):
                return False
        status = place(tmp, out, rid, same)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if status is None:
        have = json.loads((out / rid / "manifest.json").read_text(encoding="utf-8")).get("sha256", "")
        raise IndexBuildError("corpus_conflict", f"{rid}가 이미 다른 내용으로 있습니다(sha256 {str(have)[:12]}… ≠ {sha[:12]}…)")
    return {"status": status, "release_id": rid, "sha256": sha, "documents": len(rows), "location": str(out / rid), "error": None}
