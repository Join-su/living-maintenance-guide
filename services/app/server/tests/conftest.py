from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def data_dir() -> Path:
    return REPO / "data"


@pytest.fixture(scope="module")
def data_dir_module() -> Path:
    return REPO / "data"


_FAKE = {}


def pytest_configure(config):
    """모든 시험 = deploy 길(가짜 배포 서버 + 앱 engine 계산) — 기본이자 하나뿐인 길(EV-001 ES-16 CH-30).

    앱 모듈(api.main)이 import 때 배포 서버 주소를 확인하므로 수집 전에 가짜 서버를 띄우고 주소를 환경에 둔다.
    시험마다 `deploy` fixture(test_deploy_path)가 자기 가짜 서버로 바꿔 쓸 수 있다.
    """
    import os

    from fake_deploy import FakeDeploy, engine_answer

    fake = FakeDeploy().__enter__()
    fake.default = engine_answer
    os.environ["C1A15_JUDGE"], os.environ["C1A15_DEPLOY_URL"] = "deploy", fake.url
    # 시험 기본 알람 길 = 경계(시연 각본 · 흐름 시험은 경계 행으로 만듦) — ML 길 시험은 fixture가 ml로(앱 기본 ml D-546)
    os.environ["C1A15_ALARM"] = "boundary"
    _FAKE["fake"] = fake


def pytest_unconfigure(config):
    import os

    fake = _FAKE.pop("fake", None)
    if fake is None:
        return
    evidence = os.environ.get("C1A15_DEPLOY_EVIDENCE")  # 받은 본문 · 돌려준 응답 → VC-35 schema 검사
    if evidence:
        import json
        from pathlib import Path

        with Path(evidence).open("a", encoding="utf-8") as f:
            for r in fake.posts():
                f.write(json.dumps({"request": r["body"]}, ensure_ascii=False) + "\n")
            for rec in fake.keys.values():
                if rec["status"] in (200, 201):
                    f.write(json.dumps({"response": rec["payload"]["result"]["output"]}, ensure_ascii=False) + "\n")
    fake.__exit__(None, None, None)
