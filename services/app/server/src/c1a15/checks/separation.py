"""분리 검사(AC-15-C1 · AC-34-C3): 두 ID 집합이 겹치면 겹친 ID를 돌려준다. 어느 파일을 읽을지는 호출자가 정한다."""

from collections.abc import Iterable


def overlap(ids_a: Iterable[str], ids_b: Iterable[str]) -> set[str]:
    return set(ids_a) & set(ids_b)
