"""EV-090 (D-495 ② · ORCH 294-answer Q-1 (가) · 보고 297): one wheel run beforehand on a copy, carried into another store (the shared
LLMOps) as it is, so its version waits there as '승인 대기' — the receiving server computes the badge from its own wheel records only
and has no other way to take them in. Carried: the wheel, its hypotheses, candidates (the recommendation, a sealed result kept on it),
log, the confirmed edit, the new version's release and Pack row, the new version's Runs and the base's Runs of the same suites (or of
this wheel), the suites they ran; the edit and Pack copy folders. Renumbered: the wheel's seq, hypothesis · candidate · log · edit ids,
Run seq and label, the version's V-name; text ids (release · Run · suite) kept. Not carried: spend rows (the receiving caps stay honest)
and diagnosis rows (the receiving server keeps its own). One log line says it ran beforehand on a copy. plan() reads only; apply()
writes in one transaction and leaves a record file; undo() takes exactly that back."""
import json
import shutil
import time
from pathlib import Path

from . import versions

LOG_HEAD = "사본에서 미리 돌린 바퀴 · 옮김"
LOG = LOG_HEAD + " {at} · 사본 A-{src_seq} · 사본 데이터 {src} — 비용 줄은 사본에 남김(여기 상한에 안 셈)"


class CarryError(RuntimeError):
    pass


def _j(text):
    return json.loads(text) if isinstance(text, str) and text else (text or None)


def _meta(run):
    return _j(run.get("meta")) or {}


def _tree_has_env(folder):
    return any(p.name == ".env" for p in Path(folder).rglob("*"))


