import json, re, copy
B = "baseline/leakA_2026-06-20"
load=lambda n: json.load(open(f"{B}/{n}.blueprint.json",encoding="utf-8"))
p2,p3,p4,p5 = load("Part2_Main"),load("Part3_Vendor"),load("Part4_NegChecks"),load("Part5_Loop")
def walk(flow):
    for m in flow:
        yield m
        for r in m.get("routes",[]) or []: yield from walk(r.get("flow",[]))
s2={m["id"]:m for m in walk(p2["flow"])}

def renumber(bp, off):
    ids=sorted({m["id"] for m in walk(bp["flow"])}); R={i:i+off for i in ids}
    def f(o):
        if isinstance(o,dict):
            out={}
            for k,v in o.items():
                if k=="id" and isinstance(v,int) and v in R: out[k]=R[v]
                elif k=="feeder" and isinstance(v,int): out[k]=R.get(v,v)
                elif k=="target" and isinstance(v,str): out[k]=re.sub(r"^(\d+)\.",lambda mm:"%d."%R.get(int(mm.group(1)),int(mm.group(1))),v)
                else: out[k]=f(v)
            return out
        if isinstance(o,list): return [f(x) for x in o]
        if isinstance(o,str): return re.sub(r"\{\{(\d+)\.",lambda mm:"{{%d."%R.get(int(mm.group(1)),int(mm.group(1))),o)
        return o
    return f(copy.deepcopy(bp))

def rewire(node, trig, ambient, remap):
    def rs(s):
        for fld in ambient: s=s.replace("{{%d.%s"%(trig,fld),"{{918.%s"%fld)
        for fld,tgt in remap.items():
            s=s.replace("{{%d.%s}}"%(trig,fld),"{{%s}}"%tgt).replace("{{%d.%s."%(trig,fld),"{{%s."%tgt)
        return s
    def f(o):
        if isinstance(o,dict): return {k:f(v) for k,v in o.items()}
        if isinstance(o,list): return [f(x) for x in o]
        if isinstance(o,str): return rs(o)
        return o
    return f(node)

def splice_at(flow, target_id, new_nodes):
    for i,m in enumerate(flow):
        if m.get("id")==target_id:
            flow[i:i+1]=copy.deepcopy(new_nodes); return True
        for r in m.get("routes",[]) or []:
            if splice_at(r.get("flow",[]),target_id,new_nodes): return True
    return False

AMB=["Batch_id","Check_no","Sage_url","Sage_Session_id","Sage_Sender_id","Sage_Sender_pass","log_workbook_id"]

# ---------- SEAM 1: Part3 <- Part4 ----------
p4r=renumber(p4,10000)
p4r=rewire(p4r,10918,AMB,{"Vendor_id":"974.vendor_id","Total_Check_Amt":"974.Total_check_amount","SimpleArray_Invoicedata":"975.json"})
p4_body=p4r["flow"][1:]
# glue: 974 (repointed to producers) + 975 (verbatim w/ stop filter)
PROD={"Vendor_id":"535.vendor_id","Simple_array_of_json":"925.json","Total_check_amount":"824.Total_check_amount","stop_check_processing":"824.stop_check_processing","accum_invoice_amout":"824.accum_invoice_amount"}
g974=copy.deepcopy(s2[974])
for v in g974["mapper"]["variables"]:
    mm=re.match(r"\{\{965\.data\.([A-Za-z_]+)\}\}",v["value"])
    if mm: v["value"]="{{%s}}"%PROD[mm.group(1)]
g975=copy.deepcopy(s2[975])
merged=copy.deepcopy(p3)
assert splice_at(merged["flow"],967,[g974,g975]+p4_body)

# ---------- SEAM 2: Part4 <- Part5 ----------
p5r=renumber(p5,20000)
p5r=rewire(p5r,20918,AMB,{"Vendor_id":"974.vendor_id","SimpleArray_Invoicedata":"975.json","accum_invoice_amount":"10926.accum_invoice_amount","stop_check_processing":"10926.stop_check_processing"})
p5_body=p5r["flow"][1:]
# glue: carry Part2 [355] datastore temp_var=0 (verbatim, keeps stop filter -> also gates Part5); fresh id 31000
g355=copy.deepcopy(s2[355]); g355["id"]=31000
# repoint [355]'s filter ref from Part2's [926] to Part4's producer (10926)
g355=json.loads(re.sub(r"\{\{926\.","{{10926.",json.dumps(g355)))
# splice [355]+Part5 body where Part4's respond 10927 sits
assert splice_at(merged["flow"],10927,[g355]+p5_body)

