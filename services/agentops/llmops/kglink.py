"""D-588 (dev-2 · llmops-2): a graph the deploy side's gate let through changes the Pack revision, so the deployment's new Runs carry a
revision no version here has. With LLMOPS_KG_LINK=<project>|<deployment id> each applied graph of the signal folder becomes the next version:
the Pack at that tag (git archive, read only) goes through the usual registration, and one decision(deploy_approved, decided_by kg-gate) with
its ledger row is written — a row bound to an approval, so the bootstrap rule (EV-049) stays as it is. The gate is the deploy side's own step
and leaves no status file: such an approval reads as switched at the signal's time. A tag is done once (<data dir>/kg-links.json)."""
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from . import deploying, kgdiff, kgsignals

DONE = "kg-links.json"
BY = "kg-gate"
NO_GATE = "예외 없음 · 관문 안 돎"   # D-637 F7: the note of a version from an approval with no new exception (progress says it)
LABELS = "ui-labels/state-labels.json"   # outside pack/ in the Pack repository
VALUE_OPS = ("set_option", "set", "unset")
GRAPH_FILE = "templates/graph/current.json"   # the one file a graph tag changes (kg_apply)


def pending(versions, done):
    tags = [v["version"] for v in versions if v.get("version")]
    return [t for t in tags[1:] if t not in done]


