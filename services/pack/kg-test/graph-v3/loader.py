"""D-543 ② 기본 반영 길 — 적재기: 처음 이력 + 받은 편지함(승인 한 건 = 파일 하나) → 원본 그대로 합침 → 그래프 새 꼬리표 → Pack이 읽는 release 파일
(pack/templates/graph/current.json 하나 · D-545 자리 · 꼴 = handoff/d545/graph-shape.md).
LLMOps 없이 돌림(들어올 때 시험 0). 관계는 build_v3.graph_rows 그대로(이상 나온 점검 → 확정 원인 → 한 조치 · 근거 건수 · 손 관계표 0).
- 받은 편지 꼴 = c1a15-app 초안(handoff/d543/kg-inbox-draft.md): {kind: "kg_record", schema: 1, report_id, approved_at, record{…}} — record 칸은 처음 이력과 같은 이름.
- 원본은 고치지 않음: 시각만 초 단위로 맞춤(Pack 시각 셈 꼴) · 승인 표시를 붙임(받은 편지 = 승인된 것) · 예외 상황 기록은 그대로 실어 둠(관계 만들기는 ⑥).
- 꼬리표 = <그래프 꼴 버전>-<합친 원본 sha256 앞 8자리> — 같은 원본이면 같은 버전(다시 만들지 않음) · 새 승인이 오면 새 버전.
- 꼴이 틀린 편지(덮개 · 모르는 증상군 · 번호를 바꿀 수 없는 절 · 센서 칸 없음 · 같은 이력 번호 두 번)는 빼고 목록에 남김 — 나머지는 싣음.
  예외 절(EX-…)을 거친 세션 편지는 받음(D-546 덧 27 ②): EX 절은 번호를 바꾸지 않고(그대로 EX-…) 매뉴얼 절 관계에도 넣지 않음 · 기록 · 원인 셈에는 들어감.
- 새 버전은 current.json에 바로 씀(옛 버전은 git · LLMOps 버전 기록) — Pack revision이 바뀌므로 들이는 것은 받아 넣기 길(D-543 덧 3 결정 4).

- 예외 줄(D-545): `--nodes <llmops-2 추출기 출력>` = {"sections": [예외 절 줄], "exception_relations": [예외 관계 줄]} — 꼴 검사(Pack custom/exception.check와 같은 규칙)
  → 소요 분이 비면 그 증상군 점검 절 분의 중앙값(올림) → 그래프에 예외 절 노드 · ExRel 노드 → release. 꼴을 어긴 줄은 빼고 목록.
  쌓기(D-546 덧 27 ①): 기준 release(--base · 기본 = --out 파일)의 예외 절 · 관계를 이어받고 새 줄 파일을 id로 합침(같은 id면 새 것 · 지우기는 LLMOps '끔' 길만).
  꼬리표 = <그래프 꼴 버전>-<원본 + 합친 예외 줄 sha256 앞 8자리>-ex<실제 관계 수> — 같은 꼬리표면 같은 내용 · 예외 줄이 없으면 <기준 꼬리표>.

kg-test/.venv-kg/bin/python -B kg-test/graph-v3/loader.py --history <앱 저장소>/data/history/maintenance_history.jsonl --inbox <받은 편지함 폴더> [--nodes <추출기 출력>] [--base <기준 release · 기본 = --out>] [--out pack/templates/graph/current.json]"""
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import build_v3 as B
import export_release as E
from conn import driver

sys.path.insert(0, str(B.KG.parent / "pack"))
from custom import exception as X  # noqa: E402 — 예외 관계 꼴 검사는 Pack과 같은 함수

EX_ID = re.compile(r"^EX-[A-Za-z0-9-]{1,60}$")   # 약속 v8 exception_guide.section · relation_id 꼴
SEC_KEYS = {"no", "app_no", "kind", "grade", "name", "guide", "steps", "criteria", "group", "minutes", "checklist", "records", "check", "action", "note",
            "topic", "checks", "actions", "previous"}   # v10(D-566): 예외 하나 = 주제 + 점검 목록 + 조치 목록 · 옛 하나 꼴(check · action)도 받음 · previous = 같음 + 고침 옛 내용(D-560 7)