def plan(src, src_pid, name, dst, dst_pid, dst_base, src_data, dst_data):
    """What a carry would write, read only: {ok, errors, summary, b}. name = 'A-<seq>' on the copy."""
    errors, src_data, dst_data = [], Path(src_data), Path(dst_data)
    try:
        seq = int(str(name).removeprefix("A-"))
    except ValueError:
        return {"ok": False, "errors": [f"바퀴 이름 {name} — A-<번호> 꼴이어야 합니다"], "summary": {}, "b": None}
    w = next((x for x in src.carry_rows("auto_wheel", "project_id=? AND seq=?", (src_pid, seq))), None)
    if not w:
        return {"ok": False, "errors": [f"사본에 바퀴 {name}이 없습니다"], "summary": {}, "b": None}
    if w["ended_at"] is not None or w["cell"] != 7:
        errors.append(f"사본 {name}이 '⑦ 승인 사람 차례'가 아닙니다(cell {w['cell']} · 끝 {w['ended_at'] is not None})")
    cands = src.carry_rows("auto_candidate", "wheel_id=? ORDER BY candidate_id", (w["wheel_id"],))
    win = next((c for c in reversed(cands) if c["release_id"] and (_j(c["recommendation"]) or {}).get("decision") in ("adopt", "hold")), None)
    if not win:
        return {"ok": False, "errors": errors + ["권고(채택 · 보류)가 붙은 버전 후보가 없습니다"], "summary": {}, "b": None}
    rel = src.release(win["release_id"])
    frm = ((rel or {}).get("pack") or {}).get("from") or {}
    base_src, base_dst = src.release(w["base_release_id"]), dst.release(dst_base)
    if not rel or (frm.get("auto") or {}).get("wheel") != seq or not frm.get("edit_id"):
        errors.append("후보 버전이 이 바퀴의 확정 편집 버전이 아닙니다(pack.from.auto · edit_id)")
    if not base_dst or base_dst.get("project_id") != dst_pid:
        errors.append(f"받는 쪽 기준 release {dst_base}가 그 Project에 없습니다")
    elif (base_dst.get("pack") or {}).get("revision") != frm.get("revision"):
        errors.append(f"기준 revision이 다릅니다 — 사본 {str(frm.get('revision'))[7:15]} · 받는 쪽 {str((base_dst.get('pack') or {}).get('revision'))[7:15]}")
    openw = dst.open_wheel(dst_pid)
    if openw:
        errors.append(f"받는 쪽에 열린 개선 A-{openw['seq']}이 있습니다 — 끝난 뒤 옮깁니다")
    edit = next(iter(src.carry_rows("release_edit", "edit_id=?", (frm.get("edit_id"),))), None) if frm.get("edit_id") else None
    if frm.get("edit_id") and not edit:
        errors.append("확정 편집 줄이 사본에 없습니다")
    runs = src.carry_rows("runs", "project_id=? ORDER BY seq", (src_pid,))
    rec = _j(win["recommendation"]) or {}
    state = (_j(w["settings"]) or {}).get("_main") or {}
    new_runs = [r for r in runs if rel and r["release_id"] == rel["id"]]
    inputs = {(_meta(r).get("input") or {}).get("id") for r in new_runs}
    named = {((rec.get("base") or {}).get("run")), *(state.get("baseline") or [])}
    keep = [r for r in runs if r in new_runs or (base_src and r["release_id"] == base_src["id"] and (
        (_meta(r).get("input") or {}).get("id") in inputs or (_meta(r).get("auto") or {}).get("wheel") == seq or r["id"] in named))]
    if any(dst.carry_rows("runs", "id=?", (r["id"],)) for r in keep):
        errors.append("받는 쪽에 같은 Run id가 있습니다(이미 옮김?)")
    if rel and dst.release(rel["id"]):
        errors.append(f"받는 쪽에 같은 release id {rel['id']}가 있습니다(이미 옮김?)")
    if any(src.carry_rows("verdicts", "run_id=?", (r["id"],)) for r in keep):
        errors.append("사람 판정이 있는 Run은 옮기지 않습니다")
    sids = {(_meta(r).get("input") or {}).get("id") for r in keep if (_meta(r).get("input") or {}).get("kind") == "suite"}
    sids |= {v for v in (state.get("suites") or {}).values() if isinstance(v, str)}
    suites = [s for sid in sorted(x for x in sids if x) for s in src.carry_rows("eval_suites", "id=?", (sid,)) if not dst.carry_rows("eval_suites", "id=?", (sid,))]
    edit_dir = Path(rel["path"]).parent if rel else None
    copy_dir = Path((rel or {}).get("pack", {}).get("copy") or "") if rel else None
    for f in (edit_dir, copy_dir):
        if f is None or not f.is_dir():
            errors.append(f"사본 폴더가 없습니다: {f}")
        elif _tree_has_env(f):
            errors.append(f"사본 폴더에 .env가 있습니다 — 옮기지 않음(B-8): {f}")
    if copy_dir and (dst_data / "packs" / rel["id"]).exists():
        errors.append("받는 쪽 Pack 사본 폴더가 이미 있습니다")
    pk = next(iter(src.carry_rows("pack_releases", "release_id=?", (rel["id"],))), None) if rel else None
    left = (pk or {}).get("data", "").replace(str(edit_dir), "").replace(str(copy_dir), "") if pk else ""
    if str(src_data) in left:
        errors.append("Pack 줄이 옮기지 않는 사본 폴더를 가리킵니다")
    b = {"seq": seq, "wheel": w, "hyps": src.carry_rows("auto_hypothesis", "wheel_id=? ORDER BY hypothesis_id", (w["wheel_id"],)),
         "cands": cands, "win": win["candidate_id"], "logs": src.carry_rows("auto_wheel_log", "wheel_id=? ORDER BY log_id", (w["wheel_id"],)),
         "rel_row": next(iter(src.carry_rows("releases", "id=?", (rel["id"],))), None) if rel else None, "pack": pk, "edit": edit,
         "runs": keep, "cases": {r["id"]: src.carry_rows("run_cases", "run_id=? ORDER BY idx", (r["id"],)) for r in keep},
         "events": {r["id"]: src.carry_rows("run_events", "run_id=? ORDER BY seq", (r["id"],)) for r in keep},
         "suites": suites, "base_src": (base_src or {}).get("id"), "dst_pid": dst_pid, "dst_base": dst_base,
         "base_name": (base_dst or {}).get("name"), "base_copy": ((base_dst or {}).get("pack") or {}).get("copy"),
         "edit_dir": str(edit_dir) if edit_dir else None, "copy_dir": str(copy_dir) if copy_dir else None, "src_data": str(src_data)}
    summary = {"wheel": name, "release": (rel or {}).get("id"), "name_next": versions.next_name(dst.releases(dst_pid)),
               "rows": {"hypotheses": len(b["hyps"]), "candidates": len(cands), "log": len(b["logs"]) + 1, "runs": len(keep),
                        "suites": len(suites), "edit": 1, "release": 1}, "folders": [str(edit_dir), str(copy_dir)]}
    return {"ok": not errors, "errors": errors, "summary": summary, "b": b}


