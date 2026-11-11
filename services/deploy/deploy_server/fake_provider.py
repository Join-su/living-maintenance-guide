"""가짜 provider(in-process, 네트워크 없음). 개발·시험용이며 실제 모델 품질을 보이지 않는다.

chat 규칙(VFE EV-005 ES-F, CHG-06 · 오케 답장 019-05):
- 배포하는 Pack 태그 트리에 규칙 모듈 `tools/fake/rule.py`(task10-pack/fake-rule@1)가 있으면 **그 트리의 모듈**을 부른다(Gate A가 그 태그에서 쓴
  규칙 = 배포단 가짜, 시험한 것 = 배포한 것 D-50). 한계 이유(prompt_unknown · passthrough · refused)는 호출마다 `limits`에 남기고 서버 로그에 한 줄.
  거부(FakeRefused)는 그 호출을 실패로 둔다(packrt node 오류 → 실행 실패, 조용히 지나가지 않음).
- 모듈이 없는 트리(태그 v0 · v1)는 아래 옛 규칙 그대로(운영 중 v1 결과가 바뀌지 않게 — '가짜 provider 한계(옛 규칙)'). 옛 규칙은 task10-pack
  `tests/test_gate_a_e2e.py`(EV-007)의 FakeProvider를 옮겨 둔 것.
- embed: task10-pack `tools/data/index_release.fake_embedding`과 같은 64차원 결정적 벡터(색인 ir-manuals-v2와 같은 규칙) — 두 경우 같음"""
import hashlib
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections import deque
from pathlib import Path

EMBED_DIM = 64
SOURCE_NOTE = "synthetic 외부 알람(데모용), ML 결과 아님"
OLD_RULE = "가짜 provider 한계(옛 규칙)"
LIMITS_KEPT = 1000
RULE_PATH = "tools/fake/rule.py"
RULE_DIR = "tools/fake"
KNOWN_PATH = "fake-answers/known-prompts.json"
log = logging.getLogger("deploy_server")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)


CHILD = """
import json, runpy, sys
ns = runpy.run_path(sys.argv[1], run_name="task10_fake_rule")
if sys.argv[2] == "format":
    ns["load_known"]()          # 등록 목록이 없거나 깨졌으면 여기서 실패 — 배포 때 멈춤(요청마다 실패하지 않게)
    print(json.dumps({"format": ns.get("FORMAT")}))
    raise SystemExit(0)
prompt = sys.stdin.read()
try:
    text, limits = ns["respond_text"](prompt)
    out = {"text": text, "limits": list(limits)}
except ns["FakeRefused"] as e:
    out = {"refused": True, "message": str(e), "limits": list(e.limits)}
print(json.dumps(out, ensure_ascii=False))
"""


class FakeRefused(ValueError):
    """배포 트리 규칙 모듈이 거부함(가짜 응답 한계 refused). limits = 그 프롬프트의 이유 전부."""

    def __init__(self, message, limits):
        super().__init__(message)
        self.limits = list(limits)


class TreeRule:
    """배포 트리의 규칙 모듈을 **따로 띄운 파이썬**(-I -S -B, 빈 환경)에서 부른다 — 배포단 프로세스는 Pack 쪽 코드를 import하지 않음
    (import 경계 D-13 · V-06 그대로), 모듈은 표준 라이브러리만 볼 수 있음(site-packages 없음), DB 비밀 등 환경 변수는 넘기지 않음."""
    FakeRefused = FakeRefused

    def __init__(self, path):
        self.path = Path(path)
        self.FORMAT = self._call("format")["format"] or "task10-pack/fake-rule"

    def _call(self, mode, prompt=""):
        r = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", CHILD, str(self.path), mode], input=prompt,
                           capture_output=True, text=True, encoding="utf-8", timeout=60, env={})
        try:
            return json.loads(r.stdout)
        except ValueError:
            err = (r.stderr.strip().splitlines() or ["(출력 없음)"])[-1][:200]
            raise RuntimeError(f"가짜 규칙 모듈 실행 실패(종료 {r.returncode}): {err}") from None

    def respond_text(self, prompt):
        out = self._call("respond", prompt)
        if out.get("refused"):
            raise FakeRefused(out.get("message") or "가짜 응답 한계(refused)", out.get("limits") or ["refused"])
        return out["text"], out["limits"]


