"""packrt 0.13.2: custom 규칙 함수 서명(codesig.function_sig)이 Python 판에 기대지 않음 — 같은 함수는 3.12 · 3.14에서 같은 값,
주석 · 설명 글만 바꾸면 같음, 규칙을 더하거나 숫자를 바꾸면 다름(0.13.1까지는 ast.dump라 3.13부터 빈 칸이 빠져 값이 달랐음)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from packrt.ir import codesig

RW = Path(__file__).parent / "fixtures" / "ff_rewrite"
CODESIG = Path(codesig.__file__)

CHECK = '''import re

LIMIT = 3


def _ok(text):
    return len(text) <= LIMIT


def check(text):
    if not _ok(text):
        return False, "long"
    return True, None
'''


def sig_of(tmp_path, src, name="a"):
    pack = tmp_path / name
    (pack / "custom").mkdir(parents=True)
    (pack / "custom" / "__init__.py").write_text("", encoding="utf-8")
    (pack / "custom" / "rule.py").write_text(src, encoding="utf-8")
    return codesig.function_sig(pack, "custom.rule:check")


def test_same_function_has_a_fixed_value(tmp_path):
    # 이 값은 Python 판과 상관없이 같아야 함(3.12 · 3.14 둘 다 이 값)
    assert sig_of(tmp_path, CHECK) == "sha256:6ec053e932caf8b432c8450ac69904e4d5ef70e923a8535c9ad2b8153b3edfb8"


@pytest.mark.parametrize("old, new", [
    ("def check(text):\n", "def check(text):  # 주석\n"),
    ("def check(text):\n", 'def check(text):\n    """설명 글."""\n'),
    ("def _ok(text):\n", 'def _ok(text):\n    """설명 글."""\n    # 주석\n\n'),
    ("LIMIT = 3\n", "LIMIT = 3  # 한도\n"),
])
def test_comment_or_docstring_only_is_the_same(tmp_path, old, new):
    assert CHECK.count(old) == 1
    assert sig_of(tmp_path, CHECK.replace(old, new), "b") == sig_of(tmp_path, CHECK)


@pytest.mark.parametrize("old, new", [
    ("LIMIT = 3\n", "LIMIT = 4\n"),                                                                     # 숫자 바꿈(상수)
    ("return len(text) <= LIMIT", "return len(text) < LIMIT"),                                          # 비교 바꿈
    ("    return True, None\n", '    if "x" in text:\n        return False, "x"\n    return True, None\n'),  # 규칙 더함
    ('return False, "long"', 'return False, "too long"'),                                               # 글 바꿈
])
def test_rule_added_or_number_changed_is_different(tmp_path, old, new):
    assert CHECK.count(old) == 1
    assert sig_of(tmp_path, CHECK.replace(old, new), "b") != sig_of(tmp_path, CHECK)


def _others():
    found, mine = {}, sys.version_info[:2]
    for minor in range(10, 16):
        exe = shutil.which(f"python3.{minor}")
        if exe and (3, minor) != mine:
            found[minor] = exe
    return found


@pytest.mark.skipif(not _others(), reason="다른 Python 판 없음")
@pytest.mark.parametrize("ref", ["custom.report:grounded", "custom.report:grounded_2"])
def test_other_python_versions_give_the_same_value(ref):
    here = codesig.function_sig(RW, ref)
    assert here and here.startswith("sha256:")
    code = ("import importlib.util, sys\n"
            "s = importlib.util.spec_from_file_location('codesig', sys.argv[1]); m = importlib.util.module_from_spec(s)\n"
            "s.loader.exec_module(m); print(m.function_sig(sys.argv[2], sys.argv[3]))\n")
    for minor, exe in _others().items():
        if minor < 9:
            continue
        out = subprocess.run([exe, "-I", "-B", "-c", code, str(CODESIG), str(RW), ref], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, (minor, out.stderr[-500:])
        assert out.stdout.strip() == here, f"3.{minor}"
