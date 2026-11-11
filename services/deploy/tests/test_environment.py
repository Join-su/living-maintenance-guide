"""실행 환경(EV-003 CHG-02): 전용 .venv로 돌 때 설치본이 pinned.json(llmops 커밋 고정·비편집)과 같은지 본다.
공유 환경으로 되돌려(DEPLOY_VENV) 돌 때는 이 시험만 사유를 밝히고 건너뛴다. 파일 바이트 대조는 scripts/check-pin.py(V-14)."""
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

from conftest import ROOT

DEDICATED = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()


@pytest.mark.skipif(not DEDICATED, reason="공유 환경으로 실행 중(DEPLOY_VENV 되돌림) — 전용 환경 확인은 하지 않음")
def test_dedicated_venv_matches_pin():
    import packrt
    import tracewriter
    pin = json.loads((ROOT / "pinned.json").read_text(encoding="utf-8"))
    venv = str((ROOT / ".venv").resolve())
    for mod, name in ((packrt, "packrt"), (tracewriter, "tracewriter")):
        dist = distribution(name)
        assert dist.version == pin[name]["version"] == mod.__version__
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        assert not direct.get("dir_info", {}).get("editable")
        assert str(Path(mod.__file__).resolve()).startswith(venv)
    assert not [p for p in sys.path if "llmops-lite-v2" in p]
