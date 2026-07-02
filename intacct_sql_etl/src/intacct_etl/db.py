"""SQL Server connection factory (specs/04, specs/06). Fully config-driven.

`build_connection_string` is pure (testable without a driver). `connect` imports pyodbc lazily.
Auth modes: `sql` (UID/PWD), `windows` (Trusted_Connection), `aad` (Authentication=...).
"""
from __future__ import annotations

from .config import ConfigError, resolve_secret

DEFAULT_DRIVER = "ODBC Driver 17 for SQL Server"


def build_connection_string(sql_cfg: dict, secrets=None) -> str:
    server = sql_cfg.get("server")
    database = sql_cfg.get("database")
    if not server:
        raise ConfigError("sql.server is required")
    if not database:
        raise ConfigError("sql.database is required")
    driver = sql_cfg.get("driver", DEFAULT_DRIVER)
    auth = (sql_cfg.get("auth_mode") or "sql").lower()

    parts = [f"DRIVER={{{driver}}}", f"SERVER={server}", f"DATABASE={database}"]

    if auth == "windows":
        parts.append("Trusted_Connection=yes")
    elif auth == "sql":
        user = sql_cfg.get("username")
        if not user:
            raise ConfigError("sql.username is required for auth_mode 'sql'")
        pw = resolve_secret(sql_cfg.get("password_secret_key", "SQL_PASSWORD"), env=secrets)
        parts.append(f"UID={user}")
        parts.append(f"PWD={pw}")
    elif auth == "aad":
        # Azure AD (e.g. ActiveDirectoryPassword / ActiveDirectoryInteractive); method from config.
        parts.append(f"Authentication={sql_cfg.get('aad_authentication', 'ActiveDirectoryInteractive')}")
        if sql_cfg.get("username"):
            parts.append(f"UID={sql_cfg['username']}")
            if sql_cfg.get("password_secret_key"):
                parts.append(f"PWD={resolve_secret(sql_cfg['password_secret_key'], env=secrets)}")
    else:
        raise ConfigError(f"unknown sql.auth_mode: {auth}")

    extra = sql_cfg.get("odbc_extra")
    if extra:
        parts.append(extra)
    return ";".join(parts) + ";"


def connect(sql_cfg: dict, secrets=None):
    """Open a live SQL Server connection (pyodbc imported lazily)."""
    import pyodbc  # noqa: WPS433 (lazy: unit tests don't need the driver)

    return pyodbc.connect(build_connection_string(sql_cfg, secrets))


def preflight(sql_cfg: dict, secrets=None, *, connect=connect):
    """Read-only SQL connectivity check (specs/14 §14.4).

    Resolves the SQL secret, opens a connection and runs `SELECT 1`. Returns None on success, else a
    short error string (used to fail fast with a specific 'password may have expired' alert). Never
    raises — connection is always closed.
    """
    conn = None
    try:
        conn = connect(sql_cfg, secrets)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return None
    except Exception as exc:                        # surfaced to the caller, not swallowed
        return str(exc) or exc.__class__.__name__
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                       # best-effort close; the primary error already returned
                pass
