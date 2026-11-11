"""Fake Jev (TypeSafe AI) decision model — the answer shape of `POST /v1/systemone` (spec 7.1), decided by the input only
(agent-pack-runtime EV-001 CHG-104). Standard library only, so LLMOps' fake server, the Deployment Server and Pack test tools can
all call this one rule instead of copying it.

  respond(state, questions, *, model=..., overrides=None) -> {"model", "answers", "usage", "limits": ["fake_decision"]}

Values come from sha256 of the canonical JSON of (model, state, question name, question): the same input gives the same answer,
a different question or state gives another one. A Pack that needs a particular answer (to test a branch) passes `overrides`
= {question name: answer in the Jev shape}; those answers are used as given. The values mean nothing — `limits` says so."""
import hashlib
import json

LIMIT = "fake_decision"
DEFAULT_MODEL = "jev-1.13.0"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _unit(seed, i):
    """A number in [0, 1) from the seed and an index."""
    h = hashlib.sha256(f"{seed}:{i}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def _weights(seed, n):
    w = [0.05 + _unit(seed, i) for i in range(n)]
    s = sum(w)
    p = [round(x / s, 6) for x in w]
    p[-1] = round(1 - sum(p[:-1]), 6)   # the probabilities add up to 1
    return p


def _answer(name, question, seed):
    typ, crit = question.get("type"), question.get("criteria")
    if typ == "noul":
        return {"type": "noul", "noul": round(0.05 + 0.9 * _unit(seed, 0), 6)}
    if typ == "choice":
        keys = list(crit)
        p = _weights(seed, len(keys))
        top = max(range(len(keys)), key=lambda i: p[i])
        return {"type": "choice", "choice": keys[top], "probabilities": dict(zip(keys, p)), "confidence": p[top]}
    if typ == "score":
        p = _weights(seed, len(crit))
        top = max(range(len(crit)), key=lambda i: p[i])
        return {"type": "score", "score": top + 1, "probabilities": {str(i + 1): v for i, v in enumerate(p)}, "legend": list(crit),
                "confidence": p[top]}
    raise ValueError(f"가짜 Jev가 모르는 질문 종류입니다: {name}({typ!r})")


def respond(state, questions, *, model=DEFAULT_MODEL, overrides=None):
    """The Jev answer for these questions about this state (deterministic). overrides replaces answers question by question."""
    overrides = overrides or {}
    answers = {}
    for name, question in questions.items():
        if name in overrides:
            answers[name] = overrides[name]
            continue
        seed = hashlib.sha256(_canonical({"model": model, "state": state, "name": name, "question": question}).encode()).hexdigest()
        answers[name] = _answer(name, question, seed)
    return {"model": model, "answers": answers,
            "usage": {"input_tokens": len(_canonical({"state": state, "questions": questions})) // 4, "output_tokens": len(questions)},
            "limits": [LIMIT]}


class FakeJevProvider:
    """A provider object for in-process runs: decide() answers with respond(); it does nothing else."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []
        self._last = None

    def decide(self, provider, model, state, questions):
        self.calls.append({"provider": provider, "model": model, "state": state, "questions": questions})
        out = respond(state, questions, model=model, overrides=self.overrides)
        self._last = out
        return out, dict(out["usage"])

    def last_response(self):
        return self._last

    def chat(self, *a, **k):
        raise RuntimeError("가짜 Jev는 decide만 합니다(chat 없음)")

    def embed(self, *a, **k):
        raise RuntimeError("가짜 Jev는 decide만 합니다(embed 없음)")
