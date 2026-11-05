# PREREG: unheard day mode, blind, on held-out field recordings

Written 2026-11-04T06:42-05:00 by the unheard worker, before any blind run. Any later change gets a new timestamped line below; a run chosen after a score is labelled post hoc.

Tool: gwendall/unheard at 55936de6a368d6110b5da8b44d841fc9d73faede (PR 43 merge, 2026-11-03), unmodified, Node 22.23.4, deps from its pnpm-lock (pnpm 10.20.0 --frozen-lockfile).

Entry point: its own CLI, `tsx scripts/where.ts <audio> --day D --near LAT,LON --radius 50 --history DIR`, one process per packet, every other option at its default (no --from/--to, --temp 15). This is the code path of `evals/wherewhen.eval.ts` in day mode with a 100 km square (whereWhen with window = the whole UTC day minus the clip length, area side 100 km, terrain from its own tiles); the eval's only extra inputs are Livermore's weather (air temperature) and a truth probe used for diagnostics, both unavailable blind. Audio enters through the CLI's own path (ffmpeg to mono 8 kHz for FLAC).

Flight data: adsb.lol globe_history release `vYYYY.MM.DD-planes-readsb-prod-0` for the packet's UTC day, indexed by unheard's own `scripts/history-index.ts` (build 1, tar1090-db types 2ab834a, <= 10,000 ft) on the NYU cluster; only the tiles the CLI reads for that square (tilesAround, 15 km margin) are copied to the run directory.

Task: day only. Inputs per packet: the audio, the UTC day of its start (from the manifest), and a 100 km square drawn and sealed by the verifier (boxes100.csv, sha eb5ef258d1cd), truth uniform in the square. Each process reads one packet's square only; nothing combines squares across packets. No ES or A2O site metadata is looked up.

Sets, run in this order, within a set by packet id ascending:
1. A2O batch 1: all 295 packets of a2o_blind/manifest.parquet (300 s, GPS-timed).
2. Eclipse T1: the 187 rows of bench/ecl/manifest_noplace.csv with audio (55 to 60 s, 8 kHz).

Limits: 1 thread per packet, nice 15, at most 3 at once on d; 30 min wall per packet, over it = timeout.

Rows (rows.jsonl, one per packet as it finishes): id, set, status in {ok, no_tone, no_answer, timeout, crash}, lat, lon, start_utc (best answer, null if none), confident (the tool's flag), answers (ranked, as printed), tones, flights, fits, wall_s, commit.

Success rule (primary, scored by the verifier): the best answer (answer 1) within 1 km of sealed truth. Every status but ok with an answer is a miss. Secondary, reported beside it: unheard's own rule, within 1 km and start within 30 s of the manifest start; and truth among the ranked answers (post hoc only). Chance: a uniform guess in the square lands within 1 km with probability pi/10^4 = 0.031 % per packet (0.09 expected hits in 295, 0.06 in 187).

Reproduction (step 1, not blind): `SIDES=100000 WINDOWS=day ONLY=singles` and `ONLY=pairs` of `evals/wherewhen.eval.ts` on the Livermore set, its own indexer for 2026-05-09, 12, 13, 14, 16, 17. Target from PR 43: pairs within 250 m and 1 km 6 of 20, singles within 1 km 1 of 40.

2026-11-04T07:25-05:00 addendum, before any Eclipse run and after 1 A2O row: to fit the coordinator's 13:00 stop, the Eclipse set runs at the same time as A2O, on the NYU cluster node dtn012 (same commit and lockfile, Node 22.23.4, static ffmpeg 7.0.2, which only decodes since the clips are already 8 kHz mono), up to 3 at once there, nice 10. A2O stays on d, at most 3 at once. Each packet's search is single-threaded and independent of host and concurrency, so the settings and rule above are unchanged. Whatever is done at 12:30 is scored as each set's longest complete prefix in id order.

2026-11-04T08:38-05:00 note: the driver marked 4 Eclipse packets "crash" without running the tool because their square held no flight tile that day (0 tiles listed). Driver fixed to run where.ts anyway; their rows are replaced by the tool's own outcome. No tool setting changed; no scored row was seen.

2026-11-04T08:50-05:00 addendum: the coordinator raised d's limit to 9; A2O goes from 3 to 9 at once on d (single-threaded searches, settings unchanged).

2026-11-04T09:22:47-05:00 batch 3 preregistration by the unheard worker, before any batch 3 run: one unchanged pass on all 360 packets at the 23 fresh sites of a2o_blind_b3/manifest.parquet, ordered by opaque packet id. Same tool commit 55936de6a368d6110b5da8b44d841fc9d73faede, lockfile, CLI, whole UTC day, 100 km squares, default temperature, flight source and indexer, 30 minute per-packet timeout, and scoring rules as above. The verifier draws and seals new 100 km squares keyed by opaque packet id; this worker never sees site positions or combines squares. Run on d at nice 15, single-threaded searches, at most 9 jobs. Rows: unheard/rows_b3.jsonl. Primary success: first answer within 1 km, all other outcomes misses; the verifier alone scores against sealed truth. Secondary timing and ranked-answer results remain secondary/post hoc as above. No batch 3 result is available to this worker at preregistration.

2026-11-04T09:36:29-05:00 pre-run input-availability note: the exact upstream default release v2025.06.09-planes-readsb-prod-0 returns HTTP 404, independently confirmed through GitHub's release API. The tag inventory contains only prod-0tmp and staging-0tmp for that date, not the frozen default prod-0. No alternate pod, source, date or tool setting will be substituted. The four packets dated 2025-06-09 remain in the full 360-packet primary denominator; unless the exact source becomes available before launch, the unchanged driver's missing-history check records them as crash/miss, explicitly reported as unavailable flight input. This is an availability annotation before any batch 3 locator output, not a chosen search variant. All other dates continue unchanged.

2026-11-04T11:52:34-05:00 exact-source recovery, before any batch 3 locator run: release v2025.06.09-planes-readsb-prod-0tmp identifies the original path /var/globe_history/2025/06/09 and pod of origin planes-readsb-prod-0 in its primary release metadata (saved in archive_b3_2025-06-09.json). Its split tar assets have SHA256 2894c433ee7a9d4eefb118285251fc1e3712eb6ce2a083b4ddfb71f6c82901d4 (.tar.aa) and 3fedbaace4561bf7eb32779c51484441a9f2c81bf8bbb2fce41b631af5a8fd2e (.tar.ab). The unchanged indexer will read this same prod-0 collector archive using --pod prod-0tmp for the publication-name exception on 2025-06-09 only; no staging data, date substitution, source edit, search-setting change or scored-result selection. If indexing succeeds, all four affected packets run normally in the full 360-packet pass; the earlier HTTP 404 remains in the input-preparation log.
