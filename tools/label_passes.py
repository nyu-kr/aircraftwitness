#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd

NAMESPACE = 'org.geoloc.open-aircraft-passes.v1:'
RADIUS_M = 10000.0
MAX_GAP_S = 60.0
COLUMNS = ['icao', 't', 'lat', 'lon', 'alt', 'galt', 'gs', 'trk', 'src', 'type']
METHOD = 'observed samples only; no interpolation or extrapolation; minimum observed slant, not continuous closest approach'
ELEVATION_WARNING = 'terrain orthometric height used as ellipsoid-height approximation; geoid correction unavailable'
ARCHIVE_CACHE = {'path': None, 'groups': {}}


def metadata_error(recording):
    errors = []
    if pd.isna(pd.to_datetime(recording.get('utc_start'), utc=True, errors='coerce')):
        errors.append('missing or invalid UTC start')
    duration = number(recording.get('duration_s'))
    if duration is None or duration <= 0:
        errors.append('missing or invalid recording duration')
    lat, lon = number(recording.get('public_latitude')), number(recording.get('public_longitude'))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        errors.append('missing or invalid public position')
    return '; '.join(errors) or None


def utc(t):
    return pd.Timestamp(t, unit='s', tz='UTC').isoformat().replace('+00:00', 'Z')


def number(v):
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def text(v):
    return str(v) if pd.notna(v) and str(v).strip() else None


def horizontal_m(lat, lon, lat0, lon0):
    p, q = np.radians(np.asarray(lat, dtype=float)), math.radians(lat0)
    dl = np.radians(np.asarray(lon, dtype=float) - lon0)
    h = np.sin((p - q) / 2) ** 2 + np.cos(p) * math.cos(q) * np.sin(dl / 2) ** 2
    return 6371008.8 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def observer(recording, elevations):
    rid = recording['recording_id']
    e = elevations.get(rid)
    lat, lon = float(recording['public_latitude']), float(recording['public_longitude'])
    if e is not None and abs(float(e['lat']) - lat) < 1e-8 and abs(float(e['lon']) - lon) < 1e-8 and number(e['ground_m']) is not None:
        return float(e['ground_m']), 'public-coordinate Copernicus GLO-90 terrain height; ' + ELEVATION_WARNING
    return 0.0, 'assumed 0 m WGS84 ellipsoid; public-coordinate terrain height unavailable'


def interpolated_cpa(frame, first, last, start, end, lat0, lon0, ground):
    d = frame[(frame.t >= start - 10) & (frame.t <= end + 10)].sort_values('t').drop_duplicates('t').copy()
    if len(d) < 2:
        return None
    lat, lon = pd.to_numeric(d.lat, errors='coerce').to_numpy(float), pd.to_numeric(d.lon, errors='coerce').to_numpy(float)
    geom, baro = pd.to_numeric(d.galt, errors='coerce'), pd.to_numeric(d.alt, errors='coerce')
    z = geom.where(geom.notna(), baro).to_numpy(float) * 0.3048 - ground
    distance = horizontal_m(lat, lon, lat0, lon0)
    p, q, dl = np.radians(lat), math.radians(lat0), np.radians(lon - lon0)
    bearing = np.arctan2(np.sin(dl) * np.cos(p), math.cos(q) * np.sin(p) - math.sin(q) * np.cos(p) * np.cos(dl))
    points = np.column_stack([distance * np.sin(bearing), distance * np.cos(bearing), z])
    points[(np.abs(lat) > 90) | (np.abs(lon) > 180)] = np.nan
    times = d.t.to_numpy(float)
    best = None
    for i in range(len(d) - 1):
        a, b = times[i], times[i + 1]
        gap = b - a
        if gap <= 0 or gap > 10 or b < first or a > last or b < start or a >= end:
            continue
        v, delta = points[i], points[i + 1] - points[i]
        if not np.isfinite(v).all() or not np.isfinite(delta).all():
            continue
        lo, hi = max(0.0, (start - a) / gap), min(1.0, (np.nextafter(end, -np.inf) - a) / gap)
        if lo > hi:
            continue
        length = float(np.dot(delta, delta))
        fraction = float(np.clip(-np.dot(v, delta) / length if length else lo, lo, hi))
        closest = v + fraction * delta
        slant = float(np.linalg.norm(closest))
        candidate = (slant, a + fraction * gap, float(np.hypot(closest[0], closest[1])), gap)
        if best is None or candidate < best:
            best = candidate
    observed = np.linalg.norm(points[(times >= first) & (times <= last)], axis=1)
    finite = observed[np.isfinite(observed)]
    if best is not None and len(finite) and best[0] > float(finite.min()) + 1e-6:
        return None
    return best


