#!/usr/bin/env python3
import csv, datetime, gzip, json, os, re, struct, time, zoneinfo
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/aporee-live"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
SCRAPE = "https://archive.org/services/search/v1/scrape"
LOCUS = "https://locusonus.org/soundmap/list/active/json/name"
MOUNTS = ["koosmic", "kozmic", "brno_luzanky", "ortler_end_der_welt_ferner"]
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
ID = re.compile(r"rec_(.+?)(?:\.mp3|\.ogg)?_(\d{4})_(\d\d)_(\d\d)_(\d\d)_(\d\d)_(\d\d)$")
KBPS = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
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


def kbps(head):
    i = 10 + ((head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]) if head[:3] == b"ID3" else 0
    while i < len(head) - 4 and not (head[i] == 0xFF and head[i + 1] & 0xE0 == 0xE0): i += 1
    h = struct.unpack(">I", head[i:i + 4])[0]
    assert (h >> 19) & 3 == 3 and (h >> 17) & 3 == 1, "not MPEG-1 layer III"
    return KBPS[(h >> 12) & 15]


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def main():
    q = "identifier:(" + " OR ".join(f"rec_{m}*" for m in MOUNTS) + ") AND mediatype:audio"
    items = json.loads(cached(f"{RAW}/scrape.json", lambda: get(SCRAPE, params={"q": q, "fields": "identifier", "count": 10000}).content))["items"]
    locus = json.loads(cached(f"{RAW}/locus_list.json", lambda: get(LOCUS).content))
    pos = {x["url"].rsplit("/", 1)[-1].removesuffix(".mp3"): x for x in locus}
    rows, skipped = [], {}
    def skip(why): skipped[why] = skipped.get(why, 0) + 1
    for it in sorted(i["identifier"] for i in items):
        m = ID.fullmatch(it)
        if not m or m[1] not in MOUNTS: skip("identifier not rec_<mount>_timestamp for a listed mount"); continue
        md = json.loads(cached(f"{RAW}/meta/{it}.json", lambda: get(f"https://archive.org/metadata/{it}").content))
        f = next((x for x in md["files"] if x["name"].lower().endswith(".mp3")), None)
        if not f or not f.get("mtime") or not f.get("size"): skip("no mp3 with mtime and size"); continue
        head = cached(f"{RAW}/head/{it}.bin", lambda: get(f"https://archive.org/download/{it}/{f['name']}", headers={"Range": "bytes=0-4095"}).content)
        try: rate = kbps(head)
        except (AssertionError, struct.error): skip("mp3 header unreadable"); continue
        length = float(f.get("length") or 0)
        dur = length if length > 0 else int(f["size"]) * 8 / (rate * 1000) if rate else 0
        if not dur: skip("no duration"); continue
        meta = md["metadata"]; tz = meta["timezone"]; d = meta["date"]
        dm = re.fullmatch(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?: [A-Z]+ \S+)?", d)
        if not dm: skip("item date not parseable"); continue
        date_utc = datetime.datetime.strptime(dm[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zoneinfo.ZoneInfo(tz)).astimezone(datetime.timezone.utc)
        end = datetime.datetime.fromtimestamp(int(f["mtime"]), datetime.timezone.utc); start = (end - datetime.timedelta(seconds=dur)).replace(microsecond=0)
        gap = round((end - date_utc).total_seconds())
        lo = pos[m[1]]; lat, lon = lo["lat"], lo["lng"]
        lic = re.search(r"licenses/([a-z-]+)/([\d.]+)", meta.get("licenseurl", ""))
        licence = f"CC {lic[1].upper()} {lic[2]}" if lic else "not stated"
        rows.append([f"APOREELIVE_{it}", "aporee-live", it, f"https://archive.org/details/{it}", f"https://archive.org/download/{it}/{f['name']}", meta["creator"], licence,
                     "yes" if lic and "NC" not in licence and "ND" not in licence else "no", lat, lon, min(dec(lat), dec(lon)),
                     f"Locus Sonus soundmap list position of the live mount {m[1]} (streamer-declared, 4 decimals); the archive.org item has no coordinates, joined by mount name", "", "false",
                     d, tz, start.strftime("%Y-%m-%dT%H:%M:%SZ"), start.strftime("%Y-%m-%d"), round(dur, 1), "unknown",
                     "utc_start = mp3 file mtime in the archive.org file list (end of the segment) minus duration (file length, else size / MPEG bitrate from the first frame); "
                     "local_start is the item date field (about the segment end, minute resolution, uploader-set, local time and zone); the two ends differ by extra.end_gap_s; "
                     "recorder clock source not stated; precision 60 s when the gap is under 2 min, else 3600 s",
                     60 if abs(gap) < 120 else 3600, "download", "outdoor",
                     json.dumps({"mount": m[1], "end_gap_s": gap, "kbps": rate, "bytes": int(f["size"]), "dur_from": "length" if length > 0 else "size/bitrate"}, separators=(",", ":"))])
    rows.sort(key=lambda r: r[16])
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/aporee-live.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [r for r in rows if r[16] >= "2023-02-16"]
    c = dict(source="aporee-live", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(r[16]) for r in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(r[16] and r[8] and r[9]) for r in win),
             hours_from_2023_02_16=round(sum(float(r[18] or 0) for r in win) / 3600, 1), distinct_places=len({(r[8], r[9]) for r in rows}),
             distinct_days_from_2023_02_16=len({r[17] for r in win}), obscured=0, redistributable_yes=sum(r[7] == "yes" for r in rows),
             method=f"GET {SCRAPE}?q={q}; GET {LOCUS}; per item GET https://archive.org/metadata/ID and Range bytes=0-4095 of its mp3",
             mounts_included=MOUNTS, skipped_items=skipped)
    json.dump(c, open(f"{OUT}/aporee-live.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
