"""D-560 겹침 (dev-2 · llmops-2 · 정답지 31쪽 · eval T7): a person's 바꿈 · 합침 of two overlapping exceptions made into a candidate
graph file for the deploy side (orchestration agents/deploy handoff/d560/candidate-graph-inlet.md · rows = c1a15 handoff/d560/
extractor-rows.md 3 · 4절). The pair is the gate's overlap pair (kggate.overlap — each side's whole section and relation rows under
'line'). No model: the merge draft here is a fake one (the existing lists, then the new one's items the existing has not); a real draft
is a model call, after its own approval."""
import hashlib
import json
import os
import re
from pathlib import Path

FORMAT = "kg-candidate@1"
FOLDER = "kg-candidates"
CHOICES = "kg-choices.jsonl"   # the person's 유지 · 바꿈 · 합침, one line per held report (this LLMOps' own record)
PERSON = "llmops-owner"   # the LLMOps owner (no sign-in on this local product)
ID = re.compile(r"^KGC-[a-z0-9]+-[0-9]{4}-[0-9a-f]{8}$")
DROP = ("previous", "check", "action", "guide", "steps", "criteria", "edit_record")   # the loader keeps previous · refills the old fields


class ChoiceError(ValueError):
    pass


def _lines(pair):
    a, b = (pair.get("from_section") or {}).get("line") or {}, (pair.get("to_section") or {}).get("line") or {}
    if not all(isinstance(x.get(k), dict) for x in (a, b) for k in ("section", "relation")):
        raise ChoiceError("겹침 줄에 두 예외의 절 · 관계 줄이 없음")
    return a, b


def _record(line):
    recs = line["relation"].get("records") or []
    if not recs:
        raise ChoiceError("새 예외의 근거 기록이 없음")
    return recs[0]


def replace_rows(pair):
    """바꿈 (3절): the existing exception's section with the new one's lists, edit_record = the new record; its relation marked edit."""
    a, b = _lines(pair)
    rid = _record(b)
    sec = {**{k: v for k, v in a["section"].items() if k not in DROP}, "checks": b["section"].get("checks"), "actions": b["section"].get("actions"),
           "edit_record": rid}
    rel = {**{k: v for k, v in a["relation"].items() if k != "extractor"}, "records": [rid], "n": 1, "edit": True}
    return {"sections": [sec], "exception_relations": [rel]}


def merge_draft(pair):
    """A fake merge draft: the existing check list · action list, then the new one's items whose 'what' the existing has not."""
    a, b = _lines(pair)
    out = {"fake": True}
    for k in ("checks", "actions"):
        mine = list(a["section"].get(k) or [])
        seen = {(x.get("what") or "").strip() for x in mine}
        out[k] = mine + [x for x in b["section"].get(k) or [] if (x.get("what") or "").strip() not in seen]
    return out


def _ok(items):
    return isinstance(items, list) and 1 <= len(items) <= 10 and all(isinstance(x, dict) and (x.get("what") or "").strip() for x in items)


def merge_rows(pair, confirmed):
    """합침 (4절): the existing section with the lists the person confirmed (both records, edit_record = the new record) and the new
    relation whole, its step the existing exception — the new section then points nowhere and leaves the release."""
    if not isinstance(confirmed, dict) or not _ok(confirmed.get("checks")) or not _ok(confirmed.get("actions")):
        raise ChoiceError("합칠 점검 · 조치 목록이 비었거나 꼴이 틀림")
    a, b = _lines(pair)
    rid = _record(b)
    recs = list(dict.fromkeys(list(a["section"].get("records") or a["relation"].get("records") or []) + list(b["relation"].get("records") or [])))
    sec = {**{k: v for k, v in a["section"].items() if k not in DROP}, "checks": confirmed["checks"], "actions": confirmed["actions"], "records": recs,
           "edit_record": rid}
    rel = {**{k: v for k, v in b["relation"].items() if k not in ("edit", "extractor")}, "step": a["section"]["no"]}
    return {"sections": [sec], "exception_relations": [rel]}


def write(data_dir, pack_id, base, reason, source, person, rows):
    """One kg-candidate@1 file under <data dir>/kg-candidates/ — KGC-<pack>-<next number>-<sha8 of the rows>; written under a
    temporary name and renamed, never over a name already there. Returns the candidate id."""
    if not isinstance(base, dict) or not str(base.get("revision") or "").startswith("sha256:"):
        raise ChoiceError("기준 그래프 revision이 없음")
    folder = Path(data_dir) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    sha8 = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    pack = re.sub(r"[^a-z0-9]", "", str(pack_id).lower()) or "pack"
    have = [int(m.group(1)) for p in folder.glob("KGC-*.json") for m in [re.match(rf"^KGC-{pack}-([0-9]{{4}})-", p.name)] if m]
    n = max(have, default=0) + 1
    cid = f"KGC-{pack}-{n:04d}-{sha8}"
    doc = {"format": FORMAT, "candidate_id": cid, "base": {"tag": base.get("tag"), "layer": base.get("layer"), "revision": base["revision"]},
           "reason": reason, "source": source, "made_by": person, "decided_by": person, "rows": rows}
    tmp = folder / f".{cid}.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    dest = folder / f"{cid}.json"
    if dest.exists():
        tmp.unlink()
        raise ChoiceError("같은 후보 이름이 이미 있음")
    os.replace(tmp, dest)
    return cid


def choices(data_dir):
    """{report_id: {choice, file_id, at}} (file_id = the kg-candidate@1 file written, none for 유지) — the choices made so far (the last line of a report wins; one is ever written)."""
    p = Path(data_dir) / CHOICES
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("report_id"):
                out[d["report_id"]] = {"choice": d.get("choice"), "file_id": d.get("file_id"), "at": d.get("at")}
    return out


def record_choice(data_dir, report_id, choice, file_id, at):
    with open(Path(data_dir) / CHOICES, "a", encoding="utf-8") as f:
        f.write(json.dumps({"report_id": report_id, "choice": choice, "file_id": file_id, "by": PERSON, "at": at}, ensure_ascii=False) + "\n")
