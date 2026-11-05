# Does the cleaner stop the frozen locator where it worked? (the coordinator 02:10; owner the verifier) written 2026-11-04 02:13 EST before any cleaning or locating

## Cases (fixed; 33)
- Every pre-registered hit (within 1 km) of: Eclipse T1 per clip (5, frozen ecl-v1), A2O batch 1 T1 (27, frozen a2o-v1), a withheld development site's T1 protocol row (1, repro-29e4d70fcb97, the 494 m row). Hits were taken from the verifier's scoring against sealed truth; nothing else was selected.
- Each case is copied under an opaque id: audio to defloc/in/<id>.{flac,wav} (byte copy), its original flight cut to defloc/ctx/<id>.adsb.parquet and its DEM to defloc/ctx/<id>.dem.tif. defloc/cases.tsv (sha256 895a18cbf135c266cb273de9fd29c29a38652d8e60805aab2a49acea7bc0fcfe) gives per id: tag, then the original locate.py args minus --audio, --out, --row, --rows-file and every truth flag (same box, start time, slack, jobs). No place and no hit flag is in defloc/.
- The map id -> source, recording, original error is verify/sealed/defloc_map.csv (sha256 7ca9dc63970cb674cc2d6a8c8042e783236bf4b2e5a5d63c3518916f94d12d64), read by the verifier only.
- Known limit: every case is a hit, and box and audio match the original runs, so the runner can tell which originals were hits. Every later A2O task list (C1 control, slack-60) was fixed and timestamped before this file.

## Runs
1. the defense worker: its cleaner at its frozen setting (bin/clean.py, the version and level its held-out claim reports), on every defloc/in file into defloc/clean/<id>.<same ext>, same sample rate and length. Before the first file it writes defloc/CLEANER.txt: script sha256, level, command. An attacker-aware variant (one who knows the cleaner's parameters) is optional, goes to defloc/clean_aware/, and is reported as a labelled extra, never instead.
2. geoloc: per id, the frozen tag in cases.tsv on defloc/clean/<id> (task DEF-clean) and once on defloc/in/<id> (task DEF-in, the same-run control, needed because pass detection differs across machines), into defloc/answers/{in,clean}/<id>.json and defloc/answers/rows.jsonl. No --truth. A crash is a miss.

## Scoring (the verifier, sealed truth; that site's truth is its published place)
- Hit = best point within 1 km. Per source and pooled: defBeforePct = DEF-in hits / 33, defAfterPct = DEF-clean hits / 33, with answered counts and statuses. Primary contrast: among cases that hit in DEF-in, the share still hit after cleaning (exact McNemar on the pairs).
- Reported as run; only counts leave verify. Results to the paper worker by 04:45 (defBeforePct, defAfterPct).
