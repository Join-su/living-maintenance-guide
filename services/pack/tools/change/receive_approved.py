"""c1a15-pack 받아 넣기(D-420 장면 ③): LLMOps approved-change@2 요청 파일 → 검사 → (--apply) 가지 커밋 · 주석 태그 v<n> · 상태 파일.
task10-pack tools/change/apply_approved.py와 같은 순서를 c1a15-pack 모양(가지 v7-bundle · lock 없음 · unittest)으로. 기록 git(.vibe-factory EV-003 SL-5 800d7acb)에서 저장소로 옮김 — 동작 같음(기본 경로만 이 파일 자리 · 형제 저장소 기준).
  <python> -B tools/change/receive_approved.py <요청.json> [--repo <c1a15-pack>] [--branch v7-bundle] [--update-start-expectations] [--apply]
검사: ① schema(task10 docs/approved-change@2.schema.json) · base ≠ candidate ② 요청 packrt 판 = Pack vendor 판 ③ 가지 · 작업 트리 깨끗
④ 지금 revision = base_revision ⑤ 다음 빈 태그(v 최대 + 1) 없음 ⑥ packrt apply_patch(ops) → revision = candidate_revision
⑦ 사본(git archive HEAD)에 바뀐 파일(+ 가짜 DB start 기대값 다시 셈 · + 옵션 선언 값 기록)을 넣고 unittest 전체 통과.
   실패가 있으면 같은 env로 바탕(HEAD 그대로) 사본도 돌려, 바탕에서 이미 실패한 시험은 빼고 '바탕엔 통과 · 후보에서 실패'만 거부(D-645 · 이름은 상태 detail · 출력에).
다시 셈 = 사본 tests/start_contract.py(Pack 시험과 같은 함수 · 옵션 일곱 모두 코드 기본) · 요청 ops에 set_option이 있으면 tests/fixtures/declared_options.json = 후보 선언 값(D-492 덧 34 · 013-129 (ㄷ)).
같은 플래그로 T1 지문(tests/fixtures/v15_start_judge_sha.json)도 다시 셈: start 줄만 다시 씀(_rewrite = 까닭 · 요청 id · 바뀐 case 수) · judge 지문이 바뀌면 거부 t1-judge(판정 셈 바꿈 = 사람 몫 · D-492 덧 38 · 013-133 (나′)).
--apply: 같은 파일을 저장소에 쓰고 → revision 다시 = candidate → unittest → 커밋 → 주석 태그 → 상태 파일(applied → tagged). push 0.
상태 파일: <repo>/status/approved-changes/<change_id>.json(9칸, git 무시 /status/). 실패 시 저장소는 쓰기 전으로 되돌림(git checkout).
임시 폴더(c1a15-recv-* · 태그 풀기 · 패치 · 사본 시험)는 이 실행이 만든 것만 끝날 때(통과 · 거부 · 오류) 이름으로 지움(D-470 덧 23 ①)."""
import argparse, atexit, datetime, importlib.util, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("request"); ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2])); ap.add_argument("--branch", default="v7-bundle")
ap.add_argument("--update-start-expectations", action="store_true"); ap.add_argument("--packrt-accept", metavar="VER", help="요청 packrt 판이 Pack vendor 판과 달라도 이 판 하나만 받음(D-451 — 나머지 검사 그대로)"); ap.add_argument("--apply", action="store_true"); ap.add_argument("--by", metavar="TEXT", help="상태 detail 끝에 ' · TEXT'를 붙임(D-455 자동 감시 — 예 '자동 감시', 안 주면 그대로)"); ap.add_argument("--status-dir", help="상태 파일 자리(기본 <repo>/status/approved-changes — LLMOps가 읽는 곳에 맞춤)"); ap.add_argument("--schema", help="approved-change@2 schema(기본 <repo>/../task10-pack/docs/approved-change@2.schema.json)")
a = ap.parse_args(); repo = Path(a.repo).resolve(); req = json.loads(Path(a.request).read_text(encoding="utf-8"))
a.schema = a.schema or str(repo.parent / "task10-pack/docs/approved-change@2.schema.json")
sys.path.insert(0, str(repo / "vendor/packrt"))
import packrt, jsonschema
from packrt import ir
from packrt.ir.patch import apply_patch
sys.path.insert(0, str(Path(__file__).resolve().parent)); import unit_compare as TF   # D-645 바탕 실패 빼기
MADE = []   # 이 실행이 만든 임시 폴더(경로 그대로) — 끝날 때 이것만 지움
def tmpdir(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix)); MADE.append(d); return d
@atexit.register
def _clean():
    for d in MADE:   # 이름 꼴 · 자리(임시 폴더 바로 아래)까지 맞을 때만 — 남의 폴더 손 0
        if d.name.startswith("c1a15-recv-") and d.parent == Path(tempfile.gettempdir()):
            shutil.rmtree(d, ignore_errors=True)
