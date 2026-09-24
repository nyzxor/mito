"""Credential vault / broker (DESIGN §5.7). The agent never sees a secret.

Handles look like `cred:github-readonly`. The broker resolves them only inside the Handbrake
(egress gateway, future IMAP). Outputs that echo a live secret are scrubbed. Panic calls
`revoke_all`.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

KEYRING_SERVICE = "mito"
KEYRING_USER = "vault-key"
KEY_FILE_ENV = "MITO_VAULT_KEY_FILE"
HANDLE_RE = re.compile(r"^(?:cred:)?([a-z][a-z0-9_-]{0,62})$")


class VaultError(ValueError):
    pass


def normalize_handle(raw: str) -> str:
    m = HANDLE_RE.match(raw.strip())
    if not m:
        raise VaultError(
            f"invalid handle {raw!r}; use 'cred:name' with [a-z][a-z0-9_-]*"
        )
    return f"cred:{m.group(1)}"


@dataclass(frozen=True)
class HandleInfo:
    handle: str
    note: str
    revoked: bool

    def to_dict(self) -> dict[str, Any]:
        return {"handle": self.handle, "note": self.note, "revoked": self.revoked}


class VaultStore:
    def __init__(self, control_dir: Path) -> None:
        self.control_dir = control_dir
        self._path = control_dir / "vault.enc"
        self._fernet = Fernet(self._load_or_create_key(control_dir))
        self._data = self._load()

    def _load_or_create_key(self, control_dir: Path) -> bytes:
        key_file = os.environ.get(KEY_FILE_ENV)
        if key_file:
            p = Path(key_file).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(Fernet.generate_key().decode("ascii"), encoding="utf-8")
                try:
                    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
            return p.read_text(encoding="utf-8").strip().encode("ascii")
        fallback = control_dir / "vault.key"
        if fallback.exists():
            return fallback.read_text(encoding="utf-8").strip().encode("ascii")
        try:
            import keyring

            found = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if found:
                return found.encode("ascii")
            generated = Fernet.generate_key().decode("ascii")
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, generated)
            return generated.encode("ascii")
        except Exception:  # noqa: BLE001 — headless / no keyring backend
            generated = Fernet.generate_key().decode("ascii")
            fallback.write_text(generated, encoding="utf-8")
            try:
                fallback.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            return generated.encode("ascii")

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            raw = self._fernet.decrypt(self._path.read_bytes())
            data = json.loads(raw)
        except (InvalidToken, ValueError, OSError) as exc:
            raise VaultError(f"vault unreadable: {exc}") from exc
        handles = data.get("handles", {})
        if not isinstance(handles, dict):
            raise VaultError("vault blob is corrupt")
        return {str(k): dict(v) for k, v in handles.items()}

    def _save(self) -> None:
        blob = self._fernet.encrypt(
            json.dumps({"handles": self._data}, separators=(",", ":")).encode("utf-8")
        )
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self._path)

    def add(self, handle: str, secret: str, *, note: str = "") -> str:
        if not secret or secret.strip() == "":
            raise VaultError("secret must be non-empty")
        name = normalize_handle(handle)
        self._data[name] = {"secret": secret, "note": note, "revoked": False}
        self._save()
        return name

    def resolve(self, handle: str) -> str:
        name = normalize_handle(handle)
        rec = self._data.get(name)
        if rec is None or rec.get("revoked"):
            raise VaultError(f"unknown or revoked handle {name}")
        return str(rec["secret"])

    def revoke(self, handle: str) -> bool:
        name = normalize_handle(handle)
        rec = self._data.get(name)
        if rec is None:
            return False
        rec["secret"] = ""
        rec["revoked"] = True
        self._save()
        return True

    def revoke_all(self) -> int:
        n = 0
        for rec in self._data.values():
            if not rec.get("revoked"):
                rec["secret"] = ""
                rec["revoked"] = True
                n += 1
        if n:
            self._save()
        return n

    def list_handles(self) -> list[HandleInfo]:
        return [
            HandleInfo(h, str(rec.get("note", "")), bool(rec.get("revoked")))
            for h, rec in sorted(self._data.items())
        ]

    def live_secrets(self) -> list[tuple[str, str]]:
        return [
            (h, str(rec["secret"]))
            for h, rec in self._data.items()
            if not rec.get("revoked") and rec.get("secret")
        ]

    def scrub(self, text: str) -> str:
        out = text
        for handle, secret in self.live_secrets():
            if secret and secret in out:
                out = out.replace(secret, f"[REDACTED:{handle}]")
        return out

    def url_leaks_secret(self, url: str) -> str | None:
        for handle, secret in self.live_secrets():
            if secret and secret in url:
                return handle
        return None
