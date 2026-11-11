"""Store and model backends chosen by the execution config (spec 3.2, 6). Built in: vector `flat-local` (exact search over a
release folder: chunks.jsonl + vectors.json) and `pgvector` (the same search over the chunk rows of one release: `<pack_id>.index_chunk`
for a release `packrt.index build` made, else `<pack_id>.manual_chunk` or the manifest's table — EV-008 CHG-P01 3),
table `sqlite-file` (read only) and `postgres-table` (`<pack_id>.<table>` rows of one release, runtime account), model
`json-linear` and `onnx` (when onnxruntime is installed in the Pack environment). Any other backend is `module:Class` importable
in the Pack environment. PostgreSQL backends read the DSN from the environment variable the config names (pack resource
contract 3) and never put it in a message."""
import importlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


class BackendError(RuntimeError):
    pass


class RerankInvalid(BackendError):
    """retrieve@2 re-rank input is not usable (D-273 R-1): the release has no norm for a field, or the query or a chunk lacks a sensor
    value. The node fails with rerank_invalid."""


class ModelLoadError(BackendError):
    """The model cannot be opened as its release says (EV-003 CHG-P06): the predict node fails with model_load."""


class ModelOutputError(BackendError):
    """The model answered in a shape the release does not allow (EV-003 CHG-P06): the predict node fails with model_output."""


def load_class(spec):
    mod, cls = spec.split(":")
    return getattr(importlib.import_module(mod), cls)


def _cos(a, b):
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def match_filter(md, flt):
    """retrieve@1 filter (spec v0.5 supplement 11): per key, one value must equal, a list on either side must contain the
    other value, two lists must overlap; a missing metadata key fails; a null or empty-list filter value is no condition."""
    for k, want in (flt or {}).items():
        if want is None or (isinstance(want, (list, tuple)) and not want):
            continue
        if k not in md:
            return False
        have = md[k]
        hs = list(have) if isinstance(have, (list, tuple)) else [have]
        ws = list(want) if isinstance(want, (list, tuple)) else [want]
        if not any(h == w for h in hs for w in ws):
            return False
    return True