def pull(repo, tag, dest):
    """The Pack folder at a tag into dest (its files directly under it), and the Pack's label file beside dest."""
    if not kgdiff.TAG.match(str(tag)):
        raise ValueError(f"태그가 아님 · {tag}")
    r = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", f"refs/tags/{tag}", "pack"], capture_output=True, timeout=60)
    if r.returncode:
        raise ValueError(f"Pack을 못 읽음 · {tag}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        for m in t.getmembers():
            if m.name.startswith("pack/"):
                m.name = m.name[len("pack/"):]
                t.extract(m, dest, filter="data")
    r = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", f"refs/tags/{tag}", LABELS], capture_output=True, timeout=60)
    if not r.returncode:   # D-597: read beside the work folder (app.label_file); a tag without it leaves the one there
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
            t.extractall(dest.parent, filter="data")


def progress(evidence):
    """deploying.progress as if the status files said tagged · switched — the gate's approval has none."""
    k = evidence["kg_gate"]
    return deploying.progress({"stage": "tagged", "tag": k["tag"]}, {"stage": "switched", "updated_at": k["at"], "detail": f"{k.get('note') or '관문 통과'} · 그래프 {k['tag']}"})


def graph_only(a, b):
    """D-597: True when two Pack versions differ in nothing but the graph — every other file, the config and the data bindings as they were."""
    pa, pb = a.get("pack") or {}, b.get("pack") or {}
    fa, fb = pa.get("raw_files"), pb.get("raw_files")
    if not fa or not fb or pa.get("id") != pb.get("id"):
        return False
    rest = lambda files: {k: v for k, v in files.items() if k != GRAPH_FILE}   # noqa: E731
    return rest(fa) == rest(fb) and pa.get("config_sha256") == pb.get("config_sha256") and pa.get("bindings") == pb.get("bindings")


def line(rels, rel, links):
    """D-597: the versions just before rel that read as one with it, newest first — each step a linked version (links: tag → release id,
    kg-links) whose files say only the graph changed. kg-links alone is not enough: a tag can also carry code (g9 → g10). D-687: a version
    with no tag that differs in more than the graph is one this tag was not made on (a person's node edit): it is passed over."""
    mine = set(links.values())
    ids = [r["id"] for r in rels]
    out, cur = [], rel
    for prev in reversed(rels[:ids.index(rel["id"])] if rel["id"] in ids else []):
        if cur["id"] not in mine:
            break
        if graph_only(prev, cur):
            out.append(prev)
            cur = prev
        elif prev["id"] in mine:
            break
    return out


def joined(rels, rel, links):
    """D-602: every version joined to rel by line's rule, both ways — the ones before it, then the ones after it, nearest first."""
    mine = set(links.values())
    ids = [r["id"] for r in rels]
    out, cur = line(rels, rel, links), rel
    for nxt in rels[ids.index(rel["id"]) + 1:] if rel["id"] in ids else []:
        same = graph_only(cur, nxt)
        if nxt["id"] in mine and same:
            out.append(nxt)
            cur = nxt
        elif nxt["id"] in mine or same:
            break                                # D-687: only an untagged version that differs in more than the graph is passed over
    return out


def running_revision(ledger_row):
    return (ledger_row or {}).get("pack_revision")


def rebase_target(revision, joined_versions, lines):
    """D-620: the joined version running at `revision`, when the change only sets values — no file written (a graph line made on the older
    graph would undo what the newer one learned), no node moved. None otherwise: the request goes as it is and the receiver decides."""
    ops = [o for x in lines or [] for o in x.get("ops") or []]
    if not ops or any(o.get("op") not in VALUE_OPS for o in ops):
        return None
    return next((r for r in joined_versions if (r.get("pack") or {}).get("revision") == revision), None)


def _links(data_dir):
    f = Path(data_dir) / DONE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def line_of(data_dir, rels, rel):
    return line(rels, rel, _links(data_dir))


def joined_of(data_dir, rels, rel):
    return joined(rels, rel, _links(data_dir))


class DataReleaseDiffers(ValueError):
    pass


_said = set()


def say_once(data_dir, tag, kind):
    """D-617: a tag that keeps failing is looked at every few seconds now — its line goes to the log once."""
    key = (str(data_dir), tag, kind)
    if key in _said:
        return False
    _said.add(key)
    return True


def run(_settings, store, register, fields, *, config_of, running_drs, signal_dir, repo, data_dir, deployment_id, runtime_version, key_file=None):
    """One pass: the new tags become versions. config_of() → the config file the deployment runs under; running_drs() → the data release of
    the running server (None when it cannot be asked); register(tag, folder) → the release; fields(folder, config) → admin.registration's
    values. A row whose data release is not the running one refuses every Run (deploy 463), so nothing is made then. A failure is said by
    its kind and the tag stays for the next pass. Returns how many were linked."""
    f = Path(data_dir) / DONE
    done = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    versions = kgsignals.read(signal_dir)["versions"]
    n = 0
    for tag in pending(versions, done):
        at = next(v.get("at") for v in versions if v.get("version") == tag)
        folder = Path(data_dir) / "kg-tags" / tag
        try:
            pull(repo, tag, folder)
            shutil.copyfile(config_of(), folder / "execution-config.yaml")
            if key_file and not (folder / ".env").exists():
                (folder / ".env").symlink_to(key_file)   # a path only: the Pack runtime reads it (PACKRT_ENV_FILE)
            pack_id, revision, drs, bindings, config_sha, (mrs, mbind) = fields(folder, folder / "execution-config.yaml")
            if drs != running_drs():
                raise DataReleaseDiffers(tag)
            rel = register(tag, folder)
            plain = next(bool(v.get("no_gate")) for v in versions if v.get("version") == tag)   # D-637 F7: no new exception · no gate
            did = store.add_deploy_approval(deployment_id=deployment_id, decided_by=BY, reason=f"그래프 {tag} {'이력만 · 관문 안 돎' if plain else '관문 통과'}",
                                            evidence={"release": rel["id"], "version": rel.get("name"), "revision": revision,
                                                      "kg_gate": {"tag": tag, "at": at, **({"note": NO_GATE} if plain else {})}})
            store.register_deployment(decision_id=did, deployment_id=deployment_id, pack_id=pack_id, pack_revision=revision, data_release_id=drs,
                                      release_bindings=bindings, runtime_version=runtime_version, config_sha256=config_sha, pack_tag=tag,
                                      model_release_id=mrs, model_bindings=mbind)
        except Exception as e:   # noqa: BLE001 - the next pass tries again; the kind only
            if say_once(data_dir, tag, type(e).__name__):
                print(f"llmops 그래프 버전 잇기 건너뜀 — {tag} · {type(e).__name__}", file=sys.stderr, flush=True)
            break
        done[tag] = rel["id"]
        f.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        n += 1
    return n
