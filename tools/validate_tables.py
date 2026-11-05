#!/usr/bin/env python3
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def invalid_constant(value):
    raise ValueError('nonfinite JSON number: ' + value)


def load(path):
    return json.loads(path.read_text(), parse_constant=invalid_constant)


def validate(root):
    registry = load(root / 'schemas/tables.json')
    inventory_rows = load(root / 'data/inventory.json')
    inventory = {row['path']: row for row in inventory_rows}
    actual = {'data/' + p.name for p in (root / 'data').iterdir() if p.suffix in ('.csv', '.jsonl')}
    require(len(inventory) == len(inventory_rows) and set(registry) == set(inventory) == actual and bool(registry), 'table roster missing, duplicated or unregistered')
    tables = {}
    for name, schema_name in registry.items():
        path = root / name
        require(path.is_file() and not path.is_symlink(), 'missing or unsafe table: ' + name)
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == inventory[name]['sha256'], 'table digest mismatch: ' + name)
        schema_file, _, fragment = schema_name.partition('#')
        schema = load(root / 'schemas' / schema_file)
        if fragment:
            schema['$ref'] = '#' + fragment
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        if path.suffix == '.csv':
            with path.open(newline='') as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                require(reader.fieldnames == inventory[name]['fields'], 'CSV field roster mismatch: ' + name)
        else:
            rows = [json.loads(line, parse_constant=invalid_constant) for line in raw.splitlines() if line.strip()]
            require(list(dict.fromkeys(key for row in rows for key in row)) == inventory[name]['fields'], 'JSONL field roster mismatch: ' + name)
        require(len(rows) == inventory[name]['rows'] and bool(rows), 'empty or wrong row count: ' + name)
        for i, row in enumerate(rows, 1):
            error = next(validator.iter_errors(row), None)
            require(error is None, f'{name}:{i}: {error.message if error else ""}')
        tables[path.stem] = rows
    records = {r['recording_id']: r for r in tables['recordings']}
    require(len(records) == len(tables['recordings']), 'duplicate recording IDs')
    coverage = {r['recording_id']: r for r in tables['coverage']}
    require(len(coverage) == len(tables['coverage']) and set(coverage) == set(records), 'coverage does not exactly cover recordings')
    counts = Counter(row['recording_id'] for row in tables['passes'])
    require(set(counts) <= set(records), 'pass references unknown recording')
    require(len({r['pass_id'] for r in tables['passes']}) == len(tables['passes']), 'duplicate pass IDs')
    for rid, row in records.items():
        require(coverage[rid]['pass_count'] == counts[rid], 'pass_count mismatch: ' + rid)
        require(bool(row['public_latitude']) == bool(row['public_longitude']), 'partial public coordinate: ' + rid)
        if row['source'] == 'Livermore':
            require(not row['public_latitude'] and not row['public_longitude'] and counts[rid] == 0 and coverage[rid]['cut_status'] == 'withheld', 'Livermore fitted place or geometry exposed: ' + rid)
    for row in tables['passes']:
        require(row['source'] == records[row['recording_id']]['source'], 'pass source mismatch')
        if row['interpolated_slant_distance_m'] is not None:
            require(row['interpolated_slant_distance_m'] <= row['slant_distance_m'] + 1e-6, 'interpolated range exceeds observed minimum')
    claims = [r['recording_id'] for r in tables['claim_check_answers']]
    expected_claims = set(load(root / 'data/task_cohorts.json')['xeno-canto archive'])
    require(set(claims) == expected_claims and len(claims) == len(expected_claims), 'claim-check roster mismatch')
    detection_ids = [r['recording_id'] for r in tables['detection_inventory']]
    require(set(detection_ids) == set(records) and len(detection_ids) == len(records), 'detection inventory roster mismatch')
    published = [r['recording_id'] for r in tables['livermore_published_passes']]
    liv = {rid for rid, row in records.items() if row['source'] == 'Livermore'}
    require(set(published) == liv and len(published) == len(liv), 'published Livermore pass roster mismatch')
    return dict(tables=len(tables), recordings=len(records), passes=len(tables['passes']), coverage=len(coverage), first_slant_distance_m=tables['passes'][0]['slant_distance_m'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(validate(args.root), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, jsonschema.exceptions.SchemaError) as error:
        print('table validation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
