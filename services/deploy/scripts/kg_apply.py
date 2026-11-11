#!/usr/bin/env python3
"""승인마다 바로 그래프 반영(방아쇠 · D-546 K6 이어서 · dev-2) — 주기 0, 앱이 승인 끝에 받은 편지를 쓴 뒤 이 명령 한 줄을 부릅니다.
flock(한 번에 하나) → 새 편지(done 표시 없는 것) → 추출기(예외 기록 든 편지만 · 입력 = 받은 편지 · D-546 덧 10) → 예외 줄 모음
→ 적재기(처음 이력 + 받은 편지함 + 예외 줄 → 새 layer) → 지금 도는 그래프와 같으면 끝 → packrt write_file(templates/graph/current.json)
→ Pack 저장소에 커밋 · 태그 g<n> → graph_swap.swap(설정 두 칸 · check · 그 서버만 다시 띄움) → done 표시.
어디서 멈춰도 설정 · 서버는 옛 것 그대로입니다. 적재기가 뺀 편지는 state/rejected.jsonl에 이름 · 까닭 한 줄(알리는 자리는 뒤 거리).

사용(cwd = 이 저장소): scripts/py scripts/kg_apply.py
환경(자리는 모두 여기로만 · 비밀 값 0):
  AD_CONFIG · AD_PORT · AD_SERVE_LOG   배포 설정 · 포트 · 서버 로그(graph_swap · autodeploy와 같음) — Pack 저장소 = 설정 pack.repo
  KG_INBOX     받은 편지함 폴더(승인 한 건 = kg_record 파일 하나)
  KG_STATE     작업 폴더(lock · done/ · nodes.json · current.json · rejected.jsonl · kg-apply.log)
  KG_HISTORY   처음 이력 jsonl(앱 data/history/maintenance_history.jsonl)
  KG_EXGRAPH   추출기 exgraph.py(LLMOps git archive 사본) · KG_ANSWERS 가짜 답 JSON
  KG_KEY_FILE  있으면 실제 추출(exgraph --real): 키 줄이 든 env 파일 경로(값은 추출기만 메모리로 읽음 · 이 명령은 열지 않음 · 출력 0)
               KG_KEY_NAME(기본 OPENAI_API_KEY) · KG_MODEL(기본 gpt-4.1-mini) · KG_CAP(이 실행 상한 USD · 기본 0.05)
               · KG_BASE_URL(provider /v1 · 기본 https://api.openai.com/v1). 없으면 가짜 답(둘 다 없으면 예외 기록 편지에서 멈춤)
               상한을 넘을 호출은 추출기가 부르기 전에 멈추고, 그 편지는 done 표시 없이 남음. provider 오류는 code · where · HTTP만
  KG_LOADER_PY 적재기 파이썬(neo4j 드라이버 든 것) · KG_LOADER Pack 안 적재기(기본 kg-test/graph-v3/loader.py)
               적재기에는 지금 도는 태그의 그래프를 --base로 줌(예외 줄 이어받기 · Pack bef4c68~)
  KGDB_URI · KGDB_ENV_FILE   그래프 DB(사본) — 적재기가 그대로 읽음
  KG_LEDGER_WAIT 초(D-617 · 없으면 0): applied 신호 → 장부 줄 기다림 → 바꿔 띄움 차례로. 0이면 바꿔 띄운 뒤 신호(옛 차례)
  KG_GATE      예외 관문(D-554 ①) 명령 한 줄(LLMOps) — 끝에 --input <kg-gate/input@1 파일>을 붙여 부름 · 출력 마지막 줄 kg-gate/result@1.
               통과 → 배포 · 실패 + KG 후보(관계 조건 narrow · widen · off) → 고쳐 다시 적재 · 점검, 최대 KG_GATE_FIXES(기본 3)번
               · 그래도 실패 · 후보 없음 · 관문 못 돎 · 관문 cand_revision ≠ 배포 셈 → 보류(배포 0 · done 표시 'held' · state/held/<편지>.json · 이번에 쌓인 관계 되돌림).
               없으면 관문 없이 지금처럼 바로 배포(옛 사본 그대로). KG_GATE_FIXES=0 = 1단계(관문 한 번 · 실패면 고침 0 · 바로 보류 + 신호).
               관문은 새 예외가 든 승인 건이 있을 때만(일반 이력 · '같음'만이면 관문 0 · 바로 배포 · D-554 2번 · D-558 6).
               items == [](AI 나눔이 짝을 못 냄)인 새 예외 편지는 추출 · 관문 0 · 그 편지만 보류(까닭 not_split) · 나머지 편지는 이어서.
               관문이 끝내 실패해도 보류는 새 예외 편지만 — 같은 차례의 일반 이력 편지는 이번 예외 관계 없이 다시 적재해 배포(D-558 T4).
               item 필수 = 점검 what · 조치 what(빈 글 아님) · 방법 · 기준은 빈 글 됨(Pack 43025a5).
               같은 고침(또는 바뀜 없는 고침)으로 이미 시험한 그래프가 되면 다시 시험하지 않고 보류(D-561 ②).
  KG_KEEP_LAYERS 그래프 DB에 남길 앞 벌 수(기본 1) — 처리 끝에 이 kg_apply가 만든 꼬리표(layers.json) 중 지금 도는 것 + 앞 n벌 밖을 지움(D-561 ①).
  예외 칸(Pack 약속 v10 · D-566): exception_record.exception = {topic, checks[], actions[]}(예외 하나 · 적은 순서) · exception_review = {decision, match}(한 번)
               '아님'(또는 review 없음)이면 예외 통째로 추출기 · 관문에 · '같음'이면 그래프 0 · 점검 0 또는 조치 0 = 나누지 못함(held not_split).
               같음 + 고침/보탬(review.edit {checks, actions} · match.kind 예외)이면 kind same_edit로 추출기 · 관문에(대상 예외 바꿔 끼움 · same-edit.md).
  예외 칸(Pack 약속 v9 4 · 5절 · 그대로 받음): 편지 exception_record에 items(AI 나눔 = [{check, action}] · 점검 1 + 조치 1 × n)가 있으면
               exception_review([{item, decision same | not}])로 '아님'(= new_exception)만 추출기 · 관문에 넘김('같음' = missed는 그래프 0).
               exception_review가 없으면 items 모두 새 예외. item 꼴(fields-gate 1절)이 틀리면 부르기 전에 멈춤(done 없이 남음).
               items 없는 옛 꼴(v8 · check/action 한 벌 · 글 action)은 그대로.
  KG_SIGNAL_DIR 신호 폴더(LLMOps 알림용 · 앱은 읽지 않음) — 승인 건마다 <report_id>.json(마지막 상태) + events.jsonl(kg-apply/status@1:
               checking · applied · same · held · failed). 꼴: orchestration handoff/d554/gate-signal.md
  KG_LETTERS   apply(기본) | off — off면 승인 기록 파일을 이 명령이 반영하지 않음(D-570 시연: LLMOps가 KG 반영 대기 폴더를 로그로 보고 관문 뒤
               후보 그래프 파일로 반영 요청 · 같은 예외를 두 길이 반영하지 않게). 폴더는 적재기 이력(--inbox)으로는 그대로 읽힘.
  KG_CANDIDATES 후보 그래프 폴더(LLMOps 자동 개선 · 겹침 바꿈/합침이 씀 · 이 명령은 읽기만) — 꼴: orchestration handoff/d560/candidate-graph-inlet.md.
               승인 기록 파일 처리 뒤 같은 잠금 안에서 후보 파일 하나씩(이름 차례 · 처리 표시 KG_STATE/done-candidates/<id>):
               kg-candidate@1 꼴 틀림 → failed(처리 표시 0 · 같은 파일 알림은 한 번) · base.revision ≠ 지금 도는 revision → held base_moved
               · rows(Pack 줄 꼴 그대로)를 nodes.json에 id로 합쳐 적재기에(추출기 0) → 적재기가 후보 줄을 빼면 held rows_rejected
               → 까닭 ↔ 줄 뜻이 안 맞으면 held bad_candidate(관문 0 · 신호 checks.inlet = 까닭 코드 · eval T8 inlet_errors와 같은 규칙)
               → 관문(letters [] · candidate 칸 · 같은 고침 3번) → 바꿔 끼움 · 신호 report_id = candidate_id + candidate 칸.
               반영 뒤 후보가 건드린 id의 nodes.json 줄은 적재기가 낸 release 줄로 바꿈(다음 적재에서 옛 줄 · 고침 표시가 되풀이되지 않게)."""
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_swap as S  # noqa: E402

