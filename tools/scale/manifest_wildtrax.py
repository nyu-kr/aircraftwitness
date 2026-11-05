#!/usr/bin/env python3
import csv, datetime, glob, gzip, io, json, os, re, sys, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import requests
from timezonefinder import TimezoneFinder

API = "https://www-api.wildtrax.ca/bis"
RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/wildtrax"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
REPORT = ("locationReport=true&projectReport=false&tagReport=false&recordingReport=true&mainReport=false&aiReport=false&imageSetReport=false"
          "&imageReport=false&megaDetectorReport=false&includeMetaData=true&sensorId=ARU")
H = {"Content-Type": "application/json", "Origin": "https://wildtrax.ca", "Referer": "https://wildtrax.ca/",
     "User-Agent": "aircraftwitness-manifest/1.0 (research; +https://github.com/nyu-kr/aircraftwitness)"}
STATUS = {1: "Published - Public", 4: "Published - Map+Report Only"}  # 6 = Map Only: locations only, no recording report
LICENCE = ("no licence stated; Data Owner retains all rights (WildTrax Policies 2021-03-19: "
           "'Data Owners retain exclusive ownership over their data at all times within WildTrax')")
CCRE = re.compile(r"\bCC[- ]BY(?:[- ](?:NC|SA|ND))*(?:[- ]\d\.\d)?\b", re.I)
GRADE_NOTE = "recorder clock set at deployment (SM2/SM4/AudioMoth class ARU; WildTrax prints no GPS time statement, its FAQ says GPS units serve localization)"
BASIS = {
    "tf": "recording_date_time (WildTrax recording_report) is the recorder file-start stamp in the location's local time; zone from timezonefinder at the printed site, "
          "same UTC instant as WildTrax location_report.timezone; " + GRADE_NOTE,
    "tf0": "recording_date_time (WildTrax recording_report) is the recorder file-start stamp in the location's local time; zone from timezonefinder at the printed site "
           "(WildTrax location_report.timezone is blank); " + GRADE_NOTE,
    "src": "recording_date_time (WildTrax recording_report) is the recorder file-start stamp in the location's local time; location has no usable coordinate (hidden or unset) so the zone is "
           "WildTrax location_report.timezone; " + GRADE_NOTE,
    "conflict": "recording_date_time is local time but timezonefinder and WildTrax location_report.timezone give different UTC instants (zones in extra); utc_start withheld; " + GRADE_NOTE,
    "gap": "recording_date_time is local time but falls in a DST gap or overlap so the UTC instant is ambiguous; utc_start withheld; " + GRADE_NOTE,
    "none": "recording_date_time is local time but no zone could be determined (no usable coordinate or report timezone); utc_start withheld; " + GRADE_NOTE,
}
METHOD = (f"POST {API}/get-projects {{sensorId:ARU}}; per project with statusId 1 or 4: POST {API}/download-report?{REPORT} body {{projectIds:[id]}} "
          "(Origin/Referer https://wildtrax.ca), reading recording_report.csv, location_report.csv and the abstract txt; at most 1 request per second; "
          "audio never fetched; hours are task_duration sums, not recording lengths; time_and_place counts rows with utc_start and an unobscured coordinate")
S = requests.Session()
lock, last = threading.Lock(), [0.0]


def post(url, body, tries=3, timeout=170):
    for k in range(tries):
        with lock:  # start-rate limiter: one request per second across workers
            time.sleep(max(0, last[0] + 1.0 - time.time()))
            last[0] = time.time()
        try:
            r = S.post(url, json=body, headers=H, timeout=timeout)
            if r.status_code == 200:
                return r, None
            err = f"http {r.status_code} {r.text[:150]}"
        except requests.RequestException as e:
            err = str(e)[:150]
        time.sleep(3 * (k + 1))
    return None, err


def projects():
    f = f"{RAW}/projects.json"
    if not os.path.exists(f):
        r, err = post(f"{API}/get-projects", {"sensorId": "ARU"})
        if r is None:
            sys.exit(f"get-projects failed: {err}")
        open(f, "w").write(r.text)
    d = json.load(open(f))
    out = [p for p in d["results"] if p["sensorId"] == "ARU" and p["statusId"] in STATUS]
    return sorted(out, key=lambda p: (p["statusId"] != 1, p["id"]))  # The public row takes precedence when a recording is listed twice.