CHECK_KEYS, ACTION_KEYS = {"what", "how", "criteria", "minutes", "instruction"}, {"what", "parts", "minutes", "instruction"}   # instruction = D-642 C 지시 문장(선택)   # D-554 예외 절 칸(꼴 정본 handoff/d545/graph-shape.md 2절)
REL_KEYS = {"id", "step", "mode", "grade", "when", "where", "cause", "cause_name", "records", "n", "text_sha256", "context", "extractor"}
EDIT_KEEP = ("topic", "checks", "actions", "check", "action")   # 같음 + 고침 때 previous에 남기는 옛 칸(D-560 7 · 꼴 정본 handoff/d566/same-edit.md 3절)


def _second(t):
    return datetime.fromisoformat(t).isoformat(timespec="seconds")


def read_inbox(folder):
    """받은 편지함 → (받은 이력 줄, 뺀 편지 [{file, why}]). 파일 이름 차례로 읽음."""
    known = {s["no"] for s in B.manual_sections()}
    groups = {s["group"] for s in B.manual_sections() if s["group"]}
    nmap = B.number_map()
    rows, bad, seen = [], [], set()
    for p in sorted(Path(folder).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            bad.append({"file": p.name, "why": "JSON 아님"})
            continue
        why = _check(d, known, groups, nmap, seen)
        if why:
            bad.append({"file": p.name, "why": why})
            continue
        r = d["record"]
        seen.add(r["record_id"])
        rows.append({**r, "occurred_at": _second(r["occurred_at"]), "approval": {"status": "승인", "report_id": d["report_id"], "approved_at": d["approved_at"]}})
    return rows, bad


def _check(d, known, groups, nmap, seen):
    if d.get("kind") != "kg_record" or d.get("schema") != 1 or not isinstance(d.get("record"), dict):
        return "덮개 꼴이 다름(kind kg_record · schema 1 · record)"
    r = d["record"]
    for k in ("record_id", "symptom_group", "equipment_id", "occurred_at", "sensor_snapshot", "steps", "outcome"):
        if k not in r:
            return f"칸 없음: {k}"
    if r["record_id"] in seen:
        return "같은 이력 번호 두 번"
    if r["symptom_group"].get("name") not in groups:
        return "매뉴얼에 없는 증상군"
    if any(f not in r["sensor_snapshot"] for f in B.SENSORS):
        return "센서 칸 없음"
    try:
        _second(r["occurred_at"])
    except (TypeError, ValueError):
        return "시각 꼴이 다름"
    for s in r["steps"]:
        if s.get("kind") in ("check", "action") and "section" in s and not EX_ID.match(str(s["section"])) and nmap.get(s["section"]) not in known:
            return "번호를 바꿀 수 없는 절"
    return None


def originals(history, inbox):
    """합친 원본(처음 이력 줄 그대로 + 받은 이력 줄) · 뺀 편지 · 받은 수."""
    base = [l for l in Path(history).read_text(encoding="utf-8").splitlines() if l.strip()]
    got, bad = read_inbox(inbox) if inbox else ([], [])
    ids = {json.loads(l)["record_id"] for l in base}
    keep = []
    for r in got:
        if r["record_id"] in ids:
            bad.append({"file": r["approval"]["report_id"], "why": "처음 이력과 같은 이력 번호"})
            continue
        keep.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    return base + keep, bad, len(keep)


def read_nodes(path):
    """추출기 줄 파일 → (예외 절 줄, 예외 관계 줄, 뺀 줄 [{row, why}]) — 꼴 정본 handoff/d545/graph-shape.md 2 · 3 · 5절."""
    return check_nodes(json.loads(Path(path).read_text(encoding="utf-8")))


def carried(path):
    """기준 release의 예외 줄 {sections: 예외 절, exception_relations} — 파일이 없으면 빈 것."""
    p = Path(path)
    rel = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return {"sections": [s for s in rel.get("sections") or [] if s.get("kind") == "예외"], "exception_relations": list(rel.get("exception_relations") or [])}


def merge(old, new):
    """id로 합침 — 같은 절 번호 · 관계 id면 새 것, 차례는 id 차례.
    같음 + 고침(D-560 7 · same-edit.md 3절): 관계 줄 edit: true · 절 줄 edit_record = 같은 id 기준 예외를 새 내용으로 바꿔 끼움 —
    절: 새 내용 · 옛 내용은 previous[{record, topic, checks, actions}]에 쌓음 / 관계: when · cause · mode · grade 기준 그대로 · records += 이번 기록 · n + 1.
    기준에 그 id가 없으면 그 줄(절 · 관계)은 bad_edit로 뺌."""
    osec = {s.get("no"): s for s in old["sections"]}
    orel = {x.get("id"): x for x in old["exception_relations"]}
    secs, rels, bad = dict(osec), dict(orel), []
    for s in new.get("sections") or []:
        no = s.get("no")
        if "edit_record" not in s:
            secs[no] = s
            continue
        base = osec.get(no)
        if base is None:
            bad.append({"row": str(no), "why": "고칠 예외가 그래프에 없음"})
            continue
        prev = list(base.get("previous") or []) + [{"record": s["edit_record"], **{k: base[k] for k in EDIT_KEEP if base.get(k) is not None}}]
        secs[no] = {**{k: v for k, v in s.items() if k != "edit_record"}, "previous": prev}
    for x in new.get("exception_relations") or []:
        rid = x.get("id")
        if not x.get("edit"):
            rels[rid] = {k: v for k, v in x.items() if k != "edit"}
            continue
        base = orel.get(rid)
        if base is None or rid in {b["row"] for b in bad}:
            bad.append({"row": str(rid), "why": "고칠 예외가 그래프에 없음"})
            continue
        recs = list(base.get("records") or []) + [r for r in x.get("records") or [] if r not in (base.get("records") or [])]
        rels[rid] = {**base, "records": recs, "n": (base.get("n") or 0) + len(recs) - len(base.get("records") or [])}
    return {"sections": [secs[k] for k in sorted(secs, key=str)], "exception_relations": [rels[k] for k in sorted(rels, key=str)], "bad_edit": bad}


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _mins(v):
    return v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 1)


