"""Local secret and session handling."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


class SecretStore:
    """Encrypt GitHub access tokens with a local installation key."""

    def __init__(self, data_dir: Path) -> None:
        """Load or create the private encryption key."""
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = data_dir / "secret.key"
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key + b"\n")
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> bytes:
        """Encrypt a UTF-8 string."""
        return self._fernet.encrypt(value.encode())

    def decrypt(self, value: bytes) -> str:
        """Decrypt a UTF-8 string."""
        return self._fernet.decrypt(value).decode()


def new_token() -> str:
    """Return a cryptographically random URL-safe token."""
    return secrets.token_urlsafe(32)


def digest_token(value: str) -> str:
    """Return the SHA-256 digest used for server-side token lookup."""
    return hashlib.sha256(value.encode()).hexdigest()
