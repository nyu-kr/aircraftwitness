#!/usr/bin/env bash
KV2=${KV2:-$(cd "$(dirname "$0")/.." && pwd)}; T=$KV2/tools; M=$KV2/manifests; RAW=$KV2/raw_meta; LOG=$KV2/logs/refresh.log
PY="$KV2/venv/bin/python -I"; ALL="a2o ecosounds aporee aporee-live eclipse-soundscapes gbif-sounds hushcity usgs-pointlay wildtrax xeno-canto inaturalist"
mkdir -p $KV2/logs $KV2/locks $KV2/stage $RAW
exec 9>>$KV2/locks/refresh.lock
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> $LOG; }
flock -n 9 || { log "refresh busy: previous run still holds the lock"; echo "refresh busy" >&2; exit 1; }
cpu=$(taskset -pc $$ | sed 's/.*: //; s/.*[-,]//'); RUN="nice -n 15 taskset -c $cpu timeout ${REFRESH_TIMEOUT:-43200}"
export V2RAW=$RAW

nrows() { $PY -c 'import csv,gzip,sys; csv.field_size_limit(10**9); print(sum(1 for _ in csv.reader(gzip.open(sys.argv[1], "rt", newline=""))) - 1)' "$1"; }
last() { ls "$1" 2>/dev/null | sort | tail -1; }

purge() {   # Stale index pages would hide new records.
  local r=$RAW/$1 s p
  case $1 in
    a2o|ecosounds) rm -f $r/sites.json $r/projects.json; s=$(last $r/pages); [ -n "$s" ] && p=$(last $r/pages/$s) && rm -f $r/pages/$s/$p;;   # Refetch the tail page when a shard gains an upper bound.
    aporee) ;;
    aporee-live) rm -f $r/scrape.json $r/locus_list.json;;
    eclipse-soundscapes) rm -f $r/page*.json;;
    gbif-sounds) rm -rf $r/pages $r/tasks.json;;
    wildtrax) rm -rf $r/zips $r/projects.json;;
    xeno-canto) rm -rf $r/dwca $r/gbif_issues;;
    inaturalist) p=$(last $r/pages) && rm -f $r/pages/$p;;
    *) rm -rf $r;;
  esac
  find $r -name '*.tmp' -o -name '*.part' 2>/dev/null | xargs -r rm -f
}

build() {
  local s=$1 b="$T/manifest_$1.py"
  [ -f $b ] || { echo "no builder $b"; return 1; }
  case $s in
    inaturalist) $RUN $PY $b fetch && $RUN $PY $b build;;
    gbif-sounds) mkdir -p $RAW/$s && $RUN $PY $T/gbif_prep.py $RAW/$s && $RUN $PY $b;;
    aporee) $RUN $PY $b --rescrape;;
    *) $RUN $PY $b;;
  esac
}

one() {
  local s=$1 stg=$KV2/stage/$1 out=$KV2/logs/refresh.$1.out f old new
  f=$stg/$s.csv.gz
  if [ -z "$REFRESH_REUSE_STAGE" ]; then
    [ $s = inaturalist ] && [ ! -d $RAW/inaturalist/pages ] && { log "$s skipped: cache not seeded"; return 0; }
    rm -rf $stg; mkdir -p $stg $RAW/$s; : > $out
    [ $s = ecosounds ] && ln -s $M/a2o.csv.gz $stg/a2o.csv.gz   # ecosounds drops rows A2O already lists
    purge $s
    export V2MAN=$stg
    build $s >> $out 2>&1 || { log "$s FAILED builder: $(tail -n 3 $out | tr '\n' ' ' | cut -c1-240)"; return 1; }
  fi
  [ -s $f ] && [ -s $stg/$s.counts.json ] || { log "$s FAILED builder wrote no $s.csv.gz/$s.counts.json"; return 1; }
  { $RUN $PY $T/policy.py $f && $RUN $PY $T/check_manifest.py $f; } >> $out 2>&1 || { log "$s FAILED policy/check: $(tail -n 3 $out | tr '\n' ' ' | cut -c1-240)"; return 1; }
  new=$(nrows $f) || { log "$s FAILED cannot count staged rows"; return 1; }
  if [ -s $M/$s.csv.gz ]; then
    old=$(nrows $M/$s.csv.gz) || { log "$s FAILED cannot count current rows"; return 1; }
    [ "$(zcat $f | tr -d '\r' | md5sum)" = "$(zcat $M/$s.csv.gz | tr -d '\r' | md5sum)" ] && { log "$s unchanged old=$old new=$new"; rm -rf $stg; return 0; }
    [ $((new * 100)) -ge $((old * 98)) ] || { log "$s REFUSED shrink old=$old new=$new (<98%), staged copy kept in $stg"; return 1; }
  else old=0; fi
  cp $stg/$s.counts.json $M/.$s.counts.json.new && mv -f $M/.$s.counts.json.new $M/$s.counts.json &&
  cp $f $M/.$s.csv.gz.new && mv -f $M/.$s.csv.gz.new $M/$s.csv.gz || { log "$s FAILED publish"; return 1; }
  log "$s updated old=$old new=$new"; rm -rf $stg
}

fail=0
for s in ${@:-$ALL}; do one $s || { fail=1; tail -n 1 $LOG >&2; }; done
exit $fail
