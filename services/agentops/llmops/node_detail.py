"""Node detail of an Agent Pack release (EV-016, docs/node-inspector-options.md v1.0): what a node reads and writes (with the
graph state types), what it is for, its run values from a stored Run, and which of its settings the option table lets a person
change (EV-018: edited in the form and checked with a preview, nothing saved) or only view. Read only: the release copy's
pack.yaml and the stored visit records."""

import difflib
import json
import re
from pathlib import Path

from packrt import ir
from packrt.ir import strict_yaml

# 고를 값의 출처(옵션 표 2~5절). Rows marked ✎ in spec 11 plus the confirmed items of the option table section 9.
EDIT = {
    "llm": [("provider", "Pack models.yaml ∩ Project에 키가 있는 provider"),
            ("model", "Pack models.yaml의 chat 모델 ∩ Project에 키가 있는 provider"),
            ("prompt", "Pack prompts/ 파일 고르기 + 파일 내용 편집(템플릿 키는 reads 안)"),
            ("temperature", "직접 입력(0 이상)"), ("max_tokens", "직접 입력(1 이상)")],
    "retrieve:vector": [("query", "{state…} 템플릿(키는 reads 안)"), ("top_k", "직접 입력(1 이상)"), ("search", "similarity · mmr"),
                        ("mmr", "{fetch_k, lambda}(search가 mmr일 때)"), ("score_threshold", "직접 입력"),
                        ("filter", "키: 저장소 metadata_required, 값: 직접·템플릿"), ("fallback", "{min_results, relax: filter 키}")],
    # D-492 덧 27 · 28 (packrt-1 085 14줄): a retrieve@2 node's own options — no retrieve@1 names (search · mmr); index = the node's cut
    # of the vector DB (chunk size · overlap), so changing it builds the vector DB again (E1 103 (가))
    "retrieve:vector@2": [("query", "{state…} 템플릿(키는 reads 안)"), ("method", "vector · lexical · hybrid"), ("fuse", "{k} — RRF k(찾는 방식이 hybrid일 때)"),
                          ("top_k", "직접 입력(1 ~ Pack 출력 약속 수)"), ("score_threshold", "직접 입력(0 ~ 1)"),
                          ("filter", "키: 저장소 metadata_required, 값: 직접·템플릿"), ("fallback", "{min_results, relax: filter 키}"),
                          ("index", "{chunk_size, overlap} — 바꾸면 벡터 DB 다시 만듦")],
    "retrieve:table": [("where", "열 이름: 저장소 schema"), ("select", "열 이름"), ("limit", "직접 입력(1 이상)"),
                       ("fallback", "{min_results, relax}")],
    "router": [("cases", "{값: 같은 그래프 node}"), ("default", "같은 그래프 node")],
    "human_review": [("routes", "{동작: 같은 그래프 node 또는 END}"), ("timeout", "시간과 그 뒤 동작")],
    "render": [("template", "Pack templates/ 파일 고르기 + 파일 내용 편집(템플릿 키는 reads 안)")],
    "predict": [], "custom_code": [],
}
VIEW = {
    "llm": [("output", "바꾸면 출력 양식이 바뀜")],
    "retrieve": [("store", "바꾸면 근거 종류가 바뀜")],
    "router": [("by", "명세상 화면 수정 대상 아님")],
    "human_review": [("subject", "검토 흐름 고정"), ("actions", "검토 흐름 고정"), ("editable", "검토 흐름 고정"),
                     ("revalidate", "검토 흐름 고정"), ("states", "검토 흐름 고정")],
    "render": [("format", "출력 양식 고정"), ("to", "출력 양식 고정")],
    "predict": [("model", "model release"), ("features", "입력 매핑"), ("output", "출력 타입")],
    "custom_code": [("fn", "Pack 함수"), ("stores", "함수가 받는 표 저장소")],
    "validate": [],
}
# 실행 값 중 지금 Run 기록에 없는 것(옵션 표 '없음 → 필요'). 이번에는 기록을 만들지 않고 '기록 없음'으로만 보인다.
NOT_RECORDED = {
    "llm": ["provider·model·token(엔진 기록에만 있고 Run 기록에 복사되지 않음)", "렌더된 프롬프트", "provider 원본 응답"],
    "retrieve": ["실제 질의 문장", "적용된 filter·where 값"],
    "predict": ["넣은 features"],
}
UNSET = "(없음)"


