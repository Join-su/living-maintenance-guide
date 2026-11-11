"""설정 `execution`(실행 설정의 내용) 검사와 PostgreSQL backend 접속 정보(지시 006b).

- 형식: llmops-lite pack-resource-contract v1.0 3절(task10-pack `pack/execution/postgres-fake.yaml`과 같은 모양), packrt 0.8.0(EV-020 CHG-96)
  store = {backend, release} + PostgreSQL backend만 `connection: {env: LLMOPSAGENT_RUNTIME_DSN}` + vector만 `embedding: {mode, key_env}`
- 실제 읽기·release 대조·계정 확인(runtime_reader)은 packrt가 Runtime을 만들 때 한다. 여기서는 서버가 받기 전에 모양과 비밀 값 없음만 본다
- 접속 정보: 서버 프로세스 환경에는 DB 접속 정보 중 LLMOPSAGENT_RUNTIME_DSN(runtime_reader) 하나만 둔다. 값은 출력·기록하지 않고 가림 목록에만 넣는다"""
import os
import re
from urllib.parse import unquote, urlsplit

RUNTIME_DSN_ENV = "LLMOPSAGENT_RUNTIME_DSN"   # packrt가 받는 유일한 실행용 접속 이름(CHG-96)
BACKENDS = {"flat-local": "vector", "pgvector": "vector", "sqlite-file": "table", "postgres-table": "table"}
PG_BACKENDS = {"pgvector", "postgres-table"}
EMBEDDING_MODES = {"real", "fake"}
# 서버 프로세스에서 뺄 DB 비밀 이름(llmops-lite-v2 db/.env의 모양). 실행용 DSN과 trace 기록기 DSN만 남긴다
DB_SECRET_RE = re.compile(r"^(?:[A-Z0-9_]+_DSN|[A-Z0-9_]*PASSWORD)$")
SECRET_VALUE_RE = re.compile(r"(?i)(?:postgres(?:ql)?://|password\s*=)")


class ExecutionError(ValueError):
    pass


def _strings(value, where):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{where}[{i}]")
    elif isinstance(value, str):
        yield where, value


