"""Evaluator agent: critiques a Dispatch plan and lists what must be fixed.

It never edits the plan itself - it returns issues, and the Planner routes them
back to Dispatch for revision (max config.MAX_REVISIONS times).

The accept/reject decision is purely rule-based. On a rejection, an LLM (NVIDIA
NIM) writes a plain-language explanation for the trace; that text is generated
after the decision and has no effect on it.
"""
import time

import config
import nim


class EvaluatorAgent:
    name = "Evaluator"

    def __init__(self, tracer, deadline=None):
        self.tracer = tracer
        self.deadline = deadline or (time.monotonic() + config.RUN_BUDGET_S)

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
        if not passed:
            self._explain(plan, issues)
        return {"version": plan["version"], "passed": passed, "issues": issues}

    # ---- explanation (trace only; decision is already final) -------------

    def _explain(self, plan, issues):
        facts = self._facts(plan, issues)
        template = self._template_explanation(plan, issues)
        source, error = "template", None
        text = template
        if config.EVALUATOR_LLM_EXPLAIN and config.NVIDIA_API_KEY:
            try:
                text = self._llm_explanation(plan, facts)
                source = f"llm:{config.NIM_MODEL}"
            except Exception as exc:  # any failure falls back to the template; never affects the verdict
                error = str(exc)
        elif not config.NVIDIA_API_KEY:
            error = "NVIDIA_API_KEY not set; used rule-based template"
        self.tracer.log(self.name, f"explain v{plan['version']}", text,
                        explanation_source=source, llm_error=error, facts_given=facts)

    def _llm_explanation(self, plan, facts):
        remaining = self.deadline - time.monotonic()
        if remaining < 5:
            raise nim.NIMError("run time budget exhausted")
        prompt = (
            "You are the evaluator in a waste-collection dispatch system in Buea, Cameroon. "
            f"Draft plan v{plan['version']} was REJECTED by fixed rules for the reasons below. "
            "In 1-2 plain sentences for a dispatcher, explain why it was rejected and suggest a concrete fix "
            "(e.g. which collector has spare capacity to take stops). Use ONLY the numbers given; do not invent "
            "stops, collectors or figures. Reply with the explanation text only.\n\n"
            f"FACTS:\n{facts}"
        )
        text = nim.chat(prompt, max_tokens=150, temperature=0.2,
                        timeout=min(config.EVALUATOR_LLM_TIMEOUT_S, remaining))
        text = " ".join(text.split()).strip().strip('"')
        if not text:
            raise nim.NIMError("empty model reply")
        return text[:600]

    @staticmethod
    def _facts(plan, issues):
        lines = ["Issues:"] + [f"- {i['detail']}" for i in issues]
        lines.append("Collectors (home zone, load/capacity, spare):")
        for cid, r in plan["routes"].items():
            zones = sorted({s["zone"] for s in r["stops"]})
            lines.append(f"- {cid} ({r['home_zone']}): {r['load_kg']}/{r['capacity_kg']} kg, "
                         f"{max(0, r['capacity_kg'] - r['load_kg'])} kg spare; serving {', '.join(zones) or 'nothing'}")
        return "\n".join(lines)

    @staticmethod
    def _template_explanation(plan, issues):
        spare = {cid: r["capacity_kg"] - r["load_kg"] for cid, r in plan["routes"].items()}
        parts = []
        for i in issues:
            if i["type"] == "over_capacity":
                others = [f"{c} has {s} kg spare" for c, s in spare.items() if c != i["collector_id"] and s > 0]
                parts.append(f"{i['collector_id']} would be overloaded by {i['excess_kg']} kg"
                             + (f"; {', '.join(others)}." if others else "; no other collector has room."))
            else:
                parts.append(i["detail"])
        return " ".join(parts)
