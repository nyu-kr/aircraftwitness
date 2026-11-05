#!/usr/bin/env python3
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'ok', 'n/a', 'no_pass', 'no_match', 'fail', 'timeout'}


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: expected a finite numeric value')
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f'{field}: expected a finite number')
    return v


def distance(lat, lon, reference):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('prediction outside latitude/longitude range')
    p, q = math.radians(lat), math.radians(float(reference['public_latitude']))
    d = math.sin((p - q) / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(math.radians(lon - float(reference['public_longitude'])) / 2) ** 2
    return 12742017.6 * math.asin(math.sqrt(min(1, max(0, d))))


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def square_centres(data):   
    return [dict(method='centre', version='square-centre', set='A2O batch 3', task='T1', recording_id=r['recording_id'], status='ok', latitude=(float(r['lat_min']) + float(r['lat_max'])) / 2, longitude=(float(r['lon_min']) + float(r['lon_max'])) / 2) for r in read_csv(data / 'b3_blind_squares.csv') if r['task'] == 'T1']


def score(rows, data):
    if not rows:
        raise ValueError('empty answer file')
    records = {r['recording_id']: r for r in read_csv(data / 'recordings.csv')}
    cohorts = json.loads((data / 'task_cohorts.json').read_text())
    clocks = {(r['scenario'], r['recording_id']): float(r['reference_error_s']) for r in read_csv(data / 'clock_tasks.csv')}
    claims = {r['recording_id']: r['verdict'] for r in read_csv(data / 'claim_check_answers.csv')}
    verdicts = set(claims.values())
    required = {'method', 'version', 'set', 'task', 'recording_id', 'status'}
    allowed = required | {'latitude', 'longitude', 'scenario', 'predicted_clock_error_s', 'verdict'}
    groups = defaultdict(list)
    for r in rows:
        if not isinstance(r, dict) or set(r) - allowed:
            raise ValueError('answer must be an object with schema-defined fields')
        for k in sorted(required):
            if not isinstance(r.get(k), str) or not r[k]:
                raise ValueError(f'missing string field: {k}')
        if r['status'] not in STATUSES:
            raise ValueError('unknown answer status: ' + r['status'])
        for key in ('latitude', 'longitude', 'predicted_clock_error_s'):
            if key in r:
                value = number(r[key], key)
                bound = {'latitude': 90, 'longitude': 180}.get(key)
                if bound is not None and not -bound <= value <= bound:
                    raise ValueError(f'{key}: outside geographic range')
        if 'scenario' in r and r['task'] != 'clock':
            raise ValueError('scenario is only defined for clock answers')
        if 'scenario' in r and r['scenario'] not in ('native', 'am_like', 'fast_neg', 'blind_fast'):
            raise ValueError('unknown clock scenario')
        if 'verdict' in r and (not isinstance(r['verdict'], str) or r['verdict'] not in verdicts):
            raise ValueError('unknown claim-check verdict')
        if r['task'] == 'claim-check' and r['status'] == 'ok' and 'verdict' not in r:
            raise ValueError('claim-check requires verdict')
        groups[(r['method'], r['version'], r['set'], r['task'], r.get('scenario', ''))].append(r)
    output = []
    for (method, version, subset, task, scenario), answers in sorted(groups.items()):
        ids = [r['recording_id'] for r in answers]
        if len(ids) != len(set(ids)):
            raise ValueError(f'{method}/{subset}/{task}: duplicate recording answer')
        if task == 'clock':
            expected = {rid for sc, rid in clocks if sc == scenario}
            if subset != 'Livermore' or not expected:
                raise ValueError('clock requires set Livermore and a released scenario')
        elif task == 'claim-check':
            if subset != 'xeno-canto archive':
                raise ValueError('claim-check requires set xeno-canto archive')
            expected = set(claims)
        elif task in ('T1', 'T2-date'):
            if task == 'T2-date' and subset not in ('A2O batch 3', 'A2O batch 1', 'Eclipse clips'):
                raise ValueError('T2-date requires the A2O batch 3, A2O batch 1 or Eclipse clips cohort')
            if subset not in cohorts or subset == 'xeno-canto archive':
                raise ValueError('T1 set has no released location-scoring cohort: ' + subset)
            expected = set(cohorts[subset])
        else:
            raise ValueError('unsupported task; pass detection uses tools/detection_benchmark.py')
        if not expected or set(ids) - expected:
            raise ValueError(f'{subset}: empty reference or answer outside fixed cohort')
        ok = [r for r in answers if r['status'] == 'ok']
        metrics = dict(expected=len(expected), submitted=len(answers), answered=len(ok), missing=len(expected - set(ids)), failed_or_withheld=len(answers) - len(ok))
        if task in ('T1', 'T2-date'):
            errors = []
            for r in ok:
                ref = records[r['recording_id']]
                if not ref['public_latitude'] or not ref['public_longitude']:
                    raise ValueError('reference place withheld; no run-time fit is silently substituted')
                errors.append(distance(number(r.get('latitude'), 'latitude'), number(r.get('longitude'), 'longitude'), ref))
            metrics.update({f'hit_{radius}m': sum(e <= radius for e in errors) for radius in [100, 500, 1000]})
            metrics['median_error_m_answered'] = statistics.median(errors) if errors else None
            metrics['reference'] = 'source-published rounded place, not necessarily exact ground truth; all missing cases are misses'
        elif task == 'clock':
            errors = [number(r.get('predicted_clock_error_s'), 'predicted_clock_error_s') - clocks[scenario, r['recording_id']] for r in ok]
            metrics['offset_rms_s_answered'] = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None
            metrics['scenario'] = scenario
            metrics['reference'] = 'original injected clock errors at 38 alignment-supported clip starts; missing results reported separately'
        else:
            metrics['agreement'] = sum(r.get('verdict') == claims[r['recording_id']] for r in ok)
            metrics['reference'] = 'agreement with released project decisions, not independent proof of claim veracity'
        output.append(dict(method=method, version=version, set=subset, task=task, scenario=scenario, metrics=metrics))
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument('answers', type=Path)
    p.add_argument('--data', type=Path, default=ROOT / 'data')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    content = a.answers.read_bytes()
    rows = [json.loads(s) for s in content.decode('utf-8').splitlines() if s.strip()]
    result = dict(benchmark='AircraftWitness', answer_sha256=hashlib.sha256(content).hexdigest(), results=score(rows, a.data))
    text = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if a.output:
        with a.output.open('x') as h:
            h.write(text)
    print(text, end='')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'score failed: {error}', file=sys.stderr)
        sys.exit(1)
