import os, sys; sys.path.insert(0, 'tools'); __file__ = 'tools/sched.py'
exec(compile(open('tools/sched.py').read().split('\nwhile True:')[0], 'sched_head', 'exec'))
fh, lh, urgent = day_hashes(); rel = releases()
import fcntl
def held(d):   # run_day.sh holds locks/DATE.lock while it runs
    try:
        with open(f'locks/{d}.lock', 'a') as f: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return False
    except BlockingIOError: return True
running = {d for d in fh if held(d)}
filt = sorted(((n, d) for d, (h, n) in fh.items() if d in rel and done_hash(d) != h and d not in running), reverse=True)
fd = {d for _, d in filt}
lab = [d for d, h in lh.items() if d not in fd and d not in running and os.path.exists(f'points/{d}.filter.json') and label_hash_done(d) != h]
with open(sys.argv[1] if len(sys.argv) > 1 else 'slurm/days.txt', 'w') as f:
    for _, d in filt: f.write(f'{d} {rel[d]}\n')
    for d in lab: f.write(f'{d} -\n')
print(len(filt), 'filter days,', len(lab), 'label-only days,', len(running), 'running under sched:', sorted(running))
