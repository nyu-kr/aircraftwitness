#!/usr/bin/env python3
import csv, datetime, glob, gzip, hashlib, html, json, os, re, struct, sys, time, urllib.parse, zlib
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/eclipse-soundscapes"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
API = "https://zenodo.org/api/records"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
WAV_HEADER = 488  # An AudioMoth 1.8.x header with a LIST chunk has this size.
S = requests.Session(); S.headers["User-Agent"] = "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"
last = [0.0]


def get(url, delay, **kw):
    for attempt in range(8):
        wait = last[0] + delay - time.time()
        if wait > 0: time.sleep(wait)
        r = S.get(url, timeout=90, **kw); last[0] = time.time()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(int(r.headers.get("retry-after", 20)) + 2 * attempt, 90)); continue
        r.raise_for_status(); return r
    raise RuntimeError(f"giving up on {url}")


def cached(path, fn):
    if os.path.exists(path): return open(path, "rb").read()
    data = fn(); os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path + ".tmp", "wb").write(data); os.replace(path + ".tmp", path); return data


def list_records():
    recs, p = [], 1
    while True:
        raw = cached(f"{RAW}/page{p:02d}.json", lambda: get(f"{API}?communities=eclipsesoundscapes&size=25&page={p}&sort=oldest", 3).content)
        hits = json.loads(raw)["hits"]["hits"]
        if not hits: return recs
        recs += hits; p += 1


def rng(url, spec):
    return get(url, 0.8, headers={"Range": f"bytes={spec}"}).content


