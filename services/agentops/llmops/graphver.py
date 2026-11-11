"""D-536 ⑥: graph versions — the vector DB release path widened to the relation graph (D-534: the relation layer in a graph DB, each
version a tag on its nodes). A version here = the base layer's tag (the Pack's own, never written) + the know-how rows on top, in its
own tag (PREFIX only): a :Layer node {base, build_sha256} and one :Knowhow node per row pointing at a copy of its cause.

| vector DB version          | graph version                                         |
| release id · build_sha256  | tag · build_sha256 = sha256(base tag + rows)          |
| same hash → reused         | same — the same rows on the same base make no new tag |
| Pack config store → release| the Pack's node.yaml `layer:` line (D-533)            |
| rollback = the version before | rollback = the tag before                         |

The records (one manifest per tag, the tag in use and the ones before it) are files in the given folder — no table, no migration."""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PREFIX = "llmops2-kh-"   # this session's tags; every other tag is read only
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class TagError(ValueError):
    """A write to a tag that is not this session's."""


class GraphError(RuntimeError):
    """The graph DB refused or could not be reached — the HTTP status and the error code only, never its message."""


def _own(tag):
    if not str(tag).startswith(PREFIX):
        raise TagError(f"쓰기는 {PREFIX} 꼬리표만 — {tag}")


