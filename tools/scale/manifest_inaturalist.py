#!/usr/bin/env python3
import sys, os, re, time, json, gzip, csv, datetime, glob
import threading
from concurrent.futures import ThreadPoolExecutor
import requests

CACHE = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/inaturalist"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
API = "https://api.inaturalist.org/v1/observations"
PARAMS = {"sounds": "true", "d1": "2016-01-01", "order_by": "id", "order": "asc", "per_page": 200}
UA = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
DAILY_CAP = 9800  # iNat asks for ~10k requests a day, ~1 per second

KEEP = "id uri time_observed_at observed_on observed_on_string observed_time_zone time_zone_offset created_time_zone created_at oauth_application_id location geojson positional_accuracy public_positional_accuracy geoprivacy taxon_geoprivacy obscured quality_grade".split()
SKEEP = "id license_code attribution file_url file_content_type subtype hidden native_sound_id".split()


def trim(o):
    t = {k: o.get(k) for k in KEEP}
    t["taxon"] = (o.get("taxon") or {}).get("name")
    t["sounds"] = [{k: s.get(k) for k in SKEEP} for s in o.get("sounds") or []]
    return t


LOCK = threading.Lock(); STOP = threading.Event(); STATE = {"last": 0.0, "pages": 0, "until": 0.0}

BOUNDS = [0, 50000000, 80000000, 110000000, 130000000, 160000000, 180000000, 200000000, 220000000, 240000000, 260000000, 280000000, 300000000, 330000000, 350000000, 370000000, 390000000]
WORKERS = 4
SPACING = 1.4


def start_request():
    with LOCK:
        f = CACHE + "/requests_today.json"; today = datetime.date.today().isoformat()
        d = json.load(open(f)) if os.path.exists(f) else {}
        d[today] = d.get(today, 0) + 1
        if d[today] > DAILY_CAP: STOP.set(); raise RuntimeError(f"daily request cap {DAILY_CAP} reached; rerun tomorrow (cache resumes)")
        json.dump(d, open(f, "w"))
        time.sleep(max(0.0, SPACING - (time.time() - STATE["last"]), STATE["until"] - time.time())); STATE["last"] = time.time()


def get(sess, after):
    for attempt in range(6):
        if STOP.is_set(): raise RuntimeError("stopped")
        start_request()
        try:
            r = sess.get(API, params={**PARAMS, "id_above": after}, headers=UA, timeout=120)
            if r.status_code == 200:
                j = r.json(); res = j["results"]
                ids = [o["id"] for o in res]
                assert ids == sorted(set(ids)) and all(i > after for i in ids), "ids not ascending above id_above"
                return j["total_results"], res
            wait = int(r.headers.get("Retry-After", 120 if r.status_code == 429 else 30))
            print(f"HTTP {r.status_code} after id_above={after}, pausing all workers {wait}s, headers {dict(r.headers)}", flush=True)
            STATE["until"] = max(STATE["until"], time.time() + wait)
        except (requests.RequestException, ValueError, KeyError, AssertionError) as e:
            wait = 30; print(f"error after id_above={after}: {e!r}, pausing all workers {wait}s", flush=True)
        STATE["until"] = max(STATE["until"], time.time() + wait)
    STOP.set(); raise RuntimeError(f"giving up at id_above={after}")


def shard(i, maxpages):
    sess = requests.Session(); after = BOUNDS[i]; end = BOUNDS[i + 1] if i + 1 < len(BOUNDS) else None; fetched = 0
    while not STOP.is_set():
        f = f"{CACHE}/pages/{after:010d}.json.gz"
        if os.path.exists(f):
            p = json.load(gzip.open(f, "rt"))
        else:
            total, res = get(sess, after)
            p = {"after": after, "total_results": total, "n": len(res), "results": [trim(o) for o in res]}
            json.dump(p, gzip.open(f + ".tmp", "wt")); os.replace(f + ".tmp", f)
            fetched += 1
            with LOCK:
                STATE["pages"] += 1
                if STATE["pages"] % 200 == 0: print(f"{STATE['pages']} pages fetched, shard {i} at id_above={after}, total_results={total}", flush=True)
            if maxpages and fetched >= maxpages: return
        if p["n"] < PARAMS["per_page"]: return
        after = p["results"][-1]["id"]
        if end is not None and after >= end: return


def fetch(maxpages):
    with ThreadPoolExecutor(WORKERS) as ex:
        for fut in [ex.submit(shard, i, maxpages) for i in range(len(BOUNDS))]: fut.result()
    print(f"fetch complete: {STATE['pages']} new pages")