def label_recording(recording, frame, types, elevations=None, cut_reference='unknown'):
    rid = recording['recording_id']
    withheld = recording['source'] == 'Livermore'
    error = None if withheld else metadata_error(recording)
    stamp = pd.to_datetime(recording.get('utc_start'), utc=True, errors='coerce')
    start = None if pd.isna(stamp) else stamp.timestamp()
    duration = number(recording.get('duration_s'))
    end = start + duration if start is not None and duration is not None else None
    ground, ground_basis = (None, 'withheld: no published recorder position; fitted geometry prohibited') if withheld else ((0.0, 'unavailable: invalid recording metadata') if error else observer(recording, elevations or {}))
    coverage = dict(recording_id=rid, source=recording['source'], cut_status='withheld' if withheld else ('invalid_metadata' if error else ('present' if frame is not None else 'missing')), cut_reference=cut_reference,
                    withheld_reason='no published recorder position; fitted geometry prohibited' if withheld else None,
                    invalid_metadata_reason=error, recording_start_utc=utc(start) if start is not None else None, recording_end_utc=utc(end) if end is not None else None, input_sample_count=0,
                    sample_count_during_recording=0, usable_altitude_sample_count=0, missing_altitude_sample_count=0,
                    invalid_position_or_identity_sample_count=0, duplicate_sample_count=0, gaps_over_60s_count=0,
                    max_aircraft_gap_s=None, first_sample_utc=None, last_sample_utc=None, pass_count=0,
                    all_aircraft_completeness=False, altitude_reference_uncertainty=True,
                    observed_samples_above_33000ft=0, observed_passes_above_33000ft=0, omitted_passes_due_to_upstream_cap=None,
                    uncapped_daily_data_used=cut_reference.startswith('uncapped-cached-daily/'),
                    observer_elevation_m=ground, observer_elevation_basis=ground_basis,
                    sampling_method=METHOD, max_contiguous_gap_s=MAX_GAP_S,
                    coverage_caveat='cut availability is not ADS-B reception completeness; missing altitude, sample gaps, position/time uncertainty and observer datum may hide or change passes; no acoustic audibility inference')
    if withheld or error or frame is None:
        return [], coverage
    coverage['input_sample_count'] = len(frame)
    d = frame[(frame.t >= start) & (frame.t < end)].copy()
    coverage['sample_count_during_recording'] = len(d)
    if d.empty:
        return [], coverage
    valid = d.icao.notna() & d.lat.notna() & d.lon.notna() & np.isfinite(d.lat) & np.isfinite(d.lon) & d.lat.between(-90, 90) & d.lon.between(-180, 180)
    coverage['invalid_position_or_identity_sample_count'] = int((~valid).sum())
    d = d[valid].copy()
    if d.empty:
        return [], coverage
    for col in ['alt', 'galt', 'gs', 'trk']:
        d[col] = pd.to_numeric(d[col], errors='coerce').astype(float)
    # Duplicate aircraft timestamps prefer geometric altitude.
    d['_geom'] = d.galt.notna()
    d = d.sort_values(['icao', 't', '_geom'], ascending=[True, True, False], kind='stable')
    n = len(d)
    d = d.drop_duplicates(['icao', 't'])
    coverage['duplicate_sample_count'] = n - len(d)
    coverage['observed_samples_above_33000ft'] = int((d.alt > 33000).sum())
    d['_alt'] = d.galt.where(d.galt.notna(), d.alt) * 0.3048
    good_alt = np.isfinite(d['_alt'])
    coverage['missing_altitude_sample_count'] = int((~good_alt).sum())
    coverage['usable_altitude_sample_count'] = int(good_alt.sum())
    coverage['first_sample_utc'], coverage['last_sample_utc'] = utc(d.t.min()), utc(d.t.max())
    d['_horizontal'] = horizontal_m(d.lat, d.lon, float(recording['public_latitude']), float(recording['public_longitude']))
    d['_slant'] = np.hypot(d['_horizontal'], d['_alt'] - ground)
    d['_inside'] = good_alt & (d['_slant'] <= RADIUS_M)
    passes = []
    for icao, track in d.groupby('icao', sort=True):
        gaps = track.t.diff()
        if gaps.notna().any():
            mx = float(gaps.max())
            coverage['max_aircraft_gap_s'] = max(coverage['max_aircraft_gap_s'] or 0, mx)
        coverage['gaps_over_60s_count'] += int((gaps > MAX_GAP_S).sum())
        # An outside or unknown altitude sample ends a pass even with a short gap.
        runs = ((track['_inside'] != track['_inside'].shift()) | (gaps > MAX_GAP_S)).cumsum()
        for _, segment in track[track['_inside']].groupby(runs[track['_inside']], sort=True):
            nearest = segment.loc[segment['_slant'].idxmin()]
            hashed = hashlib.sha256((NAMESPACE + str(icao).strip().lower()).encode()).hexdigest()
            t = float(nearest.t)
            typ = text(nearest['type'])
            sources = sorted(set(str(v) for v in segment.src.dropna()))
            interpolation = interpolated_cpa(frame[frame.icao.eq(icao)], float(segment.t.min()), float(segment.t.max()), start, end,
                                             float(recording['public_latitude']), float(recording['public_longitude']), ground)
            p = dict(recording_id=rid, source=recording['source'], aircraft_hash=hashed,
                     pass_id=hashlib.sha256(f'{rid}:{hashed}:{float(segment.t.min()):.6f}'.encode()).hexdigest(),
                     first_in_range_sample_utc=utc(segment.t.min()), last_in_range_sample_utc=utc(segment.t.max()),
                     closest_approach_utc=utc(t), closest_approach_basis=METHOD,
                     horizontal_distance_m=float(nearest['_horizontal']), slant_distance_m=float(nearest['_slant']),
                     altitude_m=float(nearest['_alt']), altitude_basis='geometric WGS84 ellipsoid' if pd.notna(nearest.galt) else 'barometric pressure altitude fallback; uncorrected',
                     observer_elevation_m=ground, observer_elevation_basis=ground_basis,
                     ground_speed_m_s=None if number(nearest.gs) is None else float(nearest.gs) * 0.5144444444444445,
                     track_deg=number(nearest.trk), aircraft_type=typ, aircraft_category=types.get(typ, {}).get('desc'),
                     wake_turbulence_category=types.get(typ, {}).get('wtc'),
                     surveillance_sources=sources, sample_count=len(segment), max_sample_gap_s=float(segment.t.diff().max()) if len(segment) > 1 else None,
                     detector_found=None, detector_evidence='unknown: no explicit aircraft-to-detector association released',
                     locator_used=None, locator_evidence='unknown: no explicit aircraft-to-locator association released',
                     geometric_proximity_is_not_audibility=True,
                     interpolated_closest_approach_utc=utc(interpolation[1]) if interpolation else None,
                     interpolated_slant_distance_m=interpolation[0] if interpolation else None,
                     interpolated_horizontal_distance_m=interpolation[2] if interpolation else None,
                     interpolation_bracket_gap_s=interpolation[3] if interpolation else None,
                     interpolation_basis='linear in observer-centred local horizontal Cartesian coordinates and reported altitude; brackets <=10s; clipped to recording; only observed-pass candidates',
                     ground_status=bool(nearest['gnd']) if 'gnd' in nearest and pd.notna(nearest['gnd']) else None)
            coverage['observed_passes_above_33000ft'] += int((segment.alt > 33000).any())
            passes.append(p)
    coverage['pass_count'] = len(passes)
    return passes, coverage