class FlatLocal:
    """Exact similarity / MMR search over a flat-local index release (read only)."""

    def __init__(self, release, **_):
        folder = Path(release)
        self.chunks = [json.loads(l) for l in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.vectors = json.loads((folder / "vectors.json").read_text(encoding="utf-8"))

    def search(self, qvec, top_k=4, search="similarity", mmr=None, score_threshold=None, filter=None):
        cand = []
        for c in self.chunks:
            md = c.get("metadata") or {}
            if not match_filter(md, filter):
                continue
            cand.append((_cos(qvec, self.vectors[c["id"]]), c))
        cand.sort(key=lambda x: (-x[0], x[1]["id"]))
        if score_threshold is not None:
            cand = [x for x in cand if x[0] >= score_threshold]
        if search == "mmr":
            mmr = mmr or {}
            pool = cand[: int(mmr.get("fetch_k", 20))]
            lam = float(mmr.get("lambda", 0.5))
            picked = []
            while pool and len(picked) < top_k:
                def gain(x):
                    red = max((_cos(self.vectors[x[1]["id"]], self.vectors[p[1]["id"]]) for p in picked), default=0.0)
                    return lam * x[0] - (1 - lam) * red
                best = min(pool, key=lambda x: (-gain(x), x[1]["id"]))
                picked.append(best); pool.remove(best)
            cand = picked
        return [{"score": round(s, 6), **c} for s, c in cand[:top_k]]

    def search2(self, qvec, query, p, flt, norm=None, rerank_query=None):
        """retrieve@2 over this release. vector: as search() (cosine). lexical: BM25 ranks. hybrid: RRF of the BM25 ranks of every
        filtered chunk and the first `pool` vector ranks. Then the first top_k; with rerank, score = w_retrieval · base ÷ max(base) +
        w_sensor · (1 − d ÷ max(d)) over those top_k (C1-a15 R3); score_threshold on the final score. Each hit keeps locator,
        ranks{lexical, vector, fused} and the metadata fields asked in return_metadata.
        EV-015 (D-414): rerank.pool (≥ top_k, default top_k) — the re-rank sees the first `pool` candidates instead and the first top_k
        of its order are kept, so the sensors can choose which records come back; without it the search is the same as before."""
        method, top_k = p.get("method", "vector"), int(p.get("top_k", 4))
        rr = p.get("rerank")
        cut = int(rr.get("pool") or top_k) if rr else top_k
        by_id = {c["id"]: c for c in self.chunks if match_filter(c.get("metadata") or {}, flt)}
        ids = sorted(by_id)
        lex_rank, vec_rank, base = {}, {}, {}
        if method in ("lexical", "hybrid"):
            lx = p.get("lexical") or {}
            bm = BM25([(c["id"], c.get("text", "")) for c in self.chunks], lx.get("k1", 1.5), lx.get("b", 0.75), lx.get("tokenizer", "ko-bigram-1"))
            sc = bm.scores(query, ids)
            lex = sorted(ids, key=lambda d: (-sc[d], d))
            lex_rank = {d: i for i, d in enumerate(lex, start=1)}
        if method in ("vector", "hybrid"):
            fuse = p.get("fuse") or {}
            n = int(fuse.get("pool", 300)) if method == "hybrid" else cut
            hits = self.search(qvec, n, p.get("search", "similarity"), p.get("mmr"), None, flt)
            vec = [h["id"] for h in hits]
            vec_rank = {d: i for i, d in enumerate(vec, start=1)}
            cos = {h["id"]: h["score"] for h in hits}
        if method == "vector":
            order, base = vec, cos
        elif method == "lexical":
            order, base = lex, {d: sc[d] for d in ids}
        else:
            base = rrf([lex, vec], k=int((p.get("fuse") or {}).get("k", 60)))
            order = sorted(base, key=lambda d: (-base[d], d))
        top = order[:cut]
        fused_rank = {d: i for i, d in enumerate(top, start=1)}
        if method == "vector" and not rr:
            final = {d: base[d] for d in top}
        else:
            mb = max((base[d] for d in top), default=0.0) or 1.0
            final = {d: base[d] / mb for d in top}
            if rr:
                w = rr["weights"]
                for f in rr["fields"]:
                    if not isinstance(norm, dict) or not isinstance(norm.get(f), dict) or "std" not in norm[f]:
                        raise RerankInvalid(f"release의 rerank_norm에 '{f}'가 없습니다")
                    if not isinstance(rerank_query, dict) or rerank_query.get(f) is None:
                        raise RerankInvalid(f"재정렬 질의에 센서 칸 '{f}' 값이 없습니다")
                    bad = [d for d in top if (by_id[d].get("metadata") or {}).get(f) is None]
                    if bad:
                        raise RerankInvalid(f"조각 {bad[0]}에 센서 칸 '{f}' 값이 없습니다")
                dist = {d: sensor_distance(rerank_query, by_id[d].get("metadata") or {}, norm, rr["fields"]) for d in top}
                md = max(dist.values(), default=0.0) or 1.0
                final = {d: float(w["retrieval"]) * final[d] + float(w["sensor"]) * (1 - dist[d] / md) for d in top}
            top = sorted(top, key=lambda d: (-final[d], d))[:top_k]
        th = p.get("score_threshold")
        keep = [m for m in (p.get("return_metadata") or [])]
        out = []
        for d in top:
            if th is not None and final[d] < th:
                continue
            c = by_id[d]
            md_all = c.get("metadata") or {}
            out.append({**c, "score": round(final[d], 6), "locator": c.get("locator"),
                        "ranks": {"lexical": lex_rank.get(d), "vector": vec_rank.get(d), "fused": fused_rank[d]},
                        "metadata": {k: md_all.get(k) for k in keep}, "_metadata_all": md_all})
        return out


# --- retrieve@2 (EV-006 CHG-R02 · R03 · R04): the same arithmetic as C1-a15 R3 (retrieval/tokens.py · bm25.py · search.py) ---
_WORD = re.compile(r"[0-9A-Za-z가-힣._%-]+")
_HANGUL = re.compile(r"[가-힣]")


def tokenize_ko_bigram_1(text):
    """Tokenizer `ko-bigram-1`: each word (lower case) and, for a Hangul word longer than two letters, its 2-letter windows."""
    tokens = []
    for word in _WORD.findall(text or ""):
        tokens.append(word.lower())
        if _HANGUL.search(word) and len(word) > 2:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


TOKENIZERS = {"ko-bigram-1": tokenize_ko_bigram_1}


class BM25:
    """BM25 over every chunk of a release (document frequency and average length from all of them); ranks only the given ids."""

    def __init__(self, docs, k1=1.5, b=0.75, tokenizer="ko-bigram-1"):
        self.k1, self.b, self.tok = float(k1), float(b), TOKENIZERS[tokenizer]
        self.tf, self.lengths, self.df = {}, {}, Counter()
        for doc_id, text in docs:
            if doc_id in self.tf:
                continue
            terms = Counter(self.tok(text))
            self.tf[doc_id], self.lengths[doc_id] = terms, sum(terms.values())
            self.df.update(terms.keys())

    def scores(self, query, ids):
        q = self.tok(query)
        n = len(self.tf)
        avg = sum(self.lengths.values()) / n if n else 0.0
        out = {}
        for d in ids:
            tf, length, s = self.tf[d], self.lengths[d], 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * length / avg))
            out[d] = s
        return out