def _check_ok(c):   # D-556 덧 12: 방법 · 기준은 빈 글 허용(칸은 있어야 함)
    return isinstance(c, dict) and set(c) <= CHECK_KEYS and _text(c.get("what")) and all(isinstance(c.get(k), str) for k in ("how", "criteria")) and _mins(c.get("minutes")) and _say_ok(c)


def _say_ok(r):   # D-642 C: 지시 문장은 있으면 빈 글이 아닌 글
    return "instruction" not in r or _text(r["instruction"])


def _action_ok(a):
    return (isinstance(a, dict) and set(a) <= ACTION_KEYS and _text(a.get("what")) and isinstance(a.get("parts", []), list)
            and all(_text(x) for x in a.get("parts", [])) and _mins(a.get("minutes")) and _say_ok(a))


def _side(s, one, many):
    """v10 목록(many) · 옛 하나 꼴(one) → 목록 · 둘 다 있으면 False · 없으면 []."""
    if s.get(one) is not None and s.get(many) is not None:
        return False
    if s.get(many) is not None:
        return s[many] if isinstance(s[many], list) and s[many] else False
    return [s[one]] if s.get(one) is not None else []


def part_why(s):
    """D-554 · v10 칸 꼴 — 어기면 까닭, 맞으면 None(칸이 없으면 None)."""
    cs, acts = _side(s, "check", "checks"), _side(s, "action", "actions")
    if cs is False or not all(map(_check_ok, cs)):
        return "점검 칸 꼴이 다름"
    if acts is False or not all(map(_action_ok, acts)):
        return "조치 칸 꼴이 다름"
    if s.get("topic") is not None and not _text(s["topic"]):
        return "주제 꼴이 다름"
    if s.get("note") is not None and not isinstance(s["note"], str):
        return "덧붙임 꼴이 다름"
    return None


