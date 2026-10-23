"""Simulated data for SAMPLE_MODE.

THIS IS NOT REAL ECOLOOP PRODUCTION DATA. Households, volumes and reports are
invented for testing. Zone names are real Buea neighbourhoods; coordinates are
approximate zone centres with random jitter.
"""
import random

SIMULATED_NOTICE = (
    "SIMULATED DATA - generated for SAMPLE_MODE, not real EcoLoop production records. "
    "Zone names are real Buea neighbourhoods; coordinates are approximate."
)

SERVICE_DATE = "2026-10-24"

ZONES = {
    "Molyko": {"lat": 4.1530, "lon": 9.2905},
    "Bonduma": {"lat": 4.1610, "lon": 9.2800},
    "Great Soppo": {"lat": 4.1450, "lon": 9.2440},
}

DEPOT = {"id": "DEPOT", "name": "EcoSentinel depot (simulated)", "lat": 4.1520, "lon": 9.2880}

COLLECTORS = [
    {"id": "COL-1", "name": "Collector A", "home_zone": "Molyko", "capacity_kg": 300, "vehicle": "tricycle"},
    {"id": "COL-2", "name": "Collector B", "home_zone": "Bonduma", "capacity_kg": 250, "vehicle": "tricycle"},
    {"id": "COL-3", "name": "Collector C", "home_zone": "Great Soppo", "capacity_kg": 250, "vehicle": "tricycle"},
]

# (zone, kg, volume_confirmed). Unconfirmed = household's own estimate, not weighed.
# Molyko is deliberately heavier than its collector can carry so the
# Evaluator -> Dispatch feedback loop has something real to fix.
_PICKUP_SPECS = [
    ("Molyko", 60, True), ("Molyko", 45, True), ("Molyko", 55, False),
    ("Molyko", 70, True), ("Molyko", 40, True), ("Molyko", 50, False),
    ("Bonduma", 35, True), ("Bonduma", 40, False), ("Bonduma", 30, True),
    ("Great Soppo", 45, True), ("Great Soppo", 35, True), ("Great Soppo", 50, False),
]

# simulated_vision stands in for the NIM model's output when SAMPLE_MODE=true.
_DUMP_REPORTS = [
    {
        "id": "DR-1", "zone": "Molyko", "lat": 4.1541, "lon": 9.2921,
        "reported_at": "2026-10-23T07:40:00+01:00", "photo_path": "data/photos/DR-1.jpg",
        "description": "Pile of sacks and plastic dumped beside the drain near the junction",
        "est_kg": 80,
        "simulated_vision": {"confidence": 0.91, "duplicate": False,
                             "reason": "Clear photo of mixed household waste piled at the roadside."},
    },
    {
        "id": "DR-2", "zone": "Molyko", "lat": 4.1542, "lon": 9.2922,
        "reported_at": "2026-10-23T10:15:00+01:00", "photo_path": "data/photos/DR-2.jpg",
        "description": "Rubbish heap by the drain, smells bad",
        "est_kg": 80,
        "simulated_vision": {"confidence": 0.86, "duplicate": True,
                             "reason": "Appears to be the same roadside pile photographed from another angle."},
    },
    {
        "id": "DR-3", "zone": "Bonduma", "lat": 4.1622, "lon": 9.2789,
        "reported_at": "2026-10-23T19:05:00+01:00", "photo_path": "data/photos/DR-3.jpg",
        "description": "Dumping behind the market stalls",
        "est_kg": 60,
        "simulated_vision": {"confidence": 0.45, "duplicate": False,
                             "reason": "Image is dark and blurred; waste presence cannot be confirmed."},
    },
    {
        "id": "DR-4", "zone": "Great Soppo", "lat": 4.1438, "lon": 9.2452,
        "reported_at": "2026-10-23T08:30:00+01:00", "photo_path": "data/photos/DR-4.jpg",
        "description": "Bags thrown near the stream bank",
        "est_kg": 90,
        "simulated_vision": {"confidence": 0.78, "duplicate": False,
                             "reason": "Waste visible near the stream bank but partly hidden by vegetation."},
    },
]


def generate(seed=42):
    """Return one day's simulated inputs. Same seed -> same data."""
    rng = random.Random(seed)
    pickups = []
    for i, (zone, kg, confirmed) in enumerate(_PICKUP_SPECS, start=1):
        centre = ZONES[zone]
        pickups.append({
            "id": f"PU-{i:02d}",
            "household": f"HH-{100 + i}",
            "zone": zone,
            "lat": round(centre["lat"] + rng.uniform(-0.0025, 0.0025), 5),
            "lon": round(centre["lon"] + rng.uniform(-0.0025, 0.0025), 5),
            "kg": kg,
            "volume_confirmed": confirmed,
            "requested_window": "07:00-12:00",
        })
    return {
        "notice": SIMULATED_NOTICE,
        "service_date": SERVICE_DATE,
        "zones": ZONES,
        "depot": DEPOT,
        "collectors": [dict(c) for c in COLLECTORS],
        "pickups": pickups,
        "dump_reports": [dict(r) for r in _DUMP_REPORTS],
    }
