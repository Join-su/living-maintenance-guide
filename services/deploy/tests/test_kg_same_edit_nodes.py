"""같음 + 고침 승인 기록 파일 길(D-560 7 · same-edit.md) — 대상 예외가 앞서 nodes.json에 들었을 때도 고침 줄이 적재기에 감(dev-2 · 11-05 436).
보는 것: 추출기가 낸 고침 줄(절 edit_record · 관계 edit: true)은 nodes.json의 같은 id 옛 줄을 바꿈(옛 코드는 같은 id면 건너뛰어 조용히 '바뀜 0')
· 반영 뒤 그 id 줄 = 적재기가 낸 release 줄(고침 표시 0) — 다음 적재에서 옛 내용이 previous에 다시 쌓이지 않음 · 고침 표시 없는 같은 id 줄은 지금처럼 한 번만."""
import json
import types
from pathlib import Path

import pytest

from test_kg_apply import K, LETTER  # noqa: F401 - fixture K

SEC = {"no": "EX-R-0301", "kind": "예외", "topic": "필터 덮개 나사", "checks": [{"what": "필터 덮개 고정 나사"}], "actions": [{"what": "풀린 나사 다시 조임"}]}
REL = {"id": "EX-R-0301", "step": "EX-R-0301", "records": ["R-0301"], "n": 1, "when": {"group": "온도 차 저하"}}
EDIT_SEC = {**SEC, "checks": [{"what": "나사 네 개가 다 있는지"}], "actions": [{"what": "빠진 나사 새로 끼움"}], "edit_record": "R-0302"}
EDIT_REL = {**REL, "records": ["R-0302"], "edit": True}
SAME_EDIT = {"record_id": "R-0302", "exception_record": {"situation": "s", "exception": {"topic": "필터 덮개 나사", "checks": [{"what": "x"}], "actions": [{"what": "y"}]},
             "exception_review": {"decision": "same", "match": {"kind": "예외", "no": "EX-R-0301"},
                                  "edit": {"checks": [{"what": "나사 네 개가 다 있는지"}], "actions": [{"what": "빠진 나사 새로 끼움"}]}}}}


@pytest.fixture
def E(K, monkeypatch, tmp_path):   # noqa: F811
    """진짜 extract · 가짜 추출기(정해 둔 줄을 냄) · 적재기 가짜 = Pack merge 흉내(기준 = 지금 release · 고침 표시면 previous에 쌓고 records + 이번)."""
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))
    monkeypatch.setenv("KG_EXGRAPH", str(tmp_path / "exgraph.py"))
    monkeypatch.setattr(K, "extract", K.real_extract)
    K.out_rows = {"sections": [], "exception_relations": []}
    K.release = {"layer": "L1", "sections": [SEC], "exception_relations": [REL]}
    monkeypatch.setattr(K, "git", lambda *a: json.dumps(K.release))

    def sh(args, **kw):
        Path(args[args.index("--out") + 1]).write_text(json.dumps(K.out_rows))
        return types.SimpleNamespace(returncode=0, stdout='{"added": ["R-0302"]}', stderr="")
    monkeypatch.setattr(K.A, "sh", sh)

    def fake_load(tag=None, base_text=None):
        K.calls["load"] += 1
        old = json.loads(base_text) if base_text else K.release
        p = K.STATE / "nodes.json"
        new = json.loads(p.read_text()) if p.exists() else {"sections": [], "exception_relations": []}
        secs = {s["no"]: s for s in old["sections"]}
        rels = {x["id"]: x for x in old["exception_relations"]}
        for s in new["sections"]:
            if "edit_record" in s:
                b = secs[s["no"]]
                s = {**{k: v for k, v in s.items() if k != "edit_record"},
                     "previous": list(b.get("previous") or []) + [{"record": s["edit_record"], "checks": b["checks"]}]}
            secs[s["no"]] = s
        for x in new["exception_relations"]:
            if x.get("edit"):
                b = rels[x["id"]]
                recs = b["records"] + [r for r in x["records"] if r not in b["records"]]
                x = {**b, "records": recs, "n": len(recs)}
            rels[x["id"]] = x
        rel = {"layer": f"L{1 + K.calls['load']}", "sections": list(secs.values()), "exception_relations": list(rels.values())}
        (K.STATE / "current.json").write_text(json.dumps(rel, ensure_ascii=False))
        K.release = rel                                               # 바꿔 끼운 뒤 지금 도는 그래프
        return {"tag": rel["layer"], "release_id": "graph-x", "rejected": [], "rejected_rows": []}
    monkeypatch.setattr(K, "load", fake_load)
    K.STATE.mkdir(parents=True, exist_ok=True)
    (K.STATE / "nodes.json").write_text(json.dumps({"sections": [SEC], "exception_relations": [REL]}))   # 앞서 '아님' 추출로 든 대상 예외
    return K


def put(K, name, record):
    (K.INBOX / f"{name}.json").write_text(json.dumps({**LETTER, "report_id": name, "record": record}, ensure_ascii=False))


def test_edit_rows_replace_same_id_already_in_nodes(E):
    E.out_rows = {"sections": [EDIT_SEC], "exception_relations": [EDIT_REL]}
    assert E.extract([(Path("x.json"), {**LETTER, "record": SAME_EDIT})], "g1") == 1
    acc = json.loads((E.STATE / "nodes.json").read_text())
    assert acc["sections"] == [EDIT_SEC] and acc["exception_relations"] == [EDIT_REL]


def test_plain_same_id_rows_still_once(E):
    E.out_rows = {"sections": [{**SEC, "topic": "다른 것"}], "exception_relations": [{**REL, "n": 9}]}
    assert E.extract([(Path("x.json"), {**LETTER, "record": SAME_EDIT})], "g1") == 0
    acc = json.loads((E.STATE / "nodes.json").read_text())
    assert acc["sections"] == [SEC] and acc["exception_relations"] == [REL]


def test_same_edit_deploys_and_is_not_replayed(E):
    E.out_rows = {"sections": [EDIT_SEC], "exception_relations": [EDIT_REL]}
    put(E, "RPT-000302", SAME_EDIT)
    assert E.main() == "g2"
    cur = json.loads((E.STATE / "current.json").read_text())
    assert cur["sections"][0]["checks"] == EDIT_SEC["checks"] and [p["record"] for p in cur["sections"][0]["previous"]] == ["R-0302"]
    assert cur["exception_relations"][0]["records"] == ["R-0301", "R-0302"] and cur["exception_relations"][0]["n"] == 2
    acc = json.loads((E.STATE / "nodes.json").read_text())
    assert "edit_record" not in acc["sections"][0] and not acc["exception_relations"][0].get("edit")
    put(E, "RPT-000303", {"record_id": "H-0999"})                    # 다음 일반 이력 승인 — 고침이 다시 쌓이지 않음
    E.main()
    cur = json.loads((E.STATE / "current.json").read_text())
    assert [p["record"] for p in cur["sections"][0]["previous"]] == ["R-0302"] and cur["exception_relations"][0]["n"] == 2
