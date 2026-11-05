import json, os, numpy as np, pandas as pd

A, F = 6378137.0, 1 / 298.257223563; E2 = F * (2 - F)
GEOID_N = -32.5          # one fixed EGM2008 undulation (m) for every place; GLO-30 heights are EGM2008
FT, KT, FPM = 0.3048, 0.514444, 0.3048 / 60
TYPES = json.load(open(os.environ.get("GEO_TYPES", os.path.join(os.path.dirname(os.path.abspath(__file__)), "types.json"))))

def ecef(lat, lon, h):
    la, lo = np.radians(lat), np.radians(lon); n = A / np.sqrt(1 - E2 * np.sin(la) ** 2)
    return np.stack([(n + h) * np.cos(la) * np.cos(lo), (n + h) * np.cos(la) * np.sin(lo), (n * (1 - E2) + h) * np.sin(la)], -1)

def enu_basis(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    e = np.stack([-np.sin(lo), np.cos(lo), np.zeros_like(lo)], -1)
    n = np.stack([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)], -1)
    u = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)
    return e, n, u

def kind(t):
    d = TYPES.get(t, {}).get("desc", "") if isinstance(t, str) else ""
    if not d: return "unknown"
    if d[0] in "HG": return "heli" if d[0] == "H" else "glider"
    return {"P": "piston", "T": "turboprop", "J": "jet", "E": "electric"}.get(d[2], "unknown")

def csound(temp_c): return 331.3 * np.sqrt(1 + temp_c / 273.15)

class Track:
    def __init__(s, g, baro_off_ft=0.0, maxgap=60.0):
        g = g.sort_values("t").drop_duplicates("t"); g = g[~g.gnd & g.alt.notna() & g.gs.notna() & g.trk.notna()]
        s.icao, s.type, s.flight = g.icao.iloc[0], g.type.iloc[0], g.flight.dropna().iloc[0] if g.flight.notna().any() else None
        s.kind = kind(s.type)
        h = np.where(g.galt.notna(), g.galt, g.alt + baro_off_ft) * FT  # ADS-B geometric altitude is WGS84 ellipsoidal
        s.t = g.t.values; s.p = ecef(g.lat.values, g.lon.values, h)
        e, n, u = enu_basis(g.lat.values, g.lon.values); tr = np.radians(g.trk.values); gs = g.gs.values * KT
        vr = np.nan_to_num(g.vr.values.astype(float)) * FPM
        s.v = (gs * np.sin(tr))[:, None] * e + (gs * np.cos(tr))[:, None] * n + vr[:, None] * u
        s.gs = gs; s.lat, s.lon, s.h = g.lat.values, g.lon.values, h
        s.ok = np.diff(s.t) <= maxgap
    def at(s, t):
        t = np.asarray(t, float); i = np.clip(np.searchsorted(s.t, t) - 1, 0, len(s.t) - 2)
        dt = s.t[i + 1] - s.t[i]; x = ((t - s.t[i]) / dt)[..., None]; d = dt[..., None]
        p0, p1, v0, v1 = s.p[i], s.p[i + 1], s.v[i], s.v[i + 1]
        h00, h10, h01, h11 = 2 * x**3 - 3 * x**2 + 1, x**3 - 2 * x**2 + x, -2 * x**3 + 3 * x**2, x**3 - x**2
        p = h00 * p0 + h10 * d * v0 + h01 * p1 + h11 * d * v1
        dv = (1 - x) * v0 + x * v1   # reported velocities, linearly interpolated: the Hermite derivative wiggles at knots
        bad = (t < s.t[0]) | (t > s.t[-1]) | ~s.ok[i]
        p[bad] = np.nan; dv[bad] = np.nan
        return p, dv

def emission(track, o, t_rx, c=343.0, it=6):
    te = np.asarray(t_rx, float).copy()
    for _ in range(it):
        p, v = track.at(te); r = np.linalg.norm(p - o, axis=-1); te = t_rx - r / c
    p, v = track.at(te); return te, p, v

def doppler(track, o, t_rx, c=343.0):
    te, p, v = emission(track, o, t_rx, c)
    d = o - p; u = d / np.linalg.norm(d, axis=-1, keepdims=True)
    return c / (c - np.einsum("...k,...k->...", v, u))

def cpa(track, o, c=343.0, step=0.5):
    tt = np.arange(track.t[0], track.t[-1], step); p, v = track.at(tt)
    r = np.linalg.norm(p - o, axis=-1)
    if np.all(np.isnan(r)): return None
    i = np.nanargmin(r); return tt[i], tt[i] + r[i] / c, r[i], np.linalg.norm(v[i])

