"""반영 전 그래프 남김(D-592 ①) — 새 그래프로 바꿔 띄울 때 Neo4j에 (새 Layer)-[:PREVIOUS]->(바로 앞 Layer) 선과 바꾼 시각을 남김(이 kg_apply 몫 · 실패해도 반영 결과 그대로)
+ 작업 폴더 layer-pairs.jsonl 한 줄. 앞 판 layer는 지금처럼 KG_KEEP_LAYERS(기본 1)로 지워지지 않음."""
import json

from test_kg_apply import K, letter  # noqa: F401 - fixture K


def test_apply_notes_previous_layer_pair(K, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(K, "link_layers", lambda before, after, at: seen.append((before, after)) or True)
    letter(K, record={"record_id": "R-0002"})
    assert K.main() == "g2"
    assert seen == [("L1", "L2")]
    row = json.loads((K.STATE / "layer-pairs.jsonl").read_text().splitlines()[-1])
    assert row["before"] == {"tag": "g1", "layer": "L1"} and row["after"] == {"tag": "g2", "layer": "L2"} and row["neo4j"] is True


def test_same_graph_notes_nothing(K, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(K, "link_layers", lambda *a: seen.append(a) or True)
    K.loader_tag = "L1"                                           # 지금 도는 그래프와 같음 → 다시 띄움 0
    letter(K, record={"record_id": "R-0002"})
    assert K.main() == "same" and seen == [] and not (K.STATE / "layer-pairs.jsonl").exists()


def test_neo4j_failure_keeps_the_apply(K, monkeypatch):  # noqa: F811
    def boom(*a):
        raise RuntimeError("neo4j 닿지 않음")
    monkeypatch.setattr(K, "link_layers", boom)
    letter(K, record={"record_id": "R-0002"})
    assert K.main() == "g2" and K.calls["swap"] == ["g2"]
    assert json.loads((K.STATE / "layer-pairs.jsonl").read_text())["neo4j"] is False


def test_link_layers_runs_the_cypher_through_the_loader_python(K, monkeypatch, tmp_path):  # noqa: F811
    got = []
    monkeypatch.setattr(K.S, "pack_repo", lambda: tmp_path)
    monkeypatch.setattr(K.A, "sh", lambda args, **kw: got.append(args) or type("R", (), {"returncode": 0, "stdout": '{"linked": 1}', "stderr": ""})())
    assert K.link_layers("L1", "L2", "2026-11-06T14:30:00") is True
    code = got[0][got[0].index("-c") + 1]
    assert "PREVIOUS" in code and "MERGE" in code and got[0][-4:-2] == ["L1", "L2"]
