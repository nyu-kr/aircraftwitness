# Datasheet

**Motivation.** Aircraft heard in an outdoor recording, matched to public surveillance tracks, can confirm or refute the place and time it claims. AircraftWitness measures how often that works and where it fails.

**Composition.** Version 1 (`data/`, 14 tables) holds 11,647 recordings from xeno-canto, Eclipse Soundscapes, a park archive, Livermore, A2O batches 1 to 3 and 8 Locus Sonus live streams, with 4,020 passes and one coverage row per recording. Version 2 (release v2-20261105T1126) holds 11,698,054 recordings from 21 sources and 5,243,380 passes. No audio is included.

**Collection.** Recording metadata are copied from each publisher as printed, and positions are never refined. Each recording states the basis of its time (GPS, recorder clock, recordist entry). The A2O portal offsets its positions by up to about 280 m at these sites. Aircraft come from the adsb.lol daily archive.

**Labels.** A pass is a run of observed surveillance samples within 10 km slant range during the recording. Gaps over 60 s split a run. The closest approach is the nearest observed sample. A missing cut or duration is unknown, never zero. Rule audible-v1 tested a stratified sample of 2,000 version 2 passes against the source audio, comparing 50 Hz to 1 kHz energy at closest approach with the recording's quiet windows: 346 audible, 479 not and 1,175 undetermined (`data/audibility_sample.csv`). A pass is a candidate, not proof that the aircraft was heard.

**Tasks and splits.** Locating a recording in a 20 km square from its start time (T1), locating it from its date in a 100 km square (T2), estimating a recorder's clock, checking an archive's claimed place and time, and detecting passes (`data/task_definitions.json`). A hit is within 1 km, and missing answers count as misses. The test split is A2O batch 3. A2O publishes its positions, so results on it from outside entrants are development results. Every other cohort is development data.

**Personal information.** Aircraft positions, addresses, callsigns and registrations are not included. A SHA-256 aircraft hash links passes of one aircraft and can be reversed by enumerating the small address space. Creator names appear as the credits sources require.

**Uses.** Aircraft proximity retrieval, recorder clock estimation, archive claim checks and audio cleaning studies. Do not use it to locate or monitor people or sensitive wildlife sites, or to undo a source's position offset.

**Distribution and maintenance.** Version 1.0.0. Code MIT, aircraft-derived tables ODbL 1.0, sources under their own terms (`NOTICE.md`). Version 2 tables ship as release assets, checked by `data/v2/v2-20261105T1126.SHA256SUMS`. No DOI is assigned yet. Corrections come as new tagged versions.
