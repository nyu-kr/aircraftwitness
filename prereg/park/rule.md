# the zenodo worker retrospective blind test: pre-registered rule v1 (scored by the verifier)

Written by the zenodo worker before any recording was located. Its sha256 goes to the verifier's log before the first run; a later
edit is a new version. Set: Zenodo 20038954 (latest version of concept 18041380; 18041381 is v1 of the same files), CC-BY-4.0,
Eastern North America forest soundscapes, fixed sites (public coordinates), 2023 recordings only (565 of 1302).

## Method under test (frozen, same as the live test)
- geoloc tag blind-v2 = commit 06869ad, copy live/frozen/geoloc-blind-v2 (sha256 of its sorted *.py sha256 list
  4976e1804cd144cdf9df54651effd2b8042661f9ea327a7712230c30c7eafb18); locate.py defaults as in live rule v5; DEM dem_conus30s.tif
  (all sites are in CONUS); no bird prior. Runner zenodo/bin/run_blind.sh (copy of live/bin/run_blind.sh with the root changed and
  answers in zenodo/answers); sha256 of the runner, make_packets_zen.py and stage2z.py go to a COMMITS line before the first run.
- Nothing about the method changes after the first run; a crash fix is logged with its time and every packet is rerun.

## Clock (fixed here, before any location; not tuned on results)
- t0 = the metadata Time (HHMM) read as local EDT = UTC-4 (all dates are 2023-05-15..07-21), taken at the middle of the stated
  minute (+30 s). slack_s = 120 (60 s minute rounding plus 60 s recorder drift).
- Evidence: (1) Time minus computed sunrise in EDT is within 2 min of +40 min (NOAA sunrise, +-1 min) in 358 of 417 Acadia 2023 recordings
  (a designed sunrise+40 min clip start in local time); (2) Time lies inside the hour of the file-name start in 99.6% of recordings (file names are hourly
  source files, 050000, 060000, ..., odd seconds after recorder restarts; the 10-minute clip is cut from the source file at Time).
  One recording with Time before its file-name start is excluded.
- Not confirmed by flights: 10 calibration recordings, a vote over all recordings and a low-frequency-noise correlation against
  predicted aircraft range found no peak, because adsb.lol holds almost no low aircraft near these sites at dawn (aircraft within
  10 km and under 15000 ft in the stated window: 4 of 565 recordings). EDT vs EST therefore rests on the metadata alone; the
  locator searches +-120 s only.
- Calibration recordings (ItemIDs, chosen before any location by: Acadia site within 13 km of Bar Harbor airport, most clear
  passes): 6587 6696 6652 6588 6593 6594 6607 6651 6668 6712. They are excluded from the test.

## Test set (fixed in verify/sealed/zenodo_key.csv before the boxes exist)
- A packet is one 10-minute recording. Included: 2023, not calibration, Time not before the file-name start, at least one clear
  pass by the bench worker bin/detect.py (fall >= 4%, >= 80% monotone, >= 8 dB, >= 12 s; run on the native audio), and a non-empty
  adsb.lol cut for the box and window (a recording failing the last test is logged as "no ADS-B", not dropped silently).
  Result: 232 test recordings of 565 (322 without a clear pass, 10 calibration, 1 bad time).