def rrf(rank_lists, k=60):
    fused = {}
    for ranks in rank_lists:
        for i, rid in enumerate(ranks, start=1):
            fused[rid] = fused.get(rid, 0.0) + 1.0 / (k + i)
    return fused


def sensor_distance(a, b, norm, fields):
    """Distance over the sensor fields, each divided by its standard deviation from the release's rerank_norm (0 → 1)."""
    return math.sqrt(sum(((float(a[f]) - float(b[f])) / (float(norm[f]["std"]) or 1.0)) ** 2 for f in fields))


def _pg_dsn(env_name):
    dsn = os.environ.get(env_name or "")
    if not dsn:
        raise BackendError(f"접속 정보 환경 변수 '{env_name}'이 없습니다")
    return dsn


class _PgReader:
    """One read-only connection per store object, opened on first use and reused (pack resource contract 3); a lock keeps two
    parallel nodes reading the same store from sharing the connection at the same moment."""

    def __init__(self, dsn):
        import threading
        self._dsn, self._conn, self._lock = dsn, None, threading.Lock()

    def rows(self, sql, args):
        from ..ir import pg
        with self._lock:
            if self._conn is None or self._conn.closed:
                try:
                    self._conn = pg.connect(self._dsn)
                except pg.PgError as e:
                    raise BackendError(str(e)) from None
            try:
                cur = self._conn.execute(sql, args)
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            except Exception as e:  # noqa: BLE001 - type only
                raise BackendError(f"PostgreSQL 읽기 실패: {type(e).__name__}") from None

    def close(self):
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
                return True
            return False


class PgVector(FlatLocal):
    """`flat-local` search over the chunks of one release in `<schema>.<table>` (exact cosine, no index; shared DB design). A release
    `packrt.index build` made lives in `index_chunk` and keeps each chunk's locator, so retrieve@2 (search2) gives what flat-local gives
    (EV-008 CHG-P01 4); any other table is read as 0.12.0 did."""

    def __init__(self, release_id, schema, table, connection_env, **_):
        from ..ir import pg
        from ..ir.releases import INDEX_CHUNK_TABLE
        reader = _PgReader(_pg_dsn(connection_env))
        loc = ", locator" if table == INDEX_CHUNK_TABLE else ""
        try:
            rows = reader.rows(f'SELECT chunk_id, text, metadata{loc}, embedding::text AS embedding FROM "{pg.ident(schema, "schema")}"."{pg.ident(table, "표")}" '
                               "WHERE release_id = %s ORDER BY chunk_id", (release_id,))
        finally:
            reader.close()   # the whole release is read once; searches run in memory
        self.chunks = [{"id": r["chunk_id"], "metadata": r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"]), "text": r["text"],
                        **({"locator": r["locator"] if isinstance(r["locator"], dict) else json.loads(r["locator"])} if loc else {})} for r in rows]
        self.vectors = {r["chunk_id"]: json.loads(r["embedding"]) for r in rows}


