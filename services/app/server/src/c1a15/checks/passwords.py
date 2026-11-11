"""저장된 계정 비밀번호가 argon2id 해시인지 확인한다(AC-36-C2)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


def is_argon2id_hash_of(stored: str, plain: str) -> bool:
    if not stored.startswith("$argon2id$") or stored == plain:
        return False
    try:
        return PasswordHasher().verify(stored, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
