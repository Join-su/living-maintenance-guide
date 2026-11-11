"""EV-007 P-01 · P-02: loading the packrt test Packs gives what the 0.11.0 code (e6f5a26) gave — ok, revision, errors and warnings in
their order and wording — compared as JSON text (BYTE) with tests/fixtures/load-e6f5a26.json. The expected file was written by the run
folder's evidence/s0/characterize_retrieve.py (EV-007, git-ignored), which calls characterize() below."""
import json
from pathlib import Path

from packrt.ir import load_pack

FIX = Path(__file__).resolve().parent / "fixtures"
PACKS = ["decide_min", "llm_min", "predict_min", "retrieve_min", "task10_min"]


def characterize():
    out = {}
    for name in PACKS:
        r = load_pack(FIX / name)
        out[name] = {"ok": r.ok, "revision": r.revision, "errors": [str(e) for e in r.errors], "warnings": [str(w) for w in r.warnings]}
    return out


def test_load_results_are_byte_identical():
    want = json.loads((FIX / "load-e6f5a26.json").read_text(encoding="utf-8"))["packs"]
    got = characterize()
    assert list(got) == PACKS == sorted(want)
    for name in PACKS:
        assert json.dumps(got[name], ensure_ascii=False, sort_keys=True) == json.dumps(want[name], ensure_ascii=False, sort_keys=True), name
