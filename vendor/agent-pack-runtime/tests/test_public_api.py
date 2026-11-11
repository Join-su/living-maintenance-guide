"""Every name in PUBLIC-API.md exists (EV-023 CHG-201): a module that imports, or an attribute of its module."""
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def entries():
    mod = None
    for line in (ROOT / "PUBLIC-API.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## `([\w.]+)`$", line)
        if m:
            mod = m.group(1)
            continue
        m = re.match(r"^- `([\w]+)`$", line)
        if m and mod:
            yield mod, m.group(1)


def test_the_list_is_not_empty():
    assert len(list(entries())) >= 70


def test_every_listed_name_exists():
    missing = []
    for mod, name in entries():
        owner = importlib.import_module(mod)
        if hasattr(owner, name):
            continue
        try:
            importlib.import_module(f"{mod}.{name}")
        except ImportError:
            missing.append(f"{mod}.{name}")
    assert not missing, missing


def test_new_public_names_are_the_old_objects():
    from packrt.ir import patch, semantic
    assert patch.editable_params is patch._editable and semantic.state_type is semantic._state_type


def test_0_12_1_adds_two_names_and_removes_none():
    """EV-008 V-PUB (CHG-P02, PRES-P04): the 0.12.0 list (102 names, sha256 of the sorted 'module.name' lines below) is all still
    there, and 0.12.1 adds packrt.index.find_corpus and packrt.index.IndexBuildError only."""
    import hashlib
    names = sorted(f"{m}.{n}" for m, n in entries())
    added = {"packrt.index.find_corpus", "packrt.index.IndexBuildError", "packrt.ir.fixed_flow", "packrt.ir.check_fixed_flow",
             "packrt.ir.patch.bridge_ops", "packrt.ir.node_options"}   # EV-016: 0.12.6 adds fixed_flow · check_fixed_flow; D-462: 0.13.0 adds bridge_ops; D-476: 0.15.0 adds node_options
    old = sorted(set(names) - added)
    assert added <= set(names) and len(old) == 102 == len(names) - len(added)
    assert hashlib.sha256("\n".join(old).encode()).hexdigest() == "c27d59ab07c661fff80ca9fb91a0caa3f5bb9a545c9ea416be334dd2022e3520"
    from packrt.index import IndexBuildError, find_corpus
    from packrt.index.errors import IndexBuildError as same
    assert IndexBuildError is same and callable(find_corpus)