def candidate_cut(recording, root):
    rid = recording['recording_id']
    source_id = recording['source_recording_id']
    if recording['source'] == 'xeno-canto':
        return [root / 'xc' / f'{rid}.parquet']
    if rid.startswith('ES'):
        site, date, _ = rid.split('_')
        return [root / 'ecl' / 'cut2' / f'{site}_{date}.parquet', root / 'ecl' / 'cut2' / f'{site}_{date[:4]}-{date[4:6]}-{date[6:]}.parquet']
    if 'A2O' in recording['source'].upper():
        return [root / sub / 'cut' / f'{source_id}.parquet' for sub in ['a2o', 'a2o_b2', 'a2o_b3']]
    if rid.startswith('LIV_'):
        return [root / 'liv' / f'lol_{recording["utc_start"][:10]}.liv.parquet']
    return []


def read_sample_cut(path):
    import pyarrow.parquet as pq
    extra = ['gnd'] if 'gnd' in pq.read_schema(path).names else []
    return pd.read_parquet(path, columns=COLUMNS + extra)


UNCAPPED_REGIONS = {
    'usa': [(-125.5, 23.9, -65.5, 50.0), (-125.5, 47.0, -65.5, 50.6), (-180.0, 50.6, -128.5, 72.0), (172.0, 50.6, 180.0, 55.0), (-161.2, 18.5, -154.3, 22.7)],
    'leb': [(34.55, 32.60, 37.17, 35.14)]}