class PostgresTable:
    """Read-only table store over one release: equality `where`, `select`, `limit` (the `sqlite-file` rules)."""

    def __init__(self, release_id, schema, table, connection_env, key=None, **_):
        from ..ir import pg
        self.release_id, self.schema, self.table = release_id, pg.ident(schema, "schema"), pg.ident(table, "표")
        self.key = pg.ident(key, "key") if key else None
        self.reader = _PgReader(_pg_dsn(connection_env))

    def query(self, where=None, select=None, limit=5):
        from ..ir import pg
        cols = ", ".join(f'"{pg.ident(c, "열")}"' for c in select) if select else "*"
        conds = [(pg.ident(k, "열"), v) for k, v in (where or {}).items() if v is not None]
        sql = (f'SELECT {cols} FROM "{self.schema}"."{self.table}" WHERE release_id = %s'
               + "".join(f' AND "{k}" = %s' for k, _ in conds)
               + (f' ORDER BY "{self.key}"' if self.key else "") + " LIMIT %s")   # key order: the order sqlite-file rows come in
        rows = self.reader.rows(sql, [self.release_id] + [v for _, v in conds] + [int(limit)])
        return [{k: v for k, v in r.items() if k != "release_id"} if not select else r for r in rows]


class SqliteFile:
    """Read-only table store: equality `where`, `select`, `limit`."""

    def __init__(self, file, table, **_):
        self.uri = f"file:{Path(file)}?mode=ro"
        self.table = table

    def query(self, where=None, select=None, limit=5):
        if not self.table.replace("_", "").isalnum():
            raise BackendError(f"표 이름을 쓸 수 없습니다: {self.table}")
        cols = ", ".join(f'"{c}"' for c in select) if select else "*"
        conds = [(k, v) for k, v in (where or {}).items() if v is not None]
        sql = f'SELECT {cols} FROM "{self.table}"' + (" WHERE " + " AND ".join(f'"{k}" = ?' for k, _ in conds) if conds else "") + " LIMIT ?"
        db = sqlite3.connect(self.uri, uri=True)
        db.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in db.execute(sql, [v for _, v in conds] + [int(limit)])]
        finally:
            db.close()


class JsonLinear:
    """Softmax over W·x + b; classes, features, weights, bias in the artifact JSON."""

    def __init__(self, path, **_):
        self.m = json.loads(Path(path).read_text(encoding="utf-8"))

    def predict(self, x):
        """x: the input values in feature_schema.fields order (checked by the engine) → class probabilities in the model's class order."""
        z = [sum(w * v for w, v in zip(row, x)) + b for row, b in zip(self.m["weights"], self.m["bias"])]
        mx = max(z)
        e = [math.exp(v - mx) for v in z]
        s = sum(e)
        return [v / s for v in e]


ORT_TELEMETRY_ENV = "ORT_DISABLE_TELEMETRY"


def _onnxruntime():
    """onnxruntime with its telemetry off (EV-009 CHG-T01, D-311 · D-315): the official macOS · Linux builds send usage events to
    Microsoft over HTTPS unless ORT_DISABLE_TELEMETRY=1 is set before onnxruntime is imported (onnxruntime docs/Privacy.md; the API
    alone did not stop it). Set here, right before the import, and left set; the API is called too. An onnxruntime already imported
    without the variable cannot be turned off any more, so the model is not opened (model_load)."""
    if sys.modules.get("onnxruntime") is not None and os.environ.get(ORT_TELEMETRY_ENV) != "1":
        raise ModelLoadError(f"onnx 모델을 열기 전에 onnxruntime이 telemetry를 끄지 않은 채 이미 불러와졌습니다 — {ORT_TELEMETRY_ENV}=1을 먼저 두세요")
    if os.environ.get(ORT_TELEMETRY_ENV) != "1":   # written once: parallel branches opening models do not rewrite the environment
        os.environ[ORT_TELEMETRY_ENV] = "1"
    import onnxruntime as ort
    off = getattr(ort, "disable_telemetry_events", None)
    if callable(off):
        off()
    return ort