def fetch(p, tries=3, timeout=170):
    f = f"{RAW}/zips/{p['id']}.zip"
    if os.path.exists(f):
        return p["id"], None
    r, err = post(f"{API}/download-report?{REPORT}", {"projectIds": [p["id"]]}, tries, timeout)
    if r is None or r.content[:2] != b"PK":
        return p["id"], err or "not a zip"
    open(f + ".tmp", "wb").write(r.content)
    os.replace(f + ".tmp", f)
    return p["id"], None


def read_zip(pid):
    z = zipfile.ZipFile(f"{RAW}/zips/{pid}.zip")
    nm = lambda s: next(n for n in z.namelist() if n.endswith(s))
    rd = lambda s: csv.DictReader(io.TextIOWrapper(z.open(nm(s)), encoding="utf-8-sig"))
    abstract = z.read(nm("abstract_aru_conversion.txt")).decode("utf-8", "replace")
    return rd("location_report.csv"), rd("recording_report.csv"), abstract


def meta(abstract):
    g = lambda a, b: re.sub(r"\s+", " ", abstract[abstract.find(a) + len(a):abstract.find(b)]).strip(' "')
    return g("Organization Name:", "Project Type:"), g("Project Description:", "Project Results:")


def decimals(s):
    return len(s.split(".")[1]) if "." in s else 0


def utc(dt, zi):
    a, b = dt.replace(tzinfo=zi, fold=0), dt.replace(tzinfo=zi, fold=1)
    ua, ub = a.astimezone(datetime.timezone.utc), b.astimezone(datetime.timezone.utc)
    return ua if ua == ub and ua.astimezone(zi).replace(tzinfo=None) == dt else None  # None: ambiguous or nonexistent local time


