#!/usr/bin/env python3
import csv, datetime as dt, gzip, json, os, sys, time, zipfile
import duckdb, numpy as np, pandas as pd, requests
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

C = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/xeno-canto/"; REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../")
OUT = os.environ.get("V2MAN", REPO + "data/v2/manifests") + "/xeno-canto"
DWCA = "https://api.biodiversitydata.nl/v2/specimen/dwca/getDataSet/"
GBIF = "https://api.gbif.org/v1/occurrence/search"
GROUPS = [("birds", "wildlife-sounds-birds", "b1047888-ae52-4179-9dd5-5448ea342a24"), ("soundscapes", "wildlife-sounds-soundscapes", "ff571aeb-46bf-45c4-ad2c-af4d68315765"),
          ("orthoptera", "wildlife-sounds-grasshoppers", "ecce3417-d4fd-4fab-9009-09ad3f3f62b3"), ("anura", "wildlife-sounds-frogs", "bcf8d1fc-6bf0-4f57-9076-7d9ae2828ec2"),
          ("chiroptera", "wildlife-sounds-bats", "6bd8ad26-218d-4657-8549-c30d69356664")]
FLAGS = {"PRESUMED_NEGATED_LONGITUDE": "neg_lon", "PRESUMED_NEGATED_LATITUDE": "neg_lat", "PRESUMED_SWAPPED_COORDINATE": "swapped", "COUNTRY_COORDINATE_MISMATCH": "country_mismatch"}
BASIS = "recordist local date/time converted with published or inferred timezone; not GPS validation"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()