def load(folder, tolerate=None):
    """The release copy's pack.yaml as (doc, line numbers), or None when it cannot be read. `tolerate(message)` names problems that
    do not stop reading (an open edit's node waiting for its next connection, manual p.14)."""
    res = ir.load_pack(folder)
    errors = [e for e in res.errors if not (tolerate and tolerate(e.get("message") or ""))]
    return None if errors or not res.doc else (res.doc, res.lines)


def kind_name(node):
    return node["kind"].split("@")[0]


def state_types(doc, graph):
    st = doc["graphs"][graph].get("state") or {}
    return {k: (v if isinstance(v, str) else (v or {}).get("type")) for k, v in st.items()}


REBUILD = ("index",)   # D-492 덧 28: the retrieve@2 params whose change makes the vector DB again (with a declared split, below)
METHODS = ["vector", "lexical", "hybrid"]


def static_detail(doc, lines, graph, node_id, fn_info=None, options=None):
    """The node's static detail, or None when the graph has no such node. options (D-492 덧 28): the Pack's declared options of this node
    (autohyp.pack_options) — a retrieve@2 node edits them too (set_option), split marked as making the vector DB again."""
    nodes = doc["graphs"][graph]["nodes"]
    idx = next((i for i, n in enumerate(nodes) if n["id"] == node_id), None)
    if idx is None:
        return None
    n, k = nodes[idx], kind_name(nodes[idx])
    p, types = n.get("params") or {}, state_types(doc, graph)
    edit, view = [], []
    if k == "retrieve":
        stype = ((doc.get("stores") or {}).get(p.get("store")) or {}).get("type")
        two = n.get("kind") == "retrieve@2" and stype != "table"
        rows = EDIT["retrieve:table" if stype == "table" else "retrieve:vector@2" if two else "retrieve:vector"]
        if two and (((doc.get("stores") or {}).get(p.get("store")) or {}).get("index") or {}).get("split") == "record":   # contract C: the
            rows = [r for r in rows if r[0] != "index"]   # history DB is cut by record, fixed by a person — no chunk size · overlap there
        edit = [{"param": a, "value": p.get(a, UNSET), "source": s, **({"rebuild": True} if two and a in REBUILD else {})} for a, s in rows]
        view = [{"param": a, "value": p.get(a, UNSET), "reason": r} for a, r in VIEW["retrieve"]]
        if n.get("kind") == "retrieve@2" and isinstance(p.get("rerank"), dict):   # EV-072 (D-446 ③ · 덧 2): only a node with a rerank dict
            edit.append({"param": "rerank", "value": p["rerank"], "source": "재정렬 개수만 — 가중치 · 센서 칸 · 질의는 보기만"})
        if two:   # D-492 덧 28: the Pack's declared options of this node (split — the manual DB's cut, D-492 덧 15)
            for name, spec in (options or {}).items():
                if spec.get("type") == "choice":
                    edit.append({"param": f"option.{name}", "value": spec.get("_value"), "source": "Pack 선언 옵션", "label": spec.get("label") or name,
                                 "choices": list(spec.get("values") or []), "graph": spec.get("option"), **({"rebuild": True} if name == "split" else {})})
        idx_cfg = ((doc.get("stores") or {}).get(p.get("store")) or {}).get("index")
        if idx_cfg is not None:
            view.append({"param": "index", "value": idx_cfg, "reason": "벡터 DB 설정(Pack 정의)은 보기만(명세 6장)"})
    elif k == "validate":
        for i, rule in enumerate(p.get("rules") or []):
            rk = next(iter(rule)) if isinstance(rule, dict) and rule else str(rule)
            item = {"param": f"rules[{i}].{rk}", "value": rule.get(rk) if isinstance(rule, dict) else rule}
            if rk == "custom":
                view.append({**item, "reason": "Pack 함수 규칙은 보기만(추가·삭제·이동 불가)"})
            else:
                edit.append({**item, "source": "규칙 값 수정"})
        edit.append({"param": "rules.add_remove", "value": None, "source": "기본 규칙 추가·삭제(evidence·evidence_ids·unsafe_assertion·schema·required·range·unit)"})
        edit.append({"param": "on_fail", "value": p.get("on_fail", UNSET), "source": "같은 그래프 node"})
    else:
        edit = [{"param": a, "value": p.get(a, UNSET), "source": s} for a, s in EDIT.get(k, [])]
        view = [{"param": a, "value": p.get(a, UNSET), "reason": r} for a, r in VIEW.get(k, [])]
    view += [{"param": "expect.text", "value": (n.get("expect") or {}).get("text", UNSET), "reason": "보기만(옵션 표 9절 1)"},
             {"param": "on_error", "value": n.get("on_error", UNSET), "reason": "보기만(옵션 표 9절 1)"}]
    line = lines.get(("graphs", graph, "nodes", idx))
    return {"node": node_id, "kind": n["kind"], "kind_name": k, "loc": f"pack.yaml:{line}" if line else "pack.yaml",
            "function": ({"fn": p.get("fn"), **(fn_info or {})} if k == "custom_code" else None),
            "reads": [{"name": r, "type": types.get(r)} for r in n.get("reads", [])],
            "writes": [{"name": w, "type": types.get(w)} for w in n.get("writes", [])],
            "expect": {"text": (n.get("expect") or {}).get("text"), "checks": (n.get("expect") or {}).get("checks") or []},
            "on_error": n.get("on_error"), "idempotent": n.get("idempotent"),
            "editable": edit, "view_only": view,
            "structure": ({"function_node": True, "can_edit": False, "can_delete": False, "can_move": True,
                           "note": "수정 불가 — 함수 node(custom_code@3)는 입출력·기능 설명만 보입니다. 연결 위치만 바꿀 수 있고 삭제할 수 없습니다"}
                          if k == "custom_code" else {"function_node": False, "can_edit": True, "can_delete": True, "can_move": True,
                                                      "note": "값을 고친 뒤 미리보기로 검사합니다(저장하지 않음)"})}


