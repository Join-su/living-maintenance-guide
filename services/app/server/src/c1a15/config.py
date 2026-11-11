"""실행 설정은 환경 변수로만 받는다. 비밀값(비밀번호 · 키)은 저장소에 두지 않는다(AC-36)."""

import os


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(f"환경 변수 {name} 가 필요합니다")
    return value


def database_url() -> str:
    return require_env("DATABASE_URL")


def kg_inbox(env=os.environ) -> str | None:
    """그래프 적재기 받은 편지함(D-543 · c1a15-pack inbox-fit): C1A15_KG_INBOX — 없으면 None(쓰지 않음). 절대 경로만."""
    folder = env.get("C1A15_KG_INBOX", "")
    if not folder:
        return None
    if not os.path.isabs(folder):
        raise ConfigError(f"C1A15_KG_INBOX 는 절대 경로: {folder!r}")
    return folder


def r12_inbox(env=os.environ) -> str | None:
    """R12 받은 편지 폴더(EV-001 CH-23): C1A15_R12_INBOX — 없으면 None(쓰지 않음 = 지금 동작). 절대 경로만."""
    folder = env.get("C1A15_R12_INBOX", "")
    if not folder:
        return None
    if not os.path.isabs(folder):
        raise ConfigError(f"C1A15_R12_INBOX 는 절대 경로: {folder!r}")
    return folder


def checks(env=os.environ) -> str:
    """사람 체크 기록 보내기(EV-006 CH-5): C1A15_CHECKS = off(기본 — 지금 동작)
    | deploy(파트장 마지막 결정 뒤 배포 서버로)."""
    value = env.get("C1A15_CHECKS", "") or "off"
    if value not in {"off", "deploy"}:
        raise ConfigError(f"C1A15_CHECKS 는 off 또는 deploy: {value!r}")
    return value


def pack_settings(env=os.environ, dockerenv: str = "/.dockerenv") -> dict:
    """Pack을 부르는 길(EV-001 CH-15 · CH-30): 판정 C1A15_JUDGE = deploy(기본이자 하나뿐),
    알람 C1A15_ALARM = ml(dev-2 기본 — D-546 ML만 · 공구 마모만 센서 경계) | boundary.

    배포 서버 주소 C1A15_DEPLOY_URL 한 곳(EV-001 CH-38): 맥 host 직접 = http://127.0.0.1:<port>,
    컨테이너(compose · VPS) = http://<C1A15_DEPLOY_HOST>:<port>(내부망 이름 하나).
    컨테이너 안 127.0.0.1은 컨테이너 자신이라 거절.
    inproc(앱 서버 안 판정)는 3단계에서 빠짐(D-297 (1) · S3-Q8). 어긋나면 ConfigError — 서버 시작을 거절한다.
    """
    from c1a15.deploy.client import check_url, is_loopback  # 주소 규칙 한 곳

    judge = env.get("C1A15_JUDGE", "") or "deploy"
    alarm = env.get("C1A15_ALARM", "") or "ml"
    if judge == "inproc":
        raise ConfigError("C1A15_JUDGE=inproc 은 3단계(EV-001 ES-16)에서 빠짐 — deploy만")
    if judge != "deploy":
        raise ConfigError(f"C1A15_JUDGE 는 deploy: {judge!r}")
    if alarm not in ("boundary", "ml"):
        raise ConfigError(f"C1A15_ALARM 은 boundary | ml: {alarm!r}")
    raw, host_name = env.get("C1A15_DEPLOY_URL", ""), env.get("C1A15_DEPLOY_HOST", "") or None
    try:
        url = check_url(raw, host_name)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    if os.path.exists(dockerenv) and is_loopback(url):
        raise ConfigError("배포 서버 주소: 컨테이너 안 127.0.0.1은 컨테이너 자신 — C1A15_DEPLOY_HOST 이름을 쓰세요")
    return {"judge": judge, "alarm": alarm, "deploy_url": url, "deploy_host": host_name}
