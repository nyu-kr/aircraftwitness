#!/usr/bin/env python3
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.part')
    tmp.write_text(data)
    tmp.replace(path)


def main():
    entrant, packets, root = sys.argv[1:4]
    index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    with open(packets) as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    p = rows[index]
    assert re.fullmatch(r'[A-Za-z0-9_.-]+', p['packet']) and p['arm'] in ('P', 'W', 'A', 'D')
    assert p['dp_variant'] in ('au', 'released')
    assert p.get('start_slack_s', '60') in ('60', '300')
    out = Path(root) / f"{entrant}_{p['arm']}" / p['packet']
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    atomic(out / 'packet.json', json.dumps(p))
    start = time.time()
    variant = p['dp_variant'] if entrant == 'dp' else entrant
    codes = json.loads((HERE / 'pins.json').read_text())
    row = dict(packet=p['packet'], entrant=entrant, arm=p['arm'], status='crash', lat=None, lon=None,
               start_unix=None, code=codes['codes'][variant], prior_box=p['box'], t0_unix=float(p['t0_unix']),
               start_tolerance_s=float(p.get('start_slack_s', '60')))
    env = dict(os.environ)
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'RAYON_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        env[key] = '1'
    rc = 1
    try:
        for name, digest in codes['files'][variant].items():
            assert sha(name) == digest, f'pinned runtime changed: {name}'
        assert Path(p['audio_path']).stat().st_size > 0, 'audio missing or empty'
        cmd = ['timeout', '-k', '30', '1800', sys.executable, '-I', str(HERE / 'run.py'), entrant, str(out / 'packet.json'), str(out)]
        with open(out / 'task.log', 'w') as log:
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True).returncode
        if (out / 'candidate.json').exists():
            row.update(json.loads((out / 'candidate.json').read_text()))
        if rc in (124, 137):
            row.update(status='timeout', lat=None, lon=None, start_unix=None)
        elif rc and not (entrant == 'dp' and ((rc == 2 and row['status'] in ('out_of_scope', 'needs_input')) or (rc == 3 and row['status'] in ('no_match', 'no_pass')))):
            row.update(status='crash', lat=None, lon=None, start_unix=None)
        assert row['status'] != 'ok' or all(row[k] is not None for k in ('lat', 'lon')), 'empty success envelope'
        assert (row['lat'] is None) == (row['lon'] is None), 'partial coordinate'
        if row['lat'] is not None:
            assert all(math.isfinite(float(row[k])) for k in ('lat', 'lon'))
            assert -90 <= float(row['lat']) <= 90 and -180 <= float(row['lon']) <= 180
        if row['start_unix'] is not None:
            assert math.isfinite(float(row['start_unix']))
    except Exception as e:
        row.update(status='crash', error=str(e), lat=None, lon=None, start_unix=None)
        (out / 'error.txt').write_text(str(e) + '\n')
        rc = rc or 1
    row.update(input_sha256={k: sha(v) for k, v in p.items() if k in ('audio_path', 'cut_path', 'dem_path', 'dp_dem_path') and v and Path(v).is_file()},
               start_unix_run=start, end_unix_run=time.time(), alloc_cpus=os.environ.get('SLURM_CPUS_PER_TASK'),
               alloc_mem_mb=os.environ.get('SLURM_MEM_PER_NODE'), exit_code=rc, task_wall_s=round(time.time() - start, 3), host=socket.gethostname(),
               slurm=f"{os.environ.get('SLURM_ARRAY_JOB_ID')}_{index}", packet_sha256=sha(out / 'packet.json'),
               runner_sha256={name: sha(HERE / name) for name in ('task.py', 'run.py', 'pins.json')})
    atomic(out / 'row.json', json.dumps(row))
    atomic(out / 'rc', str(rc) + '\n')
    return rc


if __name__ == '__main__':
    sys.exit(main())
