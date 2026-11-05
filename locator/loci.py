#!/usr/bin/env python3
import sys, os, time, json, argparse, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import *
from concurrent.futures import ProcessPoolExecutor

def lla(p):
    x, y, z = p[..., 0], p[..., 1], p[..., 2]; b = A * (1 - F); ep2 = (A * A - b * b) / (b * b)
    r = np.hypot(x, y); th = np.arctan2(z * A, r * b)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, r - E2 * A * np.cos(th) ** 3); lon = np.arctan2(y, x)
    n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2); return np.degrees(lat), np.degrees(lon), r / np.cos(lat) - n

class Dense:
    def __init__(s, tr, t0, t1, dt=0.25):
        s.t0, s.dt = t0, dt; tt = np.arange(t0, t1 + dt, dt); s.P, s.V = tr.at(tt); s.n = len(tt)
    def at(s, t):
        x = np.nan_to_num((t - s.t0) / s.dt, nan=-1.0); i = np.clip(np.floor(x).astype(int), 0, s.n - 2); w = (x - i)[..., None]
        bad = (x < 0) | (x > s.n - 1)
        P = s.P[i] * (1 - w) + s.P[i + 1] * w; V = s.V[i] * (1 - w) + s.V[i + 1] * w
        P[bad] = np.nan; return P, V

class Ground:
    def __init__(s, dem=None, box=None):
        s.a = None
        if dem:
            import rasterio
            from rasterio.windows import from_bounds
            with rasterio.open(dem) as r:
                if box is None: s.a = r.read(1); s.inv = ~r.transform
                else:
                    w = from_bounds(box[0] - 0.2, box[1] - 0.2, box[2] + 0.2, box[3] + 0.2, r.transform).round_offsets().round_lengths()
                    s.a = r.read(1, window=w, boundless=True, fill_value=0); s.inv = ~r.window_transform(w)
    def __call__(s, lat, lon):
        if s.a is None: return np.zeros(np.shape(lat))
        c, r = s.inv * (np.asarray(lon, float), np.asarray(lat, float)); r, c = np.floor(r).astype(int), np.floor(c).astype(int)
        ok = (r >= 0) & (r < s.a.shape[0]) & (c >= 0) & (c < s.a.shape[1]); h = np.zeros(np.shape(lat))
        h[ok] = s.a[r[ok], c[ok]] / 10.0; return np.maximum(h, 0)

def loci(dn, taus, b_abs, ground, dmin, dmax, vmin, vmax, mic=2.0):
    p, v = dn.at(taus); ok = np.isfinite(p[:, 0]); p, v, taus = p[ok], v[ok], taus[ok]
    sp = np.linalg.norm(v, axis=1); m = (sp >= vmin) & (sp <= vmax); p, v, taus, sp = p[m], v[m], taus[m], sp[m]
    if not len(taus): return None
    la, lo, hp = lla(p); e, n, u = enu_basis(la, lo)
    ve, vn, vu = (v * e).sum(1), (v * n).sum(1), (v * u).sum(1); vh = np.hypot(ve, vn) + 1e-9
    te, tn = ve / vh, vn / vh
    g0 = ground(la, lo); h = hp - (g0 + GEOID_N + mic)
    a = h * vu / vh
    B = np.concatenate([-b_abs[::-1], b_abs])[None, :]
    de = a[:, None] * te[:, None] + B * tn[:, None]; dnn = a[:, None] * tn[:, None] - B * te[:, None]
    kx = 111320 * np.cos(np.radians(la)); qla = la[:, None] + dnn / 110950.0; qlo = lo[:, None] + de / kx[:, None]
    g = ground(qla, qlo); hh = hp[:, None] - (g + GEOID_N + mic); sl = np.sqrt(a[:, None] ** 2 + B ** 2 + hh ** 2)
    k = (sl >= dmin) & (sl <= dmax) & (hh > 30)
    T = np.broadcast_to(taus[:, None], k.shape)[k]
    return np.column_stack([T, np.broadcast_to(B, k.shape)[k], qla[k], qlo[k], g[k], sl[k], np.broadcast_to(sp[:, None], k.shape)[k]])

