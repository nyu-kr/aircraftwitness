
import hashlib
import numpy as np, pandas as pd, pyarrow.parquet as pq
PAD_S, MIN_KEEP_S = 600.0, 3600.0   
LABEL_COLS = ["recording_id", "source", "lat", "lon", "t0", "t1", "utc_start", "duration_s", "duration_basis", "public_latitude", "public_longitude", "time_grade", "time_precision_s"]

def keep_window(t0, t1):
    return t0 - PAD_S, max(t1, t0 + MIN_KEEP_S) + PAD_S

def day_bounds(date):
    ds = pd.Timestamp(date, tz="UTC").timestamp(); return ds, ds + 86400

def day_name(t):
    return pd.Timestamp(t, unit="s", tz="UTC").strftime("%Y-%m-%d")

def read_day(recs_path, date, cols):
    ds, de = day_bounds(date)   
    r = pq.read_table(recs_path, columns=sorted(set(cols) | {"t0", "t1"}), filters=[("t0", "<", de + PAD_S), ("t0", ">", ds - 40 * 86400)]).to_pandas()
    a = r.t0 - PAD_S; b = r[["t1"]].assign(m=r.t0 + MIN_KEEP_S).max(axis=1) + PAD_S
    return r[(a < de) & (b > ds)]

def keep_end(r):
    return r[["t1"]].assign(m=r.t0 + MIN_KEEP_S).max(axis=1)

def row_hashes(r, label=False):
    cols = LABEL_COLS if label else ["recording_id", "lat", "lon", "t0"]
    d = r[cols].copy()
    if not label: d["k"] = keep_end(r)
    return pd.util.hash_pandas_object(d, index=False).to_numpy()

def combine(h):   
    return f"{int(np.sum(h, dtype=np.uint64)):016x}-{len(h)}"

def filter_hash(r):
    return combine(row_hashes(r))

def label_hash(r, extra=""):
    return combine(row_hashes(r, True)) + extra