A = S.A


class ApplyError(RuntimeError):
    pass


class RevisionMismatch(Exception):
    """관문(Pack tools/graph_gate.py)이 낸 후보 revision ≠ 배포가 커밋할 revision — args = (관문 값, 배포 셈)."""


class NothingToAdd(Exception):
    """추출기가 '더할 것 없음'(모든 예외 기록이 이미 그래프에 든 기록 · already_in)을 냄 — 실패가 아님. args[0] = 그 기록 번호들."""


def env_path(name, default=None):
    v = os.environ.get(name) or default
    return Path(v) if v else None


INBOX, STATE = env_path("KG_INBOX"), env_path("KG_STATE")


def log(*a):
    line = f"{datetime.now():%H:%M:%S} pid {os.getpid()} " + " ".join(map(str, a))
    print(line, flush=True)
    with open(STATE / "kg-apply.log", "a") as f:
        f.write(line + "\n")


def git(*a):
    return subprocess.run(["git", "-C", str(S.pack_repo()), *a], capture_output=True, text=True, check=True).stdout.strip()


def running():
    """지금 설정의 태그 · revision · 그래프 layer."""
    tag = A.cfg_state((A.ROOT / A.CFG).read_text())[0]
    _, revision, layer = S.rev(tag)
    return tag, revision, layer


def extractor_args():
    """(exgraph 추출 칸, 'real' | 'fake'). KG_KEY_FILE이 있으면 실제, 없으면 가짜 답. 키 값은 열지 않음(경로만 넘김)."""
    key = env_path("KG_KEY_FILE")
    if key:
        if not key.is_file():
            raise ApplyError("KG_KEY_FILE 자리에 파일 없음")
        try:
            cap = float(os.environ.get("KG_CAP") or "0.05")
        except ValueError:
            raise ApplyError("KG_CAP이 숫자가 아님") from None
        if not 0 < cap <= 1:
            raise ApplyError("KG_CAP은 0보다 크고 1 이하(USD)")
        return ["--real", "--key-file", str(key), "--key-name", os.environ.get("KG_KEY_NAME") or "OPENAI_API_KEY",
                "--model", os.environ.get("KG_MODEL") or "gpt-4.1-mini", "--base-url", os.environ.get("KG_BASE_URL") or "https://api.openai.com/v1",
                "--cap", str(cap)], "real"
    answers = env_path("KG_ANSWERS")
    if not answers:
        raise ApplyError("예외 기록 편지인데 KG_ANSWERS · KG_KEY_FILE 없음")
    return ["--answers", str(answers)], "fake"


TEXT = lambda v: isinstance(v, str) and v.strip() != ""
MINUTES = lambda v: v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 1)


def item_problem(it):
    """item 하나(fields-gate 1절 꼴) → 틀린 칸 이름 또는 None. 필수 = 점검 what · 조치 what(빈 글 아님) · 방법 · 기준은 빈 글 됨."""
    if not isinstance(it, dict) or not isinstance(it.get("check"), dict) or not isinstance(it.get("action"), dict):
        return "check · action"
    c, a = it["check"], it["action"]
    if not TEXT(c.get("what")):
        return "check.what"
    for k in ("how", "criteria"):                # 빈 글 됨(Pack 43025a5가 남김 · 정답지 · eval T4) — 있으면 글이어야 함
        if k in c and not isinstance(c[k], str):
            return f"check.{k}"
    if not MINUTES(c.get("minutes")):
        return "check.minutes"
    if not TEXT(a.get("what")):
        return "action.what"
    if not isinstance(a.get("parts", []), list) or not all(TEXT(x) for x in a.get("parts", [])):
        return "action.parts"
    if not MINUTES(a.get("minutes")):
        return "action.minutes"
    return None


LIST_MAX = 10   # 약속 v10 1절: 점검 · 조치 목록 1 ~ 10


def exception_problem(exc):
    """약속 v10 예외 하나 {topic, checks[], actions[]} → 틀린 칸 이름 또는 None(빈 목록은 not_split이 먼저 거름).
    점검 what · 조치 what 필수 · how · criteria 빈 글 됨 · 분 1 이상 정수 | null · 부품 글 목록 · 적은 순서 그대로."""
    if "topic" in exc and not isinstance(exc["topic"], str):
        return "topic"
    for side, need in (("checks", ("how", "criteria")), ("actions", ())):
        rows = exc.get(side)
        if not isinstance(rows, list) or len(rows) > LIST_MAX:
            return side
        for i, r in enumerate(rows):
            if not isinstance(r, dict) or not TEXT(r.get("what")):
                return f"{side}[{i}].what"
            for k in need:
                if k in r and not isinstance(r[k], str):
                    return f"{side}[{i}].{k}"
            if side == "actions" and (not isinstance(r.get("parts", []), list) or not all(TEXT(x) for x in r.get("parts", []))):
                return f"{side}[{i}].parts"
            if not MINUTES(r.get("minutes")):
                return f"{side}[{i}].minutes"
    return None


def exception_v10(l, er):
    """약속 v10(D-566): 예외 하나 · 같음/아님 한 번 → 넘길 exception_record 또는 None('같음' = 그래프 0)."""
    review = er.get("exception_review")
    if review is not None and not (isinstance(review, dict) and review.get("decision") in ("same", "not")):
        raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception_review")
    edit = review.get("edit") if isinstance(review, dict) else None
    if edit is not None:                                     # 같음 + 고침/보탬(same-edit.md · 정답지 30쪽): 대상 예외를 새 목록으로 바꿔 끼움 → 관문 '바뀐 관계'
        m = review.get("match")
        if review["decision"] != "same" or not (isinstance(m, dict) and m.get("kind") == "예외" and TEXT(m.get("no"))) or not isinstance(edit, dict):
            raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception_review.edit")
        for side in ("checks", "actions"):
            if not isinstance(edit.get(side), list) or not edit[side]:
                raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception_review.edit.{side}")
        bad = exception_problem(edit)
        if bad:
            raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception_review.edit.{bad}")
        return {**er, "kind": "same_edit"}                   # 근거 exception · review(match · edit) 그대로 — 줄 만들기는 추출기 몫
    if review is not None and review["decision"] == "same":
        return None                                          # 같음(missed) — 그래프에 더할 예외 0
    exc = er.get("exception")
    if not isinstance(exc, dict):
        raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception")
    bad = exception_problem(exc)
    if bad:
        raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} {bad}")
    out = {k: v for k, v in er.items() if k != "exception_review"}
    return {**out, "kind": "new_exception"}