git = lambda *c, check=True: subprocess.run(["git", "-C", str(repo), *c], capture_output=True, text=True, check=check).stdout.strip()
now = lambda: datetime.datetime.now().astimezone().isoformat(timespec="seconds")
sdir = Path(a.status_dir).resolve() if a.status_dir else repo / "status/approved-changes"
def status(stage, detail, commit=None, tag=None):
    sdir.mkdir(parents=True, exist_ok=True); p = sdir / f"{req.get('change_id')}.json"; t = p.with_suffix(".tmp")
    t.write_text(json.dumps({"id": req.get("change_id"), "version": req.get("version"), "revision": req.get("candidate_revision"), "stage": stage, "detail": detail + (f" · {a.by}" if a.by else ""),
                             "commit": commit, "tag": tag, "deployment_id": None, "updated_at": now()}, ensure_ascii=False) + "\n", encoding="utf-8"); os.replace(t, p)
def stop(stage, why):
    if a.apply or stage != "format": status("failed", f"거부({stage}): {why}")
    print(json.dumps({"result": "refused", "stage": stage, "why": why}, ensure_ascii=False)); sys.exit(1)
errs = sorted(e.message for e in jsonschema.Draft202012Validator(json.loads(Path(a.schema).read_text(encoding="utf-8"))).iter_errors(req))
if errs or req["base_revision"] == req["candidate_revision"]: print(json.dumps({"result": "format_error", "errors": errs or ["base = candidate"]}, ensure_ascii=False)); sys.exit(2)
rv = req.get("packrt", {}).get("version"); pnote = f" · 요청 packrt {rv} ≠ vendor {packrt.__version__} · --packrt-accept {rv}로 받음(D-451)" if rv != packrt.__version__ and a.packrt_accept and rv == a.packrt_accept else ""
if rv != packrt.__version__ and not pnote: stop("packrt", f"요청 packrt {req.get('packrt')} ≠ Pack vendor {packrt.__version__}")
if git("rev-parse", "--abbrev-ref", "HEAD") != a.branch: stop("branch", f"가지가 {a.branch}가 아님")
dirty = [l for l in git("status", "--porcelain").splitlines() if not l[3:].startswith("status/")]   # 상태 파일 폴더는 받아 넣기가 쓰는 곳
if dirty: stop("worktree", "작업 트리가 깨끗하지 않음: " + " ".join(dirty[:3]))
cur = ir.load_pack(repo / "pack").revision
def tag_rev(t):   # 태그 → (태그, commit, revision, 푼 pack 자리)
    d = tmpdir("c1a15-recv-tag-"); subprocess.run(f"git -C '{repo}' archive {t} pack | tar -x -C '{d}'", shell=True, check=True)
    return t, git("rev-parse", f"{t}^{{commit}}"), ir.load_pack(d / "pack").revision, d / "pack"
def tag_revs():   # v 태그 → (commit, revision), 큰 번호 먼저
    return [tag_rev(t) for t in sorted((t for t in git("tag", "-l").split() if re.fullmatch(r"v\d+", t)), key=lambda t: -int(t[1:]))]
def base_on_other_tags(base):   # D-645(packrt-1 231): kg-apply 갈래의 바탕은 g 태그(그래프 반영) · 첫 상태 태그(d…-g…)뿐 — 새 것 먼저 · 맞는 첫 태그
    tags = git("tag", "-l").split()
    order = sorted((t for t in tags if re.fullmatch(r"g\d+", t)), key=lambda t: -int(t[1:])) + sorted((t for t in tags if re.fullmatch(r"d\d+-g\d+", t)), reverse=True)
    return next((x for x in map(tag_rev, order) if x[2] == base), None)
