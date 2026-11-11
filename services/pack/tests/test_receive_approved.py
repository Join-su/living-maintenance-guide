"""받아 넣기 도구 tools/change/receive_approved.py 시험(기록 git에서 옮긴 판 · 동작 같음) — 저장소 사본(clone)에서 dry-run만, 원본 저장소 쓰기 0.
형식 거부 · packrt 판 거부 / --packrt-accept 한 판만 받음(D-451) · --by 상태 글 끝(D-455) · 가지 거부 · dry-run 통과(다음 빈 태그 · 상태 requested · 사본 그대로).
D-492 덧 34(013-129 (ㄷ)): 다시 셈 = Pack 시험과 같은 함수(코드 기본) · 옵션 후보 = 도구가 옵션 선언 값 기록을 승인 요청 값으로 씀 — --apply는 따로 clone에서.
D-492 덧 38(013-133 (나′)): 셈 바꿈 후보는 도구가 T1 지문의 start 줄만 다시 씀(기록에 까닭 · 요청 id · 바뀐 case 수) · judge 지문이 바뀌면 거부(사람 몫)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/change/receive_approved.py"
SCHEMA = ROOT.parent / "task10-pack/docs/approved-change@2.schema.json"
SLOT = ROOT.parent / "agent-pack-runtime-ingest"
NESTED = ROOT.parent.name.startswith("c1a15-recv-copy-")   # 도구가 사본에서 돌리는 시험 안 — 다시 부르지 않음(끝없이 돎 막기)


@unittest.skipIf(NESTED, "받아 넣기 사본 시험 안(도구가 부른 시험)")
class ReceiveApproved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA.is_file():
            raise unittest.SkipTest(f"schema 없음: {SCHEMA}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="c1a15-recv-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)   # setUpClass가 중간에 떨어져도 이 시험 폴더는 지움(10-29 남은 5개)
        cls.repo = cls.tmp / "c1a15-pack"
        subprocess.run(["git", "clone", "-q", str(ROOT), str(cls.repo)], check=True)
        cls.branch = subprocess.run(["git", "-C", str(cls.repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        os.symlink(ROOT / ".venv", cls.repo / ".venv")   # 도구 사본 시험이 <repo>/.venv를 씀(git 무시)
        if SLOT.exists():
            os.symlink(SLOT, cls.tmp / "agent-pack-runtime-ingest")
        os.symlink(SCHEMA.parents[1], cls.tmp / "task10-pack")   # 기본 schema 자리 = <repo>/../task10-pack(읽기만)
        import packrt
        from packrt import ir
        from packrt.ir.patch import apply_patch
        cls.packrt = packrt.__version__
        base = ir.load_pack(cls.repo / "pack").revision
        def request(ops, out):
            res = apply_patch(cls.repo / "pack", ops, out_dir=cls.tmp / out / "pack")
            assert res.ok, res.errors
            return {"format": "task10-pack/approved-change@2", "change_id": "CHG-c1a15-TEST", "version": "V99", "base_revision": base, "ops": ops,
                    "candidate_revision": res.revision, "approvals": {"change": "verdict:TEST", "deploy": "deploy:CHG-c1a15-TEST"},
                    "packrt": {"version": cls.packrt, "tag": f"v{cls.packrt}"}}
        cls.req = request([{"op": "set", "graph": "run", "node": "retrieve_manual", "param": "fuse", "value": {"method": "rrf", "k": 61, "pool": 300}}], "cand")   # 매뉴얼 결과는 trace에만 — 출력 · 시험 기대값 바뀜 0
        rr = next(n for n in ir.load_pack(cls.repo / "pack").doc["graphs"]["run"]["nodes"] if n["id"] == "retrieve_history")["params"]["rerank"]
        cls.req_start = request([{"op": "set", "graph": "run", "node": "retrieve_history", "param": "rerank", "value": {**rr, "pool": 20}}], "cand-start")   # start 출력이 바뀜
        cls.req_opt = request([{"op": "set_option", "graph": "run", "node": "first_judge", "option": "call_threshold", "value": 70}], "cand-opt")   # 옵션 후보(두 판정 node 같은 값)

    def test_every_temp_folder_goes_through_tmpdir(self):   # 이미 반영됨 길(태그 풀기 c1a15-recv-tag-)처럼 시험이 닿기 어려운 길도 같은 지우기를 지나게
        src = TOOL.read_text(encoding="utf-8")
        self.assertEqual(src.count("mkdtemp("), 1)
        self.assertIn("def tmpdir(prefix):\n    d = Path(tempfile.mkdtemp(prefix=prefix)); MADE.append(d)", src)

    def test_leaves_other_folders(self):   # 도구가 지우는 것은 이 실행이 만든 폴더뿐 — 같은 이름 꼴의 남의 폴더 · 다른 폴더는 그대로
        other = self.tmp / "c1a15-recv-not-mine"
        other.mkdir(exist_ok=True)
        try:
            f = self.tmp / "req-other.json"
            f.write_text(json.dumps(self.req, ensure_ascii=False), encoding="utf-8")
            subprocess.run([sys.executable, "-B", str(TOOL), str(f), "--repo", str(self.repo), "--branch", self.branch, "--status-dir", str(self.tmp / "st-o")],
                           capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.tmp)})
            self.assertEqual(sorted(n for n in os.listdir(self.tmp) if n.startswith("c1a15-recv-")), ["c1a15-recv-not-mine"])
        finally:
            other.rmdir()

    def run_tool(self, req, *args, repo=None):
        f = self.tmp / f"req-{len(os.listdir(self.tmp))}.json"
        f.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        sd = Path(tempfile.mkdtemp(prefix="status-", dir=self.tmp))
        r = subprocess.run([sys.executable, "-B", str(TOOL), str(f), "--repo", str(repo or self.repo), "--branch", self.branch, "--status-dir", str(sd), *args],
                           capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.tmp)})   # 도구 임시 폴더도 이 시험 폴더 안(끝에 함께 지움)
        st = sd / f"{req['change_id']}.json"
        left = sorted(n for n in os.listdir(self.tmp) if n.startswith("c1a15-recv-"))
        self.assertEqual(left, [], "도구가 남긴 임시 폴더")   # D-470 덧 23 ①: 도구는 자기가 만든 임시 폴더만 이름으로 지움(통과 · 거부 모두)
        return r.returncode, r.stdout, (json.loads(st.read_text(encoding="utf-8")) if st.exists() else None)

    def last_json(self, out):
        return json.loads([x for x in out.splitlines() if x.startswith("{")][-1])

    def test_format_error(self):
        code, out, st = self.run_tool({**self.req, "candidate_revision": self.req["base_revision"]})
        self.assertEqual((code, self.last_json(out)["result"], st), (2, "format_error", None))

    def test_packrt_refused_then_accept_one_version(self):
        other = {**self.req, "packrt": {"version": "0.0.1", "tag": "v0.0.1"}}
        code, out, st = self.run_tool(other)
        self.assertEqual((code, self.last_json(out)["stage"], st["stage"]), (1, "packrt", "failed"))
        code, out, st = self.run_tool(other, "--packrt-accept", "0.0.2")   # 다른 판 하나만 받음 → 그대로 거부
        self.assertEqual((code, self.last_json(out)["stage"]), (1, "packrt"))
        code, out, st = self.run_tool(other, "--packrt-accept", "0.0.1", "--by", "자동 감시")
        self.assertEqual((code, self.last_json(out)["result"], self.last_json(out)["packrt_accept"]), (0, "dry_run_ok", "0.0.1"))
        self.assertEqual(st["stage"], "requested")
        self.assertIn(f"요청 packrt 0.0.1 ≠ vendor {self.packrt} · --packrt-accept 0.0.1로 받음(D-451)", st["detail"])
        self.assertTrue(st["detail"].endswith(" · 자동 감시"))

    def test_branch_refused(self):
        f = self.tmp / "req-branch.json"
        f.write_text(json.dumps(self.req), encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(TOOL), str(f), "--repo", str(self.repo), "--branch", "no-such-branch", "--status-dir", str(self.tmp / "st-b")],
                           capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.tmp)})
        self.assertEqual((r.returncode, self.last_json(r.stdout)["stage"]), (1, "branch"))

    def test_dry_run_ok_writes_nothing(self):
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout
        tags = [t for t in subprocess.run(["git", "-C", str(self.repo), "tag", "-l"], capture_output=True, text=True, check=True).stdout.split() if t[1:].isdigit() and t[0] == "v"]
        code, out, st = self.run_tool(self.req)
        j = self.last_json(out)
        self.assertEqual((code, j["result"], j["changed"], j["tag"]), (0, "dry_run_ok", ["pack.yaml"], f"v{max(int(t[1:]) for t in tags) + 1}"))
        self.assertEqual(st["stage"], "requested")
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout, head)
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout, "")

    def test_start_output_change_needs_expectation_update(self):   # 가짜 DB start 기대값이 바뀌는 바꿈은 사본 시험에서 막힘(--update-start-expectations 없이)
        code, out, st = self.run_tool(self.req_start)
        j = self.last_json(out)
        self.assertEqual((code, j["result"], j["stage"], st["stage"]), (1, "refused", "tests", "failed"))
        self.assertIn("--update-start-expectations", j["why"])

    def test_update_flag_keeps_unchanged_output(self):   # S1: 다시 셈 = Pack 시험과 같은 함수 — 출력이 안 바뀌는 후보는 바뀐 case 0(전엔 선언 값으로 셈 → 10 바뀜 → 시험 10 실패)
        code, out, st = self.run_tool(self.req, "--update-start-expectations")
        self.assertEqual((code, self.last_json(out)["result"]), (0, "dry_run_ok"))
        self.assertIn("가짜 DB start 기대값 바뀐 case 0\n", out)
        self.assertIn("T1 지문 바뀐 case 0(start 0 · judge 0)\n", out)   # U5: 출력 안 바뀌는 후보는 T1 지문 손 0

    def test_update_flag_takes_start_change(self):   # U1(S2 고침 · 013-133 (나′)): 셈 바꿈(rerank)은 플래그로 PS-02 기대값 · T1 start 지문을 다시 세어 사본 시험 통과
        code, out, st = self.run_tool(self.req_start, "--update-start-expectations")
        j = self.last_json(out)
        self.assertEqual((code, j["result"]), (0, "dry_run_ok"), j.get("why"))
        self.assertRegex(out, r"가짜 DB start 기대값 바뀐 case [1-9]")
        self.assertRegex(out, r"T1 지문 바뀐 case [1-9]\d*\(start [1-9]\d* · judge 0\)\n")
        self.assertRegex(st["detail"], r"T1 start 지문 [1-9]\d* case 다시 씀")

    def test_judge_fingerprint_change_refused(self):   # U3: judge 지문이 바뀌는 후보는 거부(판정 셈 바꿈 = 사람 몫) · 저장소 쓰기 0 — 따로 clone의 judge 지문 한 줄을 바꿔 같은 꼴을 만듦
        repo = self.tmp / "judge-moved"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(repo)], check=True)
        os.symlink(ROOT / ".venv", repo / ".venv")
        fp = repo / "tests/fixtures/v15_start_judge_sha.json"
        d = json.loads(fp.read_text(encoding="utf-8"))
        k = next(n for n in d if n.startswith("judge_cases/"))
        d[k] = "0" * 64
        fp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "judge 지문 한 줄"], check=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout
        code, out, st = self.run_tool(self.req, "--update-start-expectations", "--apply", repo=repo)
        j = self.last_json(out)
        self.assertEqual((code, j["result"], j["stage"], st["stage"]), (1, "refused", "t1-judge", "failed"))
        self.assertIn("판정 출력 바뀜 1 case", j["why"])
        self.assertIn("사람 몫", j["why"])
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout, head)
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout, "")

    def test_apply_rewrites_start_fingerprint(self):   # U4: --apply(따로 clone) 셈 바꿈 — 커밋 파일 · T1 기록(까닭 · 요청 id · 바뀐 case 수) · judge 줄 그대로 · 태그 revision
        from packrt import ir
        repo = self.tmp / "apply-start"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(repo)], check=True)
        os.symlink(ROOT / ".venv", repo / ".venv")
        fp = repo / "tests/fixtures/v15_start_judge_sha.json"
        before = json.loads(fp.read_text(encoding="utf-8"))
        code, out, st = self.run_tool(self.req_start, "--update-start-expectations", "--apply", repo=repo)
        j = self.last_json(out)
        self.assertEqual((code, j["result"], st["stage"]), (0, "tagged", "tagged"))
        got = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(got), ["pack/evals/c1a15-start/expectations.jsonl", "pack/pack.yaml", "tests/fixtures/v15_start_judge_sha.json"])
        msg = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"], capture_output=True, text=True, check=True).stdout
        self.assertRegex(msg, r"T1 start 지문 [1-9]\d* case 다시 씀")
        after = json.loads(fp.read_text(encoding="utf-8"))
        rw = after.pop("_rewrite")
        self.assertEqual((rw["change_id"], rw["changed"]["judge"], rw["candidate_revision"]), (self.req_start["change_id"], 0, self.req_start["candidate_revision"]))
        self.assertIn("사람 승인", rw["why"])
        moved = [n for n in before if not n.startswith("_") and before[n] != after[n]]
        self.assertEqual(len(moved), rw["changed"]["start"])
        self.assertGreater(len(moved), 0)
        self.assertEqual([n for n in moved if n.startswith(("c1a15-judge-demo/", "judge_cases/"))], [])   # judge 줄 그대로
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "describe", "--exact-match", "HEAD"], capture_output=True, text=True).stdout.strip(), j["tag"])
        self.assertEqual(ir.load_pack(repo / "pack").revision, self.req_start["candidate_revision"])
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout, "")

    def test_option_candidate_dry_run(self):   # S3: 옵션 후보 — 감시처럼 플래그를 넘겨도 바뀐 case 0 · 사본 시험 통과(옵션 선언 값 기록 = 후보 값)
        code, out, st = self.run_tool(self.req_opt, "--update-start-expectations")
        j = self.last_json(out)
        self.assertEqual((code, j["result"], j["changed"]), (0, "dry_run_ok", ["pack.yaml"]))
        self.assertIn("가짜 DB start 기대값 바뀐 case 0\n", out)
        self.assertIn("T1 지문 바뀐 case 0(start 0 · judge 0)\n", out)   # U5

    def test_apply_writes_option_record_only_for_set_option(self):   # S4 · S5: --apply(따로 clone) — 커밋 파일 · 기록 값 · 태그 revision
        from packrt import ir
        for name, req, files in (("apply-opt", self.req_opt, ["pack/pack.yaml", "tests/fixtures/declared_options.json"]), ("apply-set", self.req, ["pack/pack.yaml"])):
            with self.subTest(name=name):
                repo = self.tmp / name
                subprocess.run(["git", "clone", "-q", str(self.repo), str(repo)], check=True)
                os.symlink(ROOT / ".venv", repo / ".venv")
                rec = json.loads((repo / "tests/fixtures/declared_options.json").read_text(encoding="utf-8"))
                code, out, st = self.run_tool(req, "--update-start-expectations", "--apply", repo=repo)
                j = self.last_json(out)
                self.assertEqual((code, j["result"], st["stage"]), (0, "tagged", "tagged"))
                got = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True, check=True).stdout.split()
                self.assertEqual(sorted(got), files)
                self.assertEqual(subprocess.run(["git", "-C", str(repo), "describe", "--exact-match", "HEAD"], capture_output=True, text=True).stdout.strip(), j["tag"])
                self.assertEqual(ir.load_pack(repo / "pack").revision, req["candidate_revision"])
                want = {**rec, "call_threshold": 70} if name == "apply-opt" else rec
                self.assertEqual(json.loads((repo / "tests/fixtures/declared_options.json").read_text(encoding="utf-8")), want)
                self.assertEqual(subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout, "")


if __name__ == "__main__":
    unittest.main()