def exception_of(l):
    """편지 → 추출기 · 관문에 넘길 exception_record 또는 None(넘길 예외 0). 약속 v10(exception 하나) · v9('아님' item만) · 옛 꼴."""
    rec = l.get("record") if isinstance(l.get("record"), dict) else {}
    er = rec.get("exception_record")
    if not er:
        return None
    if isinstance(er, dict) and "exception" in er:
        return exception_v10(l, er)
    if not isinstance(er, dict) or "items" not in er:
        return er                                            # 옛 꼴 그대로
    items = er.get("items")
    if not isinstance(items, list):
        raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} items")
    review = er.get("exception_review")
    if review is None:
        keep = list(range(len(items)))
    else:
        if not isinstance(review, list) or not all(isinstance(r, dict) and r.get("decision") in ("same", "not") and isinstance(r.get("item"), int)
                                                   and 0 <= r["item"] < len(items) for r in review):
            raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} exception_review")
        keep = sorted({r["item"] for r in review if r["decision"] == "not"})
    for i in keep:
        bad = item_problem(items[i])
        if bad:
            raise ApplyError(f"예외 칸 꼴 틀림: {l.get('report_id')} item {i} {bad}")
    if not keep:
        return None                                          # 모두 '같음'(missed) — 그래프에 더할 예외 0
    out = {k: v for k, v in er.items() if k not in ("items", "exception_review")}
    return {**out, "kind": "new_exception", "items": [items[i] for i in keep], "item_index": keep, "item_count": len(items)}   # 원래 item 수(관계 id EX-<기록>(-n) 가림)


def not_split(l):
    """새 예외인데 AI 나눔이 못 냄 — 관문 0 · 바로 보류 + 알림. v9: items == [] · v10: exception null 또는 점검 0 또는 조치 0(한쪽 0 = 나누지 못함 · D-566 덧 1)."""
    rec = l.get("record") if isinstance(l.get("record"), dict) else {}
    er = rec.get("exception_record")
    if not isinstance(er, dict):
        return False
    if "exception" in er:
        review = er.get("exception_review")
        if isinstance(review, dict) and review.get("decision") == "same":
            return False
        exc = er.get("exception")
        return not isinstance(exc, dict) or not exc.get("checks") or not exc.get("actions")
    return er.get("items") == []


def answer_line(row):
    """추출기 답 한 줄(D-597): recent · 근거 기록 · 조건 칸 이름만 — 문턱 값 · 원문 · 키 0. 'AI가 최근 정비로 안 봄'을 로그에서 바로 봄."""
    when, ctx = row.get("when") or {}, row.get("context") or {}
    return (f"추출기 답 {row.get('id')} · 기록 {row.get('records')} · recent {when.get('recent')} · 정비 기록 {ctx.get('recent_record')}"
            f" · 센서 칸 {[s[0] for s in when.get('sensors') or []]}")


def extract(new, tag):
    """예외 기록 든 새 편지 → 추출기 → nodes.json에 더함(같은 id는 한 번). 더한 관계 수.
    추출기가 뺀 기록(이미 든 것 말고 — 상한 · 답 꼴 · 호출 오류)이 있으면 멈춤(그 편지는 done 없이 남음)."""
    items = [{"record_id": l["record"]["record_id"], "report_id": l["report_id"], "approved_at": l["approved_at"], "exception_record": er}
             for _, l in new for er in [exception_of(l)] if er]
    if not items:
        return 0
    args, mode = extractor_args()
    exgraph = env_path("KG_EXGRAPH")
    if not exgraph:
        raise ApplyError("예외 기록 편지인데 KG_EXGRAPH 없음")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "base.json"
        base.write_text(git("show", f"{tag}:pack/{S.GRAPH}"), encoding="utf-8")
        rec, out = Path(d) / "records.json", Path(d) / "nodes.json"
        rec.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        r = A.sh(["python3", "-I", "-B", str(exgraph), "--base", str(base), "--records", str(rec), *args, "--out", str(out)])
        if r.returncode:
            err = next((l for l in reversed(r.stderr.strip().splitlines()) if l.startswith("exgraph:")), "")   # 추출기 글 = code · where · HTTP만
            log("추출기", mode, r.returncode, err[:160])
            raise ApplyError("추출기 실패")
        try:
            rep = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except ValueError:
            rep = {}
        skipped = [x for x in rep.get("skipped") or [] if x.get("code") != "already_in"]
        log("추출기", mode, 0, "· 더함", rep.get("added"), "· 뺌", [(x.get("record_id"), x.get("code")) for x in rep.get("skipped") or []],
            "· 사용", rep.get("usage"))
        if skipped:
            raise ApplyError("상한 — 부르지 않음" if any(x.get("code") == "budget_cap" for x in skipped) else f"추출기가 뺀 기록 {len(skipped)}건")
        if not rep.get("added") and rep.get("skipped"):
            raise NothingToAdd([x.get("record_id") for x in rep["skipped"]])
        if not out.exists():
            return 0
        add = json.loads(out.read_text(encoding="utf-8"))
    for row in add.get("exception_relations", []):
        log(answer_line(row))
    if rep.get("notices"):
        log("추출기 알림", [(x.get("record_id"), x.get("code")) for x in rep["notices"]])
    p = STATE / "nodes.json"
    acc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"sections": [], "exception_relations": []}
    n = 0
    for k, key in (("sections", "no"), ("exception_relations", "id")):
        have = {x[key] for x in acc[k]}
        edits = {x[key]: x for x in add.get(k, []) if is_edit_row(x)}   # 같음 + 고침 줄: 같은 id 옛 줄을 바꿈(건너뛰면 조용히 '바뀜 0')
        new_rows = [x for x in add.get(k, []) if x[key] not in have and x[key] not in edits]
        acc[k] = [edits.pop(x[key]) if x[key] in edits else x for x in acc[k]] + new_rows + list(edits.values())
        EDITED[k] += [x for x in add.get(k, []) if is_edit_row(x)]
        n += len(new_rows) + sum(1 for x in add.get(k, []) if is_edit_row(x)) if k == "exception_relations" else 0
    p.write_text(json.dumps(acc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return n


EDITED = {"sections": [], "exception_relations": []}   # 이번 실행에서 추출기가 낸 고침 줄(반영 뒤 settle로 release 줄로 바꿈)


def is_edit_row(x):
    return "edit_record" in x or x.get("edit") is True


def load(tag, base_text=None):
    """적재기 → {tag, release_id, rejected, …}. 기준 = 지금 도는 tag의 그래프(예외 줄 이어받기) — 관문 후보가 도는 관계를 고쳤으면
    그 고친 그래프 글(base_text). 뺀 편지는 rejected.jsonl에 한 줄씩."""
    loader = os.environ.get("KG_LOADER") or "kg-test/graph-v3/loader.py"
    base = STATE / "base.json"
    base.write_text((base_text if base_text is not None else git("show", f"{tag}:pack/{S.GRAPH}")) + "\n", encoding="utf-8")
    args = [os.environ.get("KG_LOADER_PY") or "python3", "-B", loader, "--history", str(env_path("KG_HISTORY")), "--inbox", str(INBOX),
            "--base", str(base), "--out", str(STATE / "current.json")]
    if (STATE / "nodes.json").exists():
        args += ["--nodes", str(STATE / "nodes.json")]
    r = A.sh(args, cwd=S.pack_repo())
    if r.returncode:
        log("적재기 실패", r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:160])
        raise ApplyError("적재기 실패")
    res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    with open(STATE / "rejected.jsonl", "a") as f:
        for x in res.get("rejected") or []:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "letter": x} if not isinstance(x, dict) else
                               {"at": datetime.now().isoformat(timespec="seconds"), **x}, ensure_ascii=False) + "\n")
    log("적재기", res.get("tag"), res.get("release_id"), "· 이력", res.get("records"), "· 뺀 편지", len(res.get("rejected") or []),
        "· 뺀 줄", len(res.get("rejected_rows") or []))
    return res


