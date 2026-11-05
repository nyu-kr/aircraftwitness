#!/usr/bin/env python3


import os, re, sys, json, time, glob, hashlib, subprocess, urllib.request
import numpy as np, pandas as pd, pyarrow.parquet as pq
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import kv2
W = os.environ.get("KV2", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(W); os.makedirs("logs", exist_ok=True)
CPU_MAX = float(os.environ.get("KV2_CPU_MAX", "750"))   
log = lambda *a: print(time.strftime("%m-%d %H:%M:%S"), *a, flush=True)

def probe(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"Range": "bytes=0-0"}), timeout=60) as x: return x.status in (200, 206)
    except Exception: return False

def releases():

    f = "releases.json"; best = json.load(open(f)) if os.path.exists(f) else {}
    use = lambda b: {d: t for d, t in b.items() if t != "none"}   
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 3600: return use(best)   
    for l in open("tools/known_tags.txt"):
        d, t = l.split(); best[d] = t   
    d = pd.Timestamp("2023-02-16"); end = pd.Timestamp.now("UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)
    while d <= end:
        k = d.strftime("%Y-%m-%d")
        if k not in best:
            for t in ("prod-0", "prod-0tmp", "prod-1", "test-0", "staging-0"):
                tag = f"v{d.strftime('%Y.%m.%d')}-planes-readsb-{t}"
                bases = [f"https://github.com/adsblol/globe_history_{y}/releases/download/{tag}/{tag}.tar" for y in (d.year, d.year + 1)]   # December 31 is stored in the next year repository.
                if any(probe(b) or probe(b + ".aa") for b in bases): best[k] = tag; log("new release", k, tag); break
        d += pd.Timedelta(days=1)
    json.dump(best, open(f + ".tmp", "w")); os.replace(f + ".tmp", f)
    return use(best)

def day_hashes():
    r = pq.read_table("recs.parquet", columns=kv2.LABEL_COLS).to_pandas()
    a = r.t0 - kv2.PAD_S; b = kv2.keep_end(r) + kv2.PAD_S
    d0, d1 = (a // 86400).astype(int).to_numpy(), ((b - 1e-3) // 86400).astype(int).to_numpy(); n = d1 - d0 + 1   # A half open window ending at midnight excludes the following day.
    i = np.repeat(np.arange(len(r)), n); d = np.repeat(d0, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    hf, hl = kv2.row_hashes(r), kv2.row_hashes(r, True)
    name = lambda k: time.strftime("%Y-%m-%d", time.gmtime(int(k) * 86400))
    o = np.argsort(d, kind="stable"); ds, ix = d[o], i[o]; cut = np.flatnonzero(np.diff(ds)) + 1
    fh = {name(g[0]): (kv2.combine(hf[ix[s:e]]), e - s) for g, s, e in ((ds[s:e], s, e) for s, e in zip(np.r_[0, cut], np.r_[cut, len(ds)]))}
    de = ((r.t1 - 1e-3) // 86400).astype(int).to_numpy(); o = np.argsort(de, kind="stable"); ds = de[o]; cut = np.flatnonzero(np.diff(ds)) + 1
    lh = {name(ds[s]): kv2.combine(hl[o[s:e]]) + "True" for s, e in zip(np.r_[0, cut], np.r_[cut, len(ds)])}
    urgent = {name(k) for k in set((r.t0[r.source == "locustream"] // 86400).astype(int))}   
    return fh, lh, urgent

def label_hash_done(d):
    try: return json.load(open(f"labels/{d}.label.json"))["label_hash"]
    except (OSError, KeyError, ValueError): return None

def done_hash(d):
    try: return json.load(open(f"points/{d}.filter.json"))["recs_hash"]
    except (OSError, KeyError, ValueError): return None

def account_cpu():
    out = subprocess.run(["top", "-b", "-n", "2", "-d", "2", "-u", os.environ.get("USER") or __import__("getpass").getuser()], capture_output=True, text=True).stdout
    last = out.split("top - ")[-1].splitlines()
    i = next((k for k, l in enumerate(last) if l.split()[:1] == ["PID"]), None)
    return sum(float(l.split()[8]) for l in last[i + 1:] if len(l.split()) > 8) if i is not None else 0.0

running, fails, held, mt = {}, {}, {}, None
while True:
    for d, p in list(running.items()):
        if p.poll() is not None:
            rc = p.returncode; del running[d]
            if rc == 75: log("skip", d, "(already running elsewhere)"); held[d] = time.time()
            elif rc: fails[d] = fails.get(d, 0) + 1; log("FAIL", d, "rc", rc, "try", fails[d])
            else: log("done", d)
    if os.path.exists("STOP"):
        if not running: log("STOP file: exiting"); break
        time.sleep(30); continue
    mans = sorted(glob.glob("manifests/*.csv.gz")); sig = json.dumps([(m, os.path.getsize(m), os.path.getmtime(m)) for m in mans])
    if mans and (not os.path.exists("recs.parquet") or not os.path.exists("recs.sig") or open("recs.sig").read() != sig):
        rc = subprocess.run([sys.executable, "tools/recs_build.py", "recs.parquet", *mans], stdout=open("recs_build.log", "a"), stderr=subprocess.STDOUT).returncode
        log("recs rebuilt from", len(mans), "manifests, rc", rc)
        if rc == 0: open("recs.sig", "w").write(sig)
    if mt != os.path.getmtime("recs.parquet"): (hashes, lhashes, urgent), mt = day_hashes(), os.path.getmtime("recs.parquet"); log("recs", len(hashes), "days with recordings")
    rel = releases()
    pri = urgent | (set(open("URGENT").read().split()) if os.path.exists("URGENT") else set())   
    todo = sorted((d for d, (h, n) in hashes.items() if d in rel and d not in running and time.time() - held.get(d, 0) > 900 and done_hash(d) != h and fails.get(d, 0) < 3), key=lambda d: (d not in pri, os.path.exists(f"points/{d}.filter.json"), -hashes[d][1]))   # Refiltering waits for new days so manifest changes can coalesce.
    relabel = [d for d, h in lhashes.items() if d not in running and d in hashes and done_hash(d) == hashes[d][0] and label_hash_done(d) != h and fails.get(d, 0) < 3]
    for d in relabel[:20]:
        rc = subprocess.run(["bash", "tools/run_day.sh", d], env={**os.environ, "KV2_LABEL_ONLY": "1"}, stdout=open(f"logs/{d}.log", "a"), stderr=subprocess.STDOUT).returncode
        if rc and rc != 75: fails[d] = fails.get(d, 0) + 1; log("FAIL label", d, "rc", rc)
    nolol = sum(1 for d in hashes if d not in rel and d >= "2023-02-16")
    json.dump(dict(t=time.strftime("%Y-%m-%dT%H:%M:%S"), days_with_recordings=len(hashes), days_done=sum(done_hash(d) == h for d, (h, n) in hashes.items()), relabel=len(relabel),
                   todo=len(todo), todo_recordings=int(sum(hashes[d][1] for d in todo)), running=sorted(running), failed={d: n for d, n in fails.items() if n}, days_without_adsblol_release=nolol), open("status.json", "w"))
    if time.time() - (os.path.getmtime("refresh.stamp") if os.path.exists("refresh.stamp") else 0) > 7 * 86400:   
        open("refresh.stamp", "w").close(); log("weekly manifest refresh started (logs/refresh.log)")
        subprocess.Popen(["nice", "-n", "15", "bash", "tools/refresh.sh"], stdout=open("logs/refresh.out", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    lanes = int(open("LANES").read().strip()) if os.path.exists("LANES") else 2
    if todo and len(running) < lanes and account_cpu() < CPU_MAX:
        d = todo[0]; log("start", d, hashes[d][1], "recordings")
        running[d] = subprocess.Popen(["bash", "tools/run_day.sh", d, rel[d]], stdout=open(f"logs/{d}.log", "a"), stderr=subprocess.STDOUT)
        time.sleep(20); continue
    if not todo and not running: log("idle: nothing to do"); time.sleep(3600)
    time.sleep(60)
