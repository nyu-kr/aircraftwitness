#!/usr/bin/env python3

import os, sys, glob, hashlib, pandas as pd, numpy as np
out, *mans = sys.argv[1:]
LOL0 = pd.Timestamp("2023-02-16", tz="UTC").timestamp()
MAX_PREC_M = 2000          # Positions coarser than 2 km cannot support a 10 km pass label.
DEFAULT_DUR = 300.0
COLS = ["recording_id", "source", "lat", "lon", "t0", "t1", "utc_start", "duration_s", "duration_basis", "public_latitude", "public_longitude", "time_grade", "time_precision_s", "time_basis", "position_basis", "position_precision_m"]
CODE = hashlib.sha256(open(__file__, "rb").read()).hexdigest()[:12] + ("n" if os.environ.get("KV2_LABEL_NO_AUDIO") else "")
PARTS = os.path.join(os.path.dirname(os.path.abspath(out)), "recs_parts"); os.makedirs(PARTS, exist_ok=True)
keep = []
for m in mans:
    st = os.stat(m); b = os.path.basename(m); key = f"{PARTS}/{b}.{st.st_size}.{st.st_mtime_ns}.{CODE}.parquet"   
    if os.path.exists(key): keep.append(pd.read_parquet(key)); continue
    d = pd.read_csv(m, dtype=str, keep_default_na=False)
    n0 = len(d)
    d = d[(d.utc_start != "") & (d.public_latitude != "") & (d.public_longitude != "")]
    if not os.environ.get("KV2_LABEL_NO_AUDIO"): d = d[(d.audio_access != "none") | (d.source == "locustream")]   
    prec = pd.to_numeric(d.position_precision_m, errors="coerce")
    d = d[((d.obscured != "true") & ~(prec > MAX_PREC_M)) | (prec <= MAX_PREC_M)]
    t0 = pd.to_datetime(d.utc_start, utc=True, errors="coerce")
    d = d[t0.notna()].copy(); d["t0"] = (t0[t0.notna()] - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    dur = pd.to_numeric(d.duration_s, errors="coerce")
    d["duration_basis"] = np.where(dur.notna(), "source metadata", f"assumed {DEFAULT_DUR:.0f} s: source gives no duration")
    d["duration_s"] = dur.fillna(DEFAULT_DUR)
    d["t1"] = d.t0 + d.duration_s
    d["lat"] = d.public_latitude.astype(float); d["lon"] = d.public_longitude.astype(float)
    d = d[(d.t0 >= LOL0) & d.lat.between(-90, 90) & d.lon.between(-180, 180)]
    print(f"{m}: {n0} rows, {len(d)} labelable from 2023-02-16", flush=True)
    d = d[COLS]; d.to_parquet(key + f".part{os.getpid()}", index=False); os.replace(key + f".part{os.getpid()}", key); keep.append(d)
    for o in glob.glob(f"{PARTS}/{b}.*.parquet"):
        if o != key: os.remove(o)
r = pd.concat(keep, ignore_index=True)
assert not r.recording_id.duplicated().any(), r.recording_id[r.recording_id.duplicated()].head().tolist()
r["time_precision_s"] = pd.to_numeric(r.time_precision_s, errors="coerce")
part = f"{out}.part{os.getpid()}"   # A scheduler restart can leave a second build running.
r.sort_values("t0").to_parquet(part, row_group_size=50000, index=False); os.replace(part, out)
print(f"{out}: {len(r)} recordings, {r.t0.min()}..{r.t1.max()}, {pd.to_datetime(r.t0, unit='s').dt.date.nunique()} start days")
