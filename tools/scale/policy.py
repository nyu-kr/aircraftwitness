#!/usr/bin/env python3






import sys, os, re, csv, gzip, datetime
NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
OPEN = re.compile(r"^(CC0|CC-?0|PDM|public domain|CC[ -]BY(-SA)?([ -][0-9.]+)?)\b(?!-N)", re.I)
def redis(lic):
    l = lic.strip(); m = re.match(r"https?://creativecommons\.org/(licenses/([a-z-]+)|publicdomain/(zero|mark))/([0-9.]+)", l)   
    if m: l = ("CC0" if m[3] == "zero" else "PDM" if m[3] == "mark" else "CC " + m[2].upper()) + " " + m[4]
    return "yes" if OPEN.match(l) and not re.search(r"\bN[CD]\b|-N[CD]", l, re.I) else "no"
REGRADE = {"arctic-soundscapes": ("unknown", "; zone unresolved by the source, graded unknown"),
           "tabmon": ("device", "; graded device: NTP sync happens only when the unit is online, per-file sync state unknown")}
PREC = {"smartbay-galway": ("600", "; precision 600 s: the server listing time precedes some named starts by about 5 min, so the instrument clock runs minutes off")}
for m in sys.argv[1:]:
    src = os.path.basename(m).split(".")[0]; n = dropped = changed = 0
    with gzip.open(m, "rt", newline="") as fi, gzip.open(m + ".tmp.gz", "wt", newline="", compresslevel=6) as fo:
        r = csv.DictReader(fi); w = csv.DictWriter(fo, r.fieldnames); w.writeheader()
        for row in r:
            n += 1
            if (src == "gbif-sounds" and '"xc"' in row["extra"]) or (src == "inaturalist" and row["obscured"] == "true"):
                dropped += 1; continue
            if row["utc_start"] > NOW:   
                dropped += 1; continue
            g = REGRADE.get(src)
            if g and row["time_grade"] != g[0]: row["time_grade"] = g[0]; row["time_basis"] += g[1]; changed += 1
            q = PREC.get(src)
            if q and row["time_precision_s"] != q[0]: row["time_precision_s"] = q[0]; row["time_basis"] += q[1]; changed += 1
            v = redis(row["audio_licence"])
            if v != row["redistributable"]: row["redistributable"] = v; changed += 1
            w.writerow(row)
    if dropped or changed: os.replace(m + ".tmp.gz", m); print(f"{src}: {n} rows, {dropped} dropped, {changed} field changes")
    else: os.remove(m + ".tmp.gz")
