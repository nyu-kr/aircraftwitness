#!/usr/bin/env python3
# e2e_score.py ROWS RESULTS ALIASES: reruns the e2e study's scoring from its per-recording rows and checks it against the aggregate.
# The scoring functions are the frozen scorer's (sha256 6f22cff344ef...), unchanged; its custody checks of the private inputs are left out.
# Rows must stay in sample order: the recording bootstrap resamples by row position.
from collections import Counter
import csv
import json
import math
import sys
import numpy as np

ENTRANTS = {'ref': 'reference', 'dp': 'dopplerprint', 'unh': 'unheard'}
STATES = {'prep_failure', 'missing', 'late', 'invalid_or_incomplete', 'invalid_on_independent_readback'}


def score_upper(n):
    z2 = 1.959963984540054 ** 2
    return z2 / (n + z2) if n else None


def bootstrap_counts(labels, seed):
    labels = np.asarray(labels)
    unique, inverse = np.unique(labels, return_inverse=True)
    k = len(unique)
    rng = np.random.default_rng(seed)
    counts = np.empty((10000, k), dtype=float)
    for start in range(0, 10000, 250):
        indices = rng.integers(0, k, (min(250, 10000 - start), k))
        for j, row in enumerate(indices):
            counts[start + j] = np.bincount(row, minlength=k)
    return inverse, counts


def boot_ratios(values, denominator, plan):
    inverse, counts = plan
    k = counts.shape[1]
    h = np.bincount(inverse, weights=values, minlength=k)
    n = np.bincount(inverse, weights=denominator, minlength=k)
    bh, bn = counts @ h, counts @ n
    positive = bn > 0
    ratios = bh[positive] / bn[positive]
    ci = [float(x) for x in np.percentile(ratios, [2.5, 97.5])] if len(ratios) else [None, None]
    return ci, int(positive.sum()), float(np.var(ratios, ddof=1)) if len(ratios) > 1 else None


def metric(hit, answered, mask, weights, plans, statuses=None):
    n = int(mask.sum())
    hits = int(hit[mask].sum())
    denom = weights * mask
    value = weights * hit * mask
    total = float(denom.sum())
    out = {'hits': hits, 'n': n, 'answered': int(answered[mask].sum()), 'rate': float(value.sum() / total) if total else None, 'unweighted_rate': hits / n if n else None, 'p': None, 'weighted_denominator': total, 'ci95': [None, None], 'diagnostic_intervals': {}}
    if statuses is not None:
        out['status_counts'] = dict(Counter(statuses[mask]))
    out['draws'] = len(np.unique(plans['day_draw'][0][mask]))
    out['published_point_groups'] = len(np.unique(plans['published_point_cluster'][0][mask]))
    if not n:
        out['domain_status'] = 'no_data'
        return out
    variances = {}
    for name, plan in plans.items():
        ci, valid, variance = boot_ratios(value, denom, plan)
        variances[name] = variance
        info = {'ci95': ci, 'valid_resamples': valid, 'resamples': 10000, 'bootstrap_variance': variance}
        if name == 'day_draw':
            out['bootstrap_ci95'] = ci
            out['ci95'] = ci
        else:
            out['diagnostic_intervals'][name] = info
    if hits == 0:
        out['ci95'] = [None, None]
        out['zero_hit_bootstrap_uninformative'] = True
        out['binomial_score_upper95_diagnostic'] = {'upper': score_upper(n), 'n': n, 'assumption': 'independent_binomial_recordings', 'meaning': 'upper_endpoint_of_two_sided_95_percent_score_interval_not_a_design_or_cluster_interval'}
        for info in out['diagnostic_intervals'].values():
            info['degenerate_zero_hit'] = True
    record_var = variances['recording']
    out['day_draw_to_recording_bootstrap_variance_ratio'] = variances['day_draw'] / record_var if record_var else None
    out['achieved_ci95_width'] = out['ci95'][1] - out['ci95'][0] if out['ci95'][0] is not None else None
    out['domain_status'] = 'sparse' if n < 30 else 'observed'
    return out



def same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def main():
    rows = list(csv.DictReader(open(sys.argv[1])))
    expected, aliases = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    ids = list(dict.fromkeys(r['packet'] for r in rows))
    first = {r['packet']: r for r in reversed(rows)}
    keyed = {(r['packet'], r['entrant'], r['arm']): r for r in rows}
    n = len(ids)
    assert n == 1600 and len(keyed) == len(rows) == 9 * n
    col = lambda k: np.array([first[p][k] for p in ids])
    weights = 1 / col('draw_size').astype(float)
    detector, source, precision = col('detector'), col('source'), col('precision_m').astype(float)
    plans = {'day_draw': bootstrap_counts(col('draw').astype(int), 20261105), 'recording': bootstrap_counts(np.arange(n), 20261106),
             'published_point_cluster': bootstrap_counts(col('place_group'), 20261107)}
    bad = []
    for name in ENTRANTS.values():
        for arm in 'PWA':
            rs = [keyed[(p, name, arm)] for p in ids]
            hits = np.array([r['hit'] == '1' for r in rs]); answers = np.array([r['answer_latitude'] != '' for r in rs])
            status_vector = np.array([r['status'] for r in rs], dtype=object)
            processing = Counter(s if s in STATES else 'terminal' for s in status_vector)
            overall = metric(hits, answers, np.ones(n, dtype=bool), weights, plans, status_vector)
            overall['processing_counts'] = dict(processing)
            overall['status_counts'] = dict(Counter(status_vector))
            overall['by_detector'] = {v: metric(hits, answers, detector == v, weights, plans, status_vector) for v in ('positive', 'negative', 'unknown')}
            overall['by_source'] = {}
            for s, alias in aliases.items():
                mask = source == s
                overall['by_source'][alias] = metric(hits, answers, mask, weights, plans, status_vector)
                overall['by_source'][alias]['detector_positive'] = metric(hits, answers, mask & (detector == 'positive'), weights, plans, status_vector)
            overall['by_precision'] = {'over_1km': metric(hits, answers, precision > 1000, weights, plans, status_vector), 'at_most_1km': metric(hits, answers, precision <= 1000, weights, plans, status_vector)}
            if not same(overall, expected['entrants'][name][arm]):
                bad.append(f'{name}/{arm}')
    if bad:
        sys.exit('e2e_score: differs from ' + sys.argv[2] + ': ' + ', '.join(bad))
    print(f'e2e: {len(rows)} rows rescored, 9 entrant-arms match {sys.argv[2]}')


if __name__ == '__main__':
    main()