def check_nodes(d):
    secs = B.manual_sections()
    names = {s["group"] for s in secs if s["group"]}
    mid = {g: math.ceil(statistics.median([s["minutes"] for s in secs if s["group"] == g and s["kind"] == "점검"])) for g in names}
    act = {g: math.ceil(statistics.median([s["minutes"] for s in secs if s["group"] == g and s["kind"] == "조치"])) for g in names}
    old = E.old_numbers()
    work = {g: {old.get(s["no"]) for s in secs if s["group"] == g and s["kind"] in ("점검", "조치")} for g in names}   # D-570 최근 정비 조건 절 = 그 증상군 점검 · 조치 절(앱 번호)
    keep_sec, bad = {}, []
    for s in d.get("sections") or []:
        why = ("칸이 허용 목록 밖" if set(s) - SEC_KEYS else "id 꼴이 다름(EX-…)" if not EX_ID.match(str(s.get("no"))) else
               "종류가 예외가 아님" if s.get("kind") != "예외" else "등급이 다름" if s.get("grade") not in ("A", "B", "C", "D") else
               "매뉴얼에 없는 증상군" if s.get("group") not in names else part_why(s) or (
               "글 칸 없음" if not (s.get("name") and (s.get("guide") or s.get("check") or s.get("checks"))) else
               "app_no가 no와 다름" if s.get("app_no") not in (None, s.get("no")) else "같은 절 두 번" if s.get("no") in keep_sec else None))
        if why:
            bad.append({"row": str(s.get("no")), "why": why})
            continue
        g = s["group"]
        cs = [{**c, "minutes": c.get("minutes") or mid[g]} for c in _side(s, "check", "checks")]   # D-554: 분 없으면 증상군 점검 · 조치 절 분 중앙값 · v10 목록
        acts = [{"what": a["what"], "parts": list(a.get("parts") or []), "minutes": a.get("minutes") or act[g], **({"instruction": a["instruction"]} if a.get("instruction") else {})}
                for a in _side(s, "action", "actions")]
        c = cs[0] if cs else None
        keep_sec[s["no"]] = {"no": s["no"], "app_no": s["no"], "kind": "예외", "grade": s["grade"], "name": s["name"], "guide": s.get("guide") or ((c or {}).get("how") or "").strip() or None,
                             "steps": list(s.get("steps") or [x["how"] for x in cs if x["how"].strip()]),   # 덧 12: 빈 방법 → 안내 글 None · 절차 0 · v10: 점검 방법들
                             "criteria": s["criteria"] if s.get("criteria") is not None else ((c or {}).get("criteria") or "").strip() or None,   # 덧 12: 빈 기준 → 안내 기준 줄 0
                             "group": g, "minutes": s["minutes"] if isinstance(s.get("minutes"), int) else (c or {}).get("minutes") or mid[g], "checklist": [],
                             "records": list(s.get("records") or [])}
        if cs:   # v10: 점검 · 조치는 목록으로 씀(옛 하나 꼴도 목록 하나) · 칸은 있을 때만
            keep_sec[s["no"]]["checks"] = cs
        if acts:
            keep_sec[s["no"]]["actions"] = acts
        if s.get("topic"):
            keep_sec[s["no"]]["topic"] = s["topic"].strip()
        if s.get("note"):
            keep_sec[s["no"]]["note"] = s["note"]
        if s.get("previous"):   # 같음 + 고침 옛 내용(D-560 7) — 판정 · 안내는 읽지 않음
            keep_sec[s["no"]]["previous"] = list(s["previous"])
    keep_rel = []
    for x in d.get("exception_relations") or []:
        why = "칸이 허용 목록 밖" if set(x) - REL_KEYS else "id 꼴이 다름(EX-…)" if not EX_ID.match(str(x.get("id"))) else "mode가 다름" if x.get("mode") not in ("안내", "참고") else None
        if not why:
            try:
                X.check(x, keep_sec, names)
            except X.RuleError as e:
                why = str(e).split(": ", 1)[-1]
        w = x.get("when") or {}
        r = w.get("recent")
        if not why and r and r["section"] not in work.get(w["group"], set()):
            why = "최근 정비 절이 그 증상군 점검 · 조치 절이 아님"
        if not why and not (w.get("sensors") or w.get("equipment_id") or r):   # D-570 덧 4: 조건 0 = 그 증상군 모든 알람에 추천
            why = "조건 칸이 하나도 없음(센서 · 설비 · 최근 정비)"
        if why or x["id"] in {r["id"] for r in keep_rel}:
            bad.append({"row": str(x.get("id")), "why": why or "같은 관계 두 번"})
            continue
        keep_rel.append(x)
    used = {x["step"] for x in keep_rel}
    return [s for no, s in sorted(keep_sec.items()) if no in used], keep_rel, bad


