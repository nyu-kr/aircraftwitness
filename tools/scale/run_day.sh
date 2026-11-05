#!/usr/bin/env bash
d=$1; tag=$2; W=${KV2:-$(cd "$(dirname "$0")/.." && pwd)}; cd $W; PY=$W/venv/bin/python
mkdir -p $W/locks; exec 9>$W/locks/$d.lock; flock -n 9 || { echo "day $d already running"; exit 75; }   # The day lock prevents concurrent runs after a scheduler restart.
raw=$W/raw/$d; mkdir -p points; parts=()
ok() {   # Retry 429, 5xx and timeouts rather than treating them as missing assets.
  local c w
  for w in 60 120 240 stop; do
    c=$(curl -s -o /dev/null -L --connect-timeout 60 --max-time 180 -r 0-0 -w %{http_code} $1); case $c in 200|206) return 0;; 000|5??|429) ;; *) return 1;; esac
    [ $w = stop ] && { echo "$(date +%FT%T) $d probe HTTP $c $1" > $W/locks/stop.github; echo "github stopped after 3 backoffs: probe HTTP $c $1"; exit 1; }
    echo "probe HTTP $c $1, retry in $w s"; sleep $w
  done
}
slot() { while [ ! -e $W/locks/stop.github ]; do for i in $(seq 0 $((${KV2_GH_SLOTS:-32} - 1))); do exec 7>$W/locks/fetch$i.lock; flock -n 7 && return 0; done; sleep 10; done; return 1; }   # Shared slots limit concurrent CDN fetches across jobs.
get() {   # Repeated transfer failures stop GitHub fetches across jobs.
  local c r w
  for w in 60 120 240 stop; do
    c=$(curl -sSL -C - --connect-timeout 60 --speed-limit 100000 --speed-time 120 --limit-rate ${KV2_RATE:-60M} -o $2 -w %{http_code} "$1"); r=$?
    case $r/$c in 0/2??) return 0;; */000|*/5??|*/429|*/2??) ;; *) echo "HTTP $c (curl $r) $1"; return 1;; esac
    [ $w = stop ] && { echo "$(date +%FT%T) $d HTTP $c (curl $r) $1" > $W/locks/stop.github; echo "github stopped after 3 backoffs: HTTP $c (curl $r) $1"; return 1; }
    sleep $w
  done
}
[ -n "$KV2_LABEL_ONLY" ] || [ -z "$tag" ] || {
  slot || { echo "github stopped: $(cat $W/locks/stop.github)"; exit 1; }
  mkdir -p $raw; urls=()
  for y in ${d:0:4} $((${d:0:4} + 1)); do   # a Dec 31 release is published in the next year's repo
    base=https://github.com/adsblol/globe_history_$y/releases/download/$tag/$tag.tar
    if ok $base; then urls=($base); else for s in aa ab ac ad ae af ag ah ai aj; do ok $base.$s || break; urls+=($base.$s); done; fi
    [ ${#urls[@]} -gt 0 ] && break
  done
  [ ${#urls[@]} -gt 0 ] || { echo "no assets $d $tag"; exit 1; }
  for u in "${urls[@]}"; do
    f=$raw/$(basename $u); parts+=($f); [ -f $f ] && continue
    get "$u" $f.part && mv $f.part $f || { echo "download failed $u"; rm -rf $raw; exit 1; }
  done
  exec 7>&-
  nice -n 10 $PY tools/day_filter.py $d recs.parquet points "${parts[@]}"; rc=$?
  [ -n "$KV2_KEEP_RAW" ] || rm -rf $raw
  [ $rc = 0 ] || exit $rc
}
flock $W/place_elev.lock nice -n 10 $PY tools/place_elev.py $W || exit 1
nice -n 10 $PY tools/day_label.py $d recs.parquet $W || exit 1
n=$(date -u -d "$d + 1 day" +%F)   # the next day may hold recordings that started today and were pending
if [ -f labels/$n.label.json ] && grep -q '"pending": \["' labels/$n.label.json; then   # under the next day's lock: its own run may be writing the same files
  exec 8>$W/locks/$n.lock; flock -w 9000 8 && grep -q '"pending": \["' labels/$n.label.json && nice -n 10 $PY tools/day_label.py $n recs.parquet $W
fi
exit 0
