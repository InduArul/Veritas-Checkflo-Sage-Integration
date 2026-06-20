import json, re, copy
B = "baseline/leakA_2026-06-20"
p2 = json.load(open(f"{B}/Part2_Main.blueprint.json", encoding="utf-8"))
def walk(f):
    for m in f:
        yield m
        for r in m.get("routes", []) or []: yield from walk(r.get("flow", []))

PART345_URL = "https://hook.us2.make.com/REPLACE_WITH_PART_345_WEBHOOK"  # set at deploy from Part_345
DELETE = {923, 355, 929}

# 1) delete 923/355/929 from their parent flow arrays
def prune(flow):
    flow[:] = [m for m in flow if m.get("id") not in DELETE]
    for m in flow:
        for r in m.get("routes", []) or []:
            prune(r.get("flow", []))
prune(p2["flow"])

# 2) repoint 965 URL -> Part_345; 3) repoint 923.data/929.data -> 965.data everywhere
def fix(o):
    if isinstance(o, dict): return {k: fix(v) for k, v in o.items()}
    if isinstance(o, list): return [fix(x) for x in o]
    if isinstance(o, str):
        s = o.replace("{{923.data.", "{{965.data.").replace("{{929.data.", "{{965.data.")
        return s
    return o
p2["blueprint_wired"] = True
p2 = fix(p2)
# set 965 url
for m in walk(p2["flow"]):
    if m.get("id") == 965:
        m["mapper"]["url"] = PART345_URL
        m.setdefault("metadata", {}).setdefault("designer", {})["name"] = "Call Part_345 (merged Vendor+NegChecks+Loop)"

p2["name"] = "Project1_Part2_Checkflo Sage Integration_Main processing (WIRED to Part_345)"
del p2["blueprint_wired"]
json.dump(p2, open("leakA_refactor/candidates/part2_wired.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)

ids = {m["id"] for m in walk(p2["flow"])}
blob = json.dumps(p2)
print("deleted present?", {d: (d in ids) for d in DELETE})
print("remaining {{923. refs:", len(re.findall(r'\{\{923[\.\}]', blob)), "| {{929. refs:", len(re.findall(r'\{\{929[\.\}]', blob)))
print("965 url set:", next(m for m in walk(p2['flow']) if m['id']==965)['mapper']['url'])
print("modules:", len(list(walk(p2['flow']))))
