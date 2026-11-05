# geo-live blind prospective test: pre-registered scoring rule (sealed; scored by scratch-geo-verify)

Written by scratch-geo-live before any captured window was located. Its sha256 is published in live/COMMITS.tsv and sent to
scratch-geo-verify at freeze time; any later edit is a new version with its own line there, and scoring uses the version in force
when the window was captured.

## Method under test (frozen)
- geoloc bin/ at tag blind-v2 = commit 06869ad (tagged 01:47 UTC 11-04 by geoloc; replaces blind-v1, whose big-grid prefilter
  dropped turning aircraft; nothing was located with v1), copied to live/frozen/geoloc-blind-v2 (sha256 of its sorted *.py
  sha256 list: 4976e1804cd144cdf9df54651effd2b8042661f9ea327a7712230c30c7eafb18). Detector dopplerprint discover 0.1.0, ridges re-traced (locate.py default, no --no-retrace).
- locate.py defaults (step 200 m, dmin 200, dmax 10000, vtol 1.0, tol 3, nudge 4, temp 20 C, top 6), DEM geoloc/data/dem_conus30s.tif
  in CONUS, else none. Bird prior variant (task LBird only): geo-birds ens_soft25_eff_c2, T 1.375, cells with cum <= 0.25.
- Runner live/bin/run_blind.sh as hashed in COMMITS.tsv before the first run. Nothing about the method changes after the freeze;
  a bug that stops a run (crash) may be fixed only if the fix is logged with its time, and then every window is rerun.

## Units
- v2 (01:48 UTC): the PROSPECTIVE set is windows whose slot starts at or after 01:50:00 UTC 11-04 (after the blind-v2 tag).
  Windows from 01:25:41 to 01:50 are located the same way and reported apart as pre-v2. E1 is computed on the prospective set.
- A window is one stream's 10-minute UTC slot [hh:m0, +600 s), captured after FREEZE_UTC, with at least 570 s of audio.
- Streams enter the test only by a line in sealed/truth.jsonl written before the stream's first window starts. Truth = the place
  the stream's publisher states (lat, lon, precision_m, evidence URL). Strata: airport (runway/plane-spotting cams), nature
  (institutional nature cams, open mics in nature), town (town/city/harbor/beach cams), openmic (Locus Sonus and similar).
- Windows are excluded only if: audio is silent (mean volume < -70 dBFS) or missing > 30 s; or the stream visibly moved or
  changed camera place (logged before scoring). No exclusion uses the audio's content or the locator's output.
