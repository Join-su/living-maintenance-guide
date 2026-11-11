"""EV-005 K-1 · V-K (CHG-K01 ~ K03, D-276): a network module imported by Pack code is a load error whatever capabilities.network says;
the check follows the Pack modules a function imports (custom.* and relative imports that resolve to a Pack file — those are followed,
not counted, D-5); validate@2 custom rules are checked the same way. Kept: the side_effect process-module warning and the llmops /
packrt coupling error. Not seen: dynamic imports (a known limit). The module names are written into a temporary Pack only."""
import json

import packrt
from packrt.ir import load_pack

NET = "so" + "cket"   # the module names live in the temporary Pack's files, not in this file's imports
HTTPX = "ht" + "tpx"


def make(tmp_path, files, network="none", side_effect="none", rule=False, name="p"):
    d = tmp_path / name
    (d / "schemas").mkdir(parents=True)
    (d / "schemas" / "doc.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    for rel, text in {"custom/__init__.py": "", "custom/checks.py": "def ok(value):\n    return True\n", **files}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, encoding="utf-8")
    caps = {"side_effect": side_effect}
    if network is not None:
        caps["network"] = network
    nodes = [{"id": "work", "kind": "custom_code@3", "params": {"fn": "custom.work:run"}, "capabilities": caps, "reads": ["doc"], "writes": ["out"]}]
    if rule:
        nodes.append({"id": "chk", "kind": "validate@2", "params": {"rules": [{"custom": "custom.checks:ok"}]}, "reads": ["out"], "writes": ["chk"]})
    names = [n["id"] for n in nodes]
    doc = {"ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__}, "pack": {"id": "net", "version": "V0", "title": "t"},
           "env": {"required": [], "optional": []}, "schemas": {"Doc": "schemas/doc.json"},
           "graphs": {"g": {"state": {"doc": "Doc", "out": "dict", "chk": "dict"}, "nodes": nodes,
                            "edges": [["START", names[0]]] + [[a, b] for a, b in zip(names, names[1:])] + [[names[-1], "END"]]}},
           "entrypoints": [{"graph": "g", "input": "Doc", "output": "Doc"}], "trace": {"contract": 1}}
    (d / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return load_pack(d)


def errs(r):
    return [(e["path"], e["message"]) for e in r.errors]


RUN = "def run(doc):\n    return {'out': {}}\n"


def test_clean_pack_loads(tmp_path):
    r = make(tmp_path, {"custom/work.py": RUN}, rule=True)
    assert r.ok and r.warnings == [], (r.errors, r.warnings)


def test_network_import_is_an_error_whatever_network_says(tmp_path):
    for i, network in enumerate(("none", "allowlisted")):   # the schema requires capabilities.network, so there is no third case
        r = make(tmp_path, {"custom/work.py": f"import {NET}\n" + RUN}, network=network, name=f"p{i}")
        assert not r.ok
        assert errs(r) == [("graphs/g/nodes/0/params/fn",
                            f"custom/work.py이 망 모듈을 import합니다(Pack 코드는 망을 직접 쓰지 않습니다 — provider · store는 packrt가 부릅니다): {NET}")]
        assert not any("네트워크 모듈" in w["message"] for w in r.warnings)   # the 0.11.0 warning is gone


def test_from_import_and_submodule_count(tmp_path):
    r = make(tmp_path, {"custom/work.py": f"from {HTTPX} import Client\nimport urllib.request\n" + RUN})
    assert not r.ok and errs(r)[0][1].endswith(f"{HTTPX}, urllib")


def test_follows_pack_modules(tmp_path):
    files = {"custom/work.py": "from custom import helper\n" + RUN,
             "custom/helper.py": "from custom.deep.inner import x\n",
             "custom/deep/__init__.py": "", "custom/deep/inner.py": f"import {HTTPX}\nx = 1\n"}
    r = make(tmp_path, files)
    assert not r.ok
    assert errs(r)[0][1].startswith(f"custom/work.py → custom/helper.py → custom/deep/inner.py이 망 모듈을 import합니다")


def test_relative_import_to_a_pack_file_is_followed_not_counted(tmp_path):
    files = {"custom/work.py": "from .http import fetch\nfrom . import tools\n" + RUN,
             "custom/http.py": "def fetch():\n    return 1\n", "custom/tools.py": "x = 2\n"}
    r = make(tmp_path, files)
    assert r.ok, r.errors   # custom/http.py is a Pack file (D-5), not the http module
    r = make(tmp_path, {"custom/work.py": "from .http import fetch\n" + RUN}, name="b")
    assert not r.ok and errs(r)[0][1].endswith(": http")   # nothing to resolve: counted as in 0.11.0
    r = make(tmp_path, {"custom/work.py": "from .tools import t\n" + RUN, "custom/tools.py": f"import {NET}\nt = 1\n"}, name="c")
    assert not r.ok and "custom/work.py → custom/tools.py이" in errs(r)[0][1]


def test_cycles_are_safe(tmp_path):
    files = {"custom/work.py": "from custom import a\n" + RUN, "custom/a.py": "from custom import b\n", "custom/b.py": f"from custom import a\nimport {NET}\n"}
    r = make(tmp_path, files)
    assert not r.ok and len(r.errors) == 1 and "custom/a.py → custom/b.py이" in r.errors[0]["message"]


def test_validate2_custom_rule(tmp_path):
    r = make(tmp_path, {"custom/work.py": RUN, "custom/checks.py": f"import {NET}\n\ndef ok(value):\n    return True\n"}, rule=True)
    assert not r.ok
    assert errs(r) == [("graphs/g/nodes/1/params/rules/0",
                        f"custom/checks.py이 망 모듈을 import합니다(Pack 코드는 망을 직접 쓰지 않습니다 — provider · store는 packrt가 부릅니다): {NET}")]


def test_kept_rules(tmp_path):
    r = make(tmp_path, {"custom/work.py": "import subprocess\n" + RUN})
    assert r.ok and [w["message"] for w in r.warnings] == ["side_effect: none인데 프로세스 모듈을 씁니다: ['subprocess']"]   # PRES-K03
    r = make(tmp_path, {"custom/work.py": f"import packrt\nimport {NET}\n" + RUN}, name="b")
    msgs = [m for _, m in errs(r)]
    assert len(msgs) == 2 and "LLMOps 실행 모듈을 import합니다" in msgs[0] and "망 모듈을 import합니다" in msgs[1]   # PRES-K02 then K01


def test_dynamic_import_is_not_seen(tmp_path):
    r = make(tmp_path, {"custom/work.py": f"import importlib\nmod = importlib.import_module('{NET}')\n" + RUN})
    assert r.ok   # a known limit (GAP-003): only static imports are checked
