#!/usr/bin/env python3
"""Efficiency scenario seed — two starting Pack folders made from the first Pack folder, each with waste that has no effect on results.

  python3 scripts/efficiency-packs.py /packs/first /packs          → /packs/eff-model · /packs/eff-structure

  eff-model      the exception-split path also runs split_memo: the same split again on the expensive model (gpt-4.1); it writes nothing,
                 so the result is unchanged. Expected improvement: model one step down (gpt-4.1 → gpt-4.1-mini) or removal → cost goes down.
  eff-structure  the report path also runs write_report_dup: the same report sentences written twice; nobody reads report_text_dup.
                 Expected improvement: remove the node → tokens · calls · latency go down.

Register a folder on the AgentOps screen ('＋ release' · local folder path /packs/eff-model or /packs/eff-structure), then evaluate with the
test suites already in the Pack (evals/): eff-model → c1a15-split-work (12) · c1a15-split-confirm (8); eff-structure → c1a15-report-work-v4 (13)
· c1a15-report-confirm-v4 (13). Results paths, prompts and temperature stay the same as the first Pack; only the waste is added.
Each edit must match the first Pack's text exactly once, otherwise nothing is written (exit 2). Standard library only · no network · no key.
Exit codes: 0 made (or already there) · 1 usage · 2 the first Pack text is not what the edits expect · 3 target exists and differs (FORCE=1 replaces).
"""
import filecmp
import os
import shutil
import sys
from pathlib import Path

SPLIT_MEMO = """      - id: split_memo   # efficiency demo waste (model): the same split again on the expensive model — nobody reads it (writes nothing)
        kind: llm@1
        params: {provider: openai, model: gpt-4.1-2025-04-14, prompt: prompts/exception_split.md, output: ExceptionSplit, temperature: 0, strict: true}
        reads: [request]
        writes: []
        expect: {text: "예외 칸 글 메모(보조) — 결과 칸에 쓰이지 않음"}
"""
REPORT_DUP = """      - id: write_report_dup   # efficiency demo waste (structure): the same report sentences written twice — nobody reads report_text_dup
        kind: llm@1
        params: {provider: openai, model: gpt-4.1-mini-2025-04-14, prompt: prompts/report_sentence.md, output: ReportSentences, temperature: 0}
        reads: [request]
        writes: [report_text_dup]
        expect: {text: "보고서 초안 두 칸을 한 번 더 씀(중복) — 결과 칸에 쓰이지 않음"}
"""
CHAT_OLD = "chat: [gpt-4.1-mini-2025-04-14, gpt-4o-mini-2024-07-18]"
CHAT_NEW = "chat: [gpt-4.1-mini-2025-04-14, gpt-4o-mini-2024-07-18, gpt-4.1-2025-04-14]"

# (file, old text, new text) — each old text must appear exactly once
EDITS = {
    "eff-model": [
        ("pack.yaml", "      - id: finish_split\n", SPLIT_MEMO + "      - id: finish_split\n"),
        ("pack.yaml", "      - [split_exception, finish_split]\n", "      - [split_exception, split_memo]\n      - [split_memo, finish_split]\n"),
        ("models.yaml", CHAT_OLD, CHAT_NEW),   # the expensive model must be in the Pack's allowed chat list
    ],
    "eff-structure": [
        ("pack.yaml", "      report_text: ReportSentences\n", "      report_text: ReportSentences\n      report_text_dup: ReportSentences\n"),
        ("pack.yaml", "      - id: check_report\n", REPORT_DUP + "      - id: check_report\n"),
        ("pack.yaml", "      - [write_report, check_report]\n", "      - [write_report, write_report_dup]\n      - [write_report_dup, check_report]\n"),
    ],
}


def same_tree(a: Path, b: Path) -> bool:
    c = filecmp.dircmp(a, b)
    if c.left_only or c.right_only or c.diff_files or c.funny_files:
        return False
    return all(same_tree(a / d, b / d) for d in c.common_dirs)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.split("\n\n")[0])
        return 1
    first, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not (first / "pack.yaml").is_file() or not (first / "models.yaml").is_file():
        print(f"efficiency-packs · no Pack folder at {first} (pack.yaml · models.yaml)")
        return 1
    texts = {}
    for name, edits in EDITS.items():
        files = {f: (first / f).read_text(encoding="utf-8") for f in {e[0] for e in edits}}
        for f, old, new in edits:
            n = files[f].count(old)
            if n != 1:
                print(f"efficiency-packs · {name}: {f} has {n} matches for {old.strip()[:60]!r} (expected 1) — nothing written")
                return 2
            files[f] = files[f].replace(old, new)
        texts[name] = files
    for name, files in texts.items():
        tmp = out / f".{name}.new"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(first, tmp, symlinks=True)
        for f, t in files.items():
            (tmp / f).write_text(t, encoding="utf-8")
        dst = out / name
        if dst.exists():
            if same_tree(tmp, dst):
                shutil.rmtree(tmp)
                print(f"efficiency-packs · {name} already there · unchanged")
                continue
            if os.environ.get("FORCE") != "1":
                shutil.rmtree(tmp)
                print(f"efficiency-packs · {dst} exists and differs — FORCE=1 replaces it")
                return 3
            shutil.rmtree(dst)
        tmp.rename(dst)
        print(f"efficiency-packs · made {dst} ({', '.join(sorted(files))} changed · the rest is the first Pack)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
