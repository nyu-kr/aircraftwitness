#!/usr/bin/env python3
import argparse, gzip, json, os, random, re, sys, time
from datetime import datetime, timedelta, timezone
import lsc, manifest

HALF = 180; GUARD = int(os.environ.get("LS_GUARD", 600)); MAX_AGE = 48 * 3600; BUDGET = int(float(os.environ.get("LS_BUDGET_GB", 120)) * 1e9)
ts = lambda s: datetime.fromisoformat(re.sub(r"(\.\d{6})\d*", r"\1", s.replace("Z", "+00:00"))).timestamp()
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
day = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
LAB = {}


def log(P, *a):
    line = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ ") + " ".join(map(str, a))
    print(line, flush=True)


def labels(P, d, ext):
    p = f"{P['labels']}/{d}.{ext}.jsonl.gz"
    if not os.path.exists(p): return None
    k = (p, os.stat(p).st_mtime_ns)
    if k not in LAB:
        with gzip.open(p, "rt") as f: LAB[k] = [json.loads(l) for l in f]
    return LAB[k]


def ready(P, sc):
    # Neighbouring day files are needed to exclude their passes from no-pass windows.
    s = sc["fit"]["t_a_ms"] / 1000; e = s + sc["fit"]["dur_s"]
    if any(labels(P, d, "passes") is None for d in {day(s - 780), day(e - 1e-3), day(e + 780)}): return False
    cov = labels(P, day(e - 1e-3), "coverage")
    return cov is None or any(c["recording_id"] == sc["rid"] for c in cov)


def passes_of(P, mount, days):
    out = []
    for d in days:
        for p in labels(P, d, "passes") or []:
            if p["recording_id"][6:-17] == mount: out.append(p)
    return out


def write_window(P, sc, data, kind, ca, name, extra):
    a = lsc.off_at(sc, (ca - HALF) * 1000); b = lsc.off_at(sc, (ca + HALF) * 1000)
    s0 = sc["fit"]["t_a_ms"] / 1000; e0 = s0 + sc["fit"]["dur_s"]
    out = lsc.cut(data, sc["kind"], sc["header_bytes"], max(a, sc["header_bytes"]), min(b, len(data)))
    d = f"{P['keep']}/{sc['mount']}"; os.makedirs(d, exist_ok=True)
    fn = f"{sc['stamp']}_{name}.{sc['ext']}"
    lsc.write_atomic(f"{d}/{fn}", out)
    w = dict(file=fn, type=kind, start_utc=iso(max(ca - HALF, s0)), end_utc=iso(min(ca + HALF, e0)), clipped=ca - HALF < s0 or ca + HALF > e0, bytes=len(out), **extra)
    lsc.write_atomic(f"{d}/{fn}.json", json.dumps(w).encode())
    return w


def retire(P, sc, path, state, windows, reason):
    sc.update(state=state, windows=windows, reason=reason); sc.pop("arrival", None); sc.pop("index", None)
    lsc.save_sc(path + ".json.gz", sc)
    if os.path.exists(path + "." + sc["ext"]): os.unlink(path + "." + sc["ext"])


