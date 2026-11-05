#!/usr/bin/env python3
import sys, argparse, numpy as np
from scipy.optimize import least_squares

def viterbi(S, up=1, down=3, lam=0.3):
    nf, nt = S.shape; sc = S[:, 0].copy(); bp = np.zeros((nf, nt), np.int16); js = np.arange(-down, up + 1)
    for t in range(1, nt):
        cand = np.full((len(js), nf), -1e9)
        for k, d in enumerate(js):
            if d > 0: cand[k, d:] = sc[:-d]
            elif d < 0: cand[k, :d] = sc[-d:]
            else: cand[k] = sc
            cand[k] -= lam * d * d
        a = cand.argmax(0); sc = cand[a, np.arange(nf)] + S[:, t]; bp[:, t] = js[a]; sc -= sc.max()
    p = np.zeros(nt, int); p[-1] = int(sc.argmax())
    for t in range(nt - 1, 0, -1): p[t - 1] = p[t] - bp[p[t], t]
    return p

def model(par, trx, c):
    f0, v, d, tcr = par; tc = tcr - d / c
    te = np.linspace(tc - 400, tc + 400, 8001); x = v * (te - tc); r = np.hypot(d, x)
    return np.interp(trx, te + r / c, f0 * c / (c + v * x / r))

def measure(f, ts, S, t0, t1, flo, fhi, c=343.0, thr=6.0):
    k = np.where((ts >= t0) & (ts <= t1))[0]; b = np.where((f >= flo) & (f <= fhi))[0]
    sub = np.minimum(S[np.ix_(b, k)], 25.0); p = viterbi(sub)
    fr = f[b][p]; amp = sub[p, np.arange(len(k))]
    i = b[p]; lo, mi, hi = S[i - 1, k].astype(float), S[i, k].astype(float), S[i + 1, k].astype(float)
    den = lo - 2 * mi + hi; fr = fr + np.where(den < 0, 0.5 * (lo - hi) / np.where(den < 0, den, -1), 0) * (f[1] - f[0])
    m = amp > thr; tt, ff = ts[k][m], fr[m]
    x0 = [np.sqrt(ff.max() * ff.min()), 100.0, 3000.0, tt[np.argmin(np.abs(ff - np.sqrt(ff.max() * ff.min())))]]
    best = None
    for v0 in (60, 100, 140, 180):
        for d0 in (1000, 3000, 6000):
            r = least_squares(lambda q: model(q, tt, c) - ff, [x0[0], v0, d0, x0[3]], loss="soft_l1", f_scale=1.5,
                              bounds=([20, 20, 100, tt[0] - 200], [2000, 320, 30000, tt[-1] + 200]))
            if best is None or r.cost < best.cost: best = r
    J = best.jac; res = best.fun; s2 = np.sum(res ** 2) / max(1, len(res) - 4)
    try: sd = np.sqrt(np.diag(np.linalg.inv(J.T @ J) * s2))
    except np.linalg.LinAlgError: sd = np.full(4, np.nan)
    return dict(f0=best.x[0], v=best.x[1], d=best.x[2], tcpa=best.x[3], sd=sd, rms=np.sqrt(np.mean(res ** 2)), n=int(m.sum()), N=len(k), t=tt, f=ff)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("t0", type=float); ap.add_argument("t1", type=float)
    ap.add_argument("flo", type=float); ap.add_argument("fhi", type=float); ap.add_argument("--spec", required=True)
    ap.add_argument("--c", type=float, default=343.0); ap.add_argument("--plot")
    a = ap.parse_args(); z = np.load(a.spec); f, ts, S = z["f"], z["t"], z["S"].astype(np.float32)
    r = measure(f, ts, S, a.t0, a.t1, a.flo, a.fhi, a.c)
    print(f"f0 {r['f0']:.2f} Hz  v {r['v']:.1f} m/s  d {r['d']:.0f} m  t_cpa(rx) {r['tcpa']:.2f} s  sd {np.round(r['sd'], 2)}  rms {r['rms']:.2f} Hz  ridge {r['n']}/{r['N']} frames")
    if a.plot:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        k = (ts >= a.t0 - 20) & (ts <= a.t1 + 20); b = (f >= a.flo - 20) & (f <= a.fhi + 20)
        plt.figure(figsize=(12, 6)); plt.pcolormesh(ts[k], f[b], S[np.ix_(b, k)], vmin=0, vmax=25, cmap="magma", shading="auto")
        plt.plot(r["t"], r["f"], "c.", ms=2); tt = ts[k]; plt.plot(tt, model([r["f0"], r["v"], r["d"], r["tcpa"]], tt, a.c) + 4, "w--", lw=1)
        plt.title(f"f0 {r['f0']:.1f} v {r['v']:.0f} d {r['d']:.0f} tcpa {r['tcpa']:.1f}"); plt.savefig(a.plot, dpi=70)