def score(dn, O, T0, rt, rf, c, chunk=4000):
    out_r, out_f = np.full(len(O), 99.0), np.zeros(len(O))
    for s in range(0, len(O), chunk):
        o = O[s:s + chunk, None, :]; trx = T0[s:s + chunk, None] + rt[None, :]; te = trx.copy()
        for _ in range(5):
            P, _ = dn.at(te); r = np.linalg.norm(P - o, axis=-1); te = trx - r / c
        P, V = dn.at(te); d = o - P; r = np.linalg.norm(d, axis=-1)
        rat = c / (c - (V * d).sum(-1) / r); ok = np.isfinite(rat); n = ok.sum(1)
        R = np.where(ok, rat, 0); f0 = (rf[None] * R).sum(1) / np.maximum((R * R).sum(1), 1e-9)
        res = np.where(ok, rf[None] - f0[:, None] * R, 0); rms = np.sqrt((res ** 2).sum(1) / np.maximum(n, 1))
        good = n >= 0.8 * len(rt); out_r[s:s + chunk] = np.where(good, rms, 99.0); out_f[s:s + chunk] = f0
    return out_r, out_f

def run_flight(args):
    tr, ev, T0lo, T0hi, cfg = args
    c = cfg["c"]; ground = Ground(cfg.get("dem"))
    tau0 = T0lo + ev["tcpa"] - cfg["dmax"] / c - 5; tau1 = T0hi + ev["tcpa"] + 5
    tau0, tau1 = max(tau0, tr.t[0] + 5), min(tau1, tr.t[-1] - 5)
    if tau1 <= tau0: return None
    span = (ev["rt"][0] - ev["tcpa"] - 30, ev["rt"][-1] - ev["tcpa"] + 30)
    dn = Dense(tr, tau0 + span[0] - 40, tau1 + span[1] + 10)
    L = loci(dn, np.arange(tau0, tau1, cfg["dtau"]), cfg["b"], ground, cfg["dmin"], cfg["dmax"], cfg["vmin"], cfg["vmax"])
    if L is None or not len(L): return None
    if cfg.get("box"):
        w, s_, e, n = cfg["box"]; k = (L[:, 2] >= s_) & (L[:, 2] <= n) & (L[:, 3] >= w) & (L[:, 3] <= e); L = L[k]
        if not len(L): return None
    O = ecef(L[:, 2], L[:, 3], L[:, 4] + 2.0 + GEOID_N); T0 = L[:, 0] + L[:, 5] / c - ev["tcpa"]
    k = (T0 >= T0lo) & (T0 <= T0hi); L, O, T0 = L[k], O[k], T0[k]
    if not len(L): return None
    rms, f0 = score(dn, O, T0, ev["rs_t"], ev["rs_f"], c)
    j = np.argsort(rms)[:cfg["keep"]]; om = np.where(np.sign(L[:, 1]) != np.sign(L[j[0], 1]))[0]
    if len(om): j = np.unique(np.append(j, om[np.argmin(rms[om])]))
    return dict(icao=tr.icao, type=tr.type, kind=tr.kind, flight=tr.flight, n=int(len(L)), rms=rms[j], f0=f0[j], lat=L[j, 2], lon=L[j, 3], g=L[j, 4],
                slant=L[j, 5], speed=L[j, 6], T0=T0[j], b=L[j, 1])

def cpa_rx(dn, O, c, t0, t1):
    tt = np.arange(t0, t1, 0.5); P, _ = dn.at(tt); out = np.full(len(O), np.nan)
    for s in range(0, len(O), 2000):
        r = np.linalg.norm(P[None, :, :] - O[s:s + 2000, None, :], axis=-1); r = np.where(np.isfinite(r), r, np.inf)
        j = r.argmin(1); out[s:s + 2000] = tt[j] + r[np.arange(len(j)), j] / c
    return out

