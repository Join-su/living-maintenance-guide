"""C1-a15 Pack 시험(표준 라이브러리 unittest): .venv/bin/python -B -m unittest discover -s tests
VC-31 Pack 독립 · VC-02 Run 입력 칸 · VC-03 판정 node 선언 · 사본 출처 · 판정 예시(C1-a15 앱 inproc 값, 시연 10건 걸음에서 종류마다 하나)
· 약속 v2(Pack EV-001 PS-01): alarm(경계 · ML) · judge(session_id) · 약속 v3(PS-05): report(보고서 문장, 가짜 provider)
· 약속 v4(PS-02): schema = contracts/v4 바이트 · start = 이력 리트리버 retrieve@2(가짜 DB ir-history-412ea43e, 망 0)."""
import ast
import hashlib
import json
import os
import re
import sys
import unittest
from pathlib import Path

import jsonschema
import yaml
from packrt.run.engine import Runtime

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "pack"
if str(PACK) not in sys.path:   # custom 모듈을 바로 읽는 시험(judge · report)이 앞 시험의 Runtime(packrt가 Pack 폴더를 넣음)에 기대지 않게 — EV-002 SL-1 UF-001
    sys.path.insert(0, str(PACK))
STDLIB_OK = {"json", "re", "dataclasses", "collections", "functools", "pathlib", "typing", "math", "itertools"}
BANNED_TEXT = ("/Users/", "c1a15-maint-guide", "c1a15.", "DATABASE_URL", "psycopg", "sqlalchemy")
KINDS_OK = {"router@1", "predict@1", "render@1", "custom_code@3", "llm@1", "validate@2", "retrieve@2"}   # llm · validate = PS-05 · retrieve@2 = PS-02(PP-02)
SLOT = ROOT.parent / "agent-pack-runtime-ingest/releases-c1a15"   # 저장소 밖 벡터 DB 자리(packrt-1 build, git 밖) — 실행 설정 일곱 파일의 release_roots 둘째 칸
FAKE_DBS = ("ir-history-412ea43e", "ir-manuals-a5c07752")   # 가짜 DB(실행 설정 기본 · 시험)
REAL_DBS = ("ir-history-16d297d8", "ir-manuals-3ae4dcdf")   # 실제 DB(deploy-real · deploy-pg-real — 시험은 manifest만 읽음, Run 0)
SKIP_ENV = "C1A15_PACK_TESTS_WITHOUT_VECTOR_DB"   # EV-002 CH-03: 이 값이 1일 때만 자리 쓰는 시험을 건너뜀(기본은 실패)


def _release_ok(rid):
    m = SLOT / "indexes" / rid / "manifest.json"
    return m.is_file() and json.loads(m.read_text(encoding="utf-8")).get("build_sha256", "")[:8] == rid.rsplit("-", 1)[1]


def need_vector_db(test, real=False):
    """자리 쓰는 시험(Runtime을 만들거나 저장소 밖 경로를 읽는 시험)의 첫 줄 — 자리 · release가 없으면 한 줄 이유로 실패, SKIP_ENV=1이면 건너뜀(EV-002 CH-03)."""
    for kind, rids in (("가짜 DB", FAKE_DBS),) + ((("실제 DB", REAL_DBS),) if real else ()):
        miss = [r for r in rids if not _release_ok(r)]
        if miss:
            why = f"벡터 DB 자리 없음: {SLOT} — packrt-1 releases-c1a15({kind} {' · '.join(miss)}) 필요"
            if os.environ.get(SKIP_ENV) == "1":
                test.skipTest(why)
            test.fail(why)


from start_contract import CODE_DEFAULT, code_default, start_output   # noqa: E402 — 'v15 · 앱 inproc · 약속 예시 · PS-02와 같음' 시험은 옵션 일곱 모두 코드 기본(받아 넣기 도구와 같은 약속 · 013-129)


class _NeedsVectorDB(unittest.TestCase):   # 클래스 몸에서 Runtime을 만들던 클래스: 시험마다 setUp에서 자리 확인(시험 단위로 셈) → Runtime은 클래스에 한 번
    RT_CONFIG = "execution-config.yaml"
    CODE_DEFAULT = True   # 이 클래스 Runtime은 코드 기본 옵션 — 출력 · 구조 시험이 옵션 후보 값과 상관없게(013-129 B4) · False = 선언 값(test_options DeclaredRun)

    def setUp(self):
        need_vector_db(self)
        if "rt" not in type(self).__dict__:
            type(self).rt = Runtime(PACK, config_name=self.RT_CONFIG)
            if self.CODE_DEFAULT:
                code_default(type(self).rt)


EXTRA_STDLIB = {"alarm_route.py": {"os"}}   # 앱 alarms/ml.py 사본(threshold()가 환경 변수를 읽음) — Pack은 threshold()를 부르지 않음(Uses 시험)


class Independence(unittest.TestCase):   # VC-31
    def test_imports_only_stdlib_and_pack_custom(self):
        for p in sorted(PACK.rglob("*.py")):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                for n in names:
                    top = (n or "").split(".")[0]
                    self.assertTrue(top == "custom" or top in STDLIB_OK | EXTRA_STDLIB.get(p.name, set()), f"{p.relative_to(ROOT)}: import {n}")

    def test_env_threshold_never_called(self):   # ML 문턱 = 요청 settings 한 곳(D-260 (3)) — 사본의 threshold()(환경 변수)는 Pack에서 부르지 않음
        for p in sorted(PACK.rglob("*.py")):
            if p.name != "alarm_route.py":
                text = p.read_text(encoding="utf-8")
                self.assertNotIn("alarm_route.threshold", text, p.name)
                self.assertNotIn("environ", text, p.name)

    def test_no_app_paths_or_db_names(self):
        for p in sorted(x for x in PACK.rglob("*") if x.is_file() and x.suffix in {".py", ".yaml", ".json", ".md"} and "__pycache__" not in x.parts and x.name != "COPIED_FROM.json"):   # 출처 기록은 앱 이름을 적는 파일
            text = p.read_text(encoding="utf-8")
            for b in BANNED_TEXT:
                self.assertNotIn(b, text, f"{p.relative_to(ROOT)}: {b}")

    def test_node_kinds(self):
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        kinds = {n["kind"] for g in doc["graphs"].values() for n in g["nodes"]}
        self.assertTrue(kinds <= KINDS_OK, kinds)

    def test_judge_node_declares_no_network_no_side_effect(self):   # VC-03
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        for g in doc["graphs"].values():
            for n in g["nodes"]:
                if n["kind"].startswith("custom_code"):
                    self.assertEqual(n["capabilities"], {"network": "none", "side_effect": "none"}, n["id"])


class Copies(unittest.TestCase):
    def test_copied_from_hashes_match(self):
        doc = json.loads((PACK / "custom/COPIED_FROM.json").read_text(encoding="utf-8"))
        self.assertEqual(len(doc["files"]), 9)  # 판정 코드 셋 · 매뉴얼 절 · 알람 경계(예측 칸) · 경계 판정 · ML 길(PC-02) · LLM 모양 · 근거 검사(PC-09)
        self.assertEqual(doc["app_commit"], APP_COMMIT)
        self.assertEqual({f["target"]: (f["source_sha256"], f["target_sha256"]) for f in doc["files"]}, COPIES)   # D-286 조건 ①: 원본 커밋 · 파일별 sha 고정
        for f in doc["files"]:
            self.assertEqual(hashlib.sha256((PACK / f["target"]).read_bytes()).hexdigest(), f["target_sha256"], f["target"])


APP_COMMIT = "f33e12bbb10dfcfb387bc4c1b1c51cd7d4c970e8"   # 앱 dev-2(D-530 덧 3 · 84ad3f9 위 ML 길 후보 칸 지움 → no_alarm_reason) — 바뀐 사본은 alarm_route.py 하나(앞 d29f92d twf-fold-24358bd)
COPIES = {   # 앱 APP_COMMIT 원본 sha · Pack 사본 sha(import 이름만 바꾼 파일은 둘이 다름) — 사본 도구로만 바꾸고, 바꾸면 이 표도 바꿈
    "custom/knowledge.py": ("94de399012b45d52e131705bd617f2bd6bf4997c41961b3878eb67963285c001", "94de399012b45d52e131705bd617f2bd6bf4997c41961b3878eb67963285c001"),
    "custom/rules.py": ("9010cce24f1e8d6065f3c80abecbf9a09d8b653795bcab7380977a949faa8e8b", "73b29be5488720b8527c85a531788dabfa82d7a9a594e1ff41e0aa0bbc279640"),
    "custom/session.py": ("aeb712fa79b13e78041c11eb7bd7d21250805384105302268ec12cc86a66ee53", "d3166be46e7ff21198969cf9035c6d8ec112bebda733a88c43c86be0a901052b"),
    "custom/manual_sections.json": ("f88279a4f06383d46f46a00352e122f5f413f7f150befc69d797a21b21b07c94", "f88279a4f06383d46f46a00352e122f5f413f7f150befc69d797a21b21b07c94"),
    "custom/alarm_rules.json": ("9bebb5d54f5a97d4a3609afdd10008dafa84c70c0bb29f9fa762bf08128815ea", "9bebb5d54f5a97d4a3609afdd10008dafa84c70c0bb29f9fa762bf08128815ea"),
    "custom/alarm_rules.py": ("449369c40738f9ff409fd5dd27012cf0623a2bacefea091d02770e95be767942", "449369c40738f9ff409fd5dd27012cf0623a2bacefea091d02770e95be767942"),
    "custom/alarm_route.py": ("227bb7c4fd5c9a875deef43cf55685d7b9b81834e38e2ad90e3648c699918eb2", "227bb7c4fd5c9a875deef43cf55685d7b9b81834e38e2ad90e3648c699918eb2"),
    "custom/llm_base.py": ("f8901cbae8701cf9404863717fcc35726a9b01d39cb74c5e4c09d17f9b137634", "f8901cbae8701cf9404863717fcc35726a9b01d39cb74c5e4c09d17f9b137634"),
    "custom/report_grounding.py": ("b5f43fa29fb8733c1ebc2e2e3637d0573af4525a0497e8432c7a5e8fdd1d7d0b", "55a3c91a6bf2c7810a5876d3a63e2f4450c640b53580499c89ea7e27454f553d"),
}


