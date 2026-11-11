"""약속 v4 초안 시험(run 기록 안 — Pack 코드 아님). <c1a15-pack>/.venv/bin/python check_v4_draft.py"""
import copy, json
from pathlib import Path
import jsonschema
H = Path(__file__).resolve().parent
V3 = H.parent / "v3"   # 같은 저장소 contracts/v3(상대 경로 · 324)
rd = lambda p: json.loads(p.read_text(encoding="utf-8"))
REQ = jsonschema.Draft202012Validator(rd(H / "run_request.json")); RES = jsonschema.Draft202012Validator(rd(H / "run_result.json"))
for v in (REQ, RES): jsonschema.Draft202012Validator.check_schema(v.schema)
ld = lambda n: rd(H / "examples" / n)
ok = True
def want(cond, why):
    global ok
    print(("통과 " if cond else "실패 ") + why); ok &= cond
def strip(o):  # 설명 글 · $id를 뺀 모양
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if k not in ("description", "$id")}
    if isinstance(o, list): return [strip(x) for x in o]
    return o
for n in sorted(p.name for p in (H / "examples").glob("*.json")):
    want((REQ if n.endswith(".request.json") else RES).is_valid(ld(n)), f"예시 {n}")
# 요청: v3와 모양이 같음(H-5 (나) — start.similar는 받되 쓰지 않음)
want(strip(REQ.schema) == strip(rd(V3 / "run_request.json")), "요청 schema 모양 = v3(설명 글만 다름)")
# 출력: retrieval만 바뀜
a, b = strip(RES.schema), strip(rd(V3 / "run_result.json"))
want({k: v for k, v in a["properties"].items() if k != "retrieval"} == {k: v for k, v in b["properties"].items() if k != "retrieval"}
     and {k: v for k, v in a.items() if k != "properties"} == {k: v for k, v in b.items() if k != "properties"}, "출력 schema는 retrieval 칸만 다름")
sr = ld("start.result.json")
z = copy.deepcopy(sr); z["retrieval"]["source"] = "request"; want(not RES.is_valid(z), "retrieval.source = packrt만(다리 결과 없음)")
z = copy.deepcopy(sr); z["retrieval"]["top_k"] = 10; want(not RES.is_valid(z), "top_k는 출력에 없음(trace에만)")
z = copy.deepcopy(sr); z["retrieval"]["query_from"] = "release"; want(not RES.is_valid(z), "query_from release(G-3)는 v4에 없음")
z = copy.deepcopy(sr); del z["retrieval"]["fallback_used"]; want(not RES.is_valid(z), "fallback_used 필수")
z = copy.deepcopy(sr); z["similar"] = []; z["retrieval"].update(vector_db_version=None, query_from=None, fallback_used=None); want(RES.is_valid(z), "similar가 비면 셋 = null")
z = copy.deepcopy(sr); z["retrieval"]["vector_db_version"] = ""; want(not RES.is_valid(z), "vector_db_version 빈 글 안 됨")
want(not RES.is_valid(rd(V3 / "examples/start-bridge.result.json")), "v3 다리 결과(source request)는 v4 출력이 아님")
# alarm · judge · report: v3 예시가 v4에서도 그대로 맞음
for n in sorted(p.name for p in (V3 / "examples").glob("*.json")):
    if n.startswith("start"): continue
    want((REQ if n.endswith(".request.json") else RES).is_valid(rd(V3 / "examples" / n)), f"v3 {n} → v4 그대로 맞음")
for n in ("start.request.json", "start-bridge.request.json"):
    want(REQ.is_valid(rd(V3 / "examples" / n)), f"v3 {n} → v4 요청으로도 맞음(되돌리기 · 넘어가는 동안 앱 손댈 것 0)")
print("결과:", "모두 통과" if ok else "실패 있음")