def refine(args):
    tr, ev, la, lo, T0c, cfg = args; c = cfg["c"]; ground = Ground(cfg.get("dem"))
    dn = Dense(tr, T0c + ev["rt"][0] - 80, T0c + ev["rt"][-1] + 20)
    def stage(la, lo, half, step, D, rt, rf):
        sub = np.arange(-half, half + 1e-6, step); X, Y = np.meshgrid(sub, sub)
        qla = la + Y.ravel() / 110950.0; qlo = lo + X.ravel() / (111320 * np.cos(np.radians(la))); g = ground(qla, qlo)
        O = ecef(qla, qlo, g + 2.0 + GEOID_N)
        T0p = cpa_rx(dn, O, c, T0c + ev["tcpa"] - 70, T0c + ev["tcpa"] + 20) - ev["tcpa"]
        ok = np.isfinite(T0p); O, qla, qlo, T0p = O[ok], qla[ok], qlo[ok], T0p[ok]
        OO = np.repeat(O, len(D), 0); TT = np.repeat(T0p, len(D)) + np.tile(D, len(O))
        rms, f0 = score(dn, OO, TT, rt, rf, c); j = int(np.argmin(rms))
        return dict(lat=np.repeat(qla, len(D)), lon=np.repeat(qlo, len(D)), T0=TT, rms=rms, f0=f0), j
    D1 = np.arange(-4, 4.01, 0.5); g, j = stage(la, lo, cfg["rref"], cfg["sref"], D1, ev["rt"], ev["rf"])
    q, j = stage(g["lat"][j], g["lon"][j], 3 * cfg["sref"], cfg["sref"] / 5, np.arange(-4, 4.01, 0.1), ev["rt"], ev["rf"])
    nd = len(D1); q["grid"] = dict(lat=g["lat"][::nd], lon=g["lon"][::nd], rms=g["rms"].reshape(-1, nd), T0=g["T0"].reshape(-1, nd))
    return q

def resid(dn, o, T0, rt, rf, c):
    trx = T0 + rt; te = trx.copy()
    for _ in range(5): P, _ = dn.at(te); te = trx - np.linalg.norm(P - o, axis=-1) / c
    P, V = dn.at(te); d = o - P; R = c / (c - (V * d).sum(-1) / np.linalg.norm(d, axis=-1)); ok = np.isfinite(R)
    return rf[ok] - (rf[ok] * R[ok]).sum() / (R[ok] ** 2).sum() * R[ok]

