"""specs/14 Part B — email notifier: message rendering, recipient sources, best-effort send.
Test-first; offline (transport + recipient source injected; no network/files)."""
from intacct_etl import notify
from intacct_etl.audit import EnvAudit, RunAudit


class FakeTransport:
    def __init__(self, raises=False):
        self.sent = []
        self.raises = raises

    def send(self, from_addr, recipients, subject, body):
        if self.raises:
            raise RuntimeError("smtp down")
        self.sent.append((from_addr, recipients, subject, body))


def _audit(ok=True):
    e = EnvAudit(name="Evergreen", status="ok" if ok else "failed",
                 gl_read=5, gl_loaded=5, ap_read=2, ap_loaded=2, gl_deleted=1,
                 error=None if ok else "GL boom")
    return RunAudit(run_id="20260702_090000", run_type="daily",
                    window_start="2026-03-01", window_end="2026-06-30", environments=[e])


class Result:
    def __init__(self, ok, audit):
        self.ok = ok
        self.audit = audit


# ---- message rendering ----
def test_success_report_subject_and_body():
    subj, body = notify.build_success_report(_audit(ok=True))
    assert "✅" in subj or "OK" in subj
    assert "daily" in body.lower() and "Evergreen" in body and "5" in body


def test_failure_alert_mentions_env_and_error():
    subj, body = notify.build_failure_alert(_audit(ok=False))
    assert "❌" in subj or "FAIL" in subj.upper()
    assert "Evergreen" in subj or "Evergreen" in body
    assert "GL boom" in body


def test_preflight_alert_has_remediation_hint():
    subj, body = notify.build_preflight_alert("Login failed for user")
    assert "❌" in subj or "FAIL" in subj.upper()
    assert "Login failed" in body and "password" in body.lower()


# ---- recipient sources ----
def test_config_recipient_source():
    assert notify.ConfigRecipientSource(["a@x.com", "b@x.com"]).list() == ["a@x.com", "b@x.com"]


def test_file_recipient_source_reads_and_ignores_blanks_and_comments(tmp_path):
    p = tmp_path / "recips.txt"
    p.write_text("# alert recipients (edit this file to change who is paged)\n\n"
                 "a@x.com\n   b@x.com   \n# c@x.com  (temporarily disabled)\n", encoding="utf-8")
    assert notify.FileRecipientSource(str(p)).list() == ["a@x.com", "b@x.com"]


