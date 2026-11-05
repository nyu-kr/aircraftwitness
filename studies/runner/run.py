#!/usr/bin/env python3
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

R = Path('/scratch/NETID/geo/rerun')
U = Path('/scratch/NETID/geo/unheard')
CODES = {'ref': '7f02287038f5', 'au': '0.1.0(src447dbed+au)', 'released': '0.1.0(src447dbed)', 'unh': '55936de6a368d6110b5da8b44d841fc9d73faede'}


def call(cmd, log, env=None, cwd=None):
    with open(log, 'w') as f:
        return subprocess.run([str(x) for x in cmd], stdout=f, stderr=subprocess.STDOUT, env=env, cwd=cwd).returncode


def reference(p, o, row):
    e = R / 'ref'
    env = dict(os.environ, PYTHONPATH=str(e / 'sp'), GEO_TYPES=str(e / 'types.json'),
               DP_CMD=f'env PYTHONPATH={e}/dpsp {e}/py/bin/python3.12 -m dopplerprint')
    cmd = [e / 'py/bin/python3.12', '-W', 'ignore', e / 'bin/locate.py', '--audio', p['audio_path'],
           '--adsb', p['cut_path'], '--box=' + p['box'], '--t0', p['t0_unix'], '--slack', p.get('start_slack_s', '60'),
           '--out', o / 'ans', '--jobs', '3', '--row', f"blind:{p['packet']}:{p['arm']}", '--rows-file', o / 'raw.jsonl']
    if p.get('dem_path'):
        cmd += ['--dem', p['dem_path']]
    rc = call(cmd, o / 'tool.log', env)
    f = o / 'raw.jsonl'
    if f.exists() and f.stat().st_size:
        r = json.loads(f.read_text().splitlines()[0])
        row.update(status=r['status'], lat=r.get('lat'), lon=r.get('lon'), start_unix=r.get('clock_start_unix'),
                   n_passes_used=r.get('n_passes_used'), n_passes_detected=r.get('n_passes_detected'))
    return rc


def dopplerprint(p, o, row):
    d = R / 'dp'
    variant = p['dp_variant']
    package = d / 'dpau' if variant == 'au' else Path('/scratch/NETID/geo/prev/dp/rel')
    env = dict(os.environ, PYTHONPATH=f'{package}:{d}/dpsp:{d}/sp')
    py = d / 'py/bin/python3.12'
    work = o / 'work'
    work.mkdir()
    store = work / 'store'
    store.mkdir()
    os.symlink(d / 'typedb', store / 'typedb', target_is_directory=True)
    # The released build refuses unsupported boxes before requiring their inputs.
    w, s, e, n = map(float, p['box'].split(','))
    covered = variant == 'au' or (24 <= (s + n) / 2 <= 50 and -125.5 <= (w + e) / 2 <= -66)
    empty_days = []
    if covered:
        day = dt.datetime.fromtimestamp(float(p['t0_unix']), dt.timezone.utc).strftime('%Y-%m-%d')
        cmd = [py, d / 'bin/adsb_to_readsb.py', p['cut_path'], work / 'adsb', day]
        if call(cmd, o / 'convert.log', env):
            return 1
        cmd = [py, d / 'bin/split_days.py', work / 'adsb/traces', work / 'adsb/days']
        r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, env=env)
        (o / 'split.log').write_text(r.stderr)
        if r.returncode:
            return r.returncode
        for day in r.stdout.split():
            rc = call([py, '-m', 'dopplerprint', 'ingest', work / 'adsb/days' / day,
                       '--out', store / 'adsb/store', '--typedb', store / 'typedb'], o / f'ingest-{day}.log', env)
            if rc:
                return rc
        t0 = float(p['t0_unix'])
        days = sorted({dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d')
                       for t in (t0 - 2000, t0 + 2600)})
        for day in days:
            if (store / f'adsb/store/date={day}/manifest.json').exists():
                continue
            empty = work / 'empty' / day / '00'
            empty.mkdir(parents=True)
            (empty / 'trace_full_000000.json').write_text(json.dumps(dict(icao='000000', r='', t='',
                timestamp=dt.datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp(), trace=[])))
            rc = call([py, '-m', 'dopplerprint', 'ingest', empty.parent,
                       '--out', store / 'adsb/store', '--typedb', store / 'typedb'], o / f'empty-{day}.log', env)
            if rc or not (store / f'adsb/store/date={day}/manifest.json').exists():
                return rc or 1
            empty_days.append(day)
    cmd = [py, '-m', 'dopplerprint', 'locate', p['audio_path'], '--start', p['t0_unix'],
           '--start-tolerance', p.get('start_slack_s', '60') + 's', '--near=' + p['box'], '--cache', store, '--offline', '--out', o / 'out']
    if covered:
        cmd += ['--dem', p['dp_dem_path']]
    rc = call(cmd, o / 'tool.log', env, o)
    f = o / 'out/result.json'
    r = json.loads(f.read_text()) if f.exists() else {}
    lines = (o / 'tool.log').read_text().splitlines()
    if not r and rc == 3:
        r = dict(status='inconclusive', message=next((x.split(':', 1)[1].strip() for x in lines if x.startswith('Inconclusive:')), ''))
    if not r and rc == 2:
        message = ([x.strip() for x in lines if x.strip()][-1:] or [''])[0]
        r = dict(status='out_of_scope' if 'outside the area' in message else 'needs_input', message=message)
    st = r.get('status')
    status = {'found': 'ok', 'out_of_scope': 'out_of_scope', 'needs_input': 'needs_input'}.get(st, 'fail')
    if st == 'inconclusive':
        status = 'no_pass' if any(x in (r.get('message') or '').lower() for x in ('no passes', 'no aircraft')) else 'no_match'
    row.update(status=status, dp_status=st, dp_message=r.get('message'), empty_days=empty_days)
    if r.get('location'):
        row.update(lat=r['location']['lat'], lon=r['location']['lon'])
    if r.get('clock'):
        row['start_unix'] = r['clock']['start_unix']
    return rc


