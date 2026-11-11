"""Deploy approval of an Agent Pack version (EV-022 CHG-113 · 119; S-4 · S-6 · S-16 revised · S-20; manual p.28 · 29; instruction
019f). LLMOps writes only into its own data folder: the deploy_approved decision, the approved-change@2 request file and — once the
Pack session reports `tagged` — its own deployment ledger row. The Pack session (task10-pack) and the deploy session
(llmopsagent-deploy) write their status files in their own repositories; LLMOps only reads them.

Status files share one shape {"id","version","revision","stage","detail","commit","tag","deployment_id","updated_at"}:
  task10-pack  <repo>/status/approved-changes/<change id>.json   requested · applied · tagged · failed
  deploy       <repo>/status/deployments/<change id>.json         registered · switched · failed
The four steps of the approval window (answer 019-A E-S-00, instruction 019f):
  1 요청 파일          done once the request file exists
  2 Pack 받아 넣기 · 태그  requested (dry-run, waiting, apply check) and applied → in progress, tagged → done (H8)
  3 배포단 등록        registered → done
  4 운영 전환          switched → done
A failed file marks the step it belongs to (Pack side: step 2, deploy side: step 3, or 4 when the deploy side had registered).
A file with `"dry_run": true` (a rehearsal) counts as absent everywhere; its step row only says '시험 실행(dry-run) — <stage>' (EV-029 CHG-135)."""
import json
import re
import shlex
import time
from datetime import datetime
from pathlib import Path

ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")   # an alias id like owner-01: no name, no address, no phone number (decision contract)
SCHEMA = Path(__file__).parent / "schemas" / "approved-change@2.schema.json"   # copy of task10-pack docs/approved-change@2.schema.json (602e84b)
FORMAT = "task10-pack/approved-change@2"   # EV-039 CHG-197 (D-198): no tag — the Pack's apply tool takes the next free v<n>;
# approved-change@1.schema.json stays for reading the request files written before
STEPS = ("요청 파일", "Pack 받아 넣기 · 태그", "배포단 등록", "운영 전환")
ORDER = {"requested": 1, "applied": 2, "tagged": 3, "registered": 4, "switched": 5}
FIELDS = ("id", "version", "revision", "stage", "detail", "commit", "tag", "deployment_id", "updated_at")


def alias_error(who):
    """None when `who` is an alias id, else the reason (the rule the removed EV-021 decision form used)."""
    if isinstance(who, str) and ALIAS_RE.match(who) and not who.isdigit():
        return None
    return "결정자는 별명 ID(영문 소문자로 시작, 소문자·숫자·-·_, 2~32자, 예 owner-01)로 적습니다 — 실명·연락처는 넣지 않습니다"


def default_deployment_id(pack_id, version):
    """S-20: '<pack>@<version in lower case>-pg-local' (the demo's task10@v1-pg-local); the approval window may change it."""
    return f"{pack_id}@{version.lower()}-pg-local"


def window_deployment_id(op_rows, pack_id, version):
    """EV-071 (D-443, decision contract 1684 changed): the deploy window's default id — the deployment the 운영 version runs as now
    (its last tagged ledger row of this Pack); '<pack>@<version>-pg-local' when there is none. A blank POST keeps the old rule."""
    mine = [r for r in op_rows or () if r.get("pack_tag") and r.get("pack_id") == pack_id]
    return mine[-1]["deployment_id"] if mine else default_deployment_id(pack_id, version)


def target_of(deployment_id):
    """The deploy target of a deployment id: '<pack>@v<n>-<target>' → '<target>' (task10@v1-pg-local → pg-local); None otherwise."""
    m = re.match(r"^[^@]+@v\d+-(.+)$", deployment_id or "")
    return m.group(1) if m else None


