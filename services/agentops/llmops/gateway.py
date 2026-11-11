"""LLM gateway and local fake provider (B-06).

Mode comes only from outside the product (LLMOPS_LLM_MODE). In fake mode every LLMOps call is answered by the local fake
provider in this process (also served at /fake/v1 for Agent processes). In real mode a call goes to the Project's provider
in that provider's API format (EV-005 CHG-29): OpenAI chat at LLMOPS_LLM_BASE_URL, Anthropic messages at
LLMOPS_ANTHROPIC_BASE_URL, Gemini generateContent at LLMOPS_GEMINI_BASE_URL, with that provider's key as a request header;
this plan never points them at a real provider (tests use the loopback fake routes in the same formats).
Calls are logged with the key value and common key formats redacted (B-08)."""
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from packrt.ir import strict_schema_problems
from packrt.run.embedding import FAKE_DIM, fake_embedding as _packrt_fake_embedding

from . import fake_answers, fake_rule, guard
from .keys import DEFAULT_PROVIDER, PROVIDERS, env_line

FAKE_MODEL = "llmops-fake-1"
DOC_MARKERS = ("문서:", "계약 정보:")


def _tokens(text):
    return max(1, math.ceil(len(text) / 4))


JUDGE_SYSTEM = "LLMOps 채점: 답변이 질문과 기대 정답에 맞는지 0~1 점수와 한 줄 근거를 JSON {\"score\":…,\"why\":…}로 답하세요."
GEN_SYSTEM = "LLMOps 입력값 생성: 아래 Agent의 입력 필드 하나를 가진 JSON 객체 하나만 답하세요."
FAKE_JUDGE_WHY = "가짜 judge: 입력·답변 기준 고정 점수"


def fake_judge_score(text):
    """Fake mode's fixed score: deterministic per judged content (input, expected answer, answer), in 0.70–0.95."""
    return round(0.70 + (int(hashlib.sha256(text.encode()).hexdigest()[:4], 16) % 26) / 100, 2)


def fake_reply(messages):
    """Deterministic answer: JSON for the LLMOps judge and input-generation prompts; for Agent prompts the first context
    line after a known marker if present, else the system prompt's first sentence."""
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    if system.startswith(JUDGE_SYSTEM):
        return json.dumps({"score": fake_judge_score(user), "why": FAKE_JUDGE_WHY}, ensure_ascii=False)
    if system.startswith("LLMOps AI 제안:"):   # EV-073 CHG-AI-7: the ✦ AI tab (aisuggest.AI_NODE_SYSTEM)
        from .aisuggest import fake_answer
        return fake_answer(user)
    if system.startswith(GEN_SYSTEM):
        req = json.loads(user)
        topic = req.get("설명") or req.get("release") or "입력"
        return json.dumps({req["입력 필드"]: f"[가짜] {topic} 질문 {req['번호']}"}, ensure_ascii=False)
    for marker in DOC_MARKERS:
        if marker in user:
            lines = [l.strip() for l in user.split(marker, 1)[1].splitlines() if l.strip()]
            if lines and not lines[0].startswith("질문:"):
                return f"[가짜] {lines[0]}"
    first = re.split(r"(?<=[.!?。])\s|\n", system.strip(), maxsplit=1)[0] if system.strip() else "응답"
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:6]
    return f"[가짜] {first} ({digest})"


class FakeFault(Exception):
    pass


class FakeAnswerMissing(FakeFault):
    """A Run call of a revision in the demo answers file for a case or llm node the file does not have (EV-022 S-10): the
    fake provider refuses instead of making an answer up (served as an error like an injected fault)."""


class FakeSchemaError(ValueError):
    """A response_format schema the fake provider cannot satisfy (EV-002 CHG-05)."""


EMBED_DIM = 64
SCHEMA_KEYS = {"type", "properties", "required", "items", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "minItems", "maxItems", "minLength", "maxLength", "title", "description", "$schema", "$id", "additionalProperties", "default", "anyOf"}


def _seed(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).digest()


