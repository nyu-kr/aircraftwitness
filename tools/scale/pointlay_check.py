#!/usr/bin/env python3

import sys, csv, gzip, json, glob, datetime as dt
D = sys.argv[1]; TOL = 60   
iso = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
def off(s):   # The spreadsheet can turn m:ss into mm:ss:00.
    p = [int(x) for x in s.split(":")]
    return p[0] * 60 + p[1] if len(p) == 2 or p[2] == 0 else p[0] * 3600 + p[1] * 60 + p[2]
recs = {r["recording_id"]: r for r in csv.DictReader(gzip.open(f"{D}/manifests/usgs-pointlay.csv.gz", "rt"))}
cov, passes = {}, {}
for f in glob.glob(f"{D}/labels/2024-09-*.coverage.jsonl.gz"):
    for l in gzip.open(f, "rt"):
        if l.startswith('{"recording_id": "USGSPTLAY'): c = json.loads(l); cov[c["recording_id"]] = c
for f in glob.glob(f"{D}/labels/2024-09-*.passes.jsonl.gz"):
    for l in gzip.open(f, "rt"):
        if l.startswith('{"recording_id": "USGSPTLAY'): p = json.loads(l); passes.setdefault(p["recording_id"], []).append(p)
ev_n = ev_hit = pa_n = pa_hit = 0; rows = []
for rid, c in sorted(cov.items()):
    r = recs[rid]; t0 = iso(r["utc_start"]); evs = []
    for a, b, q in json.loads(r["extra"]).get("aircraft_events", []):
        evs.append((t0 + dt.timedelta(seconds=off(a) - TOL), t0 + dt.timedelta(seconds=off(b) + TOL), q))
    ps = passes.get(rid, [])
    span = lambda p: (iso(p["first_in_range_sample_utc"]), iso(p["last_in_range_sample_utc"]))
    for e in evs:
        m = [p for p in ps if span(p)[0] <= e[1] and span(p)[1] >= e[0]]; ev_n += 1; ev_hit += bool(m)
        rows.append(f"event {rid} {e[0] + dt.timedelta(seconds=TOL):%m-%d %H:%M:%S} {e[2]}: " + ("; ".join(f"{p['aircraft_type']} slant {p['slant_distance_m']:.0f} m alt {p['altitude_m']} at {p['closest_approach_utc'][11:19]}" for p in m) or "no ADS-B pass"))
    for p in ps:
        pa_n += 1; hit = any(span(p)[0] <= e[1] and span(p)[1] >= e[0] for e in evs); pa_hit += hit
        if not hit: rows.append(f"pass  {rid} {p['closest_approach_utc'][5:19]} {p['aircraft_type']} slant {p['slant_distance_m']:.0f} m alt {p['altitude_m']}: no logged event")
print("\n".join(rows))
print(f"Point Lay: {len(cov)} of {len(recs)} recordings labelled; logged aircraft events {ev_n}, with an ADS-B pass within 10 km (+-{TOL} s) {ev_hit}; ADS-B passes {pa_n}, matching a logged event {pa_hit}")