def tracks(parquet, t0=None, t1=None, box=None):
    d = pd.read_parquet(parquet)
    if t0 is not None: d = d[(d.t >= t0) & (d.t <= t1)]
    if box is not None: d = d[d.lat.between(box[1], box[3]) & d.lon.between(box[0], box[2])]
    d = d[~d.gnd]
    m = d.galt.notna() & d.alt.notna(); band = (d.alt // 2000)
    off = (d.galt - d.alt)[m].groupby(band[m]).median()
    out = []
    for ic, g in d.groupby("icao"):
        if len(g) < 4: continue
        o = off.get(g.alt.median() // 2000, off.median()) if len(off) else 0.0
        try: out.append(Track(g, o))
        except (IndexError, ValueError): pass
    return out

def passes(tr, pts_tree, pts, dmax, c=343.0, chunk=300.0, stepc=150.0, vrange=None):
    from scipy.spatial import cKDTree
    out = []
    for c0 in np.arange(tr.t[0], tr.t[-1], stepc):
        tt = np.arange(c0, min(c0 + chunk, tr.t[-1]), 1.0)
        if len(tt) < 20: continue
        p, v = tr.at(tt); ok = np.isfinite(p[:, 0])
        if ok.sum() < 20: continue
        tt, p, v = tt[ok], p[ok], v[ok]
        if vrange is not None:
            sp = np.linalg.norm(v, axis=1)
            if sp.max() < vrange[0] or sp.min() > vrange[1]: continue
        near = pts_tree.query_ball_point(p[::10], dmax + 1500)
        idx = np.unique(np.concatenate([np.asarray(n, int) for n in near])) if len(near) else np.array([], int)
        if not len(idx): continue
        d, j = cKDTree(p).query(pts[idx]); m = (d <= dmax) & (j > 2) & (j < len(tt) - 3) & (np.diff(tt).max() < 3)
        if m.any(): out.append(np.stack([idx[m], tt[j[m]] + d[m] / c, d[m], np.linalg.norm(v[j[m]], axis=1)], 1))
    if not out: return np.zeros((0, 4))
    e = np.concatenate(out); e = e[np.lexsort((e[:, 1], e[:, 0]))]
    keep = np.ones(len(e), bool); keep[1:] = ~((e[1:, 0] == e[:-1, 0]) & (np.abs(e[1:, 1] - e[:-1, 1]) < 10))
    return e[keep]

def rat_fn(tr, o, t0, t1, c=343.0):
    tt = np.arange(t0, t1, 0.05); r = doppler(tr, o, tt, c); return lambda t: np.interp(t, tt, r, left=np.nan, right=np.nan)

def trace_rms(rf, t, fo):
    r = rf(t); ok = np.isfinite(r)
    if ok.mean() < 0.8: return 99.0, 0.0
    f0 = (fo[ok] * r[ok]).sum() / (r[ok] ** 2).sum(); return float(np.sqrt(np.mean((fo[ok] - f0 * r[ok]) ** 2))), f0

def grid(lat0, lon0, half, step, shift=(73.0, -121.0)):
    kx, ky = 111320 * np.cos(np.radians(lat0)), 110950.0; xs = np.arange(-half, half + 1, step)
    X, Y = np.meshgrid(xs + shift[0], xs + shift[1]); return lat0 + Y.ravel() / ky, lon0 + X.ravel() / kx

def ground(lat, lon, dem):
    import rasterio
    with rasterio.open(dem) as r:
        a = r.read(1); inv = ~r.transform
    col, row = inv * (np.asarray(lon, float), np.asarray(lat, float)); row, col = np.floor(row).astype(int), np.floor(col).astype(int); ok = (row >= 0) & (row < a.shape[0]) & (col >= 0) & (col < a.shape[1])
    h = np.zeros(len(lat)); h[ok] = a[row[ok], col[ok]] / 10.0; return np.maximum(h, 0)

def region_grid(w, s, e, n, step, mask=None):
    lat0 = (s + n) / 2; ys = np.arange(s, n, step / 110950.0); xs = np.arange(w, e, step / (111320 * np.cos(np.radians(lat0))))
    LO, LA = np.meshgrid(xs, ys); la, lo = LA.ravel(), LO.ravel()
    if mask is not None:
        import shapely; k = shapely.contains_xy(mask, lo, la); la, lo = la[k], lo[k]
    return la, lo

def state_shape(zipf, name):
    import shapefile, shapely
    r = shapefile.Reader(zipf); i = [f[0] for f in r.fields[1:]].index("NAME")
    for sr in r.iterShapeRecords():
        if sr.record[i] == name: return shapely.geometry.shape(sr.shape.__geo_interface__)
    raise KeyError(name)