- Run order: class 0 first, then 1, 2, 3 (first id byte // 64). Class 0 holds the recordings that have an audible-source
  candidate in adsb.lol (an aircraft within 10 km and under 15000 ft at closest approach inside [t0-150, t0+750]; 2 in the test
  set) plus about a quarter of the rest; the other classes follow a hash of the ItemID with the public salt zenodo-retro-v1. Scores
  at any deadline use every recording located by then.

## Inputs geoloc receives (per recording, opaque random id)
- audio.flac: mono, native 32 kHz, re-encoded with no tags and no file name; meta.json: t0_unix, slack_s 120, duration_s,
  region_box_WSEN; adsb.parquet. No site name, park, date string beyond t0, annotation or file name.
- ADS-B: adsb.lol 2023 daily history (all target types), cut to the box + 0.15 deg and to [t0-900 s, t0+1500 s].
- Task LB (primary): a square 2.0 deg of latitude high and equally many km wide, placed uniformly at random so it contains the
  site (box_for of live, secret made by the verifier after the key was fixed; the verifier recomputes every box). Task LT1: a 0.2 deg
  square, released by stage2z.py only after the LB answer sha256 is in zenodo/answers/COMMITS.tsv.

## Scoring (the verifier, from the sealed key)
- error = great-circle distance from the locator's best point to the site. Site coordinates carry ~1 m digits; precision claimed
  1 km, so a hit is within 1 km.
- E1 (primary): hits / all test recordings for LB (no_pass and no_match are misses). Because 232 recordings sit at 48 sites,
  the primary unit is the site: per-site hit share averaged over sites, 95% CI by bootstrap over sites; recording-level Wilson
  interval reported beside it and labelled too narrow. Also E1b over all 555 non-calibration recordings with a valid clock
  (recordings without a clear pass count as misses) and E1c over the audible-source-candidate subset.
- E2: among answered recordings, median error, share within 1 and 5 km, status counts; and the false-answer rate: share of
  answered recordings with no audible-source candidate (the locator names a place although no aircraft was nearby).
- E3: LT1 as E2; calibration of the predicted uncertainty.
- Controls (same runner, packets indistinguishable): C1 wrong time, t0 + 7200 s with the ADS-B of that time; C2 wrong place,
  the recording's audio with another recording's LB box and the ADS-B of that box at the same t0. For a seeded third of the
  test recordings (secret of the verifier). A real effect needs E1 above C1 and C2.
- Chance for LB: a uniform point in the box is within 1 km with probability 6.4e-5.
- Reported as located, every packet; none dropped after its first location. Expectation stated here: with 2 audible-source
  candidates in 232, the test measures mostly specificity (false answers on noise passes); a high E1 is not expected.

## v2 (before any packet existed; v1 sha 2831c9f80b9a): corrections and disclosure
- 232 test recordings sit at 48 sites (v1 said 104, the count of the whole archive).
- Disclosure for any result: t0 alone matches one park for all 232 recordings and one site for 72 in the public metadata, so blindness
  rests on the frozen code never reading the archive's metadata, not on the packets alone.
- Controls get their own LT1 box after their own LB hash (stage2z.py, box_for with the control's id and the box owner's site); a
  control's meta.json is written fresh and never copies the parent's boxes.

## v3 (before any location; v2 sha 82ed887f3c7c): where it runs, and LT1 scope
- Locating runs on dtn012, not on d (the live worker's request, d cores belong to the live test): the same frozen files (live/frozen/geoloc-blind-v2/bin, sha256
  of the sha256sum list of bin/* 70a89e20a8fa46bb9ed9b6c1691ac413da4b56e6b912d4e9ce24f5ba67bbf53e on both machines), the same DEM (dem_conus30s.tif, 25626048 bytes,
  sha256 8b3cd3d81a61cc03... on both), venv /scratch/NETID/geo/global/venv with numpy 2.5.4, scipy 1.18.1, pandas 3.1.0, dopplerprint 0.1.0
  (the same versions as d's geoloc venv), runner bin/run_dtn.sh (the live runner's order, arguments and hash log; --jobs 1).
  Packets are built on dtn012 by bin/build_dtn.py from the cuts, the audio never leaves dtn012; answers and COMMITS.tsv are copied to d for scoring.
- LT1 is released by stage2z.py on d from the mirrored answers and COMMITS.tsv (hash check as in live) and run after all LB packets
  have an answer, as far as time allows; E3 is reported on the packets that have an LT1 answer.
- Superseded and never used: the d-side builder (make_packets_zen.py, pull_loop.sh, run_blind.sh); three audio files pulled to d
  before the switch sit in zenodo/pk_in and are not packets.
