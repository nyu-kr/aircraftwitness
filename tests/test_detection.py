import csv
import importlib.util
import itertools
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = Path(os.environ.get('DETECTION_MODULE', ROOT / 'tools' / 'detection_benchmark.py'))
spec = importlib.util.spec_from_file_location('detection_benchmark', MODULE)
det = importlib.util.module_from_spec(spec)
spec.loader.exec_module(det)
FROZEN_BIN = ROOT.parent / 'frozen' / 'a2o-v1' / 'bin'
REAL = (ROOT / 'data' / 'detection_inventory.jsonl').exists() and FROZEN_BIN.exists()
START = '2025-01-01T00:00:00Z'
SHAS = dict(predictions='p', inventory='i', passes='l', coverage='c', recordings='r', detector_files={})


def utc_at(sec):
    return f'2025-01-01T{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}Z'


def lab(rid, cpa_s, slant, **kw):
    return dict(dict(interpolated_closest_approach_utc=None, interpolated_slant_distance_m=None), recording_id=rid, closest_approach_utc=utc_at(cpa_s), slant_distance_m=slant, **kw)


def pred(rid, t, d, set_='xc'):
    return dict(method='m', set=set_, recording_id=rid, event_id=f'd{t}', t_cpa_arrive_s=t, slant_distance_m=d, status='detected')


def world(recs):

    inv = {r: dict(recording_id=r, set=s, detector_status=ds) for r, (s, ds, cs, du) in recs.items()}
    rec = {r: dict(recording_id=r, utc_start=START, duration_s=str(du), time_precision_s='1') for r, (s, ds, cs, du) in recs.items()}
    cov = {r: dict(recording_id=r, cut_status=cs, recording_start_utc=START, pass_count=0) for r, (s, ds, cs, du) in recs.items()}
    return inv, rec, cov


def brute(pt, lt, tol):
    best = (0, 0.0)
    def go(i, used, n, c):
        nonlocal best
        if i == len(pt):
            if (n, -c) > (best[0], -best[1]): best = (n, c)
            return
        go(i + 1, used, n, c)
        for j, l in enumerate(lt):
            if j not in used and abs(pt[i] - l) <= tol: go(i + 1, used | {j}, n + 1, c + abs(pt[i] - l))
    go(0, frozenset(), 0, 0.0)
    return best


class Matching(unittest.TestCase):
    def test_matches_brute_force_cardinality_and_cost(self):
        rng = random.Random(7)
        for _ in range(300):
            pt = [rng.uniform(0, 40) for _ in range(rng.randint(0, 5))]; lt = [rng.uniform(0, 40) for _ in range(rng.randint(0, 5))]
            pairs = det.match(pt, lt, 10.0)
            self.assertEqual(len({p for p, _ in pairs}), len(pairs)); self.assertEqual(len({l for _, l in pairs}), len(pairs))
            self.assertTrue(all(abs(pt[p] - lt[l]) <= 10.0 for p, l in pairs))
            n, c = brute(pt, lt, 10.0)
            self.assertEqual(len(pairs), n); self.assertAlmostEqual(sum(abs(pt[p] - lt[l]) for p, l in pairs), c, places=6)

    def test_no_duplicate_matching(self):
        self.assertEqual(len(det.match([100.0, 101.0], [100.5], 10.0)), 1)
        self.assertEqual(len(det.match([100.5], [100.0, 101.0], 10.0)), 1)
        self.assertEqual(len(det.match([100.0, 100.0], [100.0, 100.0], 10.0)), 2)

    def test_tolerance_inclusive_and_deterministic(self):
        self.assertEqual(det.match([110.0], [100.0]), [(0, 0)]); self.assertEqual(det.match([110.001], [100.0]), [])
        self.assertEqual(det.match([100.0, 100.0], [100.0]), det.match([100.0, 100.0], [100.0])); self.assertEqual(det.match([100.0, 100.0], [100.0]), [(0, 0)])

    def test_maximum_cardinality_beats_greedy_nearest(self):
        self.assertEqual(len(det.match([10.0, 20.0], [20.0, 29.0])), 2)


