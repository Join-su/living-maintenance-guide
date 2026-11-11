"""The Pack repository's fake answer rule (EV-SUI-01 CHG-S05; task10-pack answer 019-01 Q1 A · Q2, D-155, answer 022-01 K-1 · K-2).

For a Pack release Run whose llm call has no answer in the demo answers file, the fake provider asks the rule module of the Pack
repository (`tools/fake/rule.py`, format task10-pack/fake-rule@1, standard library only) instead of making up "[가짜] …" text, and
keeps the module's limits (prompt_unknown · passthrough · refused …) for the case.

Where the rule file is (fixed places only — never next to a registered Pack folder, K-1):
  1. LLMOPS_FAKE_RULE
  2. else next to the file LLMOPS_FAKE_ANSWERS names: <that folder>/../tools/fake/rule.py
  3. else the fixed neighbour default <llmops repository>/../task10-pack/tools/fake/rule.py (fake_answers.DEFAULT_PATH's root)
It applies only to Runs of the rule repository's own Pack: the id in <rule repository>/pack/pack.yaml (pack.id); a missing or
unreadable pack.yaml, or no id, applies to nothing. The id is checked before the file is run, so the module's top-level code runs
only for that Pack's Runs.
rule_unavailable (K-2): a place that must hold the rule (1 or 2 set) where the file is missing, cannot be loaded, or the call fails
with OSError (its known-prompts list missing; any other error of the rule other than its refusal counts the same) — the call
answers as before and the limit says so. A missing file at the default place (3) is silent (as today).
Loading: the source is read and compiled with its own path (no bytecode, no __pycache__, nothing put on sys.path); __file__ is
the real path so the module finds its own repository's files. One module per file content.
Trust boundary: this runs Pack-repository code in the server process (outside the sandbox) — narrowed to these three places, the
rule repository's Pack id, a read-only checkout and no bytecode (answer 022-01, recorded risk).
Test harness: pytest turns it off (tests/conftest.py sets OFF), as it replaces the real folder chooser; V-FAKERULE turns it on."""
import hashlib
import os
import sys
import threading
import types
from pathlib import Path

import yaml

RULE_ENV, ANSWERS_ENV = "LLMOPS_FAKE_RULE", "LLMOPS_FAKE_ANSWERS"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "task10-pack" / "tools" / "fake" / "rule.py"
UNAVAILABLE = "rule_unavailable"
OFF = False   # test harness only: tests/conftest.py sets True for every test (V-FAKERULE sets it back)
RUNS = 0      # rule code runs so far — loads and calls (the test harness counts that no test outside V-FAKERULE runs one)
_lock = threading.Lock()
_modules = {}   # sha256 of the source -> module
_failed = set()   # sha256 of sources that could not be loaded (not tried again: one load per file content)


def locate(env=None):
    """(path, required) of the rule file, or None when the rule is off. required: one of the two settings names the place."""
    if OFF:
        return None
    env = os.environ if env is None else env
    if (env.get(RULE_ENV) or "").strip():
        return Path(env[RULE_ENV].strip()).expanduser(), True
    if (env.get(ANSWERS_ENV) or "").strip():
        return Path(env[ANSWERS_ENV].strip()).expanduser().parent / ".." / "tools" / "fake" / "rule.py", True
    return DEFAULT_PATH, False


def pack_id_of(rule_path):
    """The Pack id of the rule's repository (<repo>/pack/pack.yaml pack.id), or None."""
    try:
        doc = yaml.safe_load((Path(rule_path).parent / ".." / ".." / "pack" / "pack.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    pid = ((doc or {}).get("pack") or {}).get("id") if isinstance(doc, dict) else None
    return pid if isinstance(pid, str) and pid else None


def _load(path):
    global RUNS
    src = Path(path).read_bytes()
    key = hashlib.sha256(src).hexdigest()
    with _lock:
        if key in _modules:
            return _modules[key]
        if key in _failed:
            raise ImportError("가짜 규칙 파일을 불러올 수 없습니다(이미 실패한 내용)")
        name = f"llmops_fake_rule_{key[:12]}"
        mod = types.ModuleType(name)
        mod.__file__ = str(Path(path).resolve())
        sys.modules[name] = mod   # dataclasses and the like look the module up by name; nothing is written anywhere
        RUNS += 1
        try:
            exec(compile(src, mod.__file__, "exec", dont_inherit=True), mod.__dict__)
        except BaseException:
            sys.modules.pop(name, None)
            _failed.add(key)
            raise
        _modules[key] = mod
        return mod


def for_pack(pack_id, env=None):
    """The rule module for a Run of this Pack, UNAVAILABLE, or None (no rule: answer as before, no limit)."""
    loc = locate(env)
    if loc is None or not pack_id:
        return None
    path, required = loc
    rid = pack_id_of(path)
    if not path.is_file():
        return UNAVAILABLE if required and (rid is None or rid == pack_id) else None
    if rid != pack_id:
        return None
    try:
        return _load(path)
    except (Exception, SystemExit):  # noqa: BLE001 - a rule file that cannot be loaded: the call answers as before
        return UNAVAILABLE if required else None


def answer(module, prompt, required=True):
    """(text, limits), or (None, [rule_unavailable]) when the call cannot be made (OSError: its known-prompts list is missing —
    the caller answers as before); at the default place (required False) that failure is silent: (None, []).
    A refusal (the module's FakeRefused) is raised as RuleRefused with its reason and limits."""
    global RUNS
    RUNS += 1
    refused = getattr(module, "FakeRefused", None)
    try:
        text, limits = module.respond_text(prompt)
    except Exception as e:  # noqa: BLE001 - the rule module's own errors never reach the caller as they are
        if isinstance(refused, type) and isinstance(e, refused):
            raise RuleRefused(str(e) or "refused", list(getattr(e, "limits", None) or ["refused"])) from None
        return None, [UNAVAILABLE] if required else []   # OSError (K-2), or any other failure of the rule: answer as before
    if not isinstance(text, str):
        return None, [UNAVAILABLE] if required else []
    return text, [str(x) for x in (limits or [])]


class RuleRefused(Exception):
    def __init__(self, message, limits):
        super().__init__(message)
        self.limits = limits
