"""D-533 '바뀐 것별 처리' ③ 작은 예 — '같은 라인' 창 30분 → 20분을 두 길로 해 보고 같은 시험 100건으로 잰다(임시 폴더만 · 결과는 비율 · 개수만).
  가. 규칙 표만 바꿈(R1에 age_min<=20) → 원본 · 관계 층 손 0
  나. 관계 정의 바꿈(relation_defs window_min 20 · rel-v2) → 관계 층만 새 버전으로 다시 만듦 · 원본 손 0 · rel-v1 남김(되돌림용) · 설정의 layer 줄만 바꿔 끼움
python kg-test/demo_redefine.py [--out <폴더>]   # 기본 kg-test/results → redefine.json · redefine.md"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

import yaml

import build_layer as B
import evaluate as E
import kg_node as N

HERE = Path(__file__).resolve().parent
NAME = "3 관계 질의 노드(D-533)"


def _shas(d, files):
    return {f: B._sha((Path(d) / f).read_bytes()) for f in files}


def _config(path, layer, rules):
    c = yaml.safe_load((HERE / "node.yaml").read_text(encoding="utf-8"))
    c.update(layer=str(layer), rules=str(rules))
    path.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _score(data, config):
    row = E.run(data, node_config=config)[NAME]
    return {c: {f: row[c][f] for f in ("recall", "precision", "first")} for c in ("E2", "all")}


def main(out):
    data = HERE / "data"
    lay = ("nodes.jsonl", "edges.jsonl", "manifest.json")
    raw0 = _shas(data, B.RAW)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "layer"
        shutil.copytree(HERE / "layer", root)                       # 지금 적재(rel-v1)
        v1 = root / "rel-v1"
        v1_0 = _shas(v1, lay)
        rows = {"지금(rel-v1 · 규칙 표 그대로)": {"config": _config(tmp / "now.yaml", v1, HERE / "exceptions.csv")}}

        rules_a = tmp / "exceptions-a.csv"                            # 가. 규칙 표만
        text = (HERE / "exceptions.csv").read_text(encoding="utf-8")
        rules_a.write_text(text.replace("R1,공급 냉각수,same_line,,2,", "R1,공급 냉각수,same_line,age_min<=20,2,"), encoding="utf-8")
        rows["가. 규칙 표만 바꿈(R1 age_min<=20)"] = {"config": _config(tmp / "a.yaml", v1, rules_a)}

        defs = yaml.safe_load((HERE / "relation_defs.yaml").read_text(encoding="utf-8"))   # 나. 관계 정의
        defs.update(version="rel-v2", same_line={"window_min": 20})
        (tmp / "defs-v2.yaml").write_text(yaml.safe_dump(defs, allow_unicode=True, sort_keys=False), encoding="utf-8")
        v2, _ = B.build(data, tmp / "defs-v2.yaml", root)
        rows["나. 관계 정의 바꿈(rel-v2 · 창 20분)"] = {"config": _config(tmp / "b.yaml", v2, HERE / "exceptions.csv")}

        res = {}
        for name, r in rows.items():
            layer = Path(yaml.safe_load(r["config"].read_text(encoding="utf-8"))["layer"])
            m = json.loads((layer / "manifest.json").read_text(encoding="utf-8"))
            res[name] = {"layer": m["version"], "edges_same_line": m["counts"]["edges_same_line"], "edges_machine_work": m["counts"]["edges_machine_work"],
                         "raw_same": _shas(data, B.RAW) == raw0, "rel_v1_same": _shas(v1, lay) == v1_0,
                         "nodes_same_as_v1": (layer / "nodes.jsonl").read_bytes() == (v1 / "nodes.jsonl").read_bytes(),
                         "score": _score(data, r["config"])}
        res["되돌림"] = {"rel_v1_kept": all((v1 / f).is_file() for f in lay), "how": "설정 layer 줄을 rel-v1로 — 관계 층 두 버전이 나란히 있음"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "redefine.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    md = [E.M.V0, "", "| 길 | 읽은 관계 층 | 같은 라인 관계 수 | 같은 설비 정비 관계 수 | 원본 바이트 같음 | rel-v1 바이트 같음 | 첫 단계 E2 | 첫 단계 전체 | 불러온 비율 전체 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, r in res.items():
        if name == "되돌림":
            continue
        s = r["score"]
        md.append(f"| {name} | {r['layer']} | {r['edges_same_line']} | {r['edges_machine_work']} | {'예' if r['raw_same'] else '아니오'} | "
                  f"{'예' if r['rel_v1_same'] else '아니오'} | {s['E2']['first']:.2f} | {s['all']['first']:.2f} | {s['all']['recall']:.2f} |")
    (out / "redefine.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "results"))
    main(Path(ap.parse_args().out))
