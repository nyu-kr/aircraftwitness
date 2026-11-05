#!/usr/bin/env python3
import csv, gzip, json, os, re, sys, time, datetime, collections
import requests
from concurrent.futures import ThreadPoolExecutor

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/gbif-sounds"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
API = "https://api.gbif.org/v1/occurrence/search"
BASE = {"mediaType": "Sound", "year": "2016,2026"}
EXCLUDE = {"b1047888-ae52-4179-9dd5-5448ea342a24", "50c9509d-22c7-4a22-a47d-8c48425ef4a7"}
CAP = 9900  # GBIF offsets of 10,000 or more take about 30 seconds per request.
DROP = {"classifications", "gadm", "kingdomKey", "phylumKey", "classKey", "orderKey", "familyKey", "genusKey", "speciesKey",
        "acceptedTaxonKey", "taxonKey", "kingdom", "phylum", "order", "family", "genus", "species", "class", "genericName",
        "specificEpithet", "acceptedScientificName", "scientificNameAuthorship", "identifiedByIDs", "recordedByIDs", "facts", "relations"}
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
S = requests.Session()

def get(params):
    for i in range(6):
        try:
            r = S.get(API, params={**BASE, **params}, timeout=90)
            if r.status_code == 200: return r.text
        except requests.RequestException: pass
        time.sleep(5 * (i + 1))
    sys.exit(f"GBIF failed after retries: {params}")

def count(p): return json.loads(get({**p, "limit": 0}))["count"]

def partitions(ds, p, n, sub):

    if n <= CAP: return [(ds, p, n)]
    if not sub: sys.exit(f"{ds} {p} still {n} records")
    key, vals = sub[0]
    out = []
    for v in vals:
        q = {**p, key: v}
        m = count({"datasetKey": ds, **q})
        if m: out += partitions(ds, q, m, sub[1:])
    return out

def tasks():
    facet = json.load(open(f"{RAW}/facet.json"))["facets"][0]["counts"]
    sub = [("year", range(2016, 2027)), ("month", range(1, 13)), ("day", range(1, 32))]
    out, tot = [], collections.Counter()
    for d in facet:
        ds = d["name"]
        if ds in EXCLUDE: continue
        for ds_, p, n in partitions(ds, {}, d["count"], sub):
            tot[ds] += n
            out += [(ds, p, o) for o in range(0, n, 300)]
        if tot[ds] != d["count"]: sys.exit(f"{ds}: partitions sum {tot[ds]} != facet {d['count']}")
    return out

def pname(ds, p, off): return f"{RAW}/pages/{ds}_" + "".join(f"{k[0]}{v}" for k, v in p.items()) + f"_{off:05d}.json.gz"

def page(t):
    ds, p, off = t
    f = pname(ds, p, off)
    if os.path.exists(f): return
    res = json.loads(get({"datasetKey": ds, **p, "limit": 300, "offset": off}), parse_float=str)["results"]  # parse_float=str keeps coordinates exactly as printed
    for r in res:
        for k in DROP: r.pop(k, None)
    with gzip.open(f + ".part", "wt") as fh: json.dump(res, fh, ensure_ascii=False)
    os.rename(f + ".part", f)
    time.sleep(0.2)

def fetch():
    os.makedirs(f"{RAW}/pages", exist_ok=True)
    tf = f"{RAW}/tasks.json"
    if not os.path.exists(tf): json.dump(tasks(), open(tf, "w"))
    t = [(a, b, c) for a, b, c in json.load(open(tf))]
    print(f"{len(t)} pages", flush=True)
    done = 0
    with ThreadPoolExecutor(3) as ex:
        for _ in ex.map(page, t):
            done += 1
            if done % 50 == 0: print(f"{done}/{len(t)} {time.strftime('%H:%M:%S')}", flush=True)
    print("fetch done", flush=True)

def licence(u):
    if not u: return ""
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([\d.]+)", u)
    if m: return f"CC {m[1].upper()} {m[2]}"
    m = re.search(r"publicdomain/zero/([\d.]+)", u)
    if m: return f"CC0 {m[1]}"
    m = re.search(r"publicdomain/mark/([\d.]+)", u)
    if m: return f"PDM {m[1]}"
    return u

