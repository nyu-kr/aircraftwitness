#!/usr/bin/env python3
# fresh_score.py ROWS RESULTS: reruns the fresh study's scoring from its per-clip rows and checks it against the aggregate.
# The scoring functions are the frozen scorer's (sha256 2d2118ae0af3...), unchanged; its custody checks of the private runner outputs are left out.
from collections import Counter, defaultdict
import csv
import json
import math
import sys

NAMES = {"ref": "reference", "dp": "dopplerprint", "unh": "unheard", "cons": "consensus"}


class Invalid(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Invalid(reason)


def number(value, strings=False):
    require(not isinstance(value, bool) and isinstance(value, (int, float, str) if strings else (int, float)), "invalid_number")
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise Invalid("invalid_number") from None
    require(math.isfinite(value), "invalid_number")
    return value


def box(value):
    require(isinstance(value, str), "invalid_box")
    try:
        values = tuple(number(x, strings=True) for x in value.split(","))
    except Invalid:
        raise Invalid("invalid_box") from None
    require(len(values) == 4, "invalid_box")
    w, s, e, n = values
    require(-180 <= w < e <= 180 and -90 <= s < n <= 90, "invalid_box")
    return values


def hav(lat, lon, lat2, lon2):
    a, b, c, d = map(math.radians, (lat, lon, lat2, lon2))
    x = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1, max(0, x))))


def relative(lat, lon, prior_box):
    w, s, e, n = box(prior_box)
    cy, cx = (s + n) / 2, (w + e) / 2
    return (lon - cx) * 111.32 * math.cos(math.radians(cy)), (lat - cy) * 110.95


def poisson_binomial(probabilities, hits):
    dist = [1.0]
    for q in probabilities:
        dist = [u * (1 - q) + v * q for u, v in zip(dist + [0.0], [0.0] + dist)]
    return min(1.0, sum(dist[hits:]))


def shift_null(rows):
    offsets = [(r["place"], relative(r["truth_lat"], r["truth_lon"], r["box"])) for r in rows]
    answers = defaultdict(list)
    for r in rows:
        if r["lat"] is not None:
            answers[r["place"]].append(relative(r["lat"], r["lon"], r["box"]))
    probabilities = {}
    for place, points in answers.items():
        other = [q for s, q in offsets if s != place]
        probabilities[place] = sum(any(math.hypot(a[0] - q[0], a[1] - q[1]) <= 1 for a in points) for q in other) / len(other) if other else 0.0
    return probabilities


def consensus(rows):
    result = dict(rows[0], status="abstain", reason="recomputed", terminal=all(r["terminal"] for r in rows),
                  lat=None, lon=None, start_unix=None, hit=False, error_km=None, exit_code=None,
                  partial_answer_discarded=False)
    if any(r["status"] == "missing" for r in rows):
        result.update(status="missing", reason="missing_constituent")
        return result
    points = [(r["lat"], r["lon"]) for r in rows if r["lat"] is not None]
    pairs = sorted((hav(*u, *v), i, j) for i, u in enumerate(points) for j, v in enumerate(points) if i < j)
    if len(points) == 3 and all(d <= 1 for d, _, _ in pairs):
        chosen = points
    elif pairs and pairs[0][0] <= 1:
        chosen = [points[pairs[0][1]], points[pairs[0][2]]]
    else:
        return result
    lat, lon = (sum(p[i] for p in chosen) / len(chosen) for i in (0, 1))
    distance = hav(result["truth_lat"], result["truth_lon"], lat, lon)
    result.update(status="ok", lat=lat, lon=lon, error_km=distance, hit=distance <= 1)
    return result