def fake_structured(schema, messages):
    """A deterministic JSON value that satisfies `schema` (EV-002 CHG-05): strings are '[가짜] ' + a short piece of the request."""
    user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
    user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    snippet = re.sub(r"\s+", " ", user).strip()[:24] or "응답"

    def gen(s, path):
        if not isinstance(s, dict):
            raise FakeSchemaError(f"{path or '$'}: 스키마가 객체가 아닙니다")
        bad = set(s) - SCHEMA_KEYS
        if bad:
            raise FakeSchemaError(f"{path or '$'}: 가짜 provider가 지원하지 않는 스키마 키워드 {sorted(bad)}")
        if "anyOf" in s:   # D-549 덧 12: the hypothesis agent's value — the first branch
            return gen(s["anyOf"][0], path)
        if "const" in s:
            return s["const"]
        if "enum" in s:
            return s["enum"][0]
        typ = s.get("type")
        if isinstance(typ, list):
            typ = next((x for x in typ if x != "null"), "null")
        h = _seed(path, user)
        if typ == "object" or (typ is None and "properties" in s):
            props = s.get("properties") or {}
            return {k: gen(v, f"{path}.{k}") for k, v in props.items()}
        if typ == "array":
            n = max(int(s.get("minItems", 1)), 1)
            n = min(n, int(s.get("maxItems", n)))
            return [gen(s.get("items") or {"type": "string"}, f"{path}[{i}]") for i in range(n)]
        if typ == "string":
            v = f"[가짜] {snippet} {path.rsplit('.', 1)[-1]}".strip()
            v = v.ljust(int(s.get("minLength", 0)), "·")
            return v[: int(s["maxLength"])] if "maxLength" in s else v
        if typ in ("number", "integer"):
            lo = s.get("minimum", s.get("exclusiveMinimum", 0)); hi = s.get("maximum", s.get("exclusiveMaximum", lo + 1))
            frac = (h[0] + 1) / 257
            v = lo + (hi - lo) * frac
            return int(round(v)) if typ == "integer" else round(v, 4)
        if typ == "boolean":
            return bool(h[0] & 1)
        if typ == "null":
            return None
        raise FakeSchemaError(f"{path or '$'}: 타입을 알 수 없습니다({typ!r})")

    return gen(schema, "")


def fake_embedding(text):
    """Same text -> same unit vector of EMBED_DIM floats (EV-002 CHG-05). The rule lives in packrt (EV-020 CHG-96: a fake-mode
    query embedded by the runtime itself must meet the same vectors)."""
    return _packrt_fake_embedding(text)


