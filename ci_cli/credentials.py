"""Stored settings and credentials.

Non-secret settings (workspace, username) live in a TOML config file:
``$XDG_CONFIG_HOME/ci-cli/config.toml``, defaulting to ``~/.config/ci-cli/config.toml``.

The API key is never written to that file. It goes in the OS credential store via
``keyring``: the login Keychain on macOS, Secret Service on Linux and Credential
Manager on Windows.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

KEYRING_SERVICE = "ci-cli"
KEYRING_ACCOUNT = "bitbucket-api-key"
CONFIG_KEYS = ("workspace", "username")


class CredentialStoreError(RuntimeError):
    """The OS credential store is missing or refused the request."""


@dataclass
class Config:
    workspace: str | None = None
    username: str | None = None


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ci-cli"


def config_path() -> Path:
    return config_dir() / "config.toml"


def load_config() -> Config:
    path = config_path()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Couldn't read {path}: {exc}") from exc
    return Config(**{k: str(data[k]) for k in CONFIG_KEYS if data.get(k)})


def save_config(config: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = ["# ci-cli settings. The API key is kept in the OS keychain, not here.\n"]
    for key in CONFIG_KEYS:
        value = getattr(config, key)
        if value:
            # A JSON string (ASCII-escaped) is also a valid TOML basic string.
            lines.append(f"{key} = {json.dumps(value)}\n")
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, path)
    return path


def delete_config() -> bool:
    try:
        config_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _keyring():
    try:
        import keyring
        import keyring.errors
    except ImportError as exc:  # pragma: no cover
        raise CredentialStoreError("The 'keyring' package is not installed.") from exc
    return keyring


def get_api_key() -> str | None:
    keyring = _keyring()
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or None
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError(str(exc) or type(exc).__name__) from exc


def set_api_key(api_key: str) -> None:
    keyring = _keyring()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, api_key)
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError(str(exc) or type(exc).__name__) from exc


def delete_api_key() -> bool:
    keyring = _keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise CredentialStoreError(str(exc) or type(exc).__name__) from exc


def keyring_backend_name() -> str:
    try:
        backend = _keyring().get_keyring()
    except Exception:  # noqa: BLE001
        return "unknown"
    return f"{type(backend).__module__}.{type(backend).__name__}"