if cur != req["base_revision"]:   # 이미 반영됨(013-38 (가) · S-P 순서 (나)): candidate revision을 가진 v 태그가 있고, base revision을 가진 태그(v → 없으면 g · 첫 상태 · D-645)에 ops를 대면 candidate → 상태 tagged만(커밋 · 태그 0)
    tr = tag_revs(); hit = next((x for x in tr if x[2] == req["candidate_revision"]), None)
    if hit is None: stop("base", f"지금 revision {cur[:15]} ≠ base {req['base_revision'][:15]} · candidate를 가진 v 태그도 없음")
    base_tag = next((x for x in tr if x[2] == req["base_revision"]), None) or base_on_other_tags(req["base_revision"])
    if base_tag is None: stop("already", f"candidate는 {hit[0]}에 있으나 base {req['base_revision'][:15]}를 가진 v · g · 첫 상태 태그 없음(같은 바꿈인지 대조 못 함)")
    out = tmpdir("c1a15-recv-"); res = apply_patch(base_tag[3], req["ops"], out_dir=out / "pack")
    if not res.ok or res.revision != req["candidate_revision"]: stop("already", f"ops를 {base_tag[0]}에 대면 candidate가 아님(같은 바꿈이 아님)")
    tag, commit = hit[0], hit[1]
    print(json.dumps({"result": "already_applied" + ("" if a.apply else "_dry_run"), "tag": tag, "commit": commit[:12], "revision": hit[2], "base_tag": base_tag[0]}, ensure_ascii=False))
    if a.apply: status("tagged", f"이미 반영된 {tag}(같은 revision · base {base_tag[0]}에 ops = candidate) — 커밋 · 태그 새로 0" + pnote, commit=commit, tag=tag)
    else: status("requested", f"dry-run 통과 — 이미 반영된 {tag}(같은 revision) · 적용 = 상태 tagged만" + pnote)
    sys.exit(0)
nums = [int(m.group(1)) for t in git("tag", "-l").split() if (m := re.fullmatch(r"v(\d+)", t))]; tag = f"v{max(nums or [0]) + 1}"
if tag in git("tag", "-l").split(): stop("tag", f"{tag} 이미 있음")
out = tmpdir("c1a15-recv-"); res = apply_patch(repo / "pack", req["ops"], out_dir=out / "pack")
if not res.ok: stop("patch", "; ".join(e["message"] for e in res.errors)[:300])
if res.revision != req["candidate_revision"]: stop("candidate", f"패치 revision {res.revision[:15]} ≠ candidate {req['candidate_revision'][:15]}")
changed = list(res.changed_files)
RECORD = "tests/fixtures/declared_options.json"   # 옵션 선언 값 기록(Pack test_options가 고정) — 승인 요청에 set_option이 있을 때만 후보 값으로 씀, 그 밖엔 손 0
record = json.dumps({o["name"]: o["value"] for o in ir.node_options(out / "pack")}, ensure_ascii=False, indent=1, sort_keys=True) + "\n" if any(o.get("op") == "set_option" for o in req["ops"]) else None
def contract(pack_dir):
    """사본 tests/start_contract.py(Pack 시험과 같은 함수)와 그 약속의 코드 기본 Runtime."""
    from packrt.run.engine import Runtime
    spec = importlib.util.spec_from_file_location("start_contract", Path(pack_dir).parent / "tests/start_contract.py"); sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
    return sc, sc.code_default(Runtime(pack_dir, env={}))
def new_start_expectations(pack_dir):
    """가짜 DB(execution-config.yaml) start suite 기대값을 후보 Pack 출력으로 다시 셈 — 입력 dataset 그대로, decision · similar · retrieval만.
    셈 약속은 사본 tests/start_contract.py(Pack 시험 test_start_output_unchanged_from_ps02와 같은 함수 · 옵션 일곱 모두 코드 기본)."""
    sc, rt = contract(pack_dir); ev = Path(pack_dir) / "evals/c1a15-start"; old = [json.loads(x) for x in (ev / "expectations.jsonl").read_text(encoding="utf-8").splitlines()]
    data = {json.loads(x)["case_id"]: json.loads(x)["input"] for x in (ev / "dataset.jsonl").read_text(encoding="utf-8").splitlines()}
    lines, diff = [], 0
    for e in old:
        o = sc.start_output(rt, data[e["case_id"]]); fin = [{"path": f["path"], "equals": o[f["path"]]} for f in e["final"]]
        diff += fin != e["final"]; lines.append(json.dumps({**e, "final": fin}, ensure_ascii=False))
    return "\n".join(lines) + "\n", diff
