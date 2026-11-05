#!/usr/bin/env python3
# fit.py SRC DIR: the Livermore recorder position from the publisher's per-pass distances and ADS-B tracks.
# DIR holds the unzipped Zenodo record 10.5281/zenodo.23424278; SRC is a dopplerprint checkout at 447dbed5f678, whose trilaterate() runs unmodified.
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
src, zdir = (Path(a) for a in sys.argv[1:3])
sys.path.insert(0, str(src))
from tools.corpus import livermore as LV

passages = []
for row in csv.DictReader(open(Path(__file__).with_name("fit_passes.csv"))):
    kml = zdir / row["category"] / row["case"] / row["track_file"]
    if hashlib.sha256(kml.read_bytes()).hexdigest() != row["kml_sha256"]:
        sys.exit(f"fit: {kml} differs from the published track")
    t_first, t_last = LV._iso(row["t_first_utc"]), LV._iso(row["t_last_utc"])
    passages.append(dict(row=row, kml=kml, L=float(row["L_ADSB_m"]), H=float(row["H_ADSB_above_recorder_m"]),
                         t_first=t_first, t_last=t_last, t_ref=(t_first + t_last) / 2))
first = LV.trilaterate(passages)
groups = {"main": [p for p, r in zip(passages, first["residuals_m"]) if abs(r) < 150]}
outliers = [p for p, r in zip(passages, first["residuals_m"]) if abs(r) >= 150]
if len(outliers) >= 3:
    groups["outlier_group"] = outliers
out = {"all": {k: first[k] for k in ("lat", "lon", "rms_m", "median_abs_m")} | {"n": len(passages)}}
for name, ps in groups.items():
    t = LV.trilaterate(ps)
    out[name] = {k: t[k] for k in ("lat", "lon", "rms_m", "median_abs_m", "alt_msl_m_median")} | {"n": len(ps), "cases": [p["row"]["case"] for p in ps]}
print(json.dumps(out, indent=1))
