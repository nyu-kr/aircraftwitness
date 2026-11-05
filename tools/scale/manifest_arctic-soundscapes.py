#!/usr/bin/env python3
import csv, datetime, gzip, json, os
from zoneinfo import ZoneInfo
import requests
from timezonefinder import TimezoneFinder

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/arctic-soundscapes"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
COORDS = "https://cn.dataone.org/cn/v2/resolve/urn:uuid:5fd4ab1b-b775-4051-b18d-a8009b80ff6f"
META = "https://arcticdata.io/metacat/d1/mn/v2/object/urn:uuid:cd810504-d3d8-4493-8c7a-752c9fe732ea"
AUDIO = "https://arcticdata.io/data/10.18739/A27S7HV48/"
PAGE = "https://doi.org/10.18739/A27S7HV48"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
BASIS = ("recording_datetime_local_start is 'in local time of the recording site' and the file has 'no timezone column' (EML attributeDefinition, "
         "arcticdata.io/metacat/d1/mn/v2/object/doi:10.18739/A27S7HV48); zone taken from timezonefinder at the site coordinate; "
         "recorder clock setting and DST handling are not documented")
UTC = datetime.timezone.utc


def fetch(url, name):
    path = f"{RAW}/{name}"
    if not os.path.exists(path):
        os.makedirs(RAW, exist_ok=True)
        with requests.get(url, stream=True, timeout=120, headers={"User-Agent": "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"}) as r:
            r.raise_for_status()
            with open(path + ".tmp", "wb") as fh:
                for chunk in r.iter_content(1 << 20): fh.write(chunk)
        os.replace(path + ".tmp", path)
    return path


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def to_utc(local, zone):
    t = datetime.datetime.fromisoformat(local)
    a, b = t.replace(tzinfo=zone, fold=0), t.replace(tzinfo=zone, fold=1)
    if a.utcoffset() != b.utcoffset(): return "", "dst-ambiguous"
    u = a.astimezone(UTC)
    if u.astimezone(zone).replace(tzinfo=None) != t: return "", "dst-nonexistent"
    return u.strftime("%Y-%m-%dT%H:%M:%SZ"), ""


def main():
    tf = TimezoneFinder(); sites = {}
    for r in csv.DictReader(open(fetch(COORDS, "region_location_coordinates.csv"), newline="")):
        la, lo = r["latitude_dd"], r["longitude_dd"]
        tz = tf.timezone_at(lat=float(la), lng=float(lo))
        sites[r["region"], r["location"]] = (la, lo, tz, ZoneInfo(tz))
    rows, flags, nosite = [], {}, {}
    for r in csv.DictReader(open(fetch(META, "arctic_soundscapes_metadata.csv"), newline="")):
        local = r["recording_datetime_local_start"]
        if local < "2016-01-01": continue
        if (r["region"], r["location"]) not in sites:
            k = f"{r['region']}/{r['location']}"; nosite[k] = nosite.get(k, 0) + 1; continue
        la, lo, tz, zone = sites[r["region"], r["location"]]
        utc, flag = to_utc(local, zone)
        if flag: flags[flag] = flags.get(flag, 0) + 1
        stem = r["filename"].rsplit(".", 1)[0]
        ex = {"region": r["region"], "site": r["location"], "rec": r["recorder_id"], "fmt": r["file_format"], "ch": r["channels"], "sr": r["sampling_rate_hz"]}
        if flag: ex["utc_unset"] = flag
        rows.append([f"ARCT_{r['region']}_{r['location']}_{stem}", "arctic-soundscapes", f"{r['region']}/{r['location']}/{r['filename']}", PAGE,
                     AUDIO + r["relative_filepath"].removeprefix("real/"), "Todd Brinkman, Enis Coban, Natalie Boelman (Arctic Data Center)",
                     "CC0 1.0 Universal", "yes", la, lo, min(dec(la), dec(lo)),
                     "fixed recorder GPS coordinate per region/location (region_location_coordinates.csv: 'GPS coordinates for each recording site')", "", "false",
                     local, tz, utc, local[:10], r["duration_s"], "device", BASIS, 1, "download", "outdoor", json.dumps(ex, separators=(",", ":"))])
    ids = {x[0] for x in rows}
    if len(ids) != len(rows): raise SystemExit(f"{len(rows) - len(ids)} duplicate recording_ids")
    rows.sort(key=lambda x: (x[14], x[0]))
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/arctic-soundscapes.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [x for x in rows if x[16] >= "2023-02-16"]
    c = dict(source="arctic-soundscapes", built_utc=datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(x[16]) for x in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(x[16] and x[8] and x[9]) for x in win),
             hours_from_2023_02_16=round(sum(float(x[18] or 0) for x in win) / 3600, 1), distinct_places=len({(x[8], x[9]) for x in rows}),
             distinct_days_from_2023_02_16=len({x[17] for x in win}), obscured=0, redistributable_yes=len(rows),
             method=f"GET {COORDS} (region_location_coordinates.csv); GET {META} (arctic_soundscapes_metadata.csv, streamed, rows with local start >= 2016-01-01); no audio fetched",
             zones=sorted({s[2] for s in sites.values()}), dst_unset_utc=flags, skipped_no_site_coordinate=nosite)
    json.dump(c, open(f"{OUT}/arctic-soundscapes.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