class FakeProvider:
    """OpenAI-compatible chat completions with deterministic replies and usage, aggregated per run/case.

    faults (test harness only, from LLMOPS_FAKE_FAULTS outside the product): {"<text in the generation description>":
    {"<row number>" or "*": "format" | "error" | "timeout"}} — makes input-generation rows fail on purpose (AC-39).

    context (EV-022 S-10): the Run context of a call, for the demo answers file (llmops/fake_answers.py) — either given to
    complete() as context=, or found by the resolver context(run, case) for a call on /fake/v1/<run>/<case>. A context is
    {"revision": <Pack revision the case runs>, "case_id": <suite case id or None>, "node": <llm node id, optional>,
    "pack_dir": <the folder the case runs, optional: finds the node from the prompt>}. Without a context, or for a revision
    the file does not have, the replies are exactly as before.

    rule_for (EV-SUI-01 CHG-S05): callable run -> the Pack id of a Pack release Run (else None). A call of such a Run that the
    answers file does not answer goes to the Pack repository's fake rule (llmops/fake_rule.py) when that rule is for this Pack;
    its limits are kept per (run, case) in `limits` until the Run takes them for the case."""

    def __init__(self, faults=None, context=None):
        self.usage = {}  # (run, case) -> {"calls", "prompt_tokens", "completion_tokens", "key_present"}
        self.lock = threading.Lock()
        self.faults = faults or {}
        self.context = context   # callable (run, case) -> context dict or None; None: no Run context (old behaviour)
        self.rule_for = None     # callable run -> Pack id or None (EV-SUI-01 CHG-S05); None: no fake rule (old behaviour)
        self.limits = {}         # (run, case) -> [{"node": <node or None>, "reasons": [...], "at": <time>}], one per call with limits

    def _note_limits(self, run, case, reasons):
        with self.lock:
            self.limits.setdefault((run, case), []).append({"node": None, "reasons": list(reasons), "at": time.time()})

    def claim_limits(self, run, case, node, start=None, end=None):
        """The calls with limits made between a node's start and end times (the runner's own clock on this machine) and not yet
        given to a node go to this node — called when the node's end line is read. Without the times: every call not yet given.
        Parallel llm nodes running at the same time may not be told apart (the first to end takes the calls)."""
        with self.lock:
            for e in self.limits.get((run, case)) or []:
                if e["node"] is None and (start is None or end is None or start <= e["at"] <= end):
                    e["node"] = node

    def take_limits(self, run, case):
        """The case's limits by node, in call order, reasons in the order first seen — and forgotten here (the case keeps them)."""
        with self.lock:
            entries = self.limits.pop((run, case), None) or []
        out = []
        for e in entries:
            row = next((r for r in out if r["node"] == e["node"]), None)
            if row is None:
                out.append(row := {"node": e["node"], "reasons": []})
            row["reasons"] += [x for x in e["reasons"] if x not in row["reasons"]]
        return out

    def _rule_answer(self, messages, run, case):
        """EV-SUI-01 CHG-S05: (reply or None, limits or None) from the Pack repository's fake rule, for a Pack release Run's call
        the answers file did not answer. A rule that should be there but is not usable: (None, [rule_unavailable]) — the call
        answers as before. A refusal fails the call with its reason; its limits are kept first."""
        pid = self.rule_for(run) if self.rule_for is not None and run is not None else None
        rule = fake_rule.for_pack(pid) if pid else None
        if rule is None:
            return None, None
        if rule is fake_rule.UNAVAILABLE:
            return None, [fake_rule.UNAVAILABLE]
        user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
        user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        try:
            return fake_rule.answer(rule, user, required=(fake_rule.locate() or (None, False))[1])
        except fake_rule.RuleRefused as e:
            self._note_limits(run, case, e.limits)
            raise FakeAnswerMissing(str(e)) from None

    def _file_answer(self, messages, run, case, context):
        """The demo answers file's response for this call, or None (no context / no file / revision not in the file)."""
        if context is None and self.context is not None and run is not None:
            try:
                context = self.context(run, case)
            except fake_answers.FakeAnswersError as e:   # a set answers file that cannot be read: refused with the reason (R1-02)
                raise FakeAnswerMissing(str(e)) from None
        if not context or not context.get("revision"):
            return None
        user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
        user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        try:
            return fake_answers.respond(context["revision"], context.get("case_id"), user, node=context.get("node"),
                                        pack_dir=context.get("pack_dir"))
        except (fake_answers.AnswerMissing, fake_answers.FakeAnswersError) as e:
            raise FakeAnswerMissing(str(e)) from None

    def _fault(self, messages):
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        if not self.faults or not system.startswith(GEN_SYSTEM):
            return None
        req = json.loads(next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "{}"))
        for marker, rows in self.faults.items():
            if marker in (req.get("설명") or ""):
                return rows.get(str(req.get("번호"))) or rows.get("*")
        return None

    def complete(self, body, run=None, case=None, key_present=False, context=None):
        messages = body.get("messages") or []
        fault = self._fault(messages)
        if fault == "error":
            raise FakeFault("가짜 provider 오류(주입)")
        if fault == "timeout":
            raise TimeoutError("가짜 provider 시간 초과(주입)")
        rf = body.get("response_format") or {}
        js = (rf.get("json_schema") or {}) if isinstance(rf, dict) and rf.get("type") == "json_schema" else {}
        if js.get("strict"):   # EV-033 RX-4 (packrt 0.10.1): a strict schema OpenAI would refuse is refused here too, before any answer
            problems = strict_schema_problems(js.get("schema") or {})[0]
            if problems:
                raise FakeSchemaError(f"strict 구조화 출력이 받지 않는 스키마입니다 — {problems[0]}")
        answer = self._file_answer(messages, run, case, context)
        if answer is None and fault is None:   # EV-SUI-01 CHG-S05: a Pack release Run's call the file does not answer
            answer, limits = self._rule_answer(messages, run, case)
            if limits:
                self._note_limits(run, case, limits)
        if answer is not None:   # the demo answers file's (or the fake rule's) response, as written (EV-022 S-10)
            reply = answer
        elif fault == "format":
            reply = "[가짜] 형식이 틀린 응답"
        elif isinstance(rf, dict) and rf.get("type") == "json_schema":   # structured output (EV-002 CHG-05)
            schema = (rf.get("json_schema") or {}).get("schema")
            reply = json.dumps(fake_structured(schema, messages), ensure_ascii=False)
        else:
            reply = fake_reply(messages)
        prompt = _tokens(json.dumps(messages, ensure_ascii=False))
        completion = _tokens(reply)
        with self.lock:
            u = self.usage.setdefault((run, case), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "key_present": False})
            u["calls"] += 1; u["prompt_tokens"] += prompt; u["completion_tokens"] += completion; u["key_present"] |= key_present
        digest = hashlib.sha256((reply + str(prompt)).encode()).hexdigest()[:12]
        return {"id": f"chatcmpl-fake-{digest}", "object": "chat.completion", "created": 0, "model": body.get("model") or FAKE_MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}, "provider": "가짜 provider"}


    def complete_anthropic(self, body, key_present=False):
        """Anthropic messages format on top of complete() (EV-005 CHG-29, real-mode tests only)."""
        msgs = ([{"role": "system", "content": body["system"]}] if body.get("system") else []) + list(body.get("messages") or [])
        out = self.complete({"model": body.get("model"), "messages": msgs}, key_present=key_present)
        u = out["usage"]
        return {"id": out["id"].replace("chatcmpl", "msg"), "type": "message", "role": "assistant", "model": out["model"],
                "content": [{"type": "text", "text": out["choices"][0]["message"]["content"]}], "stop_reason": "end_turn",
                "usage": {"input_tokens": u["prompt_tokens"], "output_tokens": u["completion_tokens"]}}

    def complete_gemini(self, model, body, key_present=False):
        """Gemini generateContent format on top of complete() (EV-005 CHG-29, real-mode tests only)."""
        text = lambda c: "".join(p.get("text", "") for p in (c or {}).get("parts") or [])
        msgs = ([{"role": "system", "content": text(body["systemInstruction"])}] if body.get("systemInstruction") else []) + \
            [{"role": "assistant" if c.get("role") == "model" else "user", "content": text(c)} for c in body.get("contents") or []]
        out = self.complete({"model": model, "messages": msgs}, key_present=key_present)
        u = out["usage"]
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": out["choices"][0]["message"]["content"]}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": u["prompt_tokens"], "candidatesTokenCount": u["completion_tokens"], "totalTokenCount": u["total_tokens"]},
                "modelVersion": model}

    def embed(self, body, run=None, case=None, key_present=False):
        """OpenAI-compatible embeddings (EV-002 CHG-05): deterministic vectors, usage aggregated per run/case like chat."""
        inp = body.get("input")
        texts = [inp] if isinstance(inp, str) else list(inp or [])
        if not texts or not all(isinstance(x, str) for x in texts):
            raise FakeSchemaError("input은 글 또는 글 목록이어야 합니다")
        prompt = sum(_tokens(x) for x in texts)
        with self.lock:
            u = self.usage.setdefault((run, case), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "key_present": False})
            u["calls"] += 1; u["prompt_tokens"] += prompt; u["key_present"] |= key_present
        return {"object": "list", "model": body.get("model") or FAKE_MODEL, "provider": "가짜 provider",
                "data": [{"object": "embedding", "index": i, "embedding": fake_embedding(x)} for i, x in enumerate(texts)],
                "usage": {"prompt_tokens": prompt, "total_tokens": prompt}}


