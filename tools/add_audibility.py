#!/usr/bin/env python3

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASSES, SCHEMA = ROOT / 'data/passes.jsonl', ROOT / 'schemas/passes.schema.json'
KIND = {bool: 'boolean', str: 'string', int: 'number', float: 'number'}
J = lambda x: json.dumps(x, ensure_ascii=False)


def schema_text(s):   # The schema uses one line per property.
    one = lambda v: '{\n' + ',\n'.join(f'    {J(k)}: {J(w)}' for k, w in v.items()) + '\n  }' if isinstance(v, dict) and v else J(v)
    return '{\n' + ',\n'.join(f'  {J(k)}: {one(v)}' for k, v in s.items()) + '\n}\n'


def cell(v):
    if v in ('true', 'false'): return v == 'true'
    if v == '': return None
    try: return float(v)
    except ValueError: return v


def main(path, described):
    flags = {}
    rows = [{k: v if k == 'pass_id' else cell(v) for k, v in r.items()} for r in csv.DictReader(open(path, newline=''))] if path.endswith('.csv') else [json.loads(l) for l in open(path)]
    for i, r in enumerate(rows, 1):
        k = r.pop('pass_id')
        if k in flags: raise SystemExit(f'add_audibility: {path}:{i}: duplicate pass_id {k}')
        flags[k] = r
    cols = sorted({c for r in flags.values() for c in r})
    if set(cols) != set(described): raise SystemExit(f'add_audibility: columns {cols} but described {sorted(described)}')
    rows = [json.loads(l) for l in PASSES.open()]
    have = {r['pass_id'] for r in rows}
    if flags.keys() - have: raise SystemExit(f'add_audibility: {len(flags.keys() - have)} flagged pass_ids not in data/passes.jsonl, e.g. {sorted(flags.keys() - have)[0]}')
    for r in rows:
        f = flags.get(r['pass_id'], {})
        r.update({c: f.get(c) for c in cols})
    s = json.loads(SCHEMA.read_text())
    for c in cols:
        kinds = sorted({KIND[type(r[c])] for r in rows if r[c] is not None})
        s['properties'][c] = {'type': kinds + ['null'], 'description': described[c]}
        if c not in s['required']: s['required'].append(c)
    tmp = PASSES.with_suffix('.jsonl.tmp')
    tmp.write_text(''.join(J(r) + '\n' for r in rows))
    tmp.replace(PASSES)
    SCHEMA.write_text(schema_text(s))
    print(f'add_audibility: {len(flags)} of {len(rows)} passes flagged; columns {cols}')


if __name__ == '__main__':
    if len(sys.argv) < 3: raise SystemExit(__doc__ or 'usage: add_audibility.py FLAGS.jsonl|FLAGS.csv COLUMN=DESCRIPTION...')
    main(sys.argv[1], dict(a.split('=', 1) for a in sys.argv[2:]))
