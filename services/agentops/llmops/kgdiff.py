"""D-581 (dev-2 · llmops-2): what changed between two graphs of the version table. The deploy side tags each graph it puts in the Pack
repository; the file at a tag is read with `git show` (LLMOPS_KG_PACK_REPO, read only). Nothing here registers a version."""
import json
import re
import subprocess

from . import graphrel

GRAPH_FILE = "pack/templates/graph/current.json"
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COUNTS = (("예외 관계", "exception_relations"), ("절", "sections"), ("이력", "records"))
LINKS = (("원인", "causes"), ("후보", "candidate"), ("조치 연결", "remedied_by"))


class DiffError(ValueError):
    pass


def graph_at(repo, tag):
    if not repo:
        raise DiffError("그래프 저장소 자리가 설정되지 않음")
    if not TAG.match(str(tag)):
        raise DiffError(f"그래프 파일을 못 읽음 · {tag}")
    r = subprocess.run(["git", "-C", str(repo), "show", f"refs/tags/{tag}:{GRAPH_FILE}"], capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise DiffError(f"그래프 파일을 못 읽음 · {tag}")
    return json.loads(r.stdout)


def _rows(names, a, b):
    return [{"name": n, "before": len(a.get(k) or []), "after": len(b.get(k) or [])} for n, k in names]


def _rel(r):
    return {"id": r.get("id"), "where": r.get("where") or graphrel.where_text(r.get("when"))}


def diff(repo, before, after):
    a, b = graph_at(repo, before), graph_at(repo, after)
    old = {r.get("id"): r for r in a.get("exception_relations") or []}
    rels = b.get("exception_relations") or []
    return {"before": before, "after": after, "counts": _rows(COUNTS, a, b), "links": _rows(LINKS, a, b),
            "new": [_rel(r) for r in rels if r.get("id") not in old],
            "changed": [_rel(r) for r in rels if r.get("id") in old and r != old[r.get("id")]]}


def before_of(versions, version):
    """The graph just before a version in the table; (False, None) when it is not a row, (True, None) when nothing is before it."""
    tags = [v["version"] for v in versions if v.get("version")]
    if version not in tags:
        return False, None
    i = tags.index(version)
    return True, tags[i - 1] if i else None