def build(plist, outdir):
    csv.field_size_limit(10**8)
    tf, zis, tfc = TimezoneFinder(), {}, {}
    zone = lambda n: zis.setdefault(n, ZoneInfo(n))
    also = {}
    for p in plist:
        _, recs, _ = read_zip(p["id"])
        for rid in {r["recording_id"] for r in recs}:
            also.setdefault(rid, []).append(p["id"])
    c = dict(rows=0, utc=0, win=0, win_tp=0, hrs=0.0, obs=0, red=0, drop_early=0, drop_late=0, no_time=0, conflict=0, gap=0, nozone=0, badpos=0)
    places, days, seen = set(), set(), set()
    now = datetime.datetime.now(datetime.timezone.utc)
    os.makedirs(outdir, exist_ok=True)
    with gzip.open(f"{outdir}/wildtrax.csv.gz.tmp", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        for p in plist:
            locs, recs, abstract = read_zip(p["id"])
            locs = {l["location_id"]: l for l in locs}
            org, desc = meta(abstract)
            cc = CCRE.search(p["fullNm"] + " " + desc)
            lic = cc.group(0) if cc else LICENCE
            public = p["statusId"] == 1
            for r in recs:
                rid = r["recording_id"]
                if rid in seen or also[rid][0] != p["id"]:
                    continue
                seen.add(rid)
                dts = r["recording_date_time"]
                try:
                    dt = datetime.datetime.fromisoformat(dts)
                except ValueError:
                    c["no_time"] += 1
                    continue
                if dts[:10] < "2016-01-01":
                    c["drop_early"] += 1
                    continue
                l = locs.get(r["location_id"], {})
                lat, lon, vis, buf = l.get("latitude", ""), l.get("longitude", ""), l.get("location_visibility", ""), l.get("location_buffer_m", "")
                try:
                    ok = vis == "Visible" and -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180 and (float(lat), float(lon)) != (0, 0)
                except ValueError:
                    ok = False
                if vis == "Visible" and not ok:
                    c["badpos"] += 1
                if not ok:
                    lat = lon = ""
                prec = int(float(buf)) if buf not in ("", "0") else ""
                obscured = bool(prec) or vis.startswith("Hidden")
                # Use the reported zone only to check the coordinate zone or when the site is hidden.
                tzr, tz, why, ut = l.get("timezone", ""), "", "none", None
                if ok:
                    tz = tfc.setdefault((lat, lon), tf.timezone_at(lat=float(lat), lng=float(lon)) or "")
                    if tz:
                        ut, why = utc(dt, zone(tz)), "tf" if tzr else "tf0"
                        if ut is None:
                            why = "gap"
                        elif tzr and tzr != tz:
                            alt = utc(dt, zone(tzr))
                            if alt != ut:
                                ut, why = None, "conflict"
                elif tzr:  # no usable coordinate (hidden or unset): the report's own zone is the only one
                    tz = tzr
                    ut = utc(dt, zone(tz))
                    why = "src" if ut else "gap"
                if ut and ut > now:
                    ut, why = None, "none"
                    c["drop_late"] += 1
                if why in ("conflict", "gap", "none"):
                    c[{"conflict": "conflict", "gap": "gap", "none": "nozone"}[why]] += 1
                    extra_tz = {"tz_tf": tz, "tz_report": tzr} if why == "conflict" else {}
                    tz = ""
                else:
                    extra_tz = {}
                url = r["recording_url"] if public and r["recording_url"].startswith("https://") else ""
                pos = bool(lat)
                basis = ("WildTrax location_report coordinate, no buffer set (true location)" if not prec else
                         f"WildTrax location_report coordinate, public point inside a {prec} m owner buffer") if pos else (
                        f"hidden by the Data Owner ({vis}): no coordinate published" if vis.startswith("Hidden") else "none: location has no coordinate")
                extra = {"project": p["id"], "loc": r["location"], "dur": "task"}
                if r["equipment"]:
                    extra["eq"] = r["equipment"]
                if len(also[rid]) > 1:
                    extra["also_projects"] = also[rid][1:]
                if cc:
                    extra["licence_text"] = desc[:400]
                extra.update(extra_tz)
                dur = r["task_duration"]
                o = {
                    "recording_id": f"WT{rid}", "source": "wildtrax", "source_recording_id": rid, "source_url": r["task_url"] or "https://wildtrax.ca/",
                    "audio_url": url, "credit": f"{org}: {p['fullNm']}", "audio_licence": lic, "redistributable": "yes" if cc else "no",
                    "public_latitude": lat, "public_longitude": lon, "place_decimals": min(decimals(lat), decimals(lon)) if pos else "",
                    "position_basis": basis, "position_precision_m": prec, "obscured": "true" if obscured else "false",
                    "local_start": dts, "tz": tz, "utc_start": ut.strftime("%Y-%m-%dT%H:%M:%SZ") if ut else "", "local_date": dts[:10],
                    "duration_s": f"{float(dur):g}" if dur else "", "time_grade": "device", "time_basis": BASIS[why], "time_precision_s": 1,
                    "audio_access": "download" if url else "request", "environment": "outdoor",
                    "extra": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
                }
                w.writerow(o)
                c["rows"] += 1
                c["utc"] += bool(ut)
                c["obs"] += obscured
                c["red"] += bool(cc)
                if pos:
                    places.add((lat, lon))
                if (o["utc_start"] or o["local_date"]) >= "2023-02-16":
                    c["win"] += 1
                    days.add(o["local_date"])
                    c["hrs"] += float(dur or 0) / 3600
                    c["win_tp"] += bool(ut and pos and not obscured)
    os.replace(f"{outdir}/wildtrax.csv.gz.tmp", f"{outdir}/wildtrax.csv.gz")
    out = {"source": "wildtrax", "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": c["rows"], "rows_with_utc_start": c["utc"],
           "rows_from_2023_02_16": c["win"], "rows_from_2023_02_16_with_time_and_place": c["win_tp"], "hours_from_2023_02_16": round(c["hrs"], 1),
           "distinct_places": len(places), "distinct_days_from_2023_02_16": len(days), "obscured": c["obs"], "redistributable_yes": c["red"], "method": METHOD}
    json.dump(out, open(f"{outdir}/wildtrax.counts.json", "w"), indent=1)
    notes = {k: c[k] for k in ("drop_early", "drop_late", "no_time", "conflict", "gap", "nozone", "badpos") if c[k]}
    if notes:
        print("rows affected:", notes)


if __name__ == "__main__":
    os.makedirs(f"{RAW}/zips", exist_ok=True)
    plist = projects()
    out = OUT
    if "--limit" in sys.argv:
        plist, out = plist[:int(sys.argv[sys.argv.index("--limit") + 1])], f"{RAW}/smoke"
    with ThreadPoolExecutor(2) as ex:
        failed = {pid: e for pid, e in ex.map(fetch, plist) if e}
    for p in plist:  # Reports with thousands of tasks need a longer timeout.
        if p["id"] in failed and (e := fetch(p, 1, 1200)[1]) is None:
            del failed[p["id"]]
    plist = [p for p in plist if p["id"] not in failed]
    if failed:
        print("download-report failed (no zip, project skipped):", failed, file=sys.stderr)
        METHOD += f"; projects whose download failed: {sorted(failed)}"
    build(plist, out)
