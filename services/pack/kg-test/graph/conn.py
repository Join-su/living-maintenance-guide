"""그래프 DB 사본 연결 — 비밀번호는 env 파일(기본 ~/.c1a15-kgdb/neo4j.env · NEO4J_AUTH=사용자/비밀번호 한 줄)에서 메모리로만 읽음. 값 출력 0."""
import os
import sys
from pathlib import Path

from neo4j import GraphDatabase

KG = Path(__file__).resolve().parent.parent
if str(KG) not in sys.path:
    sys.path.insert(0, str(KG))

URI = os.environ.get("KGDB_URI", "bolt://127.0.0.1:27687")
ENV_FILE = Path(os.environ.get("KGDB_ENV_FILE", Path.home() / ".c1a15-kgdb/neo4j.env"))


def _auth():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("NEO4J_AUTH="):
            user, pw = line.split("=", 1)[1].split("/", 1)
            return user, pw
    raise SystemExit(f"NEO4J_AUTH 줄 없음: {ENV_FILE}")


def driver():
    return GraphDatabase.driver(URI, auth=_auth())
