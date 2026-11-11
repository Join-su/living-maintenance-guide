"""`packrt.index build` (EV-006 CHG-R08, contract C · D-267): one vector DB version from a corpus release and one retrieve@2 node's
recipe — cut the documents (record · section · chars, chunk_size · overlap only where the store is cut), keep each piece's locator and
the recipe metadata, count rerank_norm over the documents its `from` selects, embed, write a flat-local index release. The same inputs
give the same release (`exists`, nothing embedded); the folder is finished and checked under a temporary name and moved in one rename,
under --out only. The sample is tests/fixtures/index_h0/indexes."""
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from ..ir import load_pack
from ..ir.releases import ReleaseError, check_index, dir_hash
from ..run.embedding import FAKE_DIM, fake_embedding
from .corpus import line, open_mode, place, read_corpus, write_manifest
from .errors import IndexBuildError
from .recipe import BUILDER, RecipeError, build_sha256, index_recipe_of, index_recipe_sha256, release_id


def windows(start, end, size, overlap):
    """[s, min(s + size, end)) from start, then s += size − overlap, until the end is reached; one window when size is None."""
    if size is None:
        return [(start, end)]
    out, s = [], start
    while True:
        e = min(s + size, end)
        out.append((s, e))
        if e >= end:
            return out
        s += size - overlap


def pieces(rows, recipe):
    """Chunks {id, text, metadata, locator}: record → <record_id>#0, section → <doc>#<section>[#<k> when chunk_size is set],
    chars → <doc>#<k>. Positions are code points; a section chunk counts from its section's first character (D-277: not per chunk,
    so the answer positions stay when chunk_size · overlap change), the others from the document start."""
    split, size, overlap, keys = recipe["split"], recipe["chunk_size"], recipe["overlap"], recipe["metadata"]
    out = []
    for d in rows:
        md = {k: d.get("metadata", {})[k] for k in keys if k in d.get("metadata", {})}
        text = d["text"]
        if split == "record":
            if "record_id" not in d:
                raise IndexBuildError("index_invalid", f"split record인데 doc '{d['doc']}'에 record_id가 없습니다")
            out.append({"id": f"{d['record_id']}#0", "text": text, "metadata": md,
                        "locator": {"doc": d["doc"], "record_id": d["record_id"], "char_start": 0, "char_end": len(text)}})
        elif split == "section":
            if "sections" not in d:
                raise IndexBuildError("index_invalid", f"split section인데 doc '{d['doc']}'에 sections가 없습니다")
            for s in d["sections"]:
                for k, (a, b) in enumerate(windows(s["char_start"], s["char_end"], size, overlap)):
                    out.append({"id": f"{d['doc']}#{s['id']}" + (f"#{k}" if size is not None else ""), "text": text[a:b], "metadata": md,
                                "locator": {"doc": d["doc"], "section": s["id"], "char_start": a - s["char_start"], "char_end": b - s["char_start"]}})
        elif split == "chars":
            for k, (a, b) in enumerate(windows(0, len(text), size, overlap)):
                out.append({"id": f"{d['doc']}#{k}", "text": text[a:b], "metadata": md,
                            "locator": {"doc": d["doc"], "char_start": a, "char_end": b}})
        else:
            raise IndexBuildError("index_invalid", f"나누기 기준 '{split}'를 알 수 없습니다")
    ids = [p["id"] for p in out]
    if len(ids) != len(set(ids)):
        raise IndexBuildError("index_invalid", "조각 id가 겹칩니다(record_id · doc · section id를 확인하세요)")
    if not out:
        raise IndexBuildError("index_invalid", "조각이 하나도 없습니다")
    return out


