# Leak A — Golden Baseline Manifest

- **Captured:** 2026-06-20 (current billing cycle)
- **Git commit at capture:** `77b90d6a4c6acf3eab1b658346ff206f0ff0e300`
- **Source:** live pull via `tools/make_cli.py` (us2, team 33002, org 1903574)
- **Drift check:** committed `Source code/` (Jun 11) == live today for all 8 scenarios (parity_diff HIGH=0, MEDIUM=0)
- **Frozen artifacts:** `baseline/leakA_2026-06-20/` — blueprints (immutable parity_diff reference), `scheduling.json`, `_scenario_meta/`

## Scenario inventory + settings snapshot

| Scenario | ID | Modules | Schedule | Interval | Restrict | maxRuns/min |
|---|---:|---:|---|---|---|---:|
| Part1_MoWeSa | 5127317 | 68 | indefinitely | 10800s (3h) | days [1, 3, 6] ['07:30', '19:30'] | - |
| Part1_TuThFr | 5237201 | 68 | indefinitely | 7200s (2h) | days [2, 4, 5] ['07:30', '19:30'] | - |
| Part2_Main | 5128270 | 50 | immediately | - | - | 100 |
| Part3_Vendor | 5203224 | 37 | immediately | - | - | 100 |
| Part4_NegChecks | 5152139 | 28 | immediately | - | - | 100 |
| Part5_Loop | 5152516 | 69 | immediately | - | - | 100 |
| Part6_Posting | 5171324 | 19 | immediately | - | - | 100 |
| Part7_Reporting | 5171262 | 15 | immediately | - | - | 100 |

> Webhook scenarios (Part2–7) also carry `maxResults: 1` on the trigger — preserve on refactor.

## Operations baseline (must-beat) — current cycle

| Scenario | Operations | Executions | Ops/exec |
|---|---:|---:|---:|
| Part1_MoWeSa | 9,270 | 184 | 50.4 |
| Part1_TuThFr | 5,536 | 60 | 92.3 |
| Part2_Main | 68,428 | 259 | 264.2 |
| Part3_Vendor | 82,685 | 4,831 | 17.1 |
| Part4_NegChecks | 25,330 | 4,946 | 5.1 |
| Part5_Loop | 183,071 | 4,852 | 37.7 |
| Part6_Posting | 34,740 | 3,515 | 9.9 |
| Part7_Reporting | 902 | 133 | 6.8 |
| **Total (Part1–7)** | **409,962** | | |

Target: bring per-check end-to-end cost (~84 ops) below ~55 to clear the 300K/mo plan at current volume.

## Still to capture (golden RUN data — do in Staging)

Runtime artifacts for the parallel-run comparison (Checklist §4) — need Staging execution, not in this static snapshot:
- Input webhook payload(s) for one known batch (Part2 trigger)
- Datastore end-state (Email_Text, Total_value_of_checks_from_checkflo, value_of_payment_posted_for_a_batch, indicators)
- Excel log-file content + Batch tracker rows
- Sage request/response XML (Vendor, AP Bill, AP Payment)
- Reporting email (recipients, subject, body) and every WebhookRespond body