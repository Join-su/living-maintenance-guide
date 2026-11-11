"""가짜 provider chat 규칙(c1a15-pack/fake-rule@1, Pack EV-001 PS-05 · D-285) — Pack revision(pack/) 밖 도구, 표준 라이브러리만.

배포 서버(deploy_server/fake_provider.py — 배포 태그 트리의 이 파일) · LLMOps(llmops/fake_rule.py — LLMOPS_FAKE_RULE 자리) · Pack 시험이
부른다(task10-pack/fake-rule@1과 같은 꼴: FORMAT · FakeRefused(limits) · load_known() · respond_text(prompt) → (text, limits)).
보고서 문장 node(write_report · rewrite_report)의 prompt `## 보고서 초안` 칸(8항목 JSON)을 읽어 cause · action을 **초안 그대로** 돌려준다
— 앱 fixture 모드(llm/fixture.py, report_sentence = 템플릿 그대로)와 같은 문장. 실제 모델 품질은 보이지 않는다.
시험 표지(초안 `unresolved` 칸 글 안 — 약속 v3는 8항목 밖 칸을 막으므로):
  [시험:근거밖수치]   첫 답 · 다시 쓴 답 모두 cause에 근거 밖 수치 → 원문 대체 길
  [시험:첫답만근거밖] 첫 답의 cause만 근거 밖, 다시 쓴 답은 근거 안 → 다시 쓴 문장 통과 길
예외 칸 AI 나눔 node(split_exception · D-556 덧 7)의 prompt(`## 예외 칸 글` = 끝 칸 · 여러 줄)는 split_known.json의 정해진 답:
  같은 글(앞뒤 · 겹친 빈칸 정리 뒤) → 그 예외 하나(v10 · D-566) · 모르는 글 → 빈 점검 · 조치(fake_unknown_text — 지어내지 않음)
  [시험:나눔없음] → 빈 점검 · 조치 · [시험:꼴깨짐] → 빈 칸 점검 · 조치 하나씩(Pack이 invalid_output으로 거름)
그 밖의 prompt(표지 칸 없음)는 FakeRefused(refused) — 답을 지어내지 않는다."""
import json
import re
from pathlib import Path

FORMAT = "c1a15-pack/fake-rule@1"
LIMITS = ("refused", "test_marker", "fake_unknown_text")
DRAFT = "보고서 초안"
PREV = "앞 답"
OUT_OF_SOURCE = " 9999"   # 초안 · 근거 사례 · 절 번호 어디에도 없는 수치(시험 표지 때만)
SECTION = re.compile(r"^## ([^\n]+)\n([^\n]*)", re.M)
SPLIT = "\n## 예외 칸 글\n"
KNOWN_SPLIT = Path(__file__).with_name("split_known.json")
_WS = re.compile(r"\s+")


class FakeRefused(ValueError):
    """모르는 prompt — 돌려줄 답이 없음."""

    def __init__(self, message, limits=("refused",)):
        super().__init__(message)
        self.limits = list(limits)


def load_known(path=None):
    """배포 서버가 배포 때 부름 — 이 규칙은 등록 prompt 목록이 없음(prompt 칸 표지로만 답함)."""
    return {"format": FORMAT, "prompts": []}


def _sections(prompt):
    return {m.group(1).strip(): m.group(2) for m in SECTION.finditer(prompt)}


def _norm(text):
    return _WS.sub(" ", text).strip()


def _split(prompt):
    text = _norm(prompt.split(SPLIT, 1)[1])
    if "[시험:나눔없음]" in text:
        return {"exception": {"topic": "", "checks": [], "actions": []}}, ["test_marker"]
    if "[시험:꼴깨짐]" in text:
        return {"exception": {"topic": "", "checks": [{"what": "", "how": "", "criteria": "", "minutes": None, "instruction": ""}], "actions": [{"what": "", "parts": [], "minutes": None, "instruction": ""}]}}, ["test_marker"]
    for k in json.loads(KNOWN_SPLIT.read_text(encoding="utf-8"))["texts"]:
        if _norm(k["text"]) == text:
            ex = k["exception"]
            return {"exception": {**ex, "checks": [{**c, "instruction": _say(c)} for c in ex["checks"]], "actions": [{**a, "instruction": _say(a)} for a in ex["actions"]]}}, []
    return {"exception": {"topic": "", "checks": [], "actions": []}}, ["fake_unknown_text"]


def _say(c):
    """D-642 C 가짜 지시 문장 — 칸 값만 이음(실제 모델 문장 품질은 보이지 않음)."""
    return " ".join(x for x in (f"{c['what']}:", c.get("how"), c.get("criteria")) if x)


def respond(prompt):
    """(답 dict, 한계 이유 목록)."""
    if SPLIT in prompt:
        return _split(prompt)
    s = _sections(prompt)
    try:
        draft = json.loads(s[DRAFT])
        cause, action = draft["cause"], draft["action"]
    except (KeyError, ValueError, TypeError):
        raise FakeRefused("보고서 초안 칸이 없거나 깨진 prompt — 이 규칙이 답할 prompt가 아님") from None
    marks = draft.get("unresolved", "")
    rewrite = PREV in s
    if "[시험:근거밖수치]" in marks:
        return {"cause": cause + OUT_OF_SOURCE, "action": action}, ["test_marker"]
    if "[시험:첫답만근거밖]" in marks:
        return ({"cause": cause + " (다시 씀)", "action": action} if rewrite else {"cause": cause + OUT_OF_SOURCE, "action": action}), ["test_marker"]
    return {"cause": cause, "action": action}, []


def respond_text(prompt):
    answer, limits = respond(prompt)
    return json.dumps(answer, ensure_ascii=False), limits
