"""Planner agent: breaks the day into subtasks and delegates them.

The Planner does NOT make final decisions. Verification decides which reports
are trusted, Dispatch builds routes, the Evaluator judges them, and the Risk
agent decides what is auto-approved vs. sent to a human. The Planner only
sequences the work and routes Evaluator feedback back to Dispatch.

Flow:  Verification -> Dispatch -> Evaluator --(issues)--> Dispatch ... -> Risk
                                     (max config.MAX_REVISIONS revisions)
"""
import time

import config
from agents.dispatch import DispatchAgent
from agents.evaluator import EvaluatorAgent
from agents.risk import RiskAgent
from agents.verification import VerificationAgent
from shared_world import SharedWorld


class PlannerAgent:
    name = "Planner"

    def __init__(self, tracer, data, sample_mode=config.SAMPLE_MODE, world=None):
        self.tracer = tracer
        self.data = data
        # One shared World Model per run; every agent reads and writes it.
        self.world = world or SharedWorld(data["service_date"])
        deadline = time.monotonic() + config.RUN_BUDGET_S
        self.verifier = VerificationAgent(tracer, self.world, sample_mode=sample_mode, deadline=deadline)
        self.dispatcher = DispatchAgent(tracer, self.world, data["depot"], data["zones"], data["collectors"])
        self.evaluator = EvaluatorAgent(tracer, self.world, deadline=deadline)
        self.risk = RiskAgent(tracer, self.world)

    def run(self):
        data = self.data
        self.world.seed(data)
        self.tracer.log(self.name, "shared state",
                        f"Seeded World Model '{self.world.world.namespace}' with {len(self.world.world.entities)} "
                        "entities; agents now read and write decisions through it.")
        tasks = self._make_task_list()

        # 1. Verification
        if any(t["task"] == "verify_dump_reports" for t in tasks):
            verification = self.verifier.run(data["dump_reports"])
        else:
            verification = []
        verified = [r for r in verification if r["status"] == "verified"]

        # 2-3. Dispatch <-> Evaluator feedback loop
        self.tracer.log(self.name, "delegate", f"Handing {len(data['pickups'])} pickups and {len(verified)} "
                                               "verified reports to Dispatch for a first draft.")
        plan = self.dispatcher.draft(data["pickups"], verified)
        plan_versions = [plan]
        evaluations = []
        while True:
            evaluation = self.evaluator.evaluate(plan, data["pickups"])
            evaluations.append(evaluation)
            if evaluation["passed"]:
                break
            fixable = [i for i in evaluation["issues"] if i["fixable"]]
            revisions_done = plan["version"] - 1
            if not fixable:
                self.tracer.log(self.name, "loop stop", "Evaluator found issues Dispatch cannot fix "
                                                        "(e.g. not enough fleet capacity); escalating via Risk.")
                break
            if revisions_done >= config.MAX_REVISIONS:
                self.tracer.log(self.name, "loop stop", f"Reached the {config.MAX_REVISIONS}-revision limit; "
                                                        "handing the unresolved plan to Risk for escalation.")
                break
            self.tracer.log(self.name, "route feedback",
                            f"Sending {len(fixable)} fixable issue(s) back to Dispatch "
                            f"(revision {revisions_done + 1} of max {config.MAX_REVISIONS}).",
                            issue_types=[i["type"] for i in fixable])
            plan = self.dispatcher.revise(plan, fixable)
            plan_versions.append(plan)

        # 4. Risk / escalation - the only place approval is decided
        self.tracer.log(self.name, "delegate", f"Handing plan v{plan['version']} to Risk for approval decisions.")
        risk = self.risk.assess(plan, evaluations[-1])

        return {
            "task_plan": tasks,
            "verification": verification,
            "plan_versions": [self._version_summary(p) for p in plan_versions],
            "evaluation_history": evaluations,
            "final_plan": self._annotate(plan, risk),
            "risk": risk,
        }

    def _make_task_list(self):
        d = self.data
        tasks = []
        if d["dump_reports"]:
            tasks.append({"task": "verify_dump_reports", "agent": "Verification",
                          "why": f"{len(d['dump_reports'])} dump reports must be checked before any can be dispatched"})
        tasks += [
            {"task": "draft_routes", "agent": "Dispatch",
             "why": f"{len(d['pickups'])} pickups across {len(d['zones'])} zones need assigning to "
                    f"{len(d['collectors'])} collectors"},
            {"task": "critique_and_revise", "agent": "Evaluator",
             "why": f"catch over-capacity, unverified reports and ignored zones; up to {config.MAX_REVISIONS} revisions"},
            {"task": "approve_or_escalate", "agent": "Risk",
             "why": "only assignments fully backed by evidence are auto-approved"},
        ]
        self.tracer.log(self.name, "task plan",
                        "Decomposed the day into: " + " -> ".join(f"{t['agent']}:{t['task']}" for t in tasks)
                        + ". Planner sequences only; it does not approve.", tasks=tasks)
        return tasks

    @staticmethod
    def _version_summary(plan):
        return {"version": plan["version"], "changes": plan["changes"],
                "loads": {cid: {"load_kg": r["load_kg"], "capacity_kg": r["capacity_kg"]}
                          for cid, r in plan["routes"].items()},
                "deferred": plan["deferred"]}

    @staticmethod
    def _annotate(plan, risk):
        """Copy the final plan and stamp each stop with the Risk agent's decision."""
        flagged = {i["stop_id"]: i["reasons"] for i in risk["human_review"] if i["stop_id"]}
        routes = {}
        for cid, route in plan["routes"].items():
            stops = []
            for s in route["stops"]:
                approval = "human_review" if s["stop_id"] in flagged else "auto_approved"
                stops.append({**s, "approval": approval, "review_reasons": flagged.get(s["stop_id"], [])})
            routes[cid] = {**route, "stops": stops}
        return {**plan, "routes": routes}
