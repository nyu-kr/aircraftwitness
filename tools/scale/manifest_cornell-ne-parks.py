#!/usr/bin/env python3
import csv, datetime, gzip, json, os, re, struct, time, zoneinfo
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/cornell-ne-parks"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
REC = "https://zenodo.org/api/records/18041381"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
TZ = "America/New_York"
S = requests.Session(); S.headers["User-Agent"] = "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"
last = [0.0]


def get(url, **kw):
    for attempt in range(8):
        wait = last[0] + 1.0 - time.time()
        if wait > 0: time.sleep(wait)
        r = S.get(url, timeout=90, **kw); last[0] = time.time()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(int(r.headers.get("retry-after", 20)) + 2 * attempt, 90)); continue
        r.raise_for_status(); return r
    raise RuntimeError(f"giving up on {url}")


def cached(path, fn):
    if os.path.exists(path): return open(path, "rb").read()
    data = fn(); os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path + ".tmp", "wb").write(data); os.replace(path + ".tmp", path); return data


def rng(url, spec): return get(url, headers={"Range": f"bytes={spec}"}).content


def central_directory(url, size):
    n = min(size, 1 << 21); tail = rng(url, f"{size - n}-{size - 1}"); base = size - n
    e = tail.rfind(b"PK\x05\x06")
    _, _, _, cnt, total, cdsize, cdoff, _ = struct.unpack("<4s4H2LH", tail[e:e + 22])
    if 0xFFFF in (cnt, total) or 0xFFFFFFFF in (cdsize, cdoff):
        l = tail.rfind(b"PK\x06\x07", 0, e); z = struct.unpack("<4sLQL", tail[l:l + 20])[2]
        z64 = tail[z - base:z - base + 56]; assert z64[:4] == b"PK\x06\x06"
        total, cdsize, cdoff = struct.unpack("<QQQ", z64[32:56])
    cd = tail[cdoff - base:cdoff - base + cdsize] if cdoff >= base else rng(url, f"{cdoff}-{cdoff + cdsize - 1}")
    out, p = [], 0
    while p < len(cd):
        h = struct.unpack("<4s6H3L5H2L", cd[p:p + 46]); assert h[0] == b"PK\x01\x02", "bad central directory"
        flag, csz, usz, nl, el, cl, off = h[3], h[8], h[9], h[10], h[11], h[12], h[16]
        name = cd[p + 46:p + 46 + nl].decode("utf-8" if flag & 0x800 else "cp437")
        out.append([name, usz]); p += 46 + nl + el + cl
    assert len(out) == total, f"central directory has {len(out)} of {total} entries"
    return out


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def main():
    rec = json.loads(cached(f"{RAW}/rec.json", lambda: get(REC).content)); files = {f["key"]: f for f in rec["files"]}
    assert rec["metadata"]["license"]["id"] == "cc-by-4.0"
    credit = "; ".join(c["name"] for c in rec["metadata"]["creators"])
    csvs = {k: cached(f"{RAW}/{k}", lambda k=k: get(files[k]["links"]["self"]).content).decode("utf-8-sig") for k in ("recording_metadata.csv", "site_metadata.csv")}
    sites = {r["SiteID"]: r for r in csv.DictReader(csvs["site_metadata.csv"].splitlines())}
    member = {}
    for sub in ("SIMR", "ACAD", "MABI"):
        f = files[f"Dataset{sub}_Recordings.zip"]
        for name, usz in json.loads(cached(f"{RAW}/members_{sub}.json", lambda: json.dumps(central_directory(f["links"]["self"], f["size"])).encode())):
            if name.endswith(".flac"): member[(sub, name.rsplit("/", 1)[-1].split(".")[0])] = (f["links"]["self"], name, usz)
    zone = zoneinfo.ZoneInfo(TZ); rows, notes = [], {"no_zip_member": 0, "zip_name_differs": 0, "time_blank_used_name": 0, "start_sources_differ_over_2min": 0}
    for r in csv.DictReader(csvs["recording_metadata.csv"].splitlines()):
        sub, iid = r["SubCollection_ID"], r["ItemID"]; s = sites[r["SiteID"]]; lat, lon = s["Latitude"], s["Longitude"]
        if (sub, iid) not in member: notes["no_zip_member"] += 1; continue
        url, zname, usz = member[(sub, iid)]; base = zname.rsplit("/", 1)[-1]
        m = re.fullmatch(r"(\d+)\.(\d+)\.\d+x\.([A-Z]+\d+)_(\d{8})_(\d{6})\.flac", base); assert m and m[3] == r["SiteID"], base
        by_name = datetime.datetime.strptime(m[4] + m[5], "%Y%m%d%H%M%S") + datetime.timedelta(minutes=int(m[2]))
        if r["Time"]: by_col = datetime.datetime.strptime(f"{r['Date']} {r['Time']}", "%Y-%m-%d %H:%M")
        else: by_col = by_name; notes["time_blank_used_name"] += 1
        gap = abs((by_name - by_col).total_seconds()); notes["start_sources_differ_over_2min"] += gap > 120
        notes["zip_name_differs"] += base != r["Recording_FileName"]
        t = by_col.replace(tzinfo=zone); extra = {"set": sub, "site": r["SiteID"], "park": r["ParkID"], "member": zname, "bytes": usz}
        if gap > 120: extra["name_start"] = by_name.strftime("%H:%M:%S")
        if base != r["Recording_FileName"]: extra["meta_name"] = r["Recording_FileName"]
        rows.append([f"CNEP{iid}", "cornell-ne-parks", base.removesuffix(".flac"), "https://zenodo.org/records/18041381", url, credit, "CC BY 4.0", "yes", lat, lon, min(dec(lat), dec(lon)),
                     "site_metadata.csv WGS84 coordinate of the recording site (one per site), unobscured", "", "false",
                     f"{r['Date']} {r['Time'] or by_name.strftime('%H:%M')}", TZ, t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), r["Date"], 600, "unknown",
                     "recording_metadata.csv Date+Time (clip start, minute); the record text says 'EST' but the clock is US Eastern daylight time (UTC-4 May-Jul): for all 873 ACAD/MABI clips (protocol: 40 min after local sunrise) "
                     "Time minus astral sunrise at the site gives UTC-4 to within 0.01 h; recorder clock source not stated; file-name start (hour-file stamp + offset minutes) kept in extra.name_start when it differs by over 2 min; "
                     "precision 3600 s then, else 60 s",
                     3600 if gap > 120 else 60, "download", "outdoor", json.dumps(extra, separators=(",", ":"))])
    rows.sort(key=lambda x: x[16])
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/cornell-ne-parks.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [x for x in rows if x[16] >= "2023-02-16"]
    c = dict(source="cornell-ne-parks", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(x[16]) for x in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(x[16] and x[8] and x[9]) for x in win),
             hours_from_2023_02_16=round(sum(float(x[18] or 0) for x in win) / 3600, 1), distinct_places=len({(x[8], x[9]) for x in rows}),
             distinct_days_from_2023_02_16=len({x[17] for x in win}), obscured=0, redistributable_yes=len(rows),
             method=f"GET {REC} (file list); GET {REC}/files/{{recording_metadata,site_metadata}}.csv/content; HTTP Range on {REC}/files/Dataset{{SIMR,ACAD,MABI}}_Recordings.zip/content: last 2 MB (central directory)",
             notes=notes)
    json.dump(c, open(f"{OUT}/cornell-ne-parks.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
