#!/bin/bash
#SBATCH --partition=cs
#SBATCH --cpus-per-task=4
#SBATCH --mem=14G
#SBATCH --time=03:00:00
#SBATCH --job-name=kv2day
#SBATCH --output=slurm/log/%A_%a.out
cd "$SLURM_SUBMIT_DIR"
read d tag < <(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" ${1:-slurm/days.txt})
[ -n "$d" ] || exit 0
echo "$(date +%T) $d $tag on $(hostname)"
if [ "$tag" = "-" ]; then bash tools/run_day.sh $d; else KV2_RATE=80M bash tools/run_day.sh $d $tag; fi
rc=$?; echo "$(date +%T) rc $rc"; exit $rc
