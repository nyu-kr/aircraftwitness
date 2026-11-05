#!/usr/bin/env python3
import sys, os, time, json, argparse, subprocess, hashlib, glob, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import ecef, csound, Track, GEOID_N, KT
from loci import Dense, Ground, score
from measure import model
from ridge import spectrogram, retrace, straight_fit
from scipy.ndimage import maximum_filter1d
from concurrent.futures import ProcessPoolExecutor

DP = os.environ.get("DP_CMD", "dopplerprint").split()
HOP_INDEP = 1.5   # s of ridge per independent residual (measured: n_eff 40 on a 60 s ridge)
SIG_NUDGE = 1.3   # s, per-aircraft ADS-B timing scatter at a known place (sd 1.3 over 30 passes)

def events(js, c, spec=None, vmin=3.0, vmax=330.0, min_len=6.0, nsub=60, min_drop=1.01):
    raw = []
    for e in json.load(open(js)):
        b = e["fit"]["best"]; h = e.get("ridge_harmonic", 1) or 1
        raw.append(dict(id=e["id"], t=np.asarray(e["ridge"]["t"], float), f=np.asarray(e["ridge"]["f_hz"], float) / h, harm=set(e.get("harmonics") or [h]),
                        par=[b["f0"] / h, b["v"], b["d_cpa"], b["t_cpa_arrive"]], snr=e.get("snr", 0), merged=False))
    raw.sort(key=lambda r: r["t"][0]); mer = []
    for r in raw:                                   # the detector can split one pass at its CPA: join abutting ridges that continue in frequency
        q = mer[-1] if mer else None
        if q is not None and 0 <= r["t"][0] - q["t"][-1] <= 3 and abs(r["f"][0] - q["f"][-1]) <= 0.06 * q["f"][-1] and len(q["t"]) > 4 and len(r["t"]) > 4:
            q["t"], q["f"] = np.concatenate([q["t"], r["t"]]), np.concatenate([q["f"], r["f"]]); q["harm"] |= r["harm"]; q["snr"] = max(q["snr"], r["snr"]); q["merged"] = True
        else: mer.append(r)
    out = []
    for e in mer:
        t, f, par = e["t"], e["f"], e["par"]
        if e["merged"]:
            par, _ = straight_fit(t, f, c, [np.median(f), 80.0, 2000.0, t[len(t) // 2]])
            if par is None: continue
        # a partial ridge's speed is degenerate with its distance, so only stationary tones are dropped here
        if not (vmin <= par[1] <= vmax) or t[-1] - t[0] < min_len or np.percentile(f, 90) / np.percentile(f, 10) < min_drop or np.polyfit(t, f, 1)[0] >= 0: continue
        h = 1
        if spec is not None:
            t2, f2, _ = retrace(*spec, t, f, sorted(e["harm"]), par, c)
            if len(t2) > 8 and t2[-1] - t2[0] >= min_len:
                p2, _ = straight_fit(t2, f2, c, par)
                if p2 is None: continue
                par, t, f = p2, t2, f2
                if not (vmin <= par[1] <= vmax): continue
        r0 = float(np.sqrt(np.mean((f - model(par, t, c)) ** 2)))
        sub = np.linspace(0, len(t) - 1, min(nsub, len(t))).astype(int)
        out.append(dict(id=e["id"], tcpa=float(par[3]), v=float(par[1]), d=float(par[2]), f0=float(par[0]), t=t, f=f, ts=t[sub], fs=f[sub], r0=r0,
                        n=max((t[-1] - t[0]) / HOP_INDEP, 3.0), snr=e.get("snr", 0)))
    out.sort(key=lambda e: -(e["t"][-1] - e["t"][0])); keep = []
    for e in out:
        if any(abs(e["tcpa"] - k["tcpa"]) < 4 and min(e["t"][-1], k["t"][-1]) - max(e["t"][0], k["t"][0]) > 0.5 * (e["t"][-1] - e["t"][0]) for k in keep): continue
        keep.append(e)
    return sorted(keep, key=lambda e: e["tcpa"])

def clip_events(path, c, retrace=True):
    EV = []
    for k, ln in enumerate(open(path)):
        au, js, off = ln.rstrip("\n").split("\t"); off = float(off)
        if not os.path.exists(js): subprocess.run(DP + ["discover", au, "--out", js], check=True, stdout=subprocess.DEVNULL)
        EV += [dict(e, id=f"{k}:{e['id']}", tcpa=e["tcpa"] + off, t=e["t"] + off, ts=e["ts"] + off) for e in events(js, c, spectrogram(au) if retrace else None)]
    return sorted(EV, key=lambda e: e["tcpa"])

def cpa_grid(dn, O, c, t0, t1, step=1.0):
    tt = np.arange(t0, t1, step); P, V = dn.at(tt); trx = np.full(len(O), np.nan); sl = trx.copy(); inn = np.zeros(len(O), bool)
    for s in range(0, len(O), 1500):
        r = np.linalg.norm(P[None] - O[s:s + 1500, None], axis=-1); r = np.where(np.isfinite(r), r, np.inf); j = r.argmin(1)
        trx[s:s + 1500] = tt[j] + r[np.arange(len(j)), j] / c; sl[s:s + 1500] = r[np.arange(len(j)), j]; inn[s:s + 1500] = (j > 0) & (j < len(tt) - 1)
    return trx, sl, inn

_G = {}   # large grids: points and their KD-tree, inherited by forked workers instead of pickled per task

def per_track(args):
    tr, EV, O, cfg = args; c = cfg["c"]; lo, hi = cfg["T0lo"], cfg["T0hi"]; tree = None
    if O is None: O, tree = _G["O"], _G["tree"]
    t_all0 = lo + min(e["t"][0] for e in EV) - cfg["dmax"] / c - 60; t_all1 = hi + max(e["t"][-1] for e in EV) + 10
    if min(t_all1, tr.t[-1]) - max(t_all0, tr.t[0]) < 10: return None
    dn = Dense(tr, max(t_all0, tr.t[0]), min(t_all1, tr.t[-1]), dt=0.5)
    sp = np.linalg.norm(dn.V, axis=1); ctr = O.mean(0); rad = np.linalg.norm(O - ctr, axis=1).max() + cfg["dmax"]
    near = np.linalg.norm(dn.P - ctr, axis=1) <= rad; tt = dn.t0 + dn.dt * np.arange(dn.n); out = []
    D = np.arange(-cfg["nudge"], cfg["nudge"] + 1e-6, 1.0)
    for i, e in enumerate(EV):
        a0, a1 = lo + e["tcpa"] - cfg["dmax"] / c - 10, hi + e["tcpa"] + 5
        w = near & (tt >= a0) & (tt <= a1)
        if not w.any() or not ((sp[w] >= e["v"] * (1 - cfg["vtol"])) & (sp[w] <= e["v"] * (1 + cfg["vtol"]))).any(): continue
        Pw = dn.P[(tt >= a0) & (tt <= a1)]; Pw = Pw[np.isfinite(Pw[:, 0])]
        if not len(Pw): continue
        if tree is None: sub = np.where(np.all((O >= Pw.min(0) - cfg["dmax"]) & (O <= Pw.max(0) + cfg["dmax"]), axis=1))[0]
        else: sub = np.unique(np.concatenate([np.asarray(x, int) for x in tree.query_ball_point(Pw[::20], cfg["dmax"] + 1500)]))
        if not len(sub): continue
        trx, sl, inn = cpa_grid(dn, O[sub], c, a0, a1); T0 = trx - e["tcpa"]
        k = np.where(inn & (sl <= cfg["dmax"]) & (sl >= cfg["dmin"]) & (T0 >= lo - cfg["nudge"]) & (T0 <= hi + cfg["nudge"]))[0]
        if not len(k): continue
        T0 = np.full(len(O), np.nan); T0[sub] = trx - e["tcpa"]; k = sub[k]
        if tree is None: Dk, ts_, fs_ = D, e["ts"], e["fs"]
        else: q = np.linspace(0, len(e["ts"]) - 1, min(20, len(e["ts"]))).astype(int); Dk, ts_, fs_ = D[::2], e["ts"][q], e["fs"][q]
        TT = (T0[k][:, None] + Dk[None]).ravel(); rms, f0 = score(dn, np.repeat(O[k], len(Dk), 0), TT, ts_, fs_, c)
        rms, TT = rms.reshape(-1, len(Dk)), TT.reshape(-1, len(Dk)); j = rms.argmin(1); r = rms[np.arange(len(k)), j]
        ok = r < 50; out.append((i, k[ok].astype(np.int32), TT[np.arange(len(k)), j][ok], r[ok].astype(np.float32)))
    return (tr.icao, tr.type, tr.flight, out) if out else None

def combine(M, EV, npts, lo, hi, tol, rref):
    b0 = lo - tol; nb = int(np.ceil(hi - lo + 2 * tol)) + 1
    U = np.unique(np.concatenate([k for v in M.values() for (k, _, _) in v.values()] or [np.zeros(0, int)])); pos = np.full(npts, -1); pos[U] = np.arange(len(U))
    acc = np.zeros((len(U), nb), np.float32)
    for i, e in enumerate(EV):
        g = np.zeros((len(U), nb), np.float32)
        for icao, (k, T0, r) in M.get(i, {}).items():
            b = np.rint(T0 - b0).astype(int); ok = (b >= 0) & (b < nb)
            np.maximum.at(g, (pos[k[ok]], b[ok]), (e["n"] * np.log(rref[i] / r[ok])).astype(np.float32))
        acc += maximum_filter1d(g, 2 * tol + 1, axis=1)
    acc[:, :tol] = -1; acc[:, nb - tol:] = -1
    S, T = np.zeros(npts, np.float32), np.full(npts, b0 + tol, float)
    if len(U): j = acc.argmax(1); S[U], T[U] = acc[np.arange(len(U)), j], b0 + j
    return S, T

def refine_point(args):
    la, lo_, T0, pairs, trk, EV, cfg = args; c = cfg["c"]; ground = Ground(cfg.get("dem"), cfg.get("box")); w, s_, e_, n_ = cfg["box"]
    dns = {i: Dense(trk[ic], T0 + EV[i]["t"][0] - 80, T0 + EV[i]["t"][-1] + 20) for i, ic in pairs}
    def stage(la, lo_, half, step, Ds, deltas):
        s = np.arange(-half, half + 1e-6, step); X, Y = np.meshgrid(s, s); qla = la + Y.ravel() / 110950.0; qlo = lo_ + X.ravel() / (111320 * np.cos(np.radians(la)))
        inb = (qla >= s_) & (qla <= n_) & (qlo >= w) & (qlo <= e_); qla, qlo = qla[inb], qlo[inb]
        O = ecef(qla, qlo, ground(qla, qlo) + 2.0 + GEOID_N); terms = {}
        for i, ic in pairs:
            ev = EV[i]; D = Ds[i]; rms, _ = score(dns[i], np.repeat(O, len(D), 0), np.repeat(np.full(len(O), T0), len(D)) + np.tile(D, len(O)), ev["t"], ev["f"], c)
            g = ev["n"] * np.log(cfg["rref"][i] / rms.reshape(-1, len(D)))
            pen = (D[None, :] - deltas[:, None]) ** 2 / (2 * SIG_NUDGE ** 2)
            tot = g[:, None, :] - pen[None]; j = tot.argmax(2)
            terms[i] = (np.maximum(0, np.take_along_axis(tot, j[..., None], 2)[..., 0]), D[j], np.take_along_axis(rms.reshape(-1, len(D))[:, None, :].repeat(len(deltas), 1), j[..., None], 2)[..., 0])
        S = sum(t[0] for t in terms.values()); k = S.argmax(1); S = S[np.arange(len(S)), k]
        per = {i: (t[2][np.arange(len(k)), k], t[1][np.arange(len(k)), k]) for i, t in terms.items()}
        return qla, qlo, S, per, deltas[k]
    if not pairs: return dict(lat=la, lon=lo_, S=0.0, T0=T0, events={}, grid50=(np.array([la]), np.array([lo_]), np.zeros(1)))
    d_lo, d_hi = np.floor(max(cfg["T0lo"] - T0, -cfg["tol"] - 2)), np.ceil(min(cfg["T0hi"] - T0, cfg["tol"] + 2))
    qla, qlo, S, per, dl = stage(la, lo_, cfg["rhalf"], 50.0, {i: np.arange(d_lo - 4, d_hi + 4.01, 0.5) for i, _ in pairs}, np.arange(d_lo, d_hi + 0.01, 0.25))
    j = int(np.argmax(S)); grid50 = (qla, qlo, S); d0 = dl[j]
    qla, qlo, S, per, dl = stage(qla[j], qlo[j], 150.0, 10.0, {i: per[i][1][j] + np.arange(-0.75, 0.76, 0.25) for i, _ in pairs}, np.array([d0]))
    j = int(np.argmax(S))
    return dict(lat=qla[j], lon=qlo[j], S=S[j], T0=T0, delta=float(d0), events={i: dict(icao=ic, rms=float(per[i][0][j]), nudge=float(per[i][1][j] - d0)) for i, ic in pairs},
                grid50=grid50)

from functools import lru_cache
@lru_cache(maxsize=4)
def _ground(dem, box): return Ground(dem, list(box) if box else None)

def at_point(la, lo, T0, EV, trk, cfg, dlim=None):
    c = cfg["c"]; ground = _ground(cfg.get("dem"), tuple(cfg["box"]) if cfg.get("box") else None); O = ecef(np.array([la]), np.array([lo]), ground(np.array([la]), np.array([lo])) + 2.0 + GEOID_N)
    D = np.arange(-6, 6.01, 0.25); deltas = np.arange(cfg["T0lo"] - T0 - 2, cfg["T0hi"] - T0 + 2.01, 0.5) if dlim is None else dlim
    best = {}
    for ic, tr in trk.items():
        for i, e in enumerate(EV):
            a0, a1 = min(T0, cfg["T0lo"]) + e["tcpa"] - cfg["dmax"] / c - 20, max(T0, cfg["T0hi"]) + e["tcpa"] + 20
            if tr.t[-1] < a0 or tr.t[0] > a1: continue
            ts_ = np.arange(max(a0, tr.t[0]), min(a1, tr.t[-1]) + 1, 5.0)
            if not len(ts_) or np.min(np.hypot((np.interp(ts_, tr.t, tr.lat) - la) * 111.0, (np.interp(ts_, tr.t, tr.lon) - lo) * 111.0 * np.cos(np.radians(la)))) > cfg["dmax"] / 1000 + 3: continue
            w0_, w1_ = max(min(a0, T0 + e["t"][0] - cfg["dmax"] / c) - 30, tr.t[0]), min(max(a1, T0 + e["t"][-1]) + 30, tr.t[-1])
            if w1_ - w0_ < 10: continue
            dn = Dense(tr, w0_, w1_, dt=0.5)
            if dn.n < 4: continue
            trx, sl, inn = cpa_grid(dn, O, c, a0, a1)
            if not (inn[0] and sl[0] <= cfg["dmax"]): continue
            T0p = trx[0] - e["tcpa"]
            if not (min(T0, cfg["T0lo"]) - 12 <= T0p <= max(T0, cfg["T0hi"]) + 12): continue
            rms, _ = score(dn, np.repeat(O, len(D), 0), T0p + D, e["t"], e["f"], c); g = e["n"] * np.log(cfg["rref"][i] / rms)
            best.setdefault(i, []).append((ic, T0p - T0 + D, g, rms, float(sl[0])))
    S = np.zeros(len(deltas)); pick = {}
    for i, L in best.items():
        tot = np.full(len(deltas), 0.0); arg = [None] * len(deltas)
        for ic, Dd, g, rms, sl in L:
            v = g[None, :] - (Dd[None, :] - deltas[:, None]) ** 2 / (2 * SIG_NUDGE ** 2); j = v.argmax(1); vv = v[np.arange(len(deltas)), j]
            for q in np.where(vv > tot)[0]: tot[q] = vv[q]; arg[q] = (ic, float(rms[j[q]]), float(Dd[j[q]] - deltas[q]), sl)
        S += tot; pick[i] = arg
    k = int(S.argmax())
    return dict(S=float(S[k]), delta=float(deltas[k]), events={i: dict(zip(("icao", "rms", "nudge", "slant_m"), pick[i][k])) for i in pick if pick[i][k]})

def hpd(lat, lon, S, cell, la0, lo0):
    w = np.exp(S - S.max()); w /= w.sum(); o = np.argsort(-w); cw = np.cumsum(w[o]); out = {}
    for lev in (0.68, 0.9):
        k = int(np.searchsorted(cw, lev)) + 1; d = np.hypot((lat[o[:k]] - la0) * 110950, (lon[o[:k]] - lo0) * 111320 * np.cos(np.radians(la0)))
        out[f"hpd{int(lev * 100)}"] = dict(area_km2=round(k * cell * cell / 1e6, 3), max_dist_m=round(float(d.max()), 0))
    return out, w

def utc(x): return float(x) if x.replace(".", "", 1).isdigit() else pd.Timestamp(x, tz="UTC").timestamp()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio"); ap.add_argument("--no-retrace", action="store_true"); ap.add_argument("--events", help="dopplerprint discover JSON (run on --audio when absent)"); ap.add_argument("--clips", help="TSV audio, dp JSON, start offset s (one recorder, one clock)")
    ap.add_argument("--adsb", required=True); ap.add_argument("--dem"); ap.add_argument("--box", help="W,S,E,N area prior (or --cells)")
    ap.add_argument("--t0", required=True, help="recording start UTC guess"); ap.add_argument("--slack", type=float, default=5.0)
    ap.add_argument("--cells"); ap.add_argument("--mass", type=float, default=0.25); ap.add_argument("--method", default="ours_planes")
    ap.add_argument("--step", type=float, default=200); ap.add_argument("--dmin", type=float, default=200); ap.add_argument("--dmax", type=float, default=10000)
    ap.add_argument("--vtol", type=float, default=1.0); ap.add_argument("--tol", type=int, default=3); ap.add_argument("--nudge", type=float, default=4)
    ap.add_argument("--temp", type=float, default=20.0); ap.add_argument("--jobs", type=int, default=8); ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--truth", help="lat,lon"); ap.add_argument("--true-t0"); ap.add_argument("--true-icao", help="comma list")
    ap.add_argument("--out", required=True); ap.add_argument("--row", help="SUBSET:recording:TASK, appends one row to --rows-file"); ap.add_argument("--rows-file")
    a = ap.parse_args(); Tst = time.time(); c = csound(a.temp); cpu0 = os.times()
    def log(*x): print(f"[{time.time() - Tst:7.1f}s]", *x, flush=True)
    js = a.events or a.clips
    if a.clips: EV = clip_events(a.clips, c, not a.no_retrace); log(f"{len(EV)} passes from the detector over {sum(1 for _ in open(a.clips))} clips ({a.clips})")
    else:
        if not js:
            js = a.out + ".dp.json"; subprocess.run(DP + ["discover", a.audio, "--out", js], check=True, stdout=subprocess.DEVNULL)
        spec = spectrogram(a.audio) if a.audio and not a.no_retrace else None
        EV = events(js, c, spec); log(f"{len(EV)} passes from the detector ({js}){'' if spec is None else ', ridges re-traced'}")
    T0g = utc(a.t0); lo, hi = T0g - a.slack, T0g + a.slack
    if a.cells:
        C = pd.read_parquet(a.cells); C = C[C.cum <= a.mass]; h = float(C.half_deg.iloc[0]); box = [C.lon.min() - h, C.lat.min() - h, C.lon.max() + h, C.lat.max() + h]
        parts = [np.meshgrid(np.arange(r.lon - h, r.lon + h, a.step / (111320 * np.cos(np.radians(r.lat)))), np.arange(r.lat - h, r.lat + h, a.step / 110950.0)) for r in C.itertuples()]
        LO, LA = np.concatenate([p[0].ravel() for p in parts]), np.concatenate([p[1].ravel() for p in parts])
    else:
        box = list(map(float, a.box.split(","))); kx = 111320 * np.cos(np.radians((box[1] + box[3]) / 2))
        la_ = np.arange(box[1], box[3] + 1e-9, a.step / 110950.0); lo_ = np.arange(box[0], box[2] + 1e-9, a.step / kx); LO, LA = np.meshgrid(lo_, la_); LA, LO = LA.ravel(), LO.ravel()
    ground = Ground(a.dem, box); kx = 111320 * np.cos(np.radians((box[1] + box[3]) / 2)); O = ecef(LA, LO, ground(LA, LO) + 2.0 + GEOID_N)
    mg = a.dmax / 110950.0 + 0.05; d = pd.read_parquet(a.adsb)
    d = d[(d.t >= lo + min(e["t"][0] for e in EV) - 200) & (d.t <= hi + max(e["t"][-1] for e in EV) + 60) & ~d.gnd.astype(bool)
          & d.lat.between(box[1] - mg, box[3] + mg) & d.lon.between(box[0] - mg * 1.4, box[2] + mg * 1.4)] if EV else d.iloc[:0]
    m = d.galt.notna() & d.alt.notna(); off = (d.galt - d.alt)[m].groupby((d.alt // 2000)[m]).median(); trk = {}
    for ic, g in d.groupby("icao"):
        if len(g) < 4: continue
        try: trk[ic] = Track(g, off.get(g.alt.median() // 2000, off.median()) if len(off) else 0.0)
        except (IndexError, ValueError): pass
    log(f"grid {len(O)} points at {a.step:.0f} m; {len(trk)} aircraft airborne near the box")
    cfg = dict(c=c, T0lo=lo, T0hi=hi, dmin=a.dmin, dmax=a.dmax, vtol=a.vtol, nudge=a.nudge, tol=a.tol, dem=a.dem, box=box, rhalf=600.0)
    M, R = {}, []
    if EV and trk:
        big = len(O) > int(os.environ.get("GEO_BIG", 200000))
        if big:
            from scipy.spatial import cKDTree
            _G.update(O=O, tree=cKDTree(O))
        with ProcessPoolExecutor(a.jobs) as ex:
            for r in ex.map(per_track, [(t, EV, None if big else O, cfg) for t in trk.values()], chunksize=1):
                if not r: continue
                for i, k, T0, rms in r[3]:
                    if len(k): M.setdefault(i, {})[r[0]] = (k, T0, rms)
    rref = np.array([max(2.0 * e["r0"], 0.8) for e in EV]); cfg["rref"] = rref
    log(f"coarse matches: {sum(len(v) for v in M.values())} (pass, aircraft) pairs over {len(M)} of {len(EV)} passes")
    J = dict(run=os.path.basename(a.out), method="ours_planes", detector="dopplerprint discover 0.1.0 (MIT, Hexay)", n_passes=len(EV), clips=a.clips, box=box, t0_prior=a.t0,
             slack_s=a.slack, adsb=a.adsb, dem=a.dem, step_m=a.step, n_points=int(len(O)))
    if a.cells:
        J.update(cells=a.cells, mass=a.mass, n_cells=int(len(C)), area_km2=float(C.km2.sum()), cells_sha256=hashlib.sha256(open(a.cells, "rb").read()).hexdigest())
        try: J["prior_variant"] = str(pd.read_parquet(os.path.join(os.path.dirname(a.cells), "index.parquet")).set_index("id").variant.get(os.path.basename(a.cells)[:-8]))
        except Exception: pass
    status = "no_pass" if not EV else "no_match"
    if M:
        S, Tb = combine(M, EV, len(O), lo, hi, a.tol, rref)
        sel = []
        for j in np.argsort(-S):
            if S[j] <= 0 or len(sel) >= a.top: break
            if all(np.hypot((LA[j] - LA[q]) * 110950, (LO[j] - LO[q]) * kx) > 1500 for q in sel): sel.append(j)
        jobs = []
        if not sel: M = {}
        for j in sel:
            pairs = []
            for i in M:
                bestg, bic = 0.0, None
                for ic, (k, T0, r) in M[i].items():
                    h = np.where((k == j) & (np.abs(T0 - Tb[j]) <= a.tol + 0.5))[0]
                    if len(h):
                        gg = EV[i]["n"] * np.log(rref[i] / r[h].min())
                        if gg > bestg: bestg, bic = gg, ic
                if bic: pairs.append((i, bic))
            jobs.append((LA[j], LO[j], float(Tb[j]), pairs, trk, EV, cfg))
    if M:
        log(f"coarse best evidence {S[sel[0]]:.1f} at {LA[sel[0]]:.4f},{LO[sel[0]]:.4f} with {len(jobs[0][3])} passes; refining {len(sel)} peaks")
        with ProcessPoolExecutor(min(a.jobs, len(jobs))) as ex: F = list(ex.map(refine_point, jobs))
        F = sorted(F, key=lambda f: -f["S"]); b = F[0]; status = "ok"
        U, w = hpd(LA, LO, S, a.step, b["lat"], b["lon"])
        g = b["grid50"]; U50, _ = hpd(g[0], g[1], g[2], 50.0, b["lat"], b["lon"]); U["local50"] = U50
        np.savez_compressed(a.out + ".grid.npz", lat=LA.astype(np.float32), lon=LO.astype(np.float32), S=S.astype(np.float32), lat50=g[0], lon50=g[1], S50=g[2],
                            peaks=np.array([[f["lat"], f["lon"], f["S"]] for f in F]))
        if a.truth:
            tl = list(map(float, a.truth.split(","))); jt = int(np.argmin(np.hypot((LA - tl[0]) * 110950, (LO - tl[1]) * kx)))
            U["truth_level_coarse"] = round(float(w[w > w[jt]].sum()), 4)
        J.update(lat=float(b["lat"]), lon=float(b["lon"]), evidence=round(float(b["S"]), 2), clock_start_unix=b["T0"] + b.get("delta", 0.0),
                 runner_up=dict(lat=float(F[1]["lat"]), lon=float(F[1]["lon"]), evidence=round(float(F[1]["S"]), 2)) if len(F) > 1 else None,
                 matched=[dict(tcpa=round(EV[i]["tcpa"], 1), f0=round(EV[i]["f0"], 1), icao=v["icao"], type=trk[v["icao"]].type, flight=trk[v["icao"]].flight,
                               rms=round(v["rms"], 3), nudge=v["nudge"]) for i, v in sorted(b["events"].items())], uncertainty=U)
        log(f"best {b['lat']:.6f},{b['lon']:.6f} evidence {b['S']:.1f} runner-up {J['runner_up']['evidence'] if J['runner_up'] else None}; {json.dumps(U)}")
        print(pd.DataFrame(J["matched"]).to_string())
        geo = at_point(b["lat"], b["lon"], b["T0"], EV, {ic: trk[ic] for ic in {v["icao"] for v in b["events"].values()}}, cfg, np.array([b.get("delta", 0.0)]))
        for x in J["matched"]:
            i = [k for k, e in enumerate(EV) if round(e["tcpa"], 1) == x["tcpa"]][0]
            x.update(slant_m=round(geo["events"].get(i, {}).get("slant_m", np.nan), 0), kind=trk[x["icao"]].kind, snr=round(float(EV[i]["snr"]), 1), dur_s=round(float(EV[i]["t"][-1] - EV[i]["t"][0]), 1))
        if a.truth:
            J["error_m"] = float(np.hypot((b["lat"] - tl[0]) * 110950, (b["lon"] - tl[1]) * 111320 * np.cos(np.radians(tl[0]))))
            J["runner_up_err_m"] = [round(float(np.hypot((f["lat"] - tl[0]) * 110950, (f["lon"] - tl[1]) * kx)), 0) for f in F[1:]]
            log(f"error {J['error_m']:.0f} m; other peaks at {J['runner_up_err_m']} m")
    if a.true_t0 and status == "ok": J["time_err_s"] = J["clock_start_unix"] - utc(a.true_t0)
    J.update(n_aircraft_near=len(trk), n_passes_detected=len(EV))
    if a.truth and EV and trk:
        tl = list(map(float, a.truth.split(","))); T0t = utc(a.true_t0) if a.true_t0 else T0g
        tp = at_point(tl[0], tl[1], T0t, EV, trk, cfg); ti = set(a.true_icao.split(",")) if a.true_icao else set()
        J["at_truth"] = dict(evidence=round(tp["S"], 2), delta=tp["delta"], passes=[dict(tcpa=round(EV[i]["tcpa"], 1), **v) for i, v in sorted(tp["events"].items())])
        tv = [v for v in tp["events"].values() if v["icao"] in ti]
        if status != "ok": why = "no pass detected" if not EV else "no aircraft fits any pass"
        elif J["error_m"] <= 1000: why = "ok"
        elif ti and not (ti & set(trk)): why = "true aircraft absent from the flight data"
        elif tp["S"] >= J["evidence"] - 1: why = "search missed a better place (truth scores as high)"
        elif ti and not tv: why = "true aircraft does not fit at the true place (ridge or flight data)"
        elif ti and any(abs(v["nudge"]) > 2.5 for v in tv): why = "clock offset in the flight data"
        elif ti & {x["icao"] for x in J.get("matched", [])}: why = "right aircraft, wrong side or along-track (single-pass ambiguity)"
        else: why = "wrong aircraft wins"
        J["diagnosis"] = why; log("diagnosis:", why, json.dumps(J["at_truth"])[:600])
    cpu1 = os.times(); J.update(status=status, runtime_s=round(time.time() - Tst, 1), cpu_s=round(sum(cpu1[:4]) - sum(cpu0[:4]), 1), cores=a.jobs, truth=a.truth,
                                code_sha256=hashlib.sha256(b"".join(open(x, "rb").read() for x in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "*.py"))))).hexdigest(),
                                written_utc=str(pd.Timestamp.now(tz="UTC")))
    json.dump(J, open(a.out + ".json", "w"), indent=1, default=float); log("wrote", a.out + ".json", status)
    if a.row:
        sub, rec, task = a.row.split(":"); tl = list(map(float, a.truth.split(","))) if a.truth else [None, None]; e = J.get("error_m")
        ti = set(a.true_icao.split(",")) if a.true_icao else None; mi = [x["icao"] for x in J.get("matched", [])]
        row = dict(method=a.method, version=J["code_sha256"][:12], subset=sub, recording=rec, task=task, status=status, error_m=e, time_err_s=J.get("time_err_s"),
                   runtime_s=J["runtime_s"], cores=a.jobs, cpu_s=J["cpu_s"], correct_aircraft=(bool(ti & set(mi)) if ti and mi else None), matched_aircraft=mi,
                   n_passes_used=len(mi), lat=J.get("lat"), lon=J.get("lon"), clock_start_unix=J.get("clock_start_unix"), truth_lat=tl[0], truth_lon=tl[1],
                   truth_t0=utc(a.true_t0) if a.true_t0 else None, prior_box=a.box, prior_start=f"-{a.slack:g},{a.slack:g}",
                   oracle=False, setup="protocol", run_id=os.path.basename(a.out), json=os.path.abspath(a.out + ".json"),
                   hpd68_km2=J.get("uncertainty", {}).get("hpd68", {}).get("area_km2"), diagnosis=J.get("diagnosis"), n_aircraft_near=J.get("n_aircraft_near"),
                   n_passes_detected=J.get("n_passes_detected"), passes=[{k: x.get(k) for k in ("icao", "type", "kind", "slant_m", "snr", "dur_s", "rms", "nudge")} for x in J.get("matched", [])], hpd90_km2=J.get("uncertainty", {}).get("hpd90", {}).get("area_km2"),
                   **{f"success_{m}m": (e is not None and e <= m) for m in (100, 500, 1000, 10000)})
        with open(a.rows_file or a.out + ".rows.jsonl", "a") as f: f.write(json.dumps(row, default=float) + "\n")

if __name__ == "__main__":
    main()
