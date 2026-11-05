import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

MODULE = Path(os.environ.get('LABEL_MODULE', Path(__file__).resolve().parents[1] / 'tools' / 'label_passes.py'))
spec = importlib.util.spec_from_file_location('label_passes', MODULE)
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)
T = 1700000000.0


def recording():
    return dict(recording_id='XCfixture', source='xeno-canto', source_recording_id='XCfixture',
                public_latitude='0', public_longitude='0', utc_start='2023-11-14T22:13:20Z', duration_s='200',
                time_basis='synthetic clock', time_precision_s='60', position_basis='synthetic public coordinates')


def sample(t=0, **kwargs):
    r = dict(icao='abcdef', t=T + t, lat=0.0, lon=0.0, alt=1000.0, galt=2000.0,
             gs=0.0, trk=90.0, src='adsb_icao', type='H60')
    r.update(kwargs)
    return r


def run(rows, rec=None, elevations=None):
    return labels.label_recording(rec or recording(), pd.DataFrame(rows, columns=labels.COLUMNS),
                                  {'H60': {'desc': 'H2T', 'wtc': 'M'}}, elevations)


class Geometry(unittest.TestCase):
    def test_known_distance(self):
        self.assertAlmostEqual(labels.horizontal_m([1], [0], 0, 0)[0], 111195.0802335, places=5)
        self.assertAlmostEqual(labels.horizontal_m([0], [-179.99], 0, 179.99)[0], 2223.90160467, places=4)

    def test_units_geometric_preference_and_no_speed_gate(self):
        p, c = run([sample(gs=100)])
        self.assertEqual(len(p), 1)
        self.assertAlmostEqual(p[0]['altitude_m'], 609.6)
        self.assertAlmostEqual(p[0]['ground_speed_m_s'], 51.4444444444)
        self.assertEqual(p[0]['aircraft_category'], 'H2T')
        self.assertEqual(p[0]['altitude_basis'], 'geometric WGS84 ellipsoid')
        p, _ = run([sample(gs=None)])
        self.assertIsNone(p[0]['ground_speed_m_s'])
        self.assertIsNone(p[0]['detector_found'])
        self.assertIsNone(p[0]['locator_used'])
        self.assertFalse(c['all_aircraft_completeness'])

    def test_inclusive_radius(self):
        p, _ = run([sample(galt=10000 / 0.3048)])
        self.assertEqual(len(p), 1)
        p, _ = run([sample(galt=10000.01 / 0.3048)])
        self.assertEqual(len(p), 0)

    def test_recording_half_open_bounds(self):
        p, c = run([sample(-1), sample(0), sample(199.999), sample(200)])
        self.assertEqual(c['sample_count_during_recording'], 2)
        self.assertEqual(len(p), 2)
        self.assertTrue(all('22:13:19' not in row['closest_approach_utc'] for row in p))

    def test_outside_sample_splits_pass(self):
        p, _ = run([sample(0), sample(10, galt=50000), sample(20)])
        self.assertEqual(len(p), 2)

    def test_gap_splits_pass(self):
        p, c = run([sample(0), sample(60), sample(121)])
        self.assertEqual(len(p), 2)
        self.assertEqual(c['gaps_over_60s_count'], 1)
        self.assertEqual(p[0]['sample_count'], 2)

    def test_missing_altitude_breaks_pass(self):
        p, c = run([sample(0), sample(10, alt=None, galt=None), sample(20)])
        self.assertEqual(len(p), 2)
        self.assertEqual(c['missing_altitude_sample_count'], 1)
        p, _ = run([sample(galt=None)])
        self.assertAlmostEqual(p[0]['altitude_m'], 304.8)
        self.assertIn('barometric', p[0]['altitude_basis'])

    def test_closest_observed_sample(self):
        p, _ = run([sample(0, galt=3000), sample(10, galt=1000), sample(20, galt=2000)])
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['closest_approach_utc'], '2023-11-14T22:13:30Z')
        self.assertAlmostEqual(p[0]['slant_distance_m'], 304.8)
        self.assertIn('no interpolation', p[0]['closest_approach_basis'])

    def test_hash_and_public_fields(self):
        p, _ = run([sample()])
        q, _ = run([sample(icao='ABCDEF')])
        self.assertEqual(p[0]['aircraft_hash'], q[0]['aircraft_hash'])
        self.assertEqual(len(p[0]['aircraft_hash']), 64)
        self.assertTrue({'icao', 'reg', 'flight', 'callsign', 'lat', 'lon'}.isdisjoint(p[0]))
        self.assertNotIn('abcdef', json.dumps(p))

    def test_elevation_requires_same_public_coordinate(self):
        e = {'XCfixture': dict(lat=0, lon=0, ground_m=500)}
        p, _ = run([sample()], elevations=e)
        self.assertAlmostEqual(p[0]['slant_distance_m'], 109.6)
        self.assertIn('geoid', p[0]['observer_elevation_basis'])
        e['XCfixture']['lat'] = 0.001
        p, _ = run([sample()], elevations=e)
        self.assertEqual(p[0]['observer_elevation_m'], 0)
        self.assertIn('assumed', p[0]['observer_elevation_basis'])

    def test_missing_cut_is_not_zero_aircraft(self):
        p, c = labels.label_recording(recording(), None, {})
        self.assertEqual(c['cut_status'], 'missing')
        self.assertFalse(c['all_aircraft_completeness'])
        self.assertEqual(p, [])
        _, c = run([])
        self.assertEqual(c['cut_status'], 'present')
        self.assertEqual(c['sample_count_during_recording'], 0)

    def test_invalid_positions_are_excluded(self):
        p, c = run([sample(lat=91), sample(1, lon=None)])
        self.assertEqual(p, [])
        self.assertEqual(c['invalid_position_or_identity_sample_count'], 2)

    def test_duplicate_timestamp_prefers_geometric(self):
        p, c = run([sample(galt=None), sample(galt=2000)])
        self.assertEqual(c['duplicate_sample_count'], 1)
        self.assertEqual(p[0]['sample_count'], 1)
        self.assertAlmostEqual(p[0]['altitude_m'], 609.6)


