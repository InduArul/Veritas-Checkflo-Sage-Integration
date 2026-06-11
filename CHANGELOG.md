# Changelog

All enhancements to the Checkflo → Sage Intacct Integration blueprints are recorded here.
Each entry corresponds to a git commit.

## [Unreleased]

### 2026-06-11
- **Part 1 – "Custom fields missing" report format.** When a fetched batch's check info
  has no custom fields, module `[901]` (`Email_Body` update, Route 3 of router `[137]`)
  now reports the standard summary block instead of `Status: Custom fields missing`:
  Total checks processed in Checkflo / posted in Intacct / Variance / Amount paid from
  Checkflo / Amount of record transfer processed in intact / Variance — all `NA`.
  Applied to both schedule variants (`MoWeSa` and `TuThFr`).
- Initial import of the 7-part MAKE.com blueprint set and project documentation.