def _mapish(v):
    return isinstance(v, dict) or (isinstance(v, list) and any(isinstance(i, dict) for i in v))


def relation_names(secs, rels):
    """예외 관계 번호 → 그래프 화면 표시 이름(D-619): 그 절 주제 · 주제가 없으면 절 이름."""
    sec = {s["no"]: s for s in secs}
    return {x["id"]: sec[x["step"]].get("topic") or sec[x["step"]]["name"] for x in rels if x["step"] in sec}


def name_relations(secs, rels, tag, drv):
    """ExRel 노드 표시 이름 — 칸 name이 없으면 그래프 화면이 cause_name 글을 이름 자리에 띄움. 내보내기 밖(판정 · release는 안 읽음)."""
    with drv.session() as s:
        for rid, name in relation_names(secs, rels).items():
            s.run("MATCH (e:ExRel {layer: $v, id: $id}) SET e.name = $name", v=tag, id=rid, name=name).consume()


def link_recent(rels, tag, drv):
    """최근 정비 조건 → 그래프 선(D-592): (ExRel)-[:WHEN_RECENT {days, app_no}]->(Section) · 조건 절은 앱 번호라 그래프 번호로 바꿔 이음 · 판정 · release는 안 읽음(when.recent 그대로)."""
    old = E.old_numbers()
    with drv.session() as s:
        for x in rels:
            w = x["when"]
            if w.get("recent"):
                app = w["recent"]["section"]
                s.run("MATCH (e:ExRel {layer: $v, id: $id}), (:Group {layer: $v, name: $g})-[:HAS_SECTION]->(x:Section {layer: $v}) WHERE x.no IN $nos AND x.kind IN ['점검', '조치'] "
                      "MERGE (e)-[r:WHEN_RECENT]->(x) SET r.days = $d, r.app_no = $a", v=tag, id=x["id"], g=w["group"], nos=[n for n, a in old.items() if a == app], d=w["recent"]["days"], a=app).consume()