class Contract(unittest.TestCase):   # VC-C01: Pack schema = 약속 v10 정본 바이트(dev-2 D-566 · v9 + 예외 하나 = 주제 + 점검 목록 + 조치 목록 · 사건 step)
    def test_schemas_are_contract_v10_bytes(self):
        for n in ("run_request.json", "run_result.json"):
            self.assertEqual((PACK / "schemas" / n).read_bytes(), (ROOT / "contracts/v10" / n).read_bytes(), n)
        self.assertEqual(hashlib.sha256((ROOT / "contracts/v4/run_request.json").read_bytes()).hexdigest()[:8], "cb47370b")
        self.assertEqual(hashlib.sha256((ROOT / "contracts/v4/run_result.json").read_bytes()).hexdigest()[:8], "61781c26")
        self.assertEqual(hashlib.sha256((ROOT / "contracts/v4/README.md").read_bytes()).hexdigest()[:8], "0e9e1c0c")   # EV-002 SL-3: 끝 '바로잡기' 절 덧붙임(앞 055c10a3)

    def test_examples_through_runtime(self):   # alarm · judge = 예시 값 그대로 · start = schema만(예시 값은 모양 자리 글 — README 1절, 실제 값은 새 기대값 ③)
        need_vector_db(self)
        rt = code_default(Runtime(PACK))   # 약속 v8 예시(v6 · v7 바이트 + 예외 셋) = 코드 기본(sum) 출력
        ex = ROOT / "contracts/v8/examples"
        res = jsonschema.Draft202012Validator(json.loads((PACK / "schemas/run_result.json").read_text(encoding="utf-8")))
        for n in ("alarm-boundary", "judge", "start", "start-context", "judge-exception"):   # start-context = v7 선택 칸 셋 · judge-exception = v8 exception_relation(지금 그래프엔 그 관계 없음 → 원래 판정)
            with self.subTest(n=n):
                out = rt.run(json.loads((ex / f"{n}.request.json").read_text(encoding="utf-8")))
                self.assertEqual(out["status"], "succeeded", out.get("error"))
                self.assertEqual([e.message for e in res.iter_errors(out["output"])], [])
                if not n.startswith("start") and n != "judge-exception":
                    self.assertEqual(out["output"], json.loads((ex / f"{n}.result.json").read_text(encoding="utf-8")))


class RunInput(unittest.TestCase):   # VC-02 (schema)
    schema = json.loads((PACK / "schemas/run_request.json").read_text(encoding="utf-8"))
    base = {"kind": "judge", "session_id": "S-1", "group": "CL", "similar": [], "events": [{"seq": 1, "type": "lock"}], "grades": {}, "minutes": {}}

    def test_base_ok(self):
        jsonschema.validate(self.base, self.schema)

    def test_rejects_user_and_memo_fields(self):
        for bad in ({"user_id": "tech.kim"}, {"memo": "원문"}, {"actor": "tech.kim"}, {"display_name": "김"}):
            with self.subTest(bad=bad):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({**self.base, "events": [{"seq": 1, "type": "result", **bad}]}, self.schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({**self.base, "user_id": "tech.kim"}, self.schema)
        with self.assertRaises(jsonschema.ValidationError):   # 정정 사유(자유 문장)는 reason 칸 enum 밖
            jsonschema.validate({**self.base, "events": [{"seq": 1, "type": "correction", "reason": "센서 다시 봄"}]}, self.schema)


class Judge(_NeedsVectorDB):
    CODE_DEFAULT = True   # 앱 inproc 엔진(sum) 판정과 같음

    def test_examples_equal_app_inproc(self):
        cases = [json.loads(line) for line in (ROOT / "tests/fixtures/judge_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(cases), 13)
        for c in cases:
            with self.subTest(case=c["case"], kind=c["decision"]["kind"]):
                out = self.rt.run({**c["input"], "session_id": "S-1"})   # 약속 v2: judge에 session_id(판정에는 안 씀)
                self.assertEqual(out["status"], "succeeded", out.get("error"))
                got = out["output"]["decision"]
                alts = (got.get("reason_facts") or {}).get("alternatives") or []
                self.assertEqual([a.get("desc") for a in alts], [DESC.get(a["section"]) for a in alts])   # D-560 덧층: 후보 설명 = 그래프 점검 절 설명
                self.assertEqual(_no_desc(got), c["decision"])   # 설명을 뺀 나머지는 앱 inproc 그대로

    def test_empty_events_gives_lock(self):   # 업무 기록이 없으면 첫 단계 = 잠금(R1)
        out = self.rt.run({"kind": "judge", "session_id": "S-1", "group": "CL", "similar": [], "events": [], "grades": {}, "minutes": {}})
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["decision"]["kind"], "lock")

    def test_unknown_kind_rejected_by_schema(self):   # router default(unsupported)는 schema enum 때문에 올바른 입력으로는 닿지 않음 — 입력 검사가 먼저 막음
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"kind": "predict", "session_id": "S-1", "group": "CL", "similar": [], "events": [], "grades": {}, "minutes": {}}, RunInput.schema)


STORE_HISTORY = {   # 약속 v4 README 3절(초안 r3) store 선언 — VC-C04 ④
    "type": "vector",
    "index": {"embedding": {"provider": "openai", "model": "text-embedding-3-small"}, "split": "record",
              "metadata": ["evidence_type", "source", "symptom_group", "cause", *("air_temperature_k process_temperature_k rotational_speed_rpm torque_nm tool_wear_min temp_diff_k power_w".split())],
              "rerank_norm": {"fields": "air_temperature_k process_temperature_k rotational_speed_rpm torque_nm tool_wear_min temp_diff_k power_w".split(), "from": {"source": "generated"}}},
    "filterable": ["symptom_group"],
}


class Retriever(_NeedsVectorDB):   # PS-02 PC-04 · PC-10 · VC-C04 ③ ~ ⑤ · VC-P09: start = retrieve_history(가짜 DB · 망 0)
    req = json.loads((ROOT / "contracts/v4/examples/start.request.json").read_text(encoding="utf-8"))

    def test_store_declaration_is_contract_readme(self):   # ④ filterable은 store 바로 아래 · cause는 filterable 밖
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        self.assertEqual(doc["stores"]["history"], STORE_HISTORY)
        readme = (ROOT / "contracts/v4/README.md").read_text(encoding="utf-8")
        self.assertIn("  - `filterable: [symptom_group]` — **store 바로 아래**(`stores.history.filterable`)", readme)
        node = next(n for n in doc["graphs"]["run"]["nodes"] if n["id"] == "retrieve_history")
        self.assertEqual(node["params"]["return_metadata"], ["cause"])
        self.assertEqual(set(node["params"]["rerank"]["fields"]), set(STORE_HISTORY["index"]["rerank_norm"]["fields"]))

    def test_start_uses_retriever_and_ignores_request_similar(self):   # H-5 (나) · retrieval 넷(PC-10) — 공용 retrieve_history(냉각 밖 TW 요청 · v20 갈래 뒤 냉각 node는 자동 개선 자리 · D-525 덧 14)
        req = {**self.req, "group": "TW"}
        a = self.rt.run(req)
        b = self.rt.run({**req, "similar": [{"record_id": "H-9999", "cause": "TW-2", "score": 1.0}]})
        self.assertEqual(a["status"], "succeeded", a.get("error"))
        self.assertEqual(a["output"], b["output"])
        out = a["output"]
        self.assertEqual(out["retrieval"], {"source": "packrt", "vector_db_version": "ir-history-412ea43e", "query_from": "embed", "fallback_used": False})
        self.assertEqual(len(out["similar"]), 10)
        scores = [(-s["score"], s["record_id"]) for s in out["similar"]]
        self.assertEqual(scores, sorted(scores))   # 점수 내림차순 · 같으면 record_id 순(PP-09)
        judge = self.rt.run({"kind": "judge", "session_id": "S-1", "group": req["group"], "similar": out["similar"], "events": [],
                             "grades": req["grades"], "minutes": req["minutes"]})
        self.assertEqual(out["decision"], judge["output"]["decision"])   # 같은 similar → 같은 판정(PP-01)

    def test_similar_fields_come_from_chunks(self):   # record_id = locator.record_id · cause = metadata.cause(null 포함)
        chunks = {}
        rel = SLOT / "indexes/ir-history-412ea43e/chunks.jsonl"
        for line in rel.read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            chunks[c["locator"]["record_id"]] = c
        for g in ("CL", "TW", "PW", "LD"):
            out = self.rt.run({**self.req, "group": g})["output"]
            for s in out["similar"]:
                self.assertEqual(chunks[s["record_id"]]["metadata"]["symptom_group"], g)
                self.assertEqual(s["cause"], chunks[s["record_id"]]["metadata"].get("cause"))

    def test_no_hits_gives_nulls(self):   # similar 0건 = Run 성공 · retrieval 셋 null(약속 v4 start-empty 예시)
        from custom import judge as j
        out = j.start({**self.req}, [])["result"]
        empty = json.loads((ROOT / "contracts/v4/examples/start-empty.result.json").read_text(encoding="utf-8"))
        self.assertEqual(out["retrieval"], empty["retrieval"])
        self.assertEqual(out["similar"], [])

    def test_rerank_needs_store_norm(self):   # ③ D-273: rerank_norm을 빼면 불러오기에서 거절
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            shutil.copytree(ROOT, Path(t) / "p", ignore=shutil.ignore_patterns(".venv", ".git", ".vibe-factory", ".tmp", "__pycache__"))
            (Path(t) / "agent-pack-runtime-ingest").symlink_to(SLOT.parent)
            y = Path(t) / "p/pack/pack.yaml"
            y.write_text(y.read_text(encoding="utf-8").replace("      rerank_norm: {fields:", "      #rerank_norm: {fields:"), encoding="utf-8")
            (Path(t) / "p/.venv").symlink_to(ROOT / ".venv")
            with self.assertRaises(Exception) as e:
                Runtime(Path(t) / "p/pack")
            self.assertIn("rerank", str(e.exception))


