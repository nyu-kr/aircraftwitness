#!/usr/bin/env python3
import sys, os, re, csv, gzip, json, time, signal, datetime, urllib.parse, threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
from collections import Counter
from decimal import Decimal
import requests

BAD_TZ = Counter(); DROP = Counter()

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/aporee"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
SCRAPE = "https://archive.org/services/search/v1/scrape"
FIELDS = "identifier,coverage,creator,date,licenseurl,title,addeddate,publicdate"
START = "2016-01-01"; WINDOW = "2023-02-16"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
TIME_BASIS = ("Uploader-typed recording date and time (local time plus IANA zone, in the item coverage field); "
              "the upload form says \"if you don't know the exact date/time, make a reasonable guess\" (https://aporee.org/maps/info/)")
HDR = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
LIC = re.compile(r"creativecommons\.org/(licenses/([a-z-]+)/([\d.]+)(?:/([a-z]+))?|publicdomain/(mark|zero)/([\d.]+))")


def get(s, url, tries=10):
    for i in range(tries):
        try:
            r = s.get(url, headers=HDR, timeout=60)
            if r.status_code in (200, 404): return r
            wait = float(r.headers.get("Retry-After", 0) or 0) if r.status_code in (429, 503) else 0
        except requests.RequestException:
            wait = 0
        time.sleep(min(max(wait, 2 ** i), 120))
    raise RuntimeError(f"GET failed after {tries} tries: {url}")


def listing(rescrape):
    d = f"{RAW}/scrape"; os.makedirs(d, exist_ok=True)
    if rescrape:
        for f in os.listdir(d): os.unlink(f"{d}/{f}")
    s = requests.Session(); items = []; cur = ""; n = 0
    while True:
        p = f"{d}/p{n:03d}.json"
        if not os.path.exists(p):
            u = f"{SCRAPE}?q=collection:radio-aporee-maps&fields={FIELDS}&count=10000" + (f"&cursor={urllib.parse.quote(cur)}" if cur else "")
            r = get(s, u)
            if r.status_code != 200: raise RuntimeError(f"scrape page {n}: HTTP {r.status_code}")
            j = r.json()
            if "items" not in j: raise RuntimeError(f"scrape page {n}: {str(j)[:200]}")
            with open(p + ".tmp", "w") as f: json.dump(j, f)
            os.rename(p + ".tmp", p)
        j = json.load(open(p)); items += j["items"]; cur = j.get("cursor", ""); n += 1
        if not cur: break
    return items


def licence(url):
    if not url: return "not stated", "no"
    m = LIC.search(url)
    if not m: return url, "no"
    if m.group(5): return ("PDM " if m.group(5) == "mark" else "CC0 ") + m.group(6), "yes"
    name = "CC " + m.group(2).upper() + " " + m.group(3) + (" " + m.group(4).upper() if m.group(4) else "")
    return name, "yes"


def parse_cov(cov):
    g = lambda k: (re.search(rf"(?:^|; ){k}=([^;]*)", cov or "") or [None, ""])[1].strip()
    lat, lon = g("latitude"), g("longitude")
    if not (re.fullmatch(r"-?\d+(\.\d+)?", lat) and re.fullmatch(r"-?\d+(\.\d+)?", lon)): lat = lon = ""
    m = re.search(r"(?:^|; )date=(\d{4}-\d\d-\d\d)(?: (\d\d:\d\d:\d\d))?(?: ([^;\s]+))?\s*;?\s*$", cov or "")
    return lat, lon, (m.groups() if m else (None, None, None))


def duration_and_audio(j):
    fs = [f for f in j.get("files", []) if f.get("format") in ("WAVE", "Flac", "VBR MP3", "MP3", "Ogg Vorbis", "AIFF", "Apple Lossless Audio")]
    if not fs: return "", ""
    def secs(f):
        v = str(f.get("length") or "")
        try:
            if ":" in v:
                t = 0
                for x in v.split(":"): t = t * 60 + float(x)
                return t
            return float(v)
        except ValueError: return 0
    orig = [f for f in fs if f.get("source") == "original"]
    lossless = [f for f in orig if f["format"] in ("WAVE", "Flac", "AIFF", "Apple Lossless Audio")] or orig
    big = lambda l: max(l, key=lambda f: int(f.get("size") or 0))
    main = big(lossless) if lossless else big(fs)
    mp3s = [f for f in fs if f["format"] in ("VBR MP3", "MP3")]
    mp3 = big(mp3s) if mp3s else None
    t = secs(main) or (secs(mp3) if mp3 else 0)
    return (f"{t:g}" if t > 0 else ""), (mp3 or main)["name"]


def metapath(ident): return f"{RAW}/meta/{ident}.json.gz"


def cached_meta(ident):
    try:
        with gzip.open(metapath(ident), "rt") as f: return json.load(f)
    except (OSError, EOFError, ValueError): return None