GBIF = "https://api.gbif.org/v1/occurrence/search"
GQ = {"datasetKey": "50c9509d-22c7-4a22-a47d-8c48425ef4a7", "mediaType": "Sound", "hasCoordinate": "true", "limit": 300}
GKEEP = "key catalogNumber occurrenceID eventDate eventTime verbatimEventDate decimalLatitude decimalLongitude coordinateUncertaintyInMeters informationWithheld dataGeneralizations scientificName".split()


def gbif_page(a, b, off, tag):   # GBIF deep offsets are slow, so date windows stay small.
    f = f"{CACHE}/gbif/{tag}_{off:06d}.json.gz"
    if os.path.exists(f): return json.load(gzip.open(f, "rt"))
    for k in range(6):
        try:
            r = requests.get(GBIF, params={**GQ, "eventDate": f"{a},{b}", "offset": off}, headers=UA, timeout=(20, 90))
            if r.status_code == 200: break
            print(f"GBIF {tag} offset {off}: HTTP {r.status_code}", flush=True)
        except requests.RequestException as e: print(f"GBIF {tag} offset {off}: {type(e).__name__}", flush=True)
        time.sleep(20 * (k + 1))
    else: sys.exit(f"GBIF {tag} offset {off} failed")
    j = r.json(); assert j["count"] < 100000, f"{tag}: {j['count']} records, over GBIF's offset cap"
    p = {"count": j["count"], "end": j["endOfRecords"], "results": [{**{k: o.get(k) for k in GKEEP}, "sounds": [{k: x.get(k) for k in ("identifier", "references", "license", "rightsHolder", "creator")} for x in o.get("media") or [] if x.get("type") == "Sound"]} for o in j["results"]]}
    with gzip.open(f + ".tmp", "wt") as fh: json.dump(p, fh)
    os.replace(f + ".tmp", f); return p


def gbif():
    os.makedirs(f"{CACHE}/gbif", exist_ok=True); day, end, done = datetime.date(2023, 2, 16), datetime.date.today(), set()
    for f in glob.glob(f"{CACHE}/gbif/????-??_000000.json.gz"):
        m = os.path.basename(f)[:7]; n = json.load(gzip.open(f, "rt"))["count"]
        if all(os.path.exists(f"{CACHE}/gbif/{m}_{o:06d}.json.gz") for o in range(0, n, 300)): done.add(m)
    days = []
    while day <= end:
        if day.strftime("%Y-%m") not in done: days.append(day.isoformat())
        day += datetime.timedelta(days=1)
    def one(d):
        c = gbif_page(d, d, 0, d)["count"]
        for off in range(300, c, 300): gbif_page(d, d, off, d)
        return c
    with ThreadPoolExecutor(2) as ex: n = list(ex.map(one, days[::-1]))
    print(len(done), "whole months reused;", len(days), "day windows,", sum(n), "records", flush=True)


COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
FORUM = "forum.inaturalist.org/t/voice-memos-not-uploading-location-time-data/49151 and /t/bulk-upload-of-audiomoth-recordings/64203"
TIME_BASIS = f"observation time is typed on the web or captured by the iNaturalist app, and iNat does not read date/time from sound files ({FORUM}); the API does not say which, so grade unknown"
DATE_BASIS = f"observation has a date only (time_observed_at null); iNat does not read date/time from sound files ({FORUM})"
OBSCURED_BASIS = "obscured by iNaturalist: random point in the 0.2 degree cell (github.com/inaturalist/inaturalist app/models/observation.rb random_neighbor_lat_lon)"
OPEN_BASIS = "public coordinate set by the observer (GPS or map pin) as printed by the iNaturalist API"
PRIVATE_BASIS = "private by observer or taxon geoprivacy: iNaturalist publishes no coordinate"
GBIF_TIME_BASIS = f"GBIF export of the iNaturalist observation: eventDate local time plus the eventTime UTC offset; " + TIME_BASIS
OPEN_GBIF_BASIS = "public coordinate set by the observer (GPS or map pin) as exported to GBIF by iNaturalist (research grade)"
OBSCURED_GBIF_BASIS = "coarsened by iNaturalist before export (GBIF informationWithheld/dataGeneralizations)"
WIN = "2023-02-16"


def licence(code):
    return "all rights reserved" if not code else "CC0" if code == "cc0" else "CC " + code[3:].upper()


def decimals(x):
    return len(x.split(".")[1]) if "." in x else 0


