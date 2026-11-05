#!/usr/bin/env python3

import sys, os, io, gzip, json, math, tarfile, hashlib
date, work, *want = sys.argv[1:]
passes = [json.loads(l) for l in gzip.open(f"{work}/labels/{date}.passes.jsonl.gz", "rt")]
cov = {c["recording_id"]: c for c in map(json.loads, gzip.open(f"{work}/labels/{date}.coverage.jsonl.gz", "rt"))}
p = next(x for x in passes if x["pass_id"] == want[0]) if want else min(passes, key=lambda x: x["slant_distance_m"])
import pyarrow.parquet as pq
rec = pq.read_table(f"{work}/recs.parquet", filters=[("recording_id", "=", p["recording_id"])]).to_pylist()[0]
NS = "org.geoloc.open-aircraft-passes.v1:"
pts = pq.read_table(f"{work}/points/{date}.points.parquet", columns=["icao"]).column("icao").to_pylist()
icao = next(i for i in set(pts) if hashlib.sha256((NS + i.strip().lower()).encode()).hexdigest() == p["aircraft_hash"])
raw = sorted(os.listdir(f"{work}/raw/{date}")); tr = None
class Chain(io.RawIOBase):
    def __init__(s, fs): s.fs = [open(f, "rb") for f in fs]; s.i = 0
    def readable(s): return True
    def readinto(s, b):
        while s.i < len(s.fs):
            n = s.fs[s.i].readinto(b)
            if n: return n
            s.i += 1
        return 0
with tarfile.open(fileobj=io.BufferedReader(Chain([f"{work}/raw/{date}/{f}" for f in raw]), 1 << 20), mode="r|*") as t:
    for m in t:
        if m.isfile() and m.name.endswith(f"trace_full_{icao}.json"):
            b = t.extractfile(m).read(); tr = json.loads(gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b); name = m.name; break
assert tr, f"{icao} not in the raw tar"
def ecef(lat, lon, h):
    a, f = 6378137.0, 1 / 298.257223563; e2 = f * (2 - f); la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo), (n * (1 - e2) + h) * math.sin(la))
o = ecef(float(rec["public_latitude"]), float(rec["public_longitude"]), 0.0)
best = None
for q in tr["trace"]:
    t = tr["timestamp"] + q[0]
    if not rec["t0"] <= t < rec["t1"]: continue
    g = q[10] if len(q) > 10 and isinstance(q[10], (int, float)) else (q[3] if isinstance(q[3], (int, float)) and q[6] & 8 else None)
    alt = g if g is not None else (q[3] if isinstance(q[3], (int, float)) else None)
    if alt is None: continue
    x = ecef(q[1], q[2], alt * 0.3048); s = math.dist(o, x)
    if best is None or s < best[0]: best = (s, t, q)
print("pass label:", json.dumps({k: p[k] for k in ("recording_id", "closest_approach_utc", "slant_distance_m", "horizontal_distance_m", "altitude_m", "altitude_basis", "aircraft_type", "sample_count", "surveillance_sources")}))
print("raw member:", name, "registration", tr.get("r"), "type", tr.get("t"), "points that day", len(tr["trace"]))
import datetime
print("raw recompute: min slant %.1f m at %s, point %s" % (best[0], datetime.datetime.fromtimestamp(best[1], datetime.timezone.utc).isoformat(), json.dumps(best[2])[:200]))
print("recording:", rec["recording_id"], rec["utc_start"], rec["duration_s"], "s at", rec["public_latitude"], rec["public_longitude"], "| coverage:", json.dumps({k: cov[p["recording_id"]][k] for k in ("pass_count", "receiver_aircraft_within_50km_utc_day", "receiver_utc_hours_with_aircraft_within_50km", "archive_utc_hours_without_data")}))
d = abs(best[0] - p["slant_distance_m"]); dt = abs(best[1] - datetime.datetime.fromisoformat(p["closest_approach_utc"].replace("Z", "+00:00")).timestamp())
print(f"agreement: slant differs by {d:.1f} m, time by {dt:.1f} s ->", "MATCH" if d < 50 and dt < 1 else "MISMATCH")
if os.environ.get("PROOF_JSON"):   
    json.dump(dict(date=date, pass_id=p["pass_id"], recording_id=p["recording_id"], aircraft_type=p["aircraft_type"],
                   label_slant_m=round(p["slant_distance_m"], 1), raw_slant_m=round(best[0], 1), label_closest_utc=p["closest_approach_utc"],
                   raw_closest_utc=datetime.datetime.fromtimestamp(best[1], datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                   slant_diff_m=round(d, 1), time_diff_s=round(dt, 2), same_second=dt < 1, method="raw adsb.lol trace_full of that aircraft, WGS84 ECEF chord from the public coordinate at 0 m, geometric altitude when flagged, else barometric"), open(os.environ["PROOF_JSON"], "w"), indent=1)
