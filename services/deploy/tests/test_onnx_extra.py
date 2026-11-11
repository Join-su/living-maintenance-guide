"""갈래 k KS-3(D-261 (2) · D-265 Q-k2 (가)): 맥 전용 덧목록 requirements/onnx-extra.txt와 check-pin의 기대 판 합치기.
이미지 목록 · deploy-venv.txt는 그대로(덧목록 이름은 거기 없음)."""
import importlib.util
import re

from conftest import ROOT

EXTRA = ROOT / "requirements" / "onnx-extra.txt"


def _check_pin():
    spec = importlib.util.spec_from_file_location("check_pin_k3", ROOT / "scripts" / "check-pin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_k3_extra_pins_with_hashes_and_no_overlap():
    mod = _check_pin()
    extra = mod.req_versions(EXTRA)
    assert extra["onnxruntime"] == "1.31.0" and set(extra) == {"onnxruntime", "numpy", "protobuf", "flatbuffers"}
    base = mod.req_versions(ROOT / "requirements" / "deploy-venv.txt")
    assert not set(extra) & set(base)                                   # 겹치는 이름 없음(packaging은 기본 목록 것)
    images = mod.req_versions(ROOT / "requirements" / "deploy-image.hashes.txt")
    assert not set(extra) & set(images)                                 # 배포 이미지 목록에 들어가지 않음
    blocks = re.split(r"\n(?=[a-z])", "\n".join(l for l in EXTRA.read_text().splitlines() if not l.startswith("#")).strip())
    assert len(blocks) == 4 and all("--hash=sha256:" in b for b in blocks)   # 줄마다 hash(--require-hashes)


def test_k3_wanted_versions_merges_extra_only_when_present(tmp_path):
    mod = _check_pin()
    base = tmp_path / "base.txt"
    base.write_text("a==1\nb==2\n")
    assert mod.wanted_versions(base, None) == {"a": "1", "b": "2"}
    assert mod.wanted_versions(base, tmp_path / "missing.txt") == {"a": "1", "b": "2"}   # 덧목록 없으면 지금과 같음
    extra = tmp_path / "extra.txt"
    extra.write_text("c==3 \\\n    --hash=sha256:00\nb==2\n")
    assert mod.wanted_versions(base, extra) == {"a": "1", "b": "2", "c": "3"}
    extra.write_text("b==9\n")
    assert mod.wanted_versions(base, extra)["b"] == "?"                 # 두 목록의 판이 다르면 불일치로


LINUX = ROOT / "requirements" / "onnx-extra-linux.hashes.txt"


def test_v3_image_extra_is_the_mac_extra_with_vps_wheel_hashes_only():
    """C1-a15 V-3(D-334 Q-2 (가) · D-363 ②): VPS 이미지 덧목록 = 맥 덧목록과 같은 이름 · 판, 해시는 VPS wheel(linux x86_64 cp314)만,
    이미지 기본 목록 · deploy-venv.txt와 이름 겹침 0 — Dockerfile이 기본 목록 다음에 같은 해시 규칙으로 설치."""
    mod = _check_pin()
    assert mod.req_versions(LINUX) == mod.req_versions(EXTRA)
    assert not set(mod.req_versions(LINUX)) & set(mod.req_versions(ROOT / "requirements" / "deploy-image.hashes.txt"))
    text = LINUX.read_text(encoding="utf-8")
    wheels = re.findall(r"^#   (\S+\.whl)$", text, re.M)
    spec = importlib.util.spec_from_file_location("hash_requirements_v3", ROOT / "scripts" / "hash-requirements.py")
    hr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hr)
    assert wheels and all(hr.fits_vps(w) for w in wheels)                 # 적어 둔 wheel 이름이 모두 VPS 규칙에 맞음
    assert any(w.startswith("onnxruntime-1.31.0-cp314-cp314-manylinux") and w.endswith("_x86_64.whl") for w in wheels)
    blocks = re.split(r"\n(?=[a-z])", "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip())
    assert len(blocks) == 4 and sum(b.count("--hash=sha256:") for b in blocks) == len(wheels)   # 해시 수 = 적어 둔 wheel 수