class _Sink:   # trace 이벤트를 메모리에 모음(시험만)
    def __init__(self):
        self.events = []

    def put_payload(self, value, *, role, retention_class):
        return {"ref": "sha256:0", "value": value}

    def emit(self, event):
        self.events.append(event)


class Manuals(_NeedsVectorDB):   # PS-03 PC-05 · VC-C05: retrieve_manual(store manuals) — 결과는 trace에만, 출력 칸 0

    def test_store_declaration(self):   # 매뉴얼 DB release(ir-manuals-a5c07752)의 recipe와 같은 선언 · 증상군 필터 없음(012-09)
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        self.assertEqual(doc["stores"]["manuals"], {"type": "vector", "index": {"embedding": {"provider": "openai", "model": "text-embedding-3-small"},
                                                                                "split": "section", "metadata": ["evidence_type", "source"]}})
        node = next(n for n in doc["graphs"]["run"]["nodes"] if n["id"] == "retrieve_manual")
        self.assertNotIn("filter", node["params"])
        self.assertEqual(node["writes"], ["manual_hits"])
        readers = [n["id"] for n in doc["graphs"]["run"]["nodes"] if "manual_hits" in n.get("reads", [])]
        self.assertEqual(readers, ["first_judge"])   # D-476 ④: 첫 판정만 읽고, 옵션 use_manual을 켤 때만 원인 몫으로 씀(끔 = 출력 v15와 같음 · test_options)

    def test_manual_hits_in_trace_only(self):
        sink = _Sink()
        req = json.loads((ROOT / "contracts/v4/examples/start.request.json").read_text(encoding="utf-8"))
        out = self.rt.run(req, sink=sink)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        self.assertEqual(set(out["output"]), {"decision", "preview", "similar", "retrieval"})   # 약속 v5 preview 덧칸 · 매뉴얼 결과는 출력 칸 0(옵션 use_manual 끔)
        ended = [e for e in sink.events if e.get("node_id") == "retrieve_manual" and e.get("type") == "node_ended"]
        self.assertEqual(len(ended), 1, [e.get("type") for e in sink.events][:20])
        r = ended[0]["body"]["meta"]   # trace node_ended meta(packrt 0.12.1) — 바퀴 ② 채점이 읽는 자리
        self.assertEqual((r["kind"], r["store"], r["release_id"], r["method"]), ("retrieve@2", "manuals", "ir-manuals-a5c07752", "hybrid"))
        self.assertEqual(len(r["results"]), 10)
        self.assertTrue(all(x["locator"]["doc"] == "MG-MM" and x["locator"]["section"] for x in r["results"]))

    def test_start_output_unchanged_from_ps02(self):   # 매뉴얼 node를 더해도 start 출력 = suite c1a15-start(PS-02 승인 기대값) 그대로
        ev = PACK / "evals/c1a15-start"
        exp = {json.loads(x)["case_id"]: json.loads(x) for x in (ev / "expectations.jsonl").read_text(encoding="utf-8").splitlines()}
        rt = code_default(Runtime(PACK, config_name=self.RT_CONFIG))   # PS-02 기대값 = 코드 기본(sum)
        for line in (ev / "dataset.jsonl").read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            with self.subTest(case=c["case_id"]):
                out = start_output(rt, c["input"])   # 받아 넣기 도구 다시 셈과 같은 함수(013-129)
                want = {p["path"]: p["equals"] for p in exp[c["case_id"]]["final"]}
                self.assertTrue(out == want, f"다른 칸: {sorted(k for k in set(out) | set(want) if out.get(k) != want.get(k))}")   # 정답 값 출력 0


def _card(mode="ml_predicted", th=0.5, **snap):
    base = {"air_temperature_k": 300.0, "process_temperature_k": 310.0, "rotational_speed_rpm": 1500.0, "torque_nm": 40.0, "tool_wear_min": 100.0}
    card = {"event_id": "C1A-E000000000001", "source_mode": mode, "equipment_id": "M-01", "sensor_snapshot": {**base, **snap},
            "alarm": {"source": "none"}, "data_lineage": {"source_release": "ai4i-2020@sha256:dc6630cd", "row_id": 1, "split": "demo"}}
    if mode == "ml_predicted":
        card["product_type"] = "M"
        return {"kind": "alarm", "event_card": card, "settings": {"ml_alarm_threshold": th}}
    return {"kind": "alarm", "event_card": card}


class PgConfig(unittest.TestCase):   # PS-06 PC-11 · VC-C13: pg 판 실행 설정 = flat 짝 파일에서 stores 줄만 다름 · 머리 주석 · 비밀 값 0 · DB 없이 index_unchecked
    PAIRS = {"deploy-pg.yaml": "deploy.yaml", "deploy-pg-real.yaml": "deploy-real.yaml"}
    NOTE = "공유 DB DDL · 적재(사용자 승인) 전에는 운영에 쓰지 않음"

    def test_same_as_flat_pair_except_stores(self):
        for pg, flat in self.PAIRS.items():
            a, b = (yaml.safe_load((PACK / "execution" / n).read_text(encoding="utf-8")) for n in (pg, flat))
            self.assertEqual({k: v for k, v in a.items() if k != "stores"}, {k: v for k, v in b.items() if k != "stores"}, pg)
            self.assertEqual(set(a["stores"]), set(b["stores"]), pg)
            for name, st in a["stores"].items():
                self.assertEqual(set(st), {"backend", "release", "connection", "embedding"}, (pg, name))
                self.assertEqual((st["backend"], st["connection"]), ("pgvector", {"env": "LLMOPSAGENT_RUNTIME_DSN"}), (pg, name))
                self.assertEqual((st["release"], st["embedding"]), (b["stores"][name]["release"], b["stores"][name]["embedding"]), (pg, name))
                want = {"mode": "real", "key_env": "OPENAI_API_KEY"} if "real" in pg else {"mode": "fake"}
                self.assertEqual(st["embedding"], want, (pg, name))   # 글자 값도 단언(PS-06 review N-05)

    def test_head_note_and_no_secret(self):
        pat = re.compile(r"sk-[A-Za-z0-9]{16,}|postgres(ql)?://|password\s*[:=]|(api_key|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}", re.I)
        for pg in self.PAIRS:
            text = (PACK / "execution" / pg).read_text(encoding="utf-8")
            self.assertIn(self.NOTE, text.splitlines()[0], pg)   # 머리 주석 첫 줄(PS-06 review N-05)
            self.assertIsNone(pat.search(text), pg)
            self.assertNotIn("/Users/", text, pg)

    def test_index_unchecked_without_db(self):   # DB 0 · 망 0: pg store는 고르지 못하고 index_unchecked로(011 1절 · D-308), recipe = 짝 release manifest
        need_vector_db(self, real=True)   # deploy-pg-real의 짝 = 실제 DB release manifest(EV-002 C-2)
        from packrt.ir import load_pack, releases
        doc = load_pack(str(PACK)).doc
        roots = SLOT / "indexes"
        for pg in self.PAIRS:
            out = releases.load_execution_config(PACK / "execution" / pg, doc, pg_lookup=False, collect_missing=True)
            self.assertEqual(out["index_missing"], [], pg)
            got = {u["store"]: u["index_recipe_sha256"] for u in out["index_unchecked"]}
            stores = yaml.safe_load((PACK / "execution" / pg).read_text(encoding="utf-8"))["stores"]
            want = {n: json.loads((roots / st["release"] / "manifest.json").read_text(encoding="utf-8"))["index_recipe_sha256"] for n, st in stores.items()}
            self.assertEqual(got, want, pg)