def load_rule(repo, commit, packs_dir):
    """배포하는 트리(commit)의 규칙 모듈. 그 트리에 `tools/fake/rule.py`가 없으면 None(옛 규칙).
    모듈이 든 폴더(tools/fake/ — 모듈이 옆에서 읽는 파일 포함, 예: c1a15-pack split_known.json) · 등록 목록을 `git archive`로
    packs_dir/<저장소>-<commit 12자>-fake-rule/에 꺼내 읽기 전용으로 두고(Pack 사본과 따로 — 사본 규칙 그대로),
    TreeRule로 감싼다. Pack 저장소에는 쓰지 않는다."""
    repo, packs_dir = Path(repo), Path(packs_dir)
    if _git(repo, "cat-file", "-e", f"{commit}:{RULE_PATH}").returncode != 0:
        return None
    root = packs_dir / f"{repo.name}-{commit[:12]}-fake-rule"
    if not root.exists():
        packs_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=packs_dir))
        try:
            paths = [RULE_DIR] + ([KNOWN_PATH] if _git(repo, "cat-file", "-e", f"{commit}:{KNOWN_PATH}").returncode == 0 else [])
            r = _git(repo, "archive", "--format=tar", commit, *paths)
            if r.returncode != 0:
                raise RuntimeError(f"규칙 모듈을 꺼내지 못했습니다: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
            (tmp / ".archive.tar").write_bytes(r.stdout)
            with tarfile.open(tmp / ".archive.tar") as tf:
                tf.extractall(tmp, filter="data")
            (tmp / ".archive.tar").unlink()
            for dirpath, _, filenames in os.walk(tmp, topdown=False):      # 쓰기 권한은 이름을 바꾸기 전에 뺌(중간에 멈춰도 쓰기 가능한 사본이 남지 않게)
                for name in filenames:
                    f = os.path.join(dirpath, name)
                    os.chmod(f, os.stat(f).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            try:
                os.rename(tmp, root)
            except OSError:
                if not root.exists():
                    raise
                shutil.rmtree(tmp, ignore_errors=True)                      # 다른 프로세스가 먼저 만듦 — 그것을 씀
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return TreeRule(root / RULE_PATH)


def fake_embedding(text):
    raw = b"".join(hashlib.sha256(f"{i}:{text}".encode()).digest() for i in range(EMBED_DIM * 2 // 32 + 1))
    vec = [(int.from_bytes(raw[2 * i:2 * i + 2], "big") / 65535.0) * 2 - 1 for i in range(EMBED_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _section_json(prompt, heading):
    m = re.search(rf"^## {re.escape(heading)}\n(.*)$", prompt, re.M)
    return json.loads(m.group(1)) if m and m.group(1).strip() else []


class FakeProvider:
    def __init__(self, rule=None):
        """rule: 배포 트리의 규칙 모듈(load_rule). None = 옛 규칙(태그 v0 · v1)."""
        self.calls = []
        self.rule = rule
        self.limits = deque(maxlen=LIMITS_KEPT)   # chat 호출마다 한계 이유 목록(최근 LIMITS_KEPT개만 — 상주 서버에서 끝없이 늘지 않게)
        self.count = 0                            # 지금까지 chat 호출 수(실행 하나의 한계만 모으는 표지 — limits_since, 지시 040 C-4)
        log.info("가짜 provider 규칙: %s", self.rule_name)   # 옛 규칙 트리(v0 · v1)는 '가짜 provider 한계(옛 규칙)'로 남음

    @property
    def rule_name(self):
        return getattr(self.rule, "FORMAT", "task10-pack/fake-rule") if self.rule is not None else OLD_RULE

    def _note(self, limits):
        self.limits.append(list(limits))
        self.count += 1

    def limits_since(self, mark):
        """표지(count) 뒤 chat 호출들의 한계 이유 목록과, 기록이 잘렸는지(LIMITS_KEPT보다 많았음)."""
        n = self.count - mark
        if n <= 0:
            return [], False
        kept = list(self.limits)
        return kept[-n:], n > len(kept)

    embedder = None   # D-403 덧: 설정 embedding 칸이면 packrt HttpProvider — 질의 임베딩만 실제, chat은 가짜 그대로

    def embed(self, provider, model, texts):
        self.calls.append(("embed", provider, model))
        if self.embedder is not None:   # last_response는 넘기지 않음(llm node provider_raw에 임베딩 응답이 섞이지 않게 — embed_tokens는 None)
            return self.embedder.embed(provider, model, texts)
        return [fake_embedding(t) for t in texts]

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, *, strict=False, seed=None, reasoning_effort=None):
        # seed(RX-1) · reasoning_effort(packrt 0.14.0 D-475)는 받기만 — 답은 그대로. strict는 schema가 strict 구조화 출력 조건에 맞지 않으면 실제 provider처럼 HTTP 400(RX-6, packrt 0.11.0)
        self.calls.append(("chat", provider, model))
        if strict and schema is not None:
            self._strict_400(provider, schema)
        prompt = messages[-1]["content"]
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        if self.rule is not None:
            try:
                text, limits = self.rule.respond_text(prompt)
            except self.rule.FakeRefused as e:
                self._note(e.limits)
                log.warning("가짜 응답 한계(%s): %s — 거부", self.rule_name, ", ".join(e.limits))
                raise
            self._note(limits)
            if limits:
                log.warning("가짜 응답 한계(%s): %s", self.rule_name, ", ".join(limits))
            return text, usage
        self._note([])
        answer = self._escalation(prompt) if "escalation으로 판정" in prompt else self._draft(prompt)
        return json.dumps(answer, ensure_ascii=False), usage

    @staticmethod
    def _strict_400(provider, schema):
        """packrt HttpProvider(0.11.0)처럼 뿌리 $schema · $id를 뺀 사본을 보고, strict_schema_problems 오류가 있으면 그 provider의
        400과 같은 모양(ProviderError '… HTTP 400: <본문>')으로 멈춘다. strict 호출 때만 import."""
        from packrt.ir import strict_schema_problems
        from packrt.run.providers import ProviderError
        errors, _ = strict_schema_problems({k: v for k, v in schema.items() if k not in ("$schema", "$id")})
        if errors:
            body = json.dumps({"error": {"message": f"Invalid schema for response_format 'output': {errors[0]}", "type": "invalid_request_error",
                                         "param": "response_format", "code": None}}, ensure_ascii=False)
            raise ProviderError(f"{provider} /chat/completions HTTP 400: {body[:300]}")

    @staticmethod
    def _candidates(evidence):
        hist = sorted((e for e in evidence if e.get("source_type") == "history" and "distance" in e), key=lambda e: (e["distance"], e["id"]))
        groups = {}
        for e in hist:
            if e.get("failure_type") in ("TWF", "HDF", "PWF", "OSF", "RNF"):
                groups.setdefault(e["failure_type"], []).append(e)
        return [{"type": t, "rank": i, "evidence_ids": [e["id"] for e in es], "best_distance": es[0]["distance"]}
                for i, (t, es) in enumerate(groups.items(), 1)]

    @staticmethod
    def _procedures(evidence):
        manual = [e["id"] for e in evidence if e.get("source_type") == "manual"]
        ids = manual or [e["id"] for e in evidence][:1]
        return [{"step": f"근거 {i}의 점검 절차·안전 경고를 따라 점검합니다", "evidence_ids": [i]} for i in ids]

    def _draft(self, prompt):
        ev = _section_json(prompt, "근거(검색 결과, 거리 가까운 순)")
        return {"kind": "draft", "cause_candidates": self._candidates(ev), "procedures": self._procedures(ev),
                "uncertainty": "센서 유사 사례 기준 후보입니다 — 담당자 확인이 필요합니다", "alarm_source_note": SOURCE_NOTE}

    def _escalation(self, prompt):
        ev = _section_json(prompt, "참고 근거(검색 결과)")
        m = re.search(r"^- 근거 충분: .*, 이유: (.*)$", prompt, re.M)
        reason = (m.group(1).strip() if m else "") or "no_evidence"
        out = {"kind": "escalation", "cause_candidates": [], "procedures": self._procedures(ev),
               "uncertainty": "원인 후보를 좁히지 못했습니다 — 담당자 확인이 필요합니다", "escalation_reason": reason, "alarm_source_note": SOURCE_NOTE}
        if reason == "mismatch":
            rules = json.loads(re.search(r"^- 세부: (.*)$", prompt, re.M).group(1))
            d = next(r["details"] for r in rules.values() if "alarm_candidate_types" in (r.get("details") or {}))
            out["escalation_detail"] = {"alarm_candidate_types": d["alarm_candidate_types"], "nearest_history": d["nearest_history"]}
        return out