def target_in(deployment_id, rows):
    """EV-067 (D-425, 019h narrowed): the deploy target of a deployment id among the ledger rows — target_of's '<pack>@v<n>-<target>'
    form, else a version-free '<pack>@<target>' id (c1a15@dev-local — the row the deploy side switched to outside LLMOps) is its own
    target only when a tagged row has that very id; None otherwise (an untagged dev row stays no target)."""
    t = target_of(deployment_id)
    if t:
        return t
    if deployment_id and "@" in deployment_id and any(r.get("deployment_id") == deployment_id and r.get("pack_tag") for r in rows):
        return deployment_id
    return None


def same_target(deployment_id, target):
    """EV-067: a deployment id belongs to the target (target_in's value) — by target_of, or as the version-free id itself."""
    return bool(target) and (target_of(deployment_id) == target or deployment_id == target)


def target_base(rows, pack_id, deployment_id):
    """Instruction 019h: the ledger row of a new deployment carries what the deployment really runs with — the data bundle, run
    bindings and execution config of the latest operational row of the same Pack and the same deploy target (the v0 row's
    principle), not the bindings the version was evaluated with. None when that target has no operational row yet."""
    target = target_in(deployment_id, rows)   # EV-067: a version-free id with its tagged row too
    mine = [r for r in rows if r.get("pack_id") == pack_id and r.get("pack_tag") and same_target(r.get("deployment_id"), target)]
    if not mine:
        return None
    r = mine[-1]
    binds = r["release_bindings"]
    return {"deployment_id": r["deployment_id"], "data_release_id": r["data_release_id"],
            "release_bindings": json.loads(binds) if isinstance(binds, str) else binds, "config_sha256": r["config_sha256"], "runtime_version": r["runtime_version"]}


def change_approved(cid, drs, approvals, drs_of):
    """D-484 덧 4: a developer change (status-file id) already approved for this data bundle — an approval of the same id whose version
    had another drs (written on it since, else read from its release) is not one: the same revision with another execution config is
    approved again."""
    for x in approvals:
        ev = x.get("evidence") or {}
        if ev.get("change_id") != cid:
            continue
        theirs = ev["drs"] if "drs" in ev else drs_of(ev.get("release"))
        if theirs == drs:
            return True
    return False


def model_line(models):
    """EV-044 CHG-220: (model_release_id, model_bindings) of an execution config's models ({name: {release_id, manifest_sha256, …}},
    as packrt binds them and a release record keeps them) — packrt's `mrs:` id over {name: release_id} (None without models) and
    {name: {release_id, sha256}} ({} without models)."""
    from packrt.trace import model_release_id
    models = models or {}
    return (model_release_id({k: v.get("release_id") for k, v in models.items()}),
            {k: {"release_id": v.get("release_id"), "sha256": v.get("manifest_sha256")} for k, v in models.items()})


# ---------------------------------------------------------------- EV-051 W2-4 (CHG-W2-22 ~ 24, D-352 · D-355): a wheel version
# that changed a vector DB. The ledger row of its approval carries that store's version instead of the target's (019h exception,
# both approval paths) and the build hash the deploy side checks before it builds (the store field's build_sha256 key).
def pack_copy_of(releases, pack_id, revision):
    """CHG-W2-22: a Pack copy folder of this pack id and revision among release records — the current copy or a kept revision (the
    same revision is the same content, any one does). None when LLMOps has none."""
    for rel in releases:
        pk = rel.get("pack") or {}
        if pk.get("id") != pack_id:
            continue
        for rev, folder in [(pk.get("revision"), pk.get("copy")), *[(x.get("revision"), x.get("copy")) for x in pk.get("revisions") or []]]:
            if rev == revision and folder and (Path(folder) / "pack.yaml").is_file():
                return Path(folder)
    return None


