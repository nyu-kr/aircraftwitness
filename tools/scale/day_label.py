#!/usr/bin/env python3

import sys, os, gzip, json, math
import numpy as np, pandas as pd, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import label_passes as lp, kv2
date, recs_path, work = sys.argv[1:]
day = kv2.day_name
BASIS = [c for c in ("time_basis", "position_basis", "position_precision_m") if c in pq.read_schema(recs_path).names]   
r = kv2.read_day(recs_path, date, kv2.LABEL_COLS + BASIS)
UPSTREAM = "adsb.lol trace_full as published: all altitudes, airborne and ground positions (ground flagged per sample); positions within 15 km of the public place from start-10 min to max(end, start+1 h)+10 min; reception completeness unknown"
r = r[[day(t - 1e-3) == date for t in r.t1]]
types = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "types.json")))
CAP = "adsb.lol globe_history prod (readsb trace_full): volunteer feeders, no altitude or region cap, reception and MLAT coverage vary by place and day; positions as received, no interpolation"
cache = {}
E = pd.read_parquet(f"{work}/place_elev.parquet") if os.path.exists(f"{work}/place_elev.parquet") else None
elev = {} if E is None else {(a, b): g for a, b, g in zip(E.lat, E.lon, E.ground_m)}
def load(d):   # Points must match the current recordings for the day.
    if d not in cache:
        f = f"{work}/points/{d}.points.parquet"
        try: fresh = json.load(open(f"{work}/points/{d}.filter.json"))["recs_hash"] == kv2.filter_hash(kv2.read_day(recs_path, d, ["recording_id", "lat", "lon"]))
        except (OSError, KeyError, ValueError): fresh = False
        if not (fresh and os.path.exists(f)): cache[d] = None
        else:
            pts = pd.read_parquet(f); pl = pd.read_parquet(f"{work}/points/{d}.places.parquet"); meta = json.load(open(f"{work}/points/{d}.filter.json"))
            pts = pts.merge(pl[["place", "lat", "lon"]].rename(columns={"lat": "plat", "lon": "plon"}), on="place").sort_values(["plat", "plon", "t"], kind="stable")
            frames = {k: (g[COLS].reset_index(drop=True), g.t.to_numpy()) for k, g in pts.groupby(["plat", "plon"], sort=False)}
            stats = {(a, b): (int(x), int(y), bin(int(z)).count("1")) for a, b, x, y, z in zip(pl.lat, pl.lon, pl.aircraft_50km, pl.points_50km, pl.hours_mask_50km)}
            cache[d] = (frames, stats, [h for h, x in enumerate(meta["points_per_utc_hour"]) if x == 0])
    return cache[d]
COLS = lp.COLUMNS + ["gnd"]; EMPTY = pd.DataFrame(columns=COLS)
def window(got, key, a, b):   
    parts = []
    for g in got:
        f = g[0].get(key)
        if f is None: continue
        i, j = np.searchsorted(f[1], a, "left"), np.searchsorted(f[1], b, "right")
        if j > i: parts.append(f[0].iloc[i:j])
    return parts[0] if len(parts) == 1 else (pd.concat(parts, ignore_index=True) if parts else EMPTY)
os.makedirs(f"{work}/labels", exist_ok=True)
npass = nrec = 0; pending = []; first = None; srcs = {}; blind = {}
with gzip.open(f"{work}/labels/{date}.passes.jsonl.gz.part", "wt") as pf, gzip.open(f"{work}/labels/{date}.coverage.jsonl.gz.part", "wt") as cf:
    for rec in r.itertuples(index=False):
        days = pd.date_range(day(rec.t0), day(rec.t1 - 1e-3), freq="D").strftime("%Y-%m-%d").tolist()
        got = [load(d) for d in days]
        if any(g is None for g in got): pending.append(rec.recording_id); continue
        fr = window(got, (rec.lat, rec.lon), rec.t0 - 15, rec.t1 + 15)
        R = dict(recording_id=rec.recording_id, source=rec.source, utc_start=rec.utc_start, duration_s=rec.duration_s, public_latitude=rec.public_latitude, public_longitude=rec.public_longitude)
        g = elev.get((rec.lat, rec.lon)); el = {} if g is None else {rec.recording_id: dict(lat=rec.lat, lon=rec.lon, ground_m=g)}
        passes, cov = lp.label_recording(R, fr, types, el, "adsb.lol " + "+".join(days) + " v2 day cut (tools/scale/day_filter.py: 15 km around the place, start-10 min to max(end, start+1 h)+10 min)")
        pl = [g[1][(rec.lat, rec.lon)] for g in got]   
        if BASIS:
            pm = pd.to_numeric(rec.position_precision_m, errors="coerce")
            cov.update(recording_time_basis=rec.time_basis, recording_position_basis=rec.position_basis, recording_position_precision_m=None if pd.isna(pm) else float(pm), upstream_cut_filters=UPSTREAM)
        cov.update(duration_basis=rec.duration_basis, recording_time_grade=rec.time_grade, recording_time_precision_s=None if pd.isna(rec.time_precision_s) else (int(rec.time_precision_s) if float(rec.time_precision_s).is_integer() else float(rec.time_precision_s)),
                   flight_source="adsb.lol", flight_source_days=days, flight_source_caps=CAP,
                   receiver_aircraft_within_50km_utc_day=[p[0] for p in pl], receiver_points_within_50km_utc_day=[p[1] for p in pl],
                   receiver_utc_hours_with_aircraft_within_50km=[p[2] for p in pl], archive_utc_hours_without_data=[g[2] for g in got])
        cf.write(json.dumps(cov, allow_nan=False) + "\n"); nrec += 1; srcs[rec.source] = srcs.get(rec.source, 0) + 1
        if not any(p[0] for p in pl): blind[rec.source] = blind.get(rec.source, 0) + 1   # No aircraft within 50 km means zero passes are unknown.
        for p in passes:
            pf.write(json.dumps(p, allow_nan=False) + "\n"); npass += 1; first = first or p
for x in ("passes.jsonl.gz", "coverage.jsonl.gz"): os.replace(f"{work}/labels/{date}.{x}.part", f"{work}/labels/{date}.{x}")
json.dump(dict(date=date, label_hash=kv2.label_hash(r, str(E is not None)), recordings=nrec, passes=npass, by_source=srcs, no_receiver_coverage_by_source=blind, pending=pending, script_sha256=lp.hashlib.sha256(open(lp.__file__, "rb").read()).hexdigest()), open(f"{work}/labels/{date}.label.json.part", "w"))
os.replace(f"{work}/labels/{date}.label.json.part", f"{work}/labels/{date}.label.json")
print(f"label {date}: {nrec} recordings, {npass} passes, {len(pending)} pending", flush=True)
