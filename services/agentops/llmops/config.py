"""Product settings that come only from outside the product (environment / launch options)."""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODES = {"fake": "가짜 provider", "real": "실제 provider"}
ADDRESS_NOTICE = "호출 주소를 따르지 않는 코드는 실제 모델을 부를 수 있습니다"
HOST = "127.0.0.1"
HOSTS = (HOST, "0.0.0.0")   # EV-VPS-01 CHG-V01: 0.0.0.0 only inside the VPS container (published on the host as 127.0.0.1, D-68)
AGENT_MODELS = ("gpt-4.1",)   # D-461 (7): the wheels' agent models (structure candidates · hypotheses) — priced in autocand.CHAT_USD_PER_1M


@dataclass(frozen=True)
class Settings:
    mode: str
    data_dir: Path
    port: int
    llm_base_url: str = ""  # real mode only: LLMOps' own LLM endpoint (LLMOPS_LLM_BASE_URL, outside the product), OpenAI format
    anthropic_base_url: str = ""  # real mode only: Anthropic messages endpoint base (LLMOPS_ANTHROPIC_BASE_URL, EV-005 CHG-29)
    gemini_base_url: str = ""  # real mode only: Gemini generateContent endpoint base (LLMOPS_GEMINI_BASE_URL, EV-005 CHG-29)
    case_limit_s: int = 30  # UA-11; tests construct Settings with a shorter limit (not a user-facing setting)
    fake_faults: dict = field(default_factory=dict)  # test harness only (LLMOPS_FAKE_FAULTS): fake-provider failures on purpose
    fake_pick: str = ""  # test harness only (LLMOPS_FAKE_PICK): JSON file answering the folder/file chooser (EV-003)
    trace: str = "off"  # EV-019 CHG-93: "postgres" writes every evaluation case to the shared trace (LLMOPS_TRACE); off keeps SQLite only
    pack_status_dir: Path | None = None    # EV-022 019f: the Pack session's status files (LLMOPS_PACK_STATUS_DIR); default <Pack repository>/status/approved-changes
    prompt_library: Path | None = None     # EV-022 E-S-11: the prompt library folder (LLMOPS_PROMPT_LIBRARY); default ../task10-pack/prompt-library
    deploy_status_dir: Path | None = None  # the deploy session's status files (LLMOPS_DEPLOY_STATUS_DIR); default ../llmopsagent-deploy/status/deployments
    ops_payload_root: Path | None = None   # EV-025 CHG-128: the deploy side's payload folder, read only (LLMOPS_OPS_PAYLOAD_ROOT)
    kg_signal_dir: Path | None = None      # D-560 (dev-2): the deploy side's graph signals kg-apply/status@1, read only (LLMOPS_KG_SIGNAL_DIR); unset = no graph-version rows · band
    kg_pack_repo: Path | None = None       # D-581 (dev-2): the Pack repository whose tags hold the graphs of the version table, read only (LLMOPS_KG_PACK_REPO); unset = no before · after
    openai_env_file: Path | None = None    # D-588 (dev-2) LLMOPS_OPENAI_ENV_FILE: a key file by path, its OPENAI_API_KEY line only; a key in the environment comes first
    kg_link: tuple | None = None           # D-588 (dev-2) LLMOPS_KG_LINK=<project>|<deployment id>|<its server>: each applied graph becomes the next version of that Project (kglink); unset = off
    field_gold_dir: Path | None = None     # D-444 #11 (D-413): the field-truth hand-over folder <record_key>.json, read only (LLMOPS_FIELD_GOLD_DIR); unset = the record · start document only
    fake_ops: str = ""  # test harness only (LLMOPS_FAKE_OPS): a recorded trace-rows file standing in for the logDB (EV-025)
    store: str = "sqlite"   # EV-029 CHG-130: "pg" keeps the records in the shared PostgreSQL llmops schema (LLMOPS_STORE); unset = SQLite
    host: str = HOST   # EV-VPS-01 CHG-V01: LLMOPS_HOST (default 127.0.0.1)
    pack_ci: object = None   # EV-VPS-01 CHG-V07: llmops.pack_ci.Config when LLMOPS_PACK_CI_REPO · _TOKEN_FILE · _API are set, else None (off)
    db_env_file: Path = REPO_ROOT / "db" / ".env"  # where the trace writer finds LLMOPSAGENT_TRACE_DSN (LLMOPS_DB_ENV_FILE); llmops itself reads only the LLMOPSAGENT_RUNTIME_DSN line (EV-020)
    auto_diag: bool = False   # EV-030 CHG-151: the automatic diagnosis of operational Runs runs in the server (on for the product —
    #                           load_settings; off for Settings made directly in tests unless a test asks for it)
    fake_judge: str = ""      # test harness only (LLMOPS_FAKE_JUDGE): JSON file of judge results standing in for an LLM judge (EV-030)
    # EV-035 CHG-187 (answer 045-01): the improvement wheel's real calls — launch settings only, never a screen or an API
    auto_budget_usd: float | None = None   # LLMOPS_AUTO_BUDGET_USD: one cap for every wheel while this server runs (real mode); unset = no paid step
    ai_tab_budget_usd: float | None = None   # EV-073 LLMOPS_AI_TAB_BUDGET_USD: the ✦ AI tab's cap while this server runs (real mode), apart from the wheels'; unset = no real call
    auto_hypothesis: str = "off"           # LLMOPS_AUTO_HYPOTHESIS: "agent" calls the hypothesis agent in real mode; off by default (R1)
    auto_structure: str = "off"            # D-462 A LLMOPS_AUTO_STRUCTURE: "agent" — the efficiency wheel's ④ asks the structure candidate agent (real mode); "agent-once" asks once and ends before any row (the first real step); off = the three rules
    auto_agent_model: str = "gpt-4.1"      # D-461 (7) LLMOPS_AUTO_AGENT_MODEL: the wheels' agent model, one of AGENT_MODELS (a priced OpenAI model)
    auto_review: str = "on"                # D-462 B LLMOPS_AUTO_REVIEW: the efficiency wheel's quality review before ⑥ (real mode asks Jev); "off" skips it
    auto_review_model: str = "jev-1.13.0"  # LLMOPS_AUTO_REVIEW_MODEL: the decision model the review asks (D-462 덧 2: Jev, decide@1 noul)
    diag_judge: str = "chat"               # D-568 B1 LLMOPS_DIAG_JUDGE: accuracy ① · ④ node judge — "chat" (the Project's chat judge, as before), "jev" (decide@1 · auto_review_model, one yes/no per expect line) or a node table 'node=jev,node=chat' (D-604; a node not in it = chat)
    typesafe_env_file: str = ""            # LLMOPS_TYPESAFE_ENV_FILE: the env file holding TYPESAFE_API_KEY — the gateway reads that one line only, masked, never printed or stored (D-463 덧 13)
    typesafe_base_url: str = ""            # LLMOPS_TYPESAFE_BASE_URL: the decision provider's address (real mode only, outside the product)
    auto_eff_after_runs: int | None = None    # D-470 ⓓ LLMOPS_AUTO_EFF_AFTER_RUNS: this many finished 운영 Runs of the 운영 revision since its last
    #                                           efficiency wheel opened open one (the set time's goals and suites); unset = off
    sealed_dir: Path | None = None          # D-477 (2) LLMOPS_SEALED_DIR: the sealed test set in use (its folder · read only) — with sealed_sha
    sealed_sha: str | None = None           # LLMOPS_SEALED_SHA: the set's sha256 as recorded; the files must add up to it (autosealed.verify_set)
    auto_apply: bool = False               # D-565 · D-560 덧 9 (dev-2) LLMOPS_AUTO_APPLY=on: a wheel's check-order version at ⑦ asks for its deploy itself; off = a person
    auto_grid_max: int = 400               # D-549 B LLMOPS_AUTO_GRID_MAX: combinations × cases a small option space may cost to be tried whole (autogrid.CASE_RUNS); 0 = off
    auto_rebuilds: int | None = None          # D-513 LLMOPS_AUTO_REBUILDS: a new wheel's iterations (1 ~ 5, kept on the wheel); unset = 3 (autocycle.DEFAULT_SETTINGS)
    auto_daily_usd: float | None = None       # D-470 ⓓ LLMOPS_AUTO_DAILY_USD: every wheel's spend since 00:00 (Asia/Seoul) — a paid step over it waits
    #                                           for the next day; the server cap (auto_budget_usd) stays as it is
    auto_index_real: bool = False          # LLMOPS_AUTO_INDEX_REAL=on: the wheel may build a real-embedding vector DB version (EV-051 CHG-W2-18, D-346); off by default
    auto_start_hypotheses: str = ""        # LLMOPS_AUTO_START_HYPOTHESES: a start-hypothesis document (JSON), used when its base revision matches
    fake_auto: str = ""                    # test harness only (LLMOPS_FAKE_AUTO): JSON file standing in for the wheel's model answers (EV-035)
    ops_judge: tuple = ()                  # LLMOPS_OPS_JUDGE (EV-062, Gate B D-405): Project ids whose operational Runs get a real judge on a real-mode server with a cap

    @property
    def payload_root(self) -> Path:
        """Raw values of trace events (redacted, content-addressed), next to the product data."""
        return self.data_dir / "payloads"

    @property
    def mode_label(self) -> str:
        return MODES[self.mode]

    def base_url(self, provider):
        """(address, variable name) of a provider's real-mode endpoint."""
        return {"openai": (self.llm_base_url, "LLMOPS_LLM_BASE_URL"), "anthropic": (self.anthropic_base_url, "LLMOPS_ANTHROPIC_BASE_URL"),
                "gemini": (self.gemini_base_url, "LLMOPS_GEMINI_BASE_URL")}[provider]


