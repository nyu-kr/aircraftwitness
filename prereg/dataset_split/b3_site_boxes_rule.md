# A2O batch 3 public test split: one 20 km search square per site (seed rule, written before any draw)
For each batch 3 site id S (the A2O site number in recording_id, e.g. A2O138 -> 138):
h = SHA-256 of the UTF-8 string "geoloc-dataset b3 site box v1|S"; u1 = first 8 bytes of h as a big-endian integer / 2^64; u2 = next 8 bytes / 2^64.
Square centre = the site's released public position (a2o_packets.csv public_latitude, public_longitude) moved (2 u1 - 1) x 9.999 km east and (2 u2 - 1) x 9.999 km north (110.95 km per degree of latitude, 111.32 cos(latitude of the centre) km per degree of longitude).
Square = centre +- 10 km in each axis, edges rounded to 5 decimals. Every batch 3 packet of site S uses that one square. The draw is independent of the blind test's squares and of any answer.
