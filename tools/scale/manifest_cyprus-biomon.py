#!/usr/bin/env python3
import os, io, re, sys, csv, gzip, json, time, zipfile, datetime, urllib.parse, requests
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import openpyxl

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/cyprus-biomon"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
HF = "https://huggingface.co"
REPO = "cmammides/BIOMON"
HDR = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
SITES = "Ecoacoustic Dataset_Cyprus_Study Sites.xlsx"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
POS_BASIS = "geographic coordinates of the exact location the recorder was placed (tree, ~1.5 m), Study Sites xlsx; Mammides et al. 2025 Sci Data 12:461"
TIME_BASIS = ("Filename RECORDER_YYYYMMDD_hhmmss is the file start by the recorder clock in the time zone set on the recorder for the deployment "
              "(Wildlife Acoustics Song Meter Mini 2 guide, SD Card Contents: 'according the time zone that was set on the recorder'); recorders were programmed with the Song Meter mobile app "
              "(Mammides et al. 2025, Sci Data 12:461, doi:10.1038/s41597-025-04807-1), which sets the clock from the phone; zone not published, Asia/Nicosia assumed (EEST in the whole window); consistent with the dawn chorus (the dataset's Acoustic Indices xlsx: BIO peaks 20-30 min before sunrise when clock = UTC+3, all sites pooled, 10-min bins); clock then free-runs ~7 days")
CREDIT = "Mammides, C., Ieronymidou, C. & Papadopoulos, H. (2025), BIOMON"
NAME = re.compile(r"^(SMA\d+)_(\d{8})_(\d{6})\.wav$")


class Ranged(io.RawIOBase):
    def __init__(s, url):
        s.url, s.pos = url, 0
        s.resolve()

    def resolve(s):
        r = requests.get(s.url, headers=dict(HDR, Range="bytes=0-0"), timeout=60); r.raise_for_status()
        s.u, s.size = r.url, int(r.headers["Content-Range"].split("/")[1])

    def seekable(s): return True
    def readable(s): return True
    def tell(s): return s.pos

    def seek(s, o, w=0):
        s.pos = {0: o, 1: s.pos + o, 2: s.size + o}[w]; return s.pos

    def readinto(s, b):
        n = min(len(b), s.size - s.pos)
        if n <= 0: return 0
        for t in range(3):
            r = requests.get(s.u, headers=dict(HDR, Range=f"bytes={s.pos}-{s.pos + n - 1}"), timeout=120)
            if r.status_code in (403, 404): s.resolve(); continue
            r.raise_for_status(); break
        d = r.content; b[:len(d)] = d; s.pos += len(d); return len(d)


def get(url, path):
    if not os.path.exists(path):
        r = requests.get(url, headers=HDR, timeout=120, allow_redirects=True); r.raise_for_status()
        with open(path + ".part", "wb") as f: f.write(r.content)
        os.rename(path + ".part", path)
    return path


def listing(zname):
    p = f"{RAW}/cd/{zname}.json"
    if os.path.exists(p): return json.load(open(p))
    z = zipfile.ZipFile(Ranged(f"{HF}/datasets/{REPO}/resolve/main/{urllib.parse.quote(zname)}"))
    m = [(i.filename, i.file_size) for i in z.infolist() if i.filename.lower().endswith(".wav")]
    summ = [i.filename for i in z.infolist() if i.filename.endswith("Summary.txt")]
    if len(summ) == 1: open(f"{RAW}/cd/{zname}.summary.txt", "wb").write(z.read(summ[0]))
    json.dump(m, open(p + ".tmp", "w")); os.rename(p + ".tmp", p)
    time.sleep(1)
    return m


def num(x): return repr(round(float(x), 7))


