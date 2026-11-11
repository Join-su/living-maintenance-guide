"""tracewriter redaction keeps masking key-like values (llmops-lite-v2 PRES-96)."""
from tracewriter import REDACTION_POLICY_VERSION, redact, redact_text
from tracewriter.redact import REDACTED


def test_key_patterns_are_masked():
    for key in ("sk-ant-abcdefgh12345678", "sk-abcdefgh12345678", "AKIAABCDEFGHIJKLMNOP", "ghp_abcdefghijklmnopqrstu"):
        out = redact_text(f"token {key} end")
        assert key not in out and REDACTED in out


def test_known_secret_and_nested_values():
    out, policy = redact({"a": ["x my-secret-value y"], "b": "task-runner-v2"}, secrets=["my-secret-value"])
    assert out == {"a": [f"x {REDACTED} y"], "b": "task-runner-v2"} and policy == REDACTION_POLICY_VERSION   # masked in place, ordinary words kept


# GAP-003 (instruction 014, llmops EV-032 0854e72): OpenAI's own half-masked key in its 401 text leaves no visible end
END = "Qz7W"   # a distinctive fake visible end
OPENAI_401 = ("Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-proj-" + "*" * 40 + END +
              ".', 'code': 'invalid_api_key'}}")   # the provider's sentence with its web address left out (netscan)


def test_provider_masked_keys_are_masked_and_other_text_is_kept():
    assert redact_text(OPENAI_401) == OPENAI_401.replace("sk-proj-" + "*" * 40 + END, "sk-***")
    assert redact_text("provided: sk-abc*****wxyz.") == "provided: sk-***."
    full = "sk-proj-fakeAbCdEfGh1234567890ijklmnopQRSTuvwx" + END
    assert END not in redact_text(f"key {full} and {full[:12]}***{END}", [full])   # what is left after the exact mask too
    for plain in ("rate 3*** ok", "별표 *** 세 개", "sk 없음 ***", "a-b ***x", "task-runner-v2"):
        assert redact_text(plain) == plain
    out, policy = redact({"error": {"message": OPENAI_401}, "lines": [OPENAI_401]})
    assert END not in str(out) and "sk-***" in str(out) and policy == REDACTION_POLICY_VERSION


def test_a_payload_file_keeps_no_visible_key_end(tmp_path):
    """The raw-value file the writer stores (no database needed for this part) — the bytes on disk and their hash."""
    from tracewriter import TraceWriter
    w = TraceWriter(payload_root=tmp_path / "payloads", secrets=())
    ref, rel = w.store_payload({"provider_raw": {"error": {"message": OPENAI_401}}, "stderr": OPENAI_401}, "operational", {})
    data = (tmp_path / "payloads" / rel).read_bytes()
    assert END.encode() not in data and b"sk-***" in data and ref["ref"].endswith(rel.split("/")[-1])
    assert [p for p in (tmp_path / "payloads").rglob("*") if p.is_file() and END.encode() in p.read_bytes()] == []
