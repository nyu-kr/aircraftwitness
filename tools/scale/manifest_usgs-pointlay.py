#!/usr/bin/env python3
import csv, datetime, gzip, json, os, re
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/usgs-pointlay"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
ITEM = "https://www.sciencebase.gov/catalog/item/671bbdaed34efed56210c8a0"
ZIP = "https://www.sciencebase.gov/catalog/file/get/671bbdaed34efed56210c8a0?name=walrus_audio_recordings_ptLay_2024.zip"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
BASIS = ("LogStart in walrus_audioEventLog_ptLay_2024.csv is ISO UTC ('Date and time to the second that the audio recording device started the recording'); "
         "file names carry Alaska summer time (UTC-8) and agree with LogStart on every file; recorder parameters were set with the iPhone Song Meter app "
         "(metadata XML procdesc); no clock-sync or drift statement")
POS = "recorder deployment coordinate in the metadata XML procdesc ('placed at'), same values as the event log Location_latitude/Location_longitude columns"
UTC = datetime.timezone.utc
S = requests.Session(); S.headers["User-Agent"] = "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"


def cached(name, url):
    path = f"{RAW}/{name}"
    if not os.path.exists(path):
        os.makedirs(RAW, exist_ok=True)
        r = S.get(url, timeout=120); r.raise_for_status()
        open(path + ".tmp", "wb").write(r.content); os.replace(path + ".tmp", path)
    return path


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def main():
    item = json.load(open(cached("item.json", ITEM + "?format=json")))
    url = {f["name"]: f["downloadUri"] for f in item["files"]}
    log = cached("walrus_audioEventLog_ptLay_2024.csv", url["walrus_audioEventLog_ptLay_2024.csv"])
    xml = open(cached("metadata.xml", url["walrus_audio_recordings_ptLay_2024_metadata.xml"]), encoding="utf-8", errors="replace").read()
    pos = {m[0]: (m[1], m[2]) for m in re.findall(r"recorder (\w+) \([^)]*\) was placed at (-?\d+\.\d+), (-?\d+\.\d+)", xml)}
    files = {}
    for r in csv.DictReader(open(log, newline="")):
        f = files.setdefault(r["AudioFile_name"], dict(r, ac=[]))
        if r["Event"] == "Aircraft": f["ac"].append([r["EventStartTime"], r["EventEndTime"], r["EventQualifier"]])
    rows = []
    for name, f in files.items():
        la, lo = pos[f["RecorderID"]]
        if (float(la), float(lo)) != (float(f["Location_latitude"]), float(f["Location_longitude"])): raise SystemExit(f"{name}: event log position differs from metadata XML")
        local = datetime.datetime.strptime(name.split("_", 1)[1], "%Y%m%d_%H%M%S").replace(tzinfo=datetime.timezone(datetime.timedelta(hours=-8)))
        if local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") != f["LogStart"]: raise SystemExit(f"{name}: file name time disagrees with LogStart {f['LogStart']}")
        ex = {"rec": f["RecorderID"], "site": f["Recorder_location"], "file": name + ".wav", "zip": "walrus_audio_recordings_ptLay_2024.zip"}
        if f["ac"]: ex["aircraft_events"] = f["ac"]
        rows.append([f"USGSPTLAY_{name}", "usgs-pointlay", name, ITEM, ZIP, "Anthony S. Fischbach, Emily C. Racenet (USGS Alaska Science Center)",
                     "CC0 1.0 Universal", "yes", la, lo, min(dec(la), dec(lo)), POS, "", "false", f["LogStart"], "", f["LogStart"], f["LogStart"][:10],
                     f["LogDurationSeconds"], "device", BASIS, 1, "download", "outdoor", json.dumps(ex, separators=(",", ":"))])
    rows.sort(key=lambda x: x[16])
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/usgs-pointlay.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [x for x in rows if x[16] >= "2023-02-16"]
    c = dict(source="usgs-pointlay", built_utc=datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(x[16]) for x in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(x[16] and x[8] and x[9]) for x in win),
             hours_from_2023_02_16=round(sum(float(x[18] or 0) for x in win) / 3600, 1), distinct_places=len({(x[8], x[9]) for x in rows}),
             distinct_days_from_2023_02_16=len({x[17] for x in win}), obscured=0, redistributable_yes=len(rows),
             method=f"GET {ITEM}?format=json; GET walrus_audioEventLog_ptLay_2024.csv and walrus_audio_recordings_ptLay_2024_metadata.xml via the item's downloadUri; no audio fetched",
             files_with_aircraft_event=sum("aircraft_events" in x[-1] for x in rows))
    json.dump(c, open(f"{OUT}/usgs-pointlay.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