def main():
    os.makedirs(f"{RAW}/cd", exist_ok=True); os.makedirs(f"{RAW}/dl", exist_ok=True); os.makedirs(OUT, exist_ok=True)
    get(f"{HF}/api/datasets/{REPO}/tree/main?recursive=true", f"{RAW}/tree.json")
    zips = {int(re.match(r"(\d+)\. ", e["path"]).group(1)): e["path"] for e in json.load(open(f"{RAW}/tree.json")) if e["path"].endswith(".zip")}
    xl = get(f"{HF}/datasets/{REPO}/resolve/main/{urllib.parse.quote(SITES)}", f"{RAW}/dl/sites.xlsx")
    rows = list(openpyxl.load_workbook(xl, read_only=True, data_only=True).active.iter_rows(values_only=True))
    hd = rows[0]; site = {int(r[hd.index("Folder Number")]): dict(zip(hd, r)) for r in rows[1:] if r[0] is not None}
    if sorted(zips) != sorted(site) or len(zips) != 61: sys.exit(f"zip folders {sorted(zips)} differ from Study Sites folders {sorted(site)}")
    tf = TimezoneFinder(); out = []; ids = set(); cnt_sheet = 0; outside = {}
    for n in sorted(zips):
        s = site[n]; rec = s["Recorder"]; lat, lon = num(s["Latitude"]), num(s["Longitude"])
        tz = tf.timezone_at(lat=float(lat), lng=float(lon)); zi = ZoneInfo(tz)
        dec = str(min(len(lat.split(".")[1]), len(lon.split(".")[1])))
        d0, d1 = s["Date Deployed"].date(), s["Date Collected"].date(); cnt_sheet += int(s["Recordings_Count"])
        zurl = f"{HF}/datasets/{REPO}/resolve/main/{urllib.parse.quote(zips[n])}"
        for member, size in listing(zips[n]):
            mm = NAME.match(os.path.basename(member))
            if not mm or mm.group(1) != rec: sys.exit(f"{zips[n]}: unexpected member {member}")
            ds, ts = mm.group(2), mm.group(3)
            t = datetime.datetime.strptime(ds + ts, "%Y%m%d%H%M%S")
            if not d0 <= t.date() <= d1: outside[n] = outside.get(n, 0) + 1; continue
            u = t.replace(tzinfo=zi).astimezone(datetime.timezone.utc)
            rid = f"BIOMON_{rec}_{ds}_{ts}"
            if rid in ids: sys.exit(f"duplicate {rid}")
            ids.add(rid)
            out.append([rid, "cyprus-biomon", member, f"{HF}/datasets/{REPO}", zurl, CREDIT, "CC BY 4.0", "yes", lat, lon, dec, POS_BASIS, "", "false",
                        f"{ds}_{ts}", tz, u.strftime("%Y-%m-%dT%H:%M:%SZ"), t.strftime("%Y-%m-%d"), round((size - 1164) / 96000, 1), "device", TIME_BASIS, 1, "download", "outdoor",
                        json.dumps({"site": s["Site Name"], "rec": rec}, separators=(",", ":"))])
    sizes = {}
    for n in zips:
        for _, sz in listing(zips[n]): sizes[sz] = sizes.get(sz, 0) + 1
    if len(sizes) > 1: print("note: member sizes differ:", dict(sorted(sizes.items(), key=lambda x: -x[1])[:5]), file=sys.stderr)
    if outside: print("dropped files outside the sheet deployment dates, by folder:", outside, file=sys.stderr)
    tmp = f"{OUT}/cyprus-biomon.csv.gz.tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(COLS); w.writerows(out)
    os.rename(tmp, f"{OUT}/cyprus-biomon.csv.gz")
    win = [r for r in out if r[16] >= "2023-02-16"]
    counts = {"source": "cyprus-biomon", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rows": len(out), "rows_with_utc_start": len(out), "rows_from_2023_02_16": len(win), "rows_from_2023_02_16_with_time_and_place": len(win),
              "hours_from_2023_02_16": round(sum(r[18] for r in win) / 3600, 1), "distinct_places": len({(r[8], r[9]) for r in out}),
              "distinct_days_from_2023_02_16": len({r[16][:10] for r in win}), "obscured": 0, "redistributable_yes": len(out),
              "sheet_recordings_count_sum": cnt_sheet, "dropped_outside_deployment_dates": sum(outside.values()), "first_utc_start": min(r[16] for r in out), "last_utc_start": max(r[16] for r in out),
              "method": f"GET {HF}/api/datasets/{REPO}/tree/main?recursive=true (61 zips + 2 xlsx + README); GET {HF}/datasets/{REPO}/resolve/main/{urllib.parse.quote(SITES)} (19 KB, lat/lon per folder); "
                        "per zip: HTTP Range reads of the end of resolve/main/<zip> (zip64 central directory, member names and sizes) plus the 300 KB Summary.txt member; no WAV bytes fetched. "
                        "duration_s = (member size - 1164 header bytes) / 96000 B/s (48 kHz 16-bit mono; 1164-byte header and 58.0 s confirmed on 10 earlier trial WAVs)."}
    json.dump(counts, open(f"{OUT}/cyprus-biomon.counts.json", "w"), indent=1)
    print(dict(zip(COLS, out[0])))


if __name__ == "__main__": main()