def unheard(p, o, row):
    t0 = float(p['t0_unix'])
    day = dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime('%Y-%m-%d')
    ds = int(dt.datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp())
    slack = float(p.get('start_slack_s', '60'))
    lo, hi = max(t0 - slack, ds), t0 + slack
    def hm(t):
        x = t - ds
        return f'{int(x // 3600)}:{int(x % 3600 // 60)}' if x % 60 == 0 else f'{int(x // 3600)}:{(x % 3600) / 60!r}'
    hist = p['unh_history_path']
    if not Path(hist, day, 'manifest.json').is_file():
        row.update(error='missing history day', status='needs_input')
        return 2
    w, s, e, n = map(float, p['box'].split(','))
    cmd = ['node_modules/.bin/tsx', 'scripts/where.ts', p['audio_path'], '--day', day,
           '--near', f'{(s+n)/2},{(w+e)/2}', '--radius', '10', '--from', hm(lo), '--to', hm(hi), '--history', hist]
    env = dict(os.environ, PATH=f'{U}/node/bin:{U}/ffmpeg-7.0.2-amd64-static:' + os.environ['PATH'])
    rc = call(cmd, o / 'tool.log', env, U / 'src')
    text = (o / 'tool.log').read_text()
    row.update(tool_rc=rc, window=[lo, hi], clamped=lo != t0 - slack)
    if rc:
        return rc
    m = re.search(r'^(\d+) aircraft tones', text, re.M)
    row['tones'] = int(m[1]) if m else None
    ans = re.compile(r'^(\d+)\. (-?[\d.]+), (-?[\d.]+)  started (\S+)  (\d+) flyover\(s\) heard well \(worth ([\d.-]+) of ([\d.-]+) nats\)', re.M)
    m = ans.search(text)
    if row['tones'] == 0:
        row['status'] = 'no_tone'
    elif not m:
        row['status'] = 'no_answer'
    else:
        row.update(status='ok', lat=float(m[2]), lon=float(m[3]),
                   start_unix=dt.datetime.fromisoformat(m[4].replace('Z', '+00:00')).timestamp())
    return rc


if __name__ == '__main__':
    entrant, packet_file, out = sys.argv[1:]
    p = json.loads(Path(packet_file).read_text())
    o = Path(out)
    code = CODES[p['dp_variant']] if entrant == 'dp' else CODES[entrant]
    row = dict(packet=p['packet'], entrant=entrant, arm=p['arm'], status='crash', lat=None, lon=None,
               start_unix=None, code=code, prior_box=p['box'], t0_unix=float(p['t0_unix']))
    start = time.monotonic()
    rc = {'ref': reference, 'dp': dopplerprint, 'unh': unheard}[entrant](p, o, row)
    row.update(wall_s=round(time.monotonic() - start, 3))
    (o / 'candidate.json').write_text(json.dumps(row))
    sys.exit(rc)