class Bands(unittest.TestCase):
    def test_boundaries(self):
        for d, b in ((0, '0_2km'), (1999.9, '0_2km'), (2000, '2_5km'), (4999.9, '2_5km'), (5000, '5_10km'), (10000, '5_10km'), (10000.1, None)):
            self.assertEqual(det.band(d), b, d)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.recs = {'R1': ('xc', 'present', 'present', 3600), 'R2': ('eclipse', 'present', 'present', 1800), 'R3': ('xc', 'missing', 'present', 3600),
                     'R4': ('eclipse', 'present', 'missing', 3600), 'P1': ('parks', 'missing', 'present', 600), 'L1': ('liv', 'withheld', 'present', 600)}
        self.inv, self.rec, self.cov = world(self.recs)
        self.passes = [lab('R1', 100, 1000.0), lab('R1', 200, 3430.0), lab('R1', 300, 6860.0), lab('R1', 400, 2000.0),   
                       lab('R2', 50, 800.0), lab('R3', 100, 500.0), lab('R4', 100, 500.0), lab('L1', 100, 500.0), lab('P1', 100, 500.0)]
        self.preds = [pred('R1', 103.0, 1100.0), pred('R1', 205.0, 3000.0), pred('R1', 300.0, 5000.0), pred('R1', 500.0, 9000.0),
                      pred('R2', 52.0, 900.0, 'eclipse'), pred('R3', 100.0, 500.0), pred('R4', 100.0, 500.0), pred('L1', 100.0, 500.0, 'liv')]

    def run_eval(self, **kw):
        a = dict(inv=self.inv, rec=self.rec, preds=self.preds, passes=self.passes, cov=self.cov); a.update(kw)
        return det.evaluate(a['inv'], a['rec'], a['preds'], a['passes'], a['cov'], SHAS)

    def test_hand_computed_values(self):
        r1 = self.run_eval()['xc']                    
        self.assertEqual([(r1[k]['numerator'], r1[k]['denominator']) for k in ('recall_0_2km', 'recall_2_5km', 'recall_5_10km', 'recall_all')], [(1, 1), (1, 2), (0, 1), (2, 4)])
        self.assertEqual((r1['unmatched_per_hour']['numerator'], r1['unmatched_per_hour']['value']), (2, 2.0))
        self.assertEqual((r1['median_abs_error_m']['value'], r1['mae_m']['value'], r1['median_signed_error_m']['value']), (265.0, 265.0, -165.0))
        self.assertEqual((r1['recall_all']['recordings'], r1['recall_all']['hours']), (1, 1.0))
        self.assertEqual(r1['recall_all']['rule'], det.RULE); self.assertEqual(r1['recall_all']['source_sha256']['inventory'], 'i')

    def test_combined_adds_sets_not_missing_cuts(self):
        c = self.run_eval()['combined']
        self.assertEqual((c['recall_all']['numerator'], c['recall_all']['denominator'], c['recall_all']['recordings']), (3, 5, 2))
        self.assertAlmostEqual(c['recall_all']['hours'], 1.5); self.assertEqual(c['recall_all']['excluded'], {'cut_missing': 1})
        self.assertEqual(c['unmatched_per_hour']['numerator'], 2)

    def test_time_of_flight_in_label_time(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        for t, hit in ((120.0, 1), (100.0, 0)):       # Sound travel time shifts the received time after closest approach.
            r = det.evaluate(inv, rec, [pred('R', t, 6860.0)], [lab('R', 100, 6860.0)], cov, SHAS)['xc']
            self.assertEqual(r['recall_all']['numerator'], hit, t)

    def test_speed_of_sound_boundary(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        label = lab('R', 0, 3430.0)
        self.assertEqual(det.label_event(label, det.utc(START))[0], 10.0)
        result = det.evaluate(inv, rec, [pred('R', 0.05, 3430.0)], [label], cov, SHAS)['xc']
        self.assertEqual(result['recall_all']['numerator'], 1)

    def test_decoy_control_hand_computed(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        r = det.evaluate(inv, rec, [pred('R', 380.0, 100.0)], [lab('R', 300, 6860.0), lab('R', 900, 12000.0)], cov, SHAS)['xc']    
        self.assertEqual((r['recall_all']['numerator'], r['decoy_recall_all']['numerator'], r['decoy_recall_all']['denominator']), (0, 1, 4))
        r = det.evaluate(inv, rec, [pred('R', 320.0, 100.0)], [lab('R', 300, 6860.0)], cov, SHAS)['xc']
        self.assertEqual((r['recall_all']['numerator'], r['decoy_recall_all']['numerator']), (1, 0))

    def test_interpolated_label_used_when_valid(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        p = lab('R', 100, 1000.0, interpolated_closest_approach_utc=utc_at(130), interpolated_slant_distance_m=3430.0)
        r = det.evaluate(inv, rec, [pred('R', 140.0, 3000.0)], [p], cov, SHAS)['xc']      
        self.assertEqual((r['recall_2_5km']['numerator'], r['recall_0_2km']['denominator'], r['recall_all']['label_basis']), (1, 0, {'interpolated': 1, 'observed': 0}))

    def test_band_boundary_in_scoring(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        ps = [lab('R', 100, 2000.0), lab('R', 200, 5000.0), lab('R', 300, 10000.0)]
        r = det.evaluate(inv, rec, [], ps, cov, SHAS)['xc']
        self.assertEqual([r[k]['denominator'] for k in ('recall_0_2km', 'recall_2_5km', 'recall_5_10km')], [0, 1, 2])
        self.assertEqual(r['recall_0_2km']['null_reason'], 'no_labels_in_band'); self.assertIsNone(r['recall_0_2km']['value'])

    def test_liv_and_parks_are_null_even_with_labels_and_predictions(self):
        out = self.run_eval()
        for s, why in (('liv', 'withheld_liv_no_geometric_metrics'), ('parks', 'no_detector_file_run_for_set')):
            self.assertTrue(all(m['value'] is None and m['null_reason'] == why for m in out[s].values()), s)
        self.assertNotIn('L1', json.dumps(out['combined']))

    def test_nothing_evaluable_is_null_never_green(self):
        inv, rec, cov = world({'R': ('xc', 'missing', 'present', 3600), 'S': ('eclipse', 'present', 'missing', 3600)})
        out = det.evaluate(inv, rec, [], [lab('R', 100, 500.0)], cov, SHAS)
        for s in ('xc', 'eclipse', 'combined'):
            self.assertTrue(all(m['value'] is None and m['null_reason'] == 'no_evaluable_recordings' for m in out[s].values()), s)
        self.assertEqual(out['xc']['recall_all']['recordings'], 0)

    def test_present_file_with_no_events_counts_as_misses(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        r = det.evaluate(inv, rec, [dict(pred('R', None, None), status='no_events', event_id=None)], [lab('R', 100, 500.0)], cov, SHAS)['xc']
        self.assertEqual((r['recall_all']['numerator'], r['recall_all']['denominator'], r['recall_all']['value']), (0, 1, 0.0))
        self.assertEqual((r['unmatched_per_hour']['numerator'], r['unmatched_per_hour']['hours']), (0, 1.0))
        self.assertIsNone(r['median_abs_error_m']['value']); self.assertEqual(r['median_abs_error_m']['null_reason'], 'no_matched_events')

    def test_no_labels_for_evaluable_recording_is_null_recall(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        r = det.evaluate(inv, rec, [pred('R', 5.0, 100.0)], [], cov, SHAS)['xc']
        self.assertIsNone(r['recall_all']['value']); self.assertEqual(r['recall_all']['null_reason'], 'no_labels'); self.assertEqual(r['unmatched_per_hour']['numerator'], 1)

    def test_start_mismatch_excluded(self):
        inv, rec, cov = world({'R': ('xc', 'present', 'present', 3600)})
        cov['R']['recording_start_utc'] = utc_at(30)
        self.assertEqual(det.evaluate(inv, rec, [], [lab('R', 100, 500.0)], cov, SHAS)['xc']['recall_all']['null_reason'], 'no_evaluable_recordings')


class Cli(unittest.TestCase):
    def run_cli(self, d, *extra):
        return subprocess.run([sys.executable, '-I', str(MODULE), 'evaluate', '--recordings', str(d / 'recordings.csv'), '--out-dir', str(d), *extra], capture_output=True, text=True)

    def fixture(self, d, with_labels):
        (d / 'recordings.csv').write_text('recording_id,utc_start,duration_s,time_precision_s\nR1,%s,3600,1\n' % START)
        (d / 'detection_inventory.jsonl').write_text(json.dumps(dict(recording_id='R1', set='xc', detector_status='present', null_reason=None, file_group='g', file_sha256='a' * 64, n_events_raw=1, n_events=1)) + '\n')
        (d / 'detection_predictions.jsonl').write_text(json.dumps(pred('R1', 105.0, 900.0)) + '\n')
        if with_labels:
            (d / 'passes.jsonl').write_text(json.dumps(lab('R1', 100, 1000.0)) + '\n')
            (d / 'coverage.jsonl').write_text(json.dumps(dict(recording_id='R1', cut_status='present', recording_start_utc=START, pass_count=1)) + '\n')

    def test_missing_labels_exit_nonzero_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self.fixture(d, False)
            r = self.run_cli(d, '--labels', str(d / 'passes.jsonl'), '--coverage', str(d / 'coverage.jsonl'))
            self.assertNotEqual(r.returncode, 0); self.assertIn('labels not available', r.stderr); self.assertFalse((d / 'detection_summary.json').exists())

    def test_end_to_end_summary_validates_against_schema(self):
        import jsonschema
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self.fixture(d, True)
            r = self.run_cli(d, '--labels', str(d / 'passes.jsonl'), '--coverage', str(d / 'coverage.jsonl')); self.assertEqual(r.returncode, 0, r.stderr)
            s = json.loads((d / 'detection_summary.json').read_text())
            jsonschema.validate(s, json.loads((ROOT / 'schemas/detection_summary.schema.json').read_text()))
            self.assertEqual((s['combined']['recall_all']['numerator'], s['combined']['recall_all']['denominator']), (1, 1))
            self.assertEqual(s['by_set']['xc']['recall_0_2km']['source_sha256']['passes'], det.sha(d / 'passes.jsonl'))
            self.assertEqual(s['by_set']['liv']['recall_all']['null_reason'], 'withheld_liv_no_geometric_metrics')
            (d / 'coverage.jsonl').write_text(json.dumps(dict(recording_id='R1', cut_status='present', recording_start_utc=START, pass_count=2)) + '\n')
            self.assertNotEqual(self.run_cli(d, '--labels', str(d / 'passes.jsonl'), '--coverage', str(d / 'coverage.jsonl')).returncode, 0)   


@unittest.skipUnless(REAL, 'real detector outputs or the frozen tree are absent in this checkout')
class RealDetectorOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = [json.loads(x) for x in (ROOT / 'data/detection_inventory.jsonl').read_text().splitlines()]
        cls.preds = [json.loads(x) for x in (ROOT / 'data/detection_predictions.jsonl').read_text().splitlines()]
        cls.recs = {r['recording_id']: r for r in csv.DictReader(ROOT.joinpath('data/recordings.csv').open())}

    def test_inventory_covers_every_recording_with_expected_files(self):
        self.assertEqual({i['recording_id'] for i in self.inv}, set(self.recs))
        n = {}
        for i in self.inv:
            if i['detector_status'] == 'present': n[i['file_group']] = n.get(i['file_group'], 0) + 1
        self.assertEqual(n, {'a2o_b1': 295, 'a2o_b2': 61, 'eclipse': 187, 'xc_t1y_T1': 152, 'xc_t1y_T1b': 33, 'xc_pc1_out1': 1})
        self.assertTrue(all(i['detector_status'] == 'withheld' for i in self.inv if i['set'] == 'liv') and any(i['set'] == 'liv' for i in self.inv))
        self.assertTrue(all(i['detector_status'] == 'missing' for i in self.inv if i['set'] == 'parks'))

    def test_real_json_parses_to_the_prediction_printed_in_the_report(self):
        fit, model = det.load_frozen(FROZEN_BIN)
        path = ROOT.parent / 'geoloc/data/xc/t1y/answers/T1/XC1000602.dp.json'
        raw = json.loads(path.read_text()); ev = det.events(raw, fit, model)
        self.assertEqual(len(raw), 3); self.assertEqual([e[0] for e in ev], [1, 2])      
        b = raw[1]['fit']['best']
        self.assertEqual((ev[0][1], ev[0][2]), (b['t_cpa_arrive'], b['d_cpa']))
        row = next(p for p in self.preds if p['recording_id'] == 'XC1000602' and p['event_id'] == 'd1')
        print('REAL PREDICTION', json.dumps(row), 'source', next(i for i in self.inv if i['recording_id'] == 'XC1000602')['file_sha256'][:16])
        self.assertEqual((row['t_cpa_arrive_s'], row['slant_distance_m'], row['status']), (round(b['t_cpa_arrive'], 3), round(b['d_cpa'], 1), 'detected'))


    def test_released_prediction_rows_leak_nothing(self):
        text = (ROOT / 'data/detection_predictions.jsonl').read_text() + (ROOT / 'data/detection_inventory.jsonl').read_text()
        self.assertNotRegex(text, r'/home/|/tmp|\.dp\.json|"P[0-9a-f]{8}"|\b(lat|lon)|\bridge')
        self.assertTrue(all(set(p) == {'method', 'set', 'recording_id', 'event_id', 't_cpa_arrive_s', 'slant_distance_m', 'status'} for p in self.preds))
        import jsonschema
        sc = {n: json.loads((ROOT / f'schemas/detection_{n}.schema.json').read_text()) for n in ('prediction', 'inventory')}
        for p in self.preds: jsonschema.validate(p, sc['prediction'])
        for i in self.inv: jsonschema.validate(i, sc['inventory'])
        self.assertEqual({p['recording_id'] for p in self.preds} , {i['recording_id'] for i in self.inv if i['detector_status'] == 'present'})


@unittest.skipUnless(REAL, 'real detector outputs or the frozen tree are absent in this checkout')
class RealParity(unittest.TestCase):
    def test_adapter_equals_frozen_locate_events_on_sampled_files(self):
        det.load_frozen(FROZEN_BIN)
        spec_ = importlib.util.spec_from_file_location('locate_frozen', FROZEN_BIN / 'locate.py'); loc = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(loc)
        recs = {r['recording_id']: r for r in csv.DictReader(ROOT.joinpath('data/recordings.csv').open())}
        files = det.detector_files(ROOT.parent, recs); self.assertGreaterEqual(len(files), 729)
        fit, model = det.load_frozen(FROZEN_BIN); merged = 0
        for rid, (_, p) in sorted(files.items())[::8]:   
            raw = json.loads(Path(p).read_text()); mine = det.events(raw, fit, model); ref = loc.events(str(p), 343.0, None)
            self.assertEqual([(e[0], e[1], e[2]) for e in mine], [(e['id'], e['tcpa'], e['d']) for e in ref], rid)
            merged += len(mine) != len(raw)
        self.assertGreater(merged, 0)


if __name__ == '__main__':
    unittest.main()
