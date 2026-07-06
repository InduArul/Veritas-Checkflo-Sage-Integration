"""Run log with a configurable location/sink (specs/07).

Mirrors the Make run-log text (accumulated lines) and persists it to a configurable directory +
filename pattern, or to stdout. No path is hardcoded.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Callable


class RunLog:
    """A timestamped run log that updates LIVE (specs/07, OQ-34).

    Each `add()` timestamps the line and — for a file sink — appends it to the log file
    immediately, so a long run is tail-able (`tail -f`) and progress survives a crash. The file
    is created fresh at construction; `flush()` finalises it with an End-of-process line.
    """

    def __init__(self, log_cfg: dict, run_type: str, when: date,
                 now: Callable[[], datetime] | None = None):
        self.cfg = log_cfg or {}
        self.run_type = run_type
        self.when = when
        self._now = now or datetime.now
        self.lines: list[str] = []
        pattern = self.cfg.get("file_name_pattern", "{run_type}run_log_{date}.txt")
        self.filename = pattern.format(run_type=run_type, date=when.strftime("%m%d%Y"))
        self._path = None
        if self.cfg.get("sink", "file") != "stdout":
            directory = self.cfg.get("dir", ".")
            os.makedirs(directory, exist_ok=True)
            self._path = os.path.join(directory, self.filename)
            open(self._path, "w", encoding="utf-8").close()      # start this run's log fresh

    def add(self, message: str) -> None:
        line = f"{self._now().strftime('%Y-%m-%dT%H:%M:%S')} {message}"
        self.lines.append(line)
        if self._path:                                           # live append -> tail-able mid-run
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")

    def flush(self):
        """Finalise the log. file sink -> append End-of-process, return path; stdout -> print, None."""
        if self.cfg.get("sink", "file") == "stdout":
            print(self.text, end="")
            return None
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(f"{self._now().strftime('%Y-%m-%dT%H:%M:%S')} End of process\n")
        return self._path