def rerank_norm(rows, spec):
    """{field: {mean, std (population), n}} over the documents whose metadata has every `from` value (G-7), six decimals."""
    if not spec:
        return None
    pick = [d for d in rows if all(d.get("metadata", {}).get(k) == v for k, v in (spec.get("from") or {}).items())]
    if not pick:
        raise IndexBuildError("index_invalid", f"rerank_norm from {spec.get('from')}에 맞는 문서가 없습니다")
    out = {}
    for f in spec["fields"]:
        xs = [d.get("metadata", {}).get(f) for d in pick]
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in xs):
            raise IndexBuildError("index_invalid", f"rerank_norm 칸 '{f}'가 숫자가 아닌 문서가 있습니다")
        xs = [float(x) for x in xs]
        mean = sum(xs) / len(xs)
        out[f] = {"mean": round(mean, 6), "std": round((sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5, 6), "n": len(xs)}
    return out


def fake_embedder(texts):
    """(vectors, calls, tokens) — the packrt fake rule (64 numbers, no provider)."""
    return [fake_embedding(t) for t in texts], 0, 0


def _same_build(build_sha):
    def same(dest):
        try:
            m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            check_index(dest, m.get("store"))
            return m.get("build_sha256") == build_sha
        except (OSError, ValueError, ReleaseError):
            return False
    return same


def _existing(out, store, corpus_ref, rsha, emb):
    """The finished version under --out built from the same inputs, or None. Fake: its build hash is known before embedding. Real: the
    model's vector size is known only from an answer, so a version with the same corpus · recipe · builder · provider · model · mode
    (any size) is the same build — nothing is sent to the provider again."""
    if emb["dim"] is not None:
        rid = release_id(store, build_sha256(corpus_ref, rsha, emb))
        if not (out / rid).exists():
            return None
        if not _same_build(build_sha256(corpus_ref, rsha, emb))(out / rid):
            raise IndexBuildError("index_invalid", f"'{out / rid}'에 같은 이름의 다른 폴더가 있습니다")
        return out / rid
    if not out.is_dir():
        return None
    for d in sorted(out.glob(f"ir-{store}-*")):
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        e = m.get("embedding") or {}
        if (m.get("corpus_release") == corpus_ref and m.get("index_recipe_sha256") == rsha and m.get("builder") == BUILDER
                and all(e.get(k) == emb[k] for k in ("provider", "model", "mode")) and _same_build(m.get("build_sha256"))(d)):
            return d
    return None


def build(pack, graph, node, corpus, out, embedding="fake", parent=None, embedder=None, model_dim=None):
    """Returns the stdout result {status, release_id, build_sha256, index_recipe_sha256, chunks, embed_calls, embed_tokens, seconds,
    location, error}; raises IndexBuildError. embedder(texts) -> (vectors, calls, tokens) for the real mode (packrt.index.real);
    model_dim, when known, is the real model's vector size (otherwise it is taken from the first answer)."""
    t0 = time.monotonic()
    res = load_pack(Path(pack))
    if not res.ok:
        raise IndexBuildError("index_invalid", f"Pack을 불러올 수 없습니다: {res.errors[0]['message'] if res.errors else '알 수 없는 오류'}")
    doc = res.doc
    try:
        recipe = index_recipe_of(doc, graph, node)
    except RecipeError as e:
        raise IndexBuildError("index_invalid", str(e)) from None
    store = next(n for n in doc["graphs"][graph]["nodes"] if n["id"] == node)["params"]["store"]
    decl = (doc["stores"][store].get("index") or {}).get("embedding") or {}
    if recipe["chunker"] is not None:
        raise IndexBuildError("index_invalid", "chunker는 0.12.0에서 부를 수 없습니다(이름만 예약)")
    _, rows, corpus_ref = read_corpus(corpus)
    rsha = index_recipe_sha256(recipe)
    out = Path(out)
    if embedding == "fake":
        emb, embedder = {"provider": decl.get("provider"), "model": decl.get("model"), "dim": FAKE_DIM, "mode": "fake"}, fake_embedder
    else:
        if embedder is None:
            raise IndexBuildError("embedding_failed", "실제 임베딩에는 provider가 필요합니다")
        emb = {"provider": decl.get("provider"), "model": decl.get("model"), "dim": int(model_dim) if model_dim else None, "mode": "real"}
    found = _existing(out, store, corpus_ref, rsha, emb)
    if found is not None:   # the same inputs: nothing is cut or embedded again
        m = json.loads((found / "manifest.json").read_text(encoding="utf-8"))
        return {"status": "exists", "release_id": m["release_id"], "build_sha256": m["build_sha256"], "index_recipe_sha256": rsha,
                "chunks": m.get("chunks", 0), "embed_calls": 0, "embed_tokens": 0, "seconds": round(time.monotonic() - t0, 3),
                "location": str(found), "error": None}
    ps = pieces(rows, recipe)
    norm = rerank_norm(rows, recipe["rerank_norm"])
    try:
        vectors, calls, tokens = embedder([p["text"] for p in ps])
    except IndexBuildError:
        raise
    except Exception as e:  # noqa: BLE001 - a provider error: its type only, never a key or a body
        raise IndexBuildError("embedding_failed", f"임베딩 실패: {type(e).__name__}") from None
    if emb["dim"] is None and vectors and isinstance(vectors[0], list):
        emb["dim"] = len(vectors[0])   # the real model's size, from its first answer
    if len(vectors) != len(ps) or not emb["dim"] or any(not isinstance(v, list) or len(v) != emb["dim"] for v in vectors):
        raise IndexBuildError("embedding_failed", f"임베딩 결과 수 · 크기가 조각과 다릅니다(조각 {len(ps)} · 크기 {emb['dim']})")
    bsha = build_sha256(corpus_ref, rsha, emb)
    rid = release_id(store, bsha)
    result = {"status": None, "release_id": rid, "build_sha256": bsha, "index_recipe_sha256": rsha, "chunks": 0, "embed_calls": 0,
              "embed_tokens": 0, "seconds": 0.0, "location": str(out / rid), "error": None}
    if (out / rid).exists() and not _same_build(bsha)(out / rid):
        raise IndexBuildError("index_invalid", f"'{out / rid}'에 같은 이름의 다른 폴더가 있습니다")
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-index-", dir=out))
    try:
        (tmp / "chunks.jsonl").write_text("".join(line(p) for p in ps), encoding="utf-8")
        (tmp / "vectors.json").write_text(json.dumps({p["id"]: v for p, v in zip(ps, vectors)}, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {"kind": "index", "release_id": rid, "store": store, "backend": "flat-local", "embedding": emb, "corpus_release": corpus_ref,
                    "index_recipe": recipe, "index_recipe_sha256": rsha, "build_sha256": bsha, "builder": BUILDER, "parent_release": parent,
                    "chunks": len(ps), "documents": len(rows), "rerank_norm": norm,
                    "files": {f: hashlib.sha256((tmp / f).read_bytes()).hexdigest() for f in ("chunks.jsonl", "vectors.json")}}
        manifest["index_sha256"] = dir_hash(tmp)
        write_manifest(tmp, manifest)
        try:
            check_index(tmp, store)
        except ReleaseError as e:
            raise IndexBuildError("index_invalid", str(e)) from None
        open_mode(tmp)
        status = place(tmp, out, rid, _same_build(bsha))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if status is None:
        raise IndexBuildError("index_invalid", f"'{out / rid}'에 같은 이름의 다른 폴더가 있습니다")
    return {**result, "status": status, "chunks": len(ps), "embed_calls": calls, "embed_tokens": tokens,
            "seconds": round(time.monotonic() - t0, 3)}
