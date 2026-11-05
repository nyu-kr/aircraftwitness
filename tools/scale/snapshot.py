#!/usr/bin/env python3


import os, sys, re, json, gzip, glob, hashlib, time
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd, pyarrow.parquet as pq, pyarrow.csv as pc, jsonschema, kv2
W = os.environ.get("KV2", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(W); tag = sys.argv[1]; S = f"snapshots/{tag}"
assert not os.path.exists(S), f"{S} exists"
SCHEMA = jsonschema.Draft202012Validator(json.load(open("tools/coverage.schema.json")))
UPSTREAM = "adsb.lol trace_full as published: all altitudes, airborne and ground positions (ground flagged per sample); positions within 15 km of the public place from start-10 min to max(end, start+1 h)+10 min; reception completeness unknown"
r = pq.read_table("recs.parquet", columns=kv2.LABEL_COLS).to_pandas()
end = ((r.t1 - 1e-3) // 86400).astype(int).to_numpy(); hl = kv2.row_hashes(r, True); o = np.argsort(end, kind="stable"); es = end[o]
cut = np.flatnonzero(np.diff(es)) + 1
want = {time.strftime("%Y-%m-%d", time.gmtime(int(es[s]) * 86400)): kv2.combine(hl[o[s:e]]) + "True" for s, e in zip(np.r_[0, cut], np.r_[cut, len(es)])}
n_listed = len(r); del r, end, hl, o, es
acc, terms = {}, {}   
for m in glob.glob("manifests/*.csv.gz"):
    C = ["recording_id", "source", "audio_access", "audio_licence", "redistributable"]
    t = pc.read_csv(m, convert_options=pc.ConvertOptions(include_columns=C, column_types={c: "string" for c in C})).to_pandas()
    for src, g in t.groupby("source"):
        u = g.audio_access.unique(); acc[src] = u[0] if len(u) == 1 else dict(zip(g.recording_id, g.audio_access))
    k = os.path.basename(m)[:-7]   
    terms[k] = dict(audio_licences=t.audio_licence.value_counts().head(6).to_dict(),
                    audio_redistributable_rows=t.redistributable.value_counts().to_dict(), audio_access_rows=t.audio_access.value_counts().to_dict())
access = lambda src, rid: (acc[src] if isinstance(acc.get(src), str) else acc.get(src, {}).get(rid)) or "unknown"
key = lambda s: "gbif-sounds" if s.startswith("gbif:") else s
RID = re.compile(r'\{"recording_id": "([^"]+)", "source": "([^"]+)"')
CA = re.compile(r'"closest_approach_utc": "([^"]+)"')
WIN = ("inside_source_duration", "inside_assumed_window", "outside_recording")   

def basis(d):   
    ds, de = kv2.day_bounds(d)
    b = pq.read_table("recs.parquet", columns=["recording_id", "t0", "t1", "time_basis", "position_basis", "position_precision_m"], filters=[("t0", "<", de), ("t0", ">", ds - 40 * 86400)]).to_pandas()
    b = b[(b.t1 - 1e-3 >= ds) & (b.t1 - 1e-3 < de)]; pm = pd.to_numeric(b.position_precision_m, errors="coerce")
    return {k: (tb, pb, None if np.isnan(p) else float(p)) for k, tb, pb, p in zip(b.recording_id, b.time_basis, b.position_basis, pm)}

def freeze(d):   # A day is frozen only when its receipt matches the row counts.
    for _ in range(5):
        got = []
        for x in (f"labels/{d}.label.json", f"labels/{d}.passes.jsonl.gz", f"labels/{d}.coverage.jsonl.gz", f"points/{d}.filter.json"):
            dst = f"{S}/{'days' if x.startswith('points') else 'labels'}/{os.path.basename(x)}"
            if os.path.exists(dst): os.remove(dst)
            os.link(x, dst); got.append(dst)
        L = json.load(open(got[0])); n = [sum(1 for _ in gzip.open(g, "rt")) for g in got[1:3]]
        if n == [L["passes"], L["recordings"]] and want.get(d) == L["label_hash"]: return L, got
        time.sleep(30)
    raise SystemExit(f"{d}: files keep changing")

def day(d):
    L, got = freeze(d); cov = got[2]; by_acc, wp, bad, B = {}, {}, 0, None
    rows = [json.loads(l) for l in gzip.open(cov, "rt")]
    if rows and "recording_time_basis" not in rows[0]:
        B = basis(d)
        for c in rows:
            tb, pb, pm = B[c["recording_id"]]; c.update(recording_time_basis=tb, recording_position_basis=pb, recording_position_precision_m=pm, upstream_cut_filters=UPSTREAM)
        os.remove(cov)   
        with gzip.open(cov + ".tmp", "wt") as f: f.write("".join(json.dumps(c, allow_nan=False) + "\n" for c in rows))
        os.replace(cov + ".tmp", cov)
    for c in rows:
        bad += any(True for _ in SCHEMA.iter_errors(c)); k = key(c["source"]); by_acc.setdefault((k, access(c["source"], c["recording_id"])), [0, 0, 0])[0] += 1
    span = {c["recording_id"]: (c["recording_start_utc"][:19], c["recording_end_utc"][:19], c["duration_basis"] == "source metadata") for c in rows}; win = {}
    for l in gzip.open(got[1], "rt"):
        m = RID.match(l); k = key(m[2]); a = by_acc.setdefault((k, access(m[2], m[1])), [0, 0, 0]); a[2] += 1
        t0, t1, known = span[m[1]]; ca = CA.search(l)[1][:19]; w = win.setdefault(k, [0, 0, 0]); w[0 if known and t0 <= ca <= t1 else 1 if t0 <= ca <= t1 else 2] += 1
        if m[1] not in wp.setdefault(k, set()): wp[k].add(m[1]); a[1] += 1
    rows_in = {got[0]: 1, got[1]: L["passes"], got[2]: L["recordings"], got[3]: 1}
    return L, {g[len(S) + 1:]: dict(bytes=os.path.getsize(g), sha256=hashlib.sha256(open(g, "rb").read()).hexdigest(), rows=rows_in[g]) for g in got}, by_acc, {k: len(v) for k, v in wp.items()}, bad, B is not None, win

fresh, stale = [], 0
for f in sorted(glob.glob("labels/*.label.json")):
    L = json.load(open(f))
    if want.get(L["date"]) == L["label_hash"]: fresh.append(L["date"])   
    else: stale += 1
os.makedirs(f"{S}/labels"); os.makedirs(f"{S}/days")
with Pool(6) as p: res = p.map(day, fresh, chunksize=4)
Ls = [x[0] for x in res]; files = {k: v for x in res for k, v in x[1].items()}; by_acc = {}; wp = {}; win = {}
for x in res:
    for k, v in x[2].items(): by_acc[k] = [a + b for a, b in zip(by_acc.get(k, [0, 0, 0]), v)]
    for k, v in x[3].items(): wp[k] = wp.get(k, 0) + v
    for k, v in x[6].items(): win[k] = [a + b for a, b in zip(win.get(k, [0, 0, 0]), v)]
bad = sum(x[4] for x in res); assert bad == 0, f"{bad} coverage rows fail tools/coverage.schema.json"
rec_src = {}
for L in Ls:
    for s, n in L["by_source"].items(): rec_src[key(s)] = rec_src.get(key(s), 0) + n
tri = lambda v: dict(zip(("recordings", "recordings_with_pass", "passes"), [int(x) for x in v]))
out = dict(tag=tag, frozen_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), flight_source="adsb.lol globe_history (ODbL-1.0)",
           days=len(Ls), first_day=Ls[0]["date"] if Ls else None, last_day=Ls[-1]["date"] if Ls else None,
           recordings=sum(L["recordings"] for L in Ls), recordings_with_pass=sum(wp.values()), passes=sum(L["passes"] for L in Ls), sources=len(rec_src),
           audio_access_note="download/fetch: audio public; request: audio only from the owner on request, not usable audio as released; none: nobody can obtain it",
           by_audio_access={a: tri(np.sum([v for (k, aa), v in by_acc.items() if aa == a], axis=0)) for a in sorted({a for _, a in by_acc})},
           by_source={k: dict(recordings=n, recordings_with_pass=wp.get(k, 0), passes=sum(v[2] for (kk, a), v in by_acc.items() if kk == k),
                              by_audio_access={a: tri(v) for (kk, a), v in sorted(by_acc.items()) if kk == k}, passes_by_window=dict(zip(WIN, win.get(k, [0, 0, 0]))), audio_terms=terms.get(k)) for k, n in sorted(rec_src.items(), key=lambda x: -x[1])},
           passes_by_window=dict(zip(WIN, np.sum([v for v in win.values()], axis=0).tolist() if win else [0, 0, 0])),
           passes_by_window_note="closest approach inside the recording's start..end with the source's duration; inside an assumed 300 s window (the source gives no duration: the audio may be shorter); or outside the recording (the aircraft was within 10 km during it but closest before or after)",
           licence_labels="ODbL-1.0 (derived from the adsb.lol database); audio is never bundled: fetch by manifest audio_url under each source's terms (audio_terms)",
           no_receiver_coverage_recordings=sum(sum(L.get("no_receiver_coverage_by_source", {}).values()) for L in Ls),
           no_receiver_coverage_counted_days=sum("no_receiver_coverage_by_source" in L for L in Ls),
           days_given_v1_basis_fields_from_recs=sum(x[5] for x in res), coverage_schema="data/v2/coverage.schema.json (every row valid)",
           pending_recordings_left_out=sum(len(L["pending"]) for L in Ls), labelable_recordings_listed=n_listed, days_with_labelable_recordings=len(want),
           stale_days_left_out=stale, files=files)
json.dump(out, open(f"{S}.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k not in ("files", "by_source")}))
