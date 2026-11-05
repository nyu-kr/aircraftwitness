#!/usr/bin/env python3

import os, sys, math, numpy as np, pandas as pd, rasterio
from concurrent.futures import ThreadPoolExecutor
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR"); os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
W = sys.argv[1]; out = f"{W}/place_elev.parquet"
pl = pd.read_parquet(f"{W}/recs.parquet", columns=["lat", "lon"]).drop_duplicates()
old = pd.read_parquet(out) if os.path.exists(out) else pd.DataFrame(columns=["lat", "lon", "ground_m", "basis"])
pl = pl.merge(old[["lat", "lon"]].assign(_o=1), on=["lat", "lon"], how="left"); pl = pl[pl._o.isna()].drop(columns="_o")
if pl.empty: sys.exit(0)
stop = f"{W}/locks/stop.copernicus"
if os.path.exists(stop): sys.exit(f"copernicus stopped: {open(stop).read().strip()}")
pl["tile"] = [(math.floor(a), math.floor(b)) for a, b in zip(pl.lat, pl.lon)]
def name(a, b): return f"Copernicus_DSM_COG_30_{'N' if a >= 0 else 'S'}{abs(a):02d}_00_{'E' if b >= 0 else 'W'}{abs(b):03d}_00_DEM"
def one(tile):
    g = pl[pl.tile == tile]; n = name(*tile)
    try:
        with rasterio.Env(GDAL_HTTP_MAX_RETRY=3, GDAL_HTTP_RETRY_DELAY=60), rasterio.open(f"/vsicurl/https://copernicus-dem-90m.s3.amazonaws.com/{n}/{n}.tif") as ds:
            v = np.array([x[0] for x in ds.sample(zip(g.lon, g.lat))], float)
            return g.assign(ground_m=np.maximum(v, 0), basis="Copernicus GLO-90 DSM at the public coordinate (sea and below-sea clipped to 0)")
    except rasterio.errors.RasterioIOError as e:
        if "404" in str(e) or "does not exist" in str(e) or "not recognized" in str(e):
            return g.assign(ground_m=0.0, basis="no GLO-90 tile (sea): 0 m")
        open(stop, "w").write(f"{n}: {e}\n"); raise
tiles = sorted(set(pl.tile)); res = []
with ThreadPoolExecutor(4) as ex:
    for k in range(0, len(tiles), 400):   
        res += list(ex.map(one, tiles[k:k + 400])); new = pd.concat(res)[["lat", "lon", "ground_m", "basis"]]
        pd.concat([old, new], ignore_index=True).to_parquet(out + ".part", index=False); os.replace(out + ".part", out)
print(f"place_elev: {len(new)} new places on {len(res)} tiles, {int((new.basis.str.startswith('no')).sum())} on sea tiles; e.g. {new.iloc[0].to_dict()}", flush=True)