def pack_view(folder, graph):
    """(the Pack's declared stores, {store: (recipe sha256, {provider, model})} of the retrieve@2 nodes of one graph — None when the
    Pack has no such graph — and whether that graph has a retrieve@2 node). The Pack is loaded and checked once by packrt; a Pack
    that does not load raises ValueError."""
    from packrt import ir
    from packrt.index import index_recipe_of, index_recipe_sha256
    res = ir.load_pack(Path(folder))
    if not res.ok:
        raise ValueError("Pack을 불러올 수 없음")
    doc = res.doc
    declared = set((doc.get("stores") or {}).keys())
    g = (doc.get("graphs") or {}).get(graph)
    if g is None:
        return declared, None, False
    out, has = {}, False
    for n in g.get("nodes") or []:
        if n.get("kind") != "retrieve@2":
            continue
        has = True
        st = (n.get("params") or {}).get("store")
        if st and st not in out:   # packrt keeps one recipe per store in a graph
            emb = ((((doc.get("stores") or {}).get(st) or {}).get("index") or {}).get("embedding")) or {}
            out[st] = (index_recipe_sha256(index_recipe_of(doc, graph, n["id"])), {"provider": emb.get("provider"), "model": emb.get("model")})
    return declared, out, has


def index_changed_stores(target, mine):
    """CHG-W2-22: the stores whose vector DB this version changed against the Pack the deploy target runs — on the verdict Run's
    entrypoint graph, a store read by retrieve@2 in both whose recipe hash or embedding declaration (provider · model) differs. A
    store the target's Pack does not have is not one (019h as before). The target's Pack without that graph: every retrieve@2
    store here that the target's Pack declares. target · mine: pack_view answers."""
    declared, theirs, _ = target
    ours = mine[1] or {}
    if theirs is None:
        return sorted(s for s in ours if s in declared)
    return sorted(s for s in ours if s in theirs and ours[s] != theirs[s])


def index_of_run(run, stores, rel_id=None, revision=None):
    """CHG-W2-23: ({store: {release_id, sha256 (= manifest sha), build_sha256, embedding_mode}}, None) from the verdict Run's
    manifest, or (None, '<store>: <why>'). With rel_id · revision the Run must be this version's."""
    if not run:
        return None, f"{stores[0]}: 판정 Run 없음"
    if run.get("status") in ("대기", "실행 중"):
        return None, f"{stores[0]}: 판정 Run이 끝나지 않음"
    man = run.get("manifest") or {}
    if rel_id is not None and (run.get("rel") != rel_id or man.get("pack_revision") != revision):
        return None, f"{stores[0]}: 판정 Run이 이 버전의 Run이 아님"
    found = man.get("evidence_index_releases") or {}
    out = {}
    for s in stores:
        e = found.get(s)
        if not isinstance(e, dict) or not e.get("release_id"):
            return None, f"{s}: 판정 Run에 그 store 칸 없음"
        if not isinstance(e.get("build_sha256"), str) or len(e["build_sha256"]) != 64:
            return None, f"{s}: build_sha256 없음(packrt가 만든 version이 아님)"
        out[s] = {"release_id": e["release_id"], "sha256": e.get("manifest_sha256"), "build_sha256": e["build_sha256"],
                  "embedding_mode": (e.get("embedding") or {}).get("mode")}
    return out, None


def index_bindings(base, stores):
    """The ledger bindings of an approval with evidence.index: the target's, with the changed stores' fields swapped in."""
    return {**(base.get("release_bindings") or {}), **stores}


def index_overlay(base, picked):
    """CHG-W2-24 (D-352 ① · ②): evidence.index = {stores: {<store>: {release_id, sha256, build_sha256}}, data_release_id} — the
    data bundle packrt names for the target's bindings with the changed stores swapped."""
    from packrt.trace import data_release_id
    stores = {s: {"release_id": v["release_id"], "sha256": v["sha256"], "build_sha256": v["build_sha256"]} for s, v in picked.items()}
    binds = index_bindings(base, stores)
    return {"stores": stores, "data_release_id": data_release_id({k: (v or {}).get("release_id") if isinstance(v, dict) else v for k, v in binds.items()})}