def prediction_of(output):
    """The Prediction value in a predict node's raw output (the state key it writes — any name): the dict with type · probability."""
    if isinstance(output, dict) and "type" in output and "probability" in output:
        return output
    return next((x for x in (output.values() if isinstance(output, dict) else ()) if isinstance(x, dict) and "type" in x and "probability" in x), None)


def predict_view(meta, prediction=None):
    """EV-041 CHG-203 · 204: what a predict@1 node reported (packrt 0.11.0 meta: model_release · probabilities · alarm · threshold) and,
    when the raw output is there, its top class (prediction.type · probability) — None when there is nothing. `text` is the one line
    the popover, the Run tab and the operational Run screen show (web/app.js predictText keeps the same rule for stored visits).
    Only a 0.11.0 record has the line: packrt 0.11.0 always writes `probabilities` into the predict meta (engine 535 · trace 1009),
    while a 0.10.x visit also has model_release and {type, probability} — no line there, as before (PRES-211, slice critic P1-C1)."""
    meta, pr = meta or {}, prediction if isinstance(prediction, dict) else {}
    get = lambda k: meta.get(k) if meta.get(k) is not None else pr.get(k)
    out = {k: get(k) for k in ("model_release", "probabilities", "alarm", "threshold")}
    out.update(type=pr.get("type"), probability=pr.get("probability"))
    out = {k: v for k, v in out.items() if v is not None}
    if not isinstance(out.get("probabilities"), dict) or not out["probabilities"]:
        return None
    pct = lambda x: f"{float(x) * 100:.1f}%"
    parts = []
    if "model_release" in out:
        parts.append(f"모델 release {out['model_release']}")
    if "type" in out and "probability" in out:
        parts.append(f"{out['type']} {pct(out['probability'])}")
    if "alarm" in out:
        parts.append(f"알람 {'예' if out['alarm'] else '아니오'}" + (f"(문턱 {float(out['threshold']):.2f})" if "threshold" in out else ""))
    ranked = sorted(out["probabilities"].items(), key=lambda kv: (-float(kv[1]), kv[0]))
    parts.append("확률 " + " · ".join(f"{k} {pct(v)}" for k, v in ranked))
    out["text"] = " · ".join(parts)
    return out


