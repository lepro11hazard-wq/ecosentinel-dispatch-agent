"""EcoLoop's shared state: one Meterless World Model per run that every agent reads and writes.

Planner seeds it, Verification asserts a `verification` fact per report,
Dispatch writes `assigned-to` edges per plan version (a moved stop closes its
old edge), the Evaluator records its verdict per version, and Evaluator and
Risk read verification results and assignments back from here. The event log
is saved to outputs/world_model.json and replayed at the end of the run.
"""
from datetime import datetime, timedelta, timezone

from world_model import WorldModel

SYSTEM = "ecoloop"
DEFERRED = f"queue:{SYSTEM}:deferred"
REMOVED = f"queue:{SYSTEM}:removed"


def stop_key(stop_id):
    return f"stop:{SYSTEM}:{stop_id}"


def collector_key(cid):
    return f"collector:{SYSTEM}:{cid}"


def report_key(report_id):
    return f"dump_report:ingest:{report_id}"


class SharedWorld:
    def __init__(self, service_date, path=None):
        if path is not None and path.exists():
            path.unlink()  # one fresh world per run
        self.world = WorldModel(f"{SYSTEM}-{service_date}", path=path)
        self._last = None

    def _source(self, agent, kind="agent"):
        """Provenance with a strictly increasing timestamp, so later decisions always win."""
        now = datetime.now(timezone.utc)
        if self._last is not None and now <= self._last:
            now = self._last + timedelta(microseconds=1)
        self._last = now
        return {"kind": kind, "by": agent, "at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}

    # ---- Planner -----------------------------------------------------------

    def seed(self, data):
        w = self.world
        src = self._source("Planner", kind="input")
        for zone, centre in data["zones"].items():
            w.upsert_entity("zone", src, name=zone, external_key={"system": SYSTEM, "id": zone}, attrs=centre)
        for c in data["collectors"]:
            w.upsert_entity("collector", src, name=c["name"], external_key={"system": SYSTEM, "id": c["id"]}, attrs=c)
            w.relate(collector_key(c["id"]), "home-zone", f"zone:{SYSTEM}:{c['home_zone']}", src)
        for p in data["pickups"]:
            w.upsert_entity("pickup", src, external_key={"system": SYSTEM, "id": p["id"]}, attrs=p)
            w.relate(f"pickup:{SYSTEM}:{p['id']}", "in-zone", f"zone:{SYSTEM}:{p['zone']}", src)
        for queue in ("deferred", "removed"):
            w.upsert_entity("queue", src, name=queue, external_key={"system": SYSTEM, "id": queue})
        # Citizen dump reports arrive as a stream: idempotent ingest, then their full fields.
        w.ingest([{"externalId": r["id"], "type": "dump_report", "title": r["description"], "at": r["reported_at"],
                   "source": {"kind": "citizen-report"}} for r in data["dump_reports"]])
        for r in data["dump_reports"]:
            w.upsert_entity("dump_report", {"kind": "citizen-report", "at": r["reported_at"]},
                            external_key={"system": "ingest", "id": r["id"]}, attrs=r)
            w.relate(report_key(r["id"]), "in-zone", f"zone:{SYSTEM}:{r['zone']}",
                     {"kind": "citizen-report", "at": r["reported_at"]})

    # ---- Verification ------------------------------------------------------

    def record_verification(self, result):
        self.world.assert_fact(report_key(result["report_id"]), "verification", result,
                               self._source("Verification"), confidence=result["confidence"])

    def verification_results(self):
        """Current verification fact for every report, oldest report first."""
        results = []
        for report in self.world.query("dump_report"):
            fact = self.world.current_fact(report["id"], "verification")
            if fact:
                results.append(fact["value"])
        return sorted(results, key=lambda r: r["reported_at"])

    # ---- Dispatch ----------------------------------------------------------

    def record_plan(self, plan):
        w = self.world
        src = self._source("Dispatch")
        for cid, route in plan["routes"].items():
            for s in route["stops"]:
                w.upsert_entity("stop", src, external_key={"system": SYSTEM, "id": s["stop_id"]},
                                attrs={k: v for k, v in s.items() if k not in ("seq", "leg_m")})
                w.relate(stop_key(s["stop_id"]), "assigned-to", collector_key(cid), src,
                         attrs={"plan_version": plan["version"]})
        for d in plan["deferred"]:
            w.relate(stop_key(d["stop_id"]), "assigned-to", DEFERRED, src, attrs={"plan_version": plan["version"]})
        on_route = {s["stop_id"] for r in plan["routes"].values() for s in r["stops"]}
        for c in plan["changes"]:
            if c["action"] == "removed" and c["stop_id"] not in on_route:
                w.relate(stop_key(c["stop_id"]), "assigned-to", REMOVED, src,
                         attrs={"plan_version": plan["version"], "reason": c["reason"]})
        w.assert_fact(f"plan:{SYSTEM}:v{plan['version']}", "loads",
                      {cid: r["load_kg"] for cid, r in plan["routes"].items()}, src)

    def assigned_collector(self, stop_id):
        """The collector this stop is assigned to right now, or None if it is deferred or unassigned."""
        targets = [e["id"] for e in self.world.neighbors_of(stop_key(stop_id), via="assigned-to")
                   if e["type"] == "collector"]
        return targets[0].rsplit(":", 1)[1] if len(targets) == 1 else None

    # ---- Evaluator / Risk --------------------------------------------------

    def record_evaluation(self, evaluation):
        self.world.assert_fact(f"plan:{SYSTEM}:v{evaluation['version']}", "evaluation",
                               {"passed": evaluation["passed"], "issues": [i["type"] for i in evaluation["issues"]]},
                               self._source("Evaluator"))

    def record_approval(self, item, approval):
        if item["stop_id"]:
            self.world.assert_fact(stop_key(item["stop_id"]), "approval",
                                   {"decision": approval, "reasons": item["reasons"]}, self._source("Risk"))

    # ---- run summary -------------------------------------------------------

    def summary(self):
        w = self.world
        w.save()
        return {
            "engine": "Meterless World Model (Python port, world_model/)",
            "namespace": w.namespace,
            "file": "outputs/world_model.json" if w.path else None,
            "events": len(w.events),
            "audit_entries": len(w.audit_log),
            "entities": len(w.entities),
            "open_assignments": len(w.edges("assigned-to")),
            "closed_assignments": len(w.edges("assigned-to", open_only=False)) - len(w.edges("assigned-to")),
            "replay_matches_live_state": w.replay_matches(),
        }
