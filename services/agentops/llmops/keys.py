"""LLMOps API keys per provider (B-06, EV-005 CHG-25). For each provider the environment variable is read first; a
temporary key entered in the settings screen lives only in this process's memory, overrides it, and disappears on
restart. Only the status ('temp' | 'env' | 'none') ever leaves this module; a value is used only as a request header."""
import os
import re
import threading
from pathlib import Path

# provider id -> short name (messages), card title, environment variables (first found wins), default model (D-1, D-3)
PROVIDERS = {
    "openai": {"name": "OpenAI", "title": "OpenAI", "env": ("LLMOPS_OPENAI_API_KEY", "LLMOPS_API_KEY"), "model": "gpt-4.1-mini"},
    "anthropic": {"name": "Anthropic", "title": "Anthropic · Claude", "env": ("LLMOPS_ANTHROPIC_API_KEY",), "model": "claude-sonnet-5-5"},
    "gemini": {"name": "Gemini", "title": "Google · Gemini", "env": ("LLMOPS_GEMINI_API_KEY",), "model": "gemini-2.5-flash"},
}
DEFAULT_PROVIDER = "openai"
ENV_NAMES = tuple(n for p in PROVIDERS.values() for n in p["env"])   # every LLMOps key variable (never given to Agents)
STATUS_LABEL = {"temp": "임시 키 사용 중 (값 숨김)", "env": "환경변수 키 있음 (값 숨김)", "none": "없음"}
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def project_llm(scoring):
    """(provider, model) a Project's LLM calls use: its own choice, else OpenAI and its default model (D-5)."""
    sc = scoring or {}
    provider = sc.get("llm_provider") if sc.get("llm_provider") in PROVIDERS else DEFAULT_PROVIDER
    return provider, sc.get("llm_model") or PROVIDERS[provider]["model"]


def agent_llm(model, scoring):
    """EV-079 (D-461 (7) · D-462 덧 3): (provider, model) the improvement agents call — the launch setting LLMOPS_AUTO_AGENT_MODEL
    (an OpenAI model, checked at start) when given, else the Project's own (project_llm)."""
    return ("openai", model) if model else project_llm(scoring)


FILE_KEY = "OPENAI_API_KEY"   # D-588: the one line of LLMOPS_OPENAI_ENV_FILE


def env_line(env_file, name):
    """One variable's value from a key file (`export` and quotes as a .env writes them) — that line only. Never printed or stored; None
    when there is none."""
    try:
        for line in Path(env_file).read_text(encoding="utf-8").split("\n"):   # not splitlines: a stray control character stays in
            k, eq, v = line.strip(" \t\r").partition("=")                     # the value and is refused, not cut silently
            if eq and k.strip().removeprefix("export ").strip() == name:
                return v.strip().strip('"').strip("'") or None
    except (OSError, TypeError, ValueError):
        return None
    return None


class Keys:
    def __init__(self, env=None, key_file=None):
        self._env = os.environ if env is None else env
        self._temp = {}
        self._lock = threading.Lock()
        self._key_file = key_file   # D-588: a path only; the value stays in this process's memory for the call

    def _env_value(self, provider):
        v = next((self._env[n] for n in PROVIDERS[provider]["env"] if self._env.get(n)), None)
        return v or (env_line(self._key_file, FILE_KEY) if self._key_file and provider == DEFAULT_PROVIDER else None)

    def status(self, provider=DEFAULT_PROVIDER):
        with self._lock:
            if self._temp.get(provider):
                return "temp"
        return "env" if self._env_value(provider) else "none"

    def set_temp(self, value, provider=DEFAULT_PROVIDER):
        value = (value or "").strip()
        if not value:
            raise ValueError("임시 키가 비어 있습니다")
        with self._lock:
            self._temp[provider] = value

    def clear_temp(self, provider=DEFAULT_PROVIDER):
        with self._lock:
            self._temp.pop(provider, None)

    def value(self, provider=DEFAULT_PROVIDER):
        """The key to send to that provider (temp > env), or None. Never log or return this to the UI."""
        with self._lock:
            if self._temp.get(provider):
                return self._temp[provider]
        return self._env_value(provider)

    def known_secrets(self):
        """Exact values to redact before anything is recorded: every provider's keys, including values that are set but
        not in use right now (LLMOPS_API_KEY behind LLMOPS_OPENAI_API_KEY, an env key behind a temporary key)."""
        with self._lock:
            vals = list(self._temp.values())
        vals += [self._env.get(n) for n in ENV_NAMES] + [self._env_value(DEFAULT_PROVIDER)]
        with self._lock:
            vals += list(getattr(self, "extra_secrets", ()))   # values LLMOps hands to loaded code itself (EV-020: the runtime DSN)
        return [v for v in dict.fromkeys(vals) if v]

    def add_secret(self, value):
        """Mask a key LLMOps read itself — the quality review's TYPESAFE_API_KEY (D-463 덧 13) — from now on."""
        if not value:
            return
        with self._lock:
            have = list(getattr(self, "extra_secrets", ()))
            self.extra_secrets = list(dict.fromkeys(have + [value]))

    def add_dsn(self, dsn):
        """Mask a DSN LLMOps hands to loaded code — the whole value and its password part — from now on (EV-020 CHG-97)."""
        if not dsn:
            return
        from urllib.parse import urlsplit
        try:
            password = urlsplit(dsn).password
        except ValueError:
            password = None
        with self._lock:
            have = list(getattr(self, "extra_secrets", ()))
            self.extra_secrets = list(dict.fromkeys(have + [v for v in (dsn, password) if v]))
