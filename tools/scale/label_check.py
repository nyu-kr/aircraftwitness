#!/usr/bin/env python3

import os, sys, glob, json, gzip
from multiprocessing import Pool
W = sys.argv[1] if len(sys.argv) > 1 else "."

def ok(f):
    d = os.path.basename(f)[:10]
    try:
        L = json.load(open(f)); n = [sum(1 for _ in gzip.open(f"{W}/labels/{d}.{x}.jsonl.gz", "rt")) for x in ("passes", "coverage")]
        return d if n != [L["passes"], L["recordings"]] else None
    except Exception: return d

if __name__ == "__main__":
    with Pool(8) as p: bad = {d for d in p.map(ok, glob.glob(f"{W}/labels/*.label.json"), chunksize=8) if d}
    bad |= {os.path.basename(p)[:10] for p in glob.glob(f"{W}/labels/*.part")}
    print("\n".join(sorted(bad))); sys.exit(1 if bad else 0)