class Interpolation(unittest.TestCase):
    def test_bracket_clipped_to_recording_start(self):
        r = recording()
        p, _ = run([sample(-5, galt=-800 / .3048), sample(5, galt=1200 / .3048)], r)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['interpolated_closest_approach_utc'], r['utc_start'])
        self.assertAlmostEqual(p[0]['interpolated_slant_distance_m'], 200)
        self.assertEqual(p[0]['interpolation_bracket_gap_s'], 10)
        self.assertAlmostEqual(p[0]['slant_distance_m'], 1200)

    def test_isolated_observed_minimum_is_not_replaced_by_worse_segment(self):
        p, _ = run([sample(0, galt=1000), sample(60, galt=5000), sample(65, galt=6000)])
        self.assertEqual(len(p), 1)
        self.assertIsNone(p[0]['interpolated_slant_distance_m'])

    def test_ten_second_limit_and_no_extrapolation(self):
        p, _ = run([sample(0), sample(10.001)])
        self.assertIsNone(p[0]['interpolated_closest_approach_utc'])
        p, _ = run([sample(0), sample(10)])
        self.assertIsNotNone(p[0]['interpolated_closest_approach_utc'])
        p, _ = run([sample(0)])
        self.assertIsNone(p[0]['interpolated_slant_distance_m'])

    def test_bracket_clipped_before_recording_end(self):
        r = recording()
        r['duration_s'] = '8'
        p, _ = run([sample(5, galt=1000 / .3048), sample(15, galt=-1000 / .3048)], r)
        t = pd.Timestamp(p[0]['interpolated_closest_approach_utc'])
        self.assertLess(t, pd.Timestamp(T + 8, unit='s', tz='UTC'))
        self.assertAlmostEqual(p[0]['interpolated_slant_distance_m'], 400, places=3)

    def test_ground_and_above_cap_observations_are_kept(self):
        r = recording()
        frame = pd.DataFrame([dict(sample(alt=35000, galt=35000), gnd=True)])
        p, c = labels.label_recording(r, frame, {}, {'XCfixture': dict(lat=0, lon=0, ground_m=2000)})
        self.assertEqual(len(p), 1)
        self.assertTrue(p[0]['ground_status'])
        self.assertEqual(c['observed_passes_above_33000ft'], 1)
        self.assertIsNone(c['omitted_passes_due_to_upstream_cap'])

    def test_livermore_geometry_withheld(self):
        r = recording()
        r.update(source='Livermore', public_latitude='', public_longitude='')
        p, c = labels.label_recording(r, pd.DataFrame([sample()]), {})
        self.assertEqual(p, [])
        self.assertEqual(c['cut_status'], 'withheld')
        self.assertIsNone(c['invalid_metadata_reason'])
        self.assertIsNone(c['observer_elevation_m'])


