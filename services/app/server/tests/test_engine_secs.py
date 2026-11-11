"""D-585: 소수 초가 든 시각도 며칠 전이 맞음(M-01 근거가 3.3일을 '1일 전'으로 보인 사건)."""

from c1a15.engine import exception


def test_fraction_of_second_does_not_change_days_ago():
    alarm = "2026-11-06T04:35:47+00:00"
    plain = (exception._secs(alarm) - exception._secs("2026-11-02T21:23:33+00:00")) / 86400
    frac = (exception._secs(alarm) - exception._secs("2026-11-02T21:23:33.435374+00:00")) / 86400
    assert round(plain, 2) == round(frac, 2) == 3.3
    assert exception._secs("2026-11-02T21:23:33.5+09:00") == exception._secs("2026-11-02T21:23:33+09:00")
