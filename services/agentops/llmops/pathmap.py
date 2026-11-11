"""path-map (EV-VPS-01 CHG-V05): rewrite the Mac absolute paths kept in the records to the VPS paths, through the server's own store
(LLMOPS_STORE: PostgreSQL as llmops_app, else the data folder's SQLite) — check (default, writes nothing), apply (one transaction,
with a record file) and undo (the record's maps reversed).

- Columns: pgmove.PATH_COLUMNS minus the append-only tables. decision.evidence (append only, AC-21 · migration 0005 trigger) is never
  changed; check and apply report how many of its rows still name an old prefix ("kept").
- A prefix is replaced only as a whole path start: not inside a longer name before it, and followed by '/', a quote or the end.
  All maps go in one pass, the longest old prefix first, so a replaced text is never replaced again (old prefixes may nest, as
  <repo>/llmops-lite-v2/.llmops-data inside <repo>/llmops-lite-v2). Refused: new prefixes that are equal or nest (undo would be
  ambiguous), a prefix that is both old and new, the same old prefix twice.
- Apply refuses when a new prefix is already in any of the columns (so undo gives back exactly the content before), and checks the
  per-column changed row counts against its own check before committing.
- No DSN, password or row value is printed; messages carry table, column, prefix and counts. The real apply on the VPS PostgreSQL
  needs its own approval (D-150 Q2); tests run on copies."""
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .pgmove import PATH_COLUMNS
from .store import APPEND_ONLY

KEYS = {"releases": "id", "pack_releases": "release_id", "runs": "id", "release_edit": "edit_id", "eval_suites": "id", "decision": "decision_id"}
COLUMNS = tuple((t, c) for t, c in PATH_COLUMNS if t not in APPEND_ONLY)
KEPT = tuple((t, c) for t, c in PATH_COLUMNS if t in APPEND_ONLY)
FORMAT = "llmops/path-map-record@1"


class PathMapStop(Exception):
    """Nothing was written; the message says why."""


def parse_maps(items):
    """['old=new', …] → [(old, new), …] longest old first. Refuses relative, empty, equal or overlapping prefixes."""
    maps = []
    for it in items or ():
        if "=" not in it:
            raise PathMapStop(f"--map은 '옛 접두=새 접두' 모양입니다: {it!r}")
        a, b = (x.rstrip("/") for x in it.split("=", 1))
        if not a.startswith("/") or not b.startswith("/") or a == b:
            raise PathMapStop(f"접두는 서로 다른 절대 경로여야 합니다: {it!r}")
        maps.append((a, b))
    if not maps:
        raise PathMapStop("--map이 하나 이상 필요합니다")
    olds, news = [a for a, _ in maps], [b for _, b in maps]
    if len(set(olds)) != len(olds):
        raise PathMapStop("같은 옛 접두가 두 번 있습니다")
    if set(olds) & set(news):
        raise PathMapStop(f"옛 접두와 새 접두에 같은 경로가 있습니다: {sorted(set(olds) & set(news))[0]}")
    for i, p in enumerate(news):
        for j, q in enumerate(news):
            if i != j and (p == q or q.startswith(p + "/")):
                raise PathMapStop(f"새 접두가 서로 겹칩니다(되돌리기가 모호함): {p} · {q}")
    for a in olds:
        for b in news:
            if b.startswith(a + "/") or a.startswith(b + "/"):
                raise PathMapStop(f"옛 접두와 새 접두가 서로 겹칩니다: {a} · {b}")   # review VS-3 L-5
    return sorted(maps, key=lambda m: -len(m[0]))


def _pattern(prefix):
    return re.compile(r"(?<![\w.\-/])" + re.escape(prefix) + r"(?=[/\"']|$)")


def rewrite(text, maps):
    """(new text, number of replacements) — every map in one pass, the longest old prefix first."""
    to = dict(maps)
    alt = re.compile(r"(?<![\w.\-/])(" + "|".join(re.escape(a) for a, _ in sorted(maps, key=lambda m: -len(m[0]))) + r")(?=[/\"']|$)")
    return alt.subn(lambda m: to[m.group(1)], text)


def _rows(db, table, column):
    key = KEYS[table]
    return [(r[0], r[1]) for r in db.execute(f"SELECT {key}, {column} FROM {table} ORDER BY {key}").fetchall()]


def content_hash(db, columns=COLUMNS):
    h = hashlib.sha256()
    for t, c in columns:
        for k, v in _rows(db, t, c):
            h.update(json.dumps([t, c, k, v], ensure_ascii=False).encode())
    return h.hexdigest()


def _plan(db, maps):
    plan, kept = {}, {}
    for t, c in COLUMNS:
        changes = []
        for k, v in _rows(db, t, c):
            if isinstance(v, str):
                new, n = rewrite(v, maps)
                if n:
                    changes.append((k, v, new))
        plan[f"{t}.{c}"] = changes
    for t, c in KEPT:
        rows = [k for k, v in _rows(db, t, c) if isinstance(v, str) and rewrite(v, maps)[1]]
        if rows:
            kept[f"{t}.{c}"] = {"rows": len(rows), "reason": "추가만 표 — 바꾸지 않음"}
    return plan, kept