APP_GROUP_NAME_LINE = 'GROUP_NAME = {"TW": "공구 마모 경고", "PW": "출력 이탈", "CL": "온도 차 저하", "LD": "토크 과부하"}'
# = 앱 server/src/c1a15/api/sessions.py:52(7b79da9 · 253aa44 같은 줄, sha256 519a9f3f…) — 앱이 바꾸면 이 줄 · judge.GROUP_NAME을 함께(대조 증거 PS-06 later L0)


class LaterL0(unittest.TestCase):   # PS-06 뒤 later L0(D-327 (4)): PS-02 review N-02 · N-04 · N-06 · PS-03 N-03
    def test_group_name_is_app_copy(self):   # PS-02 N-06: 질의 글 규칙의 증상군 이름 = 앱 줄 사본(손으로 옮긴 값이 조용히 어긋나지 않게)
        from custom import judge as j
        self.assertEqual(hashlib.sha256(APP_GROUP_NAME_LINE.encode()).hexdigest(), "519a9f3f6dedd73b2afb59f42ce54d5056ab5f0c573a3ee38c352a75c4810e88")
        self.assertEqual(j.GROUP_NAME, ast.literal_eval(APP_GROUP_NAME_LINE.split(" = ", 1)[1]))

    def test_exact_final_tie_is_record_id_order(self):   # PS-02 N-02 · PS-03 N-03: 최종 점수가 정확히 같은 두 조각(lexical · vector 순위가 엇갈려 RRF 합이 같고 센서도 같음)
        need_vector_db(self)
        import tempfile
        from packrt.index.build import build
        from packrt.index.corpus import make
        from custom import judge as j
        req = {**json.loads((ROOT / "contracts/v4/examples/start.request.json").read_text(encoding="utf-8")), "group": "TW"}   # 공용 retrieve_history(냉각 밖 · v20 갈래 뒤 냉각 node는 자동 개선 자리 · D-525 덧 14)
        q = j.history_query(req)["history_query"]["text"]   # = 이 요청의 질의 글(TW 이름 + 방향)
        sens = {**req["query"]["sensor_snapshot"], "temp_diff_k": 7.8, "power_w": 7864}
        for texts in ((q + " " + q, q), (q, q + " " + q)):   # 어느 쪽이 lexical · vector를 이기든
            with tempfile.TemporaryDirectory() as t:
                t = Path(t)
                rows = [{"doc": r, "record_id": r, "text": x, "metadata": {"evidence_type": "history", "source": "generated", "symptom_group": "TW", "cause": "TW-1", **sens}}
                        for r, x in zip(("H-9001", "H-9002"), texts)]
                (t / "d.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
                c = make(t / "d.jsonl", "c1a15-tie-v1", t / "corpus")
                corpus = t / "corpus" / c["release_id"] if (t / "corpus" / c["release_id"]).exists() else Path(c["location"])
                rel = build(PACK, "run", "retrieve_history", corpus, t / "idx", embedding="fake")["release_id"]
                (t / "c.yaml").write_text("config_version: 1\nentrypoint: run\n" f"environment: {{kind: local-python, python: {ROOT / '.venv/bin/python'}}}\n"
                                          f"release_roots: [{ROOT / 'releases'}, {t / 'idx'}, {SLOT}]\n"
                                          "models:\n  failure_classifier: {release: mr-ai4i-lgbm-large-v1}\n"
                                          f"stores:\n  history: {{backend: flat-local, release: {rel}, embedding: {{mode: fake}}}}\n"
                                          "  manuals: {backend: flat-local, release: ir-manuals-a5c07752, embedding: {mode: fake}}\n", encoding="utf-8")
                sink = _Sink()
                out = Runtime(PACK, config_name=str(t / "c.yaml")).run(req, sink=sink)
                self.assertEqual(out["status"], "succeeded", out.get("error"))
                hits = next(e["body"]["meta"]["results"] for e in sink.events if e.get("node_id") == "retrieve_history" and e.get("type") == "node_ended")
                self.assertEqual({(h["ranks"]["lexical"], h["ranks"]["vector"]) for h in hits}, {(1, 2), (2, 1)})   # 순위가 엇갈림
                sim = out["output"]["similar"]
                self.assertEqual(sim[0]["score"], sim[1]["score"])
                self.assertEqual([s["record_id"] for s in sim], ["H-9001", "H-9002"])   # 같으면 record_id 오름차순(PP-09)

    def test_rerank_inputs_always_complete(self):   # PS-02 N-04 ①: 약속에 맞는 요청이면 재정렬 센서 7칸이 늘 다 있음 → rerank_invalid는 Run에서 안 남
        from custom import judge as j
        fields = set(STORE_HISTORY["index"]["rerank_norm"]["fields"])
        reqs = [json.loads(x)["input"] for x in (PACK / "evals/c1a15-start/dataset.jsonl").read_text(encoding="utf-8").splitlines()]
        reqs += [json.loads((ROOT / "contracts/v4/examples" / n).read_text(encoding="utf-8")) for n in ("start.request.json", "start-with-bridge.request.json")]
        for r in reqs:
            got = j.history_query(r)["history_query"]["sensors"]
            self.assertLessEqual(fields, {k for k, v in got.items() if v is not None})

    def test_rerank_invalid_fails_run_at_node(self):   # PS-02 N-04 ②: 센서 하나가 빠지면 Run 실패 · code rerank_invalid · 자리 = 이력 node(냉각 요청 → v20 run.retrieve_history_cl · 임시 사본만 고침)
        need_vector_db(self)
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            shutil.copytree(ROOT, Path(t) / "p", ignore=shutil.ignore_patterns(".venv", ".git", ".vibe-factory", ".tmp", "__pycache__"))
            (Path(t) / "agent-pack-runtime-ingest").symlink_to(SLOT.parent)
            (Path(t) / "p/.venv").symlink_to(ROOT / ".venv")
            jp = Path(t) / "p/pack/custom/judge.py"
            src = jp.read_text(encoding="utf-8")
            old = 'sensors = {**q["sensor_snapshot"], **alarm.derived(q["sensor_snapshot"])}'
            self.assertEqual(src.count(old), 1)
            jp.write_text(src.replace(old, old + "; sensors.pop(\"power_w\")"), encoding="utf-8")
            req = json.loads((ROOT / "contracts/v4/examples/start.request.json").read_text(encoding="utf-8"))
            for m in [k for k in sys.modules if k == "custom" or k.startswith("custom.")]:
                sys.modules.pop(m)
            try:
                out = Runtime(Path(t) / "p/pack").run(req)
            finally:
                for m in [k for k in sys.modules if k == "custom" or k.startswith("custom.")]:
                    sys.modules.pop(m)
                sys.path[:] = [p for p in sys.path if not p.startswith(t)]
            self.assertEqual(out["status"], "failed")
            self.assertEqual((out["error"]["code"], out["error"]["node_path"]), ("rerank_invalid", "run.retrieve_history_cl"))


class FailClosed(unittest.TestCase):   # EV-002 CH-01: 운영 전 설정이 잘못 골라져도 조용히 돌지 않음(DB 0 · 망 0 — DB가 있는 두 경우는 기록 폴더 컨테이너 증거 VR-04)
    START = ROOT / "contracts/v4/examples/start.request.json"

    def test_pg_configs_refuse_without_dsn(self):   # ① DSN env 없음 → 불러오기 실패(글에 env 이름만) · Runtime 0(flat 대체 0)
        need_vector_db(self)
        from unittest import mock
        from packrt.ir.releases import ReleaseError
        with mock.patch.dict(os.environ):
            os.environ.pop("LLMOPSAGENT_RUNTIME_DSN", None)
            for cfg in ("execution/deploy-pg.yaml", "execution/deploy-pg-real.yaml"):
                with self.subTest(cfg=cfg), self.assertRaises(ReleaseError) as cm:
                    Runtime(PACK, env={}, config_name=cfg)
                msg = str(cm.exception)
                self.assertIn("LLMOPSAGENT_RUNTIME_DSN", msg)
                self.assertNotRegex(msg, r"postgres(ql)?://|password|127\.0\.0\.1|release_roots")

    def test_real_db_config_without_key_and_address_fails_at_retriever(self):   # ③ 키 · 주소 env 둘 다 없음 → start Run 실패(리트리버 node) · socket 시도 0 · 비밀 0(GAP-004: 주소만 있으면 키 없이 나감 — README 표)
        need_vector_db(self, real=True)
        import socket
        from unittest import mock
        tries = []

        def refuse(*a, **k):
            tries.append(repr(a)[:80])
            raise OSError("시험에서 망 막음")
        rt = Runtime(PACK, env={}, config_name="execution/deploy-real.yaml")   # env {} = 키 · 주소 · PACKRT_ENV_FILE 0(이 Runtime의 provider만)
        with mock.patch.object(socket.socket, "connect", refuse), mock.patch.object(socket, "create_connection", refuse), mock.patch.object(socket, "getaddrinfo", refuse):
            out = rt.run(json.loads(self.START.read_text(encoding="utf-8")))
        self.assertEqual(out["status"], "failed")
        self.assertIn(out["error"]["node_path"], ("run.retrieve_history", "run.retrieve_history_cl", "run.retrieve_manual"))   # v20 갈래: 냉각 요청은 retrieve_history_cl
        self.assertIn("OPENAI_BASE_URL", out["error"]["message"])   # 주소 env가 없다는 글(값 0)
        self.assertEqual(tries, [])
        self.assertNotRegex(json.dumps(out, ensure_ascii=False), r"sk-[A-Za-z0-9]|Bearer")


class FlowCheck(unittest.TestCase):   # v7 흐름 고침(013-07 (나) · D-376): packrt 편집 흐름 검사 F1 · F3 오류 0 — 갈래별 함수 node(decide_alarm_boundary · finish_report_1)
    def test_flow_check_clean(self):
        from packrt.ir import flow
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        self.assertEqual(flow.check(doc, PACK), [])


class SlotRule(unittest.TestCase):   # EV-002 CH-03: 자리 쓰는 시험(Runtime을 만들거나 SLOT · 저장소 밖 경로를 읽는 시험)은 모두 need_vector_db를 거침 — 자리 없이 돎
    def test_every_slot_test_calls_helper(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        own = {"need_vector_db", "_release_ok"}   # 자리를 확인하는 쪽(SLOT을 읽는 것이 일)

        def uses(node, names):
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    f = n.func
                    if (f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None) in names:
                        return True
                if isinstance(n, ast.Name) and n.id == "SLOT" or isinstance(n, ast.Constant) and isinstance(n.value, str) and "agent-pack-runtime-ingest" in n.value:
                    return True
            return False

        def calls(node, names):
            return any(isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
                       and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) in names for n in ast.walk(node))

        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name not in own}
        need = {"Runtime"}
        while True:
            more = {k for k, f in funcs.items() if k not in need and uses(f, need)}
            if not more:
                break
            need |= more
        bad = []
        for c in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            if c.name in ("_NeedsVectorDB", "SlotRule"):   # 확인하는 쪽(자리를 쓰지 않고 글만 읽음)
                continue
            body = [s for s in c.body if not isinstance(s, ast.FunctionDef)]
            if any(uses(s, need) for s in body):
                bad.append(f"{c.name}: 클래스 몸에서 자리를 씀")
            if any(isinstance(b, ast.Name) and b.id == "_NeedsVectorDB" for b in c.bases):
                continue
            meths = {m.name: m for m in c.body if isinstance(m, ast.FunctionDef)}
            helpers = {k for k, m in meths.items() if not k.startswith("test") and calls(m, {"need_vector_db"})}
            mneed = set(need)
            while True:
                more = {k for k, m in meths.items() if not k.startswith("test") and k not in mneed and uses(m, mneed)}
                if not more:
                    break
                mneed |= more
            for k, m in meths.items():
                if k.startswith("test") and uses(m, mneed) and not calls(m, {"need_vector_db"} | helpers):
                    bad.append(f"{c.name}.{k}")
        self.assertEqual(bad, [])


