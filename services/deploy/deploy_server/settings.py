"""서버 설정 파일(config/*.yaml) 읽기. 상대 경로는 설정 파일이 있는 폴더 기준. 비밀 값은 넣지 않는다(환경 변수 이름만)."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .execution import ExecutionError, check_execution

REVIEW_POLICIES = {"auto_approve"}            # 운영 1단계(Q1)
PROVIDERS = {"fake", "http"}                   # http = 실제 provider(packrt HttpProvider). 실행은 승인 뒤에만
REGISTRATION_MODES = {"none", "llmops_db", "llmops_pg"}   # llmops_db = llmops.db(SQLite) 읽기 전용, llmops_pg = PG llmops.deployment(runtime_reader, ES-E)


class SettingsError(ValueError):
    pass


@dataclass
class Settings:
    deployment_id: str
    retention_class: str
    pack_repo: Path
    pack_ref: str
    pack_subdir: str
    releases_subdir: str
    expect_revision: str | None
    execution: dict
    data_dir: Path
    packs_dir: Path
    review_policy: str = "auto_approve"
    provider: str = "fake"
    registration: dict = field(default_factory=lambda: {"mode": "none"})
    trace: dict = field(default_factory=dict)   # 공유 기록기: dsn_env(환경 변수 이름), env_file(그 이름을 찾을 파일), payload_root
    runtime_db: dict = field(default_factory=dict)   # PostgreSQL backend 실행 설정일 때: env_file(LLMOPSAGENT_RUNTIME_DSN 한 줄을 찾을 파일)
    uses_postgres: bool = False                      # execution store 중 PostgreSQL backend가 있음(서버가 계산)
    outcome_by_node: dict = field(default_factory=dict)
    grounding_keys: list = field(default_factory=list)
    secret_env_names: list = field(default_factory=list)
    release_roots_extra: list = field(default_factory=list)   # R12 R-2: Pack 사본 releases 다음에 찾는 곳(쓰기 폴더, 선택)
    embedding: dict | None = None                              # D-403 덧: provider fake 배포에서 질의 임베딩만 실제(packrt HttpProvider) — {provider: http, env_file, base_url}
    field_gold: dict | None = None                             # D-470 덧 5: 체크 기록 → 현장 정답 일감(설정 있을 때만) — {out_dir, cli, history_corpus, sha256: {field_gold, make_gold, history_corpus}}


def _path(base, value):
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def load_settings(path, overrides=None):
    path = Path(path).resolve()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    doc.update(overrides or {})
    base = path.parent
    try:
        pack, ex = doc["pack"], doc["execution"]
        s = Settings(
            deployment_id=doc["deployment_id"], retention_class=doc.get("retention_class", "standard"),
            pack_repo=_path(base, pack["repo"]), pack_ref=str(pack["ref"]), pack_subdir=pack.get("pack_subdir", "pack"),
            releases_subdir=pack.get("releases_subdir", "releases"), expect_revision=pack.get("expect_revision"),
            execution=ex, data_dir=_path(base, doc["data_dir"]), packs_dir=_path(base, doc.get("packs_dir", "../.packs")),
            review_policy=doc.get("review_policy", "auto_approve"), provider=doc.get("provider", "fake"),
            registration=doc.get("registration") or {"mode": "none"}, outcome_by_node=doc.get("outcome_by_node") or {},
            grounding_keys=list(doc.get("grounding_keys") or []), secret_env_names=list(doc.get("secret_env_names") or []),
            release_roots_extra=[_path(base, r) for r in (doc.get("release_roots_extra") or [])])
    except KeyError as e:
        raise SettingsError(f"설정 {path.name}에 {e} 항목이 없습니다")
    if "packrt_path" in doc:
        raise SettingsError("packrt_path는 없어졌습니다(015 뒤 packrt는 설치된 패키지로 import)")
    tr = dict(doc.get("trace") or {})
    s.trace = {"dsn_env": tr.get("dsn_env", "LLMOPSAGENT_TRACE_DSN"), "env_file": _path(base, tr.get("env_file")),
               "payload_root": _path(base, tr.get("payload_root")) or s.data_dir / "trace-payloads"}
    rdb = dict(doc.get("runtime_db") or {})
    if set(rdb) - {"env_file"}:
        raise SettingsError(f"runtime_db는 env_file만 받습니다(접속 이름은 LLMOPSAGENT_RUNTIME_DSN으로 고정): {sorted(set(rdb) - {'env_file'})}")
    s.runtime_db = {"env_file": _path(base, rdb.get("env_file"))}
    if s.review_policy not in REVIEW_POLICIES:
        raise SettingsError(f"운영 1단계 검토 정책은 auto_approve만 됩니다(받은 것: {s.review_policy})")
    if s.provider not in PROVIDERS:
        raise SettingsError(f"provider는 {sorted(PROVIDERS)} 중 하나입니다(받은 것: {s.provider})")
    if s.registration.get("mode") not in REGISTRATION_MODES:
        raise SettingsError(f"registration.mode는 {sorted(REGISTRATION_MODES)} 중 하나입니다")
    if s.retention_class not in ("standard", "dev"):
        raise SettingsError("retention_class는 standard 또는 dev입니다")
    if s.deployment_id.endswith("@dev-local") and s.retention_class != "dev":
        raise SettingsError("*@dev-local 배포의 원문 보존은 dev(7일)입니다(Q-D1)")
    if s.registration.get("mode") == "llmops_db":
        s.registration["path"] = str(_path(base, s.registration["path"]))
    if s.registration.get("mode") == "llmops_pg" and set(s.registration) != {"mode"}:   # 접속 이름은 LLMOPSAGENT_RUNTIME_DSN으로 고정(runtime_db.env_file)
        raise SettingsError(f"registration mode llmops_pg는 mode만 받습니다(접속은 LLMOPSAGENT_RUNTIME_DSN · runtime_db.env_file): {sorted(set(s.registration) - {'mode'})}")
    emb = doc.get("embedding")
    if emb is not None:   # D-403 덧(183-01 (가)): 키는 파일 경로로만(packrt PACKRT_ENV_FILE) · 주소는 설정(packrt S6: 주소를 코드에 두지 않음)
        if not isinstance(emb, dict) or set(emb) != {"provider", "env_file", "base_url"}:
            raise SettingsError("embedding은 {provider, env_file, base_url} 셋을 받습니다")
        if emb["provider"] != "http":
            raise SettingsError(f"embedding.provider는 http만 됩니다(받은 것: {emb['provider']})")
        if s.provider != "fake":
            raise SettingsError("embedding 칸은 provider: fake 배포에서만 씁니다(provider http면 임베딩도 이미 실제)")
        env_file = _path(base, emb["env_file"])   # 파일 있음 · 키 이름은 서버 시작 때(app._attach_embedder) — deploy-tools 묶기 대조는 키 없이 설정을 읽음(V-40, D-416 ③)
        url = str(emb["base_url"])
        if not (url.startswith("https://") or url.startswith("http://127.0.0.1")):
            raise SettingsError("embedding.base_url은 https:// 또는 루프백 http://127.0.0.1 주소입니다")
        s.embedding = {"provider": "http", "env_file": env_file, "base_url": url.rstrip("/")}
    fg = doc.get("field_gold")
    if fg is not None:   # D-470 덧 5 · 6(FIELD-GOLD-E1): 서버는 일감 파일만 씀 · CLI는 일꾼(scripts/field-gold.py)이 sha 대조 뒤 부름
        if not isinstance(fg, dict) or set(fg) != {"out_dir", "cli", "history_corpus", "sha256"}:
            raise SettingsError("field_gold는 {out_dir, cli, history_corpus, sha256} 넷을 받습니다")
        sha = fg["sha256"]
        if not isinstance(sha, dict) or set(sha) != {"field_gold", "make_gold", "history_corpus"} or \
                not all(isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in sha.values()):
            raise SettingsError("field_gold.sha256은 {field_gold, make_gold, history_corpus} 세 sha256(소문자 64자)입니다")
        s.field_gold = {"out_dir": _path(base, fg["out_dir"]), "cli": _path(base, fg["cli"]),
                        "history_corpus": _path(base, fg["history_corpus"]), "sha256": dict(sha)}   # eval 284 Q1: 생성 이력 H- 결과는 corpus에서 읽음 → sha 고정
    try:
        s.uses_postgres = check_execution(s.execution, provider=s.provider, embedding=s.embedding)
    except ExecutionError as e:
        raise SettingsError(f"설정 {path.name}: {e}")
    return s