def process_mount(P, mount, segs, now):
    rdy = [x for x in segs if x[2]]
    if not rdy: return
    days = set()
    for _, sc, _ in rdy:
        s = sc["fit"]["t_a_ms"] / 1000; days |= {day(s - 86400), day(s), day(s + 86400 + 780)}
    allp = passes_of(P, mount, sorted(days)); cas = sorted(ts(p["closest_approach_utc"]) for p in allp)
    bysid = {sc["rid"]: (b, sc) for b, sc, _ in rdy}
    wins = {sc["rid"]: [] for _, sc, _ in rdy}; data = {}
    get = lambda rid: data.setdefault(rid, open(bysid[rid][0] + "." + bysid[rid][1]["ext"], "rb").read())
    mine = [p for p in allp if p["recording_id"] in bysid]
    for p in sorted(mine, key=lambda p: p["closest_approach_utc"]):
        rid = p["recording_id"]; sc = bysid[rid][1]; ca = ts(p["closest_approach_utc"])
        wins[rid].append(write_window(P, sc, get(rid), "pass", ca, "p" + iso(ca)[11:19].replace(":", ""),
                                      dict(pass_id=p.get("pass_id"), closest_approach_utc=p["closest_approach_utc"], slant_distance_m=p.get("slant_distance_m"))))
    chosen = []
    for p in sorted(mine, key=lambda p: p["closest_approach_utc"]):
        ca = ts(p["closest_approach_utc"]); rng = random.Random(p.get("pass_id") or ca); cand = []
        for rid, (b, sc) in bysid.items():
            s0 = sc["fit"]["t_a_ms"] / 1000; e0 = s0 + sc["fit"]["dur_s"]
            c = s0 + HALF
            while c + HALF <= e0:
                if all(abs(x - c) >= GUARD for x in cas) and all(abs(x - c) >= 2 * HALF for x in chosen):
                    dt = abs((c - ca) % 86400); dt = min(dt, 86400 - dt); cand.append((dt, rng.random(), rid, c))
                c += 60
        if not cand: log(P, "no no-pass window for", p["recording_id"], p["closest_approach_utc"]); continue
        dt, _, rid, c = min(cand); chosen.append(c)
        wins[rid].append(write_window(P, bysid[rid][1], get(rid), "nopass", c, "n" + iso(c)[11:19].replace(":", ""),
                                      dict(matched_pass_id=p.get("pass_id"), hour_matched=dt < 3600, hour_offset_s=round(dt))))
    for rid, (b, sc) in bysid.items():
        w = wins[rid]
        retire(P, sc, b, "kept" if w else "deleted", w, "labelled" if w else "labelled, no window kept")
        log(P, "labelled", rid, len(w), "windows kept,", sum(x["type"] == "pass" for x in w), "pass")


def run(P, now):
    segs = {}; total = 0
    for m in sorted(os.listdir(P["buf"])) if os.path.isdir(P["buf"]) else []:
        for fn in sorted(os.listdir(f"{P['buf']}/{m}")):
            if not fn.endswith(".json.gz"): continue
            b = f"{P['buf']}/{m}/{fn[:-8]}"
            try: sc = lsc.load_sc(b + ".json.gz")
            except Exception as e: log(P, "bad sidecar", b, e); continue
            sc["rid"] = f"LOCUS_{m}_{sc['stamp']}"
            if sc.get("state") != "buffered" or not sc.get("fit"): continue
            if not os.path.exists(b + "." + sc["ext"]): continue
            total += os.path.getsize(b + "." + sc["ext"])
            segs.setdefault(m, []).append([b, sc, ready(P, sc)])
    for m, ss in segs.items():
        try: process_mount(P, m, ss, now)
        except Exception as e: log(P, "prune failed", m, type(e).__name__, e)
    left = [(sc["segment_end_utc_ms"] / 1000, b, sc) for ss in segs.values() for b, sc, r in ss if os.path.exists(b + "." + sc["ext"])]
    left.sort(); size = sum(os.path.getsize(b + "." + sc["ext"]) for _, b, sc in left)
    for end, b, sc in left:
        why = "unlabelled older than 48 h" if now - end > MAX_AGE else "over disk budget" if size > BUDGET else None
        if not why: continue
        size -= os.path.getsize(b + "." + sc["ext"]); retire(P, sc, b, "deleted", [], why); log(P, "deleted", sc["rid"], why)
    return len(left)


def settle(P):
    if not os.path.isdir(P["buf"]): return 0
    n = 0
    for m in os.listdir(P["buf"]):
        for fn in os.listdir(f"{P['buf']}/{m}"):
            if not fn.endswith(".json.gz"): continue
            p = f"{P['buf']}/{m}/{fn}"
            sc = lsc.load_sc(p)
            if sc.get("state") in ("kept", "deleted") and not os.path.exists(p[:-8] + "." + sc["ext"]):
                os.makedirs(f"{P['buf']}/{m}/done", exist_ok=True); os.replace(p, f"{P['buf']}/{m}/done/{fn}"); n += 1
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true"); ap.add_argument("--root"); ap.add_argument("--now")
    a = ap.parse_args(); P = lsc.paths(a.root) if a.root else lsc.paths()
    if a.root: P["labels"] = os.environ.get("LS_LABELS", P["labels"])
    while True:
        now = ts(a.now) if a.now else time.time()
        n = run(P, now); ch, rw = manifest.update(P, force=a.once); s = settle(P)
        log(P, f"buffered {n} segments, manifest +{ch} rows (gz rewritten: {rw}), {s} sidecars settled")
        if a.once: break
        time.sleep(600)
