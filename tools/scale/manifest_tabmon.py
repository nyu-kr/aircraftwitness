#!/usr/bin/env python3
import os, sys, json, hashlib, datetime, requests, duckdb

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/tabmon"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
REC = "https://zenodo.org/api/records/21103360"
FILES = {"files_index.parquet": "98cfa7909a162d67aa112cb094d19b57", "Deployment_metadata.ZIP": "5aa00c7354144688a77848cbab5c00b6"}
HDR = {"User-Agent": "aircraftwitness-dataset/1.0 (+https://github.com/nyu-kr/aircraftwitness)"}
TIME_BASIS = ("Filename is the UTC start of the 5-minute file (Zenodo 21103360 README: 'The filename contains the date and start time of the 5-minute recording file in UTC'); "
              "Bugg clock is set from NTP (ntp.ubuntu.com) at every upload (https://docs.bugg.xyz/time/)")
POS_BASIS = "deployment coordinate, rounded to 3 decimals by the data authors for public release (Deployment_metadata README)"
LICENCE = "not stated for audio (audio not in the record; record metadata CC BY 4.0)"


def sq(s): return s.replace("'", "''")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def fetch(name, want):
    d = f"{RAW}/dl"; os.makedirs(d, exist_ok=True); p = f"{d}/{name}"
    if os.path.exists(p) and md5(p) == want: return p
    part = p + ".part"
    for _ in range(5):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        h = dict(HDR, Range=f"bytes={have}-") if have else HDR
        with requests.get(f"{REC}/files/{name}/content", headers=h, stream=True, timeout=120) as r:
            if r.status_code == 416: break
            r.raise_for_status()
            with open(part, "ab" if r.status_code == 206 else "wb") as f:
                for b in r.iter_content(1 << 20): f.write(b)
        if md5(part) == want: break
    else:
        raise RuntimeError(f"{name}: md5 never matched {want}")
    os.rename(part, p); return p