def new_revision(tag_from, fixed=None, expect=None):
    """tag_from 위에 current.json 한 파일만 바꾼 커밋(packrt write_file) → 새 태그 g<n>."""
    from packrt.ir.patch import apply_patch
    text = (STATE / "current.json").read_text(encoding="utf-8")
    repo = S.pack_repo()
    git("checkout", "-q", "-B", "kg-apply", tag_from)
    with tempfile.TemporaryDirectory() as d:
        r = apply_patch(repo / "pack", [{"op": "write_file", "path": S.GRAPH, "text": text}], out_dir=Path(d) / "out")
        if r.errors:
            raise ApplyError(f"packrt patch 거부 {len(r.errors)}건")
        if expect and r.revision != expect:   # 관문이 점검한 그래프와 배포할 그래프가 다름 — 커밋 · 태그 전에 멈춤
            git("checkout", "-q", "--", ".")
            raise RevisionMismatch(expect, r.revision)
        (repo / "pack" / S.GRAPH).write_bytes((Path(d) / "out" / S.GRAPH).read_bytes())
    changed = [l[3:] for l in A.sh(["git", "-C", str(repo), "status", "--porcelain"]).stdout.splitlines()]
    if changed != [f"pack/{S.GRAPH}"]:
        git("checkout", "-q", "--", ".")
        raise ApplyError(f"바뀐 파일이 current.json 하나가 아님({len(changed)})")
    n = 1 + max([int(t[1:]) for t in git("tag", "--list", "g*").split() if t[1:].isdigit()] or [0])
    note = f" · 관문 고침 {len(fixed)}: " + ", ".join(f"{f['relation_id']} {f['change']}" for f in fixed) if fixed else ""
    git("-c", "user.name=kg-apply", "-c", "user.email=kg-apply@local", "commit", "-q", "-am", f"graph {json.loads(text)['layer']} (kg-apply · packrt write_file{note})")
    git("tag", f"g{n}")
    log("새 revision", f"g{n}", "· packrt 예상", r.revision[7:15])
    return f"g{n}"


CHANGES = ("narrow", "widen", "off")


def signal(new, status, **kw):
    """신호(KG_SIGNAL_DIR): 승인 건마다 마지막 상태 한 장(임시 → 이름 바꿈) + events.jsonl 한 줄. 폴더 없으면 0."""
    d = env_path("KG_SIGNAL_DIR")
    if not d:
        return
    d.mkdir(parents=True, exist_ok=True)
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    for p, l in new:
        rec = l.get("record") if isinstance(l.get("record"), dict) else {}
        if is_cand(p):                       # 후보 그래프 파일(candidate-graph-inlet.md 5절): report_id = candidate_id · candidate 칸
            row = {"format": "kg-apply/status@1", "report_id": p.stem, "record_id": None, "status": status, "at": at, **kw,
                   "candidate": {k: l.get(k) for k in ("reason", "source", "made_by", "decided_by")}}
            rid = p.stem
        else:
            rid = l.get("report_id") or p.stem
            row = {"format": "kg-apply/status@1", "report_id": rid, "record_id": rec.get("record_id"), "status": status, "at": at, **kw}
        tmp = d / f".{rid}.json.tmp"
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, d / f"{rid}.json")
        with open(d / "events.jsonl", "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def relation_ids(text):
    try:
        return [r["id"] for r in json.loads(text).get("exception_relations") or []]
    except (ValueError, AttributeError, KeyError, TypeError):
        return []


def gate_call(rnd, new, tag, new_rel, previous, cand=None):
    """관문 한 번 → 결과 dict. 관문이 못 돌면(종료 0 아님 · 꼴 틀림) None."""
    cmd = shlex.split(os.environ["KG_GATE"])
    gdir = STATE / "gate"
    gdir.mkdir(exist_ok=True)
    inp = {"format": "kg-gate/input@1", "round": rnd, "max_rounds": 1 + fixes_max(), "pack_repo": str(S.pack_repo()), "running_tag": tag,
           "running_graph": str(STATE / "base.json"), "new_graph": str(STATE / "current.json"),
           "letters": [{"report_id": l.get("report_id") or p.stem, "record_id": l["record"].get("record_id"),
                        "approved_at": l.get("approved_at"), "exception_record": exception_of(l),
                        **{k: l["record"][k] for k in ("session_id", "human_check") if k in l["record"]}}
                       for p, l in new if isinstance(l.get("record"), dict)],
           "new_relations": new_rel, "previous": previous}
    if cand is not None:                     # 후보 그래프(letters [] · candidate-graph-inlet.md 4절)
        inp["candidate"] = {k: cand.get(k) for k in ("candidate_id", "reason", "source", "made_by", "decided_by", "rows")}   # rows = 후보 파일 그대로(kggate가 옮겨 적기 셈 · llmops-2 183)
    f = gdir / f"input-{rnd}.json"
    f.write_text(json.dumps(inp, ensure_ascii=False, indent=1), encoding="utf-8")
    r = A.sh([*cmd, "--input", str(f)])
    try:
        out = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        out = None
    if r.returncode or not isinstance(out, dict) or out.get("format") != "kg-gate/result@1" or not isinstance(out.get("pass"), bool):
        log("관문 못 돎", rnd, r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:160])
        return None
    (gdir / f"result-{rnd}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log("관문", rnd, "통과" if out["pass"] else "실패", "· 까닭", out.get("reason"), "· 후보", (out.get("candidate") or {}).get("change"))
    return out


def fixes_max():
    try:
        return max(0, int(os.environ.get("KG_GATE_FIXES") or "3"))
    except ValueError:
        return 3


def apply_candidate(c, base_text):
    """KG 후보 하나를 관계 조건에만 적용 → (고친 base 글 또는 그대로, 기록 한 줄). 꼴이 틀리거나 관계가 없으면 None."""
    if not isinstance(c, dict) or c.get("change") not in CHANGES or not isinstance(c.get("relation_id"), str):
        return None
    if c["change"] != "off" and not (isinstance(c.get("when"), dict) and isinstance(c["when"].get("sensors"), list) and isinstance(c.get("where"), str)):
        return None

    def edit(rows):
        for i, r in enumerate(rows):
            if r.get("id") == c["relation_id"]:
                if c["change"] == "off":
                    del rows[i]
                else:
                    rows[i] = {**r, "when": c["when"], "where": c["where"]}
                return True
        return False

    p = STATE / "nodes.json"
    acc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if acc and edit(acc["exception_relations"]):
        p.write_text(json.dumps(acc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    else:
        base = json.loads(base_text)
        if not edit(base.setdefault("exception_relations", [])):
            return None
        base_text = json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True)
    return base_text, {"relation_id": c["relation_id"], "change": c["change"], "where": c.get("where") if c["change"] != "off" else None}


def gate(new, tag, nodes_before, res, cand=None, new_rel=None):
    """관문 · 고침 → (결과 load 꼴, 고침 목록, 차례 수) 또는 보류면 HoldError. 후보 그래프면 cand · new_rel(후보가 건드린 관계)을 받음."""
    base_text = git("show", f"{tag}:pack/{S.GRAPH}")
    if new_rel is None:
        after = (STATE / "nodes.json").read_text(encoding="utf-8") if (STATE / "nodes.json").exists() else ""
        new_rel = [i for i in relation_ids(after) if i not in set(relation_ids(nodes_before or "{}"))]
    fixed, previous, custom_base = [], [], None
    seen = {state_print(custom_base)}            # 관문이 이미 본 그래프 상태(nodes.json + 고친 base)
    for rnd in range(1, 2 + fixes_max()):
        signal(new, "checking", round=rnd)
        out = gate_call(rnd, new, tag, new_rel, previous, cand)
        if out is None:
            raise HoldError("gate_error", rnd, fixed, {})
        if out["pass"]:
            GATE_CHECKS.clear()
            GATE_CHECKS.update(out.get("checks") or {})   # 통과한 관문 결과 — applied 신호 checks(LLMOps 버전 표 '관문' 칸)
            return res, fixed, rnd, out.get("cand_revision")
        if rnd > fixes_max() or not out.get("candidate"):
            raise HoldError(out.get("reason") or "gate_failed", rnd, fixed, out.get("checks") or {})
        got = apply_candidate(out["candidate"], custom_base or base_text)
        if got is None:
            raise HoldError("bad_candidate", rnd, fixed, out.get("checks") or {})
        new_base, line = got
        custom_base = new_base if new_base != (custom_base or base_text) else custom_base
        if state_print(custom_base) in seen:     # 같은 고침 · 바뀜 없는 고침 — 같은 그래프를 다시 시험하지 않음(D-561 ② · 11-04 EX-R-0051 세 번)
            log("같은 고침 · 바뀜 없음", line, "— 더 고치지 않음 · 보류")
            raise HoldError(out.get("reason") or "gate_failed", rnd, fixed, out.get("checks") or {})
        seen.add(state_print(custom_base))
        fixed.append(line)
        previous.append({"round": rnd, "pass": False, "reason": out.get("reason"), "applied_candidate": out["candidate"]})
        log("관문 후보 적용", line)
        res = note_layer(load(tag, base_text=custom_base))


GATE_CHECKS = {}   # 이번 처리에서 통과한 관문 결과(관문 안 돌면 빈 칸)


def state_print(custom_base):
    p = STATE / "nodes.json"
    import hashlib
    return hashlib.sha256(((p.read_text(encoding="utf-8") if p.exists() else "") + "\0" + (custom_base or "")).encode()).hexdigest()


def note_layer(res):
    """적재기가 만든 꼬리표를 작업 폴더 layers.json에 적음(이 kg_apply가 만든 것만 나중에 지움 · 같은 그래프 DB의 다른 사본 꼬리표는 손 0)."""
    t = (res or {}).get("tag")
    if t:
        p = STATE / "layers.json"
        have = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if t not in have:
            p.write_text(json.dumps(have + [t], ensure_ascii=False), encoding="utf-8")
    return res


def keep_layers():
    """남길 꼬리표 = 지금 도는 태그의 layer + 그 앞 g 태그 n벌(KG_KEEP_LAYERS · 기본 1). 되돌리기는 Pack 저장소 g 태그 · 그래프 파일로 하므로 DB 꼬리표가 없어도 됨."""
    try:
        n = max(0, int(os.environ.get("KG_KEEP_LAYERS") or "1"))
    except ValueError:
        n = 1
    tag, _, layer = running()
    nums = sorted(int(t[1:]) for t in git("tag", "--list", "g*").split() if t[1:].isdigit())
    now = int(tag[1:]) if tag[1:].isdigit() else None
    keep = {layer}
    for k in ([x for x in nums if now is not None and x < now][-n:] if n else []):
        try:
            keep.add(json.loads(git("show", f"g{k}:pack/{S.GRAPH}"))["layer"])
        except (subprocess.CalledProcessError, ValueError, KeyError):
            pass
    return keep


def drop_layers(tags):
    """그래프 DB에서 꼬리표 지움(Pack build_v3 clear와 같은 줄) — 적재기 파이썬 · Pack 연결(KGDB_URI · KGDB_ENV_FILE)로. 돌려줌 {꼬리표: 지운 노드 수}."""
    ldir = (S.pack_repo() / (os.environ.get("KG_LOADER") or "kg-test/graph-v3/loader.py")).parent
    code = ("import json, sys; sys.path[:0] = [sys.argv[2], sys.argv[3]]; from conn import driver; d = driver(); out = {}\n"
            "with d.session() as s:\n"
            "    for t in json.loads(sys.argv[1]):\n"
            "        out[t] = s.run('MATCH (n {layer: $v}) RETURN count(n) AS c', v=t).single()['c']\n"
            "        s.run('MATCH (n {layer: $v}) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS', v=t).consume()\n"
            "d.close(); print(json.dumps(out))")
    r = A.sh([os.environ.get("KG_LOADER_PY") or "python3", "-B", "-c", code, json.dumps(tags), str(ldir), str(ldir.parent / "graph")], cwd=S.pack_repo())
    if r.returncode:
        log("옛 꼬리표 지우기 실패", r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:160])
        return {}
    return json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])


