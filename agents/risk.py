"""Risk/escalation agent: decides which assignments may be auto-approved.

Anything the evidence does not fully support goes to a human-review queue
instead of being auto-approved. This agent is the only place approval is decided.
"""
import config


class RiskAgent:
    name = "Risk"

    def __init__(self, tracer):
        self.tracer = tracer

    def assess(self, plan, verification, final_evaluation):
        auto_approved, human_review = [], []
        plan_passed = final_evaluation["passed"]

        for cid, route in plan["routes"].items():
            load_ratio = route["load_kg"] / route["capacity_kg"]
            tight = load_ratio > config.RISK_LOAD_CEILING
            for stop in route["stops"]:
                reasons = []
                if stop["kind"] == "dump_report" and stop["confidence"] < config.RISK_CONFIDENCE_FLOOR:
                    reasons.append(f"photo confidence {stop['confidence']:.2f} passed verification but is below "
                                   f"the {config.RISK_CONFIDENCE_FLOOR} auto-approve bar")
                if not stop["volume_confirmed"] and tight:
                    reasons.append(f"volume ({stop['kg']} kg) is an unweighed estimate and {cid} is at "
                                   f"{load_ratio:.0%} of capacity - an under-estimate would overload it")
                if not plan_passed:
                    reasons.append("plan never passed the Evaluator within the revision limit")
                item = {"stop_id": stop["stop_id"], "source_id": stop["source_id"], "kind": stop["kind"],
                        "zone": stop["zone"], "collector_id": cid, "reasons": reasons}
                (human_review if reasons else auto_approved).append(item)

        # Items that are not on any route but still need a person to decide.
        for r in verification:
            if r["status"] == "needs_review":
                human_review.append({"stop_id": None, "source_id": r["report_id"], "kind": "held_report",
                                     "zone": r["zone"], "collector_id": None, "reasons": [r["decision_reason"]]})
        for d in plan["deferred"]:
            human_review.append({"stop_id": d["stop_id"], "source_id": d["stop_id"][2:], "kind": "deferred_stop",
                                 "zone": d["zone"], "collector_id": None, "reasons": [d["reason"]]})

        if not plan_passed:
            status = "escalated_to_human"
        elif human_review:
            status = "partially_auto_approved"
        else:
            status = "auto_approved"

        for item in human_review:
            self.tracer.log(self.name, f"flag {item['source_id']}", "; ".join(item["reasons"]),
                            kind=item["kind"], collector_id=item["collector_id"])
        self.tracer.log(self.name, "decision",
                        f"Plan status '{status}': {len(auto_approved)} assignment(s) auto-approved, "
                        f"{len(human_review)} item(s) sent to human review.")
        return {"plan_status": status, "auto_approved": auto_approved, "human_review": human_review}
