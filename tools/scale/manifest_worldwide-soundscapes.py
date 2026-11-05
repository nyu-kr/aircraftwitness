#!/usr/bin/env python3
import os, re, sys, csv, gzip, json, hashlib, datetime, requests, duckdb
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/worldwide-soundscapes"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
REC = "https://zenodo.org/api/records/14216871"
FILES = {"recordings.csv": "1a58011ce1c41a089410a25ac9b5fe0b", "sites.csv": "7acbd845bbbc344b843e04ca1dc6e03a", "collections.csv": "f31dc6525c5c76522930e413e81e766c"}
HDR = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
LICENCE = "not stated for audio (meta-database tables are CC BY 4.0; audio licence is not given per collection)"
TIME_BASIS = ("recordings.csv file_date + file_time: 'local time of the recording start' (Zenodo 14216871 description, recordings table); zone = IANA zone at the site coordinate (timezonefinder), not stated by the source; "
              "recorder clock source and drift not stated; rows of duty-cycled or scheduled deployments are the start of the recording window, not of a single file (duration_s is the window; extra.dc = minutes recorded/period)")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def fetch(name, want):
    d = f"{RAW}/dl"; os.makedirs(d, exist_ok=True); p = f"{d}/{name}"
    if os.path.exists(p) and md5(p) == want: return p
    part = p + ".part"
    for _ in range(6):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        with requests.get(f"{REC}/files/{name}/content", headers=dict(HDR, Range=f"bytes={have}-") if have else HDR, stream=True, timeout=120) as r:
            if r.status_code == 416: break
            r.raise_for_status()
            with open(part, "ab" if r.status_code == 206 else "wb") as f:
                for b in r.iter_content(1 << 20): f.write(b)
        if md5(part) == want: break
    else: raise RuntimeError(f"{name}: md5 never matched {want}")
    os.rename(part, p); return p


def precision(comment):
    # Use half the latitude rounding step or the stated random blur standard deviation.
    m = re.search(r"round\w* (?:the value |the coordinates )?to (?:the nearest )?(\d|one|two|three|four) (?:decimal )?(?:digits|places)", comment)
    if m: d = WORDS.get(m.group(1)) or int(m.group(1)); return round(0.5 * 10 ** -d * 111320)
    m = re.search(r"standard deviation of ([0-9]+(?:\.[0-9]+)?)", comment)
    return round(float(m.group(1)) * 111320) if m else ""


