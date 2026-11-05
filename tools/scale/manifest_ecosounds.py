#!/usr/bin/env python3
import csv, datetime, glob, gzip, json, os, re, sys, time
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import requests
from timezonefinder import TimezoneFinder

API = "https://api.ecosounds.org"
RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/ecosounds"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
A2O_CSV = f"{OUT}/a2o.csv.gz"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
PAGE, SPAN, WORKERS, PAUSE = 500, 50000, 4, 0.3  # 500 is the API maximum
FROM = "2016-01-01T00:00:00.000Z"
FIELDS = ["id", "recorded_date", "site_id", "duration_seconds", "original_file_name", "recorded_date_timezone"]
# baw-server precision includes both jitter and exclusion ranges, converted from degrees to metres.
PRECISION_M = round((0.003 + 0.0003) * 111319.5)
RECORDER = {1168: "Frontier Labs BAR-LT (its manual makes GPS clock sync the default, user-disableable; the project does not say it was on)", 1323: "AudioMoth"}
METHOD = (f"POST {API}/projects/filter (items 500, 1 page); POST {API}/sites/filter (items 500, all pages); "
          f"POST {API}/audio_recordings/filter with filter {{and:[{{id gt LAST}},{{id lteq SHARD_END}},{{recorded_date gteq {FROM}}}]}} "
          f"(default status ready), projection include {FIELDS}, sorting id asc, paging items {PAGE}; id shards of {SPAN}, {WORKERS} workers; "
          f"A2O overlap check against {os.path.basename(A2O_CSV)}")
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "aircraftwitness-manifest/1.0 (research; +https://github.com/nyu-kr/aircraftwitness)"})


def post(path, body):
    for k in range(8):
        try:
            r = S.post(API + path, json=body, timeout=120)
            if r.status_code == 200:
                return r.text
            err = f"http {r.status_code} {r.text[:200]}"
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break
        except requests.RequestException as e:
            err = str(e)
        time.sleep(min(60, 3 * 2 ** k))
    sys.exit(f"{path} failed: {err}")


def listing(name, path):
    f = f"{RAW}/{name}.json"
    if not os.path.exists(f):
        data, p = [], 1
        while True:
            t = json.loads(post(path, {"filter": {"id": {"gt": 0}}, "sorting": {"order_by": "id", "direction": "asc"}, "paging": {"items": 500, "page": p}}), parse_float=str)
            data += t["data"]
            if p >= t["meta"]["paging"]["max_page"]:
                break
            p += 1
        json.dump(data, open(f + ".tmp", "w"))
        os.replace(f + ".tmp", f)
    return {x["id"]: x for x in json.load(open(f))}


def shard(lo, hi):
    d = f"{RAW}/pages/{lo:08d}"
    os.makedirs(d, exist_ok=True)
    gt = lo
    while True:
        f = f"{d}/{gt:08d}.json.gz"
        data = json.loads(gzip.open(f, "rt").read())["data"] if os.path.exists(f) else None
        if data is None or (len(data) < PAGE and hi is None):
            cond = [{"id": {"gt": gt}}, {"recorded_date": {"gteq": FROM}}] + ([{"id": {"lteq": hi}}] if hi else [])
            t = post("/audio_recordings/filter", {"filter": {"and": cond}, "projection": {"include": FIELDS},
                     "sorting": {"order_by": "id", "direction": "asc"}, "paging": {"items": PAGE}})
            data = json.loads(t)["data"]
            with gzip.open(f + ".tmp", "wt") as g:
                g.write(t)
            os.replace(f + ".tmp", f)
            time.sleep(PAUSE)
        if len(data) < PAGE:
            return
        gt = data[-1]["id"]


def fetch_all():
    t = post("/audio_recordings/filter", {"filter": {"id": {"gt": 0}}, "projection": {"include": ["id"]},
             "sorting": {"order_by": "id", "direction": "desc"}, "paging": {"items": 1}})
    top = json.loads(t)["data"][0]["id"]
    los = list(range(0, top + 1, SPAN))
    jobs = [(lo, lo + SPAN if i < len(los) - 1 else None) for i, lo in enumerate(los)]
    with ThreadPoolExecutor(WORKERS) as ex:
        for _ in ex.map(lambda j: shard(*j), jobs):
            pass


