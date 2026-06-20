#!/usr/bin/env python3
"""
parity_diff.py - Make blueprint structural-refactor parity checker.

Purpose
-------
Enforce "0% functional change" on a Make scenario refactor by comparing a BASELINE
blueprint against a REFACTORED blueprint module-by-module and reporting any change to
behavior-affecting fields: `parameters`, `mapper`, `filter`, and `flags`.

It is built to catch the `limit=10` class of bug: a field left BLANK in the original
(meaning "no limit / return all rows") that silently becomes Make's UI default. Any key
that appears or disappears, or whose value changes, is reported as HIGH severity.

Module matching
---------------
1. Primary: by module `id` (stable for in-place edits of one scenario).
2. Fallback (use --by-name): by (module type + designer name + structural path), for
   merges/renumbering where ids are not preserved.

Blueprint loading
-----------------
Accepts either shape:
  * committed file:  { "name", "flow": [...], "metadata" }      (flow at root)
  * live API dump:   { "id", "name", "blueprint": { "flow" } }  (flow under blueprint)
  * MCP persisted wrapper: [ { "type": "text", "text": "<json>" } ]

Exit codes
----------
  0  no HIGH/MEDIUM diffs (INFO allowed)        -> safe to proceed
  1  HIGH or MEDIUM diffs found                 -> blocks deploy
  2  usage / load error

Usage
-----
  python parity_diff.py BASELINE.json REFACTORED.json
  python parity_diff.py BASELINE.json REFACTORED.json --by-name
  python parity_diff.py BASELINE.json REFACTORED.json --json report.json
"""
import argparse
import json
import sys

# Parameter names known to carry silent Make UI defaults. A change to any of these
# (especially absent -> present) is the canonical "blank became default" regression.
DEFAULT_RISK_KEYS = {
    "limit", "maxResults", "maximum_results", "rowLimit",
    "continueOnEmpty", "stopIfEmpty", "upsert", "overwriteArrays",
    "returnWrapped", "pagination", "maxRuns", "maximum_runs_per_minute",
}

# Cosmetic keys that never affect behavior. Differences here are INFO only.
COSMETIC_TOP_KEYS = {"metadata"}
COSMETIC_META_KEYS = {"designer", "restore", "expect", "interface", "advanced", "parameters"}


