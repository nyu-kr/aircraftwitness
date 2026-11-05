#!/usr/bin/env python3
import csv, datetime, gzip, json, os, re, time, zoneinfo
import requests
from timezonefinder import TimezoneFinder

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/aerosonicdb"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
REC = "https://zenodo.org/api/records/10215080"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()


def fetch(name):
    path = f"{RAW}/{name}"
    if not os.path.exists(path):
        r = requests.get(f"{REC}/files/{name}/content" if name != "record.json" else REC, timeout=90,
                         headers={"User-Agent": "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"}); r.raise_for_status()
        os.makedirs(RAW, exist_ok=True); open(path + ".tmp", "wb").write(r.content); os.replace(path + ".tmp", path); time.sleep(1)
    return open(path, encoding="utf-8").read()


def main():
    rec = json.loads(fetch("record.json")); assert rec["metadata"]["license"]["id"] == "cc-by-nc-4.0"
    # locations.json is missing its final brace, so match the printed digits instead of parsing JSON
    pos = {k: (la, lo) for k, la, lo in re.findall(r'"(\d)"\s*:\s*\{\s*"lattitude"\s*:\s*(-?[\d.]+)\s*,\s*"longitude"\s*:\s*(-?[\d.]+)', fetch("locations.json"))}
    assert len(pos) == 3
    tzf = TimezoneFinder(); tzs = {k: tzf.timezone_at(lat=float(a), lng=float(b)) for k, (a, b) in pos.items()}
    url = f"{REC}/files/audio.zip/content"; rows = []
    for r in csv.DictReader(fetch("sample_meta.csv").splitlines()):
        lat, lon = pos[r["location"]]; tz = tzs[r["location"]]
        t = datetime.datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H-%M-%S").replace(tzinfo=zoneinfo.ZoneInfo(tz))
        stem = r["filename"].removesuffix(".wav"); silence = r["hex_id"] in ("0", "000000")
        extra = {"hex": None if silence else r["hex_id"], "class": int(r["class"]), "subclass": int(r["subclass"]), "loc": int(r["location"]), "mic": int(r["mic"]),
                 "alt_ft": int(r["altitude"]) if not silence else None, "member": f"audio/{r['class']}/{r['filename']}"}
        rows.append([f"AEROSONIC_{stem}", "aerosonicdb", stem, "https://zenodo.org/records/10215080", url, "Downward, Blake", "CC BY-NC 4.0", "no",
                     lat, lon, min(len(lat.split(".")[1]), len(lon.split(".")[1])),
                     "locations.json coordinate of a road or public space near the recorder, not the device (record text: provided instead of its exact location)", "", "true",
                     f"{r['date']} {r['time']}", tz, t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), r["date"], r["file_length"], "unknown",
                     "file name / sample_meta.csv date+time (README: 'Time of the recording'); zone and recorder clock not stated; read as Adelaide local time because all 1,895 clip hours fall 06:00-23:59 (airport curfew hours), zone from timezonefinder at the location",
                     1, "download", "outdoor", json.dumps(extra, separators=(",", ":"))])
    rows.sort(key=lambda x: x[16])
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/aerosonicdb.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [x for x in rows if x[16] >= "2023-02-16"]
    c = dict(source="aerosonicdb", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(x[16]) for x in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(x[16] and x[8] and x[9]) for x in win),
             hours_from_2023_02_16=round(sum(float(x[18] or 0) for x in win) / 3600, 1), distinct_places=len({(x[8], x[9]) for x in rows}),
             distinct_days_from_2023_02_16=len({x[17] for x in win}), obscured=sum(x[13] == "true" for x in rows), redistributable_yes=sum(x[7] == "yes" for x in rows),
             method=f"GET {REC}; GET {REC}/files/sample_meta.csv/content; GET {REC}/files/locations.json/content",
             aircraft_clips_from_2023_02_16=sum(json.loads(x[-1])["class"] == 1 for x in win))
    json.dump(c, open(f"{OUT}/aerosonicdb.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
