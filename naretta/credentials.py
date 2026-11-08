"""API key storage in Windows Credential Manager, with OPENAI_API_KEY as a fallback."""

from __future__ import annotations

import logging
import os
import sys

from .redact import register_secret

log = logging.getLogger(__name__)

SERVICE_NAME = "Naretta"
USERNAME = "openai_api_key"

SOURCE_CREDENTIAL_MANAGER = "Windows Credential Manager"
SOURCE_ENVIRONMENT = "OPENAI_API_KEY environment variable"


class CredentialError(Exception):
    pass


def _keyring():
    import keyring

    if sys.platform == "win32":
        # Pick the backend explicitly so the frozen .exe doesn't depend on entry-point discovery.
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    return keyring.get_keyring()


def load_saved_key() -> str | None:
    try:
        key = _keyring().get_password(SERVICE_NAME, USERNAME)
    except Exception as exc:  # backend missing or locked
        log.warning("Could not read the saved API key: %s", type(exc).__name__)
        return None
    key = (key or "").strip()
    return key or None


def save_key(key: str) -> None:
    key = key.strip()
    if not key:
        raise CredentialError("The API key is empty.")
    register_secret(key)
    try:
        _keyring().set_password(SERVICE_NAME, USERNAME, key)
    except Exception as exc:
        raise CredentialError(
            f"Couldn't save the key to Windows Credential Manager ({type(exc).__name__})."
        ) from None


def delete_saved_key() -> None:
    try:
        _keyring().delete_password(SERVICE_NAME, USERNAME)
    except Exception as exc:
        if type(exc).__name__ != "PasswordDeleteError":
            raise CredentialError(
                f"Couldn't remove the saved key ({type(exc).__name__})."
            ) from None


def resolve_api_key() -> tuple[str | None, str | None]:
    """Returns (key, source). The saved key wins over the environment variable."""
    key = load_saved_key()
    if key:
        register_secret(key)
        return key, SOURCE_CREDENTIAL_MANAGER
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        register_secret(key)
        return key, SOURCE_ENVIRONMENT
    return None, None