def put_nodes(secs, rels, tag, drv, nodes_sha):
    with drv.session() as s:
        for x in secs:
            p = {k: v for k, v in x.items() if not _mapish(v)}
            p.update({f"{k}_json": json.dumps(v, ensure_ascii=False, sort_keys=True) for k, v in x.items() if _mapish(v)})   # D-554 · v10 점검 · 조치 칸(그래프 DB는 맵 · 맵 목록 칸을 못 담음)
            s.run("CREATE (x:Section {layer: $v}) SET x += $p", v=tag, p=p).consume()
            s.run("MATCH (g:Group {layer: $v, name: $g}), (x:Section {layer: $v, no: $no}) CREATE (g)-[:HAS_SECTION]->(x)", v=tag, g=x["group"], no=x["no"]).consume()
        for x in rels:
            p = {k: v for k, v in x.items() if not isinstance(v, dict)}
            p.update({f"{k}_json": json.dumps(v, ensure_ascii=False, sort_keys=True) for k, v in x.items() if isinstance(v, dict)})
            s.run("CREATE (e:ExRel {layer: $v}) SET e += $p", v=tag, p=p).consume()
            s.run("MATCH (e:ExRel {layer: $v, id: $id}), (x:Section {layer: $v, no: $no}) CREATE (e)-[:GUIDES]->(x)", v=tag, id=x["id"], no=x["step"]).consume()
        recs = sorted({r for x in rels for r in x.get("records") or []})
        s.run("MATCH (m:Layer {layer: $v}) SET m.nodes_sha256 = $h, m.exception_records = $r", v=tag, h=nodes_sha, r=recs).consume()
    link_recent(rels, tag, drv)
    name_relations(secs, rels, tag, drv)


def load(history, inbox, out, drv, nodes=None, base_release=None):
    lines, bad, n_in = originals(history, inbox)
    text = "".join(l + "\n" for l in lines)
    base = f"{B.VERSION}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"
    dest = Path(out)
    old = carried(base_release or dest)
    rows = merge(old, json.loads(Path(nodes).read_text(encoding="utf-8")) if nodes else {})
    secs, rels, bad_rows = check_nodes(rows) if rows["exception_relations"] else ([], [], [])
    bad_rows = rows.get("bad_edit", []) + bad_rows   # 같음 + 고침 대상이 기준에 없음(D-560 7)
    ex_text = json.dumps({"sections": secs, "exception_relations": rels}, ensure_ascii=False, sort_keys=True)
    nodes_sha = hashlib.sha256(ex_text.encode()).hexdigest() if rels else None   # 합친 예외 줄의 sha(줄 파일 바이트 아님)
    tag = f"{B.VERSION}-{hashlib.sha256((text + ex_text).encode()).hexdigest()[:8]}-ex{len(rels)}" if rels else base
    res = {"tag": tag, "records": {"history": len(lines) - n_in, "inbox": n_in}, "rejected": bad, "release": str(dest)}
    if nodes or old["exception_relations"]:
        res.update(exception={"sections": len(secs), "relations": len(rels), "carried": len(old["exception_relations"])}, rejected_rows=bad_rows)
    now = json.loads(dest.read_text(encoding="utf-8")) if dest.is_file() else {}
    with drv.session() as s:
        in_db = s.run("MATCH (m:Layer {layer: $v}) RETURN count(m) AS n", v=tag).single()["n"] > 0
    if in_db and now.get("layer") == tag and now.get("source", {}).get("nodes_sha256") == nodes_sha:   # 파일 · 그래프 DB 모두 그 버전일 때만 손 0(이 고침 전 적재한 꼬리표는 선만 채움)
        link_recent(rels, tag, drv)
        name_relations(secs, rels, tag, drv)
        return {**res, "release_id": now["release_id"], "changed": False}
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "originals.jsonl"
        src.write_text(text, encoding="utf-8")
        B.put(B.graph_rows(src), tag, drv)
    if rels:
        put_nodes(secs, rels, tag, drv, nodes_sha)
        with drv.session() as s:
            s.run("MATCH (m:Layer {layer: $v}) SET m.base_layer = $b", v=tag, b=base).consume()
    rel = E.export(tag, drv)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rel, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {**res, "release_id": rel["release_id"], "changed": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--inbox")
    ap.add_argument("--nodes")
    ap.add_argument("--base", help="예외 줄을 이어받을 기준 release(기본 = --out 파일)")
    ap.add_argument("--out", default=str(B.KG.parent / "pack/templates/graph/current.json"))
    a = ap.parse_args()
    with driver() as d:
        print(json.dumps(load(a.history, a.inbox, a.out, d, a.nodes, a.base), ensure_ascii=False))
