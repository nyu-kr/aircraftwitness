# VBR byte rates give inaccurate timing and the initial Icecast burst distorts timing fits.
import bisect, gzip, json, os, re, struct, tempfile

ROOT = os.environ.get("LS_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
UA = "Mozilla/5.0 (compatible; aircraftwitness-capture/1.0; research)"
LIST_URL = os.environ.get("LS_LIST_URL", "https://locusonus.org/soundmap/list/active/json/name")
SEG_S = 600


def paths(root=ROOT):
    c = f"{root}/capture"
    return dict(root=root, cap=c, buf=f"{c}/buf", keep=f"{c}/keep", log=f"{c}/log", labels=f"{root}/labels",
                manifest=f"{root}/manifests/locustream.csv.gz")


def mount_list(text):
    # Preserve latitude and longitude as the literal strings printed in JSON.
    rows = json.loads(text, parse_float=str, parse_int=str)
    base = lambda r: re.sub(r"\.(mp3|ogg)$", "", r["url"].rstrip("/").rsplit("/", 1)[-1])
    cnt = {}
    for r in rows: cnt[base(r)] = cnt.get(base(r), 0) + 1
    out = {}
    for r in rows:
        b = base(r)
        if b.startswith("mobile_") or not r.get("lat") or not r.get("lng") or not r["url"]: continue
        try: float(r["lat"]); float(r["lng"])
        except ValueError: continue
        m = b if cnt[b] == 1 else f"{b}_{r['id']}"
        out[m] = dict(mount=m, id=r["id"], name=r["name"], url=r["url"], ext=r["ext"], artist=r["artist"],
                      lat=r["lat"], lng=r["lng"], city=r["city"], country=r["country"])
    return out


def write_atomic(path, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp")
    with os.fdopen(fd, "wb") as f: f.write(data)
    os.chmod(tmp, 0o644); os.replace(tmp, path)


def save_sc(path, sc): write_atomic(path, gzip.compress(json.dumps(sc, separators=(",", ":")).encode(), 6))


def load_sc(path):
    with gzip.open(path, "rt") as f: return json.load(f)


def fit(arrival, hdr, new_conn):
    pts = [(t / 1000, o - hdr) for o, t in arrival]
    if new_conn: pts = [p for p in pts if p[0] >= pts[0][0] + 20]
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < 30: return None
    n = len(pts); mt = sum(p[0] for p in pts) / n; mo = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mt) ** 2 for p in pts); r = sum((p[0] - mt) * (p[1] - mo) for p in pts) / sxx
    if r <= 0: return None
    t_a = mt - mo / r; res = (sum((p[1] - r * (p[0] - t_a)) ** 2 for p in pts) / n) ** .5 / r
    f = dict(method="byte_rate", rate_Bps=round(r, 1), t_a_ms=round(t_a * 1000), resid_s=round(res, 2), dur_s=round((arrival[-1][0] - hdr) / r, 1))
    if new_conn:
        f["burst_s"] = round(arrival[0][1] / 1000 - t_a, 2); f["burst_bytes"] = round(r * f["burst_s"])
    return f


def media_index(d, kind, hdr):
    out = []; nxt = 0
    if kind == "mp3":
        i = mp3_sync(d, 0); t = 0.0
        while i < len(d) and (L := mp3_len(d, i)):
            if t >= nxt: out.append([i, round(t, 3)]); nxt = t + 1
            v = (d[i + 1] >> 3) & 3; t += (1152 if v == 3 else 576) / MP3_SR[v][(d[i + 2] >> 2) & 3]; i += L
        out.append([i, round(t, 3)])
    elif kind == "ogg":
        k = d.find(b"\x01vorbis", 0, hdr or 4096)
        sr = struct.unpack("<I", d[k + 12:k + 16])[0] if k >= 0 else 48000
        g0 = None
        for s_, e in ogg_pages(d, hdr):
            g = struct.unpack("<q", d[s_ + 6:s_ + 14])[0]
            if g < 0: continue
            if g0 is None: g0 = g
            if (g - g0) / sr >= nxt: out.append([e, round((g - g0) / sr, 3)]); nxt = (g - g0) / sr + 1
    return out


