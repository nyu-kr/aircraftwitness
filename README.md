# AircraftWitness

AircraftWitness is a dataset and benchmark for one question: when can the aircraft overhead tell where and when an outdoor recording was made? It indexes public field recordings and labels each one with the aircraft that passed within 10 km, taken from the public adsb.lol surveillance archive. A pass is a nearby aircraft, not proof that the recording heard it.

## Contents

- `data/`: version 1, 14 tables listed in `croissant.json` with their schemas in `schemas/`: 11,647 recordings and 4,020 passes. The test split is A2O batch 3, 360 recordings at 23 sites (`data/splits.json`), with each recording's search squares in `data/b3_blind_squares.csv`. `data/results.csv` holds the cells of the paper's main table.
- `data/v2/`: the pointer file and checksums of version 2, snapshot v2-20261105T1126: 21 sources, 11,698,054 recordings labelled and 5,243,380 passes over 1,356 days of the archive (2023-02-16 to 2026-11-04).
- `tools/score_answers.py`: the benchmark scorer.
- `tools/label_passes.py`: the version 1 labeller. `tools/scale/`: the version 2 pipeline, with one manifest builder per source (`manifest_SOURCE.py`), the day pipeline (`run_day.sh`, `day_filter.py`, `day_label.py`), the snapshot and its release tars (`snapshot.py`, `pack_tars.sh`) and the label checks (`label_check.py`, `proof_check.py`, `checks.py`, `check_manifest.py`).
- `tests/`: checks of the scorer, the labeller and the detection task.
- `locator/`: the reference locator. `results/`: per-recording results of the two registered studies, scored by `studies/`. `prereg/`: the registered plans and their amendments.

## Run

Score an answer file with Python 3.10 or later, standard library only:

    make eval ANSWERS=answers.jsonl

It scores location, clock and claim check answers against the released test squares and public positions and prints the hits and the file's SHA-256. The answer format is `schemas/answers.schema.json`. Scoring each square's centre reproduces the centre row of the paper's Table 1.

Run every check (scorer, squares, labeller, detection task, table schemas):

    pip install -r requirements.txt
    make check

## Reference locator

`locator/` is the reference locator of the studies (code `7f02287038f5`): `locate.py` and the four modules it imports, with paths and comments cleaned and the logic unchanged. It finds passes with dopplerprint 0.1.0 (MIT, Henry Alexander), installed with the rest of `locator/requirements.txt` (building dopplerprint needs a Rust toolchain). Set `DP_CMD` if the `dopplerprint` command is not on `PATH`. Run it on one packet:

    pip install -r locator/requirements.txt
    make locate AUDIO=clip.flac ADSB=flights.parquet BOX=W,S,E,N T0=1749675600 OUT=out/clip DEM=glo90.tif

`ADSB` holds the adsb.lol points around the box and hour (columns `t`, `icao`, `lat`, `lon`, `alt`, `galt`, `gs`, `trk`, `vr`, `gnd`, `type`, `flight`), `T0` is the claimed start (UTC seconds) and `SLACK` its allowance in seconds (60 in e2e, 300 in fresh). The answer is in `OUT.json` (`status`, `lat`, `lon`, `clock_start_unix`).

## Study results

`results/` holds the per-recording rows of the two registered studies of 2026-11-05 and their aggregates. `make studies` reruns the scoring from the rows and checks it against the aggregates. `studies/e2e_score.py` and `studies/fresh_score.py` keep the frozen scorers' scoring functions and leave out their custody checks of private inputs. `studies/runner/` is the entrant runner as run (`task.py` for e2e, `task_fresh.py` for fresh, both calling `run.py`). Positions are as each source publishes them, `distance_km` is the great-circle distance from the answer to that position, and a hit is at most 1 km. `precision_m` is the published precision or, without one, half a unit of the coordinate's last printed decimal, in metres. Arm W shifts the claimed time by 2 h and arm A plays the audio of `donor_packet`.

- `results/e2e_rows.csv`, one row per recording, entrant and arm, in sample order: `packet, recording_id, source, source_url, audio_url, archive_member, recording_start_utc, window_offset_s, window_s, published_latitude, published_longitude, position_precision_m, precision_m, draw, draw_size, place_group, detector, entrant, arm, donor_packet, status, answer_latitude, answer_longitude, distance_km, hit`. Aggregate `results/e2e_results.json`, source aliases `results/e2e_source_aliases.json`.
- `results/fresh_rows.csv`, one row per clip, entrant and arm: `packet, recording_id, source, source_url, audio_url, archive_member, clip_start_utc, clip_offset_s, place, published_latitude, published_longitude, box, entrant, arm, donor_packet, status, reason, terminal, partial_answer_discarded, answer_latitude, answer_longitude, distance_km, hit`. Aggregate `results/fresh_results.json`.
- `results/livermore/fit.py` recovers the Livermore recorder position from the publisher's per-pass distances and ADS-B tracks (`fit_passes.csv`, each track file pinned by SHA-256): `python3 results/livermore/fit.py DOPPLERPRINT_SRC ZENODO_DIR`, with the Zenodo record 23424278 unzipped in `ZENODO_DIR` and dopplerprint checked out at `447dbed`. The release prints no position.

## Data release

The version 2 label tables are the assets of release v2-20261105T1126: `aircraftwitness-v2-20261105T1126-passes.tar`, `aircraftwitness-v2-20261105T1126-coverage.tar`, `aircraftwitness-v2-20261105T1126-days.tar` and the pointer file `v2-20261105T1126.json`, which lists every file with its bytes, rows and SHA-256. In the download folder, check them with `sha256sum -c` against `data/v2/v2-20261105T1126.SHA256SUMS`.

Audio is never bundled. Fetch it from each recording's `audio_url` or from `data/assets.csv`, under the source's terms. To keep only passes inside known audio, join passes to coverage on `recording_id` and keep rows whose `duration_basis` is "source metadata" and whose closest approach falls between the recording's start and end.

## Licences

Code is MIT (`LICENSE`). The aircraft-derived tables (passes, coverage and the version 2 release) are under the Open Database License 1.0 and contain information from adsb.lol. Audio and source metadata keep their own licences, with the credit and licence of each recording in `data/recordings.csv` (`NOTICE.md`).

## Citation

Cite with `CITATION.cff`. Croissant metadata: `croissant.json`. Datasheet: `DATASHEET.md`.