class Onnx:
    """ONNX probability classifier (onnxruntime must be installed in the Pack environment), checked against its release
    (EV-003 CHG-P06): one float input [N, fields], the probabilities as a ZipMap (dict per row) or a row of numbers in classes order.
    onnxruntime is opened with its telemetry off (EV-009)."""

    def __init__(self, path, classes=None, features=None, output=None, **_):
        try:
            ort = _onnxruntime()
        except ImportError:
            raise ModelLoadError("onnx 모델에는 Pack 환경에 onnxruntime이 필요합니다")
        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.classes, self.features = list(classes or []), list(features or [])
        ins = self.sess.get_inputs()
        if len(ins) != 1:
            raise ModelLoadError(f"onnx 모델 입력은 하나여야 합니다(받은 것: {len(ins)}개)")
        self.input = ins[0]
        if self.input.type != "tensor(float)":
            raise ModelLoadError(f"onnx 모델 입력 형이 tensor(float)가 아닙니다: {self.input.type}")
        shape = list(self.input.shape or [])
        dynamic = lambda d: d is None or isinstance(d, str)   # the docs do not say which (GAP-002): both are taken
        if len(shape) != 2 or not (dynamic(shape[1]) or shape[1] == len(self.features)):
            raise ModelLoadError(f"onnx 모델 입력 모양이 [N, 칸 수]가 아닙니다: {shape}")
        names = [o.name for o in self.sess.get_outputs()]
        name = (output or {}).get("name")
        if name is not None and name not in names:
            raise ModelLoadError(f"onnx 모델에 출력 '{name}'이 없습니다")
        self.out_index = names.index(name) if name is not None else len(names) - 1

    def predict(self, x):
        """x: the input values in feature_schema.fields order → class probabilities in manifest classes order."""
        import numpy as np
        outs = self.sess.run(None, {self.input.name: np.array([x], dtype=np.float32)})
        try:
            row = outs[self.out_index][0]
        except (IndexError, KeyError, TypeError):
            raise ModelOutputError("onnx 확률 출력 모양이 class 수와 다릅니다: 첫 행이 없음") from None
        if isinstance(row, dict):   # ZipMap: {class id or name: probability}
            got = {str(k): v for k, v in row.items()}
            if set(got) != set(self.classes):
                raise ModelOutputError(f"onnx 확률 출력의 class가 manifest classes와 다릅니다: {', '.join(sorted(set(got) ^ set(self.classes)))}")
            probs = [got[c] for c in self.classes]
        else:
            row = row.tolist() if hasattr(row, "tolist") else row
            if not isinstance(row, (list, tuple)) or len(row) != len(self.classes):
                raise ModelOutputError(f"onnx 확률 출력 모양이 class 수와 다릅니다: {len(row) if isinstance(row, (list, tuple)) else type(row).__name__}")
            probs = list(row)
        values = []
        for c, p in zip(self.classes, probs):
            try:
                v = float(p)
            except (TypeError, ValueError):
                v = math.nan
            if not (math.isfinite(v) and -1e-6 <= v <= 1 + 1e-6):
                raise ModelOutputError(f"onnx 확률 값이 0 ~ 1 밖입니다: {c}")
            values.append(v)
        return values


def open_store(binding, store_doc, schemas_dir=None):
    backend, stype = binding["backend"], store_doc["type"]
    if backend == "pgvector":
        return PgVector(**binding)
    if backend == "postgres-table":
        return PostgresTable(**binding, key=store_doc.get("key"))
    if backend == "flat-local":
        return FlatLocal(binding["path"])
    if backend == "sqlite-file":
        return SqliteFile(binding["path"], table=binding.get("table") or store_doc.get("table") or binding["name"])
    return load_class(backend)(**binding, store=store_doc)


def open_model(binding, manifest):
    art = manifest["artifact"]
    path = Path(binding["path"]) / art["file"]
    fmt = art.get("format")
    fields = (manifest.get("feature_schema") or {}).get("fields")
    if fmt == "json-linear":
        return JsonLinear(path)
    if fmt == "onnx":
        return Onnx(path, classes=manifest.get("classes"), features=fields, output=art.get("output"))
    raise BackendError(f"모델 형식 '{fmt}'를 쓸 수 없습니다(json-linear, onnx 또는 '모듈:클래스')")
