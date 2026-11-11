"""Model provider calls (spec 7). OpenAI-compatible HTTP only in P0 (chat with optional JSON-schema output, embeddings).
The address comes only from the provider's base_url_env in models.yaml (LLMOps fake mode points it at its local fake provider);
the key from the provider's env name, read from the process environment or the file named by PACKRT_ENV_FILE.
decide@1 (EV-001 CHG-103) calls a Jev-format decision provider (a models.yaml provider listing `decision` models) at
POST <base>/v1/systemone; chat and embeddings stay OpenAI-format only. On every path an HTTP error body is masked before it is
cut (EV-001 CHG-107), and a key holding a control character stops the call before any request is built (CHG-108).
EV-012 (GAP-004): a provider entry that names a key env, whose key is empty in the environment and in PACKRT_ENV_FILE, stops
before any request when its address is not loopback — loopback fake providers and providers without a key name go on as before.
EV-013 (D-396 (4) · D-404): a provider entry may name a second key, `env_fallback` (a name; the value is read like the first key).
When the first key gets HTTP 401 · 403 · 429 on the chat · embed path and the second key has a value, the same request goes once
more with the second key, and the rest of that Run uses the second key first; `start_run()` puts a Run back on the first key (one
Runtime runs one Run at a time). Other errors, an empty first key (GAP-004) and decide never switch. Only the key slot (1 · 2) is
recorded (`last_key_slot()`), for providers that name a second key."""
import ipaddress
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DECIDE_PATH = "/v1/systemone"
SWITCH_STATUS = (401, 403, 429)    # EV-013 CHG-F02: the provider's auth · permission · balance or limit answer — try the second key
DECIDE_RETRY_STATUS = (429, 529)   # rate limited / overloaded: wait longer and try again (spec 7.1)
DECIDE_RETRY_WAITS = (1, 2)        # seconds before the 2nd and 3rd attempt
_sleep = time.sleep                # replaced in tests


class ProviderError(RuntimeError):
    pass


# GAP-003 (packrt instruction 014, the same shape as tracewriter.redact and llmops 0854e72): a provider's own half-masked key in
# its error text ('Incorrect API key provided: sk-proj-****…abcd') — or what is left of it after the key mask — becomes 'sk-***'.
# The whole key is masked first, so a key-free body without that shape is the same text as before.
PROVIDER_MASKED_RE = re.compile(r"(?:\bsk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9]*|\[가림\])\*{3,}[A-Za-z0-9_-]*")


def load_env_file(path):
    """KEY=VALUE lines of a .env file (values are only used as request headers, never printed)."""
    out = {}
    if not path:
        return out
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def _error_body(e, key, limit=300, also=()):
    """The body of an HTTP error for the error text: the whole body is decoded and the key masked first, then cut, so no part
    of the key is left where the text is cut (CHG-103 decide, CHG-107 chat · embed). `also`: the other key of a provider that
    names two (EV-013), masked the same way."""
    body = e.read().decode("utf-8", "replace")
    for k in sorted((k for k in (key, *also) if k), key=len, reverse=True):   # the longer key first: no tail of one left by the other
        body = body.replace(k, "[가림]")
    return PROVIDER_MASKED_RE.sub("sk-***", body)[:limit]


def _checked_key(provider, p, key):
    """A key with a control character (0-31, 127) would end up whole in the HTTP library's header error, so the call stops
    here with only the env name in the text (CHG-108); the value is never changed or shown."""
    if key and any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ProviderError(f"provider '{provider}'의 키 환경변수 {p.get('env')} 값에 줄바꿈 · 제어 문자가 있습니다 — 값을 고쳐 주세요")
    return key


def _loopback(base):
    """True when the address's host is localhost (any case) or a loopback IP (127.0.0.0/8, ::1). A host that cannot be read is
    not loopback, so the check below stays closed for it."""
    try:
        host = urllib.parse.urlsplit(base).hostname   # lower-cased, IPv6 brackets removed
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _required_key(provider, p, key, base):
    """EV-012 CHG-G01 · G02: a key name with no key value, for an address that is not loopback, stops the call before any request
    is built — only the env name is in the text (no address, file path or value)."""
    if p.get("env") and not key and not _loopback(base):
        raise ProviderError(f"provider '{provider}'의 키 환경변수 {p.get('env')}가 환경에도 PACKRT_ENV_FILE 파일에도 없습니다 — "
                            "루프백 밖 주소로는 키 없이 요청하지 않습니다")
    return key