def link_layers(before, after, at):
    """반영 전 그래프 남김(D-592 ①): Neo4j에 (새 Layer)-[:PREVIOUS]->(바로 앞 Layer) 선 · 새 Layer swapped_at — 적재기 파이썬 · Pack 연결로(drop_layers와 같은 길).
    돌려줌: 선을 이었는지(두 Layer가 다 있어야 · 앞 판은 KG_KEEP_LAYERS로 남음)."""
    ldir = (S.pack_repo() / (os.environ.get("KG_LOADER") or "kg-test/graph-v3/loader.py")).parent
    code = ("import json, sys; sys.path[:0] = [sys.argv[4], sys.argv[5]]; from conn import driver; d = driver()\n"
            "with d.session() as s:\n"
            "    n = s.run('MATCH (a:Layer {layer: $a}), (b:Layer {layer: $b}) MERGE (a)-[r:PREVIOUS]->(b) SET a.swapped_at = $t RETURN count(r) AS c',"
            " a=sys.argv[3], b=sys.argv[2], t=sys.argv[1]).single()['c']\n"
            "d.close(); print(json.dumps({'linked': n}))")
    r = A.sh([os.environ.get("KG_LOADER_PY") or "python3", "-B", "-c", code, at, before, after, str(ldir), str(ldir.parent / "graph")], cwd=S.pack_repo())
    if r.returncode:
        log("앞 그래프 선 잇기 실패", r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:160])
        return False
    return json.loads((r.stdout.strip().splitlines() or ["{}"])[-1]).get("linked", 0) > 0


def note_pair(tag, layer, g, new_layer):
    """바꿔 띄운 뒤: 앞 · 새 그래프 짝을 Neo4j(선) · 작업 폴더 layer-pairs.jsonl에 — Neo4j가 안 되어도 반영 결과 · 신호 그대로."""
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        ok = bool(link_layers(layer, new_layer, at)) if layer and new_layer and layer != new_layer else False
    except Exception as e:  # noqa: BLE001
        log("앞 그래프 선 잇기 건너뜀", type(e).__name__)
        ok = False
    with open(STATE / "layer-pairs.jsonl", "a") as f:
        f.write(json.dumps({"at": at, "before": {"tag": tag, "layer": layer}, "after": {"tag": g, "layer": new_layer}, "neo4j": ok}, ensure_ascii=False) + "\n")


