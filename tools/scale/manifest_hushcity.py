#!/usr/bin/env python3
import csv, datetime, gzip, json, os, zoneinfo
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/hushcity"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
API = "https://hushcity2.edge-works.net/api2/group_area?lang=en"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def main():
    path = f"{RAW}/areas.json"
    if not os.path.exists(path):
        r = requests.get(API, timeout=120, headers={"User-Agent": "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"}); r.raise_for_status()
        assert r.json()["data"]["survey"], "empty survey list"
        os.makedirs(RAW, exist_ok=True); open(path + ".tmp", "wb").write(r.content); os.replace(path + ".tmp", path)
    areas = [a for s in json.load(open(path), parse_float=str)["data"]["survey"] for a in s["areas"]]  # parse_float=str keeps the printed digits
    rows = []
    for a in areas:
        lat, lon = a["latitude"], a["longitude"]; t = datetime.datetime.fromtimestamp(float(a["time"]), datetime.timezone.utc)
        local = datetime.datetime.fromisoformat(a["formatted_time"])
        assert abs((local - t).total_seconds()) < 1, a["id"]
        stale = t.astimezone(zoneinfo.ZoneInfo(a["timezone"])).utcoffset() != local.utcoffset()
        extra = {"city": a["city"], "country": a["country"], "ext": a["audio"].rsplit(".", 1)[-1], **({"fmt_offset_stale": 1} if stale else {})}
        rows.append([f"HUSH{a['id']}", "hushcity", str(a["id"]), f"https://map.opensourcesoundscapes.org/area-detail/{a['id']}", a["audio"],
                     "Hush City (Open Source Soundscapes), anonymous contributor", "none stated", "no", lat, lon, min(dec(lat), dec(lon)),
                     "phone GPS fix at the survey spot, API latitude/longitude with its own accuracy field; street address in the API not kept", f"{float(a['accuracy']):.1f}", "false",
                     a["formatted_time"], a["timezone"], t.strftime("%Y-%m-%dT%H:%M:%SZ"), a["formatted_time"][:10], "", "phone",
                     "API field time (epoch seconds, equal to formatted_time); app timestamp per area (areas of one survey differ by minutes, so capture not one submit), "
                     "phone versus server clock and any NTP sync not stated; formatted_time offset label is stale for Brazil/Mexico DST since 2019/2022 but names the same instant",
                     1, "download", "outdoor", json.dumps(extra, separators=(",", ":"))])
    rows.sort(key=lambda r: r[16])
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/hushcity.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [r for r in rows if r[16] >= "2023-02-16"]
    c = dict(source="hushcity", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(r[16]) for r in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(r[16] and r[8] and r[9]) for r in win),
             hours_from_2023_02_16=0.0, distinct_places=len({(r[8], r[9]) for r in rows}),
             distinct_days_from_2023_02_16=len({r[17] for r in win}), obscured=0, redistributable_yes=0,
             method=f"GET {API} (one call, data.survey[].areas[]); duration not published (page: up to 30 s), so hours_from_2023_02_16 is 0 here",
             stale_offset_label_rows=sum('"fmt_offset_stale"' in r[-1] for r in rows))
    json.dump(c, open(f"{OUT}/hushcity.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
