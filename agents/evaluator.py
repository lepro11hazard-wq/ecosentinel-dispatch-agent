"""Evaluator agent: critiques a Dispatch plan and lists what must be fixed.

It never edits the plan itself - it returns issues, and the Planner routes them
back to Dispatch for revision (max config.MAX_REVISIONS times).
"""


class EvaluatorAgent:
    name = "Evaluator"

    def __init__(self, tracer):
        self.tracer = tracer

    def evaluate(self, plan, verification, pickups):
        issues = []
        verified_ids = {r["report_id"] for r in verification if r["status"] == "verified"}
        expected = {f"S-{p['id']}": p["zone"] for p in pickups}
        expected.update({f"S-{r['report_id']}": r["zone"] for r in verification if r["status"] == "verified"})

        # 1. capacity, 2. unverified reports, 3. stops on two routes
        seen = {}
        spare_total = 0
        for cid, route in plan["routes"].items():
            spare_total += max(0, route["capacity_kg"] - route["load_kg"])
            if route["load_kg"] > route["capacity_kg"]:
                excess = route["load_kg"] - route["capacity_kg"]
                issues.append({"type": "over_capacity", "collector_id": cid, "excess_kg": excess, "fixable": True,
                               "detail": f"{cid} carries {route['load_kg']} kg but capacity is {route['capacity_kg']} kg "
                                         f"({excess} kg over)."})
            for stop in route["stops"]:
                if stop["kind"] == "dump_report" and stop["source_id"] not in verified_ids:
                    issues.append({"type": "unverified_report", "stop_id": stop["stop_id"], "fixable": True,
                                   "detail": f"{stop['source_id']} is on {cid}'s route but was not verified."})
                if stop["stop_id"] in seen:
                    issues.append({"type": "duplicate_assignment", "stop_id": stop["stop_id"], "fixable": True,
                                   "detail": f"{stop['stop_id']} is on both {seen[stop['stop_id']]} and {cid}."})
                seen[stop["stop_id"]] = cid

        # 4. ignored zones: demand that is on no route and not explicitly deferred
        deferred = {d["stop_id"]: d for d in plan["deferred"]}
        missing_by_zone = {}
        for stop_id, zone in expected.items():
            if stop_id not in seen and stop_id not in deferred:
                missing_by_zone.setdefault(zone, []).append(stop_id)
        for zone, ids in missing_by_zone.items():
            issues.append({"type": "ignored_zone", "zone": zone, "stop_ids": ids, "fixable": True,
                           "detail": f"{len(ids)} stop(s) in {zone} are on no route."})

        # 5. deferred stops: only fixable if spare capacity exists somewhere
        if deferred:
            fits = [sid for sid, d in deferred.items() if d["kg"] <= spare_total]
            issues.append({"type": "deferred_stops", "stop_ids": fits or list(deferred), "fixable": bool(fits),
                           "detail": f"{len(deferred)} stop(s) deferred; {spare_total} kg spare capacity across fleet."})

        passed = not issues
        if passed:
            reasoning = (f"v{plan['version']} PASSES: every collector within capacity, only verified reports used, "
                         "every zone with demand is served.")
        else:
            reasoning = f"v{plan['version']} REJECTED with {len(issues)} issue(s): " + " ".join(i["detail"] for i in issues)
        self.tracer.log(self.name, f"critique v{plan['version']}", reasoning,
                        issues=[{k: v for k, v in i.items() if k != "detail"} for i in issues])
        return {"version": plan["version"], "passed": passed, "issues": issues}