def main():
    P = {n: fetch(n, w) for n, w in FILES.items()}; os.makedirs(OUT, exist_ok=True)
    c = duckdb.connect()
    rd = lambda n: f"read_csv('{P[n]}', header=true, all_varchar=true, sample_size=-1)"
    sites = {r[0]: r for r in c.execute(f"select site_ID, site_name, trim(latitude_numeric), trim(longitude_numeric), blurred_coordinates, realm, coalesce(contributor_comments,'') from {rd('sites.csv')}").fetchall()}
    cols = {r[0]: r for r in c.execute(f"select collection_ID, name, \"ecoSound-web_link\", primary_contributors, URL_open_recordings from {rd('collections.csv')}").fetchall()}
    tf = TimezoneFinder(); zones = {}
    for k, s in sites.items():
        z = tf.timezone_at(lat=float(s[2]), lng=float(s[3]))
        if z: zones[k] = (z, ZoneInfo(z))
    cur = c.execute(f"select recording_id, col_id, site_id, file_date, file_time, duration, duty_cycle_recording, duty_cycle_period, lower(medium) from {rd('recordings.csv')} where file_date >= '2016-01-01' order by try_cast(recording_id as bigint)")
    tmp = f"{OUT}/worldwide-soundscapes.csv.gz.tmp"; dropped = {"site not in sites.csv": 0, "no time zone at the site coordinate": 0, "before 2016-01-01": 0}
    fixed = {"duration blank (missing, zero or over 30 days)": 0, "duty cycle ignored (0/0 or recording > period)": 0}; n = nwin = obsc = 0; hours = 0.0; places = set(); days = set()
    dec = lambda s: len(s.split(".")[1]) if "." in s else 0
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(COLS)
        while rows := cur.fetchmany(50000):
            for rid, cid, sid, d, tm, dur, dcr, dcp, med in rows:
                if sid not in sites: dropped["site not in sites.csv"] += 1; continue
                if sid not in zones: dropped["no time zone at the site coordinate"] += 1; continue
                s = sites[sid]; co = cols[cid]; tzn, zi = zones[sid]
                t = datetime.datetime.strptime(f"{d} {tm}", "%Y-%m-%d %H:%M:%S")
                u = t.replace(tzinfo=zi).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                blur = s[4] == "TRUE"; com = " ".join(s[6].split())
                pb = "site coordinate in sites.csv (blurred_coordinates FALSE)" if not blur else "site coordinate in sites.csv (blurred_coordinates TRUE): " + (com[:160] or "method not stated")
                realm = s[5]; env = "marine" if med == "water" and "Marine" in realm else "outdoor" if med == "air" and "Terrestrial" in realm else "unknown"
                openu = " ".join((co[4] or "").split()); access = "none" if openu in ("none", "") else "request"
                ex = {"col": cid, "site": s[1]}
                dv = float(dur) if dur not in ("NULL", "") else 0
                if not 0 < dv < 30 * 86400: dv = 0; fixed["duration blank (missing, zero or over 30 days)"] += 1
                dcv = float(dcr) / float(dcp) if dcr not in ("NULL", "") and dcp not in ("NULL", "") and 0 < float(dcr) <= float(dcp) else 0
                if dcv: ex["dc"] = f"{dcr}/{dcp}"
                elif dcr not in ("NULL", ""): fixed["duty cycle ignored (0/0 or recording > period)"] += 1
                w.writerow([f"WWS{rid}", "worldwide-soundscapes", rid, " ".join((co[2] or "").split()), "" if access == "none" else openu, " ".join(co[3].split()), LICENCE, "no", s[2], s[3],
                            min(dec(s[2]), dec(s[3])), pb, precision(com) if blur else "", "true" if blur else "false", f"{d} {tm}", tzn, u, d, dur if dv else "", "unknown", TIME_BASIS, 1, access, env,
                            json.dumps(ex, separators=(",", ":"), ensure_ascii=False)])
                n += 1; obsc += blur; places.add((s[2], s[3]))
                if u >= "2023-02-16":
                    nwin += 1; days.add(u[:10])
                    hours += dv / 3600 * (dcv or 1)
    os.rename(tmp, f"{OUT}/worldwide-soundscapes.csv.gz")
    dropped["before 2016-01-01"] = c.execute(f"select count(*) from {rd('recordings.csv')} where file_date < '2016-01-01'").fetchone()[0]
    counts = {"source": "worldwide-soundscapes", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": n, "rows_with_utc_start": n,
              "rows_from_2023_02_16": nwin, "rows_from_2023_02_16_with_time_and_place": nwin, "hours_from_2023_02_16": round(hours, 1), "distinct_places": len(places),
              "distinct_days_from_2023_02_16": len(days), "obscured": obsc, "redistributable_yes": 0, "dropped": dropped, "kept_with_field_blanked": fixed,
              "method": f"GET {REC}/files/recordings.csv/content (128 MB, md5 1a58011c...), sites.csv, collections.csv (Zenodo 14216871 v4.0.1, direct, no login); recordings.csv rows with file_date >= 2016-01-01 joined on site_id and col_id. "
                        "hours_from_2023_02_16 = duration x duty_cycle_recording/duty_cycle_period where a duty cycle is given (audio actually recorded), else duration. No audio fetched."}
    json.dump(counts, open(f"{OUT}/worldwide-soundscapes.counts.json", "w"), indent=1)
    print(c.execute(f"select * from read_csv('{OUT}/worldwide-soundscapes.csv.gz', header=true, all_varchar=true) where utc_start >= '2023-02-16' limit 1").fetchdf().iloc[0].to_dict())


if __name__ == "__main__": main()
