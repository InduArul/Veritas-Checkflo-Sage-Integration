# tests/ — Test suite (TDD; parity-gated)

Tests are written **first** (TDD is locked — see `../CLAUDE.md`). Stack: `pytest` + `pyodbc`.
Full protocol in `../specs/11_test_reconciliation.md`.

## Layout
```
tests/
├── unit/             pure logic, no DB/Sage (run always, fast)
│   e.g. run_type/window derivation, month-slicing, page-count math, Sage XML builder, JSON encoding
├── integration/      against test_ DB / Sage sandbox or recorded fixtures   [-m integration]
│   e.g. page → test_GLTable via test_AddGLDetailBulk; transaction rollback leaves test_ tables intact
├── reconciliation/   PARITY GATE: utility test_* vs Make prod for a fixture  [-m reconciliation]
└── fixtures/         stable (env, window) defs + expected metrics (counts/checksums only — no raw PII)
```

## Running
- `pytest tests/unit` — every commit (no external deps).
- `pytest -m integration` — needs `test_` DB / Sage access.
- `pytest -m reconciliation` — the wave Evaluate gate; needs `test_` DB + read access to Make prod.

## Rules
- **Never** point a test at a production table/proc. `test_`-prefixed only (the `prod_write_guard` hook
  hard-blocks prod writes). Reads of prod are allowed for snapshotting Make's golden output.
- A reconciliation mismatch **fails the wave**. Fix code, not the test. Never weaken a test to pass.
- Markers `integration` / `reconciliation` registered in `pytest.ini`/`pyproject.toml` (added at W0).