UNCAPPED_TYPES = {'path': None, 'types': {}}


def morton_cell(lat, lon):
    def spread(x):
        x = (x | x << 8) & 0x00FF00FF
        x = (x | x << 4) & 0x0F0F0F0F
        x = (x | x << 2) & 0x33333333
        return (x | x << 1) & 0x55555555
    return spread(int((lon + 180) / 360 * 65535)) << 1 | spread(int((lat + 90) / 180 * 65535))


def load_uncapped(recording, root):
    if root is None:
        return None
    lat, lon = float(recording['public_latitude']), float(recording['public_longitude'])
    dy = math.degrees(RADIUS_M / 6371008.8) + .001
    dx = dy / max(.001, math.cos(math.radians(lat)))
    regions = [key for key, boxes in UNCAPPED_REGIONS.items() if any(w <= lon - dx and lon + dx <= e and s <= lat - dy and lat + dy <= n for w, s, e, n in boxes)]
    start = pd.Timestamp(recording['utc_start']).tz_convert('UTC').tz_localize(None)
    end = start + pd.Timedelta(seconds=float(recording['duration_s']))
    days = sorted({(start - pd.Timedelta(seconds=10)).strftime('%Y-%m-%d'), (end + pd.Timedelta(seconds=10)).strftime('%Y-%m-%d')})
    for region in regions:
        paths = [root / f'{day}.{region}.parquet' for day in days]
        if not all(p.is_file() for p in paths):
            continue
        frames = []
        for path in paths:
            filters = [('gh', '>=', morton_cell(lat - dy, lon - dx)), ('gh', '<=', morton_cell(lat + dy, lon + dx)),
                       ('lat', '>=', lat - dy), ('lat', '<=', lat + dy), ('lon', '>=', lon - dx), ('lon', '<=', lon + dx),
                       ('ts', '>=', (start - pd.Timedelta(seconds=10)).to_pydatetime()), ('ts', '<=', (end + pd.Timedelta(seconds=10)).to_pydatetime())]
            columns = ['icao', 'ts', 'lat', 'lon', 'alt_baro_ft', 'alt_geom_ft', 'gs_kt', 'track_deg', 'source', 'on_ground']
            d = pd.read_parquet(path, columns=columns, filters=filters)
            d['t'] = (pd.to_datetime(d.ts, utc=True) - pd.Timestamp('1970-01-01', tz='UTC')).dt.total_seconds()
            ac = path.with_name(path.name.replace('.parquet', '.aircraft.parquet'))
            if ac != UNCAPPED_TYPES['path']:
                UNCAPPED_TYPES['types'] = pd.read_parquet(ac, columns=['icao', 'type_code']).drop_duplicates('icao').set_index('icao').type_code.to_dict() if ac.is_file() else {}
                UNCAPPED_TYPES['path'] = ac
            d['type'] = d.icao.map(UNCAPPED_TYPES['types'])
            d = d.rename(columns=dict(alt_baro_ft='alt', alt_geom_ft='galt', gs_kt='gs', track_deg='trk', source='src', on_ground='gnd'))
            frames.append(d[COLUMNS + ['gnd']])
        return pd.concat(frames, ignore_index=True), 'uncapped-cached-daily/' + ','.join(p.name for p in paths)
    return None


