#!/usr/bin/env python3

import sys, csv, gzip, json, re, datetime
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
GRADES = {"gps", "ntp", "device", "phone", "typed", "unknown"}
f = sys.argv[1]; bad = []; n = 0; ids = set(); win = 0; now = datetime.datetime.now(datetime.timezone.utc)
dec = lambda s: len(s.split(".")[1]) if "." in s else 0
with gzip.open(f, "rt", newline="") as fh:
    r = csv.DictReader(fh)
    if r.fieldnames != COLS: sys.exit(f"columns differ from spec:\n got {r.fieldnames}\nwant {COLS}")
    for row in r:
        n += 1; e = []
        rid = row["recording_id"]
        if not rid or rid in ids: e.append("missing or duplicate recording_id")
        ids.add(rid)
        la, lo = row["public_latitude"], row["public_longitude"]
        if la or lo:
            try:
                if not (-90 <= float(la) <= 90 and -180 <= float(lo) <= 180): e.append("position out of range")
            except ValueError: e.append("position not numeric")
            if row["place_decimals"] != str(min(dec(la), dec(lo))): e.append("place_decimals does not match the printed coordinates")
        if row["utc_start"]:
            try:
                t = datetime.datetime.fromisoformat(row["utc_start"].replace("Z", "+00:00"))
                if not row["utc_start"].endswith("Z"): e.append("utc_start must end in Z")
                if t < datetime.datetime(2016, 1, 1, tzinfo=datetime.timezone.utc) or t > now: e.append("utc_start outside 2016..now")
                if t >= datetime.datetime(2023, 2, 16, tzinfo=datetime.timezone.utc): win += 1
            except ValueError: e.append("utc_start not ISO")
        elif not re.fullmatch(r"\d{4}-\d\d-\d\d", row["local_date"]): e.append("no utc_start and no local_date")
        if row["duration_s"]:
            try:
                if not 0 < float(row["duration_s"]) < 30 * 86400: e.append("duration_s out of range")
            except ValueError: e.append("duration_s not numeric")
        if row["time_grade"] not in GRADES: e.append(f"time_grade {row['time_grade']!r}")
        if row["redistributable"] not in ("yes", "no"): e.append("redistributable not yes/no")
        if row["obscured"] not in ("true", "false"): e.append("obscured not true/false")
        if row["audio_access"] not in ("download", "fetch", "request", "none"): e.append("audio_access value")
        if not row["audio_licence"] or not row["source"] or not row["time_basis"]: e.append("empty licence, source or time_basis")
        if row["extra"]:
            try: json.loads(row["extra"])
            except ValueError: e.append("extra not JSON")
        if e and len(bad) < 10: bad.append((rid, e))
        elif e: bad.append(None)
if bad: print(f"FAIL {len(bad)} bad rows of {n}"); [print(" ", b) for b in bad[:10] if b]; sys.exit(1)
print(f"OK {n} rows, {win} from 2023-02-16")
