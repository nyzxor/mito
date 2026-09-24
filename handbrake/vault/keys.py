"""Operator key (Ed25519) — ADR-0007. Private key lives in the OS keyring (or an explicit
file path in headless DEV setups); the public key is pinned in control/operator.pub."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

KEYRING_SERVICE = "mito"
KEYRING_USER = "operator-key"
KEY_FILE_ENV = "MITO_OPERATOR_KEY_FILE"


class OperatorKey:
    def __init__(self, private: Ed25519PrivateKey) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> OperatorKey:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_bytes(cls, raw: bytes) -> OperatorKey:
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    def to_bytes(self) -> bytes:
        return self._private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(message)


def verify(public_raw: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


class KeyStore:
    """Where the private key lives. Keyring first; explicit file only when the operator sets
    MITO_OPERATOR_KEY_FILE (headless dev). Never the repo, never the control dir."""

    def __init__(self, public_path: Path) -> None:
        self.public_path = public_path

    def save(self, key: OperatorKey) -> None:
        encoded = base64.b64encode(key.to_bytes()).decode("ascii")
        key_file = os.environ.get(KEY_FILE_ENV)
        if key_file:
            p = Path(key_file).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(encoded, encoding="utf-8")
        else:
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, encoded)
        self.public_path.parent.mkdir(parents=True, exist_ok=True)
        self.public_path.write_text(key.public_bytes().hex(), encoding="utf-8")

    def load_private(self) -> OperatorKey:
        key_file = os.environ.get(KEY_FILE_ENV)
        if key_file:
            encoded = Path(key_file).expanduser().read_text(encoding="utf-8").strip()
        else:
            import keyring

            found = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if not found:
                raise FileNotFoundError("operator key not found in keyring; run `mito init`")
            encoded = found
        return OperatorKey.from_bytes(base64.b64decode(encoded))

    def load_public(self) -> bytes:
        return bytes.fromhex(self.public_path.read_text(encoding="utf-8").strip())
