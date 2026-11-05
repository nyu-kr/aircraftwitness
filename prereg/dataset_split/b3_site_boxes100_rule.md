# A2O batch 3 public test split: one 100 km search square per site for the date task (seed rule, written before any draw)
Sites: each site id S of the recordings listed under "A2O batch 3" in data/task_cohorts.json (the A2O site number in recording_id, e.g. A2O138 -> 138); 23 sites.
Position input: data/recordings.csv public_latitude, public_longitude of those recordings (one position per site).
h = SHA-256 of the UTF-8 string "geoloc-dataset b3 site box100 v1|S"; u1 = first 8 bytes of h as a big-endian integer / 2^64; u2 = next 8 bytes / 2^64.
Square centre = the site's position moved (2 u1 - 1) x 49.999 km east and (2 u2 - 1) x 49.999 km north (110.95 km per degree of latitude, 111.32 cos(latitude of the centre) km per degree of longitude).
Square = centre +- 50 km in each axis, edges rounded to 5 decimals. Every batch 3 packet of site S uses that one square. The draw is independent of the blind test's squares (including its 100 km squares), of the 20 km squares and of any answer.
The 20 km rule (b3_site_boxes_rule.md) names a2o_packets.csv as its position input; data/recordings.csv carries the same strings for all 23 sites, and the 20 km squares recompute from it with identical edges.
