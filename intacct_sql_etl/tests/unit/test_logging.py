"""W3/adapters — run log with a CONFIGURABLE location (specs/07). Test-first."""
from datetime import date

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


def test_run_log_stdout_sink_does_not_write_file(tmp_path, capsys):
    log_cfg = {"sink": "stdout", "dir": str(tmp_path),
               "file_name_pattern": "{run_type}run_log_{date}.txt"}
    rl = logging_setup.RunLog(log_cfg, run_type="daily", when=date(2026, 6, 24))
    rl.add("hello")
    assert rl.flush() is None
    assert "hello" in capsys.readouterr().out
