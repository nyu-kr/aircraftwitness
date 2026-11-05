#!/usr/bin/env python3

import sys, os, io, gzip, json, math, tarfile, time
import numpy as np, pandas as pd, pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kv2
try:
    import orjson; loads = orjson.loads
except ImportError:
    loads = json.loads
date, recs_path, outdir, *parts = sys.argv[1:]
KEEP_KM, COVER_KM, G = 15.0, 50.0, 0.5
ds, de = kv2.day_bounds(date)
r = kv2.read_day(recs_path, date, ["recording_id", "lat", "lon"])
assert len(r), f"no recordings on {date}"
recs_hash = kv2.filter_hash(r)
pl = r.groupby(["lat", "lon"], sort=True).size().reset_index()[["lat", "lon"]]
pl["place"] = np.arange(len(pl)); r = r.merge(pl, on=["lat", "lon"])
P_lat, P_lon = pl.lat.to_numpy(), pl.lon.to_numpy()


K = 1e6; w = []
for p, g in r.groupby("place"):
    iv = sorted((max(a, ds - 60) - ds, min(b, de + 60) - ds) for a, b in (kv2.keep_window(x, y) for x, y in zip(g.t0, g.t1)))
    a, b = iv[0]
    for x, y in iv[1:]:
        if x <= b: b = max(b, y)
        else: w.append((p * K + a, p * K + b)); a, b = x, y
    w.append((p * K + a, p * K + b))
W0, W1 = np.array([x for x, _ in w]), np.array([y for _, y in w])


cell_places = {}
for p, (la, lo) in enumerate(zip(P_lat, P_lon)):
    dla = COVER_KM / 111.19; dlo = min(COVER_KM / (111.19 * max(math.cos(math.radians(la)), 0.01)), 180)
    for i in range(math.floor((max(la - dla, -90) + 90) / G), math.floor((min(la + dla, 89.999) + 90) / G) + 1):
        for j in range(math.floor((lo - dlo + 180) / G), math.floor((lo + dlo + 180) / G) + 1):
            cell_places.setdefault(i * 720 + j % 720, []).append(p)
C = np.array(sorted(cell_places)); ptr = np.zeros(len(C) + 1, np.int64)
ptr[1:] = np.cumsum([len(cell_places[c]) for c in C]); plist = np.concatenate([np.array(cell_places[c]) for c in C])

class Chain(io.RawIOBase):
    def __init__(s, fs): s.fs = [open(f, "rb") for f in fs]; s.i = 0
    def readable(s): return True
    def readinto(s, b):
        while s.i < len(s.fs):
            n = s.fs[s.i].readinto(b)
            if n: return n
            s.i += 1
        return 0

def hav_m(la1, lo1, la2, lo2):
    p, q = np.radians(la1), np.radians(la2); dl = np.radians(lo2 - lo1)
    h = np.sin((q - p) / 2) ** 2 + np.cos(p) * np.cos(q) * np.sin(dl / 2) ** 2
    return 6371008.8 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))

def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else np.nan