class SuiteGrammar(unittest.TestCase):   # PS-02 review N-05: 기대값 줄 = LLMOps suites.py 문법(node check는 {reached: false}만 · 아니면 path + 비교 하나)
    OPS = {"equals", "contains", "contains_all", "in", "max_key", "not_equals", "not_contains_any", "any_of"}   # max_key(+ tie_order) · step · rule = LLMOps EV-079(D-463 덧 5 · 20, c1a15_order) · not_equals · not_contains_any · any_of(하위 줄도 같은 문법) = LLMOps bea14c6(D-470, report v4)

    def _check_line(self, chk, where):   # 실패 글은 칸 이름만 — 정답 값 출력 0(10-29 오케 규칙)
        self.assertEqual(len(self.OPS & chk.keys()), 1, (*where, sorted(chk)))
        self.assertLessEqual(set(chk), {"path", "step", "rule"} | self.OPS | ({"tie_order"} if "max_key" in chk else set()), (*where, sorted(chk)))
        if "any_of" in chk:
            self.assertIsInstance(chk["any_of"], list, (*where, "any_of"))
            self.assertTrue(chk["any_of"], (*where, "any_of 비어 있음"))
            for sub in chk["any_of"]:
                self._check_line(sub, (*where, "any_of"))

    def test_expectations_use_llmops_check_grammar(self):
        for d in sorted((PACK / "evals").iterdir()):
            for line in (d / "expectations.jsonl").read_text(encoding="utf-8").splitlines():
                c = json.loads(line)
                self.assertLessEqual(set(c), {"case_id", "final", "nodes"}, (d.name, c["case_id"]))   # 모르는 칸 거절(PS-03 review N-08)
                for chk in c["final"]:
                    self._check_line(chk, (d.name, c["case_id"]))
                for nid, chks in (c.get("nodes") or {}).items():
                    for chk in chks:
                        if "reached" in chk:
                            self.assertEqual(chk, {"reached": False}, (d.name, c["case_id"], nid))   # reached: true는 늘 틀림으로 셈
                        else:
                            self.assertEqual(len(self.OPS & chk.keys()), 1, (d.name, c["case_id"], nid, sorted(chk)))
                            self.assertLessEqual(set(chk), {"path"} | self.OPS, (d.name, c["case_id"], nid, sorted(chk)))


class Alarm(_NeedsVectorDB):   # 약속 v2 kind alarm(PC-02): 경계 길 · ML 길(자리 모델 — 되돌림 설정 standin.yaml로) · 예측 실패
    RT_CONFIG = "execution/standin.yaml"
    rules = {r["group"]: r["boundaries"] for r in json.loads((PACK / "custom/alarm_rules.json").read_text(encoding="utf-8"))["rules"]}

    def alarm(self, req):
        out = self.rt.run(req)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        return out["output"]["alarm"]

    def test_boundary_path(self):
        a = self.alarm(_card("sensor_boundary", process_temperature_k=300.0 + self.rules["CL"]["low"] - 0.5))
        self.assertEqual((a["alarm"], a["group"], a["group_source"], a["ml"], a["no_alarm_reason"]), (True, "CL", "sensor_boundary", None, None))
        self.assertEqual(a["alarm_block"]["direction"], "경계 아래")
        quiet = self.alarm(_card("sensor_boundary"))
        self.assertEqual((quiet["alarm"], quiet["group"], quiet["alarm_block"]), (False, None, None))

    def test_ml_path_standin_risk_and_types(self):   # VC-P07: 자리 모델 값(v0 예시와 같은 뜻)
        normal = self.alarm(_card())
        self.assertLess(normal["ml"]["risk"], 0.05)
        self.assertFalse(normal["alarm"])
        beyond = self.alarm(_card(process_temperature_k=300.0 + self.rules["CL"]["low"] - 0.5))
        self.assertGreater(beyond["ml"]["risk"], 0.9)
        self.assertEqual(beyond["ml"]["top_k"][0]["type"], "HDF")
        self.assertEqual((beyond["alarm"], beyond["group"], beyond["group_source"]), (True, "CL", "sensor_boundary"))   # 경계 먼저
        self.assertEqual(beyond["ml"]["model_release"], "mr-c1a15-standin-v1")
        wear = self.alarm(_card(tool_wear_min=self.rules["TW"]["high"] + 30))   # D-526 덧 6(v22): TWF는 예측에서 뺌 — 공구 마모는 경계 규칙(TW)이 맡고 ML 유형 후보엔 TWF 0
        self.assertNotIn("TWF", [t["type"] for t in wear["ml"]["top_k"]])
        self.assertEqual(self.alarm(_card(torque_nm=self.rules["LD"]["high"] + 2, rotational_speed_rpm=1200.0))["ml"]["top_k"][0]["type"], "OSF")
        self.assertEqual(self.alarm(_card(torque_nm=10.0, rotational_speed_rpm=1500.0))["ml"]["top_k"][0]["type"], "PWF")

    def test_threshold_comes_from_request(self):   # 문턱 = 요청 settings(앱 사용자 설정) 한 곳
        req = _card(process_temperature_k=300.0 + self.rules["CL"]["low"])   # 위험도 ≈ 0.5
        lo, hi = self.alarm(_card(th=0.1, process_temperature_k=300.0 + self.rules["CL"]["low"])), self.alarm(_card(th=0.9, process_temperature_k=300.0 + self.rules["CL"]["low"]))
        self.assertEqual((lo["ml"]["threshold"], hi["ml"]["threshold"]), (0.1, 0.9))
        self.assertTrue(lo["ml"]["alarm"])
        self.assertFalse(hi["ml"]["alarm"])
        self.assertIn("settings", req)

    def test_predict_failure_is_run_failure_at_predict_node(self):   # D-275: 예측 node 실패 = Run 실패 → 앱이 알람 없음 사유 model_failure로 읽음
        # 무한대 센서 값 → 특징 칸이 유한하지 않음 → predict@1 feature_not_number(Pack Runtime 직접 — 배포 서버 길이면 JSON 밖 값이라 422가 먼저)
        out = self.rt.run(_card(tool_wear_min=float("inf")))
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"]["node_path"], "run.predict_failure")
        self.assertNotIn("alarm", out.get("output") or {})

    def test_no_on_error_in_graph(self):   # LLMOps가 on_error 길을 등록 대조에서 세지 않음(UF-03, llmops EV-045 전)
        doc = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
        self.assertEqual([n["id"] for g in doc["graphs"].values() for n in g["nodes"] if "on_error" in n], [])

    def test_bad_input_rejected(self):
        self.assertEqual(self.rt.run({"kind": "alarm", "event_card": {"event_id": "x"}})["status"], "failed")
        bad = _card()
        bad["event_card"]["sensor_snapshot"]["TWF"] = 1   # 라벨 칸은 받지 않음
        self.assertEqual(self.rt.run(bad)["status"], "failed")
        mixed = _card()
        mixed["group"] = "CL"   # 판정 칸이 섞이면 거절
        self.assertEqual(self.rt.run(mixed)["status"], "failed")
        no_type = _card()
        del no_type["event_card"]["product_type"]   # ML 길은 제품 등급 필수(D-264)
        self.assertEqual(self.rt.run(no_type)["status"], "failed")

    def test_model_missing_fails_to_load(self):   # model release가 깨지면 Run 전에 불러오기 실패(배포 고장 — 앱은 503 pack_unavailable, 약속 B)
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as t:
            shutil.copytree(ROOT / "pack", Path(t) / "pack")
            shutil.copytree(ROOT / "releases", Path(t) / "releases")
            (Path(t) / "releases/models/mr-c1a15-standin-v1/model.json").unlink()
            (Path(t) / "releases/models/mr-ai4i-lgbm-large-v1/model.onnx").unlink()
            (Path(t) / ".venv").symlink_to(ROOT / ".venv")
            with self.assertRaises(Exception):
                Runtime(Path(t) / "pack", config_name="execution/standin.yaml")
            with self.assertRaises(Exception):   # 사용자 모델(기본 설정)도 파일이 없으면 Run 전에 불러오기 실패
                Runtime(Path(t) / "pack")