def tidy_layers():
    """처리가 끝난 뒤(잠금 안 · 관문 시험 · 보류 처리 끝남): 이 kg_apply가 만든 꼬리표 중 남길 것 밖을 지움 — 지우기 전 목록을 기록에."""
    p = STATE / "layers.json"
    have = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    keep = keep_layers()
    drop = [t for t in have if t not in keep]
    if not drop:
        return
    log("옛 꼬리표 지울 목록", drop, "· 남김", sorted(keep))
    out = drop_layers(drop)
    gone = [t for t in drop if t in out]
    with open(STATE / "layers-dropped.jsonl", "a") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "dropped": out, "kept": sorted(keep)}, ensure_ascii=False) + "\n")
    p.write_text(json.dumps([t for t in have if t not in gone], ensure_ascii=False), encoding="utf-8")
    log("옛 꼬리표 지움", len(gone), "· 노드", sum(out.values()))


class HoldError(Exception):
    """보류: args = (까닭, 차례 수, 고침 목록, 관문 마지막 checks)."""


def hold(new, tag, nodes_before, e):
    reason, rounds, fixed, checks = e.args
    p = STATE / "nodes.json"
    if nodes_before is None:
        p.unlink(missing_ok=True)
    else:
        p.write_text(nodes_before, encoding="utf-8")
    (STATE / "held").mkdir(exist_ok=True)
    at = datetime.now().isoformat(timespec="seconds")
    for q, _ in new:
        (STATE / "held" / f"{q.stem}.json").write_text(json.dumps({"at": at, "reason": reason, "rounds": rounds, "fixed": fixed}, ensure_ascii=False))
        mark(q).parent.mkdir(exist_ok=True)
        mark(q).write_text(f"held {at}")
    signal(new, "held", graph={"running_tag": tag, "new_tag": None}, rounds=rounds, fixed=fixed, reason=reason, checks=checks)
    log("보류", reason, "· 차례", rounds, "· 고침", len(fixed), "— 배포 0 · 그래프 그대로 · 쌓인 관계 되돌림", [q.stem for q, _ in new])
    return "held"


CURRENT = []   # 이번 실행의 새 편지(멈춤 신호용)


def apply_once():
    """잠금 안에서 한 번. 돌려줌: 'none' · 'same' · 바꾼 태그."""
    (STATE / "done").mkdir(parents=True, exist_ok=True)
    new = []
    for p in sorted(INBOX.glob("*.json")):
        if (STATE / "done" / p.stem).exists():
            continue
        try:
            new.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except ValueError:
            new.append((p, {}))
    if not new:
        log("새 편지 0 — 끝(다시 띄움 0)")
        return "none"
    log("새 편지", [p.name for p, _ in new])
    CURRENT[:] = new
    for k in EDITED:
        EDITED[k] = []
    tag, _, layer = running()
    nodes_p = STATE / "nodes.json"
    nodes_before = nodes_p.read_text(encoding="utf-8") if nodes_p.exists() else None
    ns = [x for x in new if not_split(x[1])]
    if ns:                                   # 나누지 못한 새 예외 — 추출 · 관문 0, 그 편지만 보류(나머지 편지는 이어서)
        hold(ns, tag, nodes_before, HoldError("not_split", 0, [], {}))
        new = [x for x in new if not not_split(x[1])]
        CURRENT[:] = new
        if not new:
            return "held"
    try:
        extract(new, tag)
    except NothingToAdd as e:
        if len(e.args[0]) == len(new):   # 새 편지 모두가 이미 든 예외 기록 — 그래프 바꿈 0 · 다시 띄움 0 · done
            log("더할 것 없음(이미 든 기록", e.args[0], ") — 그래프 바꿈 0 · 다시 띄움 0")
            signal(new, "same", graph={"running_tag": tag, "new_tag": None}, rounds=0, fixed=[], reason="already_in")
            return done(new, "same")
        log("이미 든 기록", e.args[0], "— 다른 편지가 있어 적재기로")
    res = note_layer(load(tag))
    if res.get("tag") == layer:
        log("지금 도는 그래프와 같음", layer, "— 다시 띄움 0")
        signal(new, "same", graph={"running_tag": tag, "new_tag": None}, rounds=0, fixed=[], reason="same_graph")
        return done(new, "same")
    fixed, rounds, expect = [], 0, None
    exc = [p.stem for p, l in new if exception_of(l) is not None]
    if os.environ.get("KG_GATE") and not exc:
        log("새 예외 0(일반 이력 · 같음만) — 관문 0 · 바로 배포")
    if os.environ.get("KG_GATE") and exc:
        try:
            res, fixed, rounds, expect = gate(new, tag, nodes_before, res)
        except HoldError as e:                   # 관문이 끝내 실패 — 빠지는 것은 이번 예외 관계뿐(정답지 27쪽 · eval T4 E7)
            rest = [x for x in new if x[0].stem not in exc]
            hold([x for x in new if x[0].stem in exc], tag, nodes_before, e)   # nodes.json을 그 전으로 · 그 편지만 보류
            if not rest:
                return "held"
            new = rest
            CURRENT[:] = new
            log("같은 차례의 일반 이력 편지", [p.stem for p, _ in new], "— 이번 예외 관계 없이 다시 적재")
            res = note_layer(load(tag))
            if res.get("tag") == layer:
                log("지금 도는 그래프와 같음", layer, "— 다시 띄움 0")
                signal(new, "same", graph={"running_tag": tag, "new_tag": None}, rounds=0, fixed=[], reason="same_graph")
                return done(new, "same")
            fixed, rounds, expect = [], 0, None
    try:
        g = new_revision(tag, fixed=fixed, expect=expect)
    except RevisionMismatch as e:
        log("관문 revision", str(e.args[0])[7:15], "≠ 배포 셈", str(e.args[1])[7:15], "— 커밋 · 배포 0")
        return hold(new, tag, nodes_before, HoldError("revision_mismatch", rounds, fixed, {"cand_revision": e.args[0], "deploy_revision": e.args[1]}))
    said = {"graph": {"running_tag": tag, "new_tag": g}, "rounds": rounds, "fixed": fixed, "reason": None, "checks": dict(GATE_CHECKS) if rounds else {}}
    told = row_first(g, new, **said)
    S.swap(g)
    settle(EDITED)
    note_pair(tag, layer, g, res.get("tag"))
    log(f"바꿔 띄움 {tag} → {g} · layer {layer} → {res.get('tag')}" + (f" · 관문 {rounds}번째 통과 · 고침 {len(fixed)}" if rounds else ""))
    if not told:
        signal(new, "applied", **said)
    return done(new, g)


def row_first(g, new, **said):
    """장부 줄 먼저(D-617): 서버는 장부에 없는 revision의 Run을 거부하고, LLMOps는 applied 신호를 보고 지나갈 때(30초마다) 줄을 씀 — 바꿔 띄운 뒤 신호를 내면
    그 틈의 Run이 실패함. KG_LEDGER_WAIT(초)를 주면 신호 → 줄 기다림 → 바꿔 띄움 차례. 줄이 안 오면 한 줄 남기고 지금처럼 바꿔 띄움. 돌려줌: 신호를 이미 냈는지."""
    wait = float(os.environ.get("KG_LEDGER_WAIT") or 0)
    if wait <= 0:
        return False
    signal(new, "applied", **said)
    revision, t0 = S.rev(g)[1], time.time()
    while time.time() - t0 < wait:
        if A.ledger_row(revision):
            log("장부 줄 먼저", g, f"· {time.time() - t0:.0f}초 기다림")
            return True
        time.sleep(A.POLL)
    log("장부 줄 없음", g, f"· {wait:.0f}초 — 그대로 바꿔 띄움(줄이 생길 때까지 Run 거부)")
    return True


def done(new, out):
    for p, _ in new:
        mark(p).parent.mkdir(exist_ok=True)
        mark(p).write_text(datetime.now().isoformat(timespec="seconds"))
    log("끝 · done", [p.stem for p, _ in new])
    return out


