#!/usr/bin/env python3

import argparse, csv, hashlib, json, statistics, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
RULE = "aircraftwitness-detection-v1"
METHOD = "dopplerprint0.1.0_discover_raw_locator_events_noretrace"
TOL_S, C_MS = 10.0, 343.0
BANDS = (("0_2km", 0.0, 2000.0), ("2_5km", 2000.0, 5000.0), ("5_10km", 5000.0, 10000.0))
FROZEN = {"locate.py": "a7b21a7da6e2bc8e13419ddf4708b6db38b3783fd449b066e4634388885923bb",
          "ridge.py": "8b2f2089a70f84379a61400beb5a2db4edfe5198cddc483cf501a7ad351f1d85",
          "measure.py": "fc0945ce71d44066e560a309d47f27bc97ece46009dff44dc646e466cfcc0817"}
SETS = {"xeno-canto": "xc", "Eclipse Soundscapes (Zenodo)": "eclipse", "Livermore": "liv",
        "Eastern North America forest soundscapes (Zenodo 20038954)": "parks", "Australian Acoustic Observatory": "a2o",
        "Locus Sonus open soundmap (live stream)": "live"}
SET_ORDER = ("a2o_b1", "a2o_b2", "a2o_b3", "eclipse", "xc", "parks", "liv", "live")
DECOY_SHIFTS_S = (-120.0, -60.0, 60.0, 120.0)   
NO_FILE = {"parks": "no_detector_file_run_for_set", "liv": "withheld_liv_no_geometric_metrics", "a2o_b3": "test_split_detector_outputs_not_released", "live": "stream_without_start_time_or_labels"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def band(d):

    for name, lo, hi in BANDS:
        if lo <= d < hi or (name == "5_10km" and d == hi):
            return name
    return None


def match(pred_t, label_t, tol=TOL_S):

    P = sorted(range(len(pred_t)), key=lambda i: (pred_t[i], i)); L = sorted(range(len(label_t)), key=lambda i: (label_t[i], i))
    n, m = len(L), len(P); best = [[(0, 0.0, None)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dt = abs(pred_t[P[j - 1]] - label_t[L[i - 1]])
            cands = [(best[i][j - 1][0], best[i][j - 1][1], "p"), (best[i - 1][j][0], best[i - 1][j][1], "l")]
            if dt <= tol: cands.append((best[i - 1][j - 1][0] + 1, best[i - 1][j - 1][1] + dt, "m"))
            best[i][j] = min(cands, key=lambda c: (-c[0], round(c[1], 9)))      # Equal matches prefer skipping a prediction, then a label, before matching.
    out, i, j = [], n, m
    while i and j:
        step = best[i][j][2]
        if step == "m": out.append((P[j - 1], L[i - 1])); i -= 1; j -= 1
        elif step == "p": j -= 1
        else: i -= 1
    return sorted(out)


def label_event(row, start_s):

    if row.get("interpolated_closest_approach_utc") and row.get("interpolated_slant_distance_m") is not None:
        t, d, basis = row["interpolated_closest_approach_utc"], row["interpolated_slant_distance_m"], "interpolated"
    else:
        t, d, basis = row["closest_approach_utc"], row["slant_distance_m"], "observed"
    return utc(t) - start_s + d / C_MS, float(d), basis


def read_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def stat(name, set_, num, den, recs, hours, shas, band_=None, unit="ratio", value=None, null_reason=None, extra=None):
    if value is None and null_reason is None and den: value = num / den
    r = dict(metric=name, set=set_, band=band_, numerator=num, denominator=den, value=value, unit=unit, recordings=recs, hours=round(hours, 6),
             rule=RULE, null_reason=null_reason, source_sha256=shas)
    return dict(r, **(extra or {}))


def evaluate(inv, rec, preds, passes, cov, shas):

    by_pred, by_lab = {}, {}
    for p in preds:
        if p["status"] == "detected": by_pred.setdefault(p["recording_id"], []).append(p)
    for p in passes: by_lab.setdefault(p["recording_id"], []).append(p)
    acc = {s: dict(rids=[], hours=0.0, lab={b: 0 for b, _, _ in BANDS}, hit={b: 0 for b, _, _ in BANDS}, err={b: [] for b, _, _ in BANDS}, un=0, npred=0,
                   over=0, outside=0, decoy=0, basis={"interpolated": 0, "observed": 0}, excl={}) for s in SET_ORDER}
    for rid, v in inv.items():
        s = v["set"]; a = acc[s]
        if v["detector_status"] != "present": continue
        c = cov.get(rid)
        why = "no_coverage_row" if c is None else None if c["cut_status"] == "present" else f"cut_{c['cut_status']}"
        if not why and utc(c["recording_start_utc"]) != utc(rec[rid]["utc_start"]): why = "recording_start_mismatch"
        if why: a["excl"][why] = a["excl"].get(why, 0) + 1; continue
        start, dur = utc(rec[rid]["utc_start"]), float(rec[rid]["duration_s"])
        labs = [label_event(p, start) for p in by_lab.get(rid, [])]; ps = by_pred.get(rid, [])
        pairs = match([p["t_cpa_arrive_s"] for p in ps], [l[0] for l in labs])
        a["decoy"] += sum(len(match([p["t_cpa_arrive_s"] for p in ps], [l[0] + sh for l in labs if band(l[1])])) for sh in DECOY_SHIFTS_S)
        a["rids"].append(rid); a["hours"] += dur / 3600; a["npred"] += len(ps); a["un"] += len(ps) - len(pairs)
        hit = {li: pi for pi, li in pairs}
        for li, (t, d, basis) in enumerate(labs):
            a["basis"][basis] += 1; a["outside"] += not 0 <= t <= dur
            b = band(d)
            if b is None: a["over"] += 1; continue
            a["lab"][b] += 1
            if li in hit: a["hit"][b] += 1; a["err"][b].append(ps[hit[li]]["slant_distance_m"] - d)
    out = {}
    for s in (*SET_ORDER, "combined"):
        members = [x for x in SET_ORDER if x not in ("parks", "liv")] if s == "combined" else [s]
        use = [x for x in members if acc[x]["rids"]]
        reason = None
        if s in NO_FILE: reason = NO_FILE[s]
        elif not use: reason = "no_evaluable_recordings"
        sh = dict(shas, detector_files=shas["detector_files"].get(s) if s != "combined" else shas["detector_files"])
        if reason:
            names = [(f"recall_{b}", b) for b, _, _ in BANDS] + [("recall_all", "all"), ("decoy_recall_all", "all"), ("unmatched_per_hour", None), ("median_abs_error_m", "all"), ("mae_m", "all"), ("median_signed_error_m", "all")]
            out[s] = {k: stat(k, s, None, None, 0, 0.0, sh, band_=b, null_reason=reason) for k, b in names}
            continue
        recs, hrs = sum(len(acc[x]["rids"]) for x in use), sum(acc[x]["hours"] for x in use)
        extra = dict(recordings_in_set=sum(1 for v in inv.values() if v["set"] in members), excluded=dict(sum((Counter(acc[x]["excl"]) for x in members), Counter())),
                     labels_beyond_10km=sum(acc[x]["over"] for x in use), labels_received_outside_audio=sum(acc[x]["outside"] for x in use),
                     label_basis={k: sum(acc[x]["basis"][k] for x in use) for k in ("interpolated", "observed")},
                     nominal_time_precision_s_max=max((float(rec[r]["time_precision_s"]) for x in use for r in acc[x]["rids"] if rec[r]["time_precision_s"]), default=None))
        m = {}
        tot_l = tot_h = 0
        for b, _, _ in BANDS:
            l, h = sum(acc[x]["lab"][b] for x in use), sum(acc[x]["hit"][b] for x in use); tot_l += l; tot_h += h
            m[f"recall_{b}"] = stat(f"recall_{b}", s, h, l, recs, hrs, sh, b, extra=extra, null_reason=None if l else "no_labels_in_band")
        m["recall_all"] = stat("recall_all", s, tot_h, tot_l, recs, hrs, sh, "all", extra=extra, null_reason=None if tot_l else "no_labels")
        m["decoy_recall_all"] = stat("decoy_recall_all", s, sum(acc[x]["decoy"] for x in use), tot_l * len(DECOY_SHIFTS_S), recs, hrs, sh, "all", extra=dict(
            extra, note=f"post-hoc chance control: same matching with every label time shifted by {list(DECOY_SHIFTS_S)} s, pooled", shifts_s=list(DECOY_SHIFTS_S)), null_reason=None if tot_l else "no_labels")
        un = sum(acc[x]["un"] for x in use)
        m["unmatched_per_hour"] = stat("unmatched_per_hour", s, un, round(hrs, 6), recs, hrs, sh, unit="per_hour", extra=dict(
            extra, note="proxy: unmatched detections against available ADS-B labels within 10 km, not acoustically proven false passes", detections=sum(acc[x]["npred"] for x in use)))
        errs = [e for x in use for b, _, _ in BANDS for e in acc[x]["err"][b]]
        for name, fn in (("median_abs_error_m", lambda e: statistics.median(abs(v) for v in e)), ("mae_m", lambda e: sum(abs(v) for v in e) / len(e)),
                         ("median_signed_error_m", statistics.median)):
            m[name] = stat(name, s, len(errs), len(errs), recs, hrs, sh, "all", unit="m", value=fn(errs) if errs else None, null_reason=None if errs else "no_matched_events", extra=extra)
        out[s] = m
    return out


def summarize(by, shas):
    by = dict(by)
    return dict(benchmark="AircraftWitness", task="aircraft pass detection and closest-approach distance from audio", method=METHOD, rule=RULE, tolerance_s=TOL_S,
                speed_of_sound_m_s=C_MS, labels_are="ADS-B-relative proxy, not definitive audible ground truth", source_sha256=shas, combined=by.pop("combined"), by_set=by)


def load_frozen(bindir):
    for n, h in FROZEN.items():
        if sha(Path(bindir) / n) != h: sys.exit(f"{n} sha differs from the frozen {h}")
    sys.path.insert(0, str(bindir))
    from ridge import straight_fit; from measure import model
    return straight_fit, model


def events(js, straight_fit, model, c=C_MS, vmin=3.0, vmax=330.0, min_len=6.0, min_drop=1.01):


    import numpy as np
    raw = []
    for e in js:
        b = e["fit"]["best"]; h = e.get("ridge_harmonic", 1) or 1
        raw.append(dict(id=e["id"], t=np.asarray(e["ridge"]["t"], float), f=np.asarray(e["ridge"]["f_hz"], float) / h, par=[b["f0"] / h, b["v"], b["d_cpa"], b["t_cpa_arrive"]], merged=False))
    raw.sort(key=lambda r: r["t"][0]); mer = []
    for r in raw:
        q = mer[-1] if mer else None
        if q is not None and 0 <= r["t"][0] - q["t"][-1] <= 3 and abs(r["f"][0] - q["f"][-1]) <= 0.06 * q["f"][-1] and len(q["t"]) > 4 and len(r["t"]) > 4:
            q["t"], q["f"] = np.concatenate([q["t"], r["t"]]), np.concatenate([q["f"], r["f"]]); q["merged"] = True
        else: mer.append(r)
    out = []
    for e in mer:
        t, f, par = e["t"], e["f"], e["par"]
        if e["merged"]:
            par, _ = straight_fit(t, f, c, [np.median(f), 80.0, 2000.0, t[len(t) // 2]])
            if par is None: continue
        if not (vmin <= par[1] <= vmax) or t[-1] - t[0] < min_len or np.percentile(f, 90) / np.percentile(f, 10) < min_drop or np.polyfit(t, f, 1)[0] >= 0: continue
        out.append(dict(id=e["id"], tcpa=float(par[3]), d=float(par[2]), t=t))
    out.sort(key=lambda e: -(e["t"][-1] - e["t"][0])); keep = []
    for e in out:
        if any(abs(e["tcpa"] - k["tcpa"]) < 4 and min(e["t"][-1], k["t"][-1]) - max(e["t"][0], k["t"][0]) > 0.5 * (e["t"][-1] - e["t"][0]) for k in keep): continue
        keep.append(e)
    return [(e["id"], e["tcpa"], e["d"]) for e in sorted(keep, key=lambda e: e["tcpa"])]


def detector_files(work, recs):

    g = work / "geoloc" / "data"; found = {}
    for b, d in (("1", "a2o"), ("2", "a2o_b2")):
        for r in csv.DictReader(open(work / "verify/dataset_public/a2o_packet_map.csv")):
            p = g / d / "answers/T1" / f"{r['packet']}.dp.json"
            if r["batch"] == b and p.exists(): found[r["recording_id"]] = (f"a2o_b{b}", p)
    for p in (g / "ecl/clips").glob("*.dp.json"): found[p.name[:-8]] = ("eclipse", p)
    for grp, pat in (("xc_t1y_T1", "xc/t1y/answers/T1/{}.dp.json"), ("xc_t1y_T1b", "xc/t1y/answers/T1b/{}.dp.json"), ("xc_pc1_out1", "bench/pc1_out1/XC_{}_T1.dp.json")):
        for rid, r in recs.items():
            if r["source"] == "xeno-canto" and rid not in found and (g / pat.format(rid)).exists(): found[rid] = (grp, g / pat.format(rid))
    return found


def extract(a):
    recs = {r["recording_id"]: r for r in csv.DictReader(open(a.recordings))}
    straight_fit, model = load_frozen(a.frozen)
    files = detector_files(WORK, recs); inv, preds = [], []
    b3 = set(json.loads((ROOT / "data/task_cohorts.json").read_text())["A2O batch 3"])
    for rid, r in recs.items():
        s = SETS[r["source"]]; hit = files.get(rid)
        if s == "a2o": s = hit[0] if hit else "a2o_b3" if rid in b3 else "a2o_b1"
        if s in ("a2o_b3", "live"): hit = None
        row = dict(recording_id=rid, set=s, detector_status="present", null_reason=None, file_group=None, file_sha256=None, n_events_raw=None, n_events=None)
        if s == "liv": row.update(detector_status="withheld", null_reason=NO_FILE["liv"])
        elif not hit: row.update(detector_status="missing", null_reason=NO_FILE.get(s, "no_detector_file_for_recording"))
        else:
            js = json.loads(hit[1].read_text()); ev = events(js, straight_fit, model)
            row.update(file_group=hit[0], file_sha256=sha(hit[1]), n_events_raw=len(js), n_events=len(ev))
            preds += [dict(method=METHOD, set=s, recording_id=rid, event_id=f"d{i}", t_cpa_arrive_s=round(t, 3), slant_distance_m=round(d, 1), status="detected") for i, t, d in ev]
            if not ev: preds.append(dict(method=METHOD, set=s, recording_id=rid, event_id=None, t_cpa_arrive_s=None, slant_distance_m=None, status="no_events"))
        inv.append(row)
    for name, rows in (("detection_predictions.jsonl", preds), ("detection_inventory.jsonl", inv)):
        (Path(a.out_dir) / name).write_text("".join(json.dumps(x, sort_keys=False) + "\n" for x in rows))
    k = next(p for p in preds if p["status"] == "detected"); src = next(i for i in inv if i["recording_id"] == k["recording_id"])
    print(json.dumps(k), "source", src["file_group"], src["file_sha256"][:16], f"predictions={len(preds)} recordings={len(inv)} present={sum(i['detector_status'] == 'present' for i in inv)}")


def score(a):
    miss = [p for p in (a.labels, a.coverage) if not Path(p).exists()]
    if miss:
        sys.exit(f"labels not available: {', '.join(Path(p).name for p in miss)}; run: python3 -I tools/detection_benchmark.py evaluate --labels {a.labels} --coverage {a.coverage}")
    d = Path(a.out_dir); inv = {r["recording_id"]: r for r in read_jsonl(d / "detection_inventory.jsonl")}
    rec = {r["recording_id"]: r for r in csv.DictReader(open(a.recordings))}
    cov = {r["recording_id"]: r for r in read_jsonl(a.coverage)}; passes = read_jsonl(a.labels)
    n = Counter(p["recording_id"] for p in passes)
    bad = [r for r, c in cov.items() if n[r] != int(c["pass_count"])] + [r for r in n if r not in cov]
    if bad: sys.exit(f"passes rows differ from coverage pass_count for {len(bad)} recordings, first {bad[0]}")
    files = {}
    for r in inv.values():
        if r["file_sha256"]: files.setdefault(r["set"], []).append(f"{r['recording_id']}:{r['file_sha256']}")
    fsha = {s: hashlib.sha256("\n".join(sorted(v)).encode()).hexdigest() for s, v in files.items()}
    fsha_all = hashlib.sha256("\n".join(sorted(x for v in files.values() for x in v)).encode()).hexdigest()
    shas = dict(predictions=sha(d / "detection_predictions.jsonl"), inventory=sha(d / "detection_inventory.jsonl"), passes=sha(a.labels), coverage=sha(a.coverage),
                recordings=sha(a.recordings), detector_files=dict(fsha, combined=fsha_all))
    summ = summarize(evaluate(inv, rec, read_jsonl(d / "detection_predictions.jsonl"), passes, cov, shas), dict(shas))
    (d / "detection_summary.json").write_text(json.dumps(summ, indent=1) + "\n")
    print({k: (v["numerator"], v["denominator"], v["value"], v["null_reason"]) for k, v in summ["combined"].items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__); sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("extract", "evaluate"):
        p = sub.add_parser(n); p.add_argument("--recordings", default=ROOT / "data/recordings.csv"); p.add_argument("--out-dir", default=ROOT / "data")
        if n == "extract": p.add_argument("--frozen", default=WORK / "frozen/a2o-v1/bin")
        else: p.add_argument("--labels", default=ROOT / "data/passes.jsonl"); p.add_argument("--coverage", default=ROOT / "data/coverage.jsonl")
    a = ap.parse_args(); extract(a) if a.cmd == "extract" else score(a)


if __name__ == "__main__":
    main()