def load_blueprint(path):
    """Return the parsed scenario dict regardless of wrapper shape."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    txt = raw
    stripped = raw.lstrip()
    if stripped.startswith("["):                      # MCP persisted [{type,text}]
        try:
            txt = json.loads(raw)[0]["text"]
        except Exception:
            txt = raw
    obj = json.loads(txt)
    if isinstance(obj, dict) and "blueprint" in obj and isinstance(obj["blueprint"], dict):
        return obj["blueprint"]
    return obj


def flatten(flow, path="", out=None):
    """Walk the flow recursively (including router routes). Yields dicts with the
    behavior-affecting fields and a structural path/signature for fallback matching."""
    if out is None:
        out = []
    for idx, m in enumerate(flow or []):
        mod = m.get("module", "?")
        meta = m.get("metadata") or {}
        name = (meta.get("designer") or {}).get("name", "")
        node_path = f"{path}/{idx}:{mod}"
        out.append({
            "id": m.get("id"),
            "module": mod,
            "name": name,
            "path": node_path,
            "parameters": m.get("parameters", {}),
            "mapper": m.get("mapper", {}),
            "filter": m.get("filter"),
            "flags": m.get("flags", {}),
        })
        for r_i, route in enumerate(m.get("routes", []) or []):
            flatten(route.get("flow", []), f"{node_path}/route{r_i}", out)
    return out


def match_key(node, by_name):
    if by_name:
        return ("N", node["module"], node["name"], node["path"])
    return ("I", node["id"])


def diff_value(field, base, ref, findings, ctx):
    """Deep-compare one behavior field (dict/scalar). Append findings."""
    # Dict fields (parameters / mapper / flags): compare keys + values.
    if isinstance(base, dict) or isinstance(ref, dict):
        base = base or {}
        ref = ref or {}
        for k in sorted(set(base) | set(ref)):
            in_b, in_r = k in base, k in ref
            risk = k in DEFAULT_RISK_KEYS
            if in_b and not in_r:
                findings.append((("HIGH" if risk else "HIGH"), ctx,
                                 f"{field}.{k} REMOVED (was {base[k]!r})"))
            elif in_r and not in_b:
                sev = "HIGH"
                tag = " [DEFAULT-RISK: blank->value?]" if risk else ""
                findings.append((sev, ctx,
                                 f"{field}.{k} ADDED (now {ref[k]!r}){tag}"))
            elif base[k] != ref[k]:
                findings.append(("HIGH", ctx,
                                 f"{field}.{k} CHANGED {base[k]!r} -> {ref[k]!r}"))
    else:
        if base != ref:
            findings.append(("HIGH", ctx, f"{field} CHANGED {base!r} -> {ref!r}"))


def compare(base_nodes, ref_nodes, by_name):
    findings = []
    bmap, rmap = {}, {}
    for n in base_nodes:
        bmap.setdefault(match_key(n, by_name), []).append(n)
    for n in ref_nodes:
        rmap.setdefault(match_key(n, by_name), []).append(n)

    # removed / added modules
    for k in bmap:
        if k not in rmap:
            for n in bmap[k]:
                findings.append(("MEDIUM", f"{n['module']} <<{n['name']}>> (id={n['id']})",
                                 "MODULE REMOVED from refactor"))
    for k in rmap:
        if k not in bmap:
            for n in rmap[k]:
                findings.append(("MEDIUM", f"{n['module']} <<{n['name']}>> (id={n['id']})",
                                 "MODULE ADDED in refactor"))

    # field-level parity for matched modules
    for k in bmap:
        if k not in rmap:
            continue
        b, r = bmap[k][0], rmap[k][0]
        ctx = f"{b['module']} <<{b['name']}>> (id={b['id']})"
        diff_value("parameters", b["parameters"], r["parameters"], findings, ctx)
        diff_value("mapper", b["mapper"], r["mapper"], findings, ctx)
        diff_value("flags", b["flags"], r["flags"], findings, ctx)
        if json.dumps(b["filter"], sort_keys=True) != json.dumps(r["filter"], sort_keys=True):
            findings.append(("HIGH", ctx,
                             f"filter CHANGED {b['filter']!r} -> {r['filter']!r}"))
    return findings


def main():
    ap = argparse.ArgumentParser(description="Make blueprint parity (0% functional change) checker.")
    ap.add_argument("baseline")
    ap.add_argument("refactored")
    ap.add_argument("--by-name", action="store_true",
                    help="match modules by type+name+path instead of id (use for merges/renumbering)")
    ap.add_argument("--json", metavar="FILE", help="also write findings as JSON")
    args = ap.parse_args()

    try:
        base = load_blueprint(args.baseline)
        ref = load_blueprint(args.refactored)
    except Exception as ex:
        print(f"ERROR loading blueprints: {ex}", file=sys.stderr)
        return 2

    base_nodes = flatten(base.get("flow", []))
    ref_nodes = flatten(ref.get("flow", []))
    findings = compare(base_nodes, ref_nodes, args.by_name)

    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    findings.sort(key=lambda f: (order.get(f[0], 9), f[1]))

    print(f"Baseline   : {args.baseline}  ({len(base_nodes)} modules)")
    print(f"Refactored : {args.refactored}  ({len(ref_nodes)} modules)")
    print(f"Match mode : {'name+path' if args.by_name else 'module id'}")
    print("-" * 78)
    counts = {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    for sev, ctx, msg in findings:
        counts[sev] = counts.get(sev, 0) + 1
        print(f"[{sev:6}] {ctx}\n          {msg}")
    if not findings:
        print("No differences in parameters / mapper / filter / flags. PARITY OK.")
    print("-" * 78)
    print(f"HIGH={counts['HIGH']}  MEDIUM={counts['MEDIUM']}  INFO={counts['INFO']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([{"severity": s, "module": c, "detail": m} for s, c, m in findings],
                      fh, indent=2)
        print(f"JSON written to {args.json}")

    return 1 if (counts["HIGH"] or counts["MEDIUM"]) else 0


if __name__ == "__main__":
    sys.exit(main())