def carry_from(prev):
    """버전 전환 뒤(D-588 C): 새 Pack 버전의 그래프 파일은 Pack 본줄 것이라 시연에서 배운 예외가 빠짐 — 전 태그의 그래프 파일을 지금 도는 태그 위에
    그 파일 하나만 바꾼 커밋(새 g 태그 · packrt write_file)으로 실어 다시 띄움. 같으면 손 0. 신호 report_id = carry-<지금 태그>."""
    tag, _, layer = running()
    try:
        prev_text = git("show", f"{prev}:pack/{S.GRAPH}")
    except subprocess.CalledProcessError:
        raise ApplyError(f"{prev}에 그래프 파일이 없음") from None
    now_text = git("show", f"{tag}:pack/{S.GRAPH}")
    if prev_text == now_text:
        log("옮겨 실을 그래프 같음", prev, "=", tag, "— 다시 띄움 0")
        return "same"
    (STATE / "current.json").write_text(prev_text + "\n", encoding="utf-8")
    g = new_revision(tag)
    entry = [(Path(f"carry-{tag}.json"), {"report_id": f"carry-{tag}"})]
    said = {"graph": {"running_tag": tag, "new_tag": g}, "rounds": 0, "fixed": [], "reason": "carry_on_version_switch", "checks": {}, "carried_from": prev}
    told = row_first(g, entry, **said)
    S.swap(g)
    note_pair(tag, layer, g, json.loads(prev_text).get("layer"))
    if not told:
        signal(entry, "applied", **said)
    log(f"버전 전환 뒤 그래프 옮겨 실음 {prev} → {tag} 위 {g}")
    return g


CAND_FORMAT = "kg-candidate@1"
CAND_ID = re.compile(r"^KGC-[a-z0-9]+-[0-9]{4}-[0-9a-f]{8}$")
REASONS = ("add", "widen", "replace", "merge")
ROW_KEY = (("sections", "no"), ("exception_relations", "id"))


def is_cand(p):
    d = env_path("KG_CANDIDATES")
    return bool(d) and Path(p).parent == d


def mark(p):
    """처리 표시 자리 — 승인 기록 파일 done/<이름> · 후보 그래프 파일 done-candidates/<candidate_id>."""
    return STATE / ("done-candidates" if is_cand(p) else "done") / Path(p).stem


def candidate_problem(c, p):
    """kg-candidate@1(candidate-graph-inlet.md 3절) → 틀린 칸 이름 또는 None. rows는 Pack 줄 꼴(칸 검사는 적재기 몫 · 여기선 덮개 · id만)."""
    if not isinstance(c, dict) or c.get("format") != CAND_FORMAT:
        return "format"
    for k in ("candidate_id", "base", "reason", "source", "made_by", "decided_by", "rows"):   # 칸은 모두 있어야 함(decided_by는 null 됨)
        if k not in c:
            return k
    if not CAND_ID.match(p.stem) or c.get("candidate_id") != p.stem:
        return "candidate_id"
    if not isinstance(c.get("base"), dict) or not TEXT(c["base"].get("revision")):
        return "base.revision"
    if c.get("reason") not in REASONS:
        return "reason"
    if not isinstance(c.get("source"), dict) or c["source"].get("kind") not in ("missed", "overlap"):
        return "source.kind"
    if not TEXT(c.get("made_by")):
        return "made_by"
    if c.get("decided_by") is not None and not TEXT(c["decided_by"]):
        return "decided_by"
    rows = c.get("rows")
    if not isinstance(rows, dict) or set(rows) - {k for k, _ in ROW_KEY}:
        return "rows"
    for k, key in ROW_KEY:
        v = rows.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) and TEXT(x.get(key)) for x in v):
            return f"rows.{k}"
        if len({x[key] for x in v}) != len(v):           # 같은 id 두 번(적재기는 뒤 줄만 남김 · 한 번만 낼 것 — extractor-rows.md 5절)
            return f"rows.{k}"
    if not rows.get("sections") and not rows.get("exception_relations"):
        return "rows"
    return None


def meaning_problems(c, base_text):
    """까닭 ↔ 줄 뜻(candidate-graph-inlet.md 4절 · eval T8 inlet_errors) → 까닭 코드 목록(빈 목록 = 맞음). 기준 = 지금 도는 그래프.
    edit 길로 낸 widen · merge, records 빠진 통째 줄은 관문도 통과하고 조용히 '바뀜 0 · 근거 0'이 되므로 여기서 막음."""
    base = json.loads(base_text)
    rel0 = {x.get("id"): x for x in base.get("exception_relations") or []}
    sec0 = {s.get("no") for s in base.get("sections") or [] if s.get("kind") == "예외"}
    secs, rels, r = c["rows"].get("sections") or [], c["rows"].get("exception_relations") or [], c["reason"]
    out = []
    if r == "add" and (not rels or any(x["id"] in rel0 for x in rels)):
        out.append("add_needs_new_id")                       # 선 더함 = 새 관계 id만
    if r == "widen":
        if not rels or any(x["id"] not in rel0 for x in rels):
            out.append("widen_needs_same_id")
        if any(x.get("edit") for x in rels):
            out.append("widen_not_edit_path")                # edit 길은 when을 기준 그대로 둠(extractor-rows.md ⚠)
        if any(x["id"] in rel0 and x.get("when") == rel0[x["id"]].get("when") for x in rels):
            out.append("widen_when_same")
        if any(x["id"] in rel0 and (not set(rel0[x["id"]].get("records") or []) <= set(x.get("records") or []) or x.get("n") != len(x.get("records") or []))
               for x in rels):
            out.append("widen_records_n")                    # records ⊇ 기준 · n = 그 수(빠지면 근거 0)
    if r in ("replace", "merge"):
        if not secs or any(not s.get("edit_record") or s.get("no") not in sec0 for s in secs):
            out.append(f"{r}_needs_edit_record")
        if not c.get("decided_by"):
            out.append(f"{r}_needs_person")                  # 정답지 31쪽 사람 결정
    if r == "merge":
        tgt = {s.get("no") for s in secs}
        moved = [x for x in rels if not x.get("edit") and x["id"] in rel0 and x.get("step") != rel0[x["id"]].get("step") and x.get("step") in tgt]
        if not moved or any(x.get("edit") for x in rels):
            out.append("merge_needs_step_move")              # 합침 = B 관계 통째 step → A
    return out


def same_revision(want, now):
    """후보 base.revision이 지금 도는 revision인지 — 'sha256:' 머리 있든 없든 · 앞 8자 이상 같으면 같음."""
    w, n = str(want).split(":")[-1], str(now).split(":")[-1]
    return len(w) >= 8 and n.startswith(w)


