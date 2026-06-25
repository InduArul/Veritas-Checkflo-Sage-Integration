"""Run log with a configurable location/sink (specs/07).

Mirrors the Make run-log text (accumulated lines) and persists it to a configurable directory +
filename pattern, or to stdout. No path is hardcoded.
"""
from __future__ import annotations

import os
from datetime import date


class RunLog:
    def __init__(self, log_cfg: dict, run_type: str, when: date):
        self.cfg = log_cfg or {}
        self.run_type = run_type
        self.when = when
        self.lines: list[str] = []
        pattern = self.cfg.get("file_name_pattern", "{run_type}run_log_{date}.txt")
        self.filename = pattern.format(run_type=run_type, date=when.strftime("%m%d%Y"))

    def add(self, message: str) -> None:
        self.lines.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")

    def flush(self):
        """Write the accumulated log. file sink -> returns the path; stdout sink -> prints, returns None."""
        sink = self.cfg.get("sink", "file")
        if sink == "stdout":
            print(self.text, end="")
            return None
        directory = self.cfg.get("dir", ".")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, self.filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.text)
            fh.write("End of process\n")
        return path
