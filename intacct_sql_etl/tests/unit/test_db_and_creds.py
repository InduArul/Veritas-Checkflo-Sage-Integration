"""W3/adapters — SQL connection-string assembly + per-env credential resolution. Test-first.
All values come from config/secrets; nothing hardcoded."""
import pytest

from intacct_etl import config, db


# ---- connection string (config-driven, no driver needed) ----
def test_conn_string_sql_auth():
    sql = {"server": "myhost\\SQL1", "database": "Intacct", "auth_mode": "sql",
           "username": "etl_user", "driver": "ODBC Driver 18 for SQL Server",
           "password_secret_key": "SQL_PW"}
    cs = db.build_connection_string(sql, secrets={"SQL_PW": "p@ss"})
    assert "SERVER=myhost\\SQL1" in cs
    assert "DATABASE=Intacct" in cs
    assert "UID=etl_user" in cs and "PWD=p@ss" in cs
    assert "ODBC Driver 18 for SQL Server" in cs


def test_conn_string_windows_auth_has_no_password():
    sql = {"server": "h", "database": "Intacct", "auth_mode": "windows"}
    cs = db.build_connection_string(sql, secrets={})
    assert "Trusted_Connection=yes" in cs
    assert "PWD=" not in cs


def test_conn_string_missing_server_raises():
    with pytest.raises(config.ConfigError):
        db.build_connection_string({"database": "Intacct", "auth_mode": "windows"}, secrets={})


def test_conn_string_appends_odbc_extra():
    sql = {"server": "h", "database": "d", "auth_mode": "windows",
           "odbc_extra": "Encrypt=yes;TrustServerCertificate=yes"}
    cs = db.build_connection_string(sql, secrets={})
    assert "Encrypt=yes" in cs


# ---- per-environment credential resolution (secret store; pattern from config) ----
def test_resolve_env_creds_from_secret_pattern():
    cfg = {"sage": {"url": "https://sage", "secret_key_pattern": "{key}_{field}"}}
    env = {"name": "Evergreen", "sage_creds_key": "Prod_Sage_Evergreen"}
    secrets = {
        "Prod_Sage_Evergreen_SENDER_ID": "snd",
        "Prod_Sage_Evergreen_SENDER_PASSWORD": "sp",
        "Prod_Sage_Evergreen_USER_ID": "usr",
        "Prod_Sage_Evergreen_USER_PASSWORD": "up",
        "Prod_Sage_Evergreen_COMPANY_ID": "co",
    }
    creds = config.resolve_env_creds(cfg, env, secrets)
    assert creds["sender_id"] == "snd"
    assert creds["sender_password"] == "sp"
    assert creds["user_id"] == "usr"
    assert creds["user_password"] == "up"
    assert creds["company_id"] == "co"


def test_resolve_env_creds_env_plaintext_overrides_for_nonsecret():
    cfg = {"sage": {"url": "https://sage", "secret_key_pattern": "{key}_{field}"}}
    env = {"name": "X", "sage_creds_key": "K", "company_id": "CO_INLINE", "sender_id": "SND_INLINE"}
    secrets = {"K_SENDER_PASSWORD": "sp", "K_USER_ID": "u", "K_USER_PASSWORD": "up"}
    creds = config.resolve_env_creds(cfg, env, secrets)
    assert creds["company_id"] == "CO_INLINE"   # inline non-secret wins, no secret needed
    assert creds["sender_id"] == "SND_INLINE"
    assert creds["sender_password"] == "sp"      # still from secret store


def test_resolve_env_creds_missing_secret_raises():
    cfg = {"sage": {"url": "u", "secret_key_pattern": "{key}_{field}"}}
    env = {"name": "X", "sage_creds_key": "K"}
    with pytest.raises(config.ConfigError):
        config.resolve_env_creds(cfg, env, secrets={})