def fetch(grp, slug):
    D = f"{C}dwca/{grp}/"; z = D + slug + ".zip"
    if not os.path.exists(z):
        os.makedirs(D, exist_ok=True)
        with requests.get(DWCA + slug, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(z + ".part", "wb") as f:
                for b in r.iter_content(1 << 20): f.write(b)
        os.rename(z + ".part", z)
    if not os.path.exists(D + "x/Occurrence.txt"):
        zipfile.ZipFile(z).extractall(D + "x")
    if not os.path.exists(D + "occ.parquet"):
        # Some audio rows are typed as StillImage, so use the path to distinguish spectrograms.
        rd = lambda f: f"read_csv('{D}x/{f}', header=true, all_varchar=true, quote='\"', escape='\"', max_line_size=100000000, parallel=false)"
        con = duckdb.connect()
        con.execute(f"copy (select catalogNumber,scientificName,longitudeDecimal,latitudeDecimal,eventDate,eventTime,verbatimEventDate,behavior,id from {rd('Occurrence.txt')}) to '{D}occ.parquet' (format parquet)")
        con.execute(f"copy (select CoreId,Rating,creator,accessURI,description,resourceCreationTechnique,physicalSetting,license from {rd('Multimedia.txt')} where accessURI like '%/sounds/uploaded/%') to '{D}sound.parquet' (format parquet)")


def gbif_flags(grp, key):
    out = {}
    os.makedirs(C + "gbif_issues", exist_ok=True)
    for issue, tag in FLAGS.items():
        off = 0
        while True:
            f = f"{C}gbif_issues/{grp}_{issue}_{off}.json"
            if not os.path.exists(f):
                for k in range(6):
                    try:
                        d = requests.get(GBIF, params=dict(datasetKey=key, issue=issue, eventDate="2015-12-31,2026-12-31", limit=300, offset=off), timeout=180).json(); break
                    except Exception: time.sleep(10 * (k + 1))
                else: sys.exit("gbif failed " + f)
                json.dump(dict(end=d["endOfRecords"], cats=[r["catalogNumber"] for r in d["results"]]), open(f + ".part", "w")); os.rename(f + ".part", f); time.sleep(1)
            d = json.load(open(f))
            for c in d["cats"]: out.setdefault(c, []).append(tag)
            if d["end"]: break
            off += 300
    return out


def zone(tf, lat, lon):
    z = tf.timezone_at(lat=lat, lng=lon)
    if z and z.startswith("Etc/"):   # Sea zones have no DST, so use the nearest land zone within 1.6 degrees when available.
        for rad in (0.05, 0.1, 0.2, 0.4, 0.8, 1.2, 1.6):
            for t in np.linspace(0, 2 * np.pi, 16, endpoint=False):
                zl = tf.timezone_at_land(lat=float(np.clip(lat + rad * np.cos(t), -90, 90)), lng=(lon + rad * np.sin(t) + 180) % 360 - 180)
                if zl: return zl
    return z


def duration(s):
    return s[:-2] if isinstance(s, str) and s.endswith(" s") and s[:-2].isdigit() and int(s[:-2]) > 0 else ""   # The feed rounds to whole seconds and zero means unknown length.


def main():
    parts = []; flags = {}
    for grp, slug, key in GROUPS:
        fetch(grp, slug)
        for c, t in gbif_flags(grp, key).items(): flags.setdefault(c, []).extend(t)
        parts.append(duckdb.connect().execute(f"select '{grp}' as grp, o.*, s.* exclude (CoreId) from '{C}dwca/{grp}/occ.parquet' o join '{C}dwca/{grp}/sound.parquet' s on s.CoreId = o.id where o.eventDate >= '2015-12-31'").df())
    d = pd.concat(parts, ignore_index=True)
    if d.catalogNumber.duplicated().any(): sys.exit("catalogNumber repeats across groups: " + ",".join(d.catalogNumber[d.catalogNumber.duplicated()].head(5)))
    d = d.astype(object).where(d.notna(), None)
    d = d[~d.verbatimEventDate.str.startswith("0000", na=False)].reset_index(drop=True)   # date unknown at upload: eventDate is then a shared fallback date, not the recording date
    num = r"^-?\d+(\.\d+)?$"
    ok = d.latitudeDecimal.str.match(num, na=False) & d.longitudeDecimal.str.match(num, na=False)
    la, lo = pd.to_numeric(d.latitudeDecimal.where(ok)), pd.to_numeric(d.longitudeDecimal.where(ok))
    ok &= la.abs().le(90) & lo.abs().le(180) & ~(la.eq(0) & lo.eq(0))
    d["lat"], d["lon"] = d.latitudeDecimal.where(ok, ""), d.longitudeDecimal.where(ok, "")
    dec = lambda s: s.str.split(".").str[1].fillna("").str.len()
    d["dec"] = np.where(ok, np.minimum(dec(d.lat), dec(d.lon)), -1)
    tf = TimezoneFinder(); pts = d[ok][["lat", "lon"]].drop_duplicates()
    tz = {(a, b): zone(tf, float(a), float(b)) for a, b in zip(pts.lat, pts.lon)}
    zi = {}; rows = []
    for r in d.itertuples(index=False):
        has_t = isinstance(r.eventTime, str); z = tz.get((r.lat, r.lon)) if r.lat else None; utc = ""
        if has_t and z:
            x = dt.datetime.fromisoformat(f"{r.eventDate} {r.eventTime}").replace(tzinfo=zi.setdefault(z, ZoneInfo(z)))
            utc = x.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if (utc or r.eventDate) < "2016-01-01": continue
        n = r.catalogNumber[2:]; ex = {"grp": r.grp, "sp": r.scientificName, "q": int(r.Rating) if r.Rating else None, "type": r.behavior}
        if "automatic recording: yes" in (r.resourceCreationTechnique or ""): ex["auto"] = 1
        if r.catalogNumber in flags: ex["pin_flag"] = flags[r.catalogNumber]
        lic = r.license
        rows.append([r.catalogNumber, "xeno-canto", r.catalogNumber, f"https://xeno-canto.org/{n}", r.accessURI, r.creator, lic, "yes" if lic.startswith(("CC", "PDM")) else "no",
                     r.lat, r.lon, r.dec if r.lat else "", "recordist-published coordinates, copied without refinement" if r.lat else "no coordinates published (hidden or not given)",
                     "", "false" if r.lat else "true", f"{r.eventDate} {r.eventTime}" if has_t else r.eventDate, z if utc else "", utc, r.eventDate, duration(r.description),
                     "typed", BASIS, 60 if has_t else 86400, "download", "outdoor" if r.physicalSetting == "Natural" else "unknown",
                     json.dumps({k: v for k, v in ex.items() if v}, ensure_ascii=False, separators=(",", ":"))])
    rows.sort(key=lambda r: int(r[0][2:]))
    m = pd.DataFrame(rows, columns=COLS)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT + ".csv.gz.part", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(COLS); w.writerows(rows)
    os.rename(OUT + ".csv.gz.part", OUT + ".csv.gz")
    win = m.utc_start.where(m.utc_start != "", m.local_date) >= "2023-02-16"
    dur = pd.to_numeric(m.duration_s, errors="coerce")
    counts = dict(source="xeno-canto", built_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), rows=len(m), rows_with_utc_start=int((m.utc_start != "").sum()),
                  rows_from_2023_02_16=int(win.sum()), rows_from_2023_02_16_with_time_and_place=int((win & (m.utc_start != "") & (m.public_latitude != "")).sum()),
                  hours_from_2023_02_16=round(float(dur[win].sum()) / 3600, 1), distinct_places=int(m[m.public_latitude != ""][["public_latitude", "public_longitude"]].drop_duplicates().shape[0]),
                  distinct_days_from_2023_02_16=int(m.local_date[win].nunique()), obscured=int((m.obscured == "true").sum()), redistributable_yes=int((m.redistributable == "yes").sum()),
                  method="GET " + "; GET ".join(f"{DWCA}{slug} (Naturalis DwC-A of GBIF dataset {key}, group {grp}; Occurrence.txt + Multimedia.txt)" for grp, slug, key in GROUPS)
                  + f"; per dataset GET {GBIF}?datasetKey=KEY&eventDate=2015-12-31,2026-12-31&limit=300&issue=" + "|".join(FLAGS) + " (pin flags only)")
    json.dump(counts, open(OUT + ".counts.json", "w"), indent=1)
    print(dict(zip(COLS, rows[len(rows) // 2])))


main()