def new_fingerprints(pack_dir):
    """T1 지문(요청 60 · 코드 기본)을 후보 Pack 출력으로 다시 셈 → (새 파일 글 | None, 바뀐 start 수, 바뀐 judge 수). start만 바뀌면 그 줄만 새 값 + _rewrite 기록."""
    sc, rt = contract(pack_dir); fp = Path(pack_dir).parent / sc.FINGERPRINTS; old = json.loads(fp.read_text(encoding="utf-8"))
    new, moved = {k: v for k, v in old.items() if not k.startswith("_")}, {"start": 0, "judge": 0}
    for name, r in sc.requests(Path(pack_dir).parent):
        s = sc.fingerprint(rt.run(r)["output"])
        if s != new.get(name): moved[r["kind"]] += 1; new[name] = s
    if moved["judge"] or not moved["start"]: return None, moved["start"], moved["judge"]
    new["_rewrite"] = {"why": "사람 승인 셈 바꿈 요청(start 출력 바뀜) — 받아 넣기 도구가 T1 start 지문만 다시 씀(judge 지문 그대로 · D-492 덧 38)", "change_id": req["change_id"],
                       "version": req.get("version"), "changed": moved, "base_revision": req["base_revision"], "candidate_revision": req["candidate_revision"]}
    return json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n", moved["start"], 0
def bare_copy():
    base = tmpdir("c1a15-recv-copy-"); c = base / "c1a15-pack"; c.mkdir()   # 벡터 DB 자리: 사본 옆에 저장소 옆 자리를 링크(../agent-pack-runtime-ingest)
    slot = repo.parent / "agent-pack-runtime-ingest"
    if slot.exists(): os.symlink(slot, base / "agent-pack-runtime-ingest")
    subprocess.run(f"git -C '{repo}' archive HEAD | tar -x -C '{c}'", shell=True, check=True)
    os.symlink(repo / ".venv", c / ".venv"); return c
def unit(c):   # 사본 하나의 unittest 전체 → (통과?, 마지막 줄, stderr)
    r = subprocess.run([str(c / ".venv/bin/python"), "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=c, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"], "LANG": "en_US.UTF-8", "ORT_DISABLE_TELEMETRY": "1", "PYTHONPATH": str(c / "vendor/packrt")})
    return r.returncode == 0, (r.stderr.strip().splitlines() or ["?"])[-1], r.stderr
def stage_copy():
    c = bare_copy()
    for f in changed: shutil.copyfile(out / "pack" / f, c / "pack" / f)
    exp = fps = None
    if record is not None: (c / RECORD).write_text(record, encoding="utf-8")
    if a.update_start_expectations:
        exp, n = new_start_expectations(c / "pack"); (c / "pack/evals/c1a15-start/expectations.jsonl").write_text(exp, encoding="utf-8"); print(f"가짜 DB start 기대값 바뀐 case {n}")
        fps, ns, nj = new_fingerprints(c / "pack"); print(f"T1 지문 바뀐 case {ns + nj}(start {ns} · judge {nj})")
        if nj: stop("t1-judge", f"판정 출력 바뀜 {nj} case(T1 judge 지문) — 판정 셈 바꿈은 사람 몫이라 받지 않음(D-492 덧 38) · 저장소 쓰기 0")
        if fps is not None: (c / FP).write_text(fps, encoding="utf-8")
    ok, tail, err = unit(c); return ok, tail, err, exp, fps, c