def o_at(idx, m, lo=0):
    if not idx or m <= idx[0][1]: return lo
    k = bisect.bisect_right([x[1] for x in idx], m)
    if k >= len(idx): return idx[-1][0]
    (o0, m0), (o1, m1) = idx[k - 1], idx[k]
    return int(o0 + (o1 - o0) * (m - m0) / (m1 - m0))


def m_at(idx, o):
    k = bisect.bisect_right([x[0] for x in idx], o)
    if k == 0: return idx[0][1]
    if k >= len(idx): return idx[-1][1]
    (o0, m0), (o1, m1) = idx[k - 1], idx[k]
    return m0 + (m1 - m0) * (o - o0) / (o1 - o0)


def fit_media(arrival, idx, hdr, new_conn):
    if len(idx) < 3: return None
    pts = [(t / 1000, m_at(idx, o)) for o, t in arrival]
    if new_conn: pts = [p for p in pts if p[0] >= arrival[0][1] / 1000 + 20]
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < 30: return None
    ta = sum(t - m for t, m in pts) / len(pts); res = (sum((t - m - ta) ** 2 for t, m in pts) / len(pts)) ** .5
    dur = idx[-1][1]; f = dict(method="media_clock", t_a_ms=round(ta * 1000), resid_s=round(res, 2), dur_s=round(dur, 1),
                               rate_Bps=round((arrival[-1][0] - hdr) / dur, 1))
    if new_conn:
        f["burst_s"] = round(arrival[0][1] / 1000 - ta, 2); f["burst_bytes"] = o_at(idx, f["burst_s"]) - idx[0][0]
    return f


def finish(sc, d):
    a = sc["arrival"]; idx = media_index(d, sc["kind"], sc["header_bytes"]); f = fit_media(a, idx, sc["header_bytes"], sc["new_connection"])
    if f: sc["index"] = idx
    else: f = fit(a, sc["header_bytes"], sc["new_connection"]); sc.pop("index", None)
    if f and sc["new_connection"]: f["bytes_first_1s"] = max(o for o, t in a if t <= a[0][1] + 1000) - sc["header_bytes"]
    sc["fit"] = f


def off_at(sc, t_ms):
    f = sc["fit"]
    if f["method"] == "media_clock": return o_at(sc["index"], (t_ms - f["t_a_ms"]) / 1000, sc["header_bytes"])
    return sc["header_bytes"] + int(f["rate_Bps"] * (t_ms - f["t_a_ms"]) / 1000)


MP3_BR = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
          2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
MP3_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_len(d, i):
    if i + 4 > len(d) or d[i] != 0xFF or d[i + 1] & 0xE0 != 0xE0: return 0
    v = (d[i + 1] >> 3) & 3; layer = (d[i + 1] >> 1) & 3; bi = d[i + 2] >> 4; si = (d[i + 2] >> 2) & 3
    if v == 1 or layer != 1 or bi in (0, 15) or si == 3: return 0
    sr = MP3_SR[v][si]; br = MP3_BR[1 if v == 3 else 2][bi] * 1000
    return (144 if v == 3 else 72) * br // sr + ((d[i + 2] >> 1) & 1)


def mp3_sync(d, i):
    n = len(d)
    while i < n - 8:
        i = d.find(b"\xff", i)
        if i < 0: return n
        L = mp3_len(d, i)
        if L and mp3_len(d, i + L): return i
        i += 1
    return n


def ogg_pages(d, i=0):
    n = len(d)
    while True:
        i = d.find(b"OggS", i)
        if i < 0 or i + 27 > n: return
        ns = d[i + 26]
        if i + 27 + ns > n: return
        e = i + 27 + ns + sum(d[i + 27:i + 27 + ns])
        if e > n: return
        if e < n and d[e:e + 4] != b"OggS": i += 1; continue
        yield i, e; i = e


def ogg_header_len(d):
    for s, e in ogg_pages(d, 0):
        if struct.unpack("<q", d[s + 6:s + 14])[0] != 0: return s
    return None


def cut(data, ext, hdr, a, b):
    if ext == "mp3":
        sy = lambda x: mp3_sync(data, max(x, 0)) if mp3_sync(data, max(x, 0)) < len(data) else max(x, 0)
        return data[sy(a):sy(b)]
    pg = [(s, e) for s, e in ogg_pages(data, hdr) if s >= max(a, hdr) and s < b]
    return data[:hdr] + b"".join(data[s:e] for s, e in pg)
