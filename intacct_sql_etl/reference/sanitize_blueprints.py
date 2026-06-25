#!/usr/bin/env python3
"""
Sanitize Make.com blueprints for safe storage in the (non-gitignored) reference/ folder.

The raw blueprints pulled from Make embed secrets + PII inside
metadata.designer.samples (live Sage session ids, cookies, bank data) and in
some module parameters (authPass etc.). This strips/redacts them while keeping
the full STRUCTURE + business logic (SQL text, Sage XML queries, mappings,
filters) intact for traceability.

Usage:
    python sanitize_blueprints.py <src.json> <out.json>
"""
import json
import re
import sys

SECRET_KEYS = re.compile(r"(pass|password|secret|token|sessionid|session_id|cookie|authpass)", re.I)


def scrub(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEYS.search(str(k)):
                out[k] = "[REDACTED]" if v not in (None, "", [], {}) else v
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def main():
    src, out = sys.argv[1], sys.argv[2]
    bp = json.load(open(src, encoding="utf-8"))
    # Drop live sample data wholesale (highest-risk: holds session ids, cookies, bank PII)
    md = bp.get("metadata", {})
    if isinstance(md, dict) and "designer" in md and isinstance(md["designer"], dict):
        if "samples" in md["designer"]:
            md["designer"]["samples"] = "[STRIPPED — live sample data removed for security]"
    bp = scrub(bp)
    json.dump(bp, open(out, "w", encoding="utf-8"), indent=2)
    print(f"sanitized {src} -> {out}")


if __name__ == "__main__":
    main()