FP = "tests/fixtures/v15_start_judge_sha.json"   # T1 지문(사본 start_contract.FINGERPRINTS와 같은 자리)
ok, tail, err, exp, fps, copy = stage_copy()
fnote = f" · T1 start 지문 {json.loads(fps)['_rewrite']['changed']['start']} case 다시 씀" if fps is not None else ""
cand_fail = TF.failed_names(err); base_fail = None
if not ok:   # D-645: 바탕(HEAD 그대로 · 같은 env)에서 이미 실패한 시험은 빼고 새 실패만 봄
    b_ok, b_tail, b_err = unit(bare_copy()); base_fail = set() if b_ok else (TF.failed_names(b_err) or None)
verdict, new_fail, left_out = TF.judge(ok, cand_fail, base_fail)
names = lambda xs: ", ".join(sorted(xs))
bnote = f" · 바탕 실패 {len(left_out)}건 뺌: {names(left_out)}" if left_out else ""
if verdict != "ok":
    why = (f"사본 시험 새 실패 {len(new_fail)}건: {names(new_fail)}" + (f" · 바탕 실패 {len(left_out)}건: {names(left_out)}" if left_out else "") + f" · {tail}"
           if verdict == "new" else f"사본 시험 실패(이름 못 읽음 · 바탕과 못 견줌): {tail}") + ("" if a.update_start_expectations else " — 가짜 DB start 기대값이 바뀌면 --update-start-expectations")
    status("failed", f"거부(tests): {why}")
    print(json.dumps({"result": "refused", "stage": "tests", "why": why, "new_failures": sorted(new_fail), "baseline_failures": sorted(left_out)}, ensure_ascii=False)); sys.exit(1)
print(json.dumps({"result": "dry_run_ok" if not a.apply else "checked", "tag": tag, "changed": changed, "base": cur[:15], "candidate": res.revision[:15], "tests": tail, "baseline_failures": sorted(left_out), "packrt": packrt.__version__, "packrt_accept": rv if pnote else None}, ensure_ascii=False))
if not a.apply:
    status("requested", f"dry-run 통과 — 적용 대기({tag} · 사본 시험 {tail}{bnote})" + fnote + pnote); sys.exit(0)
try:
    for f in changed: shutil.copyfile(out / "pack" / f, repo / "pack" / f)
    files = [f"pack/{f}" for f in changed]
    gi = repo / ".gitignore"; gt = gi.read_text(encoding="utf-8")
    if "/status/" not in gt.splitlines(): gi.write_text(gt + ("" if gt.endswith("\n") else "\n") + "/status/\n", encoding="utf-8"); files.append(".gitignore")   # 상태 파일 폴더(task10과 같음)
    if exp is not None: (repo / "pack/evals/c1a15-start/expectations.jsonl").write_text(exp, encoding="utf-8"); files.append("pack/evals/c1a15-start/expectations.jsonl")
    if record is not None: (repo / RECORD).write_text(record, encoding="utf-8"); files.append(RECORD)
    if fps is not None: (repo / FP).write_text(fps, encoding="utf-8"); files.append(FP)
    if ir.load_pack(repo / "pack").revision != req["candidate_revision"]: raise RuntimeError("저장소에 쓴 뒤 revision이 candidate와 다름")
    git("add", *files); git("commit", "-q", "-m", f"받아 넣기 {req['change_id']}({req.get('version')}): ops {len(req['ops'])} · revision {cur[7:15]} → {res.revision[7:15]} · 승인 {req['approvals']['deploy']}"
                         + (" · 가짜 DB start 기대값 다시 셈" if exp is not None else "") + (" · 옵션 선언 값 기록 = 승인 요청 값" if record is not None else "") + fnote + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    commit = git("rev-parse", "HEAD"); status("applied", f"로컬 커밋 {commit[:7]}" + fnote + pnote, commit=commit)
    git("tag", "-a", tag, "-m", f"{tag} = {req['change_id']}({req.get('version')}) · revision {res.revision} · packrt {packrt.__version__} · 로컬(push 0)")
    status("tagged", f"로컬 태그 {tag}" + pnote, commit=commit, tag=tag)
    print(json.dumps({"result": "tagged", "tag": tag, "commit": commit[:12], "revision": res.revision}, ensure_ascii=False))
except Exception as e:
    git("checkout", "--", ".", check=False); status("failed", f"적용 중 실패 — 되돌림: {type(e).__name__}: {e}"[:300]); raise