- v4 (02:13 UTC, before any window was located; capture now runs to Wed 23:59 EST, final scores Thu 06:00 EST): each packet
  gets a priority class from the first byte of its random id (class = byte // 64, so 4 classes of ~25%); the runner takes
  class 0 in capture order, then class 1, and so on. Scores at any deadline use every window located by then; the class-0
  windows form a time-spread random quarter that is never chosen by content. Before v4 the order was plain capture order.

## Inputs geoloc receives (per window, opaque random id)
- audio (FLAC, mono, native rate), t0 = capture time of the first sample, slack 30 s:
  HLS streams: EXT-X-PROGRAM-DATE-TIME of the window's first segment (server clock); other streams: arrival-envelope time
  on the pod clock (earliest arrival of each sample).
- ADS-B: the live log (adsb.lol + adsb.fi point queries over fixed 250 nm circles whose centres are offset at random from
  the streams) cut to the region box + 0.15 deg and to [t0 - 900 s, t0 + 1500 s]. From 02:12 UTC adsb.lol is polled for
  the worldwide VFR squawks 1200 and 7000 instead (no place in the query; ~5 s cadence for VFR aircraft), adsb.fi keeps
  the circles; the union is cut the same way.
- Task LB (primary): a square region 2.0 deg of latitude high and the same km wide (about 222 x 222 km), placed uniformly at
  random so it contains the truth (box in sealed/windows.jsonl). v3 wording (no change to the task): this is a 222 km box
  (0.049 M km2), NOT a bird region at its median size: bird regions at cum <= 0.25 (birds/regions, 3629 XC files) have median
  0.31 M km2 (IQR 0.14-0.40 M km2), so LB is finer than about 80% of them. Larger boxes are not tested because the live
  ADS-B circles reach 463 km from their centres.
- Task LT1 (stage 2, sequential unmasking): a 0.2 deg (about 22 km) square placed the same way; released for a window only
  after its LB answer file is written and its sha256 is in live/answers/COMMITS.tsv.
- Task LBird (exploratory): bird prior cells from BirdNET on the window, only where at least one species is detected.

## Scoring (by geo-verify from the sealed truth)
- error = great-circle distance from the locator's best point to the truth, minus nothing (precision_m reported beside).
- E1 (primary): share of ALL included windows (streams with precision_m <= 1000; coarser streams reported apart) of task LB whose answer is within 1 km of the truth (no_pass and no_match count
  as misses); Wilson 95% CI; overall, per stratum, US vs non-US.
- E2: among answered windows, median error and shares within 1 km and 5 km; status counts (ok, no_match, no_pass).
- E3: the same for LT1; and the calibration of the locator's own uncertainty (mean predicted vs observed within its HPD).
- Controls (same frozen runner): C1 wrong time: t0 + 7200 s with the ADS-B of that time (or t0 - 7200 s if not yet logged);
  C2 wrong place: the window's audio with another stream's LB region and ADS-B. A real effect needs E1 above C1 and C2.
  Controls run for a seeded third of windows (sealed/secret.txt), queued as ordinary packets the runner cannot tell apart;
  LB boxes and control choices are in sealed/windows.jsonl, derived from sealed/secret.txt and the window id.
  Chance for LB: a uniform point in the box is within 1 km with probability 6.4e-5.
- Airport stratum is reported on its own as the easy case and is never pooled into a headline without the pooled-minus-airport
  figure beside it.
- Reported as captured, every window, by geo-verify; no window or stream is dropped after its first location.

## v5 (02:52 UTC 11-04; 13 LB answers exist, none scored, no LT1 run yet): disk budget and a decoding fix
- Sampling: from slot 03:00 UTC 11-04 a stream's slot is captured only if the first byte of sha256("secret:stream:slot") is
  below 32 (12.5% of slots, fixed before capture, content-free). Class of every window built from now: 0 (byte < 32), 1 (< 64),
  2 (< 128), else 3; its random id's first byte is drawn inside that class's v4 range, so the runner order (class, then t0) is
  unchanged. Packets are built lazily in that order, at most 24 waiting for an LB answer. Packets built before v5 keep their class.
- Decoding: HLS windows are decoded by one continuous AAC decoder; each contiguous run is anchored at its segment's
  PROGRAM-DATE-TIME and missing or short segments are zero-filled (filled_s in windows.jsonl), so sample k is at t0 + k/sr.
  More than 30 s zero-filled = missing (excluded). The v4 path concatenated segments, so windows with gaps failed the length
  check ("decode: rc 0"); those 5 are rebuilt under v5 with new ids. On a gapless window both paths agree (corr 0.99999999).
- Icecast audio is written as FLAC at capture (same samples); the packet audio is that file.
- Raw capture is deleted once its window is decided; packet audio after its LT1 answer (controls after LB; a window with
  controls only after both exist). A crash fix reruns every window whose audio is still held; answered windows keep answers.
- C1 uses t0 - 7200 s when that time is logged after 02:15 UTC (same ADS-B feeds as the window), else t0 + 7200 s.
- Runner: skips packets without audio, and for LT1 packets whose LT1 box is not released (before, those would have been
  marked failed). LBird (exploratory) is not run: no compute left.
- windows.jsonl may hold a "queued" line before a window's final line; the last line per wid is its record.

## v6 (04:39 UTC 11-04): controls follow sequential unmasking
- Control packets no longer copy the parent's released LT1 box (11 C1 packets built before v6 carry it: reported apart).
- Every control gets its own LT1 box after its LB answer is hashed, placed by its own id around its box stream's place
  (C1: the true place, wrong time; C2: the other stream's place). Control audio is deleted after its LT1 answer.
