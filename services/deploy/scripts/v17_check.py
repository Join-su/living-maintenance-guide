"""V-17 — 태그 대조(EV-002 run 폴더 도구를 옮김, VFE EV-005 ES-B CHG-04). 알림 값이 정본이고 고정 접두어는 보지 않는다(답장 012-01).
(EV-005) tag는 --tag로 받는다(기본 v1 — v1 입력이면 원본과 판단·출력·종료 코드가 같음, V-39).

확인: 설정의 Pack 저장소에서 `git rev-parse <ref>^{commit}` = 알림 commit 40자, 태그 = 알림 tag(--tag, 기본 v1),
배포단 archive 사본(`.packs/`, 서버와 같은 pack_source.materialize)의 packrt revision = 알림 revision, revision ≠ V0.
설정에 expect_revision이 있으면 알림과 같은지도 본다(ES-2는 V-17 뒤에 채우므로 비어 있어도 통과).
사용(배포단 루트): scripts/py scripts/v17_check.py --config C --commit <40자> --revision <sha256:…> [--tag v1]
종료: 0 통과 / 1 불일치(질문) / 2 사용 오류."""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from deploy_server.pack_source import materialize  # noqa: E402
from deploy_server.settings import load_settings  # noqa: E402
from packrt.ir import load_pack  # noqa: E402

V0_REVISION = "sha256:877b0ed5e60724bc0b0b82fc77925be4f8240ba2a9249aef6090c42689ce60fa"
REV_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--tag", default="v1")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.commit) or not REV_RE.match(a.revision):
        print("사용 오류: --commit은 알림 commit 40자(소문자 16진수), --revision은 sha256:<64자>", file=sys.stderr)
        return 2
    s = load_settings(a.config)
    copy = materialize(s.pack_repo, s.pack_ref, s.packs_dir, s.pack_subdir, s.releases_subdir)
    res = load_pack(copy.pack_dir)
    if not res.ok:   # 옛 packrt로 새 Pack을 읽으면(038 1-1, 넘김 H-12) TypeError 대신 이유를 보이고 멈춤
        print(f"V-17 {s.deployment_id}: 이 이미지의 packrt가 Pack을 읽지 못함 — " + " / ".join(e["message"] for e in res.errors)[:300])
        return 1
    rev = res.revision
    checks = [
        ("commit = 알림(40자)", copy.commit == a.commit, f"{copy.commit[:12]} / 알림 {a.commit[:12]}"),
        (f"tag = {a.tag}", copy.tag == a.tag, f"{copy.tag!r}"),
        ("revision = 알림", rev == a.revision, f"{rev[:15]}… / 알림 {a.revision[:15]}…"),
        ("revision ≠ V0", rev != V0_REVISION, rev[:15] + "…"),
    ]
    if s.expect_revision:
        checks.append(("설정 expect_revision = 알림", s.expect_revision == a.revision, s.expect_revision[:15] + "…"))
    ok = all(c[1] for c in checks)
    print(f"V-17 {s.deployment_id} (Pack 저장소 {s.pack_repo.name}, ref {s.pack_ref})")
    for name, good, detail in checks:
        print(f"- {name}: {'맞음' if good else '다름'} ({detail})")
    print("V-17 통과" if ok else "V-17 불일치 — 멈추고 질문")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
