"""Demo fake answers (EV-022 E-S-10, CHG-119, S-10): the fake provider answers an Agent Pack llm node with the response kept in
the task10-pack answers file (format task10-pack/fake-answers@1: revisions[<revision>].cases[<case id>][<llm node>].response).

The file path comes from LLMOPS_FAKE_ANSWERS (outside the product), else <repo parent>/task10-pack/fake-answers/task10-holdout.json
when that file exists. The file is only read, once per path. The boundary is the revision: a revision that is in the file is
answered only from the file — a case or node the file does not have is refused (AnswerMissing), nothing is made up; a revision
that is not in the file (another Pack, a LangGraph release, an edited version) gets None here and the fake provider keeps its
old answers (PRES-114, PRES-121).

Which llm node a call comes from: packrt sends only the prompt, so the node is found from the prompt — first the file's own
prompt_sha256 of that revision and case (exact when the data releases are the same), else the llm node of the running revision
whose prompt template's fixed text all appears, in order, in the prompt (packrt fills only {state...}/{item...} lookups in it)."""
import hashlib
import json
import os
import threading
from pathlib import Path

from packrt.ir import load_pack
from packrt.run.templates import REF

FORMAT = "task10-pack/fake-answers@1"
ENV = "LLMOPS_FAKE_ANSWERS"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "task10-pack" / "fake-answers" / "task10-holdout.json"


class FakeAnswersError(ValueError):
    """The answers file cannot be used (missing at the set path, unreadable, or not the expected format)."""


class AnswerMissing(LookupError):
    """A call of a revision that is in the file, for a case or llm node the file does not have."""


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_format(doc, where="가짜 응답 파일"):
    """Raises FakeAnswersError unless doc is task10-pack/fake-answers@1 with string responses."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        got = doc.get("format") if isinstance(doc, dict) else type(doc).__name__
        raise FakeAnswersError(f"{where}: 형식이 {FORMAT}가 아닙니다(받은 것: {got!r})")
    revs = doc.get("revisions")
    if not isinstance(revs, dict) or not revs:
        raise FakeAnswersError(f"{where}: revisions가 비었거나 객체가 아닙니다")
    for rev, rv in revs.items():
        cases = rv.get("cases") if isinstance(rv, dict) else None
        if not isinstance(cases, dict):
            raise FakeAnswersError(f"{where}: revision {rev[:19]}…의 cases가 객체가 아닙니다")
        for cid, nodes in cases.items():
            if not isinstance(nodes, dict):
                raise FakeAnswersError(f"{where}: {rev[:19]}… case {cid}가 객체가 아닙니다")
            for node, entry in nodes.items():
                if not isinstance(entry, dict) or not isinstance(entry.get("response"), str):
                    raise FakeAnswersError(f"{where}: {rev[:19]}… case {cid} node {node}에 글 response가 없습니다")
    return doc


class FakeAnswers:
    """One read answers file."""

    def __init__(self, doc, path=None):
        self.doc, self.path = check_format(doc), path
        self.revisions = doc["revisions"]

    @classmethod
    def read(cls, path):
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise FakeAnswersError(f"가짜 응답 파일을 읽을 수 없습니다: {path} ({e.strerror or e})")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise FakeAnswersError(f"가짜 응답 파일이 JSON이 아닙니다: {path} ({e})")
        out = cls(doc, path)
        out.sha256 = hashlib.sha256(raw).hexdigest()
        return out

    def has(self, revision):
        return revision in self.revisions

    def answer(self, revision, case_id, node):
        """The file's response for (revision, case, llm node); None for a revision the file does not have."""
        rv = self.revisions.get(revision)
        if rv is None:
            return None
        label = rv.get("label") or revision[:19]
        cases = rv["cases"]
        if case_id is None or str(case_id) not in cases:
            raise AnswerMissing(f"가짜 응답 파일에 없는 case입니다 — revision {label}, case {case_id or '(평가 case 아님)'} "
                                f"(파일의 case: {', '.join(cases) or '없음'}); 응답을 지어내지 않습니다")
        entry = cases[str(case_id)].get(node) if node is not None else None
        if entry is None:
            have = ", ".join(cases[str(case_id)]) or "없음(이 case는 llm node를 지나지 않음)"
            raise AnswerMissing(f"가짜 응답 파일에 없는 llm node입니다 — revision {label}, case {case_id}, node {node or '(알 수 없음)'} "
                                f"(파일의 node: {have}); 응답을 지어내지 않습니다")
        return entry["response"]

    def node_by_prompt_hash(self, revision, case_id, prompt):
        """The llm node whose recorded prompt_sha256 equals this prompt's (same revision and case), else None."""
        nodes = ((self.revisions.get(revision) or {}).get("cases") or {}).get(str(case_id)) or {}
        h = _sha(prompt)
        hits = [n for n, e in nodes.items() if e.get("prompt_sha256") == h]
        return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------------------------------------ the file, read once per path
