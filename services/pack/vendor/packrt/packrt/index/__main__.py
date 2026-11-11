"""`python -m packrt.index build|corpus|load … --json` (EV-006 CHG-R08 · EV-008 CHG-P03, contract C · D-267 C-10): one JSON line on
stdout; exit 0 = built · exists · loaded · checked, 1 = failed (the JSON says why: error {code, message}), 2 = argument error
(argparse, no JSON). Nothing is written outside --out; a key is read from the environment variable named by --key-env — or, when
that is unset or empty, from the same name in the file PACKRT_ENV_FILE points at (EV-011, the runner's rule) — and never printed;
load writes only to the database the variable named by --dsn-env points at, and never prints it."""
import argparse
import json
import sys

from .corpus import ID_RE
from .errors import IndexBuildError

BUILD_KEYS = ("status", "release_id", "build_sha256", "index_recipe_sha256", "chunks", "embed_calls", "embed_tokens", "seconds",
              "location", "error")
CORPUS_KEYS = ("status", "release_id", "sha256", "documents", "location", "error")
LOAD_KEYS = ("status", "release_id", "schema", "table", "chunks", "sha256", "seconds", "error")


def _corpus_id(value):
    if not ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"corpus id는 {ID_RE.pattern} 모양이어야 합니다")
    return value


def parser():
    p = argparse.ArgumentParser(prog="python -m packrt.index", description="벡터 DB version · corpus release 만들기")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="retrieve@2 node의 index recipe로 벡터 DB version 만들기")
    b.add_argument("--pack", required=True)
    b.add_argument("--graph", required=True)
    b.add_argument("--node", required=True)
    b.add_argument("--corpus", required=True, help="corpus release 폴더(documents-1)")
    b.add_argument("--out", required=True)
    b.add_argument("--embedding", choices=("fake", "real"), required=True)
    b.add_argument("--key-env", help="실제 임베딩 API 키가 든 환경 변수 이름(--embedding real). "
                                    "환경에 없거나 빈 값이면 PACKRT_ENV_FILE 파일의 같은 이름 값")
    b.add_argument("--parent", help="앞 release_id(계보만, hash에 넣지 않음)")
    b.add_argument("--json", action="store_true")
    c = sub.add_parser("corpus", help="문서 JSON lines로 corpus release(documents-1) 만들기")
    c.add_argument("--documents", required=True)
    c.add_argument("--id", required=True, type=_corpus_id)
    c.add_argument("--parent")
    c.add_argument("--out", required=True)
    c.add_argument("--json", action="store_true")
    ld = sub.add_parser("load", help="벡터 DB version 하나를 공유 PostgreSQL에 적재(index_loader 계정, INSERT만)")
    ld.add_argument("--pack", required=True)
    ld.add_argument("--release", required=True, help="packrt.index build가 만든 version 폴더")
    ld.add_argument("--dsn-env", help="DSN이 든 환경 변수 이름(기본 LLMOPSAGENT_INDEX_LOADER_DSN)")
    ld.add_argument("--check", action="store_true", help="DB 없이 검사만")
    ld.add_argument("--json", action="store_true")
    return p


def run(args):
    if args.cmd == "load":
        from .load import load
        return load(args.pack, args.release, dsn_env=args.dsn_env, check=args.check), LOAD_KEYS
    if args.cmd == "corpus":
        from .corpus import make
        keys = CORPUS_KEYS
        try:
            return make(args.documents, args.id, args.out, parent=args.parent), keys
        except (IndexBuildError, OSError) as e:
            if not isinstance(e, IndexBuildError):
                e = IndexBuildError("index_invalid", f"쓰기 실패: {type(e).__name__}")
            return {"status": "failed", "release_id": args.id, "sha256": None, "documents": 0, "location": None,
                    "error": {"code": e.code, "message": str(e)}}, keys
    from .build import build
    keys = BUILD_KEYS
    try:
        if args.embedding == "real":
            from .real import real_embedder
            embedder, dim = real_embedder(args.pack, args.graph, args.node, args.key_env)
        else:
            embedder, dim = None, None
        return build(args.pack, args.graph, args.node, args.corpus, args.out, embedding=args.embedding, parent=args.parent,
                     embedder=embedder, model_dim=dim), keys
    except (IndexBuildError, OSError) as e:   # a disk error is reported like the others, as its type only
        if not isinstance(e, IndexBuildError):
            e = IndexBuildError("index_invalid", f"쓰기 실패: {type(e).__name__}")
        return {"status": "failed", "release_id": None, "build_sha256": None, "index_recipe_sha256": None, "chunks": 0, "embed_calls": 0,
                "embed_tokens": 0, "seconds": 0.0, "location": None, "error": {"code": e.code, "message": str(e)}}, keys


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.cmd == "build" and args.embedding == "fake" and args.key_env:
        p.error("--key-env는 --embedding real에서만 씁니다")   # an argument error: exit 2, no JSON
    if args.cmd == "build" and args.embedding == "real" and not args.key_env:
        p.error("--embedding real에는 --key-env(키가 든 환경 변수 이름)가 필요합니다")
    if args.cmd == "load" and args.check and args.dsn_env is not None:
        p.error("--dsn-env는 --check 없이 적재할 때만 씁니다")   # like --key-env with fake (answer 074)
    if args.cmd == "load" and args.dsn_env == "":
        p.error("--dsn-env에는 환경 변수 이름을 적습니다")
    result, keys = run(args)
    result = {k: result.get(k) for k in keys}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['status']} {result.get('release_id')}" + (f" — {result['error']['code']}: {result['error']['message']}" if result["error"] else ""))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
