"""Deterministic fake embedding (pack resource contract 4, E-1 `mode: fake`): the same text always gives the same unit vector of
FAKE_DIM floats. The rule is the one LLMOps' fake provider has answered embeddings with since EV-002 (CHG-05) and the one task10's
index tool builds fake releases with, so a fake-mode query and a fake release always meet. No provider is called."""
import hashlib
import math

FAKE_DIM = 64


def fake_embedding(text):
    raw = b"".join(hashlib.sha256(f"{i}:{text}".encode()).digest() for i in range(FAKE_DIM * 2 // 32 + 1))
    vec = [(int.from_bytes(raw[2 * i:2 * i + 2], "big") / 65535.0) * 2 - 1 for i in range(FAKE_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]
