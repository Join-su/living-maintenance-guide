"""Model, evidence-index and table releases (spec v0.5 3.3) and the execution config (spec v0.5 3.4: releases named by
release_id, found under release_roots), read and checked without running anything. Shared by the LLMOps server (registration) and the runtime (stage 3 check before a run).

Index release folder: manifest.json + index files. index_sha256 = sha256 of sorted "path\\tsha256" lines of every file in the
folder except manifest.json. Model release folder: manifest.json + artifact file whose sha256 is artifact.sha256."""
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath

from . import pg, strict_yaml

BUILTIN_BACKENDS = {"vector": {"flat-local", "pgvector"}, "table": {"sqlite-file", "postgres-table"}}
PG_BACKENDS = {"pgvector", "postgres-table"}   # pack resource contract 3: PostgreSQL stores read by the runtime account
RUNTIME_DSN_ENV = "LLMOPSAGENT_RUNTIME_DSN"   # the only connection name a config may use (loaded code gets this one only)
INDEX_TABLE = "manual_chunk"                  # shared DB design appendix A: index releases live in <pack_id>.manual_chunk
INDEX_CHUNK_TABLE = "index_chunk"             # EV-008 CHG-P01 3: releases `packrt.index build` made live in <pack_id>.index_chunk
R2_BACKENDS = ("flat-local", "pgvector")      # EV-008 CHG-P01 1: the backends a retrieve@2 store may use
CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
SECRET_NAME_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|credential)")
# common key formats (same family as llmops.guard.KEY_PATTERNS) — a config that looks like it holds a secret is refused
SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9])(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{20,})")


class ReleaseError(ValueError):
    pass


class IndexMissing(ReleaseError):
    """No candidate vector DB version has the recipe of the retrieve@2 node (EV-006 CHG-R06, contract C): LLMOps builds one with
    `packrt.index build` and adds it to the store's releases. Fields: store, node, index_recipe_sha256, build_sha256 (from the base
    release's corpus and embedding with this builder, or None when the base release does not record them)."""

    def __init__(self, store, node, index_recipe_sha256, build_sha256, base, corpus_id):
        self.store, self.node, self.index_recipe_sha256, self.build_sha256 = store, node, index_recipe_sha256, build_sha256
        super().__init__(f"(index_missing) node '{node}'의 index recipe {index_recipe_sha256[:12]}…에 맞는 version이 store '{store}' "
                         f"후보(기준 {base} corpus {corpus_id})에 없습니다 — packrt.index build로 만들어 주세요"
                         f"(예상 build_sha256 {build_sha256[:12] + '…' if build_sha256 else '없음 — 기준 release에 corpus · embedding 기록이 없습니다'})")

    def as_dict(self):
        return {"store": self.store, "node": self.node, "index_recipe_sha256": self.index_recipe_sha256, "build_sha256": self.build_sha256}


class IndexAmbiguous(ReleaseError):
    """More than one candidate has the node's recipe, corpus and embedding (EV-006 CHG-R06)."""