class UserModel(_NeedsVectorDB):   # PS-04 PC-06 · VC-C06 · D-546(dev-2): 사용자 모델 mr-ai4i-lgbm-large-v2(TWF 뺀 4클래스 · task10-pack dev-2 69157c8 사본)
    REL = ROOT / "releases/models/mr-ai4i-lgbm-large-v2"   # rt = 기본 실행 설정 = 사용자 모델
    CASES = ROOT / "tests/fixtures/ml_alarm_cases_v2.jsonl"   # task10-pack handoff/d546 ml-alarm-cases 23행(정답 칸 · 확률 뺌 — 기대 알람 길만)

    def test_release_is_canonical_copy(self):   # handoff release-v2.md의 sha 그대로 · v1 폴더도 그대로(되돌림 자리)
        self.assertEqual(hashlib.sha256((self.REL / "manifest.json").read_bytes()).hexdigest(), "4900e2fecf7e2ce5f1d51c2ebf66aa35a6dc8c836b19f84b545051d4af0c039d")
        m = json.loads((self.REL / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256((self.REL / "model.onnx").read_bytes()).hexdigest(), m["artifact"]["sha256"])
        self.assertTrue(m["artifact"]["sha256"].startswith("7df7d63c"))
        v1 = ROOT / "releases/models/mr-ai4i-lgbm-large-v1/manifest.json"
        self.assertEqual(hashlib.sha256(v1.read_bytes()).hexdigest(), "2b3bfe7f976fea8eb7f94d19064560dfacc71939f3b2bebad5673bd27650462d")

    def test_app_ml_cases_through_alarm_run(self):   # 사람 손 길 · 실제 꼴: 앱 시험 자료 23행 → Pack alarm Run(ML 길) = 기대 알람 · 증상군 · 사유 · 위험도
        for line in self.CASES.read_text(encoding="utf-8").splitlines():   # 공구 마모 TW 경계(M3-18 · 19) · 경계 넘어도 ML 정상이면 알람 0(M3-16 · 17) 포함
            c = json.loads(line)
            req = _card(**c["sensor_snapshot"])
            req["event_card"]["product_type"] = c["product_type"]
            a = self.rt.run(req)["output"]["alarm"]
            e = c["expect"]
            self.assertEqual((a["alarm"], a["group"], a["group_source"], a["no_alarm_reason"]), (e["alarm"], e["group"], e["group_source"], e["no_alarm_reason"]), c["case"])
            self.assertAlmostEqual(a["ml"]["risk"], e["risk"], places=5, msg=c["case"])
            self.assertEqual(a["ml"]["top_k"][0]["type"], e["predicted_type"], c["case"])

    def test_ml_type_alarm_to_graph_start(self):   # D-546 4: ML 유형(HDF) → 증상군(CL · ml_type) → start Run의 원인 후보 · 첫 점검 · 조치 = 그래프(templates/graph/current.json)
        from custom import graph_query
        c = next(json.loads(l) for l in self.CASES.read_text(encoding="utf-8").splitlines() if json.loads(l)["case"] == "M3-01")
        req = _card(**c["sensor_snapshot"])
        req["event_card"]["product_type"] = c["product_type"]
        a = self.rt.run(req)["output"]["alarm"]
        self.assertEqual((a["group"], a["group_source"], a["ml"]["top_k"][0]["type"]), ("CL", "ml_type", "HDF"))
        start = {**json.loads((ROOT / "contracts/v8/examples/start.request.json").read_text(encoding="utf-8")), "group": a["group"],
                 "event_id": a["event_id"], "query": {"direction": None, "sensor_snapshot": c["sensor_snapshot"]}}
        out = self.rt.run(start)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        k = graph_query.knowledge_from_graph()
        p = out["output"]["preview"]
        self.assertEqual(set(p["probabilities"]), {x.code for x in k.causes["CL"]})   # 원인 후보 = 그래프 증상군 → 원인
        self.assertIn(p["section"], {x.section_id for x in k.checks["CL"]})          # 첫 점검 = 그래프가 그 원인을 가리키는 점검
        self.assertEqual(k.check(p["section"]).cause, p["cause"])
        self.assertTrue(k.cause(p["cause"]).action_section)                          # 그 원인의 조치 = 그래프 원인 → 조치

    def test_tool_wear_rule(self):   # 실패 쪽: 마모 = 215(넘지 않음)는 TW 아님 · 마모 > 215면 ML 알람이 있든(앱 사본 route: 경계 증상군) 없든(D-546 규칙) TW
        from custom import alarm
        c = {json.loads(l)["case"]: json.loads(l) for l in self.CASES.read_text(encoding="utf-8").splitlines()}
        req = _card(**{**c["M3-18"]["sensor_snapshot"], "tool_wear_min": 215.0})
        req["event_card"]["product_type"] = c["M3-18"]["product_type"]
        self.assertFalse(self.rt.run(req)["output"]["alarm"]["alarm"])
        req = _card(tool_wear_min=230.0)
        basis = alarm.prepare_alarm(req)["alarm_basis"]
        pred = {"type": "HDF", "probability": 0.9, "probabilities": {"HDF": 0.9, "NONE": 0.05, "OSF": 0.03, "PWF": 0.02}, "model_release": "x"}
        a = alarm.decide_alarm(req, basis, pred)["result"]["alarm"]
        self.assertEqual((a["alarm"], a["group"], a["group_source"]), (True, "TW", "sensor_boundary"))
        none = {"type": "NONE", "probability": 0.95, "probabilities": {"HDF": 0.02, "NONE": 0.95, "OSF": 0.02, "PWF": 0.01}, "model_release": "x"}
        a = alarm.decide_alarm(req, basis, none)["result"]["alarm"]
        self.assertEqual((a["alarm"], a["group"], a["group_source"], a["ml"]["risk"]), (True, "TW", "sensor_boundary", 0.05))

    def test_type_code_and_classes_match_manifest(self):
        from custom import features
        m = json.loads((self.REL / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(features.TYPE_CODE, m["feature_schema"]["encodings"]["type_code"])
        pred = json.loads((PACK / "schemas/prediction.json").read_text(encoding="utf-8"))
        self.assertLessEqual(set(m["classes"]), set(pred["properties"]["type"]["enum"]))   # v2 클래스 ⊂ Prediction enum(TWF는 되돌림 모델 v1 · 자리 모델 몫으로 남김 — D-546)
        self.assertNotIn("TWF", m["classes"])
        mi = json.loads((PACK / "schemas/model_input.json").read_text(encoding="utf-8"))
        self.assertTrue(set(m["feature_schema"]["fields"]) <= set(mi["required"]))

    def test_compare_examples_through_alarm_run(self):   # task10-pack 기준값(onnx 확률 · 4클래스라 접기 없음) 10건 = Pack alarm Run의 위험도 · top_k
        inv = {v: k for k, v in json.loads((self.REL / "manifest.json").read_text(encoding="utf-8"))["feature_schema"]["encodings"]["type_code"].items()}
        ex = json.loads((self.REL / "compare.json").read_text(encoding="utf-8"))["examples_val"]
        self.assertEqual(len(ex), 8)   # v2 compare 예시 8개(handoff release-v2.md · rt015)
        for e in ex:
            i = e["input"]
            req = _card(air_temperature_k=i["air"], process_temperature_k=i["proc"], rotational_speed_rpm=i["rpm"], torque_nm=i["torque"], tool_wear_min=i["wear"])
            req["event_card"]["product_type"] = inv[int(i["type_code"])]
            out = self.rt.run(req)
            self.assertEqual(out["status"], "succeeded", out.get("error"))
            ml = out["output"]["alarm"]["ml"]
            want = dict(e["onnx"])
            self.assertNotIn("TWF", want)
            self.assertEqual(ml["model_release"], "mr-ai4i-lgbm-large-v2")
            self.assertAlmostEqual(ml["risk"], round(1 - want["NONE"], 6), places=5)
            top = sorted(((t, p) for t, p in want.items() if t != "NONE"), key=lambda x: (-x[1], x[0]))[:3]
            for got, (t, p) in zip(ml["top_k"], top, strict=True):
                self.assertAlmostEqual(got["probability"], p, places=5)
                if abs(got["probability"] - p) < 1e-9:
                    self.assertEqual(got["type"], t)

    def test_rollback_is_one_config_line(self):   # 되돌림 = 실행 설정 한 줄(standin.yaml) — 같은 요청이 자리 모델로 돎
        req = _card()
        a = self.rt.run(req)["output"]["alarm"]["ml"]["model_release"]
        b = Runtime(PACK, config_name="execution/standin.yaml").run(req)["output"]["alarm"]["ml"]["model_release"]
        self.assertEqual((a, b), ("mr-ai4i-lgbm-large-v2", "mr-c1a15-standin-v1"))


import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from fake import rule as fake_rule  # noqa: E402


DESC = {s["app_no"]: s["desc"] for s in json.loads((PACK / "templates/graph/current.json").read_text(encoding="utf-8"))["sections"] if s.get("app_no") and s.get("desc")}   # D-560 점검 절 설명(그래프 파일)


def _no_desc(d):   # D-560: Pack 결정 덧층(후보 설명 desc)을 뺀 꼴 — 앱 사본 엔진과 견줄 때
    rf = d.get("reason_facts")
    if not isinstance(rf, dict) or not rf.get("alternatives"):
        return d
    return {**d, "reason_facts": {**rf, "alternatives": [{k: v for k, v in a.items() if k != "desc"} for a in rf["alternatives"]]}}


class FakeProvider:   # 배포 서버 · LLMOps 가짜 provider와 같은 일: prompt → tools/fake/rule.py respond_text
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = 0, fail_at

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, **kw):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("시험 provider 오류(망 0)")
        text, _ = fake_rule.respond_text(messages[-1]["content"])
        return text, {"prompt_tokens": 0, "completion_tokens": 0}


def _report_req(mark=None):
    r = json.loads((ROOT / "contracts/v3/examples/report.request.json").read_text(encoding="utf-8"))
    if mark:
        r["report_draft"]["unresolved"] = "없음 " + mark
    return r


class Report(unittest.TestCase):   # PS-05 PC-09 · VC-C10 · VC-C12: 보고서 문장 node(가짜 provider)
    def run_report(self, req, fail_at=None):
        need_vector_db(self)
        p = FakeProvider(fail_at)
        return Runtime(PACK, provider=p).run(req), p.calls

    def test_fake_passthrough_equals_draft(self):   # ① 기대값 = 초안 그대로(= 앱 fixture 출력, 약속 v3 결과 예시와 비교하지 않음)
        req = _report_req()
        out, calls = self.run_report(req)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        for f in ("cause", "action"):
            self.assertEqual(out["output"]["report"][f], {"text": req["report_draft"][f], "fallback": False, "attempts": 1, "reason": None})
        self.assertEqual(calls, 1)

    def test_fallback_path(self):   # ② 첫 답 · 다시 쓴 답 모두 근거 밖 → cause 원문 · ungrounded_number, action 그대로
        req = _report_req("[시험:근거밖수치]")
        out, calls = self.run_report(req)
        rep = out["output"]["report"]
        self.assertEqual(rep["cause"], {"text": req["report_draft"]["cause"], "fallback": True, "attempts": 2, "reason": "ungrounded_number"})
        self.assertEqual(rep["action"], {"text": req["report_draft"]["action"], "fallback": False, "attempts": 1, "reason": None})
        self.assertEqual(calls, 2)

    def test_rewrite_passes(self):   # ④ 첫 답만 근거 밖 → 다시 쓴 문장 · attempts 2 · fallback false · reason null
        req = _report_req("[시험:첫답만근거밖]")
        rep = self.run_report(req)[0]["output"]["report"]
        self.assertEqual(rep["cause"], {"text": req["report_draft"]["cause"] + " (다시 씀)", "fallback": False, "attempts": 2, "reason": None})
        self.assertEqual(rep["action"], {"text": req["report_draft"]["action"], "fallback": False, "attempts": 1, "reason": None})

    def test_llm_failure_is_run_failure(self):   # VC-C12 ① write · ② rewrite
        out, _ = self.run_report(_report_req(), fail_at=1)
        self.assertEqual((out["status"], out["error"]["node_path"]), ("failed", "run.write_report"))
        out, _ = self.run_report(_report_req("[시험:첫답만근거밖]"), fail_at=2)
        self.assertEqual((out["status"], out["error"]["node_path"]), ("failed", "run.rewrite_report"))
        self.assertFalse(out.get("output"))

    def test_reason_kinds(self):
        from custom import report
        g = report.basis(_report_req())
        self.assertEqual(report.judge_text("", g), (False, "empty"))
        self.assertEqual(report.judge_text("4.9절 점검", g), (False, "ungrounded_section"))
        self.assertEqual(report.judge_text("H-9999 사례", g), (False, "ungrounded_record"))
        self.assertEqual(report.judge_text("온도 차 7.8 K · 1234 W", g), (False, "ungrounded_number"))
        self.assertEqual(report.judge_text("온도 차 7.8 K · H-0226 · 4.3.2절", g), (True, None))

    def test_record_ids_per_field(self):   # D-469 (2) · 013-73 A(덧 3 꼴): 칸의 기록 ID = 그 칸에 ID가 있으면 그것만, 없으면 초안 '근거 기록' 칸 ID — 둘 다 ∩ 상위 10건
        from custom import report
        req = _report_req()   # 근거 기록 = H-0226 · H-0241 · H-0162 · 상위 10건에 H-0158 · H-0202 …
        req["report_draft"]["cause"] = "냉각 팬 고장(CL-1, 참고 기록 H-0226 · H-9999)"   # H-9999 = 상위 10건 밖
        g = report.basis(req)
        self.assertEqual(report.judge_text("냉각 팬 고장(CL-1) · H-0241", g), (True, None))   # 고치기 전 빈틈: 전역 기준은 다른 기록 ID도 통과
        gc, ga = report.field_basis(req, "cause"), report.field_basis(req, "action")
        self.assertEqual(gc.top_record_ids, frozenset({"H-0226"}))   # 그 칸에 ID 있음 → 그것만
        self.assertEqual(ga.top_record_ids, frozenset({"H-0226", "H-0241", "H-0162"}))   # 앱 초안 조치 칸 = ID 없음 → 근거 기록 칸
        self.assertEqual(report.judge_text("냉각 팬 고장(CL-1) · H-0241", gc), (False, "ungrounded_record"))   # 초안이 가리킨 기록을 다른 ID로 바꿔 씀(RF01 꼴)
        self.assertEqual(report.judge_text("냉각 팬 고장(CL-1) · H-9999", gc), (False, "ungrounded_record"))   # 상위 10건 밖은 지금처럼
        self.assertEqual(report.judge_text("냉각 팬 고장(CL-1, 참고 기록 H-0226)", gc), (True, None))   # 같은 칸 · 상위 10건 안
        self.assertEqual(report.judge_text("냉각 팬 고장(CL-1)", gc), (True, None))   # ID 지움
        self.assertEqual(report.judge_text(req["report_draft"]["action"], ga), (True, None))   # 앱 꼴 초안 그대로
        self.assertEqual(report.judge_text(req["report_draft"]["action"] + " · 근거 H-0241", ga), (True, None))   # 정상 인용(근거 기록 칸 ID) — V4 첫 답 42칸 꼴
        self.assertEqual(report.judge_text(req["report_draft"]["action"] + " · 근거 H-0158", ga), (False, "ungrounded_record"))   # 근거 기록 밖 상위 10건 ID

    def test_exception_section_report(self):   # D-546 덧 36(F1 걷기 보고서 422): 예외 절(EX-…)을 거친 보고서 — 요청을 받고(고치기 전 invalid_input) · 조치 칸 EX 절 인용 통과(고치기 전 'R-0001'을 기록 ID로 셈)
        from custom import report
        req = json.loads((ROOT / "contracts/v8/examples/report-exception.request.json").read_text(encoding="utf-8"))
        out, calls = self.run_report(req)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        for f in ("cause", "action"):
            self.assertEqual(out["output"]["report"][f], {"text": req["report_draft"][f], "fallback": False, "attempts": 1, "reason": None})
        ga = report.field_basis(req, "action")
        self.assertEqual(ga.top_record_ids, frozenset({"H-0226", "H-0241", "H-0162"}))   # EX 절 안 'R-0001'은 그 칸 기록 ID가 아님 → 근거 기록 칸
        self.assertEqual(report.judge_text("필터 교체(EX-R-0001) · 근거 H-0241", ga), (True, None))
        self.assertEqual(report.judge_text("필터 교체(EX-R-0009)", ga), (False, "ungrounded_section"))   # 실패 쪽: 세션에 없는 EX 절
        self.assertEqual(report.found("필터 교체(EX-R-0009) · H-9999", ga), ["EX-R-0009", "H-9999"])
        self.assertEqual(report.judge_text("필터 교체(EX-R-0001)", report.field_basis(_report_req(), "action")), (False, "ungrounded_section"))   # EX 절 없는 세션

    def test_check_details_show_found(self):   # 013-73 A: 다시 쓰기가 무엇을 지울지 — 떨어진 칸에 걸린 낱말(found)
        from custom import report
        req = _report_req()
        out = report.grounded(req, {"cause": "냉각 팬 고장(CL-1) · H-0158 · 4.9절", "action": req["report_draft"]["action"]})
        self.assertFalse(out["passed"])
        self.assertEqual(out["details"]["cause"], {"ok": False, "reason": "ungrounded_section", "found": ["4.9", "H-0158"]})
        self.assertEqual(out["details"]["action"], {"ok": True, "reason": None, "found": []})
        two = report.grounded_2(req, None, {"details": {"custom.report:grounded": out}}, {"cause": "냉각 팬 고장(CL-1)", "action": "무시"})
        self.assertEqual(two["details"], {"cause": {"ok": True, "reason": None, "found": []}})   # 다시 쓴 칸만 봄

    def test_fake_rule_interface(self):   # 배포 서버(fake_provider.py) · LLMOps(fake_rule.py)가 부르는 꼴
        self.assertEqual(fake_rule.FORMAT, "c1a15-pack/fake-rule@1")
        self.assertEqual(fake_rule.load_known()["prompts"], [])
        with self.assertRaises(fake_rule.FakeRefused) as cm:
            fake_rule.respond_text("## 사건\n아무 글")
        self.assertEqual(cm.exception.limits, ["refused"])

    def test_models_yaml_has_no_secret(self):   # provider 칸 = 이름만(비밀 값 0)
        text = (PACK / "models.yaml").read_text(encoding="utf-8")
        self.assertIn("env: OPENAI_API_KEY", text)
        self.assertEqual(yaml.safe_load(text)["providers"]["openai"]["env_fallback"], "OPENAI_API_KEY_2")   # 013-16: 키 자동 전환 이름(0.12.3에서 켜짐, 0.12.2는 읽지 않음)
        self.assertNotRegex(text, r"sk-[A-Za-z0-9]")
        self.assertEqual(yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))["env"], {"required": []})   # 가짜 모드가 키를 요구하지 않음