def row(x, dur_cache):
    ident = x["identifier"]; cov = x.get("coverage") or ""
    lat, lon, (d, t, tz) = parse_cov(cov)
    wrapped = ""
    if lon and not -180 <= Decimal(lon) <= 180:  # map pin dragged past the antimeridian: shift by whole turns, digits kept
        wrapped, v = lon, Decimal(lon)
        while v > 180: v -= 360
        while v < -180: v += 360
        lon = str(v)
    ia = (x.get("date") or "")[:19].replace("T", " ")
    if not d and re.match(r"\d{4}-\d\d-\d\d", ia): d, t = ia[:10], (ia[11:] or None)
    if not d or d < "2015-12-31": return None
    if not lat: DROP["no position (radio show logs)"] += 1; return None
    if t == "00:00:00": t = None  # exact midnight is the form default for an unknown time: date-only
    utc = ""
    if t and tz:
        try:
            dt = datetime.datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ZoneInfo(tz))
            utc = dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, KeyError): BAD_TZ[tz] += 1
    if (utc or d)[:10] < START or utc > datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"): DROP["start outside 2016..now"] += 1; return None
    bad_tz = bool(t and tz and not utc)
    lic, redis = licence(x.get("licenseurl"))
    title = x.get("title") or ""
    env = "marine" if re.search(r"under\s?water", title, re.I) else "indoor" if re.search(r"\bindoors?\b", title, re.I) else "outdoor"
    dur, mp3 = dur_cache
    return {
        "recording_id": "APOREE_" + ident, "source": "aporee", "source_recording_id": ident,
        "source_url": f"https://archive.org/details/{ident}",
        "audio_url": f"https://archive.org/download/{ident}/{urllib.parse.quote(mp3)}" if mp3 else "",
        "credit": x.get("creator") or "", "audio_licence": lic, "redistributable": redis,
        "public_latitude": lat, "public_longitude": lon,
        "place_decimals": min((len(v.split(".")[1]) if "." in v else 0) for v in (lat, lon)) if lat else "",
        "position_basis": "uploader map pin" + ("; printed longitude lay beyond +-180 and is shifted by 360 deg" if wrapped else ""), "position_precision_m": "", "obscured": "false",
        "local_start": f"{d} {t}" if t else d, "tz": "" if bad_tz else tz or "", "utc_start": utc, "local_date": d,
        "duration_s": dur, "time_grade": "typed",
        "time_basis": TIME_BASIS + (f"; zone label {tz!r} is a site default that appears on recordings worldwide, not a usable IANA zone, so utc_start is empty" if bad_tz else ""),
        "time_precision_s": 60 if t else 86400, "audio_access": "download", "environment": env, "extra": json.dumps({k: v for k, v in (("tz_label", tz if bad_tz else ""), ("lon_printed", wrapped)) if v}) if (bad_tz or wrapped) else "",
    }


def write(items):
    rows = []
    for x in items:
        c = cached_meta(x["identifier"])
        r = row(x, duration_and_audio(c) if c and "files" in c else ("", ""))
        if r: rows.append(r)
    os.makedirs(OUT, exist_ok=True)
    p = f"{OUT}/aporee.csv.gz"
    with gzip.open(p + ".tmp", "wt", newline="") as f:
        w = csv.DictWriter(f, COLS); w.writeheader(); w.writerows(rows)
    os.rename(p + ".tmp", p)
    return rows


def fetch(todo, tick, workers=4):
    os.makedirs(f"{RAW}/meta", exist_ok=True)
    tl = threading.local()
    def one(ident):
        s = getattr(tl, "s", None) or requests.Session(); tl.s = s
        r = get(s, f"https://archive.org/metadata/{ident}")
        j = r.json() if r.status_code == 200 else {"_http": r.status_code}
        with gzip.open(metapath(ident) + ".tmp", "wt") as f: json.dump(j, f)
        os.rename(metapath(ident) + ".tmp", metapath(ident))
    ex = ThreadPoolExecutor(workers)
    try:
        for i, _ in enumerate(ex.map(one, todo), 1):
            if i % 3000 == 0: print(f"fetched {i}/{len(todo)}", flush=True); tick()
    finally:
        ex.shutdown(cancel_futures=True)


def counts(rows):
    w = lambda r: (r["utc_start"][:10] or r["local_date"]) >= WINDOW
    win = [r for r in rows if w(r)]
    secs = [float(r["duration_s"]) for r in win if r["duration_s"]]
    return {
        "source": "aporee", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": len(rows), "rows_with_utc_start": sum(1 for r in rows if r["utc_start"]),
        "rows_from_2023_02_16": len(win),
        "rows_from_2023_02_16_with_time_and_place": sum(1 for r in win if r["utc_start"] and r["public_latitude"]),
        "rows_from_2023_02_16_with_duration": len(secs),
        "hours_from_2023_02_16": round(sum(secs) / 3600, 1),
        "distinct_places": len({(round(float(r["public_latitude"]), 4), round(float(r["public_longitude"]), 4)) for r in rows if r["public_latitude"]}),
        "distinct_days_from_2023_02_16": len({r["local_date"] for r in win}),
        "obscured": sum(1 for r in rows if r["obscured"] == "true"),
        "redistributable_yes": sum(1 for r in rows if r["redistributable"] == "yes"),
        "rows_with_duration": sum(1 for r in rows if r["duration_s"]),
        "dropped": dict(DROP), "unconvertible_tz": dict(BAD_TZ),
        "method": "GET https://archive.org/services/search/v1/scrape?q=collection:radio-aporee-maps&fields=" + FIELDS + "&count=10000[&cursor=..] (listing, cursor pages); "
                  "GET https://archive.org/metadata/IDENTIFIER (files[].length for duration; 4 concurrent); no audio fetched. "
                  "distinct_places = distinct lat/lon rounded to 4 decimals.",
    }


def main():
    a = sys.argv[1:]
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    items = listing("--rescrape" in a)
    def emit():
        DROP.clear(); BAD_TZ.clear()
        rows = write(items)
        json.dump(counts(rows), open(f"{OUT}/aporee.counts.json", "w"), indent=1)
        return rows
    rows = emit()
    if "--no-fetch" not in a:
        todo = sorted((r for r in rows if not cached_meta(r["source_recording_id"])), key=lambda r: (r["local_date"] < WINDOW, r["local_date"]))
        todo = [r["source_recording_id"] for r in todo][:limit]
        fetch(todo, emit)
        emit()


if __name__ == "__main__":
    main()
