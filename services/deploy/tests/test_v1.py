"""태그 v1 배포 task10@v1-pg-local(지시 007, EV-002 CHG-03). 태그 v1이 아직 없으면 "태그 v1 대기" xfail로 표시한다(건너뜀을 통과로 보지 않음).
DB 없는 불러오기 시험: 실행 설정만 파일 backend로 바꿔 사본·태그·revision을 본다. PostgreSQL 실행은 tests/test_logdb.py."""
import subprocess

import pytest

from conftest import ROOT, TASK10, V0_REVISION


def v1_tag_exists():
    r = subprocess.run(["git", "-C", str(TASK10), "rev-parse", "--verify", "--quiet", "v1^{commit}"], capture_output=True)
    return r.returncode == 0


V1_WAIT = pytest.mark.xfail(not v1_tag_exists(), run=False,
                            reason="태그 v1 대기: task10-pack에 태그 v1이 아직 없습니다(지시 007 — 태그가 생기면 바로 켜짐)")
FILE_EXECUTION = {"entrypoint": "respond", "models": {},
                  "stores": {"history": {"backend": "sqlite-file", "release": "hc-ai4i2020-s7-v2"},
                             "manuals": {"backend": "flat-local", "release": "ir-manuals-v2"}}}


@V1_WAIT
def test_v1_pg_local_loads_tag(make_deployment):
    from deploy_server.settings import load_settings
    expect = load_settings(ROOT / "config" / "v1-pg-local.yaml").expect_revision
    assert expect, "config/v1-pg-local.yaml의 expect_revision이 비어 있습니다(EV-002 ES-2에서 알림 값으로 채움)"
    dep = make_deployment(config="v1-pg-local", execution=FILE_EXECUTION, registration={"mode": "none"})   # 불러오기만 — 장부(저장소 .env의 PG DSN)는 읽지 않음
    info = dep.pack_info()
    assert info["tag"] == "v1" and len(info["commit"]) == 40
    assert info["pack_revision"] == expect and expect != V0_REVISION   # 알림 값 기준(EV-002 v10 — 구조가 바뀐 v1도 같은 규칙)