TIME = re.compile(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?(?:\.\d+)?\s*(AM|PM)?\s*(Z|[+-]\d\d(?::?\d\d)?)?$", re.I)
XC = {"ff571aeb-46bf-45c4-ad2c-af4d68315765", "ecce3417-d4fd-4fab-9009-09ad3f3f62b3", "6bd8ad26-218d-4657-8549-c30d69356664", "bcf8d1fc-6bf0-4f57-9076-7d9ae2828ec2"}

ACCESS = {**{k: "none" for k in ("3c06621f-b241-4ea0-b830-3755e9b519a0", "4d604d01-615c-40e8-8aea-73bbf37e998d", "62ad511d-d298-4fd7-80e7-f5d5bd32299e",
                                 "c9076cd3-349f-4068-a5c7-bc34449c3916", "e3b959d6-fcbe-4a28-a166-e4a807c340a0", "e382d777-53d3-43bd-88f6-54478ec0bd18",
                                 "b7ec1bf8-819b-11e2-bad2-00145eb45e9a", "852628c6-f762-11e1-a439-00145eb45e9a", "9105d363-8e5d-4d3f-9d74-a5e176f178fc")},
          **{k: "request" for k in ("94687f6b-6723-46a3-90d8-5a06b843291c", "c6b86c40-ff71-4e5e-902c-111f400d0d56", "f11db245-3f9f-4fc6-a0cc-12b4124d081b")}}
TYPED_MACHINE = XC | {"94687f6b-6723-46a3-90d8-5a06b843291c"}  # Some records have a manually entered time despite a MACHINE_OBSERVATION basis.
DATE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)$")

def split_time(r):
    ed = (r.get("eventDate") or "").strip()
    if "/" in ed:
        a, b = ed.split("/", 1)
        if a[:10] != b[:10]: return None, ed, None, None, "multi-day interval"
        ed = a
    date, _, tm = ed.partition("T")
    if not DATE.match(date): return None, ed, None, None, "no full date"
    src = ed
    if not tm and r.get("eventTime"):
        tm = r["eventTime"].strip(); src = f"{date} {tm}"
    if not tm: return date, src, None, None, None
    m = TIME.match(tm)
    if not m: return date, src, None, None, "unparseable time" if tm else None
    h, mi, se = int(m[1]), int(m[2]), int(m[3] or 0)
    if m[4]:
        if not 1 <= h <= 12: return date, src, None, None, "unparseable time"
        h = h % 12 + (12 if m[4].upper() == "PM" else 0)
    if h > 23 or mi > 59 or se > 59: return date, src, None, None, "unparseable time"
    return date, src, f"{h:02d}:{mi:02d}" + (f":{se:02d}" if m[3] else ""), m[5], None

def offset_minutes(off):
    if off == "Z": return 0
    s = 1 if off[0] == "+" else -1
    d = off[1:].replace(":", "")
    return s * (int(d[:2]) * 60 + int(d[2:4] or 0))

