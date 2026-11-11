#!/usr/bin/env python3
"""[dev-2] D-570 실시간 시연 준비(배포 몫) — 시연 이력 한 줄 · 시연 전용 Pack 복제. 시연 스택 손 0 · 공용 c1a15-pack 쓰기 0(clone · fetch만).
  history --base <이력 jsonl> --out <시연 이력 jsonl> --at <알람 시각 ISO | now(시연 그때 · 넣는 순간 기준)> [--equipment M-01] [--section 4.3.2] [--days-ago 3] [--days 7] [--record-id H-D570]
      처음 이력 그대로 + 시연 줄 하나(그 절 action이 든 가장 늦은 승인 이력을 본으로 · 설비 · 시각만 바꿈 · Pack recent-condition.md 4절).
      --line <앱 줄 파일>: 앱 demo_history add가 찍은 줄을 그대로 붙임(앱 이력 표 · 그래프 records 같은 번호 · 권장).
      대조: [알람 - days, 알람] 안 그 절 정비가 시연 설비 말고 있으면 멈춤(exit 2 · '최근 정비 없는 같은 알람 = 추천 0'이 깨짐).
  examples --in <이력 jsonl> --out <새 jsonl> [--keep A] [--line <앱 줄 파일> ...] [--app-examples <앱 예시 파일>]
      첫 상태 그래프 이력의 사전 점검 예시 줄(provenance '사전 점검 예시')을 패턴으로 고름 — 기본 A(흡기 필터 교체 → 덮개)만 · B(공구 교체) · C(엔코더) 줄은
      새 파일에 넣지 않음(원래 파일 그대로 · 지움 0). 예시 아닌 줄 · 차례 그대로. 남길 패턴에 줄이 없거나 모르는 증상 코드면 멈춤(exit 2 · 파일 안 씀).
      --line: 앱이 첫 상태(복원) 때 새로 찍은 시연 줄 파일(line-M-01 · 03 · 13 · 줄 여럿 가능)의 줄마다 같은 번호 줄을 바꿈(자리 그대로) ·
      바탕에 없는 새 번호(예 H-0304)는 끝에 덧붙임 — 그래프 이력 시각 = 앱 유효 시각. 앱 파일들에 같은 번호가 두 번이면 멈춤.
      --app-examples: 앱이 그 첫 상태에 precheck-add로 넣은 예시 파일 — 같은 번호 예시 줄을 그 줄로 바꿈(앱은 받은 시각 그대로 넣음).
      두 옵션 모두 앱이 찍은 줄을 그대로 쓰므로 기준 시각은 앱 한 곳(손으로 박는 날짜 0). 예시 파일의 없는 번호면 멈춤.
  clone --dest <폴더> --ref <Pack 커밋 · 가지> [--tag d570-g0]
      공용 c1a15-pack에서 clone(이미 있으면 fetch) → ref를 시연 가지 demo로 · 태그 → revision 한 줄. kg_apply 그래프 커밋 · 태그는 이 복제에만 쌓임(위험 1)."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

def _pack_src():
    """공용 c1a15-pack 자리 — DEMO_PACK_SRC 또는 이 저장소 위로 올라가며 옆의 c1a15-pack(main 체크아웃 · repo/worktrees/<이름> 둘 다)."""
    if os.environ.get("DEMO_PACK_SRC"):
        return Path(os.environ["DEMO_PACK_SRC"])
    for d in Path(__file__).resolve().parents[1].parents:
        if (d / "c1a15-pack" / ".git").exists():
            return d / "c1a15-pack"
    raise SystemExit("멈춤: 공용 c1a15-pack 자리를 못 찾음 — DEMO_PACK_SRC로 주세요")


PACK = None


def did(rec, section):
    return any(s.get("kind") == "action" and s.get("section") == section for s in rec.get("steps") or [])


def shift(t, d):
    return (datetime.fromisoformat(t) + d).isoformat(timespec="seconds")


def history(a):
    lines = [l for l in Path(a.base).read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = [json.loads(l) for l in lines]
    given = json.loads(Path(a.line).read_text(encoding="utf-8").strip().splitlines()[-1]) if a.line else None   # 앱 demo_history add가 찍은 줄
    if given:
        a.record_id, a.equipment = given["record_id"], given["equipment_id"]
    if any(r["record_id"] == a.record_id for r in recs):
        raise SystemExit(f"멈춤: 이력 번호 {a.record_id}가 이미 있음")
    at = datetime.now().astimezone().replace(microsecond=0) if a.at == "now" else datetime.fromisoformat(a.at)   # D-570 덧 3: 시연 그때 기준
    if at.tzinfo is None:
        raise SystemExit("멈춤: --at에 시간대 칸이 없음(예 2026-11-11T10:00:00+09:00)")
    lo = at - timedelta(days=a.days)
    near = sorted({r["equipment_id"] for r in recs if did(r, a.section) and lo <= datetime.fromisoformat(r["occurred_at"]) <= at} - {a.equipment})
    if near:
        print(json.dumps({"stop": "다른 설비에 최근 정비가 있음", "section": a.section, "equipment": near}, ensure_ascii=False))
        sys.exit(2)
    if given:
        Path(a.out).write_text("".join(l + "\n" for l in lines) + json.dumps(given, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"out": a.out, "record_id": a.record_id, "equipment": a.equipment, "occurred_at": given.get("occurred_at"), "from": "앱 줄",
                          "lines": len(lines) + 1, "other_equipment_in_window": 0}, ensure_ascii=False))
        return
    base = [r for r in recs if did(r, a.section) and (r.get("approval") or {}).get("status") == "승인"]
    if not base:
        raise SystemExit(f"멈춤: 절 {a.section} action이 든 승인 이력이 없음")
    t = max(base, key=lambda r: r["occurred_at"])
    d = (at - timedelta(days=a.days_ago)) - datetime.fromisoformat(t["occurred_at"])
    new = {**t, "record_id": a.record_id, "equipment_id": a.equipment, "occurred_at": shift(t["occurred_at"], d), "closed_at": shift(t["closed_at"], d),
           "steps": [{**s, "at": shift(s["at"], d)} if s.get("at") else s for s in t["steps"]],
           "approval": {**t["approval"], "at": shift(t["approval"]["at"], d)} if (t.get("approval") or {}).get("at") else t.get("approval"),
           "provenance": "demo_d570", "narrative": f"[시연 D-570] {a.equipment} · {a.days_ago}일 전 · 절 {a.section} 정비(본 {t['record_id']})"}
    Path(a.out).write_text("".join(l + "\n" for l in lines) + json.dumps(new, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "record_id": a.record_id, "equipment": a.equipment, "occurred_at": new["occurred_at"], "template": t["record_id"],
                      "lines": len(lines) + 1, "window": [lo.isoformat(timespec="minutes"), at.isoformat(timespec="minutes")], "other_equipment_in_window": 0},
                     ensure_ascii=False))


EXAMPLE = "사전 점검 예시"
PATTERN_GROUP = {"A": "CL", "B": "TW", "C": "PW"}   # Pack kg-test/graph-v3/precheck_examples.py PLAN의 앞 원인 증상(CL-1 · TW-1 · PW-3)


def examples(a):
    src, out = Path(a.src), Path(a.out)
    if src.resolve() == out.resolve():
        raise SystemExit("멈춤: --out이 --in과 같음(원래 파일은 그대로 둠)")
    keep = [k.strip() for k in a.keep.split(",") if k.strip()]
    by_file = keep == ["F"]   # D-656 펌웨어 F: 예시 파일(F만)의 번호만 남김 · 증상 코드로 가르지 않음(이어짐 줄 CL이 A와 겹침)
    if by_file and not a.app_examples:
        raise SystemExit("멈춤: --keep F는 --app-examples(F 예시 파일)와 함께")
    if not by_file and (not keep or set(keep) - set(PATTERN_GROUP)):
        raise SystemExit(f"멈춤: --keep은 {','.join(PATTERN_GROUP)} 중에서(쉼표) 또는 F 하나")
    pattern = {v: k for k, v in PATTERN_GROUP.items()}
    lines = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    app, from_lines, twice = {}, [], set()
    for f, is_line in [(f, True) for f in a.line or []] + ([(a.app_examples, False)] if a.app_examples else []):
        for l in Path(f).read_text(encoding="utf-8").splitlines():
            if l.strip():
                g = json.loads(l)
                twice |= {g["record_id"]} & set(app)
                app[g["record_id"]] = g
                from_lines += [g["record_id"]] if is_line else []
    if twice:
        print(json.dumps({"stop": "앱 파일에 같은 번호가 두 번", "record_id": sorted(twice)}, ensure_ascii=False))
        sys.exit(2)
    have = {json.loads(l)["record_id"] for l in lines}
    ex_file = {k for k, v in app.items() if k not in from_lines}
    if by_file:
        odd = sorted(k for k in ex_file if app[k].get("provenance") != EXAMPLE)
        if odd:
            print(json.dumps({"stop": "F 예시 파일에 예시 아닌 줄", "record_id": odd}, ensure_ascii=False))
            sys.exit(2)
        from_lines += sorted(ex_file - have)   # F 예시 새 번호는 덧붙임(바탕 끝 · 번호 차례)
    missing = sorted(k for k in set(app) - have if k not in from_lines)   # 시연 줄 파일의 새 번호는 덧붙임 · 예시 새 번호는 멈춤
    if missing:
        print(json.dumps({"stop": "앱 예시 번호가 이력에 없음", "record_id": missing}, ensure_ascii=False))
        sys.exit(2)
    appended = [k for k in from_lines if k not in have]
    kept, left, other, body = {}, {}, 0, []
    for l in lines:
        r = json.loads(l)
        if r.get("provenance") != EXAMPLE:
            other += 1
            body.append(json.dumps(app[r["record_id"]], ensure_ascii=False) if r["record_id"] in app else l)
            continue
        code = (r.get("symptom_group") or {}).get("code")
        if by_file and r["record_id"] in ex_file:
            kept["F"] = kept.get("F", 0) + 1
            body.append(json.dumps(app[r["record_id"]], ensure_ascii=False))
            continue
        if code not in pattern:
            print(json.dumps({"stop": "모르는 예시 증상 코드", "record_id": r.get("record_id"), "code": code}, ensure_ascii=False))
            sys.exit(2)
        k = pattern[code]
        if k in keep and not by_file:
            kept[k] = kept.get(k, 0) + 1
            body.append(json.dumps(app[r["record_id"]], ensure_ascii=False) if r["record_id"] in app else l)
        else:
            left[k] = left.get(k, 0) + 1
    if set(keep) - set(kept) and not (by_file and any(k in ex_file for k in appended)):
        print(json.dumps({"stop": "남길 패턴에 예시 줄이 없음", "patterns": sorted(set(keep) - set(kept))}, ensure_ascii=False))
        sys.exit(2)
    body += [json.dumps(app[k], ensure_ascii=False) for k in appended]
    if by_file:
        kept["F"] = kept.get("F", 0) + sum(1 for k in appended if k in ex_file)
    out.write_text("".join(l + "\n" for l in body), encoding="utf-8")
    print(json.dumps({"out": str(out), "kept": dict(sorted(kept.items())), "left_out": dict(sorted(left.items())), "other": other,
                      "lines": len(body), "lines_from_app": {k: v.get("occurred_at") for k, v in sorted(app.items()) if v.get("provenance") != EXAMPLE},
                      "examples_from_app": sum(1 for v in app.values() if v.get("provenance") == EXAMPLE), "appended": appended}, ensure_ascii=False))


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def clone(a):
    global PACK
    PACK = _pack_src()
    dest = Path(a.dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", "--no-checkout", str(PACK), str(dest))
    else:
        git("fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*", "--tags", cwd=dest)
    c = None
    for r in (f"origin/{a.ref}", a.ref):         # 가지 이름이면 공용 저장소의 그 가지 · 아니면 커밋 · 태그
        p = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{r}^{{commit}}"], cwd=dest, capture_output=True, text=True)
        if p.returncode == 0:
            c = p.stdout.strip()
            break
    if not c:
        raise SystemExit(f"멈춤: {a.ref}를 찾지 못함")
    git("checkout", "-q", "-B", "demo", c, cwd=dest)
    git("tag", "-f", a.tag, c, cwd=dest)
    venv = dest / ".venv"
    if not venv.exists():
        venv.symlink_to(PACK / ".venv")   # Pack 실행 · 관문은 공용 .venv(읽기)
    ingest = dest.parent / "agent-pack-runtime-ingest"   # Pack 실행 release_roots 상대 자리(../../../agent-pack-runtime-ingest) · 벡터 DB 읽기만
    if not ingest.exists():
        ingest.symlink_to(PACK.parent / "agent-pack-runtime-ingest")
    print(json.dumps({"dest": str(dest), "commit": c[:12], "tag": a.tag}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("history")
    for k, v in (("--base", None), ("--out", None), ("--at", None)):
        h.add_argument(k, required=True)
    h.add_argument("--equipment", default="M-01")
    h.add_argument("--section", default="4.3.2")
    h.add_argument("--days-ago", type=int, default=3)
    h.add_argument("--days", type=int, default=7)
    h.add_argument("--record-id", default="H-D570")
    h.add_argument("--line", help="앱 python -m c1a15.demo_history add가 찍은 이력 줄(JSON) 파일 — 주면 이 줄을 그대로 붙임(번호 · 설비 · 시각은 앱 값)")
    e = sub.add_parser("examples")
    e.add_argument("--in", dest="src", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--keep", default="A", help="남길 사전 점검 예시 패턴(쉼표) — 기본 A · F = --app-examples 파일의 번호만(D-656 펌웨어)")
    e.add_argument("--line", action="append", help="앱이 첫 상태 때 찍은 시연 줄 파일(같은 번호 줄을 바꿈 · 여러 번)")
    e.add_argument("--app-examples", help="앱이 첫 상태 때 precheck-add로 넣은 예시 파일(같은 번호 예시 줄을 바꿈)")
    c = sub.add_parser("clone")
    c.add_argument("--dest", required=True)
    c.add_argument("--ref", required=True)
    c.add_argument("--tag", default="d570-g0")
    a = ap.parse_args()
    {"history": history, "examples": examples, "clone": clone}[a.cmd](a)


if __name__ == "__main__":
    main()
