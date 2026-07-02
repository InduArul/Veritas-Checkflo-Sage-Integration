"""Secret provider (specs/14 Part A). Swappable: env/.env now, Azure Key Vault later.

Secrets are resolved at runtime INTO MEMORY — never written to disk (specs/06). `config.resolve_secret`
accepts either a plain dict (legacy) or a provider (duck-typed via `is_secret_provider`), so switching
providers is config-only and existing behaviour is unchanged.

Rotation goal (user): the team updates one Key Vault secret; the next run picks it up. The VM's managed
identity reads the vault (no bootstrap secret to store/rotate).
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from .config import ConfigError


class EnvSecretProvider:
    """Reads secrets from the process environment / `.env` (default provider — as today)."""

    is_secret_provider = True

    def __init__(self, env: Optional[dict] = None):
        self._env = os.environ if env is None else env

    def get(self, name: str) -> str:
        val = self._env.get(name)
        if val is None or val == "":
            raise ConfigError(f"secret '{name}' not found in environment")
        return val


class KeyVaultSecretProvider:
    """Reads secrets from Azure Key Vault via an injected `fetch(kv_name) -> value` callable.

    `name_map` translates logical keys (e.g. `SQL_PASSWORD`) to KV secret names (`sql-password`);
    unmapped names pass through verbatim. The Azure client is built lazily in `from_config` so unit
    tests need no SDK.
    """

    is_secret_provider = True

    def __init__(self, fetch: Callable[[str], str], name_map: Optional[dict] = None):
        self._fetch = fetch
        self._map = name_map or {}

    def get(self, name: str) -> str:
        kv_name = self._map.get(name, name)
        val = self._fetch(kv_name)
        if val is None or val == "":
            raise ConfigError(f"secret '{name}' (Key Vault '{kv_name}') not found")
        return val


def _azure_fetch(vault_url: str) -> Callable[[str], str]:
    """Lazily build a Key Vault fetch backed by the VM's managed identity (no stored secret)."""
    if not vault_url:
        raise ConfigError("secrets.keyvault.vault_url is required for provider 'keyvault'")
    from azure.identity import ManagedIdentityCredential  # noqa: WPS433 (lazy: no SDK in tests)
    from azure.keyvault.secrets import SecretClient       # noqa: WPS433

    client = SecretClient(vault_url=vault_url, credential=ManagedIdentityCredential())

    def fetch(kv_name: str) -> str:
        return client.get_secret(kv_name).value

    return fetch


def from_config(secrets_cfg: Optional[dict], env: Optional[dict] = None,
                *, kv_fetch: Optional[Callable[[str], str]] = None):
    """Build the configured provider. Default = env. `kv_fetch` injects the KV fetch (tests)."""
    cfg = secrets_cfg or {}
    provider = (cfg.get("provider") or "env").lower()
    if provider == "env":
        return EnvSecretProvider(env)
    if provider == "keyvault":
        kv = cfg.get("keyvault", {}) or {}
        fetch = kv_fetch or _azure_fetch(kv.get("vault_url"))
        return KeyVaultSecretProvider(fetch, name_map=kv.get("name_map"))
    raise ConfigError(f"unknown secrets.provider: {provider}")