def main():
    idx = fetch("files_index.parquet", FILES["files_index.parquet"])
    zp = fetch("Deployment_metadata.ZIP", FILES["Deployment_metadata.ZIP"])
    dm = f"{RAW}/dl/dm"; os.makedirs(dm, exist_ok=True)
    import zipfile
    with zipfile.ZipFile(zp) as z:
        with open(f"{dm}/site_info_public.csv", "wb") as f: f.write(z.read("site_info_public.csv"))
    os.makedirs(OUT, exist_ok=True)
    c = duckdb.connect(); c.execute(f"set memory_limit='10GB'; set threads=8; set temp_directory='{RAW}/tmp'")
    c.execute(f"""create view dm as select *,
        strptime(deploymentBeginDate||' '||deploymentBeginTime,'%d/%m/%Y %H:%M:%S') b,
        try_strptime(nullif(deploymentEndDate,'')||' '||nullif(deploymentEndTime,''),'%d/%m/%Y %H:%M:%S') e
        from read_csv('{dm}/site_info_public.csv', header=true, all_varchar=true)""")
    stem = "regexp_replace(replace(replace(Name,'_',':'),'.mp3',''),'Z$','')"
    c.execute(f"""create view f as select deployment_id d, Name n, replace(Name,'.mp3','') stem,
        coalesce(try_strptime({stem},'%Y-%m-%dT%H:%M:%S.%g'), try_strptime({stem},'%Y-%m-%dT%H:%M')) t,
        Name like '%.___Z.mp3' ms from read_parquet('{idx}')""")
    tot, nodep, nots, before, after = c.execute("""select count(*), count(*) filter (where DeploymentID is null), count(*) filter (where t is null),
        count(*) filter (where t<b), count(*) filter (where e is not null and t>e) from f left join dm on d=DeploymentID""").fetchone()
    if nodep or nots: sys.exit(f"index rows without deployment ({nodep}) or unparsable name ({nots})")
    keep = "f join dm on d=DeploymentID where t>=b and (e is null or t<=e)"
    want = c.execute(f"select count(*) from {keep}").fetchone()[0]
    frac = lambda x: f"length(split_part(trim({x}),'.',2))"
    tmp = f"{OUT}/tabmon.csv.gz.tmp"
    c.execute(f"""copy (select
        'TABMON_'||replace(d,' ','')||'_'||replace(replace(replace(replace(stem,'-',''),'_',''),':',''),'.','') recording_id,
        'tabmon' source, d||'/'||n source_recording_id, 'https://zenodo.org/records/21103360' source_url, null audio_url,
        'TABMON project' credit, '{sq(LICENCE)}' audio_licence, 'no' redistributable,
        trim(Latitude) public_latitude, trim(Longitude) public_longitude, least({frac('Latitude')},{frac('Longitude')}) place_decimals,
        '{sq(POS_BASIS)}' position_basis, 100 position_precision_m, 'true' obscured,
        n local_start, null tz, strftime(date_trunc('second',t),'%Y-%m-%dT%H:%M:%SZ') utc_start, strftime(t,'%Y-%m-%d') local_date,
        300 duration_s, 'ntp' time_grade, '{sq(TIME_BASIS)}' time_basis, case when ms then 1 else 60 end time_precision_s,
        'request' audio_access, 'outdoor' environment, '{{"dep":"'||d||'","hab":"'||Habitat||'"}}' extra
        from {keep} order by d, t) to '{tmp}' (format csv, header, compression gzip)""")
    r = c.execute(f"""select count(*), count(utc_start), count(*) filter (where utc_start>='2023-02-16'),
        count(*) filter (where utc_start>='2023-02-16' and public_latitude is not null and public_longitude is not null),
        sum(duration_s::double) filter (where utc_start>='2023-02-16')/3600.0, count(distinct (public_latitude, public_longitude)),
        count(distinct left(utc_start,10)) filter (where utc_start>='2023-02-16'), count(*) filter (where obscured='true'),
        count(*) filter (where redistributable='yes'), count(duration_s), min(utc_start), max(utc_start),
        count(duration_s) filter (where utc_start>='2023-02-16')
        from read_csv('{tmp}', header=true, all_varchar=true, compression='gzip')""").fetchone()
    if r[0] != want: sys.exit(f"wrote {r[0]} rows, expected {want}")
    os.rename(tmp, f"{OUT}/tabmon.csv.gz")
    counts = {"source": "tabmon", "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rows": r[0], "rows_with_utc_start": r[1], "rows_from_2023_02_16": r[2], "rows_from_2023_02_16_with_time_and_place": r[3],
              "rows_from_2023_02_16_with_duration": r[12], "hours_from_2023_02_16": round(r[4], 1), "distinct_places": r[5],
              "distinct_days_from_2023_02_16": r[6], "obscured": r[7], "redistributable_yes": r[8], "rows_with_duration": r[9],
              "first_utc_start": r[10], "last_utc_start": r[11], "index_rows": tot,
              "dropped": {"file start before its deployment begin (device not yet at the published position)": before,
                          "file start after its deployment end (device removed)": after},
              "method": f"GET {REC}/files/files_index.parquet/content (75 MB, md5 98cfa790...); GET {REC}/files/Deployment_metadata.ZIP/content (8 KB, site_info_public.csv); "
                        "join index Name/deployment_id to DeploymentID, keep files inside the deployment window [begin, end]; no audio fetched. "
                        "duration_s 300 is nominal (preprint and README: 5-minute files). distinct_places = distinct printed lat/lon pairs."}
    with open(f"{OUT}/tabmon.counts.json", "w") as f: json.dump(counts, f, indent=1)
    print(c.execute(f"select * from read_csv('{OUT}/tabmon.csv.gz', header=true, all_varchar=true) limit 1").fetchdf().iloc[0].to_dict())


if __name__ == "__main__": main()
