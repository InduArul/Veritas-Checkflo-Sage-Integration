"""Audit trail — structured run/step metrics (specs/12). Test-first; offline."""
from datetime import datetime

from intacct_etl import audit


def _counter():
    """Monotonic fake clock: returns 1, 2, 3, ... (consecutive diff == 1.0s)."""
    box = {"n": 0}

    def clock():
        box["n"] += 1
        return box["n"]

    return clock


def test_stopwatch_measures_elapsed():
    clock = _counter()
    with audit.Stopwatch(clock) as sw:
        pass
    assert sw.elapsed == 1.0


def test_env_audit_to_dict_has_totals():
    ea = audit.EnvAudit(name="Evergreen", gl_read=10, ap_read=5,
                        gl_loaded=10, ap_loaded=5,
                        gl_rows_affected=8, ap_rows_affected=4)
    d = ea.to_dict()
    assert d["total_read"] == 15
    assert d["total_loaded"] == 15
    assert d["total_affected"] == 12
    assert d["name"] == "Evergreen"


def test_run_audit_to_dict_structure_and_totals():
    ra = audit.RunAudit(run_id="20260625_080000", run_type="daily",
                        window_start="2026-03-01", window_end="2026-06-30",
                        started_at="2026-06-25T08:00:00", finished_at="2026-06-25T08:01:00",
                        duration_s=60.0)
    ra.environments.append(audit.EnvAudit(name="A", gl_read=12, ap_read=8,
                                          gl_loaded=12, ap_loaded=8, failed=0, status="ok"))
    ra.environments.append(audit.EnvAudit(name="B", gl_read=3, ap_read=0,
                                          failed=3, status="failed"))
    d = ra.to_dict()
    assert d["run_id"] == "20260625_080000"
    assert d["window"]["start"] == "2026-03-01"
    assert d["totals"]["read"] == 23
    assert d["totals"]["loaded"] == 20
    assert d["totals"]["failed"] == 3
    assert d["totals"]["environments"] == 2
    assert d["ok"] is False
    assert len(d["environments"]) == 2


def _run_audit():
    ra = audit.RunAudit(run_id="20260625_080000", run_type="daily",
                        window_start="2026-03-01", window_end="2026-06-30")
    ra.environments.append(audit.EnvAudit(name="A", gl_read=12, ap_read=8, status="ok"))
    return ra


def test_write_audit_json_file(tmp_path):
    import json
    cfg = {"enabled": True, "sink": "file", "dir": str(tmp_path), "format": "json",
           "file_name_pattern": "audit_{run_type}_{run_id}.json"}
    path = audit.write_audit(_run_audit(), cfg)
    assert path.endswith("audit_daily_20260625_080000.json")
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["run_id"] == "20260625_080000"
    assert data["environments"][0]["name"] == "A"


def test_write_audit_jsonl_appends(tmp_path):
    cfg = {"enabled": True, "sink": "file", "dir": str(tmp_path), "format": "jsonl",
           "jsonl_file_name": "audit_trail.jsonl"}
    audit.write_audit(_run_audit(), cfg)
    audit.write_audit(_run_audit(), cfg)
    path = tmp_path / "audit_trail.jsonl"
    lines = [ln for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    assert len(lines) == 2                       # appended, one record per line


def test_write_audit_disabled_returns_none(tmp_path):
    cfg = {"enabled": False, "dir": str(tmp_path)}
    assert audit.write_audit(_run_audit(), cfg) is None
    assert list(tmp_path.iterdir()) == []


def test_write_audit_stdout(capsys):
    cfg = {"enabled": True, "sink": "stdout"}
    assert audit.write_audit(_run_audit(), cfg) is None
    assert "20260625_080000" in capsys.readouterr().out


def test_env_audit_surfaces_delete_counts():
    # specs/13: the delete-sweep counts are part of the audit record + rollup
    e = audit.EnvAudit(name="E", gl_deleted=3, ap_deleted=1)
    assert e.total_deleted == 4
    d = e.to_dict()
    assert d["gl_deleted"] == 3 and d["ap_deleted"] == 1 and d["total_deleted"] == 4
    ra = audit.RunAudit(run_id="r", run_type="daily", window_start="a", window_end="b",
                        environments=[e])
    assert ra.totals()["deleted"] == 4