class HttpProvider:
    def __init__(self, models, env=None):
        self.models = (models or {}).get("providers") or {}
        self.env = dict(os.environ if env is None else env)
        self.env_file = load_env_file(self.env.get("PACKRT_ENV_FILE"))
        self.usage = {"tokens_in": 0, "tokens_out": 0}
        self._last = threading.local()   # the raw response of the last call, per thread (parallel nodes call at the same time)
        self._slot = 1                   # EV-013: the Run's key slot — 2 once the first key was refused in this Run
        self._slot_lock = threading.Lock()   # held only to read or change the slot, never while a request is out (N2-2)

    def last_response(self):
        """The provider's raw JSON response of the last call made in this thread (recorded as provider_raw, trace contract 4)."""
        return getattr(self._last, "value", None)

    def start_run(self):
        """EV-013 Q-5 (가): a Run starts on the first key again (engine.run calls this; one Runtime runs one Run at a time)."""
        with self._slot_lock:
            self._slot = 1

    def last_key_slot(self):
        """The key slot (1 · 2) of the last request this thread sent, for a provider that names a second key; else None."""
        return getattr(self._last, "slot", None)

    def _fallback(self, provider):
        """(name, value) of the provider's second key (`env_fallback`), read like the first; (None, None) when none is named."""
        name = (self.models.get(provider) or {}).get("env_fallback")
        if not isinstance(name, str) or not name:
            return None, None
        return name, self.env.get(name) or self.env_file.get(name)

    def _conf(self, provider):
        p = self.models.get(provider)
        if not p:
            raise ProviderError(f"models.yaml에 provider '{provider}'가 없습니다")
        if provider != "openai":
            raise ProviderError(f"P0 runtime은 OpenAI 형식 provider만 부릅니다(받은 것: {provider})")
        # no address is built into the runtime (repository rule S6): it always comes from the environment
        base = self.env.get(p.get("base_url_env", "")) or self.env_file.get(p.get("base_url_env", ""))
        if not base:
            raise ProviderError(f"provider '{provider}'의 주소 환경변수 {p.get('base_url_env')}가 없습니다 — 실행 환경에 주소를 두세요")
        key = self.env.get(p.get("env", "")) or self.env_file.get(p.get("env", ""))
        return base.rstrip("/"), _required_key(provider, p, _checked_key(provider, p, key), base)

    def _post(self, provider, path, body, timeout=60):
        self._last.slot = None   # first: a call that stops before any request (GAP-004, a bad key) records no slot
        base, key = self._conf(provider)
        fb_name, key2 = self._fallback(provider)

        def send(k):
            headers = {"Content-Type": "application/json"}
            if k:
                headers["Authorization"] = f"Bearer {k}"
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())

        if fb_name is None:   # one key: the request and the error text of 0.12.2, byte for byte
            try:
                out = send(key)
            except urllib.error.HTTPError as e:
                raise ProviderError(f"{provider} {path} HTTP {e.code}: {_error_body(e, key)}")
        else:
            with self._slot_lock:
                slot = self._slot if key2 else 1
            self._last.slot = slot   # the slot of the request about to go, kept when it fails too (E5-N1)
            if slot == 2:   # this Run already moved to the second key: it alone, no way back
                try:
                    out = send(key2)
                except urllib.error.HTTPError as e:
                    raise ProviderError(f"{provider} {path} HTTP {e.code}(키 자리 2): {_error_body(e, key2, also=(key,))}")
            else:
                try:
                    out = send(key)
                except urllib.error.HTTPError as e:
                    if e.code not in SWITCH_STATUS or not key2:
                        raise ProviderError(f"{provider} {path} HTTP {e.code}: {_error_body(e, key, also=(key2,))}")
                    first = f"{provider} {path} HTTP {e.code}(키 자리 1)"
                    if any(ord(c) < 32 or ord(c) == 127 for c in key2):   # Q-7: a control character would carry the value into an error
                        raise ProviderError(f"{first}: {_error_body(e, key, also=(key2,))} → 키 자리 2 값에 줄바꿈 · 제어 문자가 있어 보내지 않음")
                    e.close()
                    with self._slot_lock:
                        self._slot = 2
                    self._last.slot = 2
                    try:
                        out = send(key2)
                    except urllib.error.HTTPError as e2:
                        raise ProviderError(f"{first} → 키 자리 2 HTTP {e2.code}: {_error_body(e2, key2, also=(key,))}")
        self._last.value = out
        u = out.get("usage") or {}
        self.usage["tokens_in"] += u.get("prompt_tokens", 0); self.usage["tokens_out"] += u.get("completion_tokens", 0)
        return out

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, *, strict=False, seed=None,
             reasoning_effort=None):
        """strict · seed (EV-002 CHG-L04) are keyword-only and only passed by a node that turns them on; without them the body is
        the 0.10.0 body byte for byte. In strict mode the schema goes without its root $schema · $id (a copy — the Pack's dict
        is not changed); the provider does not promise the same answer for the same seed. 0.14.0 (D-475): reasoning_effort is sent
        only for a model models.yaml lists under the provider's `reasoning`, and such a model gets max_tokens as
        max_completion_tokens; temperature is sent as the Pack says (the model may refuse it)."""
        listed = model in ((((self.models.get(provider) or {}).get("models") or {}).get("reasoning")) or [])
        body = {"model": model, "messages": messages}
        if schema is not None:
            if strict:
                schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": bool(strict)}}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_completion_tokens" if listed else "max_tokens"] = max_tokens
        if seed is not None:
            body["seed"] = seed
        if reasoning_effort is not None and listed:
            body["reasoning_effort"] = reasoning_effort
        out = self._post(provider, "/chat/completions", body)
        return out["choices"][0]["message"]["content"], out.get("usage") or {}

    def _decision_conf(self, provider):
        p = self.models.get(provider)
        if not p:
            raise ProviderError(f"models.yaml에 provider '{provider}'가 없습니다")
        if not ((p.get("models") or {}).get("decision")):
            raise ProviderError(f"provider '{provider}'에는 decision 모델이 없습니다(decide는 Jev 형식 provider만 부릅니다)")
        base = self.env.get(p.get("base_url_env", "")) or self.env_file.get(p.get("base_url_env", ""))
        if not base:
            raise ProviderError(f"provider '{provider}'의 주소 환경변수 {p.get('base_url_env')}가 없습니다 — 실행 환경에 주소를 두세요")
        key = self.env.get(p.get("env", "")) or self.env_file.get(p.get("env", ""))
        return base.rstrip("/"), _required_key(provider, p, _checked_key(provider, p, key), base)

    def decide(self, provider, model, state, questions):
        """Jev decision call (spec 7.1): {"model", "state", "questions"} -> (raw answer, usage{input_tokens, output_tokens}).
        429 and 529 are tried again after DECIDE_RETRY_WAITS; other errors end the call. The key is only a request header."""
        base, key = self._decision_conf(provider)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = json.dumps({"model": model, "state": state, "questions": questions}).encode()
        for attempt in range(len(DECIDE_RETRY_WAITS) + 1):
            req = urllib.request.Request(base + DECIDE_PATH, data=data, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                body = _error_body(e, key)
                if e.code in DECIDE_RETRY_STATUS and attempt < len(DECIDE_RETRY_WAITS):
                    _sleep(DECIDE_RETRY_WAITS[attempt])
                    continue
                raise ProviderError(f"{provider} {DECIDE_PATH} HTTP {e.code}: {body}")
        self._last.value = out
        u = out.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
        self.usage["tokens_in"] += usage["input_tokens"]; self.usage["tokens_out"] += usage["output_tokens"]
        return out, usage

    def embed(self, provider, model, texts):
        out = self._post(provider, "/embeddings", {"model": model, "input": list(texts)})
        return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]
