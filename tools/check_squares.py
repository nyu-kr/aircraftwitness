#!/usr/bin/env python3

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HALF = {'T1': 10, 'T2-date': 50}


def main():
    records = {r['recording_id']: r for r in csv.DictReader((ROOT / 'data/recordings.csv').open(newline=''))}
    test = set(json.loads((ROOT / 'data/task_cohorts.json').read_text())['A2O batch 3'])
    rows = list(csv.DictReader((ROOT / 'data/b3_blind_squares.csv').open(newline='')))
    keys = [(r['recording_id'], r['task']) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != {(rid, t) for rid in test for t in HALF}:
        sys.exit('b3_blind_squares.csv: need exactly one T1 and one T2-date square per A2O batch 3 recording')
    for r in rows:
        lat, lon = float(records[r['recording_id']]['public_latitude']), float(records[r['recording_id']]['public_longitude'])
        s, n, w, e = (float(r[k]) for k in ('lat_min', 'lat_max', 'lon_min', 'lon_max'))
        side_ns, side_ew = (n - s) * 111.195, (e - w) * 111.195 * math.cos(math.radians((s + n) / 2))
        if not (s < lat < n and w < lon < e) or max(abs(side_ns - 2 * HALF[r['task']]), abs(side_ew - 2 * HALF[r['task']])) > 0.5:
            sys.exit(f"{r['recording_id']} {r['task']}: square does not hold the published position or has sides {side_ns:.2f} x {side_ew:.2f} km")
    print(f'b3 blind squares: {len(rows)} rows, every square holds its recording and has the task\'s side')


if __name__ == '__main__':
    main()
