#!/usr/bin/env python3
import csv, datetime, glob, gzip, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import requests

API = "https://api.acousticobservatory.org"
RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/a2o"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
PAGE, SPAN, WORKERS, PAUSE = 500, 50000, 4, 0.3
FIELDS = ["id", "recorded_date", "site_id", "duration_seconds"]
CREDIT = "Australian Acoustic Observatory: A Network to Monitor Biodiversity Project Team, with funding from the Australian Government through the Australian Research Council (LE170100033) (2018). Brisbane, QLD: QUT"
BASIS = ("Frontier Labs Solar BAR recorder, GPS time sync (Roe et al. 2021 MEE); recorded_date is UTC (matches the +HHMM in original_file_name, "
         "checked on 10 ids across 2019-2026); our clock study (clock/results/a2o_clock.json) found A2O audio runs 5-10 s behind its label (-4.5 to -9.2 s at the 3 resolved sites)")
# baw-server precision includes both jitter and exclusion ranges, converted from degrees to metres.
PRECISION_M = round((0.003 + 0.0003) * 111319.5)
METHOD = (f"POST {API}/sites/filter (items 500, 1 page); POST {API}/audio_recordings/filter with filter {{and:[{{id gt LAST}},{{id lteq SHARD_END}}]}} "
          f"(default status ready), projection include {FIELDS}, sorting id asc, paging items {PAGE}; id shards of {SPAN}, {WORKERS} workers")
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "aircraftwitness-manifest/1.0 (research; +https://github.com/nyu-kr/aircraftwitness)"})
lock = threading.Lock()


def post(path, body):
    for k in range(8):
        try:
            r = S.post(API + path, json=body, timeout=120)
            if r.status_code == 200:
                return r.text
            err = f"http {r.status_code} {r.text[:200]}"
        except requests.RequestException as e:
            err = str(e)
        time.sleep(min(60, 3 * 2 ** k))
    sys.exit(f"{path} failed: {err}")


def sites():
    f = f"{RAW}/sites.json"
    if not os.path.exists(f):
        body = {"filter": {"id": {"gt": 0}}, "projection": {"include": ["id", "name", "latitude", "longitude", "location_obfuscated"]},
                "sorting": {"order_by": "id", "direction": "asc"}, "paging": {"items": 500, "page": 1}}
        t = post("/sites/filter", body)
        if json.loads(t)["meta"]["paging"]["max_page"] != 1:
            sys.exit("sites need more than one page")
        open(f, "w").write(t)
    return {s["id"]: s for s in json.loads(open(f).read(), parse_float=str)["data"]}


def shard(lo, hi):
    d = f"{RAW}/pages/{lo:08d}"
    os.makedirs(d, exist_ok=True)
    gt = lo
    while True:
        f = f"{d}/{gt:08d}.json.gz"
        data = json.loads(gzip.open(f, "rt").read())["data"] if os.path.exists(f) else None
        if data is None or (len(data) < PAGE and hi is None):
            cond = [{"id": {"gt": gt}}] + ([{"id": {"lteq": hi}}] if hi else [])
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


def row(r, st):
    rid, rd, dur = r["id"], r["recorded_date"], r["duration_seconds"]
    s = st.get(r["site_id"], {})
    lat, lon = s.get("latitude") or "", s.get("longitude") or ""
    pos = bool(lat and lon)
    dec = lambda v: len(v.split(".")[1]) if "." in v else 0
    end = 300 if dur is None or float(dur) >= 300 else int(float(dur))
    return {
        "recording_id": f"A2O{rid}", "source": "a2o", "source_recording_id": str(rid),
        "source_url": f"https://data.acousticobservatory.org/listen/{rid}",
        "audio_url": f"{API}/audio_recordings/{rid}/media.flac?start_offset=0&end_offset={end}",
        "credit": CREDIT, "audio_licence": "CC-BY-4.0", "redistributable": "yes",
        "public_latitude": lat if pos else "", "public_longitude": lon if pos else "",
        "place_decimals": min(dec(lat), dec(lon)) if pos else "",
        "position_basis": "portal site coordinate, operator-obfuscated" if pos else "none: site has no portal coordinate",
        "position_precision_m": PRECISION_M if pos else "", "obscured": "true" if pos else "false",
        "local_start": rd or "", "tz": "", "utc_start": rd.replace(".000Z", "Z") if rd else "", "local_date": rd[:10] if rd else "",
        "duration_s": dur if dur is not None else "", "time_grade": "gps", "time_basis": BASIS, "time_precision_s": 1,
        "audio_access": "fetch", "environment": "outdoor",
        "extra": json.dumps({"site_id": r["site_id"], "site": s.get("name"), "audio_note": "segments of <=300 s via start_offset/end_offset"}, ensure_ascii=False, separators=(",", ":")),
    }


def build(st):
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(f"{RAW}/pages/*/*.json.gz"))
    c = dict(rows=0, rows_with_utc_start=0, rows_from_2023_02_16=0, rows_from_2023_02_16_with_time_and_place=0, hrs=0.0, obscured=0)
    places, days, seen = set(), set(), set()
    with gzip.open(f"{OUT}/a2o.csv.gz.tmp", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        for f in files:
            for r in json.loads(gzip.open(f, "rt").read(), parse_float=str)["data"]:
                if r["id"] in seen or not r["recorded_date"] or r["recorded_date"] < "2016-01-01":
                    continue
                seen.add(r["id"])
                o = row(r, st)
                w.writerow(o)
                c["rows"] += 1
                c["rows_with_utc_start"] += 1
                c["obscured"] += o["obscured"] == "true"
                if o["public_latitude"]:
                    places.add((o["public_latitude"], o["public_longitude"]))
                if o["utc_start"] >= "2023-02-16":
                    c["rows_from_2023_02_16"] += 1
                    c["rows_from_2023_02_16_with_time_and_place"] += bool(o["public_latitude"])
                    c["hrs"] += float(o["duration_s"] or 0) / 3600
                    days.add(o["local_date"])
    os.replace(f"{OUT}/a2o.csv.gz.tmp", f"{OUT}/a2o.csv.gz")
    out = {"source": "a2o", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rows": c["rows"], "rows_with_utc_start": c["rows_with_utc_start"], "rows_from_2023_02_16": c["rows_from_2023_02_16"],
           "rows_from_2023_02_16_with_time_and_place": c["rows_from_2023_02_16_with_time_and_place"],
           "hours_from_2023_02_16": round(c["hrs"], 1), "distinct_places": len(places), "distinct_days_from_2023_02_16": len(days),
           "obscured": c["obscured"], "redistributable_yes": c["rows"], "method": METHOD}
    json.dump(out, open(f"{OUT}/a2o.counts.json", "w"), indent=1)


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True)
    st = sites()
    if "--limit-shards" in sys.argv:
        n = int(sys.argv[sys.argv.index("--limit-shards") + 1])
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(lambda i: shard(i * SPAN, (i + 1) * SPAN), range(n)))
        sys.exit()
    fetch_all()
    build(st)