def load_settings(env=None, platform=None) -> Settings:
    env = os.environ if env is None else env
    mode = env.get("LLMOPS_LLM_MODE", "fake")
    if mode not in MODES:
        raise SystemExit(f"LLMOPS_LLM_MODE must be one of {sorted(MODES)}")
    if mode == "real" and (platform or sys.platform).startswith("linux") and env.get("LLMOPS_ALLOW_REAL_ON_LINUX") != "1":   # the VPS evaluates in fake mode only; this repository's compose sets the one switch (FROZEN-DIFF.md)
        raise SystemExit("이 서버(리눅스)에서는 실제 모드로 시작하지 않습니다 — VPS 평가는 가짜 모드만입니다(D-150). LLMOPS_LLM_MODE=fake로 시작하세요")
    data_dir = Path(env.get("LLMOPS_DATA_DIR") or REPO_ROOT / ".llmops-data")
    port = int(env.get("LLMOPS_PORT", "8765"))
    # test harness only: fake-provider failures on purpose, read only inside scripts/isolated-env runs
    faults = json.loads(env["LLMOPS_FAKE_FAULTS"]) if env.get("LLMOPS_FAKE_FAULTS") and env.get("ISOLATED_ENV") == "1" else {}
    pick = env.get("LLMOPS_FAKE_PICK", "") if env.get("ISOLATED_ENV") == "1" else ""
    fake_ops = env.get("LLMOPS_FAKE_OPS", "") if env.get("ISOLATED_ENV") == "1" else ""   # EV-025: test harness only
    fake_judge = env.get("LLMOPS_FAKE_JUDGE", "") if env.get("ISOLATED_ENV") == "1" else ""   # EV-030: test harness only
    # EV-030: the automatic diagnosis is on for the product; inside isolated-env (tests) it runs only on recorded rows
    # (LLMOPS_FAKE_OPS), so a test server never reads the real logDB by itself
    auto_diag = env.get("ISOLATED_ENV") != "1" or bool(fake_ops)
    trace = env.get("LLMOPS_TRACE", "off") or "off"
    if trace not in ("off", "postgres"):
        raise SystemExit("LLMOPS_TRACE must be off or postgres")
    store = env.get("LLMOPS_STORE", "")
    if store not in ("", "pg"):   # anything else is refused with the value as the reason (CHG-130); it is a name, not a secret
        raise SystemExit(f"LLMOPS_STORE는 비워 두거나 pg여야 합니다 — 받은 값 {store[:40]!r}")
    host = env.get("LLMOPS_HOST", "") or HOST
    if host not in HOSTS:   # a name, not a secret: shown so the operator sees what was refused
        raise SystemExit(f"LLMOPS_HOST는 비워 두거나 127.0.0.1 또는 0.0.0.0이어야 합니다 — 받은 값 {host[:40]!r}")
    from .pack_ci import config as pack_ci_config
    pack_ci = pack_ci_config(env)
    db_env = Path(env["LLMOPS_DB_ENV_FILE"]) if env.get("LLMOPS_DB_ENV_FILE") else REPO_ROOT / "db" / ".env"
    budget = env.get("LLMOPS_AUTO_BUDGET_USD", "")
    try:
        budget = float(budget) if budget else None
    except ValueError:
        raise SystemExit(f"LLMOPS_AUTO_BUDGET_USD는 달러 숫자입니다 — 받은 값 {budget[:20]!r}")
    if budget is not None and not 0 < budget <= 100:
        raise SystemExit("LLMOPS_AUTO_BUDGET_USD는 0보다 크고 100 이하입니다")
    ai_cap = env.get("LLMOPS_AI_TAB_BUDGET_USD", "")   # EV-073 CHG-AI-6 (D-447 Q-2): same shape as the wheels' cap
    try:
        ai_cap = float(ai_cap) if ai_cap else None
    except ValueError:
        raise SystemExit(f"LLMOPS_AI_TAB_BUDGET_USD는 달러 숫자입니다 — 받은 값 {ai_cap[:20]!r}")
    if ai_cap is not None and not 0 < ai_cap <= 100:
        raise SystemExit("LLMOPS_AI_TAB_BUDGET_USD는 0보다 크고 100 이하입니다")
    after = env.get("LLMOPS_AUTO_EFF_AFTER_RUNS", "")   # D-470 ⓓ (answer 067 Q-1 (가) · Q-2: off by default)
    try:
        after = int(after) if after else None
    except ValueError:
        raise SystemExit(f"LLMOPS_AUTO_EFF_AFTER_RUNS는 운영 Run 개수입니다 — 받은 값 {after[:20]!r}")
    if after is not None and not 1 <= after <= 200:
        raise SystemExit("LLMOPS_AUTO_EFF_AFTER_RUNS는 1 이상 200 이하입니다")
    rebuilds = env.get("LLMOPS_AUTO_REBUILDS", "")   # D-513: up to five iterations a wheel
    if rebuilds and not (rebuilds.isdigit() and 1 <= int(rebuilds) <= 5):
        raise SystemExit(f"LLMOPS_AUTO_REBUILDS는 반복 수 1 이상 5 이하입니다 — 받은 값 {rebuilds[:20]!r}")
    grid = env.get("LLMOPS_AUTO_GRID_MAX", "")   # D-549 B: the case-run cap of trying a small option space whole
    if grid and not grid.isdigit():
        raise SystemExit(f"LLMOPS_AUTO_GRID_MAX는 case-Run 수(0 이상 정수)입니다 — 받은 값 {grid[:20]!r}")
    daily = env.get("LLMOPS_AUTO_DAILY_USD", "")
    try:
        daily = float(daily) if daily else None
    except ValueError:
        raise SystemExit(f"LLMOPS_AUTO_DAILY_USD는 달러 숫자입니다 — 받은 값 {daily[:20]!r}")
    if daily is not None and not 0 < daily <= 100:
        raise SystemExit("LLMOPS_AUTO_DAILY_USD는 0보다 크고 100 이하입니다")
    sealed_dir, sealed_sha = env.get("LLMOPS_SEALED_DIR", ""), env.get("LLMOPS_SEALED_SHA", "")
    if bool(sealed_dir) != bool(sealed_sha):
        raise SystemExit("LLMOPS_SEALED_DIR와 LLMOPS_SEALED_SHA는 둘 다 주거나 둘 다 비웁니다")
    if sealed_sha and not re.fullmatch(r"[0-9a-f]{64}", sealed_sha):
        raise SystemExit("LLMOPS_SEALED_SHA는 sha256 16진수 64자입니다")
    hyp = env.get("LLMOPS_AUTO_HYPOTHESIS", "") or "off"
    if hyp not in ("off", "agent"):
        raise SystemExit("LLMOPS_AUTO_HYPOTHESIS는 비워 두거나 off · agent입니다")
    struct = env.get("LLMOPS_AUTO_STRUCTURE", "") or "off"
    if struct not in ("off", "agent", "agent-once"):
        raise SystemExit("LLMOPS_AUTO_STRUCTURE는 비워 두거나 off · agent · agent-once입니다")
    agent_model = env.get("LLMOPS_AUTO_AGENT_MODEL", "") or AGENT_MODELS[0]
    if agent_model not in AGENT_MODELS:
        raise SystemExit("LLMOPS_AUTO_AGENT_MODEL은 " + " · ".join(AGENT_MODELS) + "입니다")
    apply_ = env.get("LLMOPS_AUTO_APPLY", "") or "off"   # D-565 (dev-2 only): a check-order fix reaches the running Pack with no person
    if apply_ not in ("on", "off"):
        raise SystemExit("LLMOPS_AUTO_APPLY는 비워 두거나 on · off입니다")
    review = env.get("LLMOPS_AUTO_REVIEW", "") or "on"
    diag_judge = env.get("LLMOPS_DIAG_JUDGE", "") or "chat"   # D-568 B1
    if diag_judge not in ("chat", "jev") and not all(re.fullmatch(r"[A-Za-z0-9_.-]+=(chat|jev)", x) for x in diag_judge.split(",")):   # D-604: a node table
        raise SystemExit("LLMOPS_DIAG_JUDGE는 비워 두거나 chat · jev · 노드 표(write_report=jev,judge=chat)입니다")
    if review not in ("on", "off"):
        raise SystemExit("LLMOPS_AUTO_REVIEW는 비워 두거나 on · off입니다")
    fake_auto = env.get("LLMOPS_FAKE_AUTO", "") if env.get("ISOLATED_ENV") == "1" else ""   # EV-035: test harness only
    status = {k: Path(env[e]) for k, e in (("pack_status_dir", "LLMOPS_PACK_STATUS_DIR"), ("deploy_status_dir", "LLMOPS_DEPLOY_STATUS_DIR"), ("prompt_library", "LLMOPS_PROMPT_LIBRARY"),
                                         ("ops_payload_root", "LLMOPS_OPS_PAYLOAD_ROOT"), ("field_gold_dir", "LLMOPS_FIELD_GOLD_DIR"),
                                         ("kg_signal_dir", "LLMOPS_KG_SIGNAL_DIR"), ("kg_pack_repo", "LLMOPS_KG_PACK_REPO"), ("openai_env_file", "LLMOPS_OPENAI_ENV_FILE")) if env.get(e)}
    return Settings(mode=mode, data_dir=data_dir, port=port, llm_base_url=env.get("LLMOPS_LLM_BASE_URL", ""),
                    anthropic_base_url=env.get("LLMOPS_ANTHROPIC_BASE_URL", ""), gemini_base_url=env.get("LLMOPS_GEMINI_BASE_URL", ""),
                    fake_faults=faults, fake_pick=pick, fake_ops=fake_ops, trace=trace, store=store or "sqlite", host=host, pack_ci=pack_ci, db_env_file=db_env,
                    auto_diag=auto_diag, fake_judge=fake_judge, auto_budget_usd=budget, ai_tab_budget_usd=ai_cap, auto_eff_after_runs=after, auto_rebuilds=int(rebuilds) if rebuilds else None, **({"auto_grid_max": int(grid)} if grid else {}), auto_daily_usd=daily, sealed_dir=Path(sealed_dir) if sealed_dir else None, sealed_sha=sealed_sha or None, auto_hypothesis=hyp, auto_structure=struct, auto_agent_model=agent_model, auto_index_real=env.get("LLMOPS_AUTO_INDEX_REAL") == "on",
                    auto_start_hypotheses=env.get("LLMOPS_AUTO_START_HYPOTHESES", ""), fake_auto=fake_auto,
                    auto_review=review, auto_apply=apply_ == "on", diag_judge=diag_judge, auto_review_model=env.get("LLMOPS_AUTO_REVIEW_MODEL", "") or "jev-1.13.0",
                    typesafe_env_file=env.get("LLMOPS_TYPESAFE_ENV_FILE", ""), typesafe_base_url=env.get("LLMOPS_TYPESAFE_BASE_URL", ""),
                    ops_judge=tuple(x.strip() for x in env.get("LLMOPS_OPS_JUDGE", "").split(",") if x.strip()),
                    kg_link=tuple(env["LLMOPS_KG_LINK"].split("|", 2)) if env.get("LLMOPS_KG_LINK", "").count("|") == 2 else None, **status)
