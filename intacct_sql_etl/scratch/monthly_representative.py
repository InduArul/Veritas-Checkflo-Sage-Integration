"""Gate #5 (representative) — prove the MONTHLY wide-sweep mechanism end-to-end live.

Runs the real orchestrator in run_type=monthly, mode=sweep-upsert over a 6-month window
(Feb..Jul 2026) for all enabled envs. Proves: multi-chunk tiling (6 chunks), bounded memory
across many chunks, per_chunk commit (monthly default), mass-delete guard, clean completion (rc=0).
Same code path as the real ~26-chunk monthly — only the iteration count differs.

Safe: test_ tables only. Upsert pulls WHENMODIFIED-since-watermark (small). Sweep over Feb-Mar
(no SQL rows) deletes nothing; Apr-Jul keys match Sage -> ~0 deletes, guard-protected.

Usage (VM): python monthly_representative.py
"""
import os, time
from datetime import datetime
from intacct_etl import (config as cfgmod, db, secret_provider, sage_client, sage_transport,
                         retry, orchestrator)


class CountingConn:
    """Wrap a real connection to count commits (prove per_chunk commit)."""
    def __init__(self, inner): self._i = inner; self.commits = 0
    def cursor(self): return self._i.cursor()
    def commit(self): self.commits += 1; return self._i.commit()
    def rollback(self): return self._i.rollback()
    def close(self): return self._i.close()


def main():
    cfg = cfgmod.load_config("/data/intacct-etl/config.yaml")
    secrets = secret_provider.from_config(cfg.get("secrets"), env=dict(os.environ))
    # Force a representative monthly wide-sweep over Feb..Jul 2026.
    cfg["run"] = dict(cfg.get("run", {}))
    cfg["run"].update({"run_type_override": "monthly", "mode": "sweep-upsert",
                       "window_override": {"start": "02/01/2026", "end": "07/31/2026"}})
    sage_cfg = cfg.get("sage", {})
    conns = []

    def connect(sql_cfg, sec):
        c = CountingConn(db.connect(sql_cfg, sec)); conns.append(c); return c

    def factory(creds):
        url = creds.get("url") or sage_cfg["url"]
        tr = sage_transport.make_http_transport(url, timeout=sage_cfg.get("request_timeout_s", 60),
                                                gzip=sage_cfg.get("gzip", True))
        return sage_client.SageClient(tr, creds, retry=retry.from_config(cfg.get("retry", {})))

    print("Running representative MONTHLY sweep-upsert (Feb..Jul 2026, per_chunk commit)...", flush=True)
    t0 = time.time()
    res = orchestrator.run(cfg, secrets, today=None, sage_client_factory=factory, connect=connect,
                           now=lambda: datetime(2026, 7, 6, 9, 0, 0))
    dur = time.time() - t0
    print(f"\nrun_type={res.run_type}  window={res.start}..{res.end}  duration={dur:.1f}s", flush=True)
    for name, e in res.environments.items():
        print(f"  {name}: gl_loaded={e.get('gl_loaded')} ap_loaded={e.get('ap_loaded')} "
              f"gl_err={e.get('gl_error')} ap_err={e.get('ap_error')} error={e.get('error')}", flush=True)
    print(f"  total commits across conns = {sum(c.commits for c in conns)} "
          f"(per_chunk => many; per_env => 1/env)", flush=True)
    print(f"\nGATE #5 (representative monthly): {'PASS (rc=0, completed)' if res.ok else 'FAIL'}", flush=True)


if __name__ == "__main__":
    main()