def load_cut(recording, args):
    uncapped = load_uncapped(recording, getattr(args, 'uncapped_root', None))
    if uncapped is not None:
        return uncapped
    mapped = getattr(args, 'cut_mapping', {}).get(recording['recording_id'])
    if mapped:
        path = Path(mapped['adsb_cut'])
        if path.is_file():
            return read_sample_cut(path), 'approved-cut-map/' + path.parent.name + '/' + path.name
        return None, 'approved-cut-map: reusable cut missing'
    if args.archive and recording['source'] == 'xeno-canto':
        path = args.archive / (recording['utc_start'][:10] + '.parquet')
        if path.exists():
            if ARCHIVE_CACHE['path'] != path:
                day = pd.read_parquet(path, columns=COLUMNS + ['rid'], filters=[('decoy', '=', 0)])
                ARCHIVE_CACHE['groups'] = {str(rid): group for rid, group in day.groupby('rid', sort=False)}
                ARCHIVE_CACHE['path'] = path
            d = ARCHIVE_CACHE['groups'].get(recording['recording_id'])
            if d is not None and len(d):
                return d, 'audit/flights/' + path.name + '; rid=recording_id, decoy=0'
    for path in candidate_cut(recording, args.cut_root):
        if path.exists():
            return read_sample_cut(path), str(path.relative_to(args.cut_root))
    return None, 'no matching reusable cut'


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, allow_nan=False, separators=(',', ':')) + '\n')
    os.replace(tmp, path)


