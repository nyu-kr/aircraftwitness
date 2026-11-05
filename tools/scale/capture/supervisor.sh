#!/usr/bin/env bash
cd "$(dirname "$0")"; mkdir -p ../log; PF=../log/supervisor.pid
if [ "${1:-}" = stop ]; then [ -s $PF ] && kill -TERM -- -"$(cat $PF)" && rm -f $PF; exit; fi   # its own process group: matching by name also hits the caller's shell
[ "$(ps -o pgid= $$ | tr -d ' ')" = "$$" ] || exec setsid "$0" "$@"
echo $$ > $PF
run() { while true; do python3 "$@"; rc=$?; echo "$(date -u +%FT%TZ) $1 exited rc=$rc, restart in 10 s"; sleep 10; done; }
run capture.py "$@" >> ../log/capture.log 2>&1 &
run prune.py >> ../log/prune.log 2>&1 &
wait