class StateLabels(unittest.TestCase):   # D-478 #8: 이름표 칸 셋(LLMOps가 점검 분 · 고른 점검 · 점검 → 원인을 이 이름으로만 읽음 — 이름 박기 0)
    def test_check_fields(self):
        lab = json.loads((ROOT / "ui-labels/state-labels.json").read_text(encoding="utf-8"))
        self.assertEqual((lab["check_minutes"], lab["chosen_check"], lab["group"]), ("request.minutes", "result.decision.section", "request.group"))
        secs = json.loads((PACK / "custom/manual_sections.json").read_text(encoding="utf-8"))
        want = {s["id"]: s["cause"] for s in secs if s.get("kind") == "check"}
        diff = sorted(k for k in set(want) | set(lab["check_cause"]) if want.get(k) != lab["check_cause"].get(k))
        self.assertEqual(diff, [], "check_cause가 매뉴얼 점검 절 표와 다른 절")   # 손으로 옮긴 값이 조용히 어긋나지 않게

    def test_exception_check_field(self):   # D-546 덧 23 ②(eval exception-label 2절): 예외 안내 헛걸음 라벨 = 시작 Run이 고른 예외 절 자리
        lab = json.loads((ROOT / "ui-labels/state-labels.json").read_text(encoding="utf-8"))
        self.assertEqual(lab["exception_check"], "result.exception_guide.section")
        def at(doc, path):
            for k in path.split("."):
                doc = doc.get(k) if isinstance(doc, dict) else None
            return doc
        out = json.loads((ROOT / "contracts/v8/examples/start-exception.result.json").read_text(encoding="utf-8"))   # Pack 함수가 낸 실제 start 출력 꼴
        state, path = lab["exception_check"].split(".", 1)
        self.assertEqual(state, "result")
        self.assertRegex(at(out, path), r"^EX-")   # LLMOps 검사 길 = 앞 state 이름을 뗀 것
        self.assertIsNone(at(out, lab["exception_check"]))   # 실패 쪽: state 이름을 그대로 두면 값을 못 찾음(라벨이 조용히 안 걸림)
        plain = json.loads((ROOT / "contracts/v8/examples/start.result.json").read_text(encoding="utf-8"))
        self.assertIsNone(at(plain, path))   # 예외 안내 없는 start면 칸 없음


    def test_diagnose_field(self):   # D-549 덧 7(llmops-2 155 꼴): 자동 개선 진단이 읽는 노드별 어긋남 기준 — 노드 · 쓰는 칸 · 실제 Run 출력 경로가 맞는지
        need_vector_db(self)
        lab = json.loads((ROOT / "ui-labels/state-labels.json").read_text(encoding="utf-8"))
        want = {"retrieve_history_cl": {"measure": "cause_share", "items": "history_hits", "cause": "metadata.cause", "min": 0.5},
                "retrieve_history": {"measure": "cause_share", "items": "history_hits", "cause": "metadata.cause", "min": 0.5},
                "first_judge": {"measure": "cause_rank", "ranks": "result.decision.probabilities", "max": 1}}
        self.assertEqual(lab["diagnose"], want)
        nodes = {n["id"]: n for n in yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))["graphs"]["run"]["nodes"]}
        for nid, r in lab["diagnose"].items():
            self.assertIn(nid, nodes)
            self.assertIn((r.get("items") or r.get("ranks")).split(".")[0], nodes[nid]["writes"], nid)   # 그 노드가 쓰는 칸
        self.assertNotIn("retrieve_history_x", nodes)   # 실패 쪽: 없는 노드는 그래프에 없음

        def at(d, path):
            for k in path.split("."):
                d = d.get(k) if isinstance(d, dict) else None
            return d

        causes = set(lab["check_cause"].values())
        req = json.loads((ROOT / "contracts/v5/examples/start.request.json").read_text(encoding="utf-8"))   # 냉각(CL) → retrieve_history_cl
        for group in ("CL", "TW"):   # TW → retrieve_history(공용)
            state = code_default(Runtime(PACK)).run({**req, "group": group})["detail"]["state"]
            hits = at(state, want["retrieve_history"]["items"])
            self.assertTrue(hits and all(at(h, "metadata.cause") in causes for h in hits), group)
            ranks = at(state, want["first_judge"]["ranks"])
            self.assertTrue(isinstance(ranks, dict) and ranks and set(ranks) <= causes, group)
            self.assertIsNone(at(state, "result.decision.probability"))   # 실패 쪽: 틀린 경로는 값 없음
            self.assertIsNone(at(hits[0], "cause"))   # 원인은 metadata 아래(바로 아래 아님)


class NoLabels(unittest.TestCase):   # VC-30: Pack · 자리 모델 · 생성 도구에 고장 라벨 열 · 원본 CSV 읽기 0(클래스 이름 TWF 등은 모델 출력 유형이라 예외)
    def test_no_label_columns_or_csv(self):
        files = [p for p in (ROOT / "pack").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != "COPIED_FROM.json"]
        files += list((ROOT / "releases").rglob("*.json")) + [ROOT / "tools/make_standin_model.py"]
        for p in files:
            text = p.read_text(encoding="utf-8")
            for bad in ("Machine failure", "ai4i2020.csv", "label_columns"):
                self.assertNotIn(bad, text, f"{p.relative_to(ROOT)}: {bad}")
        m = json.loads((ROOT / "releases/models/mr-c1a15-standin-v1/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((m["provenance"]["training"], m["provenance"]["labels_used"]), ("none", 0))


if __name__ == "__main__":
    unittest.main()
