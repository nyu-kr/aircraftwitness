#!/usr/bin/env python3
import os, re, sys, csv, gzip, json, time, datetime, requests
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/bioscape"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json?collection_concept_id=C3366080352-ORNL_CLOUD&page_size=2000&page_num="
HDR = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
GUIDE = "https://daac.ornl.gov/BIOSCAPE/guides/Acoustic_Data_Cape_Floristic.html"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
CREDIT = "Clark, M., L. Salas, R. Snyder, A. Lee, A.A. Turner, C. Seymour, J. Measey, A. Ferraz, F.D. Schneider, and F.O. Adegbola. 2025. BioSCape: BioSoundSCape Acoustic Recordings, South Africa, 2023. ORNL DAAC"
LICENCE = "NASA ORNL DAAC data use policy (no Creative Commons licence named)"
POS_BASIS = "CMR granule spatial box, a 0.0001 degree cell around the recorder point (guide: 'sounds within 50 m of audio recorder locations'); south-west corner of the box printed"
TIME_BASIS = (f"User guide ({GUIDE}): file name time 'was in 24-hr format based on local South Africa time (GMT + 2)', example 15:40 = UTC 13:40; CMR time_start labels the same clock as Z, which is wrong. "
              "Paper (Sci Data, doi:10.1038/s41597-025-05685-3): recorders synchronised with the AudioMoth Chime smartphone app; clock then free-runs 4-10 days")
NAME = re.compile(r"^Acoustic_Data_Cape_Floristic\.((s2lam\d+)_(\d{6})_(\d{4}-\d\d-\d\d)_(\d\d)-(\d\d))\.WAV$")


def page(n):
    p = f"{RAW}/pages/{n:03d}.json.gz"
    if not os.path.exists(p):
        for t in range(4):
            r = requests.get(CMR + str(n), headers=HDR, timeout=180)
            if r.status_code == 200: break
            time.sleep(5 * (t + 1))
        else: r.raise_for_status()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with gzip.open(p + ".part", "wb") as f: f.write(json.dumps({"hits": r.headers["CMR-Hits"], "entry": r.json()["feed"]["entry"]}).encode())
        os.rename(p + ".part", p); time.sleep(0.5)
    return json.load(gzip.open(p))


def main():
    os.makedirs(OUT, exist_ok=True)
    first = page(1); hits = int(first["hits"]); npages = -(-hits // 2000)
    tz = TimezoneFinder().timezone_at; out = []; ids = set(); other = []
    for n in range(1, npages + 1):
        for e in (first if n == 1 else page(n))["entry"]:
            m = NAME.match(e["title"])
            if not m: other.append(e["title"]); continue
            stem, dev, begin, d, hh, mm = m.groups()
            s, w, nn, ee = e["boxes"][0].split()
            if tz(lat=float(s), lng=float(w)) != "Africa/Johannesburg": sys.exit(f"{stem}: not in Africa/Johannesburg: {s} {w}")
            t = datetime.datetime.strptime(f"{d} {hh}:{mm}", "%Y-%m-%d %H:%M")
            u = t.replace(tzinfo=ZoneInfo("Africa/Johannesburg")).astimezone(datetime.timezone.utc)
            if e["time_start"][:16] != t.strftime("%Y-%m-%dT%H:%M"): sys.exit(f"{stem}: CMR time_start {e['time_start']} differs from the file name")
            rid = "BSC_" + stem
            if rid in ids: sys.exit(f"duplicate {rid}")
            ids.add(rid)
            href = next(l["href"] for l in e["links"] if l["rel"].endswith("/data#") and l["href"].endswith(".WAV"))
            out.append([rid, "bioscape", e["title"].split(".", 1)[1], GUIDE, href, CREDIT, LICENCE, "no", s, w, min(len(s.split(".")[1]), len(w.split(".")[1])), POS_BASIS, 15, "false",
                        f"{d}_{hh}-{mm}", "Africa/Johannesburg", u.strftime("%Y-%m-%dT%H:%M:%SZ"), d, 60, "device", TIME_BASIS, 60, "request", "outdoor",
                        json.dumps({"dep": f"{dev}_{begin}"}, separators=(",", ":"))])
    if len(out) + len(other) != hits: sys.exit(f"{len(out)} wav + {len(other)} other != CMR hits {hits}")
    tmp = f"{OUT}/bioscape.csv.gz.tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(COLS); w.writerows(out)
    os.rename(tmp, f"{OUT}/bioscape.csv.gz")
    counts = {"source": "bioscape", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": len(out), "rows_with_utc_start": len(out),
              "rows_from_2023_02_16": len(out), "rows_from_2023_02_16_with_time_and_place": len(out), "hours_from_2023_02_16": round(len(out) / 60, 1),
              "distinct_places": len({(r[8], r[9]) for r in out}), "distinct_days_from_2023_02_16": len({r[16][:10] for r in out}), "obscured": 0, "redistributable_yes": 0,
              "first_utc_start": min(r[16] for r in out), "last_utc_start": max(r[16] for r in out), "cmr_hits": hits, "non_wav_granules_skipped": other,
              "method": f"GET {CMR}N for N=1..{npages} (NASA CMR granules.json, collection C3366080352-ORNL_CLOUD, public, no login): title -> file name, time_start check, boxes -> position, links -> protected audio URL. "
                        "No audio fetched; audio needs Earthdata Login. duration_s 60 from time_end-time_start (granule_size 5.76 MB = 60 s of 48 kHz 16-bit mono)."}
    json.dump(counts, open(f"{OUT}/bioscape.counts.json", "w"), indent=1)
    print(dict(zip(COLS, out[0])))


if __name__ == "__main__": main()