merged["name"]="Project1_Part_345_CheckProcessing (p3+p4+p5)"
json.dump(merged,open("leakA_refactor/candidates/merged_p345.json","w",encoding="utf-8"),indent=2,ensure_ascii=False)
print("built merged_p345.json | modules:",len(list(walk(merged["flow"]))))
print("triggers:",[m['id'] for m in walk(merged['flow']) if m['module']=='gateway:CustomWebHook'])
print("responds:",[m['id'] for m in walk(merged['flow']) if m['module']=='gateway:WebhookRespond'])

# ================= FINAL-RESPOND LAYER (UNION SCHEMA) =================
# Union of Part3 outputs (Vendor_id/Total_check_amount/Simple_array_of_json) + Part5 outputs
# (route1..4, stop, accum). All three accum spellings included (Part2 reads each across stages).
def union_body(p3_src, routes_src, accum_src, stop_val):
    def p3(field, var):
        return '"%s": "{{%s.%s}}"' % (field, p3_src, var) if p3_src else '"%s": ""' % field
    def arr(field, var):
        return '"%s": {{%s.%s}}' % (field, p3_src, var) if p3_src else '"%s": ""' % field
    def rt(n):
        return '"route%d": {{%s.route%d}}' % (n, routes_src, n) if routes_src else '"route%d": ""' % n
    acc = '"{{%s}}"' % accum_src if accum_src else '""'
    lines = [
        '"status": "OK"',
        p3("Vendor_id","vendor_id"),
        p3("Total_check_amount","Total_check_amount"),
        arr("Simple_array_of_json","simple_array_of_invoice"),
        rt(1), rt(2), rt(3), rt(4),
        '"stop_check_processing": "%s"' % stop_val,
        '"accum_invoice_amt": %s' % acc,
        '"accum_invoice_amount": %s' % acc,
        '"accum_invoice_amout": %s' % acc,
    ]
    return "{\n  " + ",\n  ".join(lines) + "\n}"

def mk_respond(rid, stop_ref, p3_src, accum_src, label):
    return {"id": rid, "module": "gateway:WebhookRespond", "version": 1,
        "filter": {"name": label, "conditions": [[{"a": "{{%s}}" % stop_ref, "b": "1", "o": "text:equal"}]]},
        "mapper": {"body": union_body(p3_src, "", accum_src, "1"), "status": "200",
                   "headers": [{"key": "Content-Type", "value": "application/json"}]},
        "metadata": {"designer": {"x": 0, "y": 0, "name": label}}}

def insert_before(flow, target_id, node):
    for i, m in enumerate(flow):
        if m.get("id") == target_id: flow.insert(i, node); return True
        for r in m.get("routes", []) or []:
            if insert_before(r.get("flow", []), target_id, node): return True
    return False
def find(flow, tid):
    for m in flow:
        if m.get("id")==tid: return m
        for r in m.get("routes",[]) or []:
            x=find(r.get("flow",[]),tid)
            if x: return x
    return None

# stop-exit responds (Part3 data via 974; accum from current stage)
insert_before(merged["flow"], 975,   mk_respond(32001, "974.stop_check_processing", "974", "974.accum_invoice_amount", "Exit: stop after Vendor"))
insert_before(merged["flow"], 31000, mk_respond(32002, "10926.stop_check_processing", "974", "10926.accum_invoice_amount", "Exit: stop after NegChecks"))
# success respond 20935: rebuild body to UNION (Part3 from 974, Part5 routes/accum from 937)
find(merged["flow"], 20935)["mapper"]["body"] = union_body("974", "20937", "20937.accum_invoice_amount", "{{20937.stop_check_processing}}")
# fallback 971: union with everything empty, stop=1
find(merged["flow"], 971)["mapper"]["body"] = union_body("", "", "", "1")

json.dump(merged, open("leakA_refactor/candidates/merged_p345_final.json","w",encoding="utf-8"), indent=2, ensure_ascii=False)
allr=[m['id'] for m in walk(merged['flow']) if m['module']=='gateway:WebhookRespond']
print("UNION final-respond applied | responds:", sorted(allr), "| modules:", len(list(walk(merged['flow']))))
