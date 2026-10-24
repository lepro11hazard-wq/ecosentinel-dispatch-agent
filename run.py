"""EcoSentinel Dispatch Agent - entry point.

    python run.py

Runs Planner -> Verification -> Dispatch <-> Evaluator -> Risk and writes the
full result plus reasoning trace to outputs/last_run.json.
"""
import json
import time
from datetime import datetime, timezone

import config
from agents.planner import PlannerAgent
from data import sample_data
from shared_world import SharedWorld
from tracing import Tracer

SCOPE_LIMITS = [
    "Stops are ordered by straight-line (haversine) distance with a greedy nearest-neighbour heuristic, "
    "not road distance or travel time.",
    "Collector capacity is checked by weight (kg) only; volume, time windows and shift length are not modelled.",
    "Duplicate dump reports are detected by location (<= %d m) and time (<= %d h), plus the vision model's flag; "
    "photos are not compared pixel-to-pixel." % (config.DUPLICATE_RADIUS_M, config.DUPLICATE_WINDOW_H),
]


def main():
    started = time.perf_counter()
    mode = "SAMPLE (simulated data)" if config.SAMPLE_MODE else "LIVE (NVIDIA NIM vision calls)"
    print("=" * 72)
    print(f"EcoSentinel Dispatch Agent | mode: {mode}")
    if config.SAMPLE_MODE:
        print(sample_data.SIMULATED_NOTICE)
    print("=" * 72)

    data = sample_data.generate()
    tracer = Tracer()
    world = SharedWorld(data["service_date"], path=config.ROOT / "outputs" / "world_model.json")
    result = PlannerAgent(tracer, data, sample_mode=config.SAMPLE_MODE, world=world).run()
    world_summary = world.summary()
    runtime_s = round(time.perf_counter() - started, 2)

    output = {
        "meta": {
            "project": "EcoSentinel Dispatch Agent (EcoLoop)",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "service_date": data["service_date"],
            "sample_mode": config.SAMPLE_MODE,
            "data_notice": data["notice"] if config.SAMPLE_MODE else "Live vision calls on sample inputs.",
            "vision_model": None if config.SAMPLE_MODE else config.NIM_MODEL,
            "runtime_s": runtime_s,
            "scope_limits": SCOPE_LIMITS,
            "thresholds": {
                "verify_accept": config.VERIFY_ACCEPT, "verify_reject": config.VERIFY_REJECT,
                "risk_confidence_floor": config.RISK_CONFIDENCE_FLOOR,
                "risk_load_ceiling": config.RISK_LOAD_CEILING, "max_revisions": config.MAX_REVISIONS,
            },
        },
        "inputs": data,
        **result,
        "world_model": world_summary,
        "trace": tracer.entries,
    }

    out_path = config.ROOT / "outputs" / "last_run.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    risk = result["risk"]
    print("-" * 72)
    print(f"Plan status: {risk['plan_status']}  |  versions drafted: {len(result['plan_versions'])}  |  "
          f"auto-approved: {len(risk['auto_approved'])}  |  human review: {len(risk['human_review'])}")
    for cid, route in result["final_plan"]["routes"].items():
        stops = ", ".join(f"{s['source_id']}{'*' if s['approval'] == 'human_review' else ''}" for s in route["stops"])
        print(f"  {cid} ({route['home_zone']}) {route['load_kg']}/{route['capacity_kg']} kg, "
              f"{route['distance_km_straight_line']} km: {stops}")
    print("  (* = needs human review)")
    print(f"World Model: {world_summary['events']} events, {world_summary['open_assignments']} open / "
          f"{world_summary['closed_assignments']} superseded assignments, "
          f"replay matches live state: {world_summary['replay_matches_live_state']}")
    print(f"Saved {out_path.relative_to(config.ROOT)} in {runtime_s}s")


if __name__ == "__main__":
    main()