def n_eff(res):
    x = res - res.mean(); v = (x * x).mean(); s = 0.0
    for k in range(1, len(x) // 2):
        r = (x[:-k] * x[k:]).mean() / v
        if r <= 0: break
        s += r
    return len(x) / (1 + 2 * s)

def posterior(G, ne, best, truth=None, true_icao=None, cell=50.0):
    lat0 = best["lat"]; ky, kx = 110950.0, 111320 * np.cos(np.radians(lat0))
    G = G.assign(iy=np.rint(G.lat * ky / cell).astype(np.int64), ix=np.rint(G.lon * kx / cell).astype(np.int64)).drop_duplicates(["icao", "iy", "ix"])
    L = -ne * np.log(np.stack(G.rms.values)); m = L.max(); W = np.exp(L - m); w = W.sum(1); w /= w.sum()
    G = G.assign(w=w).sort_values("w", ascending=False); cw = G.w.cumsum().values
    out = dict(n_eff=round(float(ne), 1), cell_m=cell, aircraft_mass={k: round(float(v), 4) for k, v in G.groupby("icao").w.sum().sort_values(ascending=False).head(5).items()})
    for lev in (0.68, 0.9):
        k = int(np.searchsorted(cw, lev)) + 1; H = G.iloc[:k]
        dist = np.hypot((H.lat - lat0) * ky, (H.lon - best["lon"]) * kx)
        out[f"hpd{int(lev * 100)}"] = dict(area_km2=round(k * cell * cell / 1e6, 3), max_dist_m=round(float(dist.max()), 0), n_aircraft=int(H.icao.nunique()), n_blobs=int(H.blob.nunique()))
    bl = G.groupby("blob").apply(lambda b: pd.Series(dict(icao=b.icao.iloc[0], side=b.side.iloc[0], mass=b.w.sum(), lat=(b.lat * b.w).sum() / b.w.sum(),
                                                          lon=(b.lon * b.w).sum() / b.w.sum()))).sort_values("mass", ascending=False)
    out["blobs"] = [dict(icao=r.icao, side=r.side, mass=round(float(r.mass), 4), lat=round(float(r.lat), 6), lon=round(float(r.lon), 6)) for _, r in bl.head(4).iterrows()]
    if truth:
        tl = list(map(float, truth.split(","))); H = G if not true_icao else G[G.icao == true_icao]
        d = np.hypot((H.lat - tl[0]) * ky, (H.lon - tl[1]) * kx)
        if len(d) and d.min() <= cell:
            wt = H.w.iloc[int(np.argmin(d.values))]; out["truth_level"] = round(float(G.w[G.w > wt].sum()), 4)
        else: out["truth_level"] = None
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True); ap.add_argument("--win", required=True, help="t0,t1,flo,fhi of the ridge")
    ap.add_argument("--ridge", help="JSON with t,f lists (e.g. from dopplerprint) instead of measuring")
    ap.add_argument("--adsb", required=True); ap.add_argument("--dem"); ap.add_argument("--box", help="W,S,E,N")
    ap.add_argument("--t0", required=True, help="recording start UTC guess"); ap.add_argument("--slack", type=float, default=120)
    ap.add_argument("--vtol", type=float, default=0.3); ap.add_argument("--dmin", type=float, default=300); ap.add_argument("--dmax", type=float, default=10000)
    ap.add_argument("--dtau", type=float, default=1.0); ap.add_argument("--bstep", type=float, default=150); ap.add_argument("--keep", type=int, default=20)
    ap.add_argument("--temp", type=float, default=20.0); ap.add_argument("--jobs", type=int, default=16); ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--truth"); ap.add_argument("--true-icao"); ap.add_argument("--out", required=True); ap.add_argument("--nsub", type=int, default=60)
    a = ap.parse_args(); T = time.time(); c = csound(a.temp)
    def log(*x): print(f"[{time.time() - T:7.1f}s]", *x, flush=True)
    from measure import measure
    w0, w1, flo, fhi = map(float, a.win.split(","))
    z = np.load(a.spec); m = measure(z["f"], z["t"], z["S"].astype(np.float32), w0, w1, flo, fhi, c)
    rt, rf = (np.array(json.load(open(a.ridge))["t"]), np.array(json.load(open(a.ridge))["f"])) if a.ridge else (m["t"], m["f"])
    sub = np.linspace(0, len(rt) - 1, min(a.nsub, len(rt))).astype(int)
    ev = dict(tcpa=m["tcpa"], rt=rt, rf=rf, rs_t=rt[sub], rs_f=rf[sub])
    vmin, vmax = m["v"] * (1 - a.vtol), m["v"] * (1 + a.vtol)
    log(f"ridge {len(rt)} pts ({len(sub)} for coarse), straight fit v {m['v']:.0f} d {m['d']:.0f} tcpa {m['tcpa']:.1f}; speed {vmin:.0f}-{vmax:.0f}, slant {a.dmin:.0f}-{a.dmax:.0f}")
    T0g = pd.Timestamp(a.t0, tz="UTC").timestamp(); T0lo, T0hi = T0g - a.slack, T0g + a.slack
    box = list(map(float, a.box.split(","))) if a.box else None
    d = pd.read_parquet(a.adsb); d = d[(d.t >= T0lo + rt[0] - 900) & (d.t <= T0hi + rt[-1] + 300)]
    if box: d = d[d.lat.between(box[1] - 0.15, box[3] + 0.15) & d.lon.between(box[0] - 0.2, box[2] + 0.2)]
    sp = d.gs * KT; fast = d[sp.between(vmin, vmax)].icao.unique(); d = d[d.icao.isin(fast)]
    d.to_parquet("/tmp/loci_cut.parquet"); trs = tracks("/tmp/loci_cut.parquet"); log(f"{len(trs)} aircraft with a speed in range near the window")
    cfg = dict(c=c, dem=a.dem, dtau=a.dtau, b=np.arange(0, a.dmax + 1, a.bstep), dmin=a.dmin, dmax=a.dmax, vmin=vmin, vmax=vmax, keep=a.keep, box=box,
               rref=1200, sref=50, dtref=4.0)
    R = []
    with ProcessPoolExecutor(a.jobs) as ex:
        for r in ex.map(run_flight, [(t, ev, T0lo, T0hi, cfg) for t in trs], chunksize=2):
            if r: R.append(r)
    ncand = sum(r["n"] for r in R)
    C = pd.concat([pd.DataFrame({k: (r[k] if np.ndim(r[k]) else np.repeat(r[k], len(r["rms"]))) for k in r if k != "n"}) for r in R]) if R else pd.DataFrame()
    best = C.sort_values("rms").groupby("icao").head(1).reset_index(drop=True); best.index += 1
    log(f"coarse: {ncand} loci candidates from {len(R)} aircraft; best per aircraft:")
    print(best.head(a.top)[["icao", "type", "flight", "lat", "lon", "slant", "speed", "rms", "f0"]].round(4).to_string())
    byi = {t.icao: t for t in trs}; top = best.head(a.top); D, G, jobs = [], [], []
    for _, r in top.iterrows():
        jobs.append((r, "near"))
        Cm = C[(C.icao == r.icao) & (np.sign(C.b) != np.sign(r.b))]
        if len(Cm): jobs.append((Cm.loc[Cm.rms.idxmin()], "mirror"))
    with ProcessPoolExecutor(a.jobs) as ex:
        for bi, ((r, side), q) in enumerate(zip(jobs, ex.map(refine, [(byi[r.icao], ev, r.lat, r.lon, r.T0, cfg) for r, _ in jobs]))):
            j = int(np.argmin(q["rms"])); D.append(dict(icao=r.icao, type=r.type, flight=r.flight, side=side, coarse=r.rms, lat=q["lat"][j], lon=q["lon"][j], rms=q["rms"][j],
                                                         f0=q["f0"][j], T0=q["T0"][j]))
            g = q["grid"]; G.append(pd.DataFrame(dict(icao=r.icao, side=side, blob=bi, lat=g["lat"], lon=g["lon"], rms=list(g["rms"]), T0=list(g["T0"]))))
    D = pd.DataFrame(D).sort_values("rms").reset_index(drop=True); Dall = D; D = D.drop_duplicates("icao").reset_index(drop=True); D.index += 1
    b1 = D.iloc[0]; dnb = Dense(byi[b1.icao], b1.T0 + rt[0] - 60, b1.T0 + rt[-1] + 10)
    ne = n_eff(resid(dnb, ecef(b1.lat, b1.lon, Ground(a.dem)(b1.lat, b1.lon) + 2.0 + GEOID_N), b1.T0, rt, rf, c))
    U = posterior(pd.concat(G, ignore_index=True), ne, dict(lat=b1.lat, lon=b1.lon), a.truth, a.true_icao)
    log("mirror-side refinements:"); print(Dall[Dall.side == "mirror"].head(4).round(4).to_string())
    log("uncertainty:", json.dumps(U))
    if a.truth:
        tl = list(map(float, a.truth.split(","))); D["err_m"] = np.hypot((D.lat - tl[0]) * 110950, (D.lon - tl[1]) * 111320 * np.cos(np.radians(tl[0])))
    D["T0_utc"] = pd.to_datetime(D.T0, unit="s", utc=True).astype(str)
    log("refined (1.2 km at 50 m with dt 0.5 s, then 10 m with dt 0.1 s on the full ridge):"); print(D.round(4).to_string())
    import glob, hashlib
    code = hashlib.sha256(b"".join(open(x, "rb").read() for x in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "*.py"))))).hexdigest()
    rk = D.index[D.icao == a.true_icao] if a.true_icao else []
    J = dict(run=os.path.basename(a.out), mode="single pass, clock known +-%.0f s" % a.slack, box=box, adsb=a.adsb, dem=a.dem, ridge_window=a.win,
             straight_fit=dict(v=m["v"], d=m["d"], tcpa=m["tcpa"], f0=m["f0"]), n_aircraft=len(trs), n_loci_candidates=int(ncand),
             true_icao=a.true_icao, true_rank_dense=int(rk[0]) if len(rk) else None, true_score=float(D.rms[rk[0]]) if len(rk) else None,
             runner_up_score=float(D[D.icao != a.true_icao].rms.min()) if len(D) else None, err_m_dense=float(D.err_m[rk[0]]) if len(rk) and a.truth else None,
             best=dict(lat=float(D.lat.iloc[0]), lon=float(D.lon.iloc[0]), icao=D.icao.iloc[0], rms=float(D.rms.iloc[0]), T0_utc=D.T0_utc.iloc[0]) if len(D) else None,
             uncertainty=U, truth=a.truth, compute_s=round(time.time() - T, 1), cores=a.jobs, code_sha256=code, written_utc=str(pd.Timestamp.now(tz="UTC")))
    json.dump(J, open(a.out + ".json", "w"), indent=1); D.to_csv(a.out + ".dense.csv"); C.to_parquet(a.out + ".coarse.parquet"); log("wrote", a.out + ".json")

if __name__ == "__main__":
    main()
