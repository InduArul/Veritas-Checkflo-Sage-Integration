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


def test_from_config_builds_notifier_with_injected_deps():
    tr = FakeTransport()
    n = notify.from_config({"enabled": True, "from_address": "f@x.com", "recipients": ["a@x.com"]},
                           secrets={}, transport=tr,
                           recipient_source=notify.ConfigRecipientSource(["a@x.com"]))
    assert n is not None
    n.notify(Result(ok=False, audit=_audit(False)))
    assert tr.sent and ("❌" in tr.sent[0][2] or "FAIL" in tr.sent[0][2].upper())
