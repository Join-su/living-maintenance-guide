"""LLM call through the OpenAI-compatible HTTP API at $OPENAI_BASE_URL (stdlib only; never called at import)."""
import json
import os
import urllib.request


def llm(system, user, model="gpt-4o-mini"):
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("OPENAI_BASE_URL이 없습니다")
    body = json.dumps({"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]