def _relabel(text, old, new):
    return text.replace(f"A-{old}", f"A-{new}") if isinstance(text, str) else text


def apply(dst, p, dst_data, record_dir):
    """Write a plan in one transaction, copy the two folders, leave a record file. Raises CarryError (nothing written) on a bad plan."""
    if not p or not p.get("ok"):
        raise CarryError("; ".join((p or {}).get("errors") or ["계획 없음"]))
    b, dst_data, now = p["b"], Path(dst_data), time.time()
    if dst.open_wheel(b["dst_pid"]):
        raise CarryError("받는 쪽에 열린 개선이 생겼습니다 — 다시 --check")
    rid, src_seq = b["rel_row"]["id"], b["seq"]
    tmp = dst_data / "edits" / f".carry-{int(now * 1000)}"
    pack_dst = dst_data / "packs" / rid
    shutil.copytree(b["edit_dir"], tmp)
    shutil.copytree(b["copy_dir"], pack_dst)
    rec = {"at": now, "source": {"data": b["src_data"], "wheel": f"A-{src_seq}"}, "folders": [str(pack_dst)], "runs": [], "suites": [],
           "hyps": [], "cands": [], "logs": []}
    try:
        with dst.carry_tx() as (ins, sql):
            pid = b["dst_pid"]
            seq = (sql("SELECT MAX(seq) AS m FROM auto_wheel WHERE project_id=?", (pid,)).fetchone()["m"] or 0) + 1
            name = versions.next_name(dst.releases(pid))
            ins("releases", {**{k: v for k, v in b["rel_row"].items()}, "project_id": pid, "name": name, "path": str(tmp / "pack"),
                             "created_at": now, "updated_at": now})
            e = {k: v for k, v in b["edit"].items() if k != "edit_id"}
            eid = ins("release_edit", {**e, "project_id": pid, "release_id": b["dst_base"], "base_dir": b["base_copy"], "result_release_id": rid,
                                       "memo": _relabel(e.get("memo"), src_seq, seq)})
            edit_dir = dst_data / "edits" / str(eid)
            sql("UPDATE releases SET path=? WHERE id=?", (str(edit_dir / "pack"), rid))
            wrow = {k: v for k, v in b["wheel"].items() if k != "wheel_id"}
            wid = ins("auto_wheel", {**wrow, "project_id": pid, "seq": seq, "base_release_id": b["dst_base"]})
            hmap = {}
            for h in b["hyps"]:
                hmap[h["hypothesis_id"]] = ins("auto_hypothesis", {**{k: v for k, v in h.items() if k != "hypothesis_id"}, "wheel_id": wid})
            rec["hyps"] = list(hmap.values())
            labels, run_map = {}, []
            for r in b["runs"]:
                nseq = (sql("SELECT MAX(seq) AS m FROM runs WHERE project_id=?", (pid,)).fetchone()["m"] or 0) + 1
                m = _meta(r)
                labels[m.get("label")] = f"R-{nseq}"
                is_new = r["release_id"] == rid
                m.update(label=f"R-{nseq}", rel_name=name if is_new else b["base_name"])
                if isinstance(m.get("auto"), dict) and m["auto"].get("wheel") == src_seq:
                    m["auto"] = {**m["auto"], "wheel": seq, "text": _relabel(m["auto"].get("text"), src_seq, seq)}
                ins("runs", {**r, "project_id": pid, "seq": nseq, "release_id": rid if is_new else b["dst_base"], "meta": json.dumps(m, ensure_ascii=False)})
                for c in b["cases"][r["id"]]:
                    ins("run_cases", c)
                for ev in b["events"][r["id"]]:
                    ins("run_events", ev)
                run_map.append(r["id"])
            rec["runs"] = run_map
            cmap = {}
            for c in b["cands"]:
                rc = _j(c["recommendation"])
                if rc:
                    for side in ("base", "new"):
                        if isinstance(rc.get(side), dict):
                            rc[side] = {**rc[side], "label": labels.get(rc[side].get("label"), rc[side].get("label")),
                                        **({"release": rid if rc[side].get("release") == rid else b["dst_base"]} if rc[side].get("release") else {})}
                row = {**{k: v for k, v in c.items() if k != "candidate_id"}, "wheel_id": wid,
                       "hypothesis_ids": json.dumps([hmap.get(i, i) for i in (_j(c["hypothesis_ids"]) or [])]),
                       "release_id": rid if c["release_id"] == rid else None,
                       "recommendation": json.dumps(rc, ensure_ascii=False) if rc else c["recommendation"]}
                cmap[c["candidate_id"]] = ins("auto_candidate", row)
            rec["cands"] = list(cmap.values())
            for lg in b["logs"]:
                rec["logs"].append(ins("auto_wheel_log", {**{k: v for k, v in lg.items() if k != "log_id"}, "wheel_id": wid}))
            at = time.strftime("%m-%d %H:%M", time.localtime(now))
            rec["logs"].append(ins("auto_wheel_log", {"wheel_id": wid, "cell": 7, "created_at": now,
                                                      "text": LOG.format(at=at, src_seq=src_seq, src=Path(b["src_data"]).name)}))
            for s in b["suites"]:
                ins("eval_suites", {**s, "project_id": pid})
                rec["suites"].append(s["id"])
            data = json.loads(b["pack"]["data"].replace(b["edit_dir"], str(edit_dir)).replace(b["copy_dir"], str(pack_dst)))
            frm = data.get("from") or {}
            auto = frm.get("auto") or {}
            data["from"] = {**frm, "rid": b["dst_base"], "release": b["base_name"], "edit_id": eid, "memo": _relabel(frm.get("memo"), src_seq, seq),
                            "auto": {**auto, "wheel": seq, "row": cmap.get(auto.get("row"), auto.get("row")),
                                     "hypotheses": [hmap.get(i, i) for i in auto.get("hypotheses") or []]}}
            ins("pack_releases", {"release_id": rid, "data": json.dumps(data, ensure_ascii=False)})
        tmp.rename(edit_dir)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(pack_dst, ignore_errors=True)
        raise
    rec.update(wheel=f"A-{seq}", wheel_id=wid, release={"id": rid, "name": name}, edit_id=eid, project=b["dst_pid"])
    rec["folders"].append(str(edit_dir))
    Path(record_dir).mkdir(parents=True, exist_ok=True)
    path = Path(record_dir) / f"carry-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{rid}.json"
    rec["path"] = str(path)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def undo(dst, record_path, dst_data):
    """Take back exactly what a record says was written (rows in one transaction, then the folders)."""
    rec = json.loads(Path(record_path).read_text(encoding="utf-8"))
    with dst.carry_tx() as (_, sql):
        for rid in rec["runs"]:
            for t in ("run_events", "run_cases", "verdicts"):
                sql(f"DELETE FROM {t} WHERE run_id=?", (rid,))
            sql("DELETE FROM runs WHERE id=?", (rid,))
        for t, k, ids in (("auto_wheel_log", "log_id", rec["logs"]), ("auto_candidate", "candidate_id", rec["cands"]),
                          ("auto_hypothesis", "hypothesis_id", rec["hyps"])):
            for i in ids:
                sql(f"DELETE FROM {t} WHERE {k}=?", (i,))
        sql("DELETE FROM auto_wheel WHERE wheel_id=?", (rec["wheel_id"],))
        sql("DELETE FROM pack_releases WHERE release_id=?", (rec["release"]["id"],))
        sql("DELETE FROM release_edit WHERE edit_id=?", (rec["edit_id"],))
        sql("DELETE FROM releases WHERE id=?", (rec["release"]["id"],))
        for sid in rec["suites"]:
            sql("DELETE FROM eval_suites WHERE id=?", (sid,))
    for f in rec["folders"]:
        shutil.rmtree(f, ignore_errors=True)
    return rec
