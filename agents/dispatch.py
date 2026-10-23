"""Dispatch agent: turns pickups + verified dump reports into collector routes.

Draft strategy: every stop goes to the collector whose home zone it is in.
Revision strategy: fix only the issues the Evaluator raised, e.g. move stops off
an overloaded collector to the collector with room whose home zone is closest.

SCOPE LIMIT: stops are ordered by straight-line distance (greedy nearest
neighbour from the depot), not road distance. This is a deliberate simplification.
"""
from geo import haversine_m, nearest_neighbour_order


class DispatchAgent:
    name = "Dispatch"

    def __init__(self, tracer, depot, zones, collectors):
        self.tracer = tracer
        self.depot = depot
        self.zones = zones
        self.collectors = {c["id"]: c for c in collectors}
        self.stop_pool = {}

    # ---- public API -----------------------------------------------------

    def draft(self, pickups, verified_reports):
        stops = [self._pickup_stop(p) for p in pickups] + [self._report_stop(r) for r in verified_reports]
        self.stop_pool = {s["stop_id"]: s for s in stops}
        assignment = {cid: [] for cid in self.collectors}
        for stop in stops:
            assignment[self._home_collector(stop["zone"])].append(stop)

        plan = self._build_plan(assignment, deferred=[], version=1, changes=[])
        self.tracer.log(self.name, "draft v1",
                        f"Assigned {len(stops)} stops ({len(pickups)} pickups + {len(verified_reports)} verified "
                        "dump reports) to each zone's home collector; ordered by straight-line nearest neighbour.",
                        loads=self._load_summary(plan))
        return plan

    def revise(self, plan, issues):
        assignment = {cid: [self.stop_pool[s["stop_id"]] for s in r["stops"]] for cid, r in plan["routes"].items()}
        deferred = [dict(d) for d in plan["deferred"]]
        changes = []

        # Remove bad stops first so they don't consume capacity, then rebalance, then re-add missing stops.
        priority = {"unverified_report": 0, "duplicate_assignment": 0, "over_capacity": 1}
        for issue in sorted(issues, key=lambda i: priority.get(i["type"], 2)):
            kind = issue["type"]
            if kind == "over_capacity":
                self._rebalance(assignment, issue["collector_id"], deferred, changes)
            elif kind == "unverified_report":
                self._remove(assignment, issue["stop_id"], changes, "report is not verified")
            elif kind == "duplicate_assignment":
                self._remove(assignment, issue["stop_id"], changes, "stop appeared on two routes",
                             keep_one=True)
            elif kind in ("ignored_zone", "deferred_stops"):
                for stop_id in issue["stop_ids"]:
                    deferred = [d for d in deferred if d["stop_id"] != stop_id]
                    self._place(assignment, self.stop_pool[stop_id], deferred, changes)

        new_plan = self._build_plan(assignment, deferred, plan["version"] + 1, changes)
        self.tracer.log(self.name, f"revision v{new_plan['version']}",
                        f"Applied {len(changes)} change(s) in response to {len(issues)} evaluator issue(s).",
                        changes=changes, loads=self._load_summary(new_plan))
        return new_plan

    # ---- revision helpers -------------------------------------------------

    def _rebalance(self, assignment, cid, deferred, changes):
        cap = self.collectors[cid]["capacity_kg"]
        while self._load(assignment[cid]) > cap:
            best = None
            for stop in assignment[cid]:
                for tid in assignment:
                    if tid == cid or stop["kg"] > self._spare(assignment, tid):
                        continue
                    dist = haversine_m(stop, self._zone_centre(tid))
                    if best is None or dist < best[0]:
                        best = (dist, stop, tid)
            if best:
                dist, stop, tid = best
                assignment[cid].remove(stop)
                assignment[tid].append(stop)
                changes.append({"action": "moved", "stop_id": stop["stop_id"], "from": cid, "to": tid,
                                "reason": f"{cid} over capacity; {tid} has room and its home zone is "
                                          f"{dist / 1000:.2f} km from this stop (closest option)"})
            else:
                home = self._zone_centre(cid)
                stop = max(assignment[cid], key=lambda s: haversine_m(s, home))
                assignment[cid].remove(stop)
                deferred.append({"stop_id": stop["stop_id"], "zone": stop["zone"], "kg": stop["kg"],
                                 "reason": "no collector has spare capacity for it"})
                changes.append({"action": "deferred", "stop_id": stop["stop_id"], "from": cid,
                                "reason": "no collector can absorb it; deferring the stop farthest from home zone"})

    def _place(self, assignment, stop, deferred, changes):
        candidates = [cid for cid in assignment if stop["kg"] <= self._spare(assignment, cid)]
        if not candidates:
            deferred.append({"stop_id": stop["stop_id"], "zone": stop["zone"], "kg": stop["kg"],
                             "reason": "no collector has spare capacity for it"})
            changes.append({"action": "deferred", "stop_id": stop["stop_id"], "reason": "still no capacity"})
            return
        home = self._home_collector(stop["zone"])
        tid = home if home in candidates else min(candidates, key=lambda c: haversine_m(stop, self._zone_centre(c)))
        assignment[tid].append(stop)
        changes.append({"action": "added", "stop_id": stop["stop_id"], "to": tid,
                        "reason": "stop was missing from every route"})

    def _remove(self, assignment, stop_id, changes, reason, keep_one=False):
        found = False
        for cid, stops in assignment.items():
            for stop in list(stops):
                if stop["stop_id"] != stop_id:
                    continue
                if keep_one and not found:
                    found = True
                    continue
                stops.remove(stop)
                changes.append({"action": "removed", "stop_id": stop_id, "from": cid, "reason": reason})

    # ---- plan building ----------------------------------------------------

    def _build_plan(self, assignment, deferred, version, changes):
        routes = {}
        for cid, stops in assignment.items():
            col = self.collectors[cid]
            ordered, legs, total_m = nearest_neighbour_order(self.depot, stops)
            load = self._load(stops)
            routes[cid] = {
                "collector_id": cid,
                "collector_name": col["name"],
                "home_zone": col["home_zone"],
                "capacity_kg": col["capacity_kg"],
                "load_kg": load,
                "load_pct": round(100 * load / col["capacity_kg"], 1),
                "distance_km_straight_line": round(total_m / 1000, 2),
                "stops": [{**s, "seq": i + 1, "leg_m": round(leg)} for i, (s, leg) in enumerate(zip(ordered, legs))],
            }
        return {"version": version, "routes": routes, "deferred": deferred, "changes": changes}

    def _home_collector(self, zone):
        for cid, col in self.collectors.items():
            if col["home_zone"] == zone:
                return cid
        # zone with no dedicated collector -> nearest collector's home zone
        centre = self.zones[zone]
        cid = min(self.collectors, key=lambda c: haversine_m(centre, self._zone_centre(c)))
        self.tracer.log(self.name, "no home collector", f"Zone {zone} has no collector; using nearest ({cid}).")
        return cid

    def _zone_centre(self, cid):
        return self.zones[self.collectors[cid]["home_zone"]]

    def _spare(self, assignment, cid):
        return self.collectors[cid]["capacity_kg"] - self._load(assignment[cid])

    @staticmethod
    def _load(stops):
        return sum(s["kg"] for s in stops)

    @staticmethod
    def _load_summary(plan):
        return {cid: f"{r['load_kg']}/{r['capacity_kg']} kg" for cid, r in plan["routes"].items()}

    @staticmethod
    def _pickup_stop(p):
        return {"stop_id": f"S-{p['id']}", "kind": "pickup", "source_id": p["id"], "zone": p["zone"],
                "lat": p["lat"], "lon": p["lon"], "kg": p["kg"], "volume_confirmed": p["volume_confirmed"]}

    @staticmethod
    def _report_stop(r):
        return {"stop_id": f"S-{r['report_id']}", "kind": "dump_report", "source_id": r["report_id"],
                "zone": r["zone"], "lat": r["lat"], "lon": r["lon"], "kg": r["est_kg"],
                "volume_confirmed": False, "confidence": r["confidence"]}