def check_execution(execution, *, provider="fake", embedding=None):
    """설정 execution을 검사한다. 돌려줌: PostgreSQL backend를 쓰는지(bool). 틀리면 ExecutionError."""
    if not isinstance(execution, dict) or not isinstance(execution.get("entrypoint"), str) or not execution["entrypoint"]:
        raise ExecutionError("execution.entrypoint가 없습니다")
    extra = set(execution) - {"entrypoint", "stores", "models"}
    if extra:
        raise ExecutionError(f"execution에 모르는 항목이 있습니다: {sorted(extra)}(environment·release_roots는 서버가 씀)")
    for where, s in _strings(execution, "execution"):
        if SECRET_VALUE_RE.search(s):
            raise ExecutionError(f"{where}에 접속 정보 값이 있습니다. 설정에는 환경 변수 이름만 적습니다")
    stores = execution.get("stores") or {}
    if not isinstance(stores, dict):
        raise ExecutionError("execution.stores는 mapping입니다")
    pg = False
    for name, b in stores.items():
        if not isinstance(b, dict):
            raise ExecutionError(f"store '{name}'은 mapping입니다")
        backend, release = b.get("backend"), b.get("release")
        if backend not in BACKENDS:
            raise ExecutionError(f"store '{name}'의 backend '{backend}'를 받지 않습니다(받는 것: {sorted(BACKENDS)})")
        if not isinstance(release, str) or not release:
            raise ExecutionError(f"store '{name}'에 release가 없습니다")
        allowed = {"backend", "release"} | ({"connection"} if backend in PG_BACKENDS else set()) | ({"embedding", "releases"} if BACKENDS[backend] == "vector" else set())
        if set(b) - allowed:
            raise ExecutionError(f"store '{name}'({backend})에 받지 않는 항목이 있습니다: {sorted(set(b) - allowed)}")
        extra = b.get("releases")   # R12(지시 046 R-1, D-267 C-1): retrieve@2 기준 release 말고 고를 후보 version 목록 — packrt _pick_index가 고름
        if "releases" in b and (not isinstance(extra, list) or not extra or not all(isinstance(r, str) and r for r in extra)):
            raise ExecutionError(f"store '{name}'의 releases는 release 번호(문자열) 목록이어야 합니다(비어 있지 않음)")
        if backend in PG_BACKENDS:
            pg = True
            conn = b.get("connection")
            if not isinstance(conn, dict) or set(conn) != {"env"}:
                raise ExecutionError(f"store '{name}'({backend})에는 connection: {{env: {RUNTIME_DSN_ENV}}}가 필요합니다")
            if conn["env"] != RUNTIME_DSN_ENV:
                raise ExecutionError(f"실행용 접속 정보는 {RUNTIME_DSN_ENV}만 씁니다(store '{name}': {conn['env']})")
        emb = b.get("embedding")
        if emb is not None or backend == "pgvector":
            emb = emb or {}
            if not isinstance(emb, dict) or set(emb) - {"mode", "key_env"}:
                raise ExecutionError(f"store '{name}'의 embedding은 {{mode, key_env}}만 받습니다")
            mode = emb.get("mode", "real")
            if mode not in EMBEDDING_MODES:
                raise ExecutionError(f"store '{name}'의 embedding.mode는 real 또는 fake입니다(받은 것: {mode})")
            if mode == "real" and provider == "fake" and not embedding:   # real은 provider로 임베딩한다. 가짜 provider 배포는 fake 색인과만 짝
                raise ExecutionError(f"store '{name}': 가짜 provider 배포에서는 embedding.mode: fake만 씁니다(real은 실제 provider 승인 뒤 · "
                                     "또는 설정 embedding 칸으로 질의 임베딩만 실제, D-403 덧)")
            if mode == "real" and not emb.get("key_env"):
                raise ExecutionError("실제 임베딩에는 API 키 환경 변수 이름(embedding.key_env)이 필요합니다")
    return pg


def read_env_value(path, name):
    """env 파일에서 한 이름의 값만 읽는다(다른 줄은 보지 않음). 없으면 None."""
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() == name:
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                return v or None
    return None


def runtime_dsn(env_file):
    """실행용 DSN: 환경 변수 LLMOPSAGENT_RUNTIME_DSN, 없으면 env_file의 같은 이름 한 줄. 없으면 ExecutionError(값은 메시지에 넣지 않음)."""
    dsn = os.environ.get(RUNTIME_DSN_ENV) or read_env_value(env_file, RUNTIME_DSN_ENV)
    if not dsn:
        raise ExecutionError(f"접속 정보 환경 변수 '{RUNTIME_DSN_ENV}'이 없습니다(환경 변수 또는 runtime_db.env_file: {env_file})")
    return dsn


def dsn_secrets(dsn):
    """가림 목록에 넣을 값: DSN 전체와 비밀번호 부분(URL 형식·key=value 형식)."""
    out = [dsn]
    try:
        pw = urlsplit(dsn).password
    except ValueError:
        pw = None
    if pw:
        out += [pw, unquote(pw)]
    m = re.search(r"(?:^|\s)password\s*=\s*('(?:[^'\\]|\\.)*'|\S+)", dsn)
    if m:
        out.append(m.group(1).strip("'"))
    return list(dict.fromkeys(x for x in out if x))


def scrub_db_env(keep):
    """서버 프로세스 환경에서 DB 비밀(…_DSN, …PASSWORD)을 뺀다. keep에 있는 이름만 남긴다. 돌려줌: 뺀 이름(값 아님)."""
    gone = sorted(n for n in os.environ if DB_SECRET_RE.match(n) and n not in keep)
    for n in gone:
        del os.environ[n]
    return gone