def _example(old, new, maps):
    a = next(m[0] for m in maps if _pattern(m[0]).search(old))
    i = _pattern(a).search(old).start()
    before = old[max(0, i - 40): i + len(a) + 40]
    return {"before": before, "after": rewrite(before, maps)[0]}


def _unmatched(db, maps):
    """Rows (per column) where an old prefix is still written after the rewrite — at a place the boundary rule leaves alone (after
    a space, ':' …). Counts only, no value (review VS-3 L-4)."""
    out = {}
    for t, c in COLUMNS:
        n = sum(1 for _, v in _rows(db, t, c) if isinstance(v, str) and any(a in rewrite(v, maps)[0] for a, _ in maps))
        if n:
            out[f"{t}.{c}"] = n
    return out


def check(store, maps):
    """What apply would change: rows per column, one example (40 characters around), kept rows, and rows where an old prefix is
    left outside the boundary rule. Writes nothing."""
    with store.lock:
        plan, kept = _plan(store.db, maps)
        left = _unmatched(store.db, maps)
    cols = {name: {"rows": len(ch), "example": _example(ch[0][1], ch[0][2], maps)} for name, ch in plan.items() if ch}
    return {"maps": [{"from": a, "to": b} for a, b in maps], "check": cols, "kept": kept, "left_outside_rule": left, "written": 0}


def _already(db, maps):
    for t, c in COLUMNS:
        for k, v in _rows(db, t, c):
            if isinstance(v, str) and any(_pattern(b).search(v) for _, b in maps):
                return f"{t}.{c}"
    return None


def apply(store, maps, record_dir, kind="apply", expect_after=None):
    """Rewrite in one transaction and write the record file next to the data. Returns the record. expect_after: the content hash
    the rewrite must give (undo), checked before the commit."""
    Path(record_dir).mkdir(parents=True, exist_ok=True)
    name = f"path-map-record-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{kind}.json"
    out, tmp = Path(record_dir) / name, Path(record_dir) / f".{name}.tmp"
    with store.lock:
        try:
            with store.db:   # read, plan, write and check in one transaction; each row written only if it is still what was read
                hit = _already(store.db, maps)
                if hit:
                    raise PathMapStop(f"새 접두가 이미 {hit}에 있습니다 — 되돌리기가 정확하지 않으므로 바꾸지 않습니다")
                before = content_hash(store.db)
                plan, kept = _plan(store.db, maps)
                expected = {name: len(ch) for name, ch in plan.items()}
                for col, changes in plan.items():
                    t, c = col.split(".")
                    for k, old, new in changes:
                        store.db.execute(f"UPDATE {t} SET {c}=? WHERE {KEYS[t]}=? AND {c}=?", (new, k, old))
                        if store.db.execute(f"SELECT COUNT(*) FROM {t} WHERE {KEYS[t]}=? AND {c}=?", (k, new)).fetchone()[0] != 1:
                            raise PathMapStop(f"{col}의 한 줄이 읽은 뒤 바뀌었습니다(서버가 켜져 있나요?) — 되돌렸습니다")
                plan2, _ = _plan(store.db, maps)
                left = {col: len(ch) for col, ch in plan2.items() if ch}
                if left:   # every old prefix in these columns is gone, or nothing is kept
                    raise PathMapStop(f"바꾼 뒤에도 옛 접두가 남았습니다: {left} — 되돌렸습니다")
                after = content_hash(store.db)
                if expect_after and after != expect_after:
                    raise PathMapStop("되돌린 내용이 기록의 '바꾸기 전'과 다릅니다 — 쓰지 않았습니다")
                record = {"format": FORMAT, "kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "backend": store.backend,
                          "maps": [{"from": a, "to": b} for a, b in maps], "rows": expected, "kept": kept, "before_sha256": before, "after_sha256": after}
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # the record exists before the commit (review VS-3 L-2)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, out)
    record["record"] = str(out)
    return record


def undo(store, record_file, record_dir):
    """The record's maps reversed; the content afterwards must be the record's 'before'."""
    rec = json.loads(Path(record_file).read_text(encoding="utf-8"))
    if rec.get("format") != FORMAT or rec.get("kind") != "apply":
        raise PathMapStop("path-map apply 기록 파일이 아닙니다")
    with store.lock:
        if content_hash(store.db) != rec["after_sha256"]:
            raise PathMapStop("기록 뒤에 경로 칸이 바뀌었습니다 — 되돌리지 않습니다")
    return apply(store, sorted([(m["to"], m["from"]) for m in rec["maps"]], key=lambda m: -len(m[0])), record_dir, kind="undo",
                 expect_after=rec["before_sha256"])