def walk():
    for i, lo in enumerate(BOUNDS):
        after = lo; end = BOUNDS[i + 1] if i + 1 < len(BOUNDS) else None
        while True:
            f = f"{CACHE}/pages/{after:010d}.json.gz"
            if not os.path.exists(f): yield i, after, None; break
            p = json.load(gzip.open(f, "rt")); yield i, after, p
            if p["n"] < PARAMS["per_page"]: break
            after = p["results"][-1]["id"]
            if end is not None and after >= end: break


def rows(partial, stats):
    now = datetime.datetime.now(datetime.timezone.utc); seen_obs = set(); seen_snd = set(); lim = datetime.datetime(2016, 1, 1, tzinfo=datetime.timezone.utc)
    for i, after, p in walk():
        if p is None:
            if partial: continue
            sys.exit(f"cache incomplete: shard {i} missing page id_above={after}; run fetch")
        stats["api_total_results"] = p["total_results"]
        for o in p["results"]:
            if o["id"] in seen_obs: stats["duplicate_observations"] += 1; continue
            seen_obs.add(o["id"]); stats["observations"] += 1
            gp = {o["geoprivacy"], o["taxon_geoprivacy"]}; private = "private" in gp
            lat = lon = ""
            if o["location"] and not private: lat, lon = (x.strip() for x in o["location"].split(","))
            if private: stats["private_observations"] += 1
            if private and o["location"]: stats["private_with_coordinate_blanked"] += 1
            if not lat and not private: stats["no_coordinate_not_private"] += 1
            obscured = bool(o["obscured"]) or private or (bool(lat) and "obscured" in gp)
            if obscured and not o["obscured"] and not private: stats["obscured_by_geoprivacy_though_api_flag_false"] += 1
            basis = PRIVATE_BASIS if private else OBSCURED_BASIS if obscured else OPEN_BASIS if lat else "observation has no coordinate"
            ppa = o["public_positional_accuracy"]
            t = o["time_observed_at"]; od = o["observed_on"]
            if t:
                dt = datetime.datetime.fromisoformat(t); utc = dt.astimezone(datetime.timezone.utc)
                if utc < lim or utc > now: stats["dropped_utc_start_outside_2016_to_now"] += 1; continue
                utc_s = utc.strftime("%Y-%m-%dT%H:%M:%SZ"); local = t; date = od or t[:10]; prec = 1 if dt.second else 60; tz = o["observed_time_zone"] or ""; tb = TIME_BASIS
            elif od:
                utc_s = tz = ""; local = date = od; prec = 86400; tb = DATE_BASIS
            else: stats["dropped_no_date"] += 1; continue
            extra = {"obs": o["id"], "qg": o["quality_grade"], "taxon": o["taxon"]}
            if o["oauth_application_id"]: extra["oa"] = o["oauth_application_id"]
            for sd in o["sounds"]:
                if sd["hidden"]: stats["dropped_hidden_sounds"] += 1; continue
                if sd["id"] in seen_snd: stats["duplicate_sounds"] += 1; continue
                seen_snd.add(sd["id"])
                lic = licence(sd["license_code"])
                yield [f"INAT{sd['id']}", "inaturalist", f"{o['id']}_{sd['id']}", f"https://www.inaturalist.org/observations/{o['id']}", sd["file_url"] or "",
                       sd["attribution"] or "", lic, "no" if lic == "all rights reserved" else "yes", lat, lon, min(decimals(lat), decimals(lon)) if lat else "", basis,
                       "" if ppa is None or not lat else ppa, str(obscured).lower(), local, tz, utc_s, date, "", "unknown", tb, prec,
                       "download" if sd["file_url"] else "none", "unknown", json.dumps(extra, separators=(",", ":"))]
    for f in sorted(glob.glob(f"{CACHE}/gbif/*.json.gz")):
        for o in json.load(gzip.open(f, "rt"))["results"]:
            oid = int(o["catalogNumber"])
            if oid in seen_obs: stats["gbif_observations_already_listed_by_api"] += 1; continue
            seen_obs.add(oid); stats["gbif_observations"] += 1
            lat, lon = repr(o["decimalLatitude"]), repr(o["decimalLongitude"])
            obscured = bool(o["informationWithheld"] or o["dataGeneralizations"])
            stats["gbif_obscured"] += obscured
            ev = re.match(r"[\d-]*(T\d\d:\d\d(:\d\d)?)?", o["eventDate"] or "")[0]; m = re.search(r"(Z|[+-]\d\d:?\d\d)$", o["eventTime"] or ""); off = m[1] if m else ""
            if len(ev) >= 16 and off:
                dt = datetime.datetime.fromisoformat(ev + (":00" if len(ev) == 16 else "") + ("+00:00" if off == "Z" else off)); utc = dt.astimezone(datetime.timezone.utc)
                if utc > now: stats["dropped_utc_start_outside_2016_to_now"] += 1; continue
                utc_s = utc.strftime("%Y-%m-%dT%H:%M:%SZ"); prec = 1 if dt.second else 60; tb = GBIF_TIME_BASIS
            else: utc_s = ""; prec = 86400; tb = DATE_BASIS; stats["gbif_date_only"] += 1
            extra = {"obs": oid, "qg": "research", "taxon": o["scientificName"], "via": "gbif", "gbif": o["key"]}
            if off: extra["off"] = off
            for sd in o["sounds"]:
                sid = int(sd["references"].rstrip("/").rsplit("/", 1)[1]) if sd.get("references") and "/sounds/" in sd["references"] else None
                if sid is None or sid in seen_snd: stats["duplicate_sounds"] += 1; continue
                seen_snd.add(sid)
                yield [f"INAT{sid}", "inaturalist", f"{oid}_{sid}", o["occurrenceID"] or f"https://www.inaturalist.org/observations/{oid}", sd["identifier"] or "",
                       sd["rightsHolder"] or sd["creator"] or "", sd["license"] or "", "no", lat, lon, min(decimals(lat), decimals(lon)), OBSCURED_GBIF_BASIS if obscured else OPEN_GBIF_BASIS,
                       "" if o["coordinateUncertaintyInMeters"] is None else o["coordinateUncertaintyInMeters"], str(obscured).lower(), o["verbatimEventDate"] or ev, "", utc_s, ev[:10], "", "unknown", tb, prec,
                       "download" if sd["identifier"] else "none", "unknown", json.dumps(extra, separators=(",", ":"))]


