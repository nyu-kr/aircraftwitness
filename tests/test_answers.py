import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('answer_scorer', ROOT / 'tools/score_answers.py')
SCORER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORER)
DATA = ROOT / 'data'


class Answers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = json.loads((DATA / 'task_cohorts.json').read_text())['Eclipse clips']
        cls.records = {r['recording_id']: r for r in SCORER.read_csv(DATA / 'recordings.csv')}
        cls.rid = cls.ids[0]
        r = cls.records[cls.rid]
        cls.good = dict(method='check', version='test', set='Eclipse clips', task='T1', recording_id=cls.rid, status='ok', latitude=float(r['public_latitude']), longitude=float(r['public_longitude']))

    def metrics(self, rows):
        return SCORER.score(rows, DATA)[0]['metrics']

    def test_real_reference_and_missing_denominator(self):
        m = self.metrics([dict(self.good)])
        self.assertEqual(m['hit_1000m'], 1)
        self.assertEqual(m['expected'], 187)
        self.assertEqual(m['missing'], 186)
        self.assertEqual(m['median_error_m_answered'], 0)

    def test_date_only_cohort(self):
        m = self.metrics([dict(self.good, task='T2-date')])
        self.assertEqual((m['expected'], m['hit_1000m'], m['missing']), (187, 1, 186))

    def test_one_degree_is_not_a_hit(self):
        m = self.metrics([dict(self.good, latitude=self.good['latitude'] + 1)])
        self.assertEqual(m['hit_1000m'], 0)
        self.assertGreater(m['median_error_m_answered'], 100000)

    def test_failed_answer_does_not_need_coordinates(self):
        r = {k: v for k, v in self.good.items() if k not in ('latitude', 'longitude')}
        m = self.metrics([dict(r, status='timeout')])
        self.assertEqual(m['answered'], 0)
        self.assertEqual(m['hit_1000m'], 0)
        self.assertEqual(m['expected'], 187)

    def test_empty_duplicate_and_unknown_rejected(self):
        for rows in [[], [self.good, self.good], [dict(self.good, recording_id='NOT-A-RECORDING')]]:
            with self.assertRaises(ValueError):
                SCORER.score(rows, DATA)

    def test_nonfinite_and_boolean_rejected(self):
        for value in [float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError):
                self.metrics([dict(self.good, latitude=value)])

    def test_schema_invalid_rows_rejected(self):
        for row in [None, [], dict(self.good, extra='unknown'), dict(self.good, latitude='42'), dict(self.good, status='unknown'), dict(self.good, latitude=91), dict(self.good, scenario=[]), dict(self.good, verdict='invented')]:
            with self.assertRaises(ValueError):
                SCORER.score([row], DATA)

    def test_native_clock_real_reference(self):
        r = next(r for r in SCORER.read_csv(DATA / 'clock_tasks.csv') if r['scenario'] == 'native')
        self.assertEqual(float(r['reference_error_s']), 0)
        row = dict(method='check', version='test', set='Livermore', task='clock', recording_id=r['recording_id'], status='ok', scenario='native', predicted_clock_error_s=3)
        m = self.metrics([row])
        self.assertEqual(m['offset_rms_s_answered'], 3)
        self.assertEqual(m['expected'], 38)
        self.assertEqual(m['missing'], 37)

    def test_claim_agreement_uses_real_decision(self):
        r = SCORER.read_csv(DATA / 'claim_check_answers.csv')[0]
        row = dict(method='check', version='test', set='xeno-canto archive', task='claim-check', recording_id=r['recording_id'], status='ok', verdict=r['verdict'])
        m = self.metrics([row])
        self.assertEqual(m['agreement'], 1)
        self.assertEqual(m['expected'], 10464)

    def test_actual_make_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'answers.jsonl'
            path.write_text(json.dumps(self.good) + '\n')
            p = subprocess.run(['make', '--no-print-directory', 'eval', f'ANSWERS={path}', f'PYTHON={sys.executable}'], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)['results'][0]['metrics']['expected'], 187)
            path.write_text('')
            p = subprocess.run(['make', '--no-print-directory', 'eval', f'ANSWERS={path}', f'PYTHON={sys.executable}'], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('empty answer file', p.stderr)

    def test_injected_wrong_geometry_and_denominator(self):
        original = (ROOT / 'tools/score_answers.py').read_text()
        changes = [
            ('return 12742017.6 * math.asin(math.sqrt(min(1, max(0, d))))', 'return 0.0', dict(self.good, latitude=self.good['latitude'] + 1), 'hit_1000m', 0),
            ('expected = set(cohorts[subset])', 'expected = set(ids)', self.good, 'expected', 187),
        ]
        for old, new, row, key, correct in changes:
            self.assertEqual(original.count(old), 1)
            with tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / 'scorer.py'
                script.write_text(original.replace(old, new))
                answers = Path(temporary) / 'answers.jsonl'
                answers.write_text(json.dumps(row) + '\n')
                p = subprocess.run([sys.executable, '-I', str(script), str(answers), '--data', str(DATA)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotEqual(json.loads(p.stdout)['results'][0]['metrics'][key], correct)


    def test_square_centres_reproduce_table_centre_row(self):
        m = self.metrics(SCORER.square_centres(DATA))
        cell = {r['column']: r['value'] for r in SCORER.read_csv(DATA / 'results.csv') if r['set'] == 'Test set' and r['entrant'] == 'centre'}
        self.assertEqual((m['answered'], m['hit_1000m']), (int(cell['ans']), int(cell['hits'])))


if __name__ == '__main__':
    unittest.main()