def central_directory(url, size):
    n = min(size, 131072); tail = rng(url, f"{size - n}-{size - 1}"); base = size - n
    e = tail.rfind(b"PK\x05\x06")
    _, _, _, cnt, total, cdsize, cdoff, _ = struct.unpack("<4s4H2LH", tail[e:e + 22])
    if 0xFFFF in (cnt, total) or 0xFFFFFFFF in (cdsize, cdoff):
        l = tail.rfind(b"PK\x06\x07", 0, e); z = struct.unpack("<4sLQL", tail[l:l + 20])[2]
        z64 = z - base
        z64 = tail[z64:z64 + 56] if z64 >= 0 else rng(url, f"{z}-{z + 55}")
        assert z64[:4] == b"PK\x06\x06"
        total, cdsize, cdoff = struct.unpack("<QQQ", z64[32:56])
    cd = tail[cdoff - base:cdoff - base + cdsize] if cdoff >= base else rng(url, f"{cdoff}-{cdoff + cdsize - 1}")
    out, p = [], 0
    while p < len(cd):
        h = struct.unpack("<4s6H3L5H2L", cd[p:p + 46]); assert h[0] == b"PK\x01\x02", "bad central directory"
        flag, method, csz, usz, nl, el, cl, off = h[3], h[4], h[8], h[9], h[10], h[11], h[12], h[16]
        name = cd[p + 46:p + 46 + nl].decode("utf-8" if flag & 0x800 else "cp437")
        ex = cd[p + 46 + nl:p + 46 + nl + el]; q = 0
        while q + 4 <= len(ex):
            i, sz = struct.unpack("<HH", ex[q:q + 4])
            if i == 1:
                v = list(struct.unpack(f"<{sz // 8}Q", ex[q + 4:q + 4 + sz // 8 * 8]))
                if usz == 0xFFFFFFFF: usz = v.pop(0)
                if csz == 0xFFFFFFFF: csz = v.pop(0)
                if off == 0xFFFFFFFF: off = v.pop(0)
            q += 4 + sz
        out.append([name, usz, csz, off, method]); p += 46 + nl + el + cl
    assert len(out) == total, f"central directory has {len(out)} of {total} entries"
    return out


def read_member(url, size, m):
    name, usz, csz, off, method = m
    buf = rng(url, f"{off}-{min(size, off + 30 + len(name.encode()) + 512 + csz) - 1}")
    nl, el = struct.unpack("<HH", buf[26:30]); data = buf[30 + nl + el:30 + nl + el + csz]
    return (zlib.decompress(data, -15) if method == 8 else data).decode("utf-8", "replace")


def zip_listing(rec, f):
    url = f["links"]["self"]
    def build():
        mem = central_directory(url, f["size"])
        cfg = next((m for m in mem if m[0].upper().endswith("/CONFIG.TXT") or m[0].upper() == "CONFIG.TXT"), None)
        return json.dumps({"members": mem, "config": read_member(url, f["size"], cfg) if cfg and cfg[1] < 65536 else None}).encode()
    return json.loads(cached(f"{RAW}/zip/{rec['id']}_{hashlib.md5(f['key'].encode()).hexdigest()[:10]}.json", build))


def cfg_field(cfg, name):
    m = re.search(rf"^{re.escape(name)}\s*:\s*(.*?)\s*$", cfg or "", re.M)
    return m.group(1) if m else None


def site(rec):
    md = rec["metadata"]; m = re.search(r"ESID\s*#\s*(\d+)", md["title"])
    if not m or "Solar Eclipse" not in md["title"]: return None
    d = html.unescape(re.sub(r"<[^>]+>", " ", md["description"])).replace("\xa0", " ")
    la = re.search(r"Latitude:\s*(-?\d+(?:\.\d+)?)", d); lo = re.search(r"Longitude:\s*(-?\d+(?:\.\d+)?)", d)
    ts = re.search(r"Time & Date Settings:\s*(.+?)\s*(?:\(More information|Data Collector Start|Included Data)", d)
    notes = re.search(r"Data Collector Start Time Notes:\s*(.+?)\s*Included Data", d)
    return dict(esid=m.group(1), lat=la.group(1), lon=lo.group(1), setting=ts.group(1) if ts else "",
                notes=notes.group(1) if notes else "", credit="; ".join(c["name"] for c in md["creators"]))


def dec(s): return len(s.split(".")[1]) if "." in s else 0


def main():
    recs = list_records(); best, skipped = {}, {}
    sites = 0
    def skip(why): skipped[why] = skipped.get(why, 0) + 1
    for rec in recs:
        s = site(rec)
        if not s: continue
        sites += 1; files = rec["files"]; sr = None; cfgtxt = None
        wavs = [(f["links"]["self"], None, f["key"], f["size"]) for f in files if f["key"].upper().endswith(".WAV")]
        for f in files:
            if f["key"].upper() == "CONFIG.TXT" and cfgtxt is None:
                cfgtxt = cached(f"{RAW}/cfg/{rec['id']}.txt", lambda: rng(f["links"]["self"], f"0-{f['size'] - 1}")).decode("utf-8", "replace")
        for f in files:
            if f["key"].lower().endswith(".zip"):
                z = zip_listing(rec, f); cfgtxt = cfgtxt or z["config"]
                wavs += [(f["links"]["self"], f["key"], m[0], m[1]) for m in z["members"] if m[0].upper().endswith(".WAV")]
        sr = int(cfg_field(cfgtxt, "Sample rate (Hz)") or 0)
        chime = "chime" in s["setting"].lower()
        for url, zipkey, member, size in wavs:
            m = re.fullmatch(r"(\d{8})_(\d{6})T?\.WAV", os.path.basename(member), re.I)
            if not m: skip("not a recording name (macOS ._ resource forks and similar)"); continue
            try: t = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            except ValueError: skip("name not a date"); continue
            if t.year < 2016: skip("clock reset (file name before 2016)"); continue
            if size <= WAV_HEADER: skip("empty or header-only member"); continue
            esid = s["esid"]; stamp = f"{m.group(1)}_{m.group(2)}"; rid = f"ESS{esid}_{stamp}"
            extra = {"esid": esid, "zip": zipkey, "member": member, "bytes": size}
            dur = round((size - WAV_HEADER) / (2 * sr), 1) if sr else ""
            grade = "device" if chime else "unknown"
            basis = ("AudioMoth clock set from the acoustic time chime before deployment, free-running, no GPS (CONFIG: Enable GPS time setting No); "
                     "file name is UTC; Zenodo record description " + rec["links"]["self_html"]) if chime else \
                    ("record says the clock needs to be set manually; file name stamp is the unset device clock, true start only via collector notes (" + s["notes"][:120] + "); " + rec["links"]["self_html"])
            row = ([rid, "eclipse-soundscapes", f"{esid}_{stamp}", rec["links"]["self_html"], url, s["credit"], "CC BY 4.0", "yes",
                         s["lat"], s["lon"], min(dec(s["lat"]), dec(s["lon"])),
                         "data collector self-reported site latitude/longitude (decimal degrees, at least 5 decimals requested) in the Zenodo record description; ES ID replaces the collector's name",
                         "", "false", stamp, "", t.strftime("%Y-%m-%dT%H:%M:%SZ"), t.strftime("%Y-%m-%d"), dur, grade, basis, 1,
                         "download", "unknown", json.dumps(extra, separators=(",", ":"))])
            old = best.get(rid)
            if old: skip("same site and start in two records: kept the newer record" + ("" if old[1][8:10] == row[8:10] else " (positions differ)"))
            if not old or rec["created"] > old[0]: best[rid] = (rec["created"], row)
    rows = [v[1] for v in best.values()]
    rows.sort(key=lambda r: (r[0].split("_")[0], r[16]))
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/eclipse-soundscapes.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [r for r in rows if r[16] >= "2023-02-16"]
    c = dict(source="eclipse-soundscapes", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(r[16]) for r in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(r[16] and r[8] and r[9]) for r in win),
             hours_from_2023_02_16=round(sum(float(r[18] or 0) for r in win) / 3600, 1), distinct_places=len({(r[8], r[9]) for r in rows}),
             distinct_days_from_2023_02_16=len({r[17] for r in win}), obscured=0, redistributable_yes=len(rows),
             method=f"GET {API}?communities=eclipsesoundscapes&size=25&page=N&sort=oldest (records + file lists); HTTP Range on https://zenodo.org/api/records/ID/files/KEY/content: last 128 KB, central directory, CONFIG.TXT member only",
             sites=sites, skipped_members=skipped)
    json.dump(c, open(f"{OUT}/eclipse-soundscapes.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
