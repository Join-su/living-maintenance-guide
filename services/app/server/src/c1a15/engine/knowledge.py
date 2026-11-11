"""매뉴얼 지식: 증상군별 원인 후보(x.1 개요) · 점검과 연결 원인(확정 · 선별) · 사전 확인(x.2) · 조치 절."""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cause:
    code: str
    name: str
    group: str
    confirm_check: str
    action_section: str


@dataclass(frozen=True)
class Check:
    section_id: str
    title: str
    group: str
    cause: str
    kind: str  # "confirm"(확정형) | "screen"(선별형)
    checklist: tuple[str, ...] = ()


@dataclass
class ManualKnowledge:
    causes: dict[str, list[Cause]] = field(default_factory=dict)  # group -> 원인(개요 표 순서)
    checks: dict[str, list[Check]] = field(default_factory=dict)  # group -> 점검(절 번호 순)
    prechecks: dict[str, str] = field(default_factory=dict)  # group -> x.2 절
    sections: dict[str, dict] = field(default_factory=dict)

    def cause(self, code: str) -> Cause:
        return next(c for cs in self.causes.values() for c in cs if c.code == code)

    def check(self, section_id: str) -> Check:
        return next(c for cs in self.checks.values() for c in cs if c.section_id == section_id)


def section_key(section_id: str) -> tuple[int, ...]:
    return tuple(int(p) for p in section_id.split("."))


def _checklist(text: str) -> tuple[str, ...]:
    match = re.search(r"\*\*시작 전 체크리스트\*\*\s*\n((?:\s*- \[ \] .+\n?)+)", text or "")
    return (
        tuple(line.split("] ", 1)[1].strip() for line in match.group(1).splitlines() if "] " in line) if match else ()
    )


def from_sections(sections: list[dict]) -> ManualKnowledge:
    by_id = {s["id"]: s for s in sections}
    k = ManualKnowledge(sections=by_id)
    actions = {s["parent"]: s["id"] for s in sections if s["kind"] == "action"}
    for s in sections:
        if s["kind"] == "check":
            m = re.search(r"\*\*연결 원인\*\* (\S+) .+?\((확정|선별)\)", s["text"])
            if not m:
                raise ValueError(f"연결 원인이 없는 점검 절: {s['id']}")
            check = Check(
                s["id"],
                s["title"],
                s["group"],
                m.group(1),
                "confirm" if m.group(2) == "확정" else "screen",
                _checklist(s["text"]),
            )
            k.checks.setdefault(s["group"], []).append(check)
        elif s["kind"] == "precheck":
            k.prechecks[s["group"]] = s["id"]
    for s in sections:
        if s["kind"] == "info" and s["id"].endswith(".1") and s.get("group"):
            table = s["text"][s["text"].index("**원인 후보**") : s["text"].index("**점검 목록**")]
            for row in re.findall(r"^\| ([A-Z]{2}-\d) \| ([^|]+) \| [^|]+ \| ([\d.]+) \|$", table, flags=re.M):
                code, name, confirm = row[0], row[1].strip(), row[2]
                action = actions.get(confirm)
                if not action:
                    m = re.search(r"\*\*조치\*\* 원인이 확정되면 ([\d.]+) 절", by_id[confirm]["text"])
                    action = m.group(1) if m else ""
                k.causes.setdefault(s["group"], []).append(Cause(code, name, s["group"], confirm, action))
    for group in k.checks:
        k.checks[group].sort(key=lambda c: section_key(c.section_id))
    return k