def stats(rows, influence=True):
    hits = sum(r["hit"] for r in rows)
    answered = sum(r["lat"] is not None for r in rows)
    hit_counts = Counter(r["place"] for r in rows if r["hit"])
    depths = Counter(r["place"] for r in rows)
    qs = shift_null(rows)
    uniform = [1 - (1 - math.pi / 400) ** n for n in depths.values()]
    result = dict(hit_places=len(hit_counts), places=len(depths), chance_places=round(sum(qs.values()), 3),
                  chance_places_uniform=round(sum(uniform), 4), p=poisson_binomial(qs.values(), len(hit_counts)),
                  p_uniform=poisson_binomial(uniform, len(hit_counts)), hits=hits, clips=len(rows), answered=answered,
                  abstained_or_failed=len(rows) - answered, status=dict(Counter(r["status"] for r in rows)),
                  diagnostics=dict(Counter(r["reason"] for r in rows)), terminal=sum(r["terminal"] for r in rows),
                  partial_answers_discarded=sum(r["partial_answer_discarded"] for r in rows),
                  answered_places=len(qs), precision=round(hits / answered, 4) if answered else None,
                  precision_wilson95_ignoring_clustering=None)
    if answered:
        z = 1.959964
        p = hits / answered
        c = (p + z * z / (2 * answered)) / (1 + z * z / answered)
        h = z * math.sqrt(p * (1 - p) / answered + z * z / (4 * answered * answered)) / (1 + z * z / answered)
        result["precision_wilson95_ignoring_clustering"] = [round(c - h, 4), round(c + h, 4)]
    if influence:
        result.update(largest_place_hit_share=None, leave_top_place_out=None)
        if hits:
            top, count = hit_counts.most_common(1)[0]
            result.update(largest_place_hit_share=round(count / hits, 3),
                          leave_top_place_out=stats([r for r in rows if r["place"] != top], influence=False))
    return result



def load(path):
    rows = {}
    for r in csv.DictReader(open(path)):
        f = lambda k: float(r[k]) if r[k] else None
        rows.setdefault((r["entrant"], r["arm"]), []).append(dict(packet=r["packet"], place=r["place"], truth_lat=float(r["published_latitude"]),
            truth_lon=float(r["published_longitude"]), box=r["box"], status=r["status"], reason=r["reason"], lat=f("answer_latitude"), lon=f("answer_longitude"),
            start_unix=None, hit=r["hit"] == "1", error_km=f("distance_km"), terminal=r["terminal"] == "1", exit_code=None, partial_answer_discarded=r["partial_answer_discarded"] == "1"))
    return rows


def same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def main():
    shipped = load(sys.argv[1]); expected = json.load(open(sys.argv[2]))
    rows, bad = {}, []
    for arm in "PWA":
        for e in ("ref", "dp", "unh"):
            rows[e, arm] = shipped[NAMES[e], arm]
        rows["cons", arm] = [consensus(list(rs)) for rs in zip(*(rows[e, arm] for e in ("ref", "dp", "unh")))]
        for a, b in zip(rows["cons", arm], shipped["consensus", arm]):
            if (a["status"], a["hit"]) != (b["status"], b["hit"]) or not same(a["lat"], b["lat"]) or not same(a["lon"], b["lon"]):
                bad.append(f"consensus/{arm} {a['packet']}")
    for e, name in NAMES.items():
        for arm in "PWA":
            if not same(stats(rows[e, arm]), expected["entrants"][name][arm]):
                bad.append(f"{name}/{arm}")
            if arm != "P":
                rs = rows[e, arm]; n = next((i for i, r in enumerate(rs) if not r["terminal"]), len(rs))
                got = dict(secondary=True, prefix_clips=n, control=stats(rs[:n]), primary_on_same_packets=stats(rows[e, "P"][:n]))
                if not same(got, expected["secondary_complete_prefix"][name][arm]):
                    bad.append(f"{name}/{arm} prefix")
    if bad:
        sys.exit("fresh_score: differs from " + sys.argv[2] + ": " + ", ".join(bad))
    print(f"fresh: {sum(map(len, shipped.values()))} rows rescored, 12 entrant-arms match {sys.argv[2]}")


if __name__ == "__main__":
    main()
