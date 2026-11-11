"""The web page's optional password (PBKDF2-SHA256; only the hash is stored)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(ITERATIONS, salt.hex(), digest.hex())


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        found = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(found.hex(), digest)
    except (ValueError, TypeError):
        return False


def password_from_header(header: str) -> str:
    """The password in an HTTP Basic Authorization header (any user name)."""
    if not header or not header.lower().startswith("basic "):
        return ""
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""
    return decoded.split(":", 1)[1] if ":" in decoded else ""


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")