def test_from_config_file_recipient_source_is_people_independent(tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("a@x.com\nb@x.com\n", encoding="utf-8")
    n = notify.from_config({"enabled": True, "from_address": "f@x.com", "transport": "graph",
                            "recipient_source": "file", "recipients_file": str(p), "graph": {}},
                           secrets={})
    assert sorted(n.recipient_source.list()) == ["a@x.com", "b@x.com"]
    p.write_text("only@x.com\n", encoding="utf-8")          # edit the file -> no code/config change
    assert n.recipient_source.list() == ["only@x.com"]


def test_caching_source_saves_on_success_then_falls_back_to_cache_then_admin():
    store = {}

    class Primary:
        mode = "ok"

        def list(self):
            if self.mode == "ok":
                return ["live@x.com"]
            raise RuntimeError("sharepoint down")

    p = Primary()
    cs = notify.CachingRecipientSource(p, cache_path=None, fallback_admin="admin@x.com",
                                       load=lambda: store.get("v"), save=lambda v: store.__setitem__("v", v))
    assert cs.list() == ["live@x.com"] and store["v"] == ["live@x.com"]   # live + cached
    p.mode = "fail"
    assert cs.list() == ["live@x.com"]                                    # fall back to cache
    store.clear()
    assert cs.list() == ["admin@x.com"]                                   # fall back to admin


# ---- notifier behaviour ----
def test_notifier_sends_success_to_recipients():
    tr = FakeTransport()
    n = notify.Notifier(tr, notify.ConfigRecipientSource(["a@x.com"]), "from@x.com",
                        events={"on_success": True, "on_failure": True})
    n.notify(Result(ok=True, audit=_audit(True)))
    assert len(tr.sent) == 1
    frm, recips, subj, _ = tr.sent[0]
    assert frm == "from@x.com" and recips == ["a@x.com"]


def test_notifier_respects_event_toggle():
    tr = FakeTransport()
    n = notify.Notifier(tr, notify.ConfigRecipientSource(["a@x.com"]), "f@x.com",
                        events={"on_success": False, "on_failure": True})
    n.notify(Result(ok=True, audit=_audit(True)))
    assert tr.sent == []                                    # success suppressed


def test_notifier_is_best_effort_on_transport_error():
    tr = FakeTransport(raises=True)
    n = notify.Notifier(tr, notify.ConfigRecipientSource(["a@x.com"]), "f@x.com")
    n.notify(Result(ok=False, audit=_audit(False)))         # must NOT raise


def test_notifier_skips_when_no_recipients():
    tr = FakeTransport()
    n = notify.Notifier(tr, notify.ConfigRecipientSource([]), "f@x.com")
    n.notify(Result(ok=True, audit=_audit(True)))
    assert tr.sent == []


# ---- from_config ----
def test_from_config_disabled_returns_none():
    assert notify.from_config({"enabled": False}, secrets={}) is None
    assert notify.from_config(None, secrets={}) is None


def test_from_config_graph_transport_does_not_raise_at_build():
    # FIX #3: building with the default 'graph' transport must NOT raise (would crash the run).
    n = notify.from_config({"enabled": True, "from_address": "f@x.com", "transport": "graph",
                            "recipients": ["a@x.com"]}, secrets={})
    assert n is not None


def test_graph_transport_send_is_best_effort_not_a_crash():
    n = notify.from_config({"enabled": True, "from_address": "f@x.com", "transport": "graph",
                            "recipients": ["a@x.com"]}, secrets={})
    n.notify(Result(ok=True, audit=_audit(True)))      # send may be unimplemented — must be swallowed


# ---- Graph sendMail transport (#8) ----
class FakeResp:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_graph_transport_send_posts_sendmail_with_client_credentials_token():
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        if url.endswith("/oauth2/v2.0/token"):
            return FakeResp({"access_token": "TOK123"})
        return FakeResp({}, status=202)

    tr = notify.GraphTransport(from_address="ikannan@x.com",
                               secrets={"GRAPH_CLIENT_SECRET": "shh"},
                               client_id="cid", tenant_id="tid",
                               client_secret_key="GRAPH_CLIENT_SECRET", http_post=fake_post)
    tr.send("ikannan@x.com", ["a@x.com", "b@x.com"], "subj", "body")

    token_url, token_kw = calls[0]
    assert token_url.endswith("/tid/oauth2/v2.0/token")
    assert token_kw["data"]["grant_type"] == "client_credentials"
    assert token_kw["data"]["client_secret"] == "shh"                     # resolved from secret store
    assert token_kw["data"]["scope"] == "https://graph.microsoft.com/.default"

    send_url, send_kw = calls[1]
    assert send_url.endswith("/users/ikannan@x.com/sendMail")             # send-as the sender mailbox
    assert send_kw["headers"]["Authorization"] == "Bearer TOK123"
    addrs = [r["emailAddress"]["address"] for r in send_kw["json"]["message"]["toRecipients"]]
    assert addrs == ["a@x.com", "b@x.com"]
    assert send_kw["json"]["message"]["subject"] == "subj"


def test_graph_transport_raises_on_http_error_but_notifier_swallows_it():
    def fake_post(url, **kw):
        if url.endswith("/oauth2/v2.0/token"):
            return FakeResp({"access_token": "T"})
        return FakeResp({"error": "forbidden"}, status=403)      # e.g. missing send-as policy

    tr = notify.GraphTransport(from_address="f@x.com", secrets={"K": "s"},
                               client_id="c", tenant_id="t", client_secret_key="K", http_post=fake_post)
    import pytest
    with pytest.raises(Exception):
        tr.send("f@x.com", ["a@x.com"], "s", "b")               # transport surfaces the failure

    n = notify.Notifier(tr, notify.ConfigRecipientSource(["a@x.com"]), "f@x.com")
    n.alert("t", "b")                                           # ...but via the notifier it's best-effort


def test_graph_transport_missing_creds_raises_configerror_at_send_not_build():
    tr = notify.GraphTransport(from_address="f@x.com", secrets={})       # build must not raise
    import pytest
    with pytest.raises(Exception):
        tr.send("f@x.com", ["a@x.com"], "s", "b")               # missing client_id/tenant/secret_key


def test_from_config_builds_notifier_with_injected_deps():
    tr = FakeTransport()
    n = notify.from_config({"enabled": True, "from_address": "f@x.com", "recipients": ["a@x.com"]},
                           secrets={}, transport=tr,
                           recipient_source=notify.ConfigRecipientSource(["a@x.com"]))
    assert n is not None
    n.notify(Result(ok=False, audit=_audit(False)))
    assert tr.sent and ("❌" in tr.sent[0][2] or "FAIL" in tr.sent[0][2].upper())