def find_status(folder, revision):
    """EV-037 CHG-191: the Pack status files in this version's Pack status folder for the same revision — read with read_status, so the
    file's id is its name and its stage is known; a dry-run file counts as absent. The Pack id is the folder (the version's own Pack status
    folder) together with the revision (the Pack's content hash); the name prefix is not relied on. Read only."""
    out = []
    for p in sorted(Path(folder).glob("*.json")) if folder and Path(folder).is_dir() else []:
        doc = read_status(folder, p.stem)
        if doc and not doc.get("dry_run") and doc.get("revision") == revision:   # a rehearsal file counts as absent (EV-029 CHG-135)
            out.append(doc)
    return out


def read_status(folder, change_id):
    """One status file, or None when it is not there (yet) or cannot be read now — the next read tries again (019f-2)."""
    if not folder:
        return None
    p = Path(folder) / f"{change_id}.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("id") != change_id or doc.get("stage") not in (*ORDER, "failed"):
        return None
    out = {k: doc.get(k) for k in FIELDS}
    if doc.get("dry_run") is True:   # EV-029 CHG-135: task10-pack ci_steps.status_doc's "dry_run": bool — only a rehearsal file carries it,
        out["dry_run"] = True        # an ordinary file (absent or false) reads exactly as before (PRES-144)
    return out


def progress(pack_status, deploy_status, request_note=None):
    """The approval window's four steps from the two status files (either may be None). Returns {"stage", "failed", "reason",
    "tagged", "steps": [{"name", "state": done|active|wait|failed, "detail"}], "pack", "deploy"}."""
    # EV-029 CHG-135: a status file with dry_run true is absent for every calculation (steps, badge, band, lock, the S-20 ledger
    # row); only its own step row shows '시험 실행(dry-run) — <stage>' in progress, without ✓
    dry = {}
    if pack_status and pack_status.get("dry_run"):
        dry["pack"], pack_status = pack_status, None
    if deploy_status and deploy_status.get("dry_run"):
        dry["deploy"], deploy_status = deploy_status, None
    p, d = pack_status or {}, deploy_status or {}
    failed, reason, fail_step = False, None, None
    if d.get("stage") == "failed":
        failed, reason = True, d.get("detail")
        fail_step = 4 if "전환" in (d.get("detail") or "") else 3
    elif p.get("stage") == "failed":
        failed, reason, fail_step = True, p.get("detail"), 2
    stage = d.get("stage") if d.get("stage") in ("registered", "switched") else p.get("stage") if p.get("stage") in ORDER else None
    level = ORDER.get(stage, 1)   # no status file yet: the request file exists, step 1 is done
    # H8 (answer 019-A merge 1, manual p.28 ②): ② is the Pack session's whole work — requested (dry-run, waiting, apply check)
    # and applied are ② in progress, tagged is ② done; no Pack status file yet is ② waiting
    states = ["done",
              "done" if level >= 3 else "active" if p.get("stage") in ("requested", "applied") else "wait",
              "done" if level >= 4 else "wait",
              "done" if level >= 5 else "wait"]
    if fail_step:
        states[fail_step - 1] = "failed"
    details = ["요청 파일을 만들었습니다", "", "", ""]
    if p.get("detail"):
        details[1] = p["detail"]
    elif request_note and not p and "pack" not in dry:   # EV-VPS-01 CHG-V07: no Pack status yet and the runner request was not taken
        details[1] = request_note
    if p.get("tag"):
        details[1] = f"태그 {p['tag']}" + (f" · 커밋 {str(p.get('commit'))[:8]}" if p.get("commit") else "")
    if d.get("detail"):
        details[2 if d.get("stage") == "registered" or fail_step == 3 else 3] = d["detail"]
    if level >= 4 and not details[2]:   # the deploy file keeps only its last stage: name the deployment it registered (p.29)
        details[2] = f"배포단 등록 {d.get('deployment_id') or ''}".strip()
    for who, doc in dry.items():   # the dry-run file's own step: Pack → ②, deploy registered → ③, switched (or a failed switch) → ④
        i = 1 if who == "pack" else 3 if doc.get("stage") == "switched" or (doc.get("stage") == "failed" and "전환" in (doc.get("detail") or "")) else 2
        if states[i] in ("wait", "active"):   # a real result already in that step stays as it is
            states[i], details[i] = "active", f"시험 실행(dry-run) — {doc.get('stage')}"
    out = {"stage": stage or "requested", "failed": failed, "reason": reason, "tagged": level >= 3,
           "steps": [{"name": n, "state": s, "detail": x} for n, s, x in zip(STEPS, states, details)], "pack": pack_status, "deploy": deploy_status}
    if dry:   # only with a dry-run file: the answer of an ordinary file keeps its shape (PRES-144)
        out["dry_run"] = {k: v.get("stage") for k, v in dry.items()}
    return out


