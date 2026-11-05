#!/usr/bin/env python3
import csv, datetime, decimal, gzip, json, os
from zoneinfo import ZoneInfo
import requests
from timezonefinder import TimezoneFinder

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/neon-bartlett"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
ITEM = "https://www.sciencebase.gov/catalog/item/6895e584d4be025c0fd639b5"
ZIP = "https://www.sciencebase.gov/catalog/file/get/68962152d4be023aaab379af?name={:03d}.zip"
PER_ZIP = 700  # Each zip covers a block of 700 consecutive media row numbers.
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
POS = ("centre of the published 0.0002-degree blur box (locations.csv long_min/long_max/lat_min/lat_max; item text: 'locations have been \"blurred\" by bounding boxes'), "
       "rounded to 4 decimals; precision is the box half-diagonal (about 11 m N-S, 8 m E-W); the box is in extra")
BASIS = ("media.csv start_date/start_time carry no zone (locations.csv tz column is empty, no zone in dbdictionary or the item XML); zone from timezonefinder at the plot; "
         "local time is consistent with the data (sampling dense at 05-09 h and 17-21 h, first file at the next whole hour after each 'set' visit in visits.csv); "
         "recorder clock source (Wildlife Acoustics 'smm') not documented")
UTC = datetime.timezone.utc
S = requests.Session(); S.headers["User-Agent"] = "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"


def cached(name, url):
    path = f"{RAW}/{name}"
    if not os.path.exists(path):
        os.makedirs(RAW, exist_ok=True)
        r = S.get(url, timeout=120); r.raise_for_status()
        open(path + ".tmp", "wb").write(r.content); os.replace(path + ".tmp", path)
    return path


def centre(lo, hi):
    return str(((decimal.Decimal(lo) + decimal.Decimal(hi)) / 2).quantize(decimal.Decimal("0.0001"), decimal.ROUND_HALF_EVEN))


def to_utc(local, zone):
    t = datetime.datetime.fromisoformat(local)
    a, b = t.replace(tzinfo=zone, fold=0), t.replace(tzinfo=zone, fold=1)
    if a.utcoffset() != b.utcoffset(): return ""
    u = a.astimezone(UTC)
    return "" if u.astimezone(zone).replace(tzinfo=None) != t else u.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    item = json.load(open(cached("item.json", ITEM + "?format=json")))
    url = {f["name"]: f["downloadUri"] for f in item["files"]}
    table = lambda n: list(csv.DictReader(open(cached(n, url[n]), newline="")))
    visits = {v["pk_visitid"]: v for v in table("visits.csv")}
    locs = {}
    tf = TimezoneFinder()
    for l in table("locations.csv"):
        if not l["lat_min"]: continue
        la, lo = centre(l["lat_min"], l["lat_max"]), centre(l["long_min"], l["long_max"])
        tz = tf.timezone_at(lat=float(la), lng=float(lo))
        box = {k: l[k] for k in ("lat_min", "lat_max", "long_min", "long_max")}
        locs[l["pk_locationid"]] = (la, lo, tz, ZoneInfo(tz), box)
    media = table("media.csv")
    ids = [int(m["pk_mediaid"]) for m in media]
    if ids != sorted(set(ids)): raise SystemExit("media.csv is not in increasing pk_mediaid order; zip numbering would be wrong")
    rows = []
    for i, m in enumerate(media):
        loc = visits[m["fk_visitid"]]["fk_locationid"]; la, lo, tz, zone, box = locs[loc]
        local = f"{m['start_date']} {m['start_time']}"
        stem = m["filename"].rsplit(".", 1)[0]; z = f"{i // PER_ZIP + 1:03d}.zip"
        ex = {"plot": loc, "rec": stem.split("_")[0], "zip": z, "member": m["filename"], "box": box}
        rows.append([f"NEONBART_{stem}", "neon-bartlett", m["filename"], ITEM, ZIP.format(i // PER_ZIP + 1),
                     "Hayes J, Fischer J, Clarfeld LA, Donovan TM (USGS AMMonitor); NEON Bartlett NH", "CC0 1.0 Universal", "yes",
                     la, lo, min(len(la.split(".")[1]), len(lo.split(".")[1])), POS, 14, "true", local, tz, to_utc(m["start_date"] + "T" + m["start_time"], zone),
                     m["start_date"], 60, "device", BASIS, 1, "download", "outdoor", json.dumps(ex, separators=(",", ":"))])
    rows.sort(key=lambda x: (x[16], x[0]))
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/neon-bartlett.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [x for x in rows if x[16] >= "2023-02-16"]
    c = dict(source="neon-bartlett", built_utc=datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(x[16]) for x in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(x[16] and x[8] and x[9]) for x in win),
             hours_from_2023_02_16=round(sum(float(x[18] or 0) for x in win) / 3600, 1), distinct_places=len({(x[8], x[9]) for x in rows}),
             distinct_days_from_2023_02_16=len({x[17] for x in win}), obscured=sum(x[13] == "true" for x in rows), redistributable_yes=len(rows),
             method=f"GET {ITEM}?format=json; GET media.csv, visits.csv, locations.csv via the item's downloadUri; no audio fetched",
             duration_note="duration_s=60 is not stated in the CSVs: the census read one zip member as 2,881,144 bytes (60 s) and modeloutputs.csv scoring windows end at 60 s; hours assume it")
    json.dump(c, open(f"{OUT}/neon-bartlett.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
