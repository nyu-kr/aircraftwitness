#!/usr/bin/env python3
import csv, gzip, io, json, os, time
from datetime import datetime, timezone
import lsc

COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
BASIS = ("chrony-synced host clock at byte arrival; audio start fitted from the arrival log (audio clock from MP3 frames / Ogg granules, "
         "Icecast burst removed); no timestamps in the stream, stream latency unknown, precision about 10 s")
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
dec = lambda s: len(s.split(".")[1]) if "." in s else 0
# Geocoded town centres do not identify microphone locations and must not be labelled.
CENTRE = {"372": "London", "2192": "Lancaster PA", "2567": "Drigg", "2588": "Amsterdam"}


def row(sc, P):
    f = sc["fit"]; ta = f["t_a_ms"] / 1000; u = iso(round(ta)); w = sc.get("windows") or []
    path = f"{P['buf']}/{sc['mount']}/{sc['stamp']}.{sc['ext']}"
    if sc["state"] == "kept" and w: path = f"{P['keep']}/{sc['mount']}/{w[0]['file']}"
    ex = dict(mount=sc["mount"], site_id=sc["site_id"], stream_url=sc["stream_url"], stamp=sc["stamp"], bytes=sc["bytes"], rate_Bps=f["rate_Bps"],
              fit_resid_s=f["resid_s"], state=sc["state"], new_connection=sc["new_connection"])
    if "burst_bytes" in f: ex.update(burst_bytes=f["burst_bytes"], burst_s=f["burst_s"])
    if w: ex["windows"] = [{k: x[k] for k in ("file", "type", "start_utc", "end_utc")} for x in w]
    if sc["state"] != "deleted": ex["capture_path"] = os.path.relpath(path, P["root"])   # Relative paths avoid publishing account names.
    return dict(recording_id=f"LOCUS_{sc['mount']}_{sc['stamp']}", source="locustream", source_recording_id=f"{sc['mount']}_{sc['stamp']}",
                source_url="https://locusonus.org/soundmap/", audio_url="",
                credit=f"Locus Sonus Vitae (www.locusonus.org), stream {sc['artist']}",
                audio_licence="Locustream terms of use (record and redistribute, cite Locus Sonus)", redistributable="yes",
                public_latitude=sc["lat"], public_longitude=sc["lng"], place_decimals=min(dec(sc["lat"]), dec(sc["lng"])),
                position_basis="streamer-declared position on the Locus Sonus soundmap" + (f"; the point is the geocoded centre of {CENTRE[sc['site_id']]}, not the microphone" if sc["site_id"] in CENTRE else ""),
                position_precision_m="", obscured="true" if sc["site_id"] in CENTRE else "false",
                local_start=u, tz="", utc_start=u, local_date=u[:10], duration_s=f["dur_s"],
                time_grade="ntp", time_basis=BASIS, time_precision_s=10, audio_access="none",
                environment="marine" if "hydrophon" in sc["name"].lower() else "unknown", extra=json.dumps(ex, separators=(",", ":")))


def update(P, force=False):
    part = P["manifest"].replace(".csv.gz", ".part.jsonl")
    sp = f"{P['cap']}/manifest.seen"; seen = json.load(open(sp)) if os.path.exists(sp) else {}
    cur = set(); new = []
    for m in sorted(os.listdir(P["buf"])) if os.path.isdir(P["buf"]) else []:
        for fn in os.listdir(f"{P['buf']}/{m}"):
            if not fn.endswith(".json.gz"): continue
            p = f"{P['buf']}/{m}/{fn}"; rid = f"LOCUS_{m}_{fn[:-8]}"; cur.add(rid)
            try: mt = os.stat(p).st_mtime_ns
            except FileNotFoundError: continue
            if seen.get(rid) == mt: continue
            sc = lsc.load_sc(p)
            if not sc.get("complete") or not sc.get("fit"): continue
            new.append({k: str(v) for k, v in row(sc, P).items()}); seen[rid] = mt
    os.makedirs(os.path.dirname(P["manifest"]), exist_ok=True)
    if new:
        with open(part, "a") as f: f.write("".join(json.dumps(r) + "\n" for r in new)); f.flush(); os.fsync(f.fileno())
    lsc.write_atomic(sp, json.dumps({k: v for k, v in seen.items() if k in cur}).encode())
    rewrote = False
    if os.path.exists(part) and os.path.getsize(part) and (force or not os.path.exists(P["manifest"]) or time.time() - os.path.getmtime(P["manifest"]) >= 3600):
        rows = {}
        if os.path.exists(P["manifest"]):
            with gzip.open(P["manifest"], "rt", newline="") as fh:
                for r in csv.DictReader(fh): rows[r["recording_id"]] = r
        with open(part) as f:
            for l in f: r = json.loads(l); rows[r["recording_id"]] = r
        buf = io.StringIO(newline=""); w = csv.DictWriter(buf, COLS, lineterminator="\n"); w.writeheader(); w.writerows(rows.values())
        lsc.write_atomic(P["manifest"], gzip.compress(buf.getvalue().encode(), 6)); os.unlink(part); counts(P, rows); rewrote = True
    return len(new), rewrote


def counts(P, rows):
    win = [r for r in rows.values() if r["utc_start"] >= "2023-02-16"]
    c = dict(source="locustream", built_utc=iso(time.time()), rows=len(rows), rows_with_utc_start=sum(bool(r["utc_start"]) for r in rows.values()),
             rows_from_2023_02_16=len(win), rows_from_2023_02_16_with_time_and_place=sum(bool(r["utc_start"] and r["public_latitude"]) for r in win),
             hours_from_2023_02_16=round(sum(float(r["duration_s"]) for r in win) / 3600, 2), distinct_places=len({(r["public_latitude"], r["public_longitude"]) for r in rows.values()}),
             distinct_days_from_2023_02_16=len({r["local_date"] for r in win}), obscured=0, redistributable_yes=sum(r["redistributable"] == "yes" for r in rows.values()),
             method="own capture of Icecast streams listed by GET https://locusonus.org/soundmap/list/active/json/name; no remote metadata beyond that list")
    lsc.write_atomic(P["manifest"].replace(".csv.gz", ".counts.json"), json.dumps(c).encode())


if __name__ == "__main__":
    print("manifest: %d new rows appended, gz rewritten: %s" % update(lsc.paths(), force=True))
