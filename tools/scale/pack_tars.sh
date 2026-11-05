#!/usr/bin/env bash
# Fixed metadata and sorted names make release archives reproducible.
set -e; export LC_ALL=C; S=$(cd "$1" && pwd); TAG=$2; mkdir -p "$3"; O=$(cd "$3" && pwd)
T="tar --sort=name --owner=0 --group=0 --numeric-owner --mode=u=rwX,go=rX --mtime=@0 --format=gnu -cf"
cd "$S/$TAG"
$T "$O/aircraftwitness-$TAG-passes.tar" labels/*.passes.jsonl.gz labels/*.label.json
$T "$O/aircraftwitness-$TAG-coverage.tar" labels/*.coverage.jsonl.gz
$T "$O/aircraftwitness-$TAG-days.tar" days
cp "$S/$TAG.json" "$O/"
cd "$O" && sha256sum aircraftwitness-$TAG-*.tar $TAG.json > SHA256SUMS
