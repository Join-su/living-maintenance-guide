import pytest
from argon2 import PasswordHasher

from c1a15.checks.passwords import is_argon2id_hash_of
from c1a15.loader import core


def test_manual_and_grade_table(data_dir):
    sections = core.read_manual(data_dir)
    assert len(sections) == 43
    grades = core.grade_rows(sections)
    assert len(grades) == 36  # 부록 B 행 수
    by_id = {g["section_id"]: g for g in grades}
    assert by_id["4.4"]["grade"] == "C" and by_id["5.6"]["grade"] == "D" and by_id["2.5"]["grade"] == "B"
    actions = [s for s in sections if s["kind"] == "action"]
    assert len(actions) == 11 and all(
        s.get("parent") and "text" not in s for s in actions
    )  # 적재기는 text 없는 절을 받아야 한다


def test_common_precheck_items(data_dir):
    items = core.common_precheck_items(core.read_manual(data_dir))
    assert [i["text"] for i in items] == ["주변 공구·이물질을 치웠다", "커버가 모두 닫혔다", "주변 작업자가 없다"]
    assert [i["source_section"] for i in items] == ["2.5", "2.5", "5.5"]


def test_history_and_assignments(data_dir):
    history = core.read_history(data_dir)
    assert len(history) == 300
    rows = core.history_rows(history)
    assert {r["source"] for r in rows} == {"generated"}
    # 원본 행 번호(source.udi)를 record에 보존 — 각본 · 평가 카드 분리 검사(P-07 · P-16)의 입력
    assert all(r["record"]["source"]["udi"] for r in rows) and len({r["record"]["source"]["udi"] for r in rows}) == 300
    assignments = core.equipment_assignments(history)
    assert len(assignments) == 20
    assert {a["user_id"] for a in assignments} == {uid for uid, _ in core.TECH_ACCOUNTS}


def test_account_passwords_are_argon2id_and_required():
    hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    seeds = core.account_seeds("t-pass", "l-pass", hasher)
    assert [s.role for s in seeds].count("tech") == 4 and [s.role for s in seeds].count("lead") == 1
    assert all(s.password_hash.startswith("$argon2id$") for s in seeds)
    assert is_argon2id_hash_of(seeds[0].password_hash, "t-pass")
    with pytest.raises(ValueError):
        core.account_seeds("", "l-pass")
