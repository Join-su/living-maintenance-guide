"""suite c1a15-start 기대값 만들기(Pack EV-001 PS-02, 약속 v4 · D-313 · D-314) — Pack 밖 개발 도구.

  .venv/bin/python -B tools/make_start_suite.py <승인한 바뀐 기대값 목록 g9-2-changed-expectations.jsonl>
- 입력(dataset.jsonl)은 그대로 둔다(tools/make_evals.py가 만든 시연 10 — 다리 칸 similar가 있어도 약속 v4 start는 쓰지 않음, H-5 (나)).
- 기대값 = Pack 기본 실행 설정(가짜 DB ir-history-412ea43e, 망 0) start Run 출력(decision · similar · retrieval).
  쓰기 전에 사용자가 승인한 목록(③, D-314)의 새 값과 사례마다 같은지 검사한다 — 다르면 쓰지 않고 멈춘다."""
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "pack/evals/c1a15-start"
HEAD = """# 평가 suite — Pack revision 밖. 시연 10 세션의 첫 Run(약속 v4 — Pack 리트리버 retrieve_history) — 기대값 = Pack 출력(가짜 DB ir-history-412ea43e, D-313 · D-314 승인)
# 만든 도구: tools/make_start_suite.py(입력은 tools/make_evals.py — 다리 칸 similar는 쓰지 않음 H-5 (나))
suite: c1a15_start
split: fixture
graph: run
dataset: dataset.jsonl
expectations: expectations.jsonl
review_policy: {default: fixture, missing_fixture: stop_before}
"""


def main(approved):
    tries = []
    socket.create_connection = lambda *a, **k: (tries.append(1), (_ for _ in ()).throw(OSError("netblock")))[1]
    from packrt.run.engine import Runtime
    ok = {r["case"]: r["new"] for r in map(json.loads, approved.read_text(encoding="utf-8").splitlines()) if r["case"].startswith("S")}
    rt = Runtime(ROOT / "pack")
    ex = []
    for line in (SUITE / "dataset.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        r = rt.run(c["input"])
        assert r["status"] == "succeeded", (c["case_id"], r.get("error"))
        out = r["output"]
        assert out == {"decision": ok[c["case_id"]]["decision"], "similar": ok[c["case_id"]]["similar"], "retrieval": ok[c["case_id"]]["retrieval"]}, c["case_id"]
        ex.append({"case_id": c["case_id"], "final": [{"path": k, "equals": out[k]} for k in ("decision", "similar", "retrieval")],
                   "nodes": {"unsupported": [{"reached": False}]}})   # node check는 {reached: false}만(llmops suites.py 문법) — reached: true는 늘 틀림으로 셈
    assert len(ex) == len(ok) == 10 and not tries
    (SUITE / "expectations.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in ex), encoding="utf-8")
    (SUITE / "suite.yaml").write_text(HEAD, encoding="utf-8")
    print(f"c1a15-start 기대값 {len(ex)}건 = 승인 목록 새 값 · 망 시도 {len(tries)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
