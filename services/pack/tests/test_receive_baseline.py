"""받아 넣기 도구 — 바탕(HEAD)에서 이미 실패한 시험은 빼고 '바탕엔 통과 · 후보에서 실패'만 거부(D-645 · packrt-1 229: kg-apply 갈래 그래프 커밋 때문에
바탕부터 실패하는 시험이 어떤 승인 변경이든 막음). 실패 이름 읽기 · 견주기(빠름) + clone에 시험 둘을 커밋해 도구를 돌림(dry-run · 원본 저장소 쓰기 0)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "change"))
import unit_compare as F  # noqa: E402

TOOL = ROOT / "tools/change/receive_approved.py"
SCHEMA = ROOT.parent / "task10-pack/docs/approved-change@2.schema.json"
SLOT = ROOT.parent / "agent-pack-runtime-ingest"
NESTED = ROOT.parent.name.startswith("c1a15-recv-copy-")

ERR = """F.E
======================================================================
ERROR: test_b (test_two.Two.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
RuntimeError: x
======================================================================
FAIL: test_a (test_one.One.test_a)
----------------------------------------------------------------------
AssertionError: 1 != 2
----------------------------------------------------------------------
Ran 3 tests in 0.01s

FAILED (failures=1, errors=1)
"""


class Names(unittest.TestCase):
    def test_reads_fail_and_error_names(self):
        self.assertEqual(F.failed_names(ERR), {"test_one.One.test_a", "test_two.Two.test_b"})

    def test_ok_run_has_none(self):
        self.assertEqual(F.failed_names("...\n----\nRan 3 tests in 0.1s\n\nOK\n"), set())

    def test_judge(self):
        self.assertEqual(F.judge(True, set(), None), ("ok", set(), set()))
        self.assertEqual(F.judge(False, {"a", "b"}, {"a", "b"}), ("ok", set(), {"a", "b"}))           # 바탕 실패뿐 → 받음
        self.assertEqual(F.judge(False, {"a", "c"}, {"a", "b"}), ("new", {"c"}, {"a"}))               # 새 실패 c → 거부
        self.assertEqual(F.judge(False, set(), set()), ("unknown", set(), set()))                     # 실패인데 이름 못 읽음(가져오기 오류 등) → 거부
        self.assertEqual(F.judge(False, {"a"}, None), ("new", {"a"}, set()))                          # 바탕을 못 돌림 → 모두 새 실패로


@unittest.skipIf(NESTED, "받아 넣기 사본 시험 안(도구가 부른 시험)")
class Tool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA.is_file():
            raise unittest.SkipTest(f"schema 없음: {SCHEMA}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="c1a15-recv-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.repo = cls.tmp / "c1a15-pack"
        subprocess.run(["git", "clone", "-q", str(ROOT), str(cls.repo)], check=True)
        cls.branch = subprocess.run(["git", "-C", str(cls.repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        os.symlink(ROOT / ".venv", cls.repo / ".venv")
        if SLOT.exists():
            os.symlink(SLOT, cls.tmp / "agent-pack-runtime-ingest")
        os.symlink(SCHEMA.parents[1], cls.tmp / "task10-pack")
        # 바탕부터 늘 실패하는 시험 하나 · 후보(fuse k 61)에서만 실패하는 시험 하나를 clone HEAD에 커밋(Pack revision 바뀜 0 · 시험 파일만)
        (cls.repo / "tests/test_zz_probe_base.py").write_text(
            "import unittest\nclass Base(unittest.TestCase):\n    def test_always(self):\n        self.fail('바탕부터 실패')\n", encoding="utf-8")
        g = ["git", "-C", str(cls.repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*g, "add", "tests/test_zz_probe_base.py"], check=True)
        subprocess.run([*g, "commit", "-q", "-m", "probe base"], check=True)
        import packrt
        from packrt import ir
        from packrt.ir.patch import apply_patch
        base = ir.load_pack(cls.repo / "pack").revision
        ops = [{"op": "set", "graph": "run", "node": "retrieve_manual", "param": "fuse", "value": {"method": "rrf", "k": 61, "pool": 300}}]
        res = apply_patch(cls.repo / "pack", ops, out_dir=cls.tmp / "cand" / "pack")
        assert res.ok, res.errors
        cls.req = {"format": "task10-pack/approved-change@2", "change_id": "CHG-c1a15-BASE", "version": "V98", "base_revision": base, "ops": ops,
                   "candidate_revision": res.revision, "approvals": {"change": "verdict:TEST", "deploy": "deploy:CHG-c1a15-BASE"},
                   "packrt": {"version": packrt.__version__, "tag": f"v{packrt.__version__}"}}

    def run_tool(self):
        f = self.tmp / f"req-{len(os.listdir(self.tmp))}.json"
        f.write_text(json.dumps(self.req, ensure_ascii=False), encoding="utf-8")
        sd = Path(tempfile.mkdtemp(prefix="status-", dir=self.tmp))
        r = subprocess.run([sys.executable, "-B", str(TOOL), str(f), "--repo", str(self.repo), "--branch", self.branch, "--status-dir", str(sd)],
                           capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.tmp)})
        self.assertEqual(sorted(n for n in os.listdir(self.tmp) if n.startswith("c1a15-recv-")), [], "도구가 남긴 임시 폴더")
        st = sd / f"{self.req['change_id']}.json"
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), json.loads(st.read_text(encoding="utf-8"))

    def test_1_base_failure_is_left_out(self):
        code, out, st = self.run_tool()
        self.assertEqual((code, out["result"]), (0, "dry_run_ok"), out)
        self.assertEqual(out["baseline_failures"], ["test_zz_probe_base.Base.test_always"])
        self.assertEqual(st["stage"], "requested")
        self.assertIn("바탕 실패 1건 뺌: test_zz_probe_base.Base.test_always", st["detail"])
        self.assertEqual(sorted(st), ["commit", "deployment_id", "detail", "id", "revision", "stage", "tag", "updated_at", "version"])   # 상태 파일 9칸 그대로

    def test_2_new_failure_is_refused_with_its_name(self):
        (self.repo / "tests/test_zz_probe_cand.py").write_text(
            "import unittest\nfrom pathlib import Path\nfrom packrt import ir\n"
            "class Cand(unittest.TestCase):\n    def test_fuse_k(self):\n"
            "        n = next(n for n in ir.load_pack(Path('pack')).doc['graphs']['run']['nodes'] if n['id'] == 'retrieve_manual')\n"
            "        self.assertNotEqual(n['params']['fuse']['k'], 61)\n", encoding="utf-8")
        g = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*g, "add", "tests/test_zz_probe_cand.py"], check=True)
        subprocess.run([*g, "commit", "-q", "-m", "probe cand"], check=True)
        code, out, st = self.run_tool()
        self.assertEqual((code, out["result"], out["stage"]), (1, "refused", "tests"), out)
        self.assertEqual(out["new_failures"], ["test_zz_probe_cand.Cand.test_fuse_k"])
        self.assertEqual(out["baseline_failures"], ["test_zz_probe_base.Base.test_always"])
        self.assertEqual(st["stage"], "failed")
        self.assertIn("새 실패 1건: test_zz_probe_cand.Cand.test_fuse_k", st["detail"])
        self.assertIn("바탕 실패 1건: test_zz_probe_base.Base.test_always", st["detail"])


@unittest.skipIf(NESTED, "받아 넣기 사본 시험 안(도구가 부른 시험)")
class AlreadyOnGraphTag(unittest.TestCase):
    """kg-apply 갈래의 바탕은 g 태그(그래프 반영) · 첫 상태 태그뿐 — '이미 반영됨' 대조가 v 태그만 찾으면 다시 태우기가 늘 막힘(packrt-1 231 · 60행)."""
    @classmethod
    def setUpClass(cls):
        if not SCHEMA.is_file():
            raise unittest.SkipTest(f"schema 없음: {SCHEMA}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="c1a15-recv-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.repo = cls.tmp / "c1a15-pack"
        subprocess.run(["git", "clone", "-q", str(ROOT), str(cls.repo)], check=True)
        cls.branch = subprocess.run(["git", "-C", str(cls.repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        os.symlink(ROOT / ".venv", cls.repo / ".venv")
        if SLOT.exists():
            os.symlink(SLOT, cls.tmp / "agent-pack-runtime-ingest")
        os.symlink(SCHEMA.parents[1], cls.tmp / "task10-pack")
        subprocess.run(["git", "-C", str(cls.repo), "tag", "g7"], check=True)   # 바탕 = 그래프 반영 태그(v 태그 없음)
        import packrt
        from packrt import ir
        from packrt.ir.patch import apply_patch
        base = ir.load_pack(cls.repo / "pack").revision
        ops = [{"op": "set", "graph": "run", "node": "retrieve_manual", "param": "fuse", "value": {"method": "rrf", "k": 61, "pool": 300}}]
        res = apply_patch(cls.repo / "pack", ops, out_dir=cls.tmp / "cand" / "pack")
        assert res.ok, res.errors
        cls.req = {"format": "task10-pack/approved-change@2", "change_id": "CHG-c1a15-GTAG", "version": "V97", "base_revision": base, "ops": ops,
                   "candidate_revision": res.revision, "approvals": {"change": "verdict:TEST", "deploy": "deploy:CHG-c1a15-GTAG"},
                   "packrt": {"version": packrt.__version__, "tag": f"v{packrt.__version__}"}}

    def run_tool(self, *args):
        f = self.tmp / f"req-{len(os.listdir(self.tmp))}.json"
        f.write_text(json.dumps(self.req, ensure_ascii=False), encoding="utf-8")
        sd = Path(tempfile.mkdtemp(prefix="status-", dir=self.tmp))
        r = subprocess.run([sys.executable, "-B", str(TOOL), str(f), "--repo", str(self.repo), "--branch", self.branch, "--status-dir", str(sd), *args],
                           capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.tmp)})
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), json.loads((sd / f"{self.req['change_id']}.json").read_text(encoding="utf-8"))

    def test_rerun_after_apply_finds_base_on_graph_tag(self):
        code, out, st = self.run_tool("--apply")
        self.assertEqual((code, out["result"], st["stage"]), (0, "tagged", "tagged"), out)
        code, out, st = self.run_tool()                                      # 같은 요청 다시(③에서 멈춘 뒤 다시 태우기 꼴)
        self.assertEqual((code, out["result"]), (0, "already_applied_dry_run"), out)
        self.assertEqual(out["base_tag"], "g7")
        self.assertEqual(st["stage"], "requested")