def index_view(visit, index_releases):
    """EV-050 CHG-W2-04: '벡터 DB <release_id> · 청크 <n | 섹션 그대로> · 오버랩 <n>' for a retrieve visit whose index release
    (its recorded retrievals.index_release) has a recipe in the Run manifest's evidence_index_releases; None otherwise."""
    r = (visit or {}).get("retrievals") or {}
    rel, idx = r.get("index_release"), index_releases or {}
    for e in ([idx.get(r.get("store"))] if r.get("store") in idx else idx.values()):   # the visit's store first (retrieve@2 meta)
        e = e or {}
        recipe = e.get("index_recipe")
        if rel and e.get("release_id") == rel and isinstance(recipe, dict):
            size = recipe.get("chunk_size")
            return f"벡터 DB {rel} · 청크 {'섹션 그대로' if size is None else size} · 오버랩 {recipe.get('overlap') or 0}"
    return None


def _graphs(value, depth=0):
    """The graph {used, layer} marks in a node output: on the output itself or on the items of its lists (graph_query hits); D-545:
    an exception_guide's relation_id (C1-a15 Pack v8 start result) and a decision's relation (kind + relation) are used relations too
    (no layer)."""
    if depth > 3:
        return
    if isinstance(value, dict):
        g = value.get("graph")
        if isinstance(g, dict) and g.get("layer") and g.get("used"):
            yield g
        eg = value.get("exception_guide")
        if isinstance(eg, dict) and isinstance(eg.get("relation_id"), str) and eg["relation_id"]:
            yield {"used": eg["relation_id"], "layer": None}
        if "kind" in value and isinstance(value.get("relation"), str) and value["relation"] and not isinstance(value.get("graph"), dict):
            yield {"used": value["relation"], "layer": None}   # a decision's relation (the Pack's exception step) before its graph mark
        for k, v in value.items():
            if k not in ("graph", "exception_guide"):
                yield from _graphs(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _graphs(v, depth + 1)


def relations_of(output):
    """D-543 ⑥ ①: the relations a graph query node used, from its output (c1a15-pack graph_query: graph {used: rule · relation id
    or 'base', layer} on every hit) — {layer, used, hits, text} or None when the output has none."""
    marks = list(_graphs(output))
    if not marks:
        return None
    used = sorted({u for g in marks for u in ([g["used"]] if isinstance(g["used"], str) else g["used"])})
    layer = " · ".join(sorted({str(g["layer"]) for g in marks if g.get("layer")}))
    named = [u for u in used if u != "base"]
    text = (f"그래프 {layer} · " if layer else "그래프 · ") + (f"쓰인 관계 {', '.join(named)}" if named else "기본 관계(이력 개수)")
    return {"layer": layer, "used": used, "hits": len(marks), "text": text}


def case_relations(visits):
    """The relations each graph query visit of one case used: [{node, iter, layer, used}] in visit order."""
    out, seen = [], {}
    for v in visits or []:
        seen[v.get("node")] = seen.get(v.get("node"), 0) + 1
        r = relations_of(v.get("output"))
        if r:
            out.append({"node": v.get("node"), "iter": seen[v.get("node")], "layer": r["layer"], "used": r["used"]})
    return out


def run_value(detail, visits, iteration=1, index_releases=None):
    """Run values of the node in one case's stored visits: input = reads keys only, output = writes keys only (option table 9-7)."""
    vs = [v for v in visits or [] if v.get("node") == detail["node"]]
    norec = NOT_RECORDED.get(detail["kind_name"], [])
    if not vs:
        return {"visited": False, "iters": 0, "not_recorded": norec}
    it = max(1, min(int(iteration or 1), len(vs)))
    v = vs[it - 1]
    inp, out = v.get("input") or {}, v.get("output") or {}
    reads, writes = [r["name"] for r in detail["reads"]], [w["name"] for w in detail["writes"]]
    return {"visited": True, "iter": it, "iters": len(vs), "status": v.get("status"), "ms": v.get("ms"),
            "input": {k: inp[k] for k in reads if isinstance(inp, dict) and k in inp},
            "output": {k: out[k] for k in writes if isinstance(out, dict) and k in out},
            "expect_checks": v.get("expect_checks"), "node_error": v.get("node_error"), "not_recorded": norec,
            "predict": predict_view(v, prediction_of(out)) if detail["kind_name"] == "predict" else None,
            "index": index_view(v, index_releases) if detail["kind_name"] == "retrieve" else None, "relations": relations_of(out)}


# ---- EV-018: what the edit form chooses from, and the changed lines of a preview
FILE_TEXT_MAX = 200_000   # bytes; a larger prompt/template file is listed but its text is not sent to the form


def form(folder, doc, graph):
    """What the edit form of a node chooses from (CHG-86): the chat models per provider of the copy's models.yaml, the files
    under prompts/ and templates/ with their text, and the node ids of the graph. Read only."""
    folder = Path(folder)
    providers = {}
    try:
        models, _ = strict_yaml.load((folder / "models.yaml").read_text(encoding="utf-8"))
        providers = {p: list(((c or {}).get("models") or {}).get("chat") or []) for p, c in ((models or {}).get("providers") or {}).items()}
    except (OSError, strict_yaml.StrictYAMLError, UnicodeDecodeError, AttributeError):
        providers = {}
    files, text, big = {}, {}, []
    for d in ("prompts", "templates"):
        base = folder / d
        found = sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink()) if base.is_dir() else []
        files[d] = [p.relative_to(folder).as_posix() for p in found]
        for p in found:
            rel = p.relative_to(folder).as_posix()
            if p.stat().st_size > FILE_TEXT_MAX:
                big.append(rel)
                continue
            try:
                text[rel] = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                big.append(rel)
    nodes = [n["id"] for n in doc["graphs"][graph].get("nodes", [])]
    from .autohyp import pack_limits   # D-492 덧 28 (013-66): the Pack's own output promise is the upper end of 가져올 개수
    limits = {k: {"max": v[0], "from": v[1]} for k, v in pack_limits(folder).items()}
    return {"providers": providers, "files": files, "file_text": text, "too_large": big, "nodes": nodes, "enums": enums(folder, doc, graph),
            "limits": limits, "choices": {"method": list(METHODS)}}


