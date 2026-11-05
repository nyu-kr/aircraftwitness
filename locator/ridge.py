import numpy as np, soundfile as sf
from scipy.signal import stft
from scipy.optimize import least_squares
from measure import model

def spectrogram(path, win_s=1.024, hop=0.25, fmax=2500.0):
    x, fs = sf.read(path, dtype="float32", always_2d=True); x = x.mean(1); n = int(round(win_s * fs))
    f, t, Z = stft(x, fs, window="hann", nperseg=n, noverlap=n - int(round(hop * fs)), boundary=None, padded=False)
    k = f <= fmax; S = 10 * np.log10(np.abs(Z[k]) ** 2 + 1e-20)
    return f[k], t, np.clip(S - np.median(S, axis=1, keepdims=True), 0, 30).astype(np.float32)

def retrace(f, ts, S, rt, rf, harm, par, c, pad=4.0, band=0.15, step=0.1, up=1, down=3, lam=0.05, thr=3.0):
    k = np.where((ts >= rt[0] - pad) & (ts <= rt[-1] + pad))[0]; fm = model(par, ts[k], c)
    harm = [h for h in harm if h * fm.max() * (1 + band) < f[-1]] or [1]
    g = np.arange(fm.min() * (1 - band), fm.max() * (1 + band), step)
    H = np.mean([np.array([np.interp(h * g, f, S[:, j]) for j in k]).T for h in harm], axis=0)
    H[(g[:, None] < fm[None] * (1 - band)) | (g[:, None] > fm[None] * (1 + band))] = -5.0
    nf, nt = H.shape; sc = H[:, 0].copy(); bp = np.zeros((nf, nt), np.int16); js = np.arange(-down, up + 1)
    for t in range(1, nt):
        cand = np.full((len(js), nf), -1e9)
        for q, d in enumerate(js):
            if d > 0: cand[q, d:] = sc[:-d]
            elif d < 0: cand[q, :d] = sc[-d:]
            else: cand[q] = sc
            cand[q] -= lam * d * d
        a = cand.argmax(0); sc = cand[a, np.arange(nf)] + H[:, t]; bp[:, t] = js[a]; sc -= sc.max()
    p = np.zeros(nt, int); p[-1] = int(sc.argmax())
    for t in range(nt - 1, 0, -1): p[t - 1] = p[t] - bp[p[t], t]
    amp = H[p, np.arange(nt)]; lo, mi, hi = H[np.maximum(p - 1, 0), np.arange(nt)], amp, H[np.minimum(p + 1, nf - 1), np.arange(nt)]
    den = lo - 2 * mi + hi; fr = g[p] + np.clip(np.where(den < -1e-6, 0.5 * (lo - hi) / np.where(den < -1e-6, den, -1), 0), -0.5, 0.5) * step
    m = amp > thr; return ts[k][m], fr[m], amp[m]

def straight_fit(t, fr, c, x0):
    best = None; f0, tc = float(np.clip(x0[0], 11, 2999)), float(np.clip(x0[3], t[0] - 299, t[-1] + 299))
    for v0 in (x0[1], 60.0, 120.0):
        for d0 in (x0[2], 1000.0, 4000.0):
            try: r = least_squares(lambda q: model(q, t, c) - fr, [f0, float(np.clip(v0, 6, 339)), float(np.clip(d0, 31, 39999)), tc], loss="soft_l1", f_scale=1.0,
                                   bounds=([10, 5, 30, t[0] - 300], [3000, 340, 40000, t[-1] + 300]))
            except ValueError: continue
            if best is None or r.cost < best.cost: best = r
    if best is None: return None, float("inf")
    return best.x, float(np.sqrt(np.mean(best.fun ** 2)))