class Entrypoint(unittest.TestCase):
    def test_invalid_metadata_is_not_zero_aircraft(self):
        for field, bad in [('duration_s', ''), ('utc_start', ''), ('public_latitude', '91')]:
            r = recording()
            r[field] = bad
            p, c = labels.label_recording(r, None, {})
            self.assertEqual(p, [])
            self.assertEqual(c['cut_status'], 'invalid_metadata')
            self.assertTrue(c['invalid_metadata_reason'])
            self.assertFalse(c['all_aircraft_completeness'])

    def test_merge_refuses_wrong_count_and_missing_subject(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'coverage.jsonl').write_text(json.dumps(dict(recording_id='XCfixture', pass_count=1)) + '\n')
            (root / 'passes.jsonl').write_text('')
            with self.assertRaisesRegex(ValueError, 'pass count mismatch'):
                labels.merge_outputs([recording()], [root], root / 'merged')
            (root / 'coverage.jsonl').write_text('')
            with self.assertRaisesRegex(ValueError, 'missing recording coverage'):
                labels.merge_outputs([recording()], [root], root / 'merged')

    def test_merge_redacts_legacy_livermore_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = recording()
            r['source'] = 'Livermore'
            p, c = run([sample()])
            p[0]['source'] = 'Livermore'
            c['source'] = 'Livermore'
            (root / 'coverage.jsonl').write_text(json.dumps(c) + '\n')
            (root / 'passes.jsonl').write_text(json.dumps(p[0]) + '\n')
            labels.merge_outputs([r], [root], root / 'out')
            self.assertEqual((root / 'out' / 'passes.jsonl').read_text(), '')
            c = json.loads((root / 'out' / 'coverage.jsonl').read_text())
            self.assertEqual(c['cut_status'], 'withheld')
            self.assertEqual(c['pass_count'], 0)
            self.assertIsNone(c['observer_elevation_m'])

    def test_uncapped_cached_parquet_keeps_high_altitude_and_ground(self):
        self.assertEqual(labels.morton_cell(0, 0), 0x3fffffff)
        self.assertEqual(labels.morton_cell(-90, -180), 0)
        self.assertEqual(labels.morton_cell(90, 180), 0xffffffff)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = recording()
            r.update(public_latitude='40', public_longitude='-75')
            row = dict(icao='abcdef', ts=pd.Timestamp(r['utc_start']).tz_localize(None), gh=labels.morton_cell(40, -75),
                       lat=40., lon=-75., alt_baro_ft=35000, alt_geom_ft=30000, gs_kt=0., track_deg=90., source='mlat', on_ground=True)
            other = dict(row, lat=41., gh=labels.morton_cell(41, -75))
            pd.DataFrame([row, other]).to_parquet(root / '2023-11-14.usa.parquet')
            frame, ref = labels.load_uncapped(r, root)
            self.assertEqual(len(frame), 1)
            self.assertTrue(ref.startswith('uncapped-cached-daily/'))
            p, c = labels.label_recording(r, frame, {}, cut_reference=ref)
            self.assertEqual(len(p), 1)
            self.assertTrue(p[0]['ground_status'])
            self.assertTrue(c['uncapped_daily_data_used'])
            self.assertEqual(c['observed_passes_above_33000ft'], 1)

    def test_explicit_locator_match_only_for_unique_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, c = run([sample()])
            association = root / 'bench_rows.jsonl'
            association.write_text(json.dumps(dict(recording='XCfixture', matched_aircraft=['abcdef'], method='ours_planes', run_id='fixture')) + '\n')
            (root / 'coverage.jsonl').write_text(json.dumps(c) + '\n')
            (root / 'passes.jsonl').write_text(json.dumps(p[0]) + '\n')
            labels.merge_outputs([recording()], [root], root / 'unique', association)
            row = json.loads((root / 'unique' / 'passes.jsonl').read_text())
            self.assertTrue(row['locator_used'])
            self.assertIn('bench_rows.jsonl:1', row['locator_evidence'])
            self.assertIsNone(row['detector_found'])
            self.assertNotIn('abcdef', json.dumps(row))
            second = dict(p[0], pass_id='1' * 64)
            c['pass_count'] = 2
            (root / 'coverage.jsonl').write_text(json.dumps(c) + '\n')
            (root / 'passes.jsonl').write_text(json.dumps(p[0]) + '\n' + json.dumps(second) + '\n')
            labels.merge_outputs([recording()], [root], root / 'ambiguous', association)
            rows = [json.loads(s) for s in (root / 'ambiguous' / 'passes.jsonl').read_text().splitlines()]
            self.assertTrue(all(r['locator_used'] is None for r in rows))

    def test_real_parquet_archive_filter_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'archive').mkdir()
            r = recording()
            with (root / 'recordings.csv').open('w') as f:
                w = csv.DictWriter(f, fieldnames=list(r))
                w.writeheader()
                w.writerow(r)
            rows = [dict(sample(), rid='XCfixture', decoy=0),
                    dict(sample(5, icao='111111'), rid='XCfixture', decoy=1),
                    dict(sample(10, icao='222222'), rid='other', decoy=0)]
            pd.DataFrame(rows).to_parquet(root / 'archive' / '2023-11-14.parquet')
            (root / 'types.json').write_text('{}')
            cmd = [sys.executable, '-I', str(MODULE), '--recordings', str(root / 'recordings.csv'),
                   '--cut-root', str(root / 'cuts'), '--archive', str(root / 'archive'), '--type-map', str(root / 'types.json'),
                   '--out', str(root / 'out'), '--work-dir', str(root / 'work')]
            result = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            p = [json.loads(s) for s in (root / 'out' / 'passes.jsonl').read_text().splitlines()]
            c = [json.loads(s) for s in (root / 'out' / 'coverage.jsonl').read_text().splitlines()]
            self.assertEqual(len(p), 1)
            self.assertEqual(len(c), 1)
            self.assertEqual(c[0]['pass_count'], len(p))
            self.assertEqual(c[0]['input_sample_count'], 1)
            self.assertEqual(p[0]['recording_id'], 'XCfixture')
            self.assertEqual(json.loads((root / 'out' / 'coverage_summary.json').read_text())['pass_count'], len(p))
            labels.merge_outputs([r], [root / 'out'], root / 'merged')
            merged = json.loads((root / 'merged' / 'coverage.jsonl').read_text())
            self.assertEqual(merged['recording_time_basis'], 'synthetic clock')
            self.assertEqual(merged['recording_time_precision_s'], 60)
            self.assertIn('33000', merged['upstream_cut_filters'])


if __name__ == '__main__':
    unittest.main()