class NoKey(Exception):
    pass


class NoAddress(Exception):
    pass


class DecideError(Exception):
    """D-462 B: a decision call that did not answer — only a code, where it was and the HTTP status, never the provider's text."""
    def __init__(self, code, status=None, where=None):
        super().__init__(" ".join(x for x in (code, where, None if status is None else f"HTTP {status}") if x))
        self.code, self.status, self.where = (code if status is None else f"{code}_{status}"), status, where


# D-463 덧 13 (물음 056 (가)): the quality review calls Jev itself, in the request shape of a Pack's decide@1 node (spec 7.1) — packrt's
# provider and fake are not public names. The key is the TYPESAFE_API_KEY line of the file LLMOPS_TYPESAFE_ENV_FILE names, that line only.
TYPESAFE_KEY_ENV = "TYPESAFE_API_KEY"
DECIDE_PATH = "/v1/systemone"
DECIDE_WHERE = "typesafe " + DECIDE_PATH
DECIDE_RETRY_STATUS = (429, 529)   # rate limited / overloaded: wait and try again
DECIDE_RETRY_WAITS = (1, 2)        # seconds before the 2nd and 3rd attempt
_sleep = time.sleep                # replaced in tests
FAKE_DECISION = "fake_decision"    # the fake's answers mean nothing: its limits say so


