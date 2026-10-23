"""Straight-line distance helpers.

SCOPE LIMIT: routes are ordered by straight-line (haversine) distance, not road
distance or travel time. Buea's hills and road network are not modelled.
"""
import math

EARTH_RADIUS_M = 6_371_000


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def nearest_neighbour_order(start, stops):
    """Greedy route: always go to the closest unvisited stop, then return to start.

    Returns (ordered_stops, leg_distances_m, total_m).
    """
    remaining = list(stops)
    ordered, legs = [], []
    current = start
    while remaining:
        nxt = min(remaining, key=lambda s: haversine_m(current, s))
        legs.append(haversine_m(current, nxt))
        ordered.append(nxt)
        remaining.remove(nxt)
        current = nxt
    total = sum(legs) + (haversine_m(current, start) if ordered else 0.0)
    return ordered, legs, total