def enums(folder, doc, graph):
    """The values a list field chooses from, read from the Pack's schemas (EV-022 manual p.7, user decision 10-13 'every field but
    the prompt is chosen'): require_source_types = the source_type enum of the item schema of the graph's list state keys
    (task10: evidence list[EvidenceRef] → [history, manual]). Read only; an unreadable schema adds nothing."""
    out = set()
    schemas = doc.get("schemas") or {}
    for t in ((doc["graphs"][graph].get("state") or {}).values()):
        t = t if isinstance(t, str) else (t or {}).get("type")
        m = re.fullmatch(r"list\[(\w+)\]", str(t or ""))
        if not m or m.group(1) not in schemas:
            continue
        try:
            s = json.loads((Path(folder) / schemas[m.group(1)]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        e = (((s.get("properties") or {}).get("source_type") or {}).get("enum")) if isinstance(s, dict) else None
        if isinstance(e, list):
            out.update(x for x in e if isinstance(x, str))
    return {"require_source_types": sorted(out)} if out else {}


def hunks(old, new, context=0):
    """Changed lines between two texts: [{old_start, old: [lines], new_start, new: [lines]}] (1-based line numbers). With
    context, each hunk also has the unchanged lines around it (before/after, from ctx_start in the old text) and the pack.yaml
    node id it sits in (the nearest '- id:' line above it), for the change view (EV-022 manual p.21)."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        h = {"old_start": i1 + 1, "old": a[i1:i2], "new_start": j1 + 1, "new": b[j1:j2]}
        if context:
            s = max(0, i1 - context)
            h.update(ctx_start=s + 1, before=a[s:i1], after=a[i2:i2 + context])
            h["ctx_count"] = len(h["before"]) + len(h["old"]) + len(h["after"])
            ids = [m.group(1) for line in a[:i2 or 1][::-1] if (m := re.match(r"\s*-\s*id:\s*([\w.-]+)", line))]
            h["id"] = ids[0] if ids else None
        out.append(h)
    return out