cover_ac = [set() for _ in range(len(pl))]; cover_hours = np.zeros(len(pl), np.int64); cover_pts = np.zeros(len(pl), np.int64)
hours_all = np.zeros(48, np.int64)
cols = {k: [] for k in ("place", "icao", "t", "lat", "lon", "alt", "galt", "gs", "trk", "src", "type", "gnd", "flags")}
n = bad = npts = 0; t_begin = time.time()
with tarfile.open(fileobj=io.BufferedReader(Chain(parts), 1 << 20), mode="r|*") as tf:
    for m in tf:
        if not (m.isfile() and "trace_full" in m.name): continue
        n += 1
        try:
            raw = tf.extractfile(m).read()
            tr = loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        except Exception:
            bad += 1; continue
        pts = tr.get("trace") or []
        if not pts: continue
        k = len(pts); npts += k
        lat = np.fromiter((p[1] for p in pts), float, k); lon = np.fromiter((p[2] for p in pts), float, k)
        t = tr["timestamp"] + np.fromiter((p[0] for p in pts), float, k)
        h = ((t - ds) // 3600).astype(np.int64) + 24
        hours_all += np.bincount(np.clip(h, 0, 47), minlength=48)
        cid = np.floor((lat + 90) / G).astype(np.int64) * 720 + np.floor((lon + 180) / G).astype(np.int64) % 720
        pos = np.minimum(np.searchsorted(C, cid), len(C) - 1); hit = np.nonzero(C[pos] == cid)[0]
        if not len(hit): continue
        a, b = ptr[pos[hit]], ptr[pos[hit] + 1]; cnt = b - a
        pi = np.repeat(hit, cnt); pp = plist[np.repeat(a - np.cumsum(cnt) + cnt, cnt) + np.arange(cnt.sum())]
        d = hav_m(lat[pi], lon[pi], P_lat[pp], P_lon[pp])
        inday = (t[pi] >= ds) & (t[pi] < de)
        c = (d <= COVER_KM * 1000) & inday
        if c.any():
            icao = tr["icao"]
            for p in np.unique(pp[c]): cover_ac[p].add(icao)
            np.add.at(cover_pts, pp[c], 1)
            np.bitwise_or.at(cover_hours, pp[c], np.left_shift(1, h[pi[c]] - 24))
        near = np.nonzero(d <= KEEP_KM * 1000)[0]
        if not len(near): continue
        key = pp[near] * K + (t[pi[near]] - ds); j = np.searchsorted(W0, key, side="right") - 1
        ok = (j >= 0) & (key <= W1[np.maximum(j, 0)]); near = near[ok]
        if not len(near): continue
        icao, typ = tr["icao"], tr.get("t")
        for x in near:
            q = pts[pi[x]]; fl = q[6] if isinstance(q[6], int) else 0
            alt = q[3]; gnd = alt == "ground"; alt = np.nan if gnd else num(alt); galt = num(q[10]) if len(q) > 10 else np.nan
            if fl & 8 and not math.isnan(alt):   # The readsb flag 8 marks geometric rather than barometric altitude.
                galt = alt if math.isnan(galt) else galt; alt = np.nan
            cols["place"].append(int(pp[x])); cols["icao"].append(icao); cols["t"].append(float(t[pi[x]])); cols["lat"].append(q[1]); cols["lon"].append(q[2])
            cols["alt"].append(alt); cols["galt"].append(galt); cols["gs"].append(num(q[4])); cols["trk"].append(num(q[5]))
            cols["src"].append(q[9] if len(q) > 9 and isinstance(q[9], str) else None); cols["type"].append(typ); cols["gnd"].append(bool(gnd)); cols["flags"].append(fl)
assert n > 1000, f"{date}: only {n} trace files"
assert bad <= 0.001 * n + 3, f"{date}: {bad} of {n} trace files unreadable"
os.makedirs(outdir, exist_ok=True)
pts_t = pa.table({k: pa.array(v, type=pa.string() if k in ("icao", "src", "type") else None) for k, v in cols.items()}) if cols["t"] else pa.table({"place": pa.array([], pa.int64()), **{k: pa.array([], pa.string() if k in ("icao", "src", "type") else (pa.bool_() if k == "gnd" else (pa.int64() if k == "flags" else pa.float64()))) for k in cols if k != "place"}})
pq.write_table(pts_t, f"{outdir}/{date}.points.parquet.part", compression="zstd")
pl["aircraft_50km"] = [len(s) for s in cover_ac]; pl["points_50km"] = cover_pts; pl["hours_mask_50km"] = cover_hours
pl.to_parquet(f"{outdir}/{date}.places.parquet.part")
meta = dict(date=date, recs_hash=recs_hash, trace_files=n, unreadable=bad, points=npts, places=len(pl), recordings=len(r), kept_points=len(cols["t"]),
            points_per_utc_hour=hours_all[24:48].tolist(), parts=[[os.path.basename(f), os.path.getsize(f)] for f in parts], filter_s=round(time.time() - t_begin, 1),
            keep_km=KEEP_KM, cover_km=COVER_KM, pad_s=kv2.PAD_S, min_keep_s=kv2.MIN_KEEP_S)
json.dump(meta, open(f"{outdir}/{date}.filter.json.part", "w"))
for x in ("points.parquet", "places.parquet", "filter.json"): os.replace(f"{outdir}/{date}.{x}.part", f"{outdir}/{date}.{x}")
print(f"filter {date}: {n} traces, {npts} points, {len(pl)} places, {len(r)} recordings, kept {len(cols['t'])}, {meta['filter_s']} s", flush=True)