def build():
    from zoneinfo import ZoneInfo
    from timezonefinder import TimezoneFinder
    tzf = TimezoneFinder()
    dsm = {k: (t, n) for k, n, t, _ in json.load(open(f"{RAW}/datasets.json"))}
    rows, seen, drop, kept = [], set(), collections.Counter(), collections.Counter()
    now = datetime.datetime.now(datetime.timezone.utc)
    lo = datetime.datetime(2016, 1, 1, tzinfo=datetime.timezone.utc)
    for f in sorted(os.listdir(f"{RAW}/pages")):
        if not f.endswith(".json.gz"): continue
        for r in json.load(gzip.open(f"{RAW}/pages/{f}", "rt")):
            k = r["key"]
            if k in seen: drop["duplicate key"] += 1; continue
            seen.add(k)
            ds = r["datasetKey"]
            snd = [m for m in r.get("media", []) if m.get("type") == "Sound" and (m.get("identifier") or m.get("references"))]
            if not snd: drop["no sound media url"] += 1; continue
            m0 = snd[0]
            audio = m0.get("identifier") or m0["references"]
            audio = audio.replace("/proxyImageThumbnailLarge?", "/proxyImage?").replace("archive.org/details/", "archive.org/download/")  # the published forms return a PNG thumbnail / an HTML page
            date, local_start, tm, off, why = split_time(r)
            if why and date is None: drop[why] += 1; continue
            if why: kept[why] += 1
            lat, lon = r.get("decimalLatitude"), r.get("decimalLongitude")
            lat = None if lat is None else str(lat); lon = None if lon is None else str(lon)
            if lat is None or lon is None: lat = lon = None
            tz = utc = ""; via_created = False
            if tm:
                local = datetime.datetime.fromisoformat(f"{date}T{tm}")
                if off:
                    t = (local - datetime.timedelta(minutes=offset_minutes(off))).replace(tzinfo=datetime.timezone.utc)
                    utc = t.strftime("%Y-%m-%dT%H:%M:%SZ")
                elif lat is not None:
                    tz = tzf.timezone_at(lng=float(lon), lat=float(lat)) or ""
                    if tz.startswith("Etc/"):  # open sea has no civil clock: accept the media created time only if it agrees with the printed clock within a day
                        tz = ""
                        try: c = datetime.datetime.fromisoformat(m0["created"]).astimezone(datetime.timezone.utc)
                        except (KeyError, ValueError): c = None
                        if c and abs((c.replace(tzinfo=None) - local).total_seconds()) <= 14 * 3600:
                            utc = c.strftime("%Y-%m-%dT%H:%M:%SZ"); via_created = True
                        else: kept["open-sea point, no civil timezone"] += 1
                    if tz:
                        t = local.replace(tzinfo=ZoneInfo(tz)).astimezone(datetime.timezone.utc)
                        utc = t.strftime("%Y-%m-%dT%H:%M:%SZ")
                if utc:
                    t = datetime.datetime.fromisoformat(utc.replace("Z", "+00:00"))
                    if t < lo or t > now: drop["utc_start outside 2016..now"] += 1; continue
            elif not (lo.date() <= datetime.date.fromisoformat(date) <= now.date()):
                drop["date outside 2016..now"] += 1; continue
            prec = 86400 if not tm else (1 if tm.count(":") == 2 else 60)
            gen = r.get("dataGeneralizations") or r.get("informationWithheld") or ""
            lic = licence(m0.get("license") or r.get("license"))
            dec = lambda s: len(s.split(".")[1]) if "." in s else 0
            typed = r.get("basisOfRecord") != "MACHINE_OBSERVATION" or ds in TYPED_MACHINE
            grade = "typed" if typed else "unknown"
            basis = (f"eventDate/eventTime as published on the GBIF occurrence; " + ("entered by the observer or recordist" if typed else "passive-recorder record") +
                     f"; the dataset metadata (https://www.gbif.org/dataset/{ds}) does not document a recorder clock" + ("; UTC taken from the media created timestamp (open-sea point)" if via_created else ""))
            unc = r.get("coordinateUncertaintyInMeters")
            ex = {"ds": dsm[ds][0][:60], "sp": r.get("scientificName", "")}
            if ds in XC and (x := re.search(r"/XC(\d+)-", audio)): ex["xc"] = x[1]
            if len(snd) > 1: ex["n_sound"] = len(snd)
            if gen: ex["gen"] = gen[:120]
            rows.append({
                "recording_id": f"GBIF{k}", "source": f"gbif:{ds}", "source_recording_id": str(k),
                "source_url": f"https://www.gbif.org/occurrence/{k}", "audio_url": audio,
                "credit": r.get("recordedBy") or m0.get("creator") or m0.get("rightsHolder") or m0.get("publisher") or r.get("rightsHolder") or "",
                "audio_licence": lic or "unspecified",
                "redistributable": "yes" if re.match(r"(CC|PDM)", lic) else "no",
                "public_latitude": lat or "", "public_longitude": lon or "",
                "place_decimals": "" if lat is None else min(dec(lat), dec(lon)),
                "position_basis": "GBIF occurrence coordinate" + (", generalised by publisher" if gen else ", as published"),
                "position_precision_m": "" if unc is None else (str(int(float(unc))) if float(unc) == int(float(unc)) else str(unc)),
                "obscured": "true" if gen else "false",
                "local_start": local_start, "tz": tz, "utc_start": utc, "local_date": date, "duration_s": "",
                "time_grade": grade, "time_basis": basis, "time_precision_s": prec,
                "audio_access": ACCESS.get(ds, "download"), "environment": "unknown",
                "extra": json.dumps(ex, ensure_ascii=False, separators=(",", ":")),
            })
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/gbif-sounds.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader(); w.writerows(rows)
    since = lambda x: (x["utc_start"][:10] or x["local_date"]) >= "2023-02-16"
    late = [x for x in rows if since(x)]
    c = {"source": "gbif-sounds", "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": len(rows),
         "rows_with_utc_start": sum(bool(x["utc_start"]) for x in rows), "rows_from_2023_02_16": len(late),
         "rows_from_2023_02_16_with_time_and_place": sum(bool(x["utc_start"] and x["public_latitude"]) for x in late),
         "hours_from_2023_02_16": 0, "distinct_places": len({(x["public_latitude"], x["public_longitude"]) for x in rows if x["public_latitude"]}),
         "distinct_days_from_2023_02_16": len({x["local_date"] for x in late}),
         "obscured": sum(x["obscured"] == "true" for x in rows), "redistributable_yes": sum(x["redistributable"] == "yes" for x in rows),
         "dropped": dict(drop), "kept_date_only_for_time_issue": dict(kept), "rows_by_dataset": dict(collections.Counter(x["source"] for x in rows)),
         "method": "GET https://api.gbif.org/v1/occurrence/search?mediaType=Sound&year=2016,2026&limit=0&facet=datasetKey&facetLimit=200; "
                   "per dataset (excluding b1047888 xeno-canto and 50c9509d iNaturalist) GET .../occurrence/search?mediaType=Sound&datasetKey=KEY[&year=Y[&month=M[&day=D]]]&limit=300&offset=N "
                   "(partitioned so each query holds <= 9900 records); GET https://api.gbif.org/v1/dataset/KEY for titles. duration_s not given by GBIF so hours_from_2023_02_16 is 0."}
    json.dump(c, open(f"{OUT}/gbif-sounds.counts.json", "w"), indent=1)

if __name__ == "__main__":
    a = sys.argv[1:] or ["fetch", "build"]
    if "fetch" in a: fetch()
    if "build" in a: build()
