#!/usr/bin/env python3
import os, re, sys, csv, gzip, json, time, datetime, requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/smartbay-galway"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
B = "http://smartbay.marine.ie/data/audio/ICListenRecordings/"
SRC = "https://data.gov.ie/dataset/galway-bay-observatory-hydrophone-raw-data"
HDR = {"User-Agent": "Mozilla/5.0 aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
POS_BASIS = "fixed observatory coordinate of the SmartBay Galway Bay hydrophone node, ERDDAP smartbay_obs_acoustic geospatial_lat/lon (min=max); hydrophone sits on a lander ~30 m from the cable end node (data.gov.ie raw-data page)"
TIME_BASIS = ("File name icListen_<serial>_YYYYMMDDTHHMMSSZ.wav is the UTC start (Ocean Sonics icListen User Guide: the recorder uses UTC date and time in the file name; clock set via 'SYNC Time'); "
              "how the Galway unit is synchronised is not published; extra.mt is the server's listing time (UTC, end of the 1-minute file) and differs from the name by minutes on some days, so the instrument clock steps or drifts")
TIME_BASIS_OLD = ("File name SBW<serial>_YYYYMMDD_HHMMSS.wav or Raw_Wav_YYYYMMDD_HHMMSS.wav: no zone stated (other files use UTC 'Z'); read as UTC, Europe/Dublin gives the same on these dates (GMT, 2023-01-25 and 2023-03-01/02); "
                  "how the unit is synchronised is not published; extra.mt is the server's listing time")
ITEM = re.compile(r'<a href="([^"]+)">[^<]*</a>\s+(\d\d-\w{3}-\d{4} \d\d:\d\d)\s+(\S+)')
ITEM = re.compile(r'<a href="([^"]+)">[^<]*</a>\s+(\d\d-\w{3}-\d{4} \d\d:\d\d)\s+(\S+)')
NAME = re.compile(r"^(?:icListen_(\d+)_(\d{8})T(\d{6})Z|SBW(\d+)_(\d{8})_(\d{6})|(Raw_Wav)_(\d{8})_(\d{6}))\.wav$")
STD = {44 + 180 * fs * 1000 for fs in (4, 8, 16, 32, 64, 96, 128, 256, 512)}  # exactly 60 s of 24-bit mono at a whole-kHz rate


def page(path):
    p = f"{RAW}/pages/{path or 'root'}.html"
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        r = requests.get(B + path, headers=HDR, timeout=60, allow_redirects=True); r.raise_for_status()
        open(p + ".part", "w").write(r.text); os.rename(p + ".part", p); time.sleep(0.2)
    return [i for i in ITEM.findall(open(p).read()) if i[0] != "../"]


def main():
    os.makedirs(OUT, exist_ok=True); files = []
    for y in [i[0] for i in page("") if re.fullmatch(r"\d{4}/", i[0])]:
        for m in [i[0] for i in page(y) if i[0].endswith("/")]:
            for d in [i[0] for i in page(y + m) if i[0].endswith("/")]:
                for name, mt, size in page(y + m + d):
                    files.append((y + m + d + name, int(size), mt))
    rows = []; ids = set(); skipped = []
    for path, size, mt in files:
        name = path.rsplit("/", 1)[1]; m = NAME.match(name)
        if not m: skipped.append(path); continue
        g = m.groups(); old = g[0] is None
        ser, ds, ts = (g[3], g[4], g[5]) if g[3] else (None, g[7], g[8]) if g[6] else g[:3]
        t = datetime.datetime.strptime(ds + ts, "%Y%m%d%H%M%S")
        rid = "SMARTBAY_" + name[:-4]
        if rid in ids: sys.exit(f"duplicate {rid}")
        ids.add(rid)
        mtu = datetime.datetime.strptime(mt, "%d-%b-%Y %H:%M").strftime("%Y-%m-%dT%H:%MZ")
        rows.append([rid, "smartbay-galway", path, SRC, "https://smartbay.marine.ie/data/audio/ICListenRecordings/" + path, "Marine Institute", "CC BY 4.0", "yes", "53.2273", "-9.2663", 4, POS_BASIS, 30, "false",
                     ds + ("T" + ts + "Z" if not old else "_" + ts), "Europe/Dublin" if old else "", t.strftime("%Y-%m-%dT%H:%M:%SZ"), t.strftime("%Y-%m-%d"), 60 if size in STD else "",
                     "device", TIME_BASIS_OLD if old else TIME_BASIS, 1, "download", "marine", json.dumps({"ser": ser or "", "B": size, "mt": mtu}, separators=(",", ":"))])
    tmp = f"{OUT}/smartbay-galway.csv.gz.tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(COLS); w.writerows(rows)
    os.rename(tmp, f"{OUT}/smartbay-galway.csv.gz")
    win = [r for r in rows if r[16] >= "2023-02-16"]; dur = lambda rs: sum(r[18] or 0 for r in rs)
    counts = {"source": "smartbay-galway", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": len(rows), "rows_with_utc_start": len(rows),
              "rows_from_2023_02_16": len(win), "rows_from_2023_02_16_with_time_and_place": len(win), "hours_from_2023_02_16": round(dur(win) / 3600, 1), "distinct_places": 1,
              "distinct_days_from_2023_02_16": len({r[16][:10] for r in win}), "obscured": 0, "redistributable_yes": len(rows), "rows_with_duration": sum(1 for r in rows if r[18]),
              "first_utc_start": min(r[16] for r in rows), "last_utc_start": max(r[16] for r in rows), "skipped_non_matching_files": skipped,
              "method": f"GET {B} (nginx autoindex): year, month and day pages {B}YYYY/MM/DD/ (name, listing time, byte size), cached; no audio fetched. "
                        "duration_s 60 only where the byte size is exactly 44 + 180000 x kHz (60 s of 24-bit mono at a whole-kHz rate); other sizes left empty (rate unknown). "
                        "Position: ERDDAP https://erddap.marine.ie/erddap/info/smartbay_obs_acoustic/index.csv geospatial_lat/lon (min=max). Licence: data.gov.ie package_show license_id CC-BY-4.0."}
    json.dump(counts, open(f"{OUT}/smartbay-galway.counts.json", "w"), indent=1)
    print(dict(zip(COLS, rows[len(rows) // 2])))


if __name__ == "__main__": main()
