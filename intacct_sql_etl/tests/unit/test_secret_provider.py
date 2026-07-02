"""specs/14 Part A — secret provider (env now / Key Vault later) + resolve_secret support.
Test-first; offline (no azure SDK — Key Vault fetch is injected)."""
import pytest

from intacct_etl import secret_provider as sp
from intacct_etl.config import ConfigError, resolve_secret


def test_env_provider_returns_value():
    p = sp.EnvSecretProvider({"A": "1"})
    assert p.get("A") == "1"


def test_env_provider_missing_or_empty_raises():
    p = sp.EnvSecretProvider({"E": ""})
    with pytest.raises(ConfigError):
        p.get("E")
    with pytest.raises(ConfigError):
        p.get("ABSENT")


def test_keyvault_provider_maps_logical_name():
    seen = {}

    def fetch(kv_name):
        seen["name"] = kv_name
        return "secretval"

    p = sp.KeyVaultSecretProvider(fetch, name_map={"SQL_PASSWORD": "sql-password"})
    assert p.get("SQL_PASSWORD") == "secretval"
    assert seen["name"] == "sql-password"          # mapped to the KV secret name
    p.get("OTHER")
    assert seen["name"] == "OTHER"                 # unmapped passes through verbatim


def test_from_config_defaults_to_env():
    p = sp.from_config(None, env={"X": "1"})
    assert isinstance(p, sp.EnvSecretProvider)
    assert p.get("X") == "1"


def test_from_config_keyvault_with_injected_fetch():
    p = sp.from_config({"provider": "keyvault", "keyvault": {"name_map": {"SQL_PASSWORD": "sql-password"}}},
                       env={}, kv_fetch=lambda n: "v")
    assert p.get("SQL_PASSWORD") == "v"


def test_from_config_unknown_provider_raises():
    with pytest.raises(ConfigError):
        sp.from_config({"provider": "vault-o-matic"}, env={})


def test_resolve_secret_accepts_a_provider():
    p = sp.EnvSecretProvider({"K": "val"})
    assert resolve_secret("K", env=p) == "val"
    with pytest.raises(ConfigError):
        resolve_secret("MISSING", env=p)


def test_resolve_secret_dict_path_unchanged():
    assert resolve_secret("K", env={"K": "v"}) == "v"
    with pytest.raises(ConfigError):
        resolve_secret("K", env={"K": ""})