def merge_outputs(recordings, directories, out, associations=None):
    selected = {}
    producers = set()
    wanted = {r['recording_id']: r for r in recordings}
    for directory in directories:
        summary_path = directory / 'coverage_summary.json'
        if summary_path.is_file():
            producers.add(json.loads(summary_path.read_text())['script_sha256'])
        crows = [json.loads(s) for s in (directory / 'coverage.jsonl').read_text().splitlines()]
        prows = [json.loads(s) for s in (directory / 'passes.jsonl').read_text().splitlines()]
        cmap = {r['recording_id']: dict(coverage=r, passes=[]) for r in crows}
        if len(cmap) != len(crows):
            raise ValueError(f'{directory}: duplicate coverage IDs')
        for row in prows:
            cmap[row['recording_id']]['passes'].append(row)
        for rid, result in cmap.items():
            if result['coverage']['pass_count'] != len(result['passes']):
                raise ValueError(f'{directory}: pass count mismatch for {rid}')
            if rid in wanted:
                if rid in selected and selected[rid]['coverage']['cut_status'] == 'present' and result['coverage']['cut_status'] == 'missing':
                    continue
                selected[rid] = result
    if set(selected) != set(wanted):
        raise ValueError(f'missing recording coverage: {sorted(set(wanted) - set(selected))[:5]}')
    if associations:
        matches = {}
        for line, raw in enumerate(associations.open(), 1):
            r = json.loads(raw)
            rid = r.get('recording')
            if rid not in wanted or wanted[rid]['source'] == 'Livermore':
                continue
            for address in r.get('matched_aircraft') or []:
                hashed = hashlib.sha256((NAMESPACE + str(address).strip().lower()).encode()).hexdigest()
                run_hash = hashlib.sha256(str(r.get('run_id', '')).encode()).hexdigest()
                matches.setdefault((rid, hashed), []).append(f'bench_rows.jsonl:{line}; explicit locator match; method={r.get("method")}; run_sha256={run_hash}')
        for rid, result in selected.items():
            counts = {}
            for row in result['passes']:
                counts[row['aircraft_hash']] = counts.get(row['aircraft_hash'], 0) + 1
            for row in result['passes']:
                evidence = matches.get((rid, row['aircraft_hash']))
                if evidence and counts[row['aircraft_hash']] == 1:
                    row['locator_used'] = True
                    row['locator_evidence'] = '; '.join(evidence)
    out.mkdir(parents=True, exist_ok=True)
    seen = set()
    counts = {}
    with (out / 'passes.jsonl').open('w') as pf, (out / 'coverage.jsonl').open('w') as cf:
        for rid in sorted(wanted):
            result = selected[rid]
            if wanted[rid]['source'] == 'Livermore':
                result = dict(passes=[], coverage=label_recording(wanted[rid], None, {})[1])
            c = result['coverage']
            if c['source'] != wanted[rid]['source']:
                raise ValueError(f'{rid}: changed source')
            c['recording_time_basis'] = wanted[rid]['time_basis']
            c['recording_time_precision_s'] = number(wanted[rid].get('time_precision_s'))
            c['recording_position_basis'] = wanted[rid]['position_basis']
            c['recording_position_precision_m'] = number(wanted[rid].get('position_precision_m'))
            c['upstream_cut_filters'] = ('airborne only; barometric altitude <=33000 ft; per-recording space/time cut; reception completeness unknown'
                                         if c['cut_reference'].startswith('audit/flights/') else
                                         ('cached daily source has no altitude or ground filter; public-position 10km bounding box and nominal time only; reception completeness unknown'
                                          if c['cut_reference'].startswith('uncapped-cached-daily/') else
                                          'reused spatial/time cut; upstream reception, altitude and ground filtering not independently established'))
            cf.write(json.dumps(c, allow_nan=False) + '\n')
            n = counts.setdefault(c['source'], dict(recordings=0, passes=0, present_cuts=0, missing_cuts=0, invalid_metadata=0, withheld=0, zero_pass_with_cut=0, uncapped_daily_recordings=0, observed_samples_above_33000ft=0, observed_passes_above_33000ft=0))
            n['recordings'] += 1
            n['uncapped_daily_recordings'] += int(c.get('uncapped_daily_data_used', False))
            n['observed_samples_above_33000ft'] += c.get('observed_samples_above_33000ft', 0)
            n['observed_passes_above_33000ft'] += c.get('observed_passes_above_33000ft', 0)
            n['passes'] += len(result['passes'])
            n[{'present': 'present_cuts', 'missing': 'missing_cuts', 'invalid_metadata': 'invalid_metadata', 'withheld': 'withheld'}[c['cut_status']]] += 1
            n['zero_pass_with_cut'] += int(c['cut_status'] == 'present' and not result['passes'])
            for row in result['passes']:
                if row.get('interpolated_slant_distance_m') is not None and row['interpolated_slant_distance_m'] > row['slant_distance_m'] + 1e-6:
                    for field in ['interpolated_closest_approach_utc', 'interpolated_slant_distance_m', 'interpolated_horizontal_distance_m', 'interpolation_bracket_gap_s']:
                        row[field] = None
                if row['pass_id'] in seen:
                    raise ValueError(f'duplicate pass {row["pass_id"]}')
                seen.add(row['pass_id'])
                pf.write(json.dumps(row, allow_nan=False) + '\n')
    actual_coverage = [json.loads(s) for s in (out / 'coverage.jsonl').read_text().splitlines()]
    actual_passes = [json.loads(s) for s in (out / 'passes.jsonl').read_text().splitlines()]
    if len(actual_coverage) != len(wanted) or len(actual_passes) != sum(c['pass_count'] for c in actual_coverage):
        raise ValueError('written output failed independent reconciliation')
    summary = dict(recording_count=len(actual_coverage), pass_count=len(actual_passes), by_source=counts,
                   aircraft_hash_namespace=NAMESPACE, radius_m=RADIUS_M, method=METHOD, all_aircraft_completeness=False,
                   script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   input_producer_script_sha256=sorted(producers),
                   output_sha256={name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ['passes.jsonl', 'coverage.jsonl']})
    write_json(out / 'coverage_summary.json', summary)
    print(json.dumps(summary))
    if actual_passes:
        print('real_pass_row=' + json.dumps(actual_passes[0]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--recordings', type=Path, required=True)
    p.add_argument('--cut-root', type=Path)
    p.add_argument('--archive', type=Path)
    p.add_argument('--cut-map', type=Path)
    p.add_argument('--uncapped-root', type=Path)
    p.add_argument('--packet-map', type=Path)
    p.add_argument('--associations', type=Path)
    p.add_argument('--type-map', type=Path)
    p.add_argument('--elevations', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--work-dir', type=Path)
    p.add_argument('--merge-from', nargs='+', type=Path)
    p.add_argument('--only-id')
    p.add_argument('--only-source')
    a = p.parse_args()
    rows = list(csv.DictReader(a.recordings.open()))
    if a.only_id:
        rows = [r for r in rows if r['recording_id'] == a.only_id]
    if a.only_source:
        rows = [r for r in rows if r['source'] == a.only_source]
    if not rows:
        raise ValueError('empty recording selection')
    if a.merge_from:
        merge_outputs(rows, a.merge_from, a.out, a.associations)
        return
    if not all([a.type_map, a.cut_root, a.work_dir]):
        p.error('--type-map, --cut-root and --work-dir required when not merging')
    types = json.loads(a.type_map.read_text())
    a.cut_mapping = {r['recording_id']: r for r in csv.DictReader(a.cut_map.open())} if a.cut_map else {}
    if a.packet_map:
        for r in csv.DictReader(a.packet_map.open()):
            sub = {'1': 'a2o', '2': 'a2o_b2', '3': 'a2o_b3'}[r['batch']]
            a.cut_mapping[r['recording_id']] = dict(adsb_cut=str(a.cut_root / sub / 'cut' / (r['packet'] + '.parquet')))
    elevations = {}
    if a.elevations and a.elevations.exists():
        e = pd.read_parquet(a.elevations)
        if 'decoy' in e:
            e = e[e.decoy.eq(0)]
        elevations = {str(r['rid']): r for r in e.to_dict('records')}
    a.work_dir.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    begun = time.monotonic()
    rows.sort(key=lambda r: (r['utc_start'][:10], r['recording_id']))
    for r in rows:
        key = hashlib.sha256(json.dumps(r, sort_keys=True).encode() + version.encode()).hexdigest()
        checkpoint = a.work_dir / (key + '.json')
        if not checkpoint.exists():
            frame, ref = (None, 'withheld: no published recorder position') if r['source'] == 'Livermore' else ((None, 'not evaluated: invalid recording metadata') if metadata_error(r) else load_cut(r, a))
            passes, coverage = label_recording(r, frame, types, elevations, ref)
            write_json(checkpoint, dict(passes=passes, coverage=coverage))
    counts = {}
    first = None
    with (a.out / 'passes.jsonl').open('w') as pf, (a.out / 'coverage.jsonl').open('w') as cf:
        for r in rows:
            key = hashlib.sha256(json.dumps(r, sort_keys=True).encode() + version.encode()).hexdigest()
            result = json.loads((a.work_dir / (key + '.json')).read_text())
            c = result['coverage']
            cf.write(json.dumps(c, allow_nan=False) + '\n')
            counts.setdefault(c['source'], dict(recordings=0, passes=0, present_cuts=0, missing_cuts=0, invalid_metadata=0, withheld=0, zero_pass_with_cut=0, uncapped_daily_recordings=0, observed_samples_above_33000ft=0, observed_passes_above_33000ft=0))
            n = counts[c['source']]
            n['recordings'] += 1
            n['uncapped_daily_recordings'] += int(c.get('uncapped_daily_data_used', False))
            n['observed_samples_above_33000ft'] += c.get('observed_samples_above_33000ft', 0)
            n['observed_passes_above_33000ft'] += c.get('observed_passes_above_33000ft', 0)
            n['passes'] += len(result['passes'])
            n[{'present': 'present_cuts', 'missing': 'missing_cuts', 'invalid_metadata': 'invalid_metadata', 'withheld': 'withheld'}[c['cut_status']]] += 1
            n['zero_pass_with_cut'] += int(c['cut_status'] == 'present' and c['pass_count'] == 0)
            for row in result['passes']:
                pf.write(json.dumps(row, allow_nan=False) + '\n')
                first = first or row
    summary = dict(recording_count=len(rows), pass_count=sum(v['passes'] for v in counts.values()), by_source=counts,
                   elapsed_s=time.monotonic() - begun, aircraft_hash_namespace=NAMESPACE, radius_m=RADIUS_M,
                   method=METHOD, all_aircraft_completeness=False, script_sha256=version)
    write_json(a.out / 'coverage_summary.json', summary)
    print(json.dumps(summary, allow_nan=False))
    if first:
        print('real_pass_row=' + json.dumps(first, allow_nan=False))


if __name__ == '__main__':
    main()