def build(partial):
    stats = {k: 0 for k in "observations duplicate_observations duplicate_sounds dropped_hidden_sounds dropped_no_date dropped_utc_start_outside_2016_to_now private_observations private_with_coordinate_blanked no_coordinate_not_private obscured_by_geoprivacy_though_api_flag_false gbif_observations gbif_observations_already_listed_by_api gbif_obscured gbif_date_only".split()}
    out = f"{CACHE}/partial.csv.gz" if partial else f"{OUT}/inaturalist.csv.gz"
    n = utc = win = win_tp = obs = red = 0; places = set(); days = set()
    with gzip.open(out + ".tmp", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS)
        for r in rows(partial, stats):
            w.writerow(r); n += 1
            d = {c: v for c, v in zip(COLS, r)}
            utc += bool(d["utc_start"]); obs += d["obscured"] == "true"; red += d["redistributable"] == "yes"
            if d["public_latitude"]: places.add((d["public_latitude"], d["public_longitude"]))
            if (d["utc_start"] or d["local_date"]) >= WIN:
                win += 1; days.add(d["local_date"]); win_tp += bool(d["utc_start"] and d["public_latitude"])
    os.replace(out + ".tmp", out)
    counts = {"source": "inaturalist", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": n, "rows_with_utc_start": utc,
              "rows_from_2023_02_16": win, "rows_from_2023_02_16_with_time_and_place": win_tp, "hours_from_2023_02_16": 0, "distinct_places": len(places),
              "distinct_days_from_2023_02_16": len(days), "obscured": obs, "redistributable_yes": red,
              "method": f"GET {API}?{'&'.join(f'{k}={v}' for k, v in PARAMS.items())}&id_above=LAST, 200 per page, in {len(BOUNDS)} id_above shards ({BOUNDS}), each followed by id_above=last id until it reaches the next shard start; plus research-grade observations from GBIF ({GBIF}?{'&'.join(f'{k}={v}' for k, v in GQ.items())}, one eventDate window per month from 2023-02-16) that the API pages do not hold, the API row winning per observation and sound; rows dated by utc_start, or local_date for date-only rows, against 2023-02-16; hours unknown (no duration in metadata)",
              **stats}
    json.dump(counts, open(out.replace(".csv.gz", ".counts.json"), "w"), indent=1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]: fetch(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif sys.argv[1:2] == ["gbif"]: gbif()
    elif sys.argv[1:2] == ["build"]: build("--partial" in sys.argv)
    else: sys.exit(__doc__)