_lock = threading.Lock()
_cache = {}   # resolved path -> FakeAnswers


def answers_path():
    """The set path (LLMOPS_FAKE_ANSWERS), else the default next to this repository when it exists, else None."""
    env = os.environ.get(ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return DEFAULT_PATH if DEFAULT_PATH.is_file() else None


def load(path=None):
    """The answers file (read once per path), or None when no path is set and the default file is not there.
    A set path that cannot be read, or a file in another format, raises FakeAnswersError."""
    path = Path(path) if path is not None else answers_path()
    if path is None:
        return None
    key = str(path.resolve())
    with _lock:
        if key not in _cache:
            _cache[key] = FakeAnswers.read(path)
        return _cache[key]


def reset():
    """Forget the read files (tests)."""
    with _lock:
        _cache.clear()
        _templates.clear()


def answer(revision, case_id, node):
    """The file's response for (revision, case id, llm node). None when there is no file or the revision is not in it (the
    old fake answer stays); AnswerMissing when the revision is in the file but the case or node is not."""
    fa = load()
    return fa.answer(revision, case_id, node) if fa is not None else None


# ------------------------------------------------------------------------------------------- which llm node sent this prompt
_templates = {}   # (pack folder, revision) -> {llm node id: [fixed text pieces]}


def llm_templates(pack_dir, revision=None):
    """{llm node id: fixed text pieces of its prompt template} of the Pack at pack_dir (read only, cached per folder+revision)."""
    key = (str(Path(pack_dir).resolve()), revision)
    with _lock:
        if key in _templates:
            return _templates[key]
    res = load_pack(pack_dir)
    if not res.ok:
        raise FakeAnswersError(f"가짜 응답: Pack을 읽을 수 없습니다({pack_dir})")
    out = {}
    for g in res.doc["graphs"].values():
        for n in g["nodes"]:
            if str(n.get("kind", "")).startswith("llm@") and (n.get("params") or {}).get("prompt"):
                try:
                    text = (Path(pack_dir) / n["params"]["prompt"]).read_text(encoding="utf-8")
                except OSError as e:
                    raise FakeAnswersError(f"가짜 응답: node {n['id']}의 프롬프트를 읽을 수 없습니다({e.strerror or e})")
                out[n["id"]] = [p for p in REF.split(text)[::3] if p]   # REF has two groups: text, root, path, text, ...
    with _lock:
        _templates[key] = out
    return out


def _fits(pieces, prompt):
    at = 0
    for p in pieces:
        at = prompt.find(p, at)
        if at < 0:
            return False
        at += len(p)
    return True


def node_for_prompt(templates, prompt):
    """The one llm node whose template's fixed text all appears in order in prompt; None when none or more than one fit."""
    hits = [n for n, pieces in templates.items() if pieces and _fits(pieces, prompt)]
    if len(hits) > 1:   # one template's text inside another's: the longest fixed text is the more specific one
        best = max(sum(map(len, templates[n])) for n in hits)
        hits = [n for n in hits if sum(map(len, templates[n])) == best]
    return hits[0] if len(hits) == 1 else None


def applies(revision, case_id, releases, suite=None):
    """Answer 019-04 (orchestrator, S-10 · PRES-114 · 121): the file answers a Run call only when all hold — the Run's Pack revision
    is in the file, the Run's suite is the file's suite (meta.suite) and the case is listed in meta.cases, and the Run's data
    releases equal meta.data_releases exactly (the prompts match only with the same data). Any other Run keeps the old fake
    answers. False when there is no file; a set path that cannot be read raises FakeAnswersError."""
    fa = load()
    if fa is None or not fa.has(revision):
        return False
    meta = fa.doc.get("meta") or {}
    want = meta.get("data_releases") or {}
    file_suite = str(meta.get("suite") or "").rstrip("/").split("/")[-1]   # 'pack/evals/task10-holdout' → task10-holdout
    return case_id is not None and str(case_id) in (meta.get("cases") or []) and bool(want) and dict(releases or {}) == dict(want) \
        and bool(file_suite) and suite == file_suite   # the same suite (review E-S-10-R1-01: evals/ is outside the Pack revision)


def respond(revision, case_id, prompt, node=None, pack_dir=None):
    """The fake provider's file answer for one Run call: None when there is no file or its revision is not in the file;
    otherwise the file's response for the call's llm node (given, else found from the prompt), or AnswerMissing."""
    fa = load()
    if fa is None or not fa.has(revision):
        return None
    if node is None:
        node = fa.node_by_prompt_hash(revision, case_id, prompt)
    if node is None and pack_dir is not None:
        node = node_for_prompt(llm_templates(pack_dir, revision), prompt)
    return fa.answer(revision, case_id, node)
