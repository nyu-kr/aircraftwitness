#!/bin/bash
#SBATCH --partition=cs
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=03:00:00
#SBATCH --job-name=kv2post
#SBATCH --output=slurm/log/post_%j.out
cd "$SLURM_SUBMIT_DIR"; TAG=$1; [ -n "$TAG" ] || exit 2
fix() { printf '%s\n' "$@" | xargs -r -P 8 -I{} env KV2_LABEL_ONLY=1 bash tools/run_day.sh {}; }
bad=$(venv/bin/python tools/label_check.py .); echo "$(date +%T) inconsistent: ${bad:-none}"; fix $bad
pend=$(for f in $(grep -l '"pending": \["' labels/*.label.json); do d=$(basename $f .label.json); [ days/$(date -u -d "$d - 1 day" +%F).filter.json -nt $f ] && echo $d; done)   # Retry pending entries only after the prior day is filtered again, keeping other frozen files byte-stable.
echo "$(date +%T) with pending that can now resolve: $(echo $pend | wc -w)"; fix $pend
venv/bin/python tools/label_check.py . || { echo "$(date +%T) still inconsistent"; exit 1; }
venv/bin/python tools/snapshot.py $TAG || { echo "$(date +%T) snapshot failed"; exit 1; }
bash tools/pack_tars.sh snapshots $TAG snapshots/$TAG.release; rc=$?; echo "$(date +%T) pack rc $rc"; cat snapshots/$TAG.release/SHA256SUMS; exit $rc
