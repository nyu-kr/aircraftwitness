# A2O batch 2 blind set (a2o_blind_b2, geo-sources 2026-11-03 ~23:58): pre-registered run (geoloc), written 2026-11-03 23:59 EST before any batch 2 run
- code: same frozen a2o-v1 as batch 1 (frozen/a2o-v1); cut/task/DEM tools generalized to take the batch dir (default arguments reproduce batch 1's tasks.tsv byte for byte)
- inputs read: a2o_blind_b2/{manifest.parquet (packet, box, utc_start, dur_s, audio, passes, clock, flights_day), boxes.csv (sealed 20 km boxes), flights/adsblol}. No place or score read.
- protocol identical to batch 1 (geoloc/data/a2o/PREREG.md): per packet box, flights_day file only, box + 0.3 deg, start - 20 min .. end + 21 min; GLO-90 DEM box +- 0.35 deg; T1 per packet, slack 5 s, step 200 m, dmax 10 km; one box per packet so no pooled run
- geo-sources' note, read before the run: cruise traffic only (median 37,600 ft), stated time holds weakly. Not acted on: slack stays 5 s.
- order: after batch 1's pre-registered run, before batch 1's post hoc slack 60 s run. Rows subset A2Ob2, geoloc/data/a2o_b2/answers/rows.jsonl
- scoring: geo-verify against sealed truth; success within 1 km
- 01:23 EST, before any batch 2 run: a post hoc slack 60 s run (rows T1-slack60-posthoc, answers/T1ph60, tasks_ph60.tsv) follows batch 1's post hoc run, as for batch 1. Prompted by geo-clock's clock-only A2O fit (01:2x: other A2O recorders -4.5 to -9.2 s), which uses no batch 2 data; slack 60 s is the Eclipse value, not tuned. The pre-registered batch 2 result stays slack 5 s.
