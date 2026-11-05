# Licences and attribution

| Material | Licence | Conditions |
| --- | --- | --- |
| Code in this repository | MIT (`LICENSE`) | Keep the notice |
| Aircraft-derived passes, coverage and the version 2 label release | Open Database License 1.0 | Contains information from adsb.lol, made available under the Open Database License. Attribute adsb.lol and the AircraftWitness contributors and share an adapted database under ODbL |
| xeno-canto audio and metadata | Per recording in `data/recordings.csv` | As the recordist states, with no blanket permission to redistribute or modify |
| Eclipse Soundscapes records | CC BY 4.0 | Credit as given per recording in `data/recordings.csv` |
| Eastern North America forest soundscapes, Zenodo 20038954 | CC BY 4.0 | Credit the record creators |
| Livermore, Zenodo 23424278 | CC BY 4.0 | Credit the creators. Only the per-pass distances, heights and time windows the source publishes are included, with the SHA-256 of its track files |
| A2O batches 1 to 3 | CC BY 4.0 | Keep the portal's credits and its offset positions |
| Version 2 sources | As each source states | Audio terms per source in the pointer file `data/v2/v2-20261105T1126.json` |
| Study rows in `results/` | As each source states | Recording ids, URLs and positions exactly as each source publishes them |
| dopplerprint, installed by `locator/requirements.txt` | MIT, Henry Alexander | Not bundled. Called by `locator/locate.py` and `results/livermore/fit.py` |

No audio is bundled. Fetching source audio does not relicense it.

ODbL: https://opendatacommons.org/licenses/odbl/1-0/. adsb.lol: https://www.adsb.lol/.

`aircraft_type` comes from the adsb.lol records. `aircraft_category`, `wake_turbulence_category` and `locator/types.json` come from the Mictronics type table as redistributed by wiedehopf/tar1090-db, which states no licence.
