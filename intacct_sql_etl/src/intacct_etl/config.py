"""Config loading/validation + secret resolution + test-target guard (specs/06).

Secrets are NEVER read from the YAML; they are resolved by key from the environment
(or a .env loaded into the environment). Non-secret defaults live in the YAML.
"""
from __future__ import annotations

import os
import re

import yaml

REQUIRED_SECTIONS = ("run", "environments", "sage", "gl", "ap", "sql", "retry", "log")

# SQL config keys that name a table or stored procedure (must be test_-prefixed in test mode).
_SQL_TARGET_KEYS = (
    "gl_table",
    "apbill_table",
    "watermark_table",           # specs/13: delta watermark control table
    "proc_add_gl",
    "proc_add_ap",
    "proc_sync_links",
    "proc_update_vendor",
)


class ConfigError(Exception):
    """Raised on invalid configuration or a missing secret."""


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config root must be a mapping")
    missing = [s for s in REQUIRED_SECTIONS if s not in cfg]
    if missing:
        raise ConfigError(f"missing required config section(s): {', '.join(missing)}")
    envs = cfg.get("environments")
    if not isinstance(envs, list) or len(envs) == 0:
        raise ConfigError("config 'environments' must be a non-empty list")
    for i, env in enumerate(envs):
        if not isinstance(env, dict) or "name" not in env or "sage_creds_key" not in env:
            raise ConfigError(
                f"environments[{i}] must have 'name' and 'sage_creds_key'"
            )


def resolve_secret(name: str, env=None) -> str:
    # specs/14: `env` may be a SecretProvider (duck-typed) — delegate to it. Otherwise treat it as a
    # plain dict / os.environ (legacy path, unchanged).
    if getattr(env, "is_secret_provider", False):
        return env.get(name)
    env = os.environ if env is None else env
    val = env.get(name)
    if val is None or val == "":
        raise ConfigError(f"secret '{name}' not found in environment")
    return val


def _bare_object_name(qualified: str) -> str:
    """Last identifier of a (possibly schema/db-qualified, possibly bracketed) name.

    '[Intacct].[dbo].[GLTable]' -> 'GLTable'; 'dbo.test_GLTable' -> 'test_GLTable'.
    """
    last = qualified.replace("[", "").replace("]", "").split(".")[-1]
    return last.strip()


# Allowed characters for a SQL identifier that we interpolate into SQL text (CR-19).
# Word chars, dots, and brackets only — no spaces, quotes, semicolons, or parentheses.
_IDENT_RE = re.compile(r"^[\w.\[\]]+$")


def safe_identifier(name: str) -> str:
    """Return `name` if it is a safe SQL identifier, else raise ConfigError.

    Identifiers (table/proc/column names) cannot be passed as bound parameters, so they are
    interpolated into the SQL text. They come from config (trusted) but we validate anyway as
    defense-in-depth against accidental/malicious injection.
    """
    if not name or not _IDENT_RE.match(name):
        raise ConfigError(f"unsafe SQL identifier: {name!r}")
    return name


def assert_test_sql_targets(sql_cfg: dict) -> None:
    """Guard: every SQL table/proc target in `sql_cfg` must be test_-prefixed.

    Single shared implementation used by both the config validator and the SqlLoader (CR-09),
    so the safety rule cannot drift between two copies.
    """
    sql = sql_cfg or {}
    offenders = []
    for key in _SQL_TARGET_KEYS:
        if key not in sql:
            continue
        name = _bare_object_name(str(sql[key]))
        if not re.match(r"(?i)^test_", name):
            offenders.append(f"{key}={sql[key]}")
    if offenders:
        raise ConfigError(
            "test mode requires test_-prefixed SQL targets; offending: "
            + ", ".join(offenders)
        )


_CRED_FIELDS = ("sender_id", "sender_password", "user_id", "user_password", "company_id")


def resolve_env_creds(cfg: dict, env: dict, secrets=None) -> dict:
    """Resolve a single environment's Sage credentials (specs/06).

    For each credential field: use an inline value from the env entry if present (handy for
    non-secret ids), else fetch from the secret store using a configurable key pattern
    (default `{key}_{FIELD}`, e.g. `Prod_Sage_Evergreen_SENDER_ID`). Values never live in YAML.
    """
    secrets = os.environ if secrets is None else secrets
    pattern = cfg.get("sage", {}).get("secret_key_pattern", "{key}_{field}")
    key = env["sage_creds_key"]
    creds = {"url": cfg.get("sage", {}).get("url")}
    for field in _CRED_FIELDS:
        if field in env and env[field] not in (None, ""):
            creds[field] = env[field]
        else:
            secret_key = pattern.format(key=key, field=field.upper())
            creds[field] = resolve_secret(secret_key, env=secrets)
    return creds


def is_test_mode(cfg: dict) -> bool:
    return bool(cfg.get("run", {}).get("test_mode", True))


def assert_test_targets(cfg: dict) -> None:
    """Guard: every SQL table/proc target must be test_-prefixed (defense-in-depth
    alongside the prod_write_guard hook). Raises ConfigError on any prod-named target.
    """
    assert_test_sql_targets(cfg.get("sql", {}) or {})