def a2o_keys():
    # Matching sites allow 0.01 degree for server location jitter.
    k = {}
    with gzip.open(A2O_CSV, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            k.setdefault((r["utc_start"], round(float(r["duration_s"] or 0), 3)), []).append((r["public_latitude"], r["public_longitude"]))
    return k


def is_a2o(o, a2o):
    if not o["public_latitude"]:
        return False
    return any(la and abs(float(la) - float(o["public_latitude"])) < 0.01 and abs(float(lo) - float(o["public_longitude"])) < 0.01
               for la, lo in a2o.get((o["utc_start"], round(float(o["duration_s"] or 0), 3)), ()))


def desc_cc(p):  # licence field null but the project description names CC BY 4.0
    d = p.get("description") or ""
    return not p.get("license") and "Creative Commons" in d and "Attribution 4.0" in d


NAME = re.compile(r"(20\d\d)(\d\d)(\d\d)[T_-]?(\d\d)(\d\d)(\d\d)([+-]\d{4})?")
TF, ZONES = TimezoneFinder(), {}


def zones_of(r, s):
    z = {r.get("recorded_date_timezone"), (s.get("timezone_information") or {}).get("identifier")}
    if s.get("latitude"):
        k = (s["latitude"], s["longitude"])
        if k not in ZONES:
            ZONES[k] = TF.timezone_at(lat=float(k[0]), lng=float(k[1]))
        z.add(ZONES[k])
    return sorted(x for x in z if x)


def clock_check(r, s):

    m = NAME.search(r.get("original_file_name") or "")
    zs = zones_of(r, s)
    if not m or not zs or m.group(7):
        return None
    try:
        loc = datetime.datetime(*map(int, m.groups()[:6]))
    except ValueError:
        return None
    u = datetime.datetime.fromisoformat(r["recorded_date"].replace("Z", "+00:00"))
    off = (loc - u.replace(tzinfo=None)).total_seconds() / 3600
    ok = {0.0}
    for z in zs:
        ok |= {datetime.datetime(u.year, mo, 15, tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(z)).utcoffset().total_seconds() / 3600 for mo in (1, 7)}
    return off, "/".join(zs), off in ok


def row(r, st, pj, lic):
    rid, rd, dur = r["id"], r["recorded_date"], r["duration_seconds"]
    s = st.get(r["site_id"], {})
    p = pj[s["project_ids"][0]] if s.get("project_ids") else {}
    lat, lon = s.get("latitude") or "", s.get("longitude") or ""
    pos = bool(lat and lon)
    obf = pos and s.get("location_obfuscated") is True
    dec = lambda v: len(v.split(".")[1]) if "." in v else 0
    end = 300 if dur is None or float(dur) >= 300 else int(float(dur))
    cc = clock_check(r, s)
    bad = cc is not None and not cc[2]
    owners = ",".join(str(o) for o in p.get("owner_ids", []))
    return {
        "recording_id": f"ECO{rid}", "source": "ecosounds", "source_recording_id": str(rid),
        "source_url": f"https://www.ecosounds.org/listen/{rid}",
        "audio_url": f"{API}/audio_recordings/{rid}/media.flac?start_offset=0&end_offset={end}",
        "credit": f"{p.get('name', '')} (QUT Ecosounds project {p.get('id', '')}; owner user ids {owners}; owner names are not shown to anonymous API callers)",
        "audio_licence": lic, "redistributable": "yes" if lic.startswith("CC-BY-4.0") or lic.startswith("CC0") else "no",
        "public_latitude": lat if pos else "", "public_longitude": lon if pos else "",
        "place_decimals": min(dec(lat), dec(lon)) if pos else "",
        "position_basis": "portal site coordinate, operator-obfuscated" if obf else ("portal site coordinate" if pos else "none: site has no portal coordinate"),
        "position_precision_m": PRECISION_M if obf else "", "obscured": "true" if obf else "false",
        "local_start": rd or "", "tz": "", "utc_start": rd.replace(".000Z", "Z") if rd else "", "local_date": rd[:10] if rd else "",
        "duration_s": dur if dur is not None else "", "time_grade": "unknown" if bad else "device",
        "time_basis": (f"recorded_date is UTC but the file name read as local time sits {cc[0]:+g} h from it, which is neither 0 nor a UTC offset of {cc[1]} (standard or daylight); the stored instant is doubtful"
                       if bad else "recorded_date is UTC, imported from the file name (original_file_name) with the uploader's zone; recorder clock set at deployment, "
                       f"no GPS time sync stated in the project {p.get('id', '')} description (projects/filter id {p.get('id', '')})"
                       + (f"; recorder: {RECORDER[p['id']]}" if p.get("id") in RECORDER else "")),
        "time_precision_s": 1, "audio_access": "fetch", "environment": "outdoor",
        "extra": json.dumps({"site_id": r["site_id"], "site": s.get("name"), "project_id": p.get("id"), "zone": r.get("recorded_date_timezone"),
                             "file": r.get("original_file_name"), **({"name_off_h": cc[0]} if cc else {}), **({"licence_in_description": "CC BY 4.0"} if desc_cc(p) else {}), "audio_note": "segments of <=300 s via start_offset/end_offset"}, ensure_ascii=False, separators=(",", ":")),
    }


def build(st, pj):
    os.makedirs(OUT, exist_ok=True)
    lic = {i: (p["license"] or "not stated") for i, p in pj.items()}
    a2o = a2o_keys()
    c = dict(rows=0, rows_from_2023_02_16=0, rows_from_2023_02_16_with_time_and_place=0, hrs=0.0, obscured=0, redistributable_yes=0, a2o_same_time=0, a2o_dup=0)
    places, days, seen = set(), set(), set()
    with gzip.open(f"{OUT}/ecosounds.csv.gz.tmp", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        for f in sorted(glob.glob(f"{RAW}/pages/*/*.json.gz")):
            for r in json.loads(gzip.open(f, "rt").read(), parse_float=str)["data"]:
                if r["id"] in seen or not r["recorded_date"] or r["recorded_date"] < FROM:
                    continue
                seen.add(r["id"])
                s = st.get(r["site_id"], {})
                o = row(r, st, pj, lic.get((s.get("project_ids") or [0])[0], "not stated"))
                if (o["utc_start"], round(float(o["duration_s"] or 0), 3)) in a2o:
                    c["a2o_same_time"] += 1
                if is_a2o(o, a2o):
                    c["a2o_dup"] += 1
                    continue
                w.writerow(o)
                c["rows"] += 1
                c["obscured"] += o["obscured"] == "true"
                c["redistributable_yes"] += o["redistributable"] == "yes"
                if o["public_latitude"]:
                    places.add((o["public_latitude"], o["public_longitude"]))
                if o["utc_start"] >= "2023-02-16":
                    c["rows_from_2023_02_16"] += 1
                    c["rows_from_2023_02_16_with_time_and_place"] += bool(o["public_latitude"])
                    c["hrs"] += float(o["duration_s"] or 0) / 3600
                    days.add(o["local_date"])
    os.replace(f"{OUT}/ecosounds.csv.gz.tmp", f"{OUT}/ecosounds.csv.gz")
    out = {"source": "ecosounds", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rows": c["rows"], "rows_with_utc_start": c["rows"], "rows_from_2023_02_16": c["rows_from_2023_02_16"],
           "rows_from_2023_02_16_with_time_and_place": c["rows_from_2023_02_16_with_time_and_place"],
           "hours_from_2023_02_16": round(c["hrs"], 1), "distinct_places": len(places), "distinct_days_from_2023_02_16": len(days),
           "obscured": c["obscured"], "redistributable_yes": c["redistributable_yes"], "method": METHOD}
    json.dump(out, open(f"{OUT}/ecosounds.counts.json", "w"), indent=1)
    print(f"A2O overlap: {c['a2o_dup']} dropped (same start, duration and place within 0.01 deg); {c['a2o_same_time']} rows share start and duration with an A2O row, the rest are elsewhere and kept")


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True)
    pj = listing("projects", "/projects/filter")
    st = listing("sites", "/sites/filter")
    if "--limit-shards" in sys.argv:
        n = int(sys.argv[sys.argv.index("--limit-shards") + 1])
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(lambda i: shard(i * SPAN, (i + 1) * SPAN), range(n)))
        sys.exit()
    fetch_all()
    build(st, pj)
