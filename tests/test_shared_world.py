"""The pipeline reads and writes its state through the World Model."""
from agents.planner import PlannerAgent
from data import sample_data
from shared_world import collector_key, stop_key
from tracing import Tracer


def run_pipeline():
    planner = PlannerAgent(Tracer(), sample_data.generate(), sample_mode=True)
    return planner, planner.run()


def test_world_matches_final_plan_and_replays():
    planner, result = run_pipeline()
    world = planner.world
    for cid, route in result["final_plan"]["routes"].items():
        for stop in route["stops"]:
            assert world.assigned_collector(stop["stop_id"]) == cid
    assert world.verification_results() == sorted(result["verification"], key=lambda r: r["reported_at"])
    assert world.world.replay_matches()


def test_risk_sends_drifted_assignment_to_human_review():
    planner, result = run_pipeline()
    item = result["risk"]["auto_approved"][0]
    other = next(c for c in ("COL-1", "COL-2", "COL-3") if c != item["collector_id"])
    planner.world.world.relate(stop_key(item["stop_id"]), "assigned-to", collector_key(other),
                               planner.world._source("test"))
    risk = planner.risk.assess(result["final_plan"], result["evaluation_history"][-1])
    flagged = {i["stop_id"]: i["reasons"] for i in risk["human_review"]}
    assert any("shared state assigns this stop to " + other in r for r in flagged[item["stop_id"]])
