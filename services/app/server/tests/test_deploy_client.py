# ruff: noqa: E501 — 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-06: 배포 서버 부르기 한 곳 — VC-36 재시도 · 시간 초과 · 409 폴링 · 시도 키 · 재시도 0 / VC-38 설정 거절.

가짜 배포 서버(fake_deploy, 계약 v0.9 키 저장 규칙)와 메모리 감사(Ledger)로 본다. 기다림은 가짜 sleep · 가짜 시계로(실제로 기다리지 않음).
감사를 DB에 따로 commit하는 것은 test_deploy_ledger.
"""

import socket

import pytest

from c1a15.config import ConfigError, pack_settings
from c1a15.deploy.client import BACKOFF, TIMEOUT, Client, PackUnavailable, attempt_key, base_key, check_url
from fake_deploy import FakeDeploy

BODY = {"kind": "judge", "session_id": "S-A57", "group": "CL", "similar": [], "events": [], "grades": {}, "minutes": {}}
OUT = {"decision": {"kind": "lock", "section": "1.2"}}
BASE = base_key("judge", "S-A57", 12, BODY)


class MemLedger:
    def __init__(self):
        self.rows = []

    def retryable_stored(self, base):
        return sum(1 for r in self.rows if r["base_key"] == base and r["stored"] and r["retryable"])

    def record(self, detail):
        self.rows.append(detail)


class Sleeps(list):
    """가짜 sleep — 받은 초를 남기고 가짜 시계를 그만큼 움직인다. on_sleep(n)은 n번째 sleep 뒤에 부름."""

    def __init__(self, on_sleep=None):
        super().__init__()
        self.t, self.on_sleep = 0.0, on_sleep

    def __call__(self, s):
        self.append(s)
        self.t += s
        if self.on_sleep:
            self.on_sleep(len(self))

    def clock(self):
        return self.t


def client(fake, ledger=None, sleep=None, **kw):
    sl = sleep if sleep is not None else Sleeps()
    return Client(fake.url, ledger if ledger is not None else MemLedger(), sleep=sl, clock=sl.clock, **kw)


def keys(fake):
    return [r["headers"]["Idempotency-Key"] for r in fake.posts()]


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_success_returns_201_output_and_one_post():
    with FakeDeploy() as f:
        f.plan = [{"output": OUT}]
        r = client(f).run("judge", "S-A57", 12, BODY)
    assert (r.output, r.run_id, r.deployment_id, r.replayed) == (OUT, "run_0001", "c1a15@v1-local", False)
    assert keys(f) == [BASE] and f.executions == 1


def test_base_key_shape_and_attempt_key():
    assert (
        BASE.startswith("c1a15-judge-S-A57-12-")
        and len(BASE.rsplit("-", 1)[1]) == 16
        and len(BASE) <= 255
        and " " not in BASE
    )
    assert attempt_key(BASE, 0) == BASE and attempt_key(BASE, 2) == BASE + "-r2"
    assert (
        base_key("judge", "S-A57", 12, dict(reversed(list(BODY.items())))) == BASE
    )  # 같은 본문 = 키 순서 무관(계약 3절)
    for bad in ("S A57", "세션", "S-\t1"):
        with pytest.raises(ValueError):
            base_key("judge", bad, 1, BODY)


def test_vc36_unreachable_same_key_three_times_then_fail():
    led, sl = MemLedger(), Sleeps()
    c = Client(f"http://127.0.0.1:{closed_port()}", led, sleep=sl, clock=sl.clock)
    with pytest.raises(PackUnavailable) as e:
        c.run("judge", "S-A57", 12, BODY)
    assert e.value.code == "unreachable" and list(sl) == list(BACKOFF)
    assert (
        len(led.rows) == 1 and led.rows[0]["stored"] is False and led.rows[0]["retryable"] is False
    )  # A10-N1: 연결 실패 = 감사 1줄


def test_vc36_502_retry_same_key():
    # 실제 502 · 504는 프록시 응답이라 키에 저장되지 않지만, 가짜는 저장된 502를 replay한다 — 이 시험은 '같은 키 4번'만 본다
    with FakeDeploy() as f:
        f.plan = [{"status": 502, "code": "bad_gateway"}]
        sl, led = Sleeps(), MemLedger()
        with pytest.raises(PackUnavailable):
            client(f, led, sl).run("judge", "S-A57", 12, BODY)
    assert keys(f) == [BASE] * 4 and list(sl) == list(BACKOFF) and f.executions == 1


def test_vc36_a_stored_trace_unavailable_then_attempt_key_r1_succeeds():
    with FakeDeploy() as f:
        f.plan = [{"status": 503, "code": "trace_unavailable", "retryable": True}, {"output": OUT}]
        led = MemLedger()
        r = client(f, led).run("judge", "S-A57", 12, BODY)
    assert r.output == OUT and keys(f) == [BASE, BASE + "-r1"] and f.executions == 2
    assert [(x["code"], x["stored"], x["retryable"], x["key"]) for x in led.rows] == [
        ("trace_unavailable", True, True, BASE)
    ]


def test_vc36_a_resend_same_seq_after_two_stored_failures_uses_r2():
    """A9-N8 · A10-N1: 요청 1회 = 기본 키 · -r1 둘 다 저장된 retryable 실패(감사 2줄) → 503. 같은 seq 다시 = -r2 새 실행 → 통과."""
    led = MemLedger()
    with FakeDeploy() as f:
        f.plan = [{"status": 503, "code": "trace_unavailable", "retryable": True}] * 2 + [{"output": OUT}]
        with pytest.raises(PackUnavailable) as e:
            client(f, led).run("judge", "S-A57", 12, BODY)
        assert e.value.code == "trace_unavailable" and len(led.rows) == 2
        r = client(f, led).run("judge", "S-A57", 12, BODY)  # 같은 seq · 같은 본문(되돌림 뒤 화면이 다시 보냄)
    assert r.output == OUT and keys(f) == [BASE, BASE + "-r1", BASE + "-r2"] and f.executions == 3


def test_vc36_b_timeout_then_server_finished_same_key_replay():
    """첫 POST가 시간 초과(가짜는 실행을 끝까지 저장) → 같은 키로 다시 → 저장 결과 replay. 다시 보내기 전에 실행이 끝나기를 기다린다(결정적)."""
    with FakeDeploy() as f:
        f.plan = [{"output": OUT, "delay": 0.5}]
        sl = Sleeps(on_sleep=lambda n: f.done.wait(5))
        r = client(f, sleep=sl, timeouts={"judge": 0.1}).run("judge", "S-A57", 12, BODY)
    assert (
        r.output == OUT and r.replayed is True and keys(f) == [BASE, BASE] and f.executions == 1 and list(sl) == [2.0]
    )


def test_vc36_c_stored_non_retryable_failure_resend_gets_same_failure_no_new_run():
    led = MemLedger()
    with FakeDeploy() as f:
        f.plan = [{"status": 500, "code": "deployment_not_registered"}]
        for _ in range(2):
            with pytest.raises(PackUnavailable) as e:
                client(f, led).run("judge", "S-A57", 12, BODY)
            assert e.value.code == "deployment_not_registered"
    assert f.executions == 1 and keys(f) == [BASE, BASE]  # 두 번째는 저장된 같은 실패(replay) · 새 실행 0
    assert all(x["stored"] and not x["retryable"] for x in led.rows)


def test_vc36_d_409_without_run_id_resends_same_key_after_2s():
    with FakeDeploy() as f:
        f.plan = [{"output": OUT}]
        f.start_running(BASE, BODY, run_id=None)  # 실행 중인데 run_id 없음(지금 서버 코드로는 안 생김)
        sl = Sleeps(on_sleep=lambda n: f.keys.pop(BASE, None))  # 2초 뒤면 앞 요청이 사라진 모양
        r = client(f, sleep=sl).run("judge", "S-A57", 12, BODY)
    assert r.output == OUT and keys(f) == [BASE, BASE] and list(sl) == [2.0] and f.gets() == []


def test_vc36_409_poll_every_2s_until_succeeded_one_post():
    with FakeDeploy() as f:
        rid = f.start_running(BASE, BODY)
        sl = Sleeps(on_sleep=lambda n: n == 3 and f.finish(BASE, output=OUT))
        r = client(f, sleep=sl).run("judge", "S-A57", 12, BODY)
    assert r.output == OUT and r.run_id == rid and len(keys(f)) == 1 and f.executions == 0
    assert list(sl) == [2.0, 2.0, 2.0] and [g["path"] for g in f.gets()] == [f"/runs/{rid}"] * 3


def test_vc36_409_poll_limit_120s_then_fail_without_resending():
    """review R-1 · R-2: 120초 안에 안 끝나면 같은 키로 다시 보내지 않고 끝냄 — 폴링은 한 번(2초씩 60번)."""
    led = MemLedger()
    with FakeDeploy() as f:
        rid = f.start_running(BASE, BODY)
        sl = Sleeps()
        with pytest.raises(PackUnavailable) as e:
            client(f, led, sl).run("judge", "S-A57", 12, BODY)
    assert (e.value.code, e.value.run_id) == ("poll_timeout", rid) and len(keys(f)) == 1
    assert sum(sl) == pytest.approx(120.0) and len(f.gets()) <= 60
    assert len(led.rows) == 1 and led.rows[0]["stored"] is False and led.rows[0]["run_id"] == rid


def test_vc36_409_poll_counts_get_time(monkeypatch):
    """review R-2: GET에 걸린 시간도 120초 안에 셈 — 가짜 시계에서 GET 한 번이 30초 걸리면 GET은 4번 안."""
    with FakeDeploy() as f:
        f.start_running(BASE, BODY)
        sl = Sleeps()
        c = client(f, sleep=sl)
        orig = c._http

        get_timeouts = []

        def slow_get(method, path, timeout, key=None, body=None):
            if method == "GET":
                get_timeouts.append(timeout)
                sl.t += 30.0
            return orig(method, path, timeout, key, body)

        monkeypatch.setattr(c, "_http", slow_get)
        with pytest.raises(PackUnavailable) as e:
            c.run("judge", "S-A57", 12, BODY)
    assert e.value.code == "poll_timeout" and len(f.gets()) == 4
    assert get_timeouts[:3] == [30.0, 30.0, 30.0] and get_timeouts[3] == pytest.approx(22.0)  # 마지막은 남은 시간


def test_vc36_409_poll_404_no_retry_audit():
    led = MemLedger()
    with FakeDeploy() as f:
        f.start_running(BASE, BODY, run_id="run_gone")
        f.runs.pop("run_gone")
        with pytest.raises(PackUnavailable) as e:
            client(f, led).run("judge", "S-A57", 12, BODY)
    assert e.value.code == "run_not_found" and e.value.run_id == "run_gone" and len(keys(f)) == 1
    assert led.rows[0]["stored"] is False and led.rows[0]["run_id"] == "run_gone"


def test_vc36_409_poll_failed_run_interrupted_then_new_key_once():
    with FakeDeploy() as f:
        f.start_running(BASE, BODY)
        f.plan = [{"output": OUT}]
        sl = Sleeps(on_sleep=lambda n: n == 1 and f.finish(BASE, error={"code": "run_interrupted", "retryable": True}))
        led = MemLedger()
        r = client(f, led, sl).run("judge", "S-A57", 12, BODY)
    assert r.output == OUT and keys(f) == [BASE, BASE + "-r1"] and led.rows[0]["code"] == "run_interrupted"


def test_vc36_e_unknown_code_no_retry_audit():
    led, sl = MemLedger(), Sleeps()
    with FakeDeploy() as f:
        f.plan = [{"status": 500, "code": "payload_too_large"}]
        with pytest.raises(PackUnavailable) as e:
            client(f, led, sl).run("judge", "S-A57", 12, BODY)
    assert e.value.code == "payload_too_large" and len(keys(f)) == 1 and list(sl) == []
    assert (
        led.rows[0]["code"] == "payload_too_large"
        and led.rows[0]["retryable"] is False
        and led.rows[0]["stored"] is True
    )


def test_vc36_invalid_input_no_retry_not_stored():
    led = MemLedger()
    with FakeDeploy() as f:
        with pytest.raises(PackUnavailable) as e:
            client(f, led).run("judge", "S-A57", 12, {**BODY, "event_card": {}})  # judge에 금지 칸
    assert e.value.code == "invalid_input" and len(keys(f)) == 1 and led.rows[0]["stored"] is False


def test_vc36_run_failed_retryable_node_error_gets_one_new_key_only():
    with FakeDeploy() as f:
        f.plan = [{"status": 500, "code": "run_failed", "retryable": True}] * 3
        led = MemLedger()
        with pytest.raises(PackUnavailable):
            client(f, led).run("judge", "S-A57", 12, BODY)
    assert f.executions == 2 and len(led.rows) == 2  # 새 키는 요청 한 번에 한 번


def test_vc36_run_failed_reads_node_error_retryable_not_top_level():
    """review (e): 위쪽 retryable과 node_error.retryable이 엇갈리면 node_error만 본다(계약 10절 6항)."""
    with FakeDeploy() as f:
        f.plan = [{"status": 500, "code": "run_failed", "retryable": True, "node_retryable": False}, {"output": OUT}]
        with pytest.raises(PackUnavailable):
            client(f).run("judge", "S-A57", 12, BODY)
    assert f.executions == 1


def test_vc36_run_failed_carries_where_and_node_code_for_model_failure_mapping():
    """D-275: 예측 node 실패 = 500 run_failed, where = run.predict_failure — 앱(ES-08)이 읽도록 예외 · 감사에 싣는다."""
    led = MemLedger()
    with FakeDeploy() as f:
        f.plan = [
            {"status": 500, "code": "run_failed", "where": "run.predict_failure", "node_code": "feature_not_number"}
        ]
        with pytest.raises(PackUnavailable) as e:
            client(f, led).run("alarm", "C1A-E7f3a91c2d4b0", 0, {"kind": "alarm", "event_card": {}})
    assert (e.value.code, e.value.where, e.value.node_code) == (
        "run_failed",
        "run.predict_failure",
        "feature_not_number",
    )
    assert (led.rows[0]["where"], led.rows[0]["node_code"]) == ("run.predict_failure", "feature_not_number")


def test_vc36_409_without_run_id_waits_2s_each_three_times():
    """deploy 답 R6-5: run_id 없는 409는 2초 뒤 같은 키로, 3번 안."""
    with FakeDeploy() as f:
        f.start_running(BASE, BODY, run_id=None)
        sl = Sleeps()
        with pytest.raises(PackUnavailable) as e:
            client(f, sleep=sl).run("judge", "S-A57", 12, BODY)
    assert e.value.code == "idempotency_in_progress" and list(sl) == [2.0, 2.0, 2.0] and keys(f) == [BASE] * 4


def test_vc36_bad_2xx_body_is_pack_unavailable_with_audit():
    led = MemLedger()
    with FakeDeploy() as f:
        f.plan = [{"output": "보고서 글"}]  # output이 dict가 아님
        with pytest.raises(PackUnavailable) as e:
            client(f, led).run("judge", "S-A57", 12, BODY)
    assert e.value.code == "bad_response" and led.rows[0]["code"] == "bad_response"


def test_vc36_timeouts_by_kind():
    assert TIMEOUT == {
        "alarm": 90.0,
        "start": 90.0,
        "judge": 30.0,
        "report": 90.0,
        "exception_split": 90.0,  # 약속 v10 2절 AI 나눔
        "exception_similar": 30.0,  # 약속 v10 3절
    } and BACKOFF == (
        2.0,
        5.0,
        10.0,
    )  # report = Q-19(deploy 108)
    with FakeDeploy() as f, pytest.raises(ValueError):
        client(f, poll_every=0)


@pytest.mark.parametrize(
    "env",
    [
        {"C1A15_JUDGE": "deploy"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "http://deploy:8796"},  # 이름을 C1A15_DEPLOY_HOST로 정하지 않음
        {
            "C1A15_DEPLOY_URL": "http://other:8796",
            "C1A15_DEPLOY_HOST": "fake-deploy",
        },  # 정한 이름과 다름(preview X 넷째)
        {"C1A15_DEPLOY_URL": "http://localhost:8796", "C1A15_DEPLOY_HOST": "localhost"},  # 이름 규칙(preview X 다섯째)
        {"C1A15_DEPLOY_URL": "http://10.0.0.5:8796", "C1A15_DEPLOY_HOST": "10.0.0.5"},
        {"C1A15_DEPLOY_URL": "http://Bad_Name:8796", "C1A15_DEPLOY_HOST": "Bad_Name"},
        {"C1A15_DEPLOY_URL": "http://a..b:8796", "C1A15_DEPLOY_HOST": "a..b"},
        {"C1A15_DEPLOY_URL": "http://deploy.:8796", "C1A15_DEPLOY_HOST": "deploy."},
        # ES-20 review B1: IPv4로 읽히는 이름(inet_aton 꼴) · *.localhost · 그 밖 IP 꼴
        {"C1A15_DEPLOY_URL": "http://0x7f000001:8796", "C1A15_DEPLOY_HOST": "0x7f000001"},
        {"C1A15_DEPLOY_URL": "http://0x7f.1:8796", "C1A15_DEPLOY_HOST": "0x7f.1"},
        {"C1A15_DEPLOY_URL": "http://10.0.0.0x5:8796", "C1A15_DEPLOY_HOST": "10.0.0.0x5"},
        {"C1A15_DEPLOY_URL": "http://0x0:8796", "C1A15_DEPLOY_HOST": "0x0"},
        {"C1A15_DEPLOY_URL": "http://127.1:8796", "C1A15_DEPLOY_HOST": "127.1"},
        {"C1A15_DEPLOY_URL": "http://a.localhost:8796", "C1A15_DEPLOY_HOST": "a.localhost"},
        {"C1A15_DEPLOY_URL": "http://ip6-localhost:8796", "C1A15_DEPLOY_HOST": "ip6-localhost"},
        {"C1A15_DEPLOY_URL": "http://ip6-loopback:8796", "C1A15_DEPLOY_HOST": "ip6-loopback"},
        {"C1A15_DEPLOY_URL": "http://0.0.0.0:8796", "C1A15_DEPLOY_HOST": "0.0.0.0"},
        {"C1A15_DEPLOY_URL": "http://127.0.0.1:8796", "C1A15_DEPLOY_HOST": "127.0.0.1"},
        {"C1A15_DEPLOY_URL": "http://[::1]:8796", "C1A15_DEPLOY_HOST": "::1"},
        {"C1A15_DEPLOY_URL": "http://Fake-Deploy:8796", "C1A15_DEPLOY_HOST": "Fake-Deploy"},
        {"C1A15_DEPLOY_URL": "http://fake-deploy:8796", "C1A15_DEPLOY_HOST": "a" * 254},
        {"C1A15_DEPLOY_URL": "http://fake-deploy:8796/x", "C1A15_DEPLOY_HOST": "fake-deploy"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "https://127.0.0.1:8796"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "http://127.0.0.1"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "http://localhost:8796"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "http://u:p@127.0.0.1:8796"},
        {"C1A15_JUDGE": "deploy", "C1A15_DEPLOY_URL": "http://127.0.0.1:8796/x?y=1"},
        {"C1A15_ALARM": "ml"},  # 배포 서버 주소 없음
        {},  # 배포 서버 주소 없음
        # ES-16 N-13: 값 때문에 거절되는지 보려고 맞는 주소를 함께 줌
        {"C1A15_JUDGE": "pack", "C1A15_DEPLOY_URL": "http://127.0.0.1:8796"},
        {"C1A15_JUDGE": "inproc", "C1A15_DEPLOY_URL": "http://127.0.0.1:8796"},  # 3단계에서 빠짐(ES-16 S3-Q8)
        {"C1A15_ALARM": "sensor", "C1A15_DEPLOY_URL": "http://127.0.0.1:8796"},
    ],
)
def test_vc38_settings_refused(env, tmp_path):
    with pytest.raises(ConfigError) as e:
        pack_settings(env, dockerenv=str(tmp_path / "none"))
    if env.get("C1A15_JUDGE") == "inproc":
        assert "inproc" in str(e.value) and "deploy만" in str(e.value)


def test_vc38_deploy_refused_inside_container(tmp_path):
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    with pytest.raises(ConfigError) as e:  # 컨테이너 안 127.0.0.1 = 컨테이너 자신(preview X 셋째)
        pack_settings(
            {"C1A15_DEPLOY_URL": "http://127.0.0.1:8796", "C1A15_DEPLOY_HOST": "fake-deploy"}, dockerenv=str(marker)
        )
    assert "컨테이너 자신" in str(e.value) and "C1A15_DEPLOY_HOST" in str(e.value)
    with pytest.raises(ConfigError):
        pack_settings({"C1A15_DEPLOY_URL": "http://127.0.0.1:8796"}, dockerenv=str(marker))


def test_vc60_container_uses_one_internal_name(tmp_path):
    """VC-60(EV-001 CH-38) · preview X: 컨테이너면 C1A15_DEPLOY_HOST 이름 하나로 시작 · 맥 host 직접은 127.0.0.1 그대로."""
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    env = {"C1A15_DEPLOY_URL": "http://fake-deploy:18796", "C1A15_DEPLOY_HOST": "fake-deploy"}
    got = pack_settings(env, dockerenv=str(marker))
    assert got["deploy_url"] == "http://fake-deploy:18796" and got["deploy_host"] == "fake-deploy"
    assert pack_settings(env, dockerenv=str(tmp_path / "none"))["deploy_url"] == "http://fake-deploy:18796"
    with pytest.raises(ValueError):
        check_url("http://fake-deploy:18796")  # 이름을 정하지 않으면 127.0.0.1만
    assert check_url("http://fake-deploy:18796", "fake-deploy") == "http://fake-deploy:18796"
    # D-333 덧 Q-a: 점 있는 DNS 이름(맥 compose → 맥 배포 창 · VPS 서비스 이름)
    mac = {"C1A15_DEPLOY_URL": "http://host.docker.internal:8796", "C1A15_DEPLOY_HOST": "host.docker.internal"}
    assert pack_settings(mac, dockerenv=str(marker))["deploy_url"] == "http://host.docker.internal:8796"
    vps = {
        "C1A15_DEPLOY_URL": "http://deploy-example-v1:8796",
        "C1A15_DEPLOY_HOST": "deploy-example-v1",
    }  # 꼴만(실제 값 0, PR-40)
    assert pack_settings(vps, dockerenv=str(marker))["deploy_host"] == "deploy-example-v1"


@pytest.mark.parametrize(
    ("env", "msg"),
    [  # VC-60 · preview X 넷째 · 다섯째 줄 메시지(ES-20 review N5)
        (
            {"C1A15_DEPLOY_URL": "http://other:8796", "C1A15_DEPLOY_HOST": "fake-deploy"},
            "또는 http://<C1A15_DEPLOY_HOST>:<port>만",
        ),
        (
            {"C1A15_DEPLOY_URL": "http://fake-deploy:8796", "C1A15_DEPLOY_HOST": "localhost"},
            "C1A15_DEPLOY_HOST는 내부망 이름",
        ),
        (
            {"C1A15_DEPLOY_URL": "http://fake-deploy:8796", "C1A15_DEPLOY_HOST": "10.0.0.5"},
            "C1A15_DEPLOY_HOST는 내부망 이름",
        ),
        (
            {"C1A15_DEPLOY_URL": "http://fake-deploy:8796", "C1A15_DEPLOY_HOST": "Bad_Name"},
            "C1A15_DEPLOY_HOST는 내부망 이름",
        ),
    ],
)
def test_vc60_refusal_messages(env, msg, tmp_path):
    with pytest.raises(ConfigError) as e:
        pack_settings(env, dockerenv=str(tmp_path / "none"))
    assert msg in str(e.value)


def test_vc38_defaults_and_ok(tmp_path):
    none = str(tmp_path / "none")
    assert pack_settings({"C1A15_DEPLOY_URL": "http://127.0.0.1:8796"}, dockerenv=none) == {
        "judge": "deploy",
        "alarm": "ml",
        "deploy_url": "http://127.0.0.1:8796",
        "deploy_host": None,
    }  # 기본 = deploy(EV-001 CH-30) · 알람 ml(dev-2 D-546 ML만)
    ok = pack_settings(
        {"C1A15_JUDGE": "deploy", "C1A15_ALARM": "ml", "C1A15_DEPLOY_URL": "http://127.0.0.1:8796/"}, dockerenv=none
    )
    assert ok == {"judge": "deploy", "alarm": "ml", "deploy_url": "http://127.0.0.1:8796", "deploy_host": None}
    assert check_url("http://127.0.0.1:8796") == "http://127.0.0.1:8796"
