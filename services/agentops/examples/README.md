Each example release calls an LLM only through `llm()` in its own graph.py: stdlib HTTP to $OPENAI_BASE_URL/chat/completions.
In fake mode the product points OPENAI_BASE_URL at its local fake provider. Nothing is called at import time.
