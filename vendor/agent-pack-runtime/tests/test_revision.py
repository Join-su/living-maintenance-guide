"""Pack revision is fixed (EV-023 PRES-202): the test Pack copied from llmops-lite-v2 tests/fixtures/packs/task10_min keeps its revision."""
from pathlib import Path

from packrt.ir import load_pack

PACK = Path(__file__).resolve().parent / "fixtures" / "task10_min"


def test_task10_min_revision():
    r = load_pack(PACK)
    assert r.ok, r.errors
    assert r.revision == "sha256:1f573d6fbf764b0f12d35702fcf289b8836856248a220c9d17a2ee2f22f4f419"


def test_versions():
    import packrt
    import tracewriter
    assert packrt.__version__ == "0.15.0" and packrt.IR_VERSION == 1 and tracewriter.__version__ == "0.1.4"
