#!/usr/bin/env python3
"""분리 검사(AC-34-C3 · AC-15-C1 일부 · EV-001 VC-04): 시연 각본 행 ∩ 이력 행 = 0, 각본 행 ∩ 평가 카드 원본 행 = 0, 이력 ∩ 카드 = 0,
그리고 분할 v2 몫(generation/split): 이력 ⊂ train · 카드 ⊂ val ∪ test(val 카드 ∩ test 카드 = 0, val 카드의 test 몫 행은 D-246 예외 기록 안만) · 시연 ⊂ demo. 어기면 종료 코드 1.

평가 카드 쪽은 eval/cards/<val|test>/card_udi_map.json의 행 번호(UDI)만 읽는다(사용자 결정 2026-10-13). 검사 전용 스크립트이며 제품 코드가 아니다.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server" / "src"))
from c1a15.checks.separation import overlap  # noqa: E402

demo = {s["udi"] for s in json.loads((ROOT / "data/demo/scenarios.json").read_text())["scenarios"]}
history = {json.loads(line)["source"]["udi"] for line in (ROOT / "data/history/maintenance_history.jsonl").read_text().splitlines() if line.strip()}
sets = {p.parent.name: set(json.loads(p.read_text()).values()) for p in sorted((ROOT / "eval/cards").glob("*/card_udi_map.json"))}
cards = set().union(*sets.values()) if sets else set()
with open(ROOT / "generation/split/ai4i-split-by-udi.csv", encoding="utf-8") as f:
    split = {r["UDI"]: r["split"] for r in csv.DictReader(f)}
outside = lambda rows, allowed: sorted(u for u in rows if split.get(u) not in allowed)
sel = ROOT / "eval/cards/val/selection.json"
exc = (json.loads(sel.read_text()).get("exception") or {}) if sel.is_file() else {}   # D-246 예외 기록(구간 56)
sel_t = ROOT / "eval/cards/test/selection.json"
exc_ok = not exc or ((json.loads(sel_t.read_text()).get("exception") if sel_t.is_file() else None) == exc and all(split.get(u) == "test" for u in exc.get("cl_alarm_rows", [])) and set(exc.get("to_val", [])) <= set(exc.get("cl_alarm_rows", [])))
to_val = set(exc.get("to_val", [])) if exc_ok else set()
cross = sorted([u for u in sets.get("val", set()) if split.get(u) != "val" and u not in to_val] + [u for u in sets.get("test", set()) if split.get(u) != "test"])
results = {"demo_rows": len(demo), "history_rows": len(history), "eval_card_rows": {k: len(v) for k, v in sets.items()},
           "demo∩history": sorted(overlap(demo, history)), "demo∩eval_cards": sorted(overlap(demo, cards)), "history∩eval_cards": sorted(overlap(history, cards)),
           "val∩test_cards": sorted(overlap(sets.get("val", set()), sets.get("test", set()))),
           "history_not_train": outside(history, {"train"}), "cards_not_val_test": outside(cards, {"val", "test"}), "cards_cross_split_outside_exception": cross, "demo_not_demo": outside(demo, {"demo"}), "exception_record_bad": [] if exc_ok else ["val · test 예외 기록이 다르거나 test 몫 · 행 목록 밖"]}
print(json.dumps(results, ensure_ascii=False))
bad = [k for k, v in results.items() if isinstance(v, list) and v] + ([] if set(sets) == {"val", "test"} else ["card_sets"])
sys.exit(1 if bad else 0)