def rollout_row(prog, d):
    """D-624 4번: a version's deploy approval as the screen reads it — the steps and the failure (progress) beside the decision.
    `reason` stays the decision's own reason (the window's '이유:'); the failure's reason is `fail_reason` (it was covered by it:
    '배포 실패(<the approval's reason>)')."""
    return {**prog, "fail_reason": prog.get("reason"), "decision_id": d["decision_id"], "deployment_id": d["subject_id"],
            "change_id": d["evidence"].get("change_id"), "request": d["evidence"].get("request"), "decided_by": d["decided_by"],
            "source": d["evidence"].get("source"), "reason": d["reason"],
            "at": time.strftime("%m-%d %H:%M", time.localtime(d["decided_at"]))}


ROLLBACK_STEPS = ("되돌리기 요청", "배포단 등록 확인", "운영 전환")


def epoch_of(iso):
    """A status file's ISO time (with or without zone; without = this machine's local time) as epoch seconds, or None when it
    cannot be read (EV-SUI-01 CHG-S01)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(iso)).timestamp() if iso else None
    except (ValueError, TypeError, OverflowError):
        return None


def rollback_progress(status):
    """EV-SUI-01 CHG-S02: the rollback window's three steps from the deploy side's status file named by the rollback id (None = not
    there yet). ① is done once the decision exists; ② registered; ③ switched. A failure is ③ when its detail names the switch
    ('전환'), else ②. A dry-run file counts as absent; only its own row says '시험 실행(dry-run) — <stage>'."""
    dry = status if status and status.get("dry_run") else None
    d = None if dry else (status or {})
    stage = d.get("stage") if d else None
    failed = stage == "failed"
    fail_step = (3 if "전환" in (d.get("detail") or "") else 2) if failed else None
    level = {"registered": 2, "switched": 3}.get(stage, 1)
    states = ["done", "done" if level >= 2 else "active" if not failed else "wait", "done" if level >= 3 else "wait"]
    if fail_step:
        states[fail_step - 1] = "failed"
        if fail_step == 3:
            states[1] = "done"
    details = ["결정을 저장했습니다", "", ""]
    if d and d.get("detail"):
        details[2 if stage == "switched" or fail_step == 3 else 1] = d["detail"]
    if dry:
        i = 2 if dry.get("stage") == "switched" else 1
        if states[i] in ("wait", "active"):
            states[i], details[i] = "active", f"시험 실행(dry-run) — {dry.get('stage')}"
    out = {"stage": stage or "requested", "failed": failed, "reason": d.get("detail") if failed else None, "done": stage == "switched",
           "updated_at": d.get("updated_at") if d else None,
           "steps": [{"name": n, "state": s, "detail": x} for n, s, x in zip(ROLLBACK_STEPS, states, details)]}
    if dry:
        out["dry_run"] = dry.get("stage")
    return out


ROLLBACK_FORMAT = "llmops/rollback-request@1"   # D-633: deploy 499 (the watcher's side) — same folder as the deploy request files


def rollback_request_doc(*, rollback_id, pack_id, target, from_, decided_by, reason, decided_at):
    """D-633 (deploy 499): the request file of one rollback approval, <data>/approved-changes/<rollback_id>.json. The demo watcher
    takes it (format · rollback_id = the file name · deployment_id · version · revision are the fields it needs), switches the
    running server to that revision (tag: the ledger row's when known, else null and the watcher finds it) and writes the status file
    named rollback_id (registered → switched · failed) that the rollback window reads. The other fields are kept as a record.
    target / from_: {version, release, revision, deployment_id, tag, commit}. No secret, no key path."""
    if not target.get("revision") or not target.get("deployment_id"):
        raise ValueError("되돌릴 버전의 revision · deployment가 없습니다")
    return {"format": ROLLBACK_FORMAT, "rollback_id": rollback_id, "deployment_id": target["deployment_id"], "version": target.get("version"),
            "revision": target["revision"], "release": target.get("release"), "tag": target.get("tag"), "commit": target.get("commit"),
            "pack_id": pack_id, "from_version": from_.get("version"), "from_release": from_.get("release"), "from_revision": from_.get("revision"),
            "from_deployment_id": from_.get("deployment_id"), "decided_by": decided_by, "reason": reason,
            "decided_at": datetime.fromtimestamp(decided_at).astimezone().isoformat(timespec="seconds")}


def rollback_command(rollback_id, target_version, target_deployment, from_deployment):
    """The one line the operator runs on the VPS (deploy README:87-89 form). The three build values are the deploy side's (its
    approval notice), so they stay placeholders; config and services follow the deploy repository's names (config/<v>-pg-vps.yaml,
    compose deploy-<pack>-<v>-pg-local): a target ending in '-local' becomes '-vps' for the config, else the config is a placeholder."""
    def parts(dep):
        m = re.match(r"^([^@]+)@(v\d+)-(.+)$", dep or "")
        return (m.group(1), m.group(2), m.group(3)) if m else (None, None, None)
    pack, v0, target = parts(target_deployment)
    _, v1, from_target = parts(from_deployment)
    config = f"config-live/{v0}-{target[:-len('-local')]}-vps.yaml" if v0 and target and target.endswith("-local") else "<v0 운영 설정>"
    service = f"deploy-{pack}-{v0}-{target}" if pack and v0 else "<되돌릴 서비스>"
    old = f"deploy-{pack}-{v1}-{from_target}" if pack and v1 else "<내릴 서비스>"
    return (f"DEPLOY_TAG=<deploy 태그> PACKRT_TAG=<packrt 태그> DEPLOY_COMMIT=<deploy 태그 40자> scripts/vps/deploy-check rollback "
            f"--id {shlex.quote(rollback_id)} --version {shlex.quote(target_version)} --config {config} --status-dir /var/lib/status/deployments "
            f"--service {service} --old-service {old}")


def request_doc(*, change_id, version, base_revision, ops, candidate_revision, change_approval, deploy_approval):
    """The approved-change@2 request file (task10-pack docs/approved-change-format.md), checked against the schema copy and its one
    rule outside the schema (the two revisions differ). Raises ValueError with the reasons. EV-039 CHG-197 (D-198): no tag — the
    Pack's apply tool takes the next free v<n> and LLMOps reads it from the Pack status file (S-20 · the window's steps).
    EV-039 CHG-196 (D-198): `packrt` = the packrt installed in this server ({version, tag "v<version>"} — agent-pack-runtime has one
    repository tag per version); the Pack runner checks it against its own packrt before taking the change in (D-58)."""
    import jsonschema
    from packrt import __version__ as packrt_version
    doc = {"format": FORMAT, "change_id": change_id, "version": version, "base_revision": base_revision, "ops": ops,
           "candidate_revision": candidate_revision, "approvals": {"change": change_approval, "deploy": deploy_approval},
           "packrt": {"version": packrt_version, "tag": "v" + packrt_version}}
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(f"{'/'.join(map(str, e.path)) or '(전체)'}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(doc))
    if base_revision == candidate_revision:
        errors.append("base_revision과 candidate_revision이 같습니다")
    if errors:
        raise ValueError(errors)
    return doc
