#!/usr/bin/env python3
"""
make_cli.py - A zero-dependency CLI to connect this repo to the Make.com REST API.

Capabilities
  read scenarios   :  scenarios | get | pull
  read run logs    :  logs | log | dlqs | dlq
  edit scenarios   :  push      (safe: auto-backup of the live blueprint first)
  QA scenarios     :  qa | qa-diff | run

Auth / config (no secrets in code, no secrets in git):
  Required env vars (or .make/config.json):
    MAKE_API_TOKEN   Make API token  (Profile -> API/MCP access -> Add token)
    MAKE_ZONE        Your zone from the Make URL, e.g. eu2, us1, eu1
  One of:
    MAKE_TEAM_ID     Team id    (from the team URL/settings)
    MAKE_ORG_ID      Organization id

Examples:
    python tools/make_cli.py doctor
    python tools/make_cli.py scenarios
    python tools/make_cli.py pull 1234567 --out "Source code/Part2.blueprint.json"
    python tools/make_cli.py logs 1234567 --limit 20
    python tools/make_cli.py dlqs 1234567
    python tools/make_cli.py qa "Source code/Project1_Part2_Checkflo Sage Integration_Main processing.blueprint.json"
    python tools/make_cli.py qa-diff old.json new.json
    python tools/make_cli.py push 1234567 "Source code/Part2.blueprint.json" --yes
    python tools/make_cli.py run 1234567
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(REPO, ".make", "config.json")
BACKUP_DIR = os.path.join(REPO, ".make", "backups")


# ----------------------------------------------------------------------------- config
# .env files are searched in this order; first definition of a key wins. Override
# with MAKE_ENV_FILE. The parent dir is included so a shared .env can live above the repo.
ENV_SEARCH = [
    os.environ.get("MAKE_ENV_FILE"),
    os.path.join(REPO, ".env"),
    os.path.join(REPO, ".make", ".env"),
    os.path.join(os.path.dirname(REPO), ".env"),
]


def _parse_env_line(line):
    line = line.lstrip("﻿").strip()
    if not line or line.startswith("#"):
        return None
    if line.lower().startswith("export "):
        line = line[7:].strip()
    # accept KEY=value (standard) and, as a fallback, KEY:value
    if "=" in line:
        k, v = line.split("=", 1)
    elif ":" in line:
        k, v = line.split(":", 1)
    else:
        return None
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    return (k, v) if k else None


def load_env_files():
    for path in ENV_SEARCH:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        kv = _parse_env_line(line)
                        if kv and kv[0] not in os.environ:
                            os.environ[kv[0]] = kv[1]
            except Exception:
                pass


def _first_env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def normalize_zone(z):
    if not z:
        return z
    z = z.strip().replace("https://", "").replace("http://", "")
    if z.endswith(".make.com"):
        z = z[: -len(".make.com")]
    return z.strip("/")


def load_config():
    load_env_files()
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            die(f"Could not read {CONFIG_PATH}: {e}")
    # env vars (incl. those loaded from .env) win over the file. Accept both the
    # canonical MAKE_* names and the project's MAKE_VERITAS_* names.
    cfg["token"] = _first_env("MAKE_API_TOKEN", "MAKE_VERITAS_API_TOKEN") or cfg.get("token")
    cfg["zone"] = normalize_zone(_first_env("MAKE_ZONE", "MAKE_VERITAS_ZONE") or cfg.get("zone"))
    cfg["team_id"] = _first_env("MAKE_TEAM_ID", "MAKE_VERITAS_TEAM_ID") or cfg.get("team_id")
    cfg["org_id"] = _first_env("MAKE_ORG_ID", "MAKE_VERITAS_ORG_ID") or cfg.get("org_id")
    return cfg


def require(cfg, *keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        die(
            "Missing config: "
            + ", ".join(missing)
            + "\nSet env vars MAKE_API_TOKEN / MAKE_ZONE / MAKE_TEAM_ID, "
            + f"or fill {os.path.relpath(CONFIG_PATH, REPO)} (see config.example.json)."
        )


def base_url(cfg):
    return f"https://{cfg['zone']}.make.com/api/v2"


# ----------------------------------------------------------------------------- http
def api(cfg, method, path, query=None, body=None):
    require(cfg, "token", "zone")
    url = base_url(cfg) + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = None
    headers = {
        "Authorization": f"Token {cfg['token']}",
        "Accept": "application/json",
        # Make sits behind Cloudflare, which bans the default urllib User-Agent (err 1010).
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) make-cli/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        die(f"HTTP {e.code} on {method} {path}\n{detail}")
    except urllib.error.URLError as e:
        die(f"Network error on {method} {path}: {e.reason}\n"
            f"Check MAKE_ZONE='{cfg.get('zone')}' (should match your Make URL).")


# ----------------------------------------------------------------------------- helpers
def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def team_or_org_query(cfg):
    if cfg.get("team_id"):
        return {"teamId": cfg["team_id"]}
    if cfg.get("org_id"):
        return {"organizationId": cfg["org_id"]}
    die("Need MAKE_TEAM_ID or MAKE_ORG_ID to list scenarios.")


def collect_modules(flow, acc):
    """Recursively collect modules from a Make blueprint flow (routers + error handlers)."""
    for mod in flow or []:
        acc.append(mod)
        for route in mod.get("routes", []) or []:
            collect_modules(route.get("flow", []), acc)
        collect_modules(mod.get("onerror", []) or [], acc)   # descend into error handlers too
    return acc


# ----------------------------------------------------------------------------- commands
def cmd_doctor(cfg, args):
    require(cfg, "token", "zone")
    me = api(cfg, "GET", "/users/me")
    user = me.get("authUser") or me.get("user") or me
    print("OK - authenticated to Make.")
    print(f"  zone   : {cfg['zone']}.make.com")
    print(f"  user   : {user.get('email') or user.get('name') or user}")
    print(f"  team   : {cfg.get('team_id') or '(unset)'}")
    print(f"  org    : {cfg.get('org_id') or '(unset)'}")


def cmd_scenarios(cfg, args):
    q = team_or_org_query(cfg)
    q["pg[limit]"] = args.limit
    res = api(cfg, "GET", "/scenarios", query=q)
    rows = res.get("scenarios", res)
    if args.json:
        return out(rows)
    print(f"{'ID':<12} {'ACTIVE':<7} {'NAME'}")
    print("-" * 70)
    for s in rows:
        print(f"{str(s.get('id','')):<12} {str(s.get('isActive','')):<7} {s.get('name','')}")


def cmd_get(cfg, args):
    res = api(cfg, "GET", f"/scenarios/{args.scenario_id}")
    out(res)


def cmd_pull(cfg, args):
    res = api(cfg, "GET", f"/scenarios/{args.scenario_id}/blueprint")
    bp = res.get("response", {}).get("blueprint", res)
    text = json.dumps(bp, indent=2, ensure_ascii=False)
    if args.out:
        path = args.out
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote blueprint -> {path}")
    else:
        print(text)


def cmd_logs(cfg, args):
    res = api(cfg, "GET", f"/scenarios/{args.scenario_id}/logs",
              query={"pg[limit]": args.limit, "pg[sortDir]": "desc"})
    rows = res.get("scenarioLogs", res)
    if args.json:
        return out(rows)
    print(f"{'EXECUTION ID':<26} {'STATUS':<10} {'OPS':<6} {'STARTED'}")
    print("-" * 80)
    for l in rows:
        status = "OK" if l.get("status") in (1, "1", "success") else l.get("status")
        print(f"{str(l.get('imtId','')):<26} {str(status):<10} "
              f"{str(l.get('operations','')):<6} {l.get('timestamp','')}")


def cmd_log(cfg, args):
    res = api(cfg, "GET", f"/scenarios/{args.scenario_id}/logs/{args.execution_id}")
    out(res)


def cmd_dlqs(cfg, args):
    # Incomplete executions (Data Loss Queue) - the most useful view for failures.
    res = api(cfg, "GET", "/dlqs",
              query={"scenarioId": args.scenario_id, "pg[limit]": args.limit})
    rows = res.get("dlqs", res)
    if args.json:
        return out(rows)
    print(f"{'DLQ ID':<26} {'REASON'}")
    print("-" * 80)
    for d in rows:
        print(f"{str(d.get('id','')):<26} {d.get('reason') or d.get('text') or ''}")


def cmd_dlq(cfg, args):
    res = api(cfg, "GET", f"/dlqs/{args.dlq_id}")
    out(res)


def cmd_run(cfg, args):
    body = {"responsive": True}
    if args.data:
        with open(args.data, "r", encoding="utf-8") as f:
            body["data"] = json.load(f)
    print(f"Running scenario {args.scenario_id} ...")
    res = api(cfg, "POST", f"/scenarios/{args.scenario_id}/run", body=body)
    out(res)


def cmd_push(cfg, args):
    if not os.path.exists(args.file):
        die(f"Blueprint file not found: {args.file}")
    with open(args.file, "r", encoding="utf-8") as f:
        new_bp = json.load(f)  # validate JSON before doing anything

    # local QA gate
    problems = qa_blueprint(new_bp)
    errs = [p for p in problems if p[0] == "ERROR"]
    if errs and not args.force:
        for level, msg in problems:
            print(f"  [{level}] {msg}")
        die("Local QA found errors. Fix them, or re-run with --force to override.")

    # safety backup of the LIVE blueprint
    os.makedirs(BACKUP_DIR, exist_ok=True)
    live = api(cfg, "GET", f"/scenarios/{args.scenario_id}/blueprint")
    live_bp = live.get("response", {}).get("blueprint", live)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"{args.scenario_id}-{stamp}.json")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(live_bp, f, indent=2, ensure_ascii=False)
    print(f"Backed up live blueprint -> {os.path.relpath(backup, REPO)}")

    if not args.yes:
        die("Refusing to push without --yes (backup is saved above). "
            "Re-run with --yes to apply the update.")

    body = {"blueprint": json.dumps(new_bp)}
    api(cfg, "PATCH", f"/scenarios/{args.scenario_id}", body=body)
    print(f"Pushed {args.file} -> scenario {args.scenario_id}. "
          f"Rollback available at {os.path.relpath(backup, REPO)}")


# ----------------------------------------------------------------------------- QA (local, offline)
def qa_blueprint(bp):
    """Static checks on a Make blueprint. Returns list of (level, message)."""
    problems = []
    if not isinstance(bp, dict):
        return [("ERROR", "Top-level blueprint is not a JSON object.")]
    if "flow" not in bp or not isinstance(bp["flow"], list):
        problems.append(("ERROR", "Missing top-level 'flow' array."))
        return problems
    if not bp.get("name"):
        problems.append(("WARN", "Blueprint has no 'name'."))

    mods = collect_modules(bp["flow"], [])
    ids = [m.get("id") for m in mods]
    # duplicate module ids
    seen, dupes = set(), set()
    for i in ids:
        if i in seen:
            dupes.add(i)
        seen.add(i)
    for d in sorted(x for x in dupes if x is not None):
        problems.append(("ERROR", f"Duplicate module id [{d}]."))
    # modules missing id/module
    for m in mods:
        if m.get("id") is None:
            problems.append(("ERROR", f"Module missing 'id': {m.get('module','?')}"))
        if not m.get("module"):
            problems.append(("ERROR", f"Module [{m.get('id')}] missing 'module' type."))
    # webhook wiring sanity (informational)
    webhooks = [m for m in mods if str(m.get("module", "")).startswith("gateway:CustomWebHook")]
    senders = [m for m in mods if str(m.get("module", "")) in
               ("http:ActionSendData", "http:ActionSendDataBasicAuth")]
    problems.append(("INFO", f"{len(mods)} modules, {len(webhooks)} webhook trigger(s), "
                             f"{len(senders)} HTTP sender(s)."))
    return problems


def cmd_qa(cfg, args):
    with open(args.file, "r", encoding="utf-8") as f:
        try:
            bp = json.load(f)
        except json.JSONDecodeError as e:
            die(f"Invalid JSON in {args.file}: {e}")
    problems = qa_blueprint(bp)
    n_err = sum(1 for p in problems if p[0] == "ERROR")
    n_warn = sum(1 for p in problems if p[0] == "WARN")
    for level, msg in problems:
        print(f"  [{level}] {msg}")
    print("-" * 50)
    print(f"{args.file}: {n_err} error(s), {n_warn} warning(s).")
    if n_err:
        sys.exit(2)


def cmd_qa_diff(cfg, args):
    def modmap(path):
        with open(path, "r", encoding="utf-8") as f:
            bp = json.load(f)
        return {m.get("id"): m.get("module") for m in collect_modules(bp.get("flow", []), [])}, bp.get("name")

    old, old_name = modmap(args.old)
    new, new_name = modmap(args.new)
    added = sorted(set(new) - set(old), key=lambda x: (x is None, x))
    removed = sorted(set(old) - set(new), key=lambda x: (x is None, x))
    changed = sorted([i for i in set(old) & set(new) if old[i] != new[i]],
                     key=lambda x: (x is None, x))
    if old_name != new_name:
        print(f"NAME  : '{old_name}' -> '{new_name}'")
    print(f"ADDED modules   : {[f'[{i}] {new[i]}' for i in added] or 'none'}")
    print(f"REMOVED modules : {[f'[{i}] {old[i]}' for i in removed] or 'none'}")
    print(f"RETYPED modules : {[f'[{i}] {old[i]} -> {new[i]}' for i in changed] or 'none'}")
    if removed:
        print("\n  WARNING: removing modules can break downstream mappings/webhook wiring.")


# ----------------------------------------------------------------------------- QA gate (deep)
AGG_SLUGS = {"builtin:BasicAggregator", "util:TextAggregator",
             "util:NumericalAggregator", "util:TableAggregator"}
INVALID_SLUGS = {"builtin:ArrayAggregator"}  # not a real Make slug (use builtin:BasicAggregator)
_INTERNAL_PREFIXES = ("builtin:", "util:", "json:", "csv:", "xml:", "regexp:",
                      "text:", "gateway:", "tools:", "math:")
_BRACE_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
# a module reference inside {{...}}: an integer followed by ".field" - not a decimal,
# and the field starts with a letter/underscore/backtick (e.g. 70.`__IMTKEY__`, 313.values)
_REF_RE = re.compile(r"(?<![\d.])(\d+)\.(?=[A-Za-z_`])")


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def collect_referenced_ids(bp):
    """Module ids referenced via {{id.field}} anywhere in the blueprint -> {id: count}."""
    refs = {}
    for s in _iter_strings(bp):
        if "{{" not in s:
            continue
        for blk in _BRACE_RE.findall(s):
            for m in _REF_RE.finditer(blk):
                rid = int(m.group(1))
                refs[rid] = refs.get(rid, 0) + 1
    return refs


def gate_blueprint(bp, baseline=None):
    """Deep static checks. Returns (findings[(level,code,msg)], module_count)."""
    findings = []
    add = lambda lvl, code, msg: findings.append((lvl, code, msg))

    mods = collect_modules(bp.get("flow", []), [])
    idset = {m.get("id") for m in mods}

    # structural (reuse qa, drop its INFO line)
    for level, msg in qa_blueprint(bp):
        if level != "INFO":
            add(level, "STRUCT", msg)

    # invalid / non-existent slugs
    for m in mods:
        if m.get("module") in INVALID_SLUGS:
            add("ERROR", "SLUG", f"[{m.get('id')}] invalid slug '{m.get('module')}' "
                                 "(Array Aggregator == builtin:BasicAggregator)")

    # reference integrity - the big one: dangling {{id.field}}
    for rid, n in sorted(collect_referenced_ids(bp).items()):
        if rid not in idset:
            add("ERROR", "REF", f"{n} reference(s) to {{{{{rid}.…}}}} but module [{rid}] does not exist")

    # aggregator boundary (Source Module / feeder)
    for m in mods:
        if m.get("module") in AGG_SLUGS:
            feeder = (m.get("parameters") or {}).get("feeder")
            if feeder is None:
                add("ERROR", "AGG", f"[{m.get('id')}] {m.get('module')} has no Source Module (feeder)")
            elif feeder not in idset:
                add("ERROR", "AGG", f"[{m.get('id')}] aggregator feeder [{feeder}] does not exist")

    # variable usage (best-effort)
    set_vars, get_uses = set(), []
    for m in mods:
        mp = m.get("mapper") or {}
        if m.get("module") == "util:SetVariables":
            for v in mp.get("variables", []) or []:
                if isinstance(v, dict) and v.get("name"):
                    set_vars.add(v["name"])
        elif m.get("module") == "util:GetVariables":
            for v in mp.get("variables", []) or []:
                name = v if isinstance(v, str) else (v.get("name") if isinstance(v, dict) else None)
                if name:
                    get_uses.append((m.get("id"), name))
    for mid, name in get_uses:
        if name not in set_vars:
            add("WARN", "VAR", f"[{mid}] GetVariables reads '{name}' which no SetVariables sets")

    # external calls without error handlers (advisory)
    ext_no_handler = [m.get("id") for m in mods
                      if (m.get("module") or "") and not m["module"].startswith(_INTERNAL_PREFIXES)
                      and not m.get("onerror")]
    if ext_no_handler:
        add("INFO", "RESIL", f"{len(ext_no_handler)} external-call module(s) with no onerror handler "
                             f"(review D1 for any inside a loop): {ext_no_handler[:15]}")

    # diff-aware (visibility of deletions/additions vs a baseline)
    if baseline is not None:
        base_ids = {m.get("id") for m in collect_modules(baseline.get("flow", []), [])}
        removed = sorted(i for i in base_ids - idset if i is not None)
        added = sorted(i for i in idset - base_ids if i is not None)
        if removed:
            add("INFO", "DIFF", f"removed vs baseline: {removed}")
        if added:
            add("INFO", "DIFF", f"added vs baseline: {added}")
    return findings, len(mods)


MANUAL_CHECKS = [
    "C1 run-once: any 'fetch once' module sits BEFORE the fan-out (iterator/groupBy aggregator)?",
    "C2 scope: data cached for in-loop use is execution-scoped (not 'one cycle')?",
    "C4 match logic: in-memory lookup reproduces the same field(s)+operators (case/trim)?",
    "E1 output: downstream columns/fields/order unchanged?",
    "E2 filters: row-skip filters (e.g. TOPLEVEL / petty cash) preserved?",
    "D3 logging: business counters / log writes preserved or consciously dropped?",
    "LIVE: run once in Make, then `logs`/`dlqs` clean - the only true behavioral proof.",
]


def cmd_gate(cfg, args):
    with open(args.file, "r", encoding="utf-8") as f:
        try:
            bp = json.load(f)
        except json.JSONDecodeError as e:
            die(f"Invalid JSON in {args.file}: {e}")
    baseline = None
    if args.baseline:
        with open(args.baseline, "r", encoding="utf-8") as f:
            baseline = json.load(f)
    findings, nmods = gate_blueprint(bp, baseline)
    rank = {"ERROR": 0, "WARN": 1, "INFO": 2}
    for level, code, msg in sorted(findings, key=lambda x: rank.get(x[0], 9)):
        print(f"  [{level:<5}] {code:<6} {msg}")
    n_err = sum(1 for f in findings if f[0] == "ERROR")
    n_warn = sum(1 for f in findings if f[0] == "WARN")
    print("-" * 72)
    print(f"{os.path.basename(args.file)}: {nmods} modules | {n_err} error(s), {n_warn} warning(s)")
    print("\nMANUAL CHECKS (not automatable - confirm before push):")
    for c in MANUAL_CHECKS:
        print(f"  [ ] {c}")
    print("-" * 72)
    if n_err:
        print("GATE: FAIL - fix errors above before push.")
        sys.exit(2)
    print("GATE: PASS (automated) - complete manual checks + a live run before push.")


# ----------------------------------------------------------------------------- argparse
def build_parser():
    p = argparse.ArgumentParser(prog="make_cli", description="Make.com API CLI for this repo.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="verify auth/connectivity")

    s = sub.add_parser("scenarios", help="list scenarios in the team/org")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("get", help="get scenario metadata")
    s.add_argument("scenario_id")

    s = sub.add_parser("pull", help="download a scenario blueprint")
    s.add_argument("scenario_id")
    s.add_argument("--out", help="write to this file instead of stdout")

    s = sub.add_parser("logs", help="list recent execution logs")
    s.add_argument("scenario_id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("log", help="get one execution's detail")
    s.add_argument("scenario_id")
    s.add_argument("execution_id")

    s = sub.add_parser("dlqs", help="list incomplete executions (failures) for a scenario")
    s.add_argument("scenario_id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("dlq", help="get one incomplete execution's bundle detail")
    s.add_argument("dlq_id")

    s = sub.add_parser("run", help="run a scenario once (live QA)")
    s.add_argument("scenario_id")
    s.add_argument("--data", help="JSON file to feed the run")

    s = sub.add_parser("push", help="update a live scenario from a blueprint file")
    s.add_argument("scenario_id")
    s.add_argument("file")
    s.add_argument("--yes", action="store_true", help="confirm the live update")
    s.add_argument("--force", action="store_true", help="push even if local QA has errors")

    s = sub.add_parser("qa", help="static QA of a local blueprint file")
    s.add_argument("file")

    s = sub.add_parser("qa-diff", help="compare two blueprint files")
    s.add_argument("old")
    s.add_argument("new")

    s = sub.add_parser("gate", help="comprehensive QA gate: deep static checks + manual checklist")
    s.add_argument("file")
    s.add_argument("--baseline", help="compare against this baseline blueprint (diff-aware checks)")

    return p


COMMANDS = {
    "doctor": cmd_doctor, "scenarios": cmd_scenarios, "get": cmd_get, "pull": cmd_pull,
    "logs": cmd_logs, "log": cmd_log, "dlqs": cmd_dlqs, "dlq": cmd_dlq, "run": cmd_run,
    "push": cmd_push, "qa": cmd_qa, "qa-diff": cmd_qa_diff, "gate": cmd_gate,
}


def main():
    args = build_parser().parse_args()
    cfg = load_config()
    COMMANDS[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
