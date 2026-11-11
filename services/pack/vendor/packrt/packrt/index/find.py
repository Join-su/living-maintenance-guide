"""packrt.index.find_corpus (EV-008 CHG-P02 · D-293, contract C): the corpus release folder a vector DB version was built from, found
under release_roots by the 0.12.x rule — one or two levels of manifest.json under the roots, by release_id (the same real folder
counts once, an unreadable manifest is skipped); the folder must read as a corpus (kind · format · file hashes · content hash) and,
when a sha256 is given, have that hash. Files are only read."""
from pathlib import Path

from .errors import IndexBuildError


def find_corpus(release_roots, corpus_release):
    """release_roots: folder paths (str | Path); corpus_release: a base index manifest's {"id", "sha256"} as written, or an id.
    Returns the folder as found under the roots. Raises IndexBuildError: corpus_missing (no such id) · corpus_ambiguous (the id in
    two or more real folders) · index_invalid (not a corpus, files or hash changed, or another sha256 than the one given)."""
    from ..ir.releases import find_releases
    from .corpus import read_corpus   # imported here: `import packrt.index` stays as light as 0.12.0
    if isinstance(corpus_release, dict):
        cid, want = corpus_release.get("id"), corpus_release.get("sha256")
    else:
        cid, want = corpus_release, None
    if not isinstance(cid, str) or not cid or (want is not None and not isinstance(want, str)):
        raise IndexBuildError("index_invalid", f"corpus_release는 {{id, sha256}} 또는 release 번호여야 합니다: {corpus_release!r}")
    if isinstance(release_roots, (str, Path)) or not all(isinstance(r, (str, Path)) for r in release_roots):
        raise IndexBuildError("index_invalid", "release_roots는 폴더 경로 목록이어야 합니다")
    places = find_releases([Path(r) for r in release_roots]).get(cid) or {}
    if not places:
        raise IndexBuildError("corpus_missing", f"corpus release '{cid}'을 보관 폴더에서 찾을 수 없습니다")
    if len(places) > 1:
        raise IndexBuildError("corpus_ambiguous", f"corpus release 번호가 겹칩니다: {cid} ({', '.join(sorted(str(v) for v in places.values()))})")
    folder = Path(next(iter(places.values())))
    data, _, _ = read_corpus(folder)
    if data["release_id"] != cid:
        raise IndexBuildError("index_invalid", f"corpus release '{cid}' 폴더의 manifest release_id가 다릅니다: {data['release_id']}")
    if want is not None and data["sha256"] != want:
        raise IndexBuildError("index_invalid", f"corpus release '{cid}'의 sha256이 주어진 값과 다릅니다")
    return folder
