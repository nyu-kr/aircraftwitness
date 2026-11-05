#!/usr/bin/env python3

import json, sys, requests
raw = sys.argv[1]; A = "https://api.gbif.org/v1"
r = requests.get(f"{A}/occurrence/search", params={"mediaType": "Sound", "year": "2016,2026", "limit": 0, "facet": "datasetKey", "facetLimit": 200}, timeout=90); r.raise_for_status()
f = r.json(); open(f"{raw}/facet.json", "w").write(r.text)
out = []
for c in f["facets"][0]["counts"]:
    d = requests.get(f"{A}/dataset/{c['name']}", timeout=60); d.raise_for_status(); d = d.json()
    out.append([c["name"], c["count"], d["title"], d.get("license", "")])
json.dump(out, open(f"{raw}/datasets.json", "w"), indent=1)
