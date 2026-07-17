"""Email notifications (specs/14 Part B): failure alert + daily success report.

Best-effort — a notification failure NEVER raises or changes the run's exit code (data outcome ≠
notification outcome). Transport (graph|smtp) and recipient source (config|SharePoint) are swappable
and injectable; the real Graph/SharePoint/SMTP clients are built lazily so unit tests need no network.
Messages render from the RunAudit (specs/12) — no secrets (FR-AUD-8). Recipients: `Config.xlsx`
Sheet1/Table1 later; a local cache + fallback admin guarantee a failure alert still goes out if the
SharePoint read fails.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from .config import ConfigError, resolve_secret

log = logging.getLogger(__name__)

SUBJECT_PREFIX = "[Intacct→SQL]"


# ---------------- message rendering (pure; from RunAudit) ----------------
def _d(audit):
    return audit.to_dict() if audit is not None else {}


def build_success_report(audit):
    d = _d(audit)
    rt = d.get("run_type", "run")
    t = d.get("totals", {})
    w = d.get("window", {})
    subject = (f"{SUBJECT_PREFIX} ✅ {rt} OK — "
               f"{t.get('loaded', 0)} upserted, {t.get('deleted', 0)} swept")
    lines = [
        f"Run {d.get('run_id', '')} ({rt}) window {w.get('start', '')}..{w.get('end', '')}",
        f"Totals: read={t.get('read', 0)} upserted={t.get('loaded', 0)} "
        f"deleted={t.get('deleted', 0)} failed={t.get('failed', 0)}",
        "",
    ]
    for e in d.get("environments", []):
        lines.append(
            f"- {e['name']}: GL {e.get('gl_loaded', 0)} up / {e.get('gl_deleted', 0)} del ; "
            f"AP {e.get('ap_loaded', 0)} up / {e.get('ap_deleted', 0)} del ; status={e.get('status')}"
        )
    return subject, "\n".join(lines)


def build_failure_alert(audit):
    d = _d(audit)
    rt = d.get("run_type", "run")
    failed = [e for e in d.get("environments", [])
              if e.get("status") != "ok" or e.get("error") or e.get("gl_errors") or e.get("ap_errors")]
    names = ", ".join(e["name"] for e in failed) or "run"
    subject = f"{SUBJECT_PREFIX} ❌ FAILED — {names}"
    lines = [f"Run {d.get('run_id', '')} ({rt}) FAILED/degraded.", ""]
    for e in failed:
        errs = (e.get("error")
                or "; ".join(list(e.get("gl_errors", [])) + list(e.get("ap_errors", [])))
                or "degraded")
        lines.append(f"- {e['name']}: {errs}")
    lines += ["", "See the audit trail JSON for full detail."]
    return subject, "\n".join(lines)


def build_preflight_alert(error: str):
    subject = f"{SUBJECT_PREFIX} ❌ FAILED — SQL pre-flight"
    body = (
        "The Intacct→SQL run did not start: the SQL connection pre-flight failed.\n\n"
        f"Error: {error}\n\n"
        "The SQL password may have expired or been rotated. Update the secret in the secret store "
        "(Key Vault secret 'sql-password' / .env SQL_PASSWORD) and re-run."
    )
    return subject, body


# ---------------- recipient sources ----------------
class ConfigRecipientSource:
    def __init__(self, recipients=None):
        self._recipients = list(recipients or [])

    def list(self):
        return list(self._recipients)


class FileRecipientSource:
    """Reads the recipient list from a static text file (one email per line; blank lines and lines
    starting with '#' ignored). Lets ops change WHO gets alerts by editing ONE file — no config edit,
    no code change, no redeploy (people-independent). Wrapped in CachingRecipientSource so a bad/missing
    file still falls back to the last-known-good list, then the fallback admin."""

    def __init__(self, path):
        if not path:
            raise ConfigError("notify.recipients_file is required for recipient_source 'file'")
        self._path = path

    def list(self):
        out = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s and not s.startswith("#"):
                    out.append(s)
        return out


class SharePointRecipientSource:
    """Reads the recipient list via an injected `fetch() -> list[str]` (real Graph client is lazy)."""

    def __init__(self, fetch: Callable[[], list]):
        self._fetch = fetch

    def list(self):
        return list(self._fetch())


class CachingRecipientSource:
    """Wrap a primary source: cache the last-known-good list; on failure fall back to cache, then
    the fallback admin — so a failure alert is never blocked by a SharePoint outage (specs/14 §14.8)."""

    def __init__(self, primary, cache_path: Optional[str] = None, fallback_admin: Optional[str] = None,
                 *, load: Optional[Callable] = None, save: Optional[Callable] = None):
        self._primary = primary
        self._cache_path = cache_path
        self._fallback_admin = fallback_admin
        self._load = load or self._file_load
        self._save = save or self._file_save

    def _file_load(self):
        if not self._cache_path:
            return None
        try:
            with open(self._cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _file_save(self, value):
        if not self._cache_path:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._cache_path) or ".", exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as fh:
                json.dump(value, fh)
        except Exception as exc:
            log.warning("could not write recipient cache: %s", exc)

    def list(self):
        try:
            live = self._primary.list()
            if live:
                self._save(live)
                return list(live)
        except Exception as exc:
            log.warning("recipient source failed, falling back to cache/admin: %s", exc)
        cached = self._load()
        if cached:
            return list(cached)
        return [self._fallback_admin] if self._fallback_admin else []


# ---------------- transports ----------------
class SmtpTransport:
    """SMTP-AUTH transport (stdlib smtplib). Built lazily from config + secrets."""

    def __init__(self, host, port, username, password, *, use_tls=True):
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._use_tls = use_tls

    def send(self, from_addr, recipients, subject, body):
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP(self._host, self._port) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.sendmail(from_addr, recipients, msg.as_string())


class GraphTransport:
    """Microsoft Graph `sendMail` transport (app-only client-credentials).

    Acquires an app token (client_id + tenant + client secret resolved by key from the secret store —
    never in YAML) and POSTs `/users/{sender}/sendMail`. `requests` is imported lazily and the HTTP
    layer / token fetch are injectable so unit tests need no network or SDK. Construction NEVER fails
    (secrets resolve at send time); `send()` may raise and callers use it best-effort, so a mail
    problem can never crash a run. Closes #8 without SMTP AUTH (which the tenant disables)."""

    _AUTH_HOST = "https://login.microsoftonline.com"
    _GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    _SCOPE = "https://graph.microsoft.com/.default"

    def __init__(self, from_address=None, secrets=None, *, client_id=None, tenant_id=None,
                 client_secret_key=None, save_to_sent_items=False, timeout=30,
                 content_type="Text", http_post=None, get_token=None, **_ignored):
        self._from = from_address
        self._secrets = secrets
        self._client_id = client_id
        self._tenant_id = tenant_id
        self._client_secret_key = client_secret_key
        self._save_to_sent = bool(save_to_sent_items)
        self._timeout = timeout
        self._content_type = content_type
        self._http_post = http_post      # tests inject: (url, **kw) -> resp with .json()/.raise_for_status()
        self._get_token = get_token      # tests inject: () -> access_token

    def _post(self, url, **kw):
        if self._http_post is not None:
            return self._http_post(url, **kw)
        import requests  # noqa: WPS433 (lazy: keeps unit tests network-free)
        return requests.post(url, timeout=self._timeout, **kw)

    def _token(self):
        if self._get_token is not None:
            return self._get_token()
        if not (self._client_id and self._tenant_id and self._client_secret_key):
            raise ConfigError(
                "graph transport needs graph.client_id, graph.tenant_id, graph.client_secret_key"
            )
        secret = resolve_secret(self._client_secret_key, env=self._secrets)
        resp = self._post(
            f"{self._AUTH_HOST}/{self._tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._client_id,
                "client_secret": secret,
                "grant_type": "client_credentials",
                "scope": self._SCOPE,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def send(self, from_addr, recipients, subject, body):
        sender = from_addr or self._from
        if not sender:
            raise ConfigError("graph transport needs a from_address (sender mailbox)")
        token = self._token()
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": self._content_type, "content": body},
                "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
            },
            "saveToSentItems": self._save_to_sent,
        }
        resp = self._post(
            f"{self._GRAPH_BASE}/users/{sender}/sendMail",
            json=message,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()


def _build_transport(cfg, secrets, transport=None):
    if transport is not None:
        return transport
    kind = (cfg.get("transport") or "graph").lower()
    if kind == "smtp":
        s = cfg.get("smtp", {}) or {}
        from .config import resolve_secret
        pwd = resolve_secret(s["password_secret_key"], env=secrets) if s.get("password_secret_key") else None
        return SmtpTransport(s.get("host"), s.get("port", 587), s.get("username"), pwd,
                             use_tls=s.get("use_tls", True))
    if kind == "graph":
        return GraphTransport(from_address=cfg.get("from_address"), secrets=secrets, **(cfg.get("graph", {}) or {}))
    raise ConfigError(f"unknown notify.transport: {kind}")


def _build_recipient_source(cfg, secrets, recipient_source=None):
    if recipient_source is not None:
        return recipient_source
    kind = (cfg.get("recipient_source") or "config").lower()
    if kind == "config":
        primary = ConfigRecipientSource(cfg.get("recipients"))
    elif kind == "file":
        primary = FileRecipientSource(cfg.get("recipients_file"))
    elif kind == "sharepoint":
        raise ConfigError("recipient_source 'sharepoint' needs Graph access (C6) — inject a source "
                          "or use 'config' until then")
    else:
        raise ConfigError(f"unknown notify.recipient_source: {kind}")
    return CachingRecipientSource(primary, cache_path=cfg.get("cache_path"),
                                  fallback_admin=cfg.get("fallback_admin"))


# ---------------- notifier ----------------
class Notifier:
    def __init__(self, transport, recipient_source, from_address, events=None):
        self.transport = transport
        self.recipient_source = recipient_source
        self.from_address = from_address
        self.events = events or {"on_success": True, "on_failure": True}

    def notify(self, result):
        """Send the success report or failure alert for a completed run. Best-effort."""
        try:
            ok = getattr(result, "ok", True)
            if ok and not self.events.get("on_success", True):
                return
            if not ok and not self.events.get("on_failure", True):
                return
            audit = getattr(result, "audit", None)
            subject, body = build_success_report(audit) if ok else build_failure_alert(audit)
            self._send(subject, body)
        except Exception as exc:                    # never let a notification break the run
            log.error("notification failed (non-fatal): %s", exc)

    def alert(self, subject, body):
        """Send an ad-hoc alert (e.g. pre-flight failure). Best-effort."""
        try:
            self._send(subject, body)
        except Exception as exc:
            log.error("alert failed (non-fatal): %s", exc)

    def _send(self, subject, body):
        recipients = self.recipient_source.list()
        if not recipients:
            log.warning("no notification recipients resolved; skipping send of %r", subject)
            return
        self.transport.send(self.from_address, recipients, subject, body)


def from_config(notify_cfg, secrets, *, transport=None, recipient_source=None):
    """Build a Notifier from the `notify` config, or None if disabled. Deps injectable for tests."""
    cfg = notify_cfg or {}
    if not cfg.get("enabled", False):
        return None
    rs = _build_recipient_source(cfg, secrets, recipient_source)
    tr = _build_transport(cfg, secrets, transport)
    return Notifier(tr, rs, cfg.get("from_address", ""), cfg.get("events"))