def typesafe_key(env_file):
    """The TYPESAFE_API_KEY value — this one line only, from the key file (`export` and quotes as a .env writes them). Never
    printed or stored; None when there is none."""
    return env_line(env_file, TYPESAFE_KEY_ENV)


def fake_decide(state, questions, model):
    """LLMOps' own fake Jev: a noul probability per question from sha256 of (model, state, question) — the same input, the same answer."""
    answers = {}
    for name, q in questions.items():
        seed = hashlib.sha256(json.dumps({"model": model, "state": state, "name": name, "question": q}, ensure_ascii=False,
                                         sort_keys=True, default=str).encode()).digest()
        answers[name] = {"type": "noul", "noul": round(0.05 + 0.9 * int.from_bytes(seed[:8], "big") / 2 ** 64, 6)}
    size = len(json.dumps({"state": state, "questions": questions}, ensure_ascii=False, default=str))
    return {"model": model, "answers": answers, "usage": {"input_tokens": size // 4, "output_tokens": len(questions)}, "limits": [FAKE_DECISION]}


def no_key(provider):
    return NoKey(f"{PROVIDERS[provider]['name']} API 키를 찾을 수 없음")


def _split_system(messages):
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    return system, [m for m in messages if m.get("role") != "system"]


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# EV-031 (L-02, instruction 024): a judge request asks for the steadiest answer the provider gives — temperature 0, a seed where the
# provider takes one, and an output cap — so repeated Runs differ by the Pack, not by the judge. Prompt and score rule unchanged;
# other calls (input generation) and the fake provider are as before.
JUDGE_OPTIONS = {"temperature": 0, "max_tokens": 400, "seed": 7}
AI_NODE_OPTIONS = {"temperature": 0, "max_tokens": 1500}   # EV-073 CHG-AI-7: the ✦ AI tab's suggestion (aisuggest.MAX_OUTPUT_TOKENS)


def _call_openai(base, key, model, messages, timeout, options=None):
    body = {"model": model, "messages": messages}
    if options:
        body.update({k: options[k] for k in ("temperature", "max_tokens", "seed", "response_format") if k in options})   # D-549 덧 12: a strict answer shape
    out = _post(base.rstrip("/") + "/chat/completions", body, {"Authorization": f"Bearer {key}"}, timeout)
    return out["choices"][0]["message"]["content"], out.get("usage"), out.get("model")   # the model the provider says answered (dated name)


def _call_anthropic(base, key, model, messages, timeout, options=None):
    system, rest = _split_system(messages)
    body = {"model": model, "max_tokens": 1024, "messages": rest, **({"system": system} if system else {})}
    if options:   # Anthropic takes no seed
        body.update({"max_tokens": options.get("max_tokens", 1024), "temperature": options.get("temperature", 0)})
    out = _post(base.rstrip("/") + "/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
    u = out.get("usage") or {}
    pt, ct = u.get("input_tokens", 0), u.get("output_tokens", 0)
    return "".join(b.get("text", "") for b in out.get("content") or [] if b.get("type") == "text"), {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}, out.get("model")


def _call_gemini(base, key, model, messages, timeout, options=None):
    system, rest = _split_system(messages)
    body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in rest],
            **({"systemInstruction": {"parts": [{"text": system}]}} if system else {})}
    if options:
        body["generationConfig"] = {"temperature": options.get("temperature", 0), "maxOutputTokens": options.get("max_tokens", 400)}
    url = base.rstrip("/") + "/models/" + urllib.parse.quote(model, safe="") + ":generateContent"   # one path segment
    out = _post(url, body, {"x-goog-api-key": key}, timeout)
    parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    u = out.get("usageMetadata") or {}
    pt, ct = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
    return "".join(p.get("text", "") for p in parts), {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": u.get("totalTokenCount", pt + ct)}, out.get("modelVersion")


REAL_CALLS = {"openai": _call_openai, "anthropic": _call_anthropic, "gemini": _call_gemini}


class Gateway:
    def __init__(self, settings, keys, fake):
        self.settings, self.keys, self.fake = settings, keys, fake
        self.log = []  # redacted call records (purpose, mode, model, status, latency, previews)
        self.lock = threading.Lock()

    @property
    def mode_label(self):
        return self.settings.mode_label

    def _record(self, row):
        secrets = self.keys.known_secrets()
        row = {k: guard.redact(v, secrets) if isinstance(v, str) else v for k, v in row.items()}
        with self.lock:
            self.log.append(row)

    def decide(self, model, state, questions):
        """D-462 B (덧 2 · D-463 덧 13): one Jev decision call (decide@1 — POST <base>/v1/systemone, Bearer key) for the efficiency
        wheel's quality review → (raw answer, usage). Fake mode: LLMOps' own fake (no network). Real mode: the key is the one
        TYPESAFE_API_KEY line of LLMOPS_TYPESAFE_ENV_FILE, masked from then on; the address is LLMOPS_TYPESAFE_BASE_URL; 429 · 529
        are tried again. An error carries only a code, where and the HTTP status."""
        t = time.monotonic()
        if self.settings.mode == "fake":
            raw = fake_decide(state, questions, model)
            usage = dict(raw["usage"])
        else:
            raw, usage = self._decide_real(model, state, questions)
        with self.lock:   # nothing in this row can hold a key (no answer text, no state)
            self.log.append({"purpose": "quality_review", "mode": self.settings.mode, "model": model, "status": "ok", "latency": round(time.monotonic() - t, 3)})
        return raw, usage

    def _decide_real(self, model, state, questions):
        if not self.settings.typesafe_env_file:
            raise DecideError("decide_no_key_file")
        key = typesafe_key(self.settings.typesafe_env_file)
        if not key:
            raise DecideError("decide_no_key")
        if self.keys is not None:
            self.keys.add_secret(key)
        if any(ord(c) < 32 or ord(c) == 127 for c in key):   # it would end up whole in the HTTP library's header error
            raise DecideError("decide_bad_key")
        base = (self.settings.typesafe_base_url or "").rstrip("/")
        if not base:
            raise DecideError("decide_no_address")
        data = json.dumps({"model": model, "state": state, "questions": questions}).encode()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        for attempt in range(len(DECIDE_RETRY_WAITS) + 1):
            try:
                with urllib.request.urlopen(urllib.request.Request(base + DECIDE_PATH, data=data, method="POST", headers=headers), timeout=60) as r:
                    out = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                e.close()
                if e.code in DECIDE_RETRY_STATUS and attempt < len(DECIDE_RETRY_WAITS):
                    _sleep(DECIDE_RETRY_WAITS[attempt])
                    continue
                raise DecideError("decide_http", e.code, DECIDE_WHERE) from None
            except (OSError, ValueError):
                raise DecideError("decide_network", where=DECIDE_WHERE) from None
        if not isinstance(out, dict):
            raise DecideError("decide_answer", where=DECIDE_WHERE)
        u = out.get("usage") or {}
        return out, {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}

    def chat(self, messages, purpose, provider=DEFAULT_PROVIDER, model=None, timeout=30, fake_keyless=False, response_format=None):
        """One LLMOps call (LLM judge, input generation) with that provider's key only. Raises NoKey when that provider has
        no key. Project calls always pass the Project's model; a call without one sends FAKE_MODEL (internal compatibility).
        fake_keyless (D-502 (1), the ✦ AI tab): in fake mode the fake provider answers without a key; real mode still needs one.
        response_format (D-549 덧 12, the hypothesis agent): OpenAI's structured output, sent as it is (the fake provider refuses a
        strict schema OpenAI would refuse); the other providers' calls take none."""
        key = self.keys.value(provider)
        if not key and not (fake_keyless and self.settings.mode == "fake"):
            raise no_key(provider)
        model = model or FAKE_MODEL
        t = time.monotonic()
        if self.settings.mode == "fake":
            out = self.fake.complete({"model": model, "messages": messages, **({"response_format": response_format} if response_format else {})},
                                     run=purpose, key_present=True)
            text, usage, served = out["choices"][0]["message"]["content"], out.get("usage"), None
        else:
            base, var = self.settings.base_url(provider)
            if not base:
                raise NoAddress(f"실제 모드인데 {PROVIDERS[provider]['name']} 주소(환경변수 {var})가 없습니다")
            options = JUDGE_OPTIONS if purpose == "judge" else AI_NODE_OPTIONS if purpose == "ai_node" else None
            if response_format and provider == "openai":   # the other formats' calls would read the options as their own
                options = {**(options or {}), "response_format": response_format}
            text, usage, served = REAL_CALLS[provider](base, key, model, messages, timeout, options)
            served = served if isinstance(served, str) else None
        self._record({"purpose": purpose, "mode": self.settings.mode, "provider": self.mode_label, "llm_provider": provider, "model": model, "served_model": served, "status": "ok",
                      "options": dict(JUDGE_OPTIONS) if purpose == "judge" and self.settings.mode != "fake" else None,
                      "latency_s": round(time.monotonic() - t, 3), "request_preview": json.dumps(messages, ensure_ascii=False)[:300], "response_preview": text[:300],
                      "usage": usage})
        return {"text": text, "usage": usage, "provider": self.mode_label, "llm_provider": provider, "model": model, "served_model": served,
                "fake": self.settings.mode == "fake"}


def judge_names(out):
    """EV-036 (answer 036-01): who judged — the gateway mode label, the provider and model LLMOps asked for, and the model the
    provider says answered (OpenAI's dated name; None when the reply has none). A fake-mode call is recorded as '가짜'."""
    if out.get("fake"):
        return {"provider": out["provider"], "llm_provider": "가짜", "model": "가짜", "served_model": None}
    return {"provider": out["provider"], "llm_provider": out.get("llm_provider"), "model": out.get("model"), "served_model": out.get("served_model")}


def judge(gw, case, provider=DEFAULT_PROVIDER, model=None):
    """LLM judge for one finished case through the gateway with the Project's provider (raises NoKey without its key)."""
    prompt = [{"role": "system", "content": JUDGE_SYSTEM},
              {"role": "user", "content": json.dumps({"input": case["input"], "expected": case.get("expected"), "answer": case.get("final")}, ensure_ascii=False)}]
    out = gw.chat(prompt, purpose="judge", provider=provider, model=model)
    try:
        data = json.loads(strip_fence(out["text"]))
        return {"score": max(0.0, min(1.0, float(data["score"]))), "why": str(data.get("why", ""))[:200], **judge_names(out)}
    except (ValueError, KeyError, TypeError):
        return {"score": 0.0, "why": "judge 응답을 해석하지 못함", **judge_names(out)}


def judge_part(value, limit):
    """D-593 (eval 193): one part of the node judge's message — whole when its JSON fits `limit` characters, else its first `limit`
    characters with '…(n자 중 앞 limit자)' (the whole message used to be cut at 6000 with the answer last: a large input left nothing of it)."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return value
    return text if len(text) <= limit else f"{text[:limit]}…({len(text)}자 중 앞 {limit}자)"


def judge_of(table, node):
    """D-604: which judge asks for this node — LLMOPS_DIAG_JUDGE is 'chat' · 'jev' (every node) or a table 'node=jev,node=chat'; a node not
    in the table, or no node named, is chat."""
    table = table or "chat"
    if "=" not in table:
        return table
    return dict(x.split("=", 1) for x in table.split(",")).get(node, "chat")


def node_kw(settings, node):
    """D-604: {'node': node} for a judge_node call when a node table is set, else {} (stand-ins and the plain settings take four arguments)."""
    return {"node": node} if "=" in (getattr(settings, "diag_judge", "") or "") else {}


def judge_node(gw, expect_text, node_input, node_output, provider=DEFAULT_PROVIDER, model=None, node=None):
    """Model reviewer for one node visit against its expect.text (EV-002 CHG-12 model_judge) with the Project's provider.
    Raises NoKey without its key."""
    if judge_of(getattr(getattr(gw, "settings", None), "diag_judge", "chat"), node) == "jev":   # D-568 B1 · D-604: Jev for this node
        return judge_node_jev(gw, expect_text, node_input, node_output)
    prompt = [{"role": "system", "content": JUDGE_SYSTEM},
              {"role": "user", "content": json.dumps({"expect": judge_part(expect_text, 1000), "answer": judge_part(node_output, 3000),
                                                      "input": judge_part(node_input, 2000)}, ensure_ascii=False, default=str)}]   # D-593: each part cut on its own; D-604 덧 2: this order stays
    out = gw.chat(prompt, purpose="judge", provider=provider, model=model)
    usage = {"usage": out.get("usage")}   # D-506: the wheel's ④ records the call at its own tokens (the summaries keep named fields only)
    try:
        data = json.loads(strip_fence(out["text"]))
        return {"score": max(0.0, min(1.0, float(data["score"]))), "why": str(data.get("why", ""))[:200], **judge_names(out), **usage}
    except (ValueError, KeyError, TypeError):
        return {"score": 0.0, "why": "judge 응답을 해석하지 못함", **judge_names(out), **usage}


def judge_node_jev(gw, expect_text, node_input, node_output):
    """D-568 B1: the node judge through Jev (decide@1, the review's model) — each non-empty line of expect.text one yes/no question,
    the score the lowest yes probability (every line must hold), the reason 'Jev · 기대 n줄 중 k줄 예', Jev's own price. Raises
    DecideError (code only) when it cannot ask, as the chat judge raises NoKey."""
    from .autoreview import usd
    lines = [x.strip() for x in str(expect_text or "").splitlines() if x.strip()][:8] or [str(expect_text or "")]
    questions = {f"e{i}": {"type": "noul", "instructions": f"출력이 이 기대를 지키나? — {t}",
                           "criteria": {"true": "출력이 이 기대를 지킴", "false": "출력이 이 기대를 어기거나 빠뜨림"}} for i, t in enumerate(lines, 1)}
    model = gw.settings.auto_review_model
    raw, usage = gw.decide(model, {"입력": node_input, "출력": node_output}, questions)
    answers = (raw or {}).get("answers") if isinstance(raw, dict) else None
    ps = [((answers or {}).get(k) or {}).get("noul") for k in questions]
    if not ps or any(not isinstance(p, (int, float)) for p in ps):
        return {"score": 0.0, "why": "judge 응답을 해석하지 못함", "provider": "typesafe", "llm_provider": "가짜" if gw.settings.mode == "fake" else "Jev",
                "model": model, "served_model": None, "usage": usage, "usd": usd(usage)}
    return {"score": round(float(min(ps)), 6), "why": f"Jev · 기대 {len(ps)}줄 중 {sum(p >= 0.5 for p in ps)}줄 예",
            "provider": "typesafe", "llm_provider": "가짜" if gw.settings.mode == "fake" else "Jev", "model": model,
            "served_model": (raw or {}).get("model") if isinstance((raw or {}).get("model"), str) and gw.settings.mode != "fake" else None,
            "usage": usage, "usd": usd(usage)}


def strip_fence(text):
    """Models often wrap JSON in a ``` block."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s.strip()


GEN_FORMAT_REASON = "입력 형식에 맞지 않아 뺐습니다"


def generate_inputs(gw, release, count, desc="", provider=DEFAULT_PROVIDER, model=None):
    """Input generation (P-09): one LLMOps call per row with the Project's provider; a row is kept only if the reply is a
    JSON object whose input-field value is a non-empty string (the shape of the release graph's input). Raises NoKey
    before any call without that provider's key."""
    if not gw.keys.value(provider):
        raise no_key(provider)
    field = release["graph"]["input_field"]
    secrets = gw.keys.known_secrets()
    rows = []
    for n in range(1, count + 1):
        req = {"입력 필드": field, "release": release["name"], "설명": desc, "번호": n, "개수": count}
        prompt = [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": json.dumps(req, ensure_ascii=False)}]
        try:
            text = gw.chat(prompt, purpose="generate", provider=provider, model=model)["text"]
        except NoKey:
            raise
        except (TimeoutError, OSError) as e:
            timed_out = isinstance(e, TimeoutError) or "timed out" in str(e)
            rows.append({"ok": False, "raw": "", "reason": "만들지 못했습니다 (시간 초과)" if timed_out else f"만들지 못했습니다 (LLM 오류: {guard.redact(str(e), secrets)[:120]})"})
            continue
        except Exception as e:  # noqa: BLE001 - any provider failure is a failed row, not a failed request
            rows.append({"ok": False, "raw": "", "reason": f"만들지 못했습니다 (LLM 오류: {guard.redact(str(e), secrets)[:120]})"})
            continue
        try:
            data = json.loads(strip_fence(text))
        except ValueError:
            data = None
        value = data.get(field) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            rows.append({"ok": True, "value": {field: guard.redact(value.strip(), secrets)}})
        else:
            rows.append({"ok": False, "raw": guard.redact(text, secrets)[:200], "reason": GEN_FORMAT_REASON})
    return rows
