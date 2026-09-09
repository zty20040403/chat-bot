from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# OWASP's minimum Argon2id profile; keep work bounded for the small control host.
HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = HASHER.hash("dummy-account-that-cannot-be-used-to-log-in")


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码需要 12～128 个字符")
    return HASHER.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    if not 1 <= len(password) <= 128:
        return False
    try:
        return HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
