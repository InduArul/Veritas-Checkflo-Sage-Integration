"""Audit trail — structured, analyzable run/step metrics (specs/12).

Produces one machine-readable record per run: counts (read/loaded/affected/failed), per-step
durations (extract/transform/load + wipe/reconcile), timestamps, and status — per environment and
rolled up. Pure/serializable; the I/O sink is config-driven and never blocks the run (FR-AUD-7).
No secrets are recorded (FR-AUD-8).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Stopwatch:
    """Context manager that records elapsed seconds from an injected monotonic clock."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self.elapsed = 0.0

    def __enter__(self) -> "Stopwatch":
        self._start = self._clock()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = self._clock() - self._start


@dataclass
class EnvAudit:
    name: str
    status: str = "ok"                  # ok | degraded | failed
    gl_read: int = 0
    ap_read: int = 0
    gl_loaded: int = 0
    ap_loaded: int = 0
    gl_rows_affected: int = 0
    ap_rows_affected: int = 0
    gl_rows_affected_known: bool = True   # W8/OQ-15: False = proc didn't report a count (not a real 0)
    ap_rows_affected_known: bool = True
    gl_pages: int = 0
    ap_pages: int = 0
    gl_deleted: int = 0                  # specs/13 delete-sweep counts
    ap_deleted: int = 0
    gl_deleted_keys: list = field(default_factory=list)   # W8: WHICH keys were swept (auditability)
    ap_deleted_keys: list = field(default_factory=list)
    failed: int = 0
    extract_s: float = 0.0
    transform_s: float = 0.0
    load_s: float = 0.0
    wipe_s: float = 0.0
    reconcile_s: float = 0.0
    duration_s: float = 0.0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    rollback_error: Optional[str] = None
    gl_errors: list = field(default_factory=list)
    ap_errors: list = field(default_factory=list)

    @property
    def total_read(self) -> int:
        return self.gl_read + self.ap_read

    @property
    def total_loaded(self) -> int:
        return self.gl_loaded + self.ap_loaded

    @property
    def total_affected(self) -> int:
        return self.gl_rows_affected + self.ap_rows_affected

    @property
    def total_deleted(self) -> int:
        return self.gl_deleted + self.ap_deleted

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_read"] = self.total_read
        d["total_loaded"] = self.total_loaded
        d["total_affected"] = self.total_affected
        d["total_deleted"] = self.total_deleted
        return d


@dataclass
class RunAudit:
    run_id: str
    run_type: str
    window_start: str
    window_end: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: float = 0.0
    environments: list = field(default_factory=list)        # list[EnvAudit]

    @property
    def ok(self) -> bool:
        return all(e.status == "ok" for e in self.environments)

    def totals(self) -> dict:
        return {
            "environments": len(self.environments),
            "read": sum(e.total_read for e in self.environments),
            "loaded": sum(e.total_loaded for e in self.environments),
            "rows_affected": sum(e.total_affected for e in self.environments),
            "deleted": sum(e.total_deleted for e in self.environments),
            "failed": sum(e.failed for e in self.environments),
            "gl_read": sum(e.gl_read for e in self.environments),
            "ap_read": sum(e.ap_read for e in self.environments),
        }

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "run_type": self.run_type,
            "window": {"start": self.window_start, "end": self.window_end},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "ok": self.ok,
            "totals": self.totals(),
            "environments": [e.to_dict() for e in self.environments],
        }


def write_audit(run_audit: RunAudit, audit_cfg: dict, *, opener=open) -> Optional[str]:
    """Persist the audit record per the `audit` config (specs/12 FR-AUD-6). Returns the path
    written (file sink) or None (disabled / stdout). Never raises (FR-AUD-7)."""
    cfg = audit_cfg or {}
    if not cfg.get("enabled", True):
        return None
    payload = run_audit.to_dict()
    try:
        if cfg.get("sink", "file") == "stdout":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return None
        directory = cfg.get("dir", "./audit")
        os.makedirs(directory, exist_ok=True)
        if cfg.get("format", "json") == "jsonl":
            path = os.path.join(directory, cfg.get("jsonl_file_name", "audit_trail.jsonl"))
            with opener(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            pattern = cfg.get("file_name_pattern", "audit_{run_type}_{run_id}.json")
            name = pattern.format(run_id=run_audit.run_id, run_type=run_audit.run_type)
            path = os.path.join(directory, name)
            with opener(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
        return path
    except Exception as exc:                                # FR-AUD-7: never block the run
        log.error("failed to write audit trail: %s", exc)
        return None


def write_deletes_log(run_audit: RunAudit, audit_cfg: dict, *, opener=open) -> Optional[str]:
    """W8 observability: a dedicated, greppable trail of WHICH keys the delete-sweep removed —
    one JSON line per key `{run_id, env, object, key, at}`. Complements the count-only run log
    (specs/07 FR-17) so a delete is identifiable/verifiable after the fact. Returns the path
    written, or None when there were no deletes / audit disabled. Never raises (FR-AUD-7)."""
    cfg = audit_cfg or {}
    if not cfg.get("enabled", True):
        return None
    try:
        rows = []
        for e in getattr(run_audit, "environments", []):
            for obj, keys in (("GL", getattr(e, "gl_deleted_keys", None)),
                              ("AP", getattr(e, "ap_deleted_keys", None))):
                for key in keys or []:
                    rows.append({"run_id": run_audit.run_id, "env": e.name, "object": obj,
                                 "key": key, "at": run_audit.finished_at})
        if not rows:
            return None
        directory = cfg.get("deletes_dir", cfg.get("dir", "./audit"))
        os.makedirs(directory, exist_ok=True)
        name = cfg.get("deletes_file_pattern", "deletes_{run_id}.jsonl").format(run_id=run_audit.run_id)
        path = os.path.join(directory, name)
        with opener(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:                                # FR-AUD-7: never block the run
        log.error("failed to write deletes log: %s", exc)
        return None