def _sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dir_hash(folder, exclude=("manifest.json",)):
    folder = Path(folder)
    lines = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and rel not in exclude and "__pycache__" not in p.parts:
            lines.append(f"{rel}\t{_sha_file(p)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _manifest(folder, what):
    folder = Path(folder)
    if not folder.is_dir():
        raise ReleaseError(f"{what} release 폴더가 없습니다: {folder}")
    m = folder / "manifest.json"
    if not m.is_file():
        raise ReleaseError(f"{what} release에 manifest.json이 없습니다: {folder}")
    try:
        data = json.loads(m.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ReleaseError(f"{what} release manifest.json을 읽을 수 없습니다: {e}")
    return data, _sha_file(m)


def _inside(name):
    """A file named in a manifest must stay inside its release folder (EV-004 CHG-21)."""
    s = str(name)
    return bool(s) and not s.startswith(("/", "~")) and ".." not in PurePosixPath(s).parts and "\\" not in s


def check_files(folder, data):
    """Common release manifest check (spec v0.5 3.3): release_id, kind and files whose sha256 match the folder."""
    rid = data.get("release_id")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise ReleaseError(f"release '{rid}'의 manifest에 files가 없습니다")
    for name, digest in sorted(files.items()):
        if not _inside(name):
            raise ReleaseError(f"release '{rid}'의 files가 release 폴더 밖을 가리킵니다: {name}")
        f = Path(folder) / name
        if not f.is_file() or _sha_file(f) != digest:
            raise ReleaseError(f"release '{rid}'의 파일 hash가 manifest와 다릅니다: {name}")


def check_index(folder, store_name=None):
    data, msha = _manifest(folder, "색인")
    if data.get("kind") != "index":
        raise ReleaseError(f"색인 release의 kind가 'index'가 아닙니다: {data.get('kind')!r}")
    if store_name and data.get("store") not in (None, store_name):
        raise ReleaseError(f"release '{data.get('release_id')}'는 저장소 '{data.get('store')}'용입니다(필요: {store_name})")
    check_files(folder, data)
    actual = dir_hash(folder)
    if data.get("index_sha256") != actual:
        raise ReleaseError(f"색인 release '{data.get('release_id')}'의 manifest hash가 파일과 다릅니다")
    return {"release_id": data.get("release_id"), "manifest_sha256": msha, "index_sha256": actual,
            "embedding": data.get("embedding"), "backend": data.get("backend")}


def check_index_backend(folder):
    return (json.loads((Path(folder).expanduser() / "manifest.json").read_text(encoding="utf-8")).get("backend"))


def check_model(folder):
    data, msha = _manifest(folder, "모델")
    if data.get("kind") != "model":
        raise ReleaseError(f"모델 release의 kind가 'model'이 아닙니다: {data.get('kind')!r}")
    if data.get("trusted") is not True:
        raise ReleaseError(f"모델 release '{data.get('release_id')}'가 trusted: true가 아닙니다")
    check_files(folder, data)
    art = data.get("artifact") or {}
    if art.get("file") and not _inside(art["file"]):
        raise ReleaseError(f"release '{data.get('release_id')}'의 files가 release 폴더 밖을 가리킵니다: {art['file']}")
    f = Path(folder) / str(art.get("file", ""))
    if not art.get("file") or not f.is_file():
        raise ReleaseError(f"모델 release '{data.get('release_id')}'의 모델 파일이 없습니다: {art.get('file')}")
    if _sha_file(f) != art.get("sha256"):
        raise ReleaseError(f"모델 release '{data.get('release_id')}'의 모델 파일 hash가 manifest와 다릅니다")
    rid, fmt = data.get("release_id"), art.get("format")
    _model_is_safe(data, art, f, rid, fmt)
    fields = _feature_fields(data, rid)
    classes = _model_classes(data, f, fields, rid, fmt)
    _artifact_output(art, rid)
    info = {"release_id": rid, "manifest_sha256": msha, "artifact_sha256": art.get("sha256"),
            "format": fmt, "feature_schema_version": (data.get("feature_schema") or {}).get("version"), "classes": classes, "feature_fields": fields}
    threshold = _threshold(data, classes, rid)
    if threshold is not None:
        info["threshold"] = threshold
    return info


_MODEL_FORMATS = ("json-linear", "onnx")   # EV-003 CHG-P01: no format that runs code when loaded
_PICKLE_SUFFIXES = (".pkl", ".pickle", ".joblib")


def _model_is_safe(data, art, f, rid, fmt):
    """EV-003 CHG-P01: json-linear · onnx only; no pickle-family file in the release, no pickle mark on the model file."""
    if fmt not in _MODEL_FORMATS:
        raise ReleaseError(f"모델 release '{rid}'의 형식 '{fmt}'는 받지 않습니다(json-linear · onnx만) — pickle 계열과 '모듈:클래스'는 불러올 때 코드를 실행할 수 있어 받지 않습니다")
    for name in sorted(set(map(str, data.get("files") or {})) | {str(art["file"])}):
        if name.lower().endswith(_PICKLE_SUFFIXES):
            raise ReleaseError(f"모델 release '{rid}'에 pickle 계열 파일이 있습니다: {name}")
    with open(f, "rb") as fh:
        head = fh.read(2)
    if len(head) == 2 and head[0] == 0x80 and 2 <= head[1] <= 5:   # pickle protocol 2 ~ 5 starts with PROTO 0x80 + version
        raise ReleaseError(f"모델 release '{rid}'의 모델 파일이 pickle 모양입니다")


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _names(value, least):
    return isinstance(value, list) and len(value) >= least and all(isinstance(v, str) and v for v in value) and len(set(value)) == len(value)


def _feature_fields(data, rid):
    """EV-003 CHG-P02: the model's input fields, in order."""
    fields = (data.get("feature_schema") or {}).get("fields") if isinstance(data.get("feature_schema"), dict) else None
    if not _names(fields, 1):
        raise ReleaseError(f"모델 release '{rid}'에 feature_schema.fields(모델 입력 칸 목록)가 없거나 형식이 틀립니다")
    return list(fields)


def _model_classes(data, f, fields, rid, fmt):
    """EV-003 CHG-P02: the class names in the model's order — the json-linear file (read as JSON, no code) or the onnx manifest."""
    if fmt == "onnx":
        if not _names(data.get("classes"), 2):
            raise ReleaseError(f"모델 release '{rid}'(onnx)에 classes가 없습니다")
        return list(data["classes"])
    bad = f"모델 release '{rid}'의 json-linear 모델 파일 모양이 틀립니다: "
    try:
        m = json.loads(Path(f).read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ReleaseError(bad + "UTF-8 JSON이 아닙니다") from None
    if not isinstance(m, dict):
        raise ReleaseError(bad + "객체가 아닙니다")
    classes, feats, weights, bias = m.get("classes"), m.get("features"), m.get("weights"), m.get("bias")
    if not _names(classes, 2):
        raise ReleaseError(bad + "classes는 겹치지 않는 이름 2개 이상이어야 합니다")
    if not isinstance(feats, list):
        raise ReleaseError(bad + "features가 목록이 아닙니다")
    if not (isinstance(weights, list) and len(weights) == len(classes)
            and all(isinstance(row, list) and len(row) == len(feats) and all(map(_number, row)) for row in weights)):
        raise ReleaseError(bad + "weights는 class 수 × 칸 수의 숫자여야 합니다")
    if not (isinstance(bias, list) and len(bias) == len(classes) and all(map(_number, bias))):
        raise ReleaseError(bad + "bias는 class 수만큼의 숫자여야 합니다")
    if feats != fields:
        raise ReleaseError(f"모델 release '{rid}'의 모델 파일 features가 manifest feature_schema.fields와 다릅니다")
    if data.get("classes") is not None and data.get("classes") != classes:
        raise ReleaseError(f"모델 release '{rid}'의 모델 파일 classes가 manifest classes와 다릅니다")
    return list(classes)


def _artifact_output(art, rid):
    """EV-003 CHG-P06: artifact.output is {kind: proba} or {kind: proba, name}; this runtime takes probability models only."""
    out = art.get("output")
    if out is None:
        return
    kind = out.get("kind") if isinstance(out, dict) else None
    if not (isinstance(out, dict) and kind == "proba" and set(out) <= {"kind", "name"} and ("name" not in out or (isinstance(out["name"], str) and out["name"]))):
        raise ReleaseError(f"모델 release '{rid}'의 artifact.output은 {{kind: proba, name?}}만 받습니다 — 이 runtime은 확률 출력 모델만 받습니다(받은 것: {kind})")


def _threshold_sha256(values):
    """EV-003 CHG-P03: sha256 of the threshold values written as canonical JSON (sorted keys, no spaces, UTF-8)."""
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _threshold(data, classes, rid):
    """EV-003 CHG-P03: the optional threshold_config {sha256, values: {min_probability, normal_classes}} — checked, or None."""
    tc = data.get("threshold_config")
    if tc is None:
        return None
    bad = f"모델 release '{rid}'의 threshold_config가 틀립니다: "
    if not isinstance(tc, dict) or set(tc) != {"sha256", "values"}:
        raise ReleaseError(bad + "{sha256, values}만 받습니다")
    v = tc["values"]
    if not isinstance(v, dict) or set(v) != {"min_probability", "normal_classes"}:
        raise ReleaseError(bad + "values는 {min_probability, normal_classes}만 받습니다")
    mp, normal = v["min_probability"], v["normal_classes"]
    if not (_number(mp) and 0 <= mp <= 1):
        raise ReleaseError(bad + "min_probability는 0 ~ 1 숫자여야 합니다")
    if not _names(normal, 1):
        raise ReleaseError(bad + "normal_classes는 겹치지 않는 class 이름 1개 이상이어야 합니다")
    unknown = [c for c in normal if c not in classes]
    if unknown:
        raise ReleaseError(bad + f"normal_classes에 없는 class가 있습니다: {', '.join(unknown)}")
    if len(normal) == len(classes):
        raise ReleaseError(bad + "normal_classes가 모든 class입니다(이상 class가 없음)")
    digest = _threshold_sha256(v)
    if tc["sha256"] != digest:
        raise ReleaseError(f"모델 release '{rid}'의 threshold_config sha256이 values와 다릅니다")
    return {"min_probability": mp, "normal_classes": list(normal), "sha256": digest}


def check_table(folder, store_name=None):
    """Table release (spec v0.5 3.3): manifest kind table, format sqlite, file inside the folder and listed in files."""
    data, msha = _manifest(folder, "표")
    rid = data.get("release_id")
    if data.get("kind") != "table":
        raise ReleaseError(f"표 release의 kind가 'table'이 아닙니다: {data.get('kind')!r}")
    if store_name and data.get("store") not in (None, store_name):
        raise ReleaseError(f"release '{rid}'는 저장소 '{data.get('store')}'용입니다(필요: {store_name})")
    check_files(folder, data)
    name = data.get("file")
    if name and not _inside(name):
        raise ReleaseError(f"release '{rid}'의 files가 release 폴더 밖을 가리킵니다: {name}")
    if data.get("format") != "sqlite" or not name or name not in (data.get("files") or {}):
        raise ReleaseError(f"표 release '{rid}'에는 format: sqlite와 files에 있는 file이 필요합니다")
    f = Path(folder) / name
    return {"release_id": rid, "manifest_sha256": msha, "path": str(f), "sha256": _sha_file(f)}


def find_releases(roots):
    """release_id -> {real folder: folder} for every manifest.json one or two levels under the roots (spec v0.5 3.4).
    The same folder reached through overlapping roots counts once; unreadable manifests are skipped."""
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


def _locate(found, rid):
    places = found.get(rid) or {}
    if not places:
        raise ReleaseError(f"release '{rid}'을 보관 폴더에서 찾을 수 없습니다")
    if len(places) > 1:
        raise ReleaseError(f"release 번호가 겹칩니다: {rid} ({', '.join(sorted(str(v) for v in places.values()))})")
    return next(iter(places.values()))


def _kind_of(folder):
    try:
        return json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8")).get("kind")
    except (OSError, ValueError):
        return None


def _secret_like(value, path=()):
    if isinstance(value, dict):
        for k, v in value.items():
            if SECRET_NAME_RE.search(str(k)):
                return path + (k,)
            hit = _secret_like(v, path + (k,))
            if hit:
                return hit
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hit = _secret_like(v, path + (i,))
            if hit:
                return hit
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        return path
    return None


TOP_KEYS = {"config_version", "entrypoint", "environment", "release_roots", "stores", "models"}


def _abs(base, value):
    """A path from the config: relative to the config file's folder; tidied with normpath, symlinks NOT resolved, so the
    Pack env folder is still found by its pyvenv.cfg (EV-004 CHG-20, CHG-09)."""
    p = os.path.expanduser(str(value))
    return Path(os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p)))


def _release_ref(value, what):
    if not isinstance(value, str) or not value:
        raise ReleaseError(f"{what}의 release(번호)가 실행 설정에 없습니다")
    if "/" in value or "\\" in value:
        raise ReleaseError(f"release는 경로가 아니라 번호(release_id)로 적으세요: {value}")
    return value


def uses_postgres(cfg_stores):
    return any(isinstance(b, dict) and b.get("backend") in PG_BACKENDS for b in (cfg_stores or {}).values())


def _embedding_config(name, b):
    """(embedding block, mode) checked from the config alone, before any release or database is read."""
    e = b.get("embedding")
    if e is not None and (not isinstance(e, dict) or set(e) - {"mode", "key_env"}):
        raise ReleaseError(f"store '{name}'의 embedding은 {{mode, key_env}}만 받습니다")
    e = e or {}
    mode = e.get("mode", "real")
    if mode not in ("real", "fake"):
        raise ReleaseError(f"store '{name}'의 embedding.mode는 real 또는 fake입니다(받은 것: {mode!r})")
    if mode == "real" and not e.get("key_env"):
        raise ReleaseError("실제 임베딩에는 API 키 환경 변수 이름(embedding.key_env)이 필요합니다")
    return e, mode


def _embedding(name, b, store_doc, release_emb, file_release):
    """Embedding mode and the E-3 match (pack resource contract 4) for a pgvector store, or a vector store whose config writes
    `embedding`. Returns {"embedding_mode", "embedding_dim", "embedding_key_env", "embedding_model"}; raises ReleaseError."""
    e, mode = _embedding_config(name, b)
    decl = ((store_doc.get("index") or {}).get("embedding") or {})
    rel = release_emb or {}
    rid = rel.get("release_id")
    if not rel.get("mode") or not rel.get("dim"):
        raise ReleaseError(f"(embedding_mismatch) release '{rid}'에 임베딩 mode·dim 기록이 없습니다"
                           + (" — 파일 release는 manifest embedding에 mode·dim이 있어야 embedding을 적을 수 있습니다" if file_release else ""))
    for k in ("provider", "model"):
        if decl.get(k) and rel.get(k) != decl.get(k):
            raise ReleaseError(f"(embedding_mismatch) release '{rid}'의 임베딩 {k} '{rel.get(k)}'가 Pack 선언 '{decl.get(k)}'와 다릅니다")
    if rel["mode"] != mode:
        raise ReleaseError(f"(embedding_mismatch) release '{rid}'는 {rel['mode']} 임베딩인데 실행 설정은 {mode}입니다")
    return {"embedding_mode": mode, "embedding_dim": int(rel["dim"]), "embedding_key_env": e.get("key_env"), "embedding_model": rel.get("model")}


def chunk_table(manifest, stype, name):
    """The table a PostgreSQL release's rows live in: `<pack_id>.index_chunk` for a release `packrt.index build` made (EV-008 CHG-P01 3,
    retrieve@1 and @2 alike), else the manifest's `table` or the default (as 0.12.0)."""
    if stype == "vector" and isinstance(manifest.get("builder"), dict) and manifest["builder"].get("name") == "packrt.index":
        return INDEX_CHUNK_TABLE
    return manifest.get("table") or (INDEX_TABLE if stype == "vector" else name)


def _pg_store(name, b, stype, pack_doc, env, rows=None):
    """Binding of a PostgreSQL store: the release row read with the runtime account (pack resource contract 3). rows: the rows
    already read for a retrieve@2 store ({release_id: row}, EV-008 CHG-P01 2) — then no second read."""
    conn = b.get("connection")
    if not isinstance(conn, dict) or set(conn) != {"env"} or not conn.get("env"):
        raise ReleaseError(f"저장소 '{name}'의 접속 정보 환경 변수 이름(connection.env)이 없습니다")
    if conn["env"] != RUNTIME_DSN_ENV:
        raise ReleaseError(f"실행용 접속 정보는 {RUNTIME_DSN_ENV}만 씁니다(저장소 '{name}': {conn['env']})")
    if stype == "vector":
        _embedding_config(name, b)   # config-only checks first (no database needed to refuse a bad config)
    dsn = (env or {}).get(conn["env"])
    if not dsn:
        raise ReleaseError(f"접속 정보 환경 변수 '{conn['env']}'이 없습니다")
    rid = _release_ref(b.get("release"), f"{stype} store '{name}'")
    schema = (pack_doc.get("pack") or {}).get("id")
    if rows is None:
        try:
            row = pg.release_row(dsn, schema, rid)
        except pg.PgError as e:
            raise ReleaseError(str(e).replace(dsn, "[가림]")) from None
    else:
        row = rows.get(rid)
    return _pg_bound(name, b, stype, pack_doc, rid, row)


def _pg_bound(name, b, stype, pack_doc, rid, row):
    """The binding of one data_release row for store `name` (kind · store · table · E-3 checks); raises ReleaseError."""
    conn = b["connection"]
    schema = (pack_doc.get("pack") or {}).get("id")
    if row is None:
        raise ReleaseError(f"release '{rid}'이 DB에 적재되지 않았습니다")
    want = "index" if stype == "vector" else "table"
    if row["kind"] != want:
        raise ReleaseError(f"release '{rid}'는 kind '{row['kind']}'라 저장소 '{name}'({stype})에 backend '{b.get('backend')}'로 쓸 수 없습니다")
    if row.get("store") not in (None, name):   # like a file release's manifest store (check_table / check_index)
        raise ReleaseError(f"release '{rid}'는 저장소 '{row['store']}'용입니다(필요: {name})")
    manifest = row["manifest"] or {}
    table = chunk_table(manifest, stype, name)
    try:
        pg.ident(table, "표")
    except pg.PgError as e:
        raise ReleaseError(str(e)) from None
    bound = {"type": stype, "backend": b["backend"], "release_id": rid, "sha256": row["sha256"], "manifest_sha256": pg.canonical_sha256(manifest),
             "connection_env": conn["env"], "schema": schema, "table": table, "manifest": manifest}
    if stype == "vector":
        emb = {"release_id": rid, "provider": row["embedding_provider"], "model": row["embedding_model"], "dim": row["embedding_dim"],
               "mode": row["embedding_mode"]}
        bound["embedding"] = {k: v for k, v in emb.items() if k != "release_id"}
        bound["index_sha256"] = manifest.get("index_sha256")
        bound.update(_embedding(name, b, pack_doc["stores"][name], emb, file_release=False))
    return bound


def _pick_index(name, b, base_bound, found, store_doc, node, deny):
    """retrieve@2 store (EV-006 CHG-R06, contract C · D-267 C-1): the store's `release` is the base and fixes the corpus; the
    candidates are the base and the store's `releases`; the one whose recipe hash is the node's, whose corpus is the base's and whose
    embedding passes the E-3 match is bound. None: IndexMissing; more than one: IndexAmbiguous."""
    from ..index.recipe import index_recipe, index_recipe_sha256
    want = index_recipe_sha256(index_recipe(store_doc, (node.get("params") or {}).get("index")))
    extra = b.get("releases")
    if extra is not None and (not isinstance(extra, list) or not extra):
        raise ReleaseError(f"실행 설정의 store '{name}' releases는 release 번호 목록이어야 합니다")
    base_m = json.loads((Path(base_bound["path"]) / "manifest.json").read_text(encoding="utf-8"))
    cands, seen = [(base_bound, base_m)], {base_bound["release_id"]}
    for rid in extra or []:
        rid = _release_ref(rid, f"vector store '{name}' releases")
        if rid in seen:
            continue
        seen.add(rid)
        folder = _locate(found, rid)
        if _kind_of(folder) != "index":
            raise ReleaseError(f"release '{rid}'는 kind '{_kind_of(folder)}'라 저장소 '{name}'(vector)에 쓸 수 없습니다")
        info = {k: v for k, v in check_index(folder, name).items() if k != "backend"}
        bound = {"type": "vector", "backend": b.get("backend"), "path": str(folder), **info, "index_backend": check_index_backend(folder)}
        if b.get("embedding") is not None:
            try:
                bound.update(_embedding(name, b, store_doc, {"release_id": rid, **(info.get("embedding") or {})}, file_release=True))
            except ReleaseError:   # a candidate whose embedding does not match is not a match (the base keeps raising as before)
                bound = None
        deny.append(str(folder))
        if bound is not None:
            cands.append((bound, json.loads((folder / "manifest.json").read_text(encoding="utf-8"))))
    return _choose(name, node, want, base_bound["release_id"], base_m, cands)


def _choose(name, node, want, base_id, base_m, cands):
    """C-1's last step, shared by folders and database rows: of [(binding, manifest)] the one with the node's recipe hash and the
    base's corpus; none: IndexMissing (with the build hash the builder will give); more than one: IndexAmbiguous."""
    from ..index.recipe import build_sha256
    corpus = base_m.get("corpus_release")
    hits = [bd for bd, m in cands if m.get("index_recipe_sha256") == want and m.get("corpus_release") == corpus]
    if len(hits) == 1:
        return hits[0]
    if hits:
        ids = " · ".join(h["release_id"] for h in hits)
        raise IndexAmbiguous(f"(index_ambiguous) node '{node['id']}'의 index recipe {want[:12]}…에 맞는 version이 "
                             f"{'둘' if len(hits) == 2 else f'{len(hits)}개'}입니다: {ids} — releases에 하나만 두세요")
    emb = base_m.get("embedding") or {}
    ok = isinstance(corpus, dict) and corpus.get("id") and corpus.get("sha256") and all(emb.get(k) for k in ("provider", "model", "dim", "mode"))
    raise IndexMissing(name, node["id"], want, build_sha256(corpus, want, emb) if ok else None, base_id,
                       (corpus or {}).get("id") if isinstance(corpus, dict) else None)


def _pg_rows(name, b, pack_doc, env, ids):
    """The data_release rows of a retrieve@2 pgvector store's base and candidates, read at once (EV-008 CHG-P01 2)."""
    dsn = (env or {}).get(b["connection"]["env"])
    if not dsn:
        raise ReleaseError(f"접속 정보 환경 변수 '{b['connection']['env']}'이 없습니다")
    try:
        return pg.release_rows(dsn, (pack_doc.get("pack") or {}).get("id"), ids)
    except pg.PgError as e:
        raise ReleaseError(str(e).replace(dsn, "[가림]")) from None


def _extra_ids(name, b):
    extra = b.get("releases")
    if extra is not None and (not isinstance(extra, list) or not extra):
        raise ReleaseError(f"실행 설정의 store '{name}' releases는 release 번호 목록이어야 합니다")
    return [_release_ref(rid, f"vector store '{name}' releases") for rid in extra or []]


def _pick_index_pg(name, b, base_bound, rows, pack_doc, node):
    """retrieve@2 pgvector store (EV-008 CHG-P01 2): C-1 as _pick_index over data_release rows — the base row fixes the corpus; a
    candidate is a row of `releases` (kind · store checked as the base; one whose embedding fails E-3 is no candidate); the one whose
    manifest has the node's recipe hash and the base's corpus is bound. None: IndexMissing; more than one: IndexAmbiguous."""
    from ..index.recipe import index_recipe, index_recipe_sha256
    store_doc = pack_doc["stores"][name]
    want = index_recipe_sha256(index_recipe(store_doc, (node.get("params") or {}).get("index")))
    cands, seen = [base_bound], {base_bound["release_id"]}
    for rid in _extra_ids(name, b):
        if rid in seen:
            continue
        seen.add(rid)
        try:
            cands.append(_pg_bound(name, b, "vector", pack_doc, rid, rows.get(rid)))
        except ReleaseError as e:
            if not str(e).startswith("(embedding_mismatch)"):   # a candidate whose embedding does not match is not a match
                raise
    return _choose(name, node, want, base_bound["release_id"], base_bound["manifest"], [(bd, bd["manifest"]) for bd in cands])


def _unchecked(name, pack_doc, node):
    """EV-008 CHG-P01 7: a retrieve@2 pgvector store the probe cannot choose for (no database) — {store, node, index_recipe_sha256}."""
    from ..index.recipe import index_recipe, index_recipe_sha256
    want = index_recipe_sha256(index_recipe(pack_doc["stores"][name], (node.get("params") or {}).get("index")))
    return {"store": name, "node": node["id"], "index_recipe_sha256": want}


def load_execution_config(path, pack_doc, env=None, pg_lookup=True, collect_missing=False):
    """Read and check an execution config (spec v0.5 3.4: release numbers, no secrets) against a validated pack.yaml.
    Returns {"config" (normalized: absolute python and release_roots), "config_sha256", "config_text", "entrypoint",
    "python", "bindings", "deny_paths"}. Raises ReleaseError.
    env: where PostgreSQL stores find their connection variable (default: this process's environment; EV-020 CHG-96·97).
    collect_missing: a retrieve@2 store with no version of its node's recipe is left unbound and listed in "index_missing"
    ([IndexMissing.as_dict()]) instead of raising — for the graph probe (EV-006 N-3); every other error still raises. With it the
    result also has "index_unchecked": the retrieve@2 pgvector stores pg_lookup=False could not choose for (EV-008 CHG-P01 7)."""
    conn_env = os.environ if env is None else env
    # pg_lookup=False: PostgreSQL stores are bound by name only (no database, no DSN) — the graph probe builds the graph without
    # reading data (EV-020 CHG-97: the probe process gets no DSN)
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    if not p.is_file():
        raise ReleaseError(f"실행 설정 파일이 없습니다: {path}")
    base = str(p.parent)
    text = p.read_text(encoding="utf-8")
    if SECRET_VALUE_RE.search(text):   # the whole text, comments included
        raise ReleaseError("실행 설정 파일에 키 형식의 값이 있습니다(주석 포함). 키는 환경에만 두세요")
    try:
        cfg, _ = strict_yaml.load(text)
    except strict_yaml.StrictYAMLError as e:
        raise ReleaseError(f"실행 설정 {e}")
    if not isinstance(cfg, dict):
        raise ReleaseError("실행 설정은 mapping이어야 합니다")
    hit = _secret_like(cfg)
    if hit is not None:
        raise ReleaseError(f"실행 설정에 비밀 값처럼 보이는 항목이 있습니다({'/'.join(map(str, hit))}). 키는 환경에만 두세요")
    unknown = set(cfg) - TOP_KEYS
    if unknown:
        raise ReleaseError(f"실행 설정에 모르는 항목이 있습니다: {sorted(unknown)}")
    if cfg.get("config_version") != 1:
        raise ReleaseError(f"실행 설정 형식 판을 알 수 없습니다: {cfg.get('config_version')} — 명세 v0.5 3.4장 형식(config_version: 1)으로 쓰세요")
    entry = cfg.get("entrypoint")
    if not entry:
        raise ReleaseError("실행 설정에 entrypoint가 없습니다 — 실행할 그래프를 적으세요")
    eps = [e["graph"] for e in pack_doc.get("entrypoints", [])]
    if entry not in eps:
        raise ReleaseError(f"실행 설정의 entrypoint '{entry}'가 pack.yaml entrypoints에 없습니다({eps})")
    env = cfg.get("environment")
    if not isinstance(env, dict):
        raise ReleaseError("실행 설정에 environment가 없습니다(kind: local-python, python)")
    if env.get("kind") != "local-python":   # lockfile·image are reserved for deployment (spec v0.5 3.4)
        raise ReleaseError(f"실행 설정 environment '{env.get('kind')}'는 아직 지원하지 않습니다(local-python만)")
    if set(env) - {"kind", "python"}:
        raise ReleaseError(f"실행 설정 environment에 모르는 항목이 있습니다: {sorted(set(env) - {'kind', 'python'})}")
    if not env.get("python"):
        raise ReleaseError("실행 설정 environment에 python이 없습니다")
    py = _abs(base, env["python"])
    if not (py.is_file() and os.access(py, os.X_OK)):
        raise ReleaseError(f"실행 설정의 python이 실행 파일이 아닙니다: {py}")
    raw_roots = cfg.get("release_roots")
    only_pg = isinstance(cfg.get("stores"), dict) and bool(cfg["stores"]) and all(
        isinstance(b, dict) and b.get("backend") in PG_BACKENDS for b in cfg["stores"].values()) and not (cfg.get("models") or {})
    if raw_roots in (None, []) and only_pg:   # every store reads PostgreSQL and there are no models: no release folder to find (the normalized copy keeps [])
        raw_roots = []
    elif not isinstance(raw_roots, list) or not raw_roots:
        raise ReleaseError("실행 설정에 release_roots가 없습니다")
    roots = [_abs(base, r) for r in raw_roots]
    for r in roots:
        if not r.is_dir():
            raise ReleaseError(f"release_roots의 폴더가 없습니다: {r}")
    found = find_releases(roots)
    stores_doc = pack_doc.get("stores") or {}
    cfg_stores, cfg_models = cfg.get("stores") or {}, cfg.get("models") or {}
    if not isinstance(cfg_stores, dict) or not isinstance(cfg_models, dict):
        raise ReleaseError("실행 설정의 stores·models는 mapping이어야 합니다")
    graph = pack_doc["graphs"][entry]
    used_stores, used_models, used2 = set(), set(), {}   # used2: store -> first retrieve@2 node reading it (its nodes share index values)
    for n in graph.get("nodes", []):
        k = n["kind"].split("@")[0]
        if k == "retrieve":
            used_stores.add(n["params"]["store"])
            if n["kind"] == "retrieve@2":
                used2.setdefault(n["params"]["store"], n)
        elif n["kind"] == "custom_code@3":   # the table stores handed to the function as resources (spec 11 supplement 3)
            used_stores.update((n.get("params") or {}).get("stores") or [])
        elif k == "predict":
            used_models.add(n["params"]["model"])
    for name in sorted(set(cfg_stores) - set(stores_doc)):
        raise ReleaseError(f"실행 설정의 store '{name}'가 pack.yaml stores에 없습니다")
    for name in sorted(used_stores):
        if not isinstance(cfg_stores.get(name), dict):
            raise ReleaseError(f"entrypoint '{entry}'가 쓰는 store '{name}'의 연결이 실행 설정에 없습니다")
    for name in sorted(used_models):
        if not isinstance(cfg_models.get(name), dict) or not cfg_models[name].get("release"):
            raise ReleaseError(f"entrypoint '{entry}'가 쓰는 모델 '{name}'의 release가 실행 설정에 없습니다")
    bindings, deny, missing, unchecked = {"stores": {}, "models": {}}, [str(r) for r in roots], [], []
    for name in sorted(cfg_stores):   # config-only checks of every store before any release folder or database is read
        b = cfg_stores[name]
        if not isinstance(b, dict) or name not in stores_doc:
            continue
        stype = stores_doc[name]["type"]
        if name in used2 and b.get("backend") not in R2_BACKENDS:   # before any choosing (EV-006 N-6, EV-008 CHG-P01 1)
            raise ReleaseError(f"(backend_unsupported) store '{name}': retrieve@2는 flat-local · pgvector만 씁니다")
        if b.get("backend") in PG_BACKENDS:
            conn = b.get("connection")
            if not isinstance(conn, dict) or set(conn) != {"env"} or not conn.get("env"):
                raise ReleaseError(f"저장소 '{name}'의 접속 정보 환경 변수 이름(connection.env)이 없습니다")
            if conn["env"] != RUNTIME_DSN_ENV:
                raise ReleaseError(f"실행용 접속 정보는 {RUNTIME_DSN_ENV}만 씁니다(저장소 '{name}': {conn['env']})")
        if stype == "vector" and (b.get("backend") == "pgvector" or b.get("embedding") is not None):
            _embedding_config(name, b)
        if name in used2 and b.get("backend") == "pgvector":   # releases checked without a database too, so the probe refuses what the start refuses
            _extra_ids(name, b)
    store_folders = {}   # store name -> its release folder (table resources of custom_code@3 read manifest and table file here; bindings unchanged)
    # every number in the config is found, checked and denied (CHG-21, CHG-22); only what the entrypoint uses is bound to
    # the run, so the run record and the same-condition comparison keep covering the used releases only (as before)
    for name in sorted(cfg_stores):
        b = cfg_stores[name]
        if not isinstance(b, dict):
            raise ReleaseError(f"실행 설정의 store '{name}'는 mapping이어야 합니다")
        stype = stores_doc[name]["type"]
        allowed = {"backend", "release"} if stype in ("vector", "table") else {"backend"}
        if b.get("backend") in PG_BACKENDS:
            allowed = allowed | {"connection"}
        if stype == "vector":
            allowed = allowed | {"embedding"}
        if name in used2:
            allowed = allowed | {"releases"}   # candidates for a retrieve@2 store only; elsewhere still an unknown item
        if set(b) - allowed:
            raise ReleaseError(f"실행 설정의 store '{name}'에 모르는 항목이 있습니다: {sorted(set(b) - allowed)}")
        backend = b.get("backend")
        if backend not in BUILTIN_BACKENDS.get(stype, set()) and not (isinstance(backend, str) and CLASS_RE.match(backend)):
            raise ReleaseError(f"store '{name}'({stype})의 backend '{backend}'를 알 수 없습니다(기본: {sorted(BUILTIN_BACKENDS.get(stype, []))} 또는 '모듈:클래스')")
        if stype not in ("vector", "table"):
            if name in used_stores:
                bindings["stores"][name] = {"type": stype, "backend": backend}
            continue
        if backend in PG_BACKENDS:
            if (backend == "pgvector") != (stype == "vector"):
                raise ReleaseError(f"저장소 '{name}'({stype})에 backend '{backend}'를 쓸 수 없습니다")
            if name in used2 and pg_lookup:   # base and candidates in one read, then C-1 over the rows (EV-008 CHG-P01 2)
                rid = _release_ref(b.get("release"), f"{stype} store '{name}'")
                _embedding_config(name, b)
                rows = _pg_rows(name, b, pack_doc, conn_env, [rid] + _extra_ids(name, b))
                bound = _pg_store(name, b, stype, pack_doc, conn_env, rows=rows)
                try:
                    bound = _pick_index_pg(name, b, bound, rows, pack_doc, used2[name])
                except IndexMissing as e:
                    if not collect_missing:
                        raise
                    missing.append(e.as_dict())
                    continue
            else:
                bound = (_pg_store(name, b, stype, pack_doc, conn_env) if pg_lookup else   # checked even when unused, like a file release
                         {"type": stype, "backend": backend, "release_id": _release_ref(b.get("release"), f"{stype} store '{name}'"),
                          "connection_env": RUNTIME_DSN_ENV, "schema": (pack_doc.get("pack") or {}).get("id"), "unresolved": True})
                if name in used2:
                    unchecked.append(_unchecked(name, pack_doc, used2[name]))
            if name in used_stores:
                bindings["stores"][name] = bound
            continue
        rid = _release_ref(b.get("release"), f"{stype} store '{name}'")
        folder = _locate(found, rid)
        want = "index" if stype == "vector" else "table"
        kind = _kind_of(folder)
        if kind != want:
            raise ReleaseError(f"release '{rid}'는 kind '{kind}'라 저장소 '{name}'({stype})에 쓸 수 없습니다")
        if stype == "vector":
            info = check_index(folder, name)
            info = {k: v for k, v in info.items() if k != "backend"}   # the execution config chooses the backend, not the index manifest
            bound = {"type": stype, "backend": backend, "path": str(folder), **info, "index_backend": check_index_backend(folder)}
            if b.get("embedding") is not None:   # a file index with an explicit embedding mode is matched like a pgvector store (E-3)
                bound.update(_embedding(name, b, stores_doc[name], {"release_id": rid, **(info.get("embedding") or {})}, file_release=True))
            if name in used2:
                deny.append(str(folder))   # the base is checked and denied whichever candidate is bound
                try:
                    bound = _pick_index(name, b, bound, found, stores_doc[name], used2[name], deny)
                except IndexMissing as e:
                    if not collect_missing:
                        raise
                    missing.append(e.as_dict())
                    continue
                folder = Path(bound["path"])
        else:
            info = check_table(folder, name)
            bound = {"type": stype, "backend": backend, **info}
        if name in used_stores:
            bindings["stores"][name] = bound
            store_folders[name] = str(folder)
        deny.append(str(folder))
    for name in sorted(cfg_models):
        b = cfg_models[name]
        if not isinstance(b, dict):
            raise ReleaseError(f"실행 설정의 모델 '{name}'는 mapping이어야 합니다")
        if set(b) - {"release"}:
            raise ReleaseError(f"실행 설정의 모델 '{name}'에 모르는 항목이 있습니다: {sorted(set(b) - {'release'})}")
        rid = _release_ref(b.get("release"), f"모델 '{name}'")
        folder = _locate(found, rid)
        kind = _kind_of(folder)
        if kind != "model":
            raise ReleaseError(f"release '{rid}'는 kind '{kind}'라 모델 '{name}'에 쓸 수 없습니다")
        bound = {"path": str(folder), **check_model(folder)}
        if name in used_models:
            bindings["models"][name] = bound
        deny.append(str(folder))
    norm = {"config_version": 1, "entrypoint": entry, "environment": {"kind": "local-python", "python": str(py)},
            "release_roots": [str(r) for r in roots], "stores": cfg_stores, "models": cfg_models}
    out = {"config": norm, "config_sha256": config_sha256(norm), "config_text": config_text(norm), "entrypoint": entry,
           "python": str(py), "bindings": bindings, "store_folders": store_folders, "deny_paths": sorted(set(deny))}
    if collect_missing:
        out["index_missing"] = missing
        out["index_unchecked"] = unchecked
    return out


def config_sha256(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def config_text(cfg):
    """The checked config written back as plain YAML (no comments): what the Pack copy keeps instead of the original file."""
    import yaml
    return "# execution config checked at registration (comments are not kept)\n" + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=True)
