#!/usr/bin/env python3


import sys, os, json, gzip, glob, collections
lab, out, *proof = sys.argv[1:]
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")
v1c = {c["recording_id"]: c for c in map(json.loads, open(f"{R}/data/coverage.jsonl"))}
v1p = collections.defaultdict(list)
for p in map(json.loads, open(f"{R}/data/passes.jsonl")): v1p[p["recording_id"]].append(p)
v2c, v2p, files = {}, collections.defaultdict(list), sorted(glob.glob(f"{lab}/*.coverage.jsonl.gz"))
for f in files:
    for l in gzip.open(f, "rt"):
        rid = l[18:l.index('"', 18)]   
        if rid in v1c: v2c[rid] = json.loads(l)
    for l in gzip.open(f.replace("coverage", "passes"), "rt"):
        rid = l[18:l.index('"', 18)]   
        if rid in v1c: v2p[rid].append(json.loads(l))
same = [r for r in v2c if {p["pass_id"] for p in v1p[r]} == {p["pass_id"] for p in v2p[r]}]
diff = [r for r in v2c if r not in same]
shared = [(q, p) for r in v2c for p in v2p[r] for q in v1p[r] if q["pass_id"] == p["pass_id"]]
exact = [1 for q, p in shared if q["slant_distance_m"] == p["slant_distance_m"] and q["closest_approach_utc"] == p["closest_approach_utc"]]
why = collections.Counter(v1c[r].get("cut_status") for r in diff)
src = lambda r: v1c[r].get("cut_reference", "").split("/")[0]   
endne = lambda r: v1c[r]["recording_end_utc"][:19] != v2c[r]["recording_end_utc"][:19]   
ne = sorted({r for q, p in shared if not (q["slant_distance_m"] == p["slant_distance_m"] and q["closest_approach_utc"] == p["closest_approach_utc"]) for r in [p["recording_id"]]})
pres = [r for r in diff if v1c[r].get("cut_status") == "present"]
res = dict(v1_v2=dict(recordings=len(v2c), identical_pass_sets=len(same), shared_passes=len(shared), shared_agree_exactly=len(exact),
                      differing=len(diff), differing_v1_cut_status=dict(why), v1_passes_on_differing=sum(len(v1p[r]) for r in diff), v2_passes_on_differing=sum(len(v2p[r]) for r in diff),
                      differing_note="v1 cut_status 'missing' means v1 had no flight data for that recording; v2's own day cut supplies it. Where both had data, v1 used a filtered cut (airborne only, <=33,000 ft) or another daily cache and a duration measured from the audio; v2 keeps every published position and takes the source's duration",
                      present_but_differing=dict(recordings=len(pres), by_v1_cut_source=dict(collections.Counter(map(src, pres))), end_time_differs=sum(map(endne, pres))),
                      shared_not_exact=dict(recordings=len(ne), by_v1_cut_source=dict(collections.Counter(map(src, ne))), end_time_differs=sum(map(endne, ne)), sources=dict(collections.Counter(v1c[r]["source"] for r in ne))), label_days_scanned=len(files),
                      method="v1 data/passes.jsonl and data/coverage.jsonl vs v2 day labels for the same recording_id; pass_id sets compared, then slant_distance_m and closest_approach_utc of shared passes"))
if proof: res["raw_recompute"] = json.load(open(proof[0]))
json.dump(res, open(out, "w"), indent=1)
print(json.dumps(res["v1_v2"]))
