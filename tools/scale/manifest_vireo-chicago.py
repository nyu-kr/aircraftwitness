#!/usr/bin/env python3
import csv, datetime, gzip, json, os, re, struct, time, zlib
import requests

RAW = os.environ.get("V2RAW", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../v2raw")) + "/vireo-chicago"
OUT = os.environ.get("V2MAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../data/v2/manifests"))
REC = "https://zenodo.org/api/records/21246876"
COLS = "recording_id source source_recording_id source_url audio_url credit audio_licence redistributable public_latitude public_longitude place_decimals position_basis position_precision_m obscured local_start tz utc_start local_date duration_s time_grade time_basis time_precision_s audio_access environment extra".split()
LOC = {"blackwell": ("Blackwell", "BW"), "gv": ("Greene Valley", "GV"), "whalon": ("Whalon Lake", "WL")}
S = requests.Session(); S.headers["User-Agent"] = "aircraftwitness-manifest (+https://github.com/nyu-kr/aircraftwitness)"
last = [0.0]


def get(url, **kw):
    for attempt in range(8):
        wait = last[0] + 1.0 - time.time()
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


def rng(url, spec): return get(url, headers={"Range": f"bytes={spec}"}).content


def central_directory(url, size):
    n = min(size, 1 << 21); tail = rng(url, f"{size - n}-{size - 1}"); base = size - n
    e = tail.rfind(b"PK\x05\x06")
    _, _, _, cnt, total, cdsize, cdoff, _ = struct.unpack("<4s4H2LH", tail[e:e + 22])
    if 0xFFFF in (cnt, total) or 0xFFFFFFFF in (cdsize, cdoff):
        l = tail.rfind(b"PK\x06\x07", 0, e); z = struct.unpack("<4sLQL", tail[l:l + 20])[2]
        z64 = tail[z - base:z - base + 56]; assert z64[:4] == b"PK\x06\x06"
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


def wav_seconds(url, m):
    name, usz, csz, off, method = m
    buf = rng(url, f"{off}-{off + 30 + len(name.encode()) + 2200}")
    nl, el = struct.unpack("<HH", buf[26:30]); data = buf[30 + nl + el:]
    h = zlib.decompressobj(-15).decompress(data, 2048) if method == 8 else data[:2048]
    assert h[:4] == b"RIFF" and h[8:12] == b"WAVE", "not a WAV"
    _, ch, rate, _, _, bits = struct.unpack("<HHIIHH", h[20:36])
    j = h.find(b"data", 36); assert j > 0
    return round(struct.unpack("<I", h[j + 4:j + 8])[0] / (rate * ch * bits // 8), 1)


def readme_sites(text):
    sites, loc = {}, None
    for line in text.splitlines():
        if line.strip() in ("Blackwell", "Greene Valley", "Whalon Lake"): loc = line.strip(); continue
        m = re.fullmatch(r"(\d):\s*(-?\d+\.\d+),\s*(-?\d+\.\d+)", line.strip())
        if m and loc: sites[(loc, int(m[1]))] = (m[2], m[3])
    assert len(sites) == 18, f"README gave {len(sites)} sites"
    return sites


def dec(s): return len(s.split(".")[1])


def main():
    rec = json.loads(cached(f"{RAW}/record.json", lambda: get(REC).content))
    md = rec["metadata"]; credit = md["creators"][0]["name"] + " (Vireo Initiative)"
    zf = next(f for f in rec["files"] if f["key"].endswith(".zip")); rf = next(f for f in rec["files"] if f["key"] == "README.md")
    url, size = zf["links"]["self"], zf["size"]
    sites = readme_sites(cached(f"{RAW}/README.md", lambda: get(rf["links"]["self"]).content).decode())
    mem = json.loads(cached(f"{RAW}/members.json", lambda: json.dumps(central_directory(url, size)).encode()))
    wavs = [m for m in mem if re.search(r"\.wav$", m[0], re.I)]
    secs = {}
    for m in wavs:
        if os.path.basename(m[0]).startswith("._") or m[1] in secs: continue
        secs[m[1]] = json.loads(cached(f"{RAW}/wav_{m[1]}.json", lambda: json.dumps(wav_seconds(url, m)).encode()))
    rows, skipped = [], {}
    def skip(why): skipped[why] = skipped.get(why, 0) + 1
    for name, usz, csz, off, method in wavs:
        b = os.path.basename(name); folder = os.path.basename(os.path.dirname(name))
        t = re.fullmatch(r"(\d{8})_(\d{6})(\(B\))?\.WAV", b)
        if not t: skip("not a recording name (macOS ._ resource fork)"); continue
        k = re.fullmatch(r"(?:\d_)?(blackwell|gv|whalon)\s*(\d)", folder, re.I)
        place, abbr = LOC[k[1].lower()]; n = int(k[2]); lat, lon = sites[(place, n)]
        ts = datetime.datetime.strptime(t[1] + t[2], "%Y%m%d%H%M%S"); stamp = f"{t[1]}_{t[2]}"
        rows.append([f"VIREO_{abbr}{n}_{stamp}{'B' if t[3] else ''}", "vireo-chicago", f"{place}/{n}/{stamp}{t[3] or ''}", rec["links"]["self_html"], url, credit,
                     "CC BY 4.0", "yes", lat, lon, min(dec(lat), dec(lon)),
                     "README.md site table, one coordinate per recorder site; folder number = README site number under the preserve (README order, unverified join)", "", "false",
                     stamp, "", ts.strftime("%Y-%m-%dT%H:%M:%SZ"), ts.strftime("%Y-%m-%d"), secs[usz], "device",
                     "file name = AudioMoth clock time, stated UTC in the WAV header comment (Recorded at HH:MM:SS DD/MM/YYYY (UTC)) and in CONFIG.TXT (Recording period ... (UTC)); clock set at configuration, GPS time setting No; no timezone conversion applied",
                     1, "download", "outdoor", json.dumps({"preserve": place, "site": n, "member": name, "bytes": usz, **({"b": 1} if t[3] else {})}, separators=(",", ":"))])
    rows.sort(key=lambda r: (r[0].split("_")[1], r[16]))
    os.makedirs(OUT, exist_ok=True)
    with gzip.open(f"{OUT}/vireo-chicago.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    win = [r for r in rows if r[16] >= "2023-02-16"]
    c = dict(source="vireo-chicago", built_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             rows=len(rows), rows_with_utc_start=sum(bool(r[16]) for r in rows), rows_from_2023_02_16=len(win),
             rows_from_2023_02_16_with_time_and_place=sum(bool(r[16] and r[8] and r[9]) for r in win),
             hours_from_2023_02_16=round(sum(float(r[18] or 0) for r in win) / 3600, 1), distinct_places=len({(r[8], r[9]) for r in rows}),
             distinct_days_from_2023_02_16=len({r[17] for r in win}), obscured=0, redistributable_yes=len(rows),
             method=f"GET {REC} (file list), GET {REC}/files/README.md/content (site table); HTTP Range on {url}: last 2 MB (zip central directory) and first ~2.2 KB of one member per size class ({len(secs)}) for the WAV header",
             skipped_members=skipped)
    json.dump(c, open(f"{OUT}/vireo-chicago.counts.json", "w"), indent=1)


if __name__ == "__main__":
    main()
