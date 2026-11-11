"""약속 v1(contracts/v1, 약속 확정본 2026-10-24 A · B) 시험 — 사례는 contract_cases 공통."""
import unittest

from contract_cases import cases

Examples, RequestRejects, ResultRejects, _, _, _ = cases("v1")

if __name__ == "__main__":
    unittest.main()