def build_sha(base, rows):
    return hashlib.sha256(json.dumps({"base": base, "rows": rows}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class MemGraph:
    """The graph in memory (tests): {tag: {"causes": {code: {name}}, ...}}; written versions add {"base", "knowhow"}."""

    def __init__(self, layers):
        self.layers = {t: dict(v) for t, v in layers.items()}

    def causes(self, tag):
        return dict(self.layers[tag].get("causes") or {})

    def write_version(self, tag, base, rows):
        _own(tag)
        if tag in self.layers:
            raise GraphError(f"이미 있는 꼬리표 {tag}")
        self.layers[tag] = {"base": base, "knowhow": [dict(r) for r in rows]}

    def knowhow(self, tag):
        return [dict(r) for r in self.layers.get(tag, {}).get("knowhow") or []]


class HttpGraph:
    """The graph DB copy over its HTTP query API (Neo4j ≥ 5 · /db/<db>/query/v2) — no driver to install. The address must be this
    machine's; the user/password line (NEO4J_AUTH=user/password) is read from the env file into memory on each call, never shown.
    post(url, body, headers) → (status, json) can be given (tests); the default goes through urllib without proxies."""

    def __init__(self, url, env_file, db="neo4j", post=None):
        host = urlparse(url).hostname
        if host not in LOOPBACK:
            raise GraphError(f"그래프 DB 주소는 이 컴퓨터만 — {host}")
        self.url, self.env_file, self.db, self._post = url.rstrip("/"), Path(env_file), db, post or _post

    def __repr__(self):
        return f"HttpGraph({self.url}, db={self.db})"

    def _auth(self):
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("NEO4J_AUTH="):
                user, pw = line.split("=", 1)[1].strip().split("/", 1)
                return "Basic " + base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
        raise GraphError("NEO4J_AUTH 줄 없음")

    def query(self, statement, **params):
        body = json.dumps({"statement": statement, "parameters": params}, ensure_ascii=False)
        status, got = self._post(f"{self.url}/db/{self.db}/query/v2", body,
                                 {"Authorization": self._auth(), "Content-Type": "application/json", "Accept": "application/json"})
        if status not in (200, 202) or (got or {}).get("errors"):
            code = next((e.get("code") for e in (got or {}).get("errors") or [] if isinstance(e, dict)), None)
            raise GraphError(f"그래프 DB 거절 — HTTP {status}" + (f" {code}" if code else ""))
        data = (got or {}).get("data") or {}
        return [dict(zip(data.get("fields") or [], v)) for v in data.get("values") or []]

    def causes(self, tag):
        return {r["code"]: {"name": r["name"], "in_manual": r["in_manual"]}
                for r in self.query("MATCH (c:Cause {layer: $tag}) RETURN c.code AS code, c.name AS name, c.in_manual AS in_manual", tag=tag)}

    def checks(self, tag):
        """{check section: [the causes it checks]} of a layer."""
        rows = self.query("MATCH (s:Step {layer: $tag, kind: 'check'}) OPTIONAL MATCH (s)-[:CHECKS]->(c:Cause) "
                          "RETURN s.section AS section, collect(c.code) AS causes", tag=tag)
        return {r["section"]: sorted(r["causes"]) for r in rows}

    def write_version(self, tag, base, rows):
        _own(tag)
        if self.query("MATCH (n {layer: $tag}) RETURN count(n) AS n", tag=tag)[0]["n"]:
            raise GraphError(f"이미 있는 꼬리표 {tag}")
        self.query("CREATE (m:Layer {layer: $tag, base: $base, knowhow: $ids, build_sha256: $sha}) "
                   "WITH m UNWIND range(0, size($rows) - 1) AS i WITH i, $rows[i] AS r "
                   "CREATE (k:Knowhow) SET k = r, k.layer = $tag, k.order = i "
                   "WITH k, r OPTIONAL MATCH (b:Cause {layer: $base, code: r.then}) "
                   "MERGE (c:Cause {layer: $tag, code: r.then}) ON CREATE SET c.name = b.name, c.in_manual = b.in_manual "
                   "CREATE (k)-[:POINTS_TO]->(c) RETURN count(k) AS n",
                   tag=tag, base=base, ids=[r["id"] for r in rows], sha=build_sha(base, rows), rows=[dict(r) for r in rows])

    def knowhow(self, tag):
        return [{x: v for x, v in r["k"].items() if x not in ("layer", "order")}
                for r in self.query("MATCH (k:Knowhow {layer: $tag}) RETURN properties(k) AS k ORDER BY k.order", tag=tag)]


def _post(url, body, headers):
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        raise GraphError(f"그래프 DB 연결 실패({type(e).__name__})") from None


class Versions:
    """The graph versions this session made: <dir>/<tag>.json (tag · base · from · rows · build_sha256 · status 시험 · 운영 · 거절 · 이전 ·
    what the test found) and <dir>/current.json (the tag in use and the ones before it). Before any is added, the base is in use."""

    def __init__(self, folder, base):
        self.dir, self.base = Path(folder), base

    def _write(self, name, data):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    def list(self):
        num = lambda p: int(re.sub(r"\D", "", p.stem) or 0)   # noqa: E731
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.dir.glob(f"{PREFIX}*.json"), key=num)] if self.dir.is_dir() else []

    def get(self, tag):
        p = self.dir / f"{tag}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _state(self):
        p = self.dir / "current.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"tag": self.base, "before": []}

    def current(self):
        return self._state()["tag"]

    def rows(self, tag=None):
        tag = tag or self.current()
        return [] if tag == self.base else list((self.get(tag) or {}).get("rows") or [])

    def make(self, graph, rows):
        """(tag, made): the version of these rows on the base — an existing one with the same build hash is used again."""
        sha = build_sha(self.base, rows)
        same = next((v for v in self.list() if v["build_sha256"] == sha), None)
        if same:
            return same["tag"], False
        tag = f"{PREFIX}{1 + max([int(v['tag'][len(PREFIX):]) for v in self.list()] or [0])}"
        graph.write_version(tag, self.base, rows)
        self._write(tag, {"tag": tag, "base": self.base, "from": self.current(), "rows": rows, "build_sha256": sha, "status": "시험",
                          "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return tag, True

    def note(self, tag, **fields):
        self._write(tag, {**self.get(tag), **fields})

    def adopt(self, tag):
        st = self._state()
        if st["tag"] != self.base:
            self.note(st["tag"], status="이전")
        self.note(tag, status="운영")
        self._write("current", {"tag": tag, "before": st["before"] + [st["tag"]]})

    def reject(self, tag, why):
        self.note(tag, status="거절", why=why)

    def rollback(self):
        """The tag before the one in use goes back in use; the one in use becomes '이전'. Nothing is deleted."""
        st = self._state()
        if not st["before"]:
            return st["tag"]
        prev = st["before"][-1]
        if st["tag"] != self.base:
            self.note(st["tag"], status="이전")
        if prev != self.base:
            self.note(prev, status="운영")
        self._write("current", {"tag": prev, "before": st["before"][:-1]})
        return prev