def put_rows(rows):
    """후보 줄을 nodes.json에 id로 합침 — 같은 id면 새 줄로 바꿈 · 새 id면 더함(적재기 규칙과 같음)."""
    p = STATE / "nodes.json"
    acc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"sections": [], "exception_relations": []}
    for k, key in ROW_KEY:
        new = {x[key]: x for x in rows.get(k) or []}
        acc[k] = [new.pop(x[key]) if x[key] in new else x for x in acc.get(k) or []] + list(new.values())
    p.write_text(json.dumps(acc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def settle(rows):
    """반영 뒤: 건드린 id의 nodes.json 줄 ← 적재기가 낸 release(current.json) 줄(고침 표시 edit_record · edit 없는 결과 줄).
    release에서 빠진 절(합침으로 아무 관계도 안 가리킴)은 nodes.json에서도 뺌. 다음 적재가 옛 줄 · 고침을 되풀이하지 않게."""
    cur_p, p = STATE / "current.json", STATE / "nodes.json"
    if not cur_p.exists() or not p.exists():
        return
    cur = json.loads(cur_p.read_text(encoding="utf-8"))
    acc = json.loads(p.read_text(encoding="utf-8"))
    for k, key in ROW_KEY:
        have = {x.get(key): x for x in cur.get(k) or [] if k != "sections" or x.get("kind") == "예외"}
        ids = {x[key] for x in rows.get(k) or []}
        acc[k] = [have.get(x[key], x) if x[key] in ids else x for x in acc.get(k) or [] if x[key] not in ids or x[key] in have]
    p.write_text(json.dumps(acc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def touched_relations(rows, base_text):
    """관문 new_relations — 후보 관계 줄 id + 후보가 바꾼 절을 가리키는 지금 관계 id(바꿈 · 합침 = relations.changed)."""
    out = [x["id"] for x in rows.get("exception_relations") or []]
    nos = {s["no"] for s in rows.get("sections") or []}
    try:
        base = json.loads(base_text).get("exception_relations") or []
    except (ValueError, AttributeError):
        base = []
    return out + [x["id"] for x in base if isinstance(x, dict) and x.get("step") in nos and x.get("id") not in out]


def candidate_once(p):
    """후보 그래프 파일 하나(잠금 안) → 'failed' · 'held' · 'same' · 바꾼 태그."""
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        c = None
    entry = [(p, c if isinstance(c, dict) else {})]
    CURRENT[:] = entry
    bad = candidate_problem(c, p)
    if bad:                                      # 꼴 틀림 — 적재 · 관문 0 · 처리 표시 0(고친 새 파일을 기다림) · 같은 파일 알림은 한 번
        f = STATE / "failed-candidates" / p.stem
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            f.write_text(f"{bad} {datetime.now().isoformat(timespec='seconds')}")
            signal(entry, "failed", reason=f"후보 꼴 틀림: {bad}")
        log("후보 꼴 틀림", p.name, bad, "— 적재 · 관문 0")
        return "failed"
    tag, revision, layer = running()
    log("후보", p.stem, c["reason"], "· 만든 이", c["made_by"], "· 기준", str(c["base"].get("tag")), str(c["base"]["revision"])[:15])
    nodes_p = STATE / "nodes.json"
    nodes_before = nodes_p.read_text(encoding="utf-8") if nodes_p.exists() else None
    if not same_revision(c["base"]["revision"], revision):
        log("기준이 바뀜", str(c["base"]["revision"])[:15], "≠ 지금", str(revision)[:15])
        return hold(entry, tag, nodes_before, HoldError("base_moved", 0, [], {"base_revision": c["base"]["revision"], "running_revision": revision}))
    rows = c["rows"]
    try:
        put_rows(rows)
        res = note_layer(load(tag))
        ids = {s["no"] for s in rows.get("sections") or []} | {x["id"] for x in rows.get("exception_relations") or []}
        cut = [x for x in res.get("rejected_rows") or [] if isinstance(x, dict) and x.get("row") in ids]
        if cut:                                  # 적재기가 후보 줄을 뺌(고칠 예외 없음 · 센서 칸 · 기호 등) — 반쯤 실은 그래프는 배포 0
            return hold(entry, tag, nodes_before, HoldError("rows_rejected", 0, [], {"rejected_rows": cut}))
        base_text = git("show", f"{tag}:pack/{S.GRAPH}")
        bad_meaning = meaning_problems(c, base_text)
        if bad_meaning:                          # 까닭 ↔ 줄 뜻이 안 맞음 — 관문 0 · KG 반영 안 됨 + 신호로 LLMOps 알림
            log("후보 뜻 안 맞음", bad_meaning)
            return hold(entry, tag, nodes_before, HoldError("bad_candidate", 0, [], {"inlet": bad_meaning}))
        if res.get("tag") == layer:
            log("지금 도는 그래프와 같음", layer, "— 다시 띄움 0")
            settle(rows)
            signal(entry, "same", graph={"running_tag": tag, "new_tag": None}, rounds=0, fixed=[], reason="same_graph")
            return done(entry, "same")
        fixed, rounds, expect = [], 0, None
        if os.environ.get("KG_GATE"):
            try:
                res, fixed, rounds, expect = gate(entry, tag, nodes_before, res, cand=c, new_rel=touched_relations(rows, base_text))
            except HoldError as e:
                return hold(entry, tag, nodes_before, e)
        try:
            g = new_revision(tag, fixed=fixed, expect=expect)
        except RevisionMismatch as e:
            log("관문 revision", str(e.args[0])[7:15], "≠ 배포 셈", str(e.args[1])[7:15], "— 커밋 · 배포 0")
            return hold(entry, tag, nodes_before, HoldError("revision_mismatch", rounds, fixed, {"cand_revision": e.args[0], "deploy_revision": e.args[1]}))
        said = {"graph": {"running_tag": tag, "new_tag": g}, "rounds": rounds, "fixed": fixed, "reason": None, "checks": dict(GATE_CHECKS) if rounds else {}}
        told = row_first(g, entry, **said)
        S.swap(g)
    except (ApplyError, S.SwapError):            # 적재기 · 바꿔 끼움 실패 — 후보 줄이 다음 승인 기록 적재에 관문 없이 섞이지 않게 되돌림
        if nodes_before is None:
            nodes_p.unlink(missing_ok=True)
        else:
            nodes_p.write_text(nodes_before, encoding="utf-8")
        raise
    settle(rows)
    note_pair(tag, layer, g, res.get("tag"))
    log(f"후보 바꿔 띄움 {tag} → {g} · layer {layer} → {res.get('tag')}" + (f" · 관문 {rounds}번째 통과 · 고침 {len(fixed)}" if rounds else ""))
    if not told:
        signal(entry, "applied", **said)
    return done(entry, g)


def candidates():
    d = env_path("KG_CANDIDATES")
    if not d or not d.is_dir():
        return []
    return [p for p in sorted(d.glob("*.json")) if not p.name.startswith(".") and not mark(p).exists()]


def main():
    if not INBOX or not STATE:
        raise SystemExit("멈춤: KG_INBOX · KG_STATE가 없음")
    letters = os.environ.get("KG_LETTERS") or "apply"
    if letters not in ("apply", "off"):
        raise SystemExit("멈춤: KG_LETTERS는 비워 두거나 apply · off")
    STATE.mkdir(parents=True, exist_ok=True)
    CURRENT.clear()
    with open(STATE / "kg-apply.lock", "w") as lk:
        t0 = time.time()
        fcntl.flock(lk, fcntl.LOCK_EX)
        log(f"잠금 얻음(기다림 {time.time() - t0:.1f}초)")
        try:
            if "--carry-from" in sys.argv:          # 감시가 버전 전환을 끝낸 뒤 부름(D-588 C)
                out = carry_from(sys.argv[sys.argv.index("--carry-from") + 1])
                tidy_layers()
                return out
            out = apply_once() if letters == "apply" else "none"
            for p in candidates():               # 후보 그래프 파일 — 승인 기록 파일 뒤 · 한 번에 하나(candidate-graph-inlet.md 4절)
                c_out = candidate_once(p)
                out = c_out if out == "none" else out
            try:
                tidy_layers()                    # 뒷정리 — 실패해도 이번 처리 결과 · 신호는 그대로
            except Exception as e:               # noqa: BLE001
                log("옛 꼬리표 정리 건너뜀", type(e).__name__)
            return out
        except (ApplyError, S.SwapError) as e:
            log("멈춤", e)
            signal(CURRENT, "failed", reason=str(e))
            raise SystemExit(f"멈춤: {e}") from None


if __name__ == "__main__":
    main()
