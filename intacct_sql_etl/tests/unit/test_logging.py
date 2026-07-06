"""W3/adapters — run log with a CONFIGURABLE location (specs/07). Test-first."""
import os
from datetime import date, datetime

from intacct_etl import logging_setup


def test_run_log_writes_to_configured_location(tmp_path):
    log_cfg = {"sink": "file", "dir": str(tmp_path),
               "file_name_pattern": "{run_type}run_log_{date}.txt"}
    rl = logging_setup.RunLog(log_cfg, run_type="daily", when=date(2026, 6, 24))
    rl.add("Start of process")
    rl.add('Processing data for "Evergreen" environment')
    path = rl.flush()
    assert path.endswith("dailyrun_log_06242026.txt")
    assert str(tmp_path) in path
    text = open(path, encoding="utf-8").read()
    assert "Start of process" in text
    assert "Evergreen" in text


def test_run_log_filename_uses_run_type_and_date(tmp_path):
    log_cfg = {"sink": "file", "dir": str(tmp_path),
               "file_name_pattern": "{run_type}run_log_{date}.txt"}
    rl = logging_setup.RunLog(log_cfg, run_type="monthly", when=date(2026, 1, 5))
    assert rl.filename == "monthlyrun_log_01052026.txt"


def test_run_log_appends_live_before_flush(tmp_path):
    # OQ-34: each add() lands on disk immediately so a long run is tail-able mid-run.
    cfg = {"sink": "file", "dir": str(tmp_path), "file_name_pattern": "{run_type}run_log_{date}.txt"}
    rl = logging_setup.RunLog(cfg, run_type="daily", when=date(2026, 6, 24))
    rl.add("Evergreen GL upsert chunk 1: +200")
    path = os.path.join(str(tmp_path), "dailyrun_log_06242026.txt")
    assert "Evergreen GL upsert chunk 1: +200" in open(path, encoding="utf-8").read()  # BEFORE flush
    rl.add("Evergreen GL upsert chunk 2: +180")
    body = open(rl.flush(), encoding="utf-8").read()
    assert "chunk 1" in body and "chunk 2" in body and "End of process" in body


def test_run_log_lines_are_timestamped(tmp_path):
    fixed = datetime(2026, 6, 24, 8, 30, 0)
    rl = logging_setup.RunLog({"sink": "file", "dir": str(tmp_path)}, "daily",
                              date(2026, 6, 24), now=lambda: fixed)
    rl.add("hello")
    assert "2026-06-24T08:30:00 hello" in rl.text


def test_run_log_stdout_sink_does_not_write_file(tmp_path, capsys):
    log_cfg = {"sink": "stdout", "dir": str(tmp_path),
               "file_name_pattern": "{run_type}run_log_{date}.txt"}
    rl = logging_setup.RunLog(log_cfg, run_type="daily", when=date(2026, 6, 24))
    rl.add("hello")
    assert rl.flush() is None
    assert "hello" in capsys.readouterr().out
