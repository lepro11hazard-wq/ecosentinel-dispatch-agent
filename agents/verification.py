"""Verification agent: decides which dump reports are trustworthy enough to dispatch.

Two independent signals are combined:
  1. the vision model's read of the photo -> {confidence, duplicate, reason}
  2. a location/time check against earlier reports (same pile reported twice)
"""
import base64
import json
import mimetypes
import re
import time
from datetime import datetime

import config
import nim
from geo import haversine_m


def parse_vision_response(text):
    """Turn the model's free-text reply into {confidence, duplicate, reason}."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {"confidence": 0.0, "duplicate": False, "reason": "no JSON in model reply", "error": True}
    try:
        raw = json.loads(match.group(0))
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
        duplicate = raw.get("duplicate", False)
        if isinstance(duplicate, str):
            duplicate = duplicate.strip().lower() in ("true", "yes", "1")
        return {"confidence": confidence, "duplicate": bool(duplicate),
                "reason": str(raw.get("reason", "")).strip() or "no reason given"}
    except (ValueError, TypeError) as exc:
        return {"confidence": 0.0, "duplicate": False, "reason": f"unparseable model reply: {exc}", "error": True}


class VerificationAgent:
    name = "Verification"

    def __init__(self, tracer, world, sample_mode=config.SAMPLE_MODE, deadline=None):
        self.tracer = tracer
        self.world = world
        self.sample_mode = sample_mode
        self.deadline = deadline or (time.monotonic() + config.RUN_BUDGET_S)

    def run(self, reports):
        results = []
        source = "simulated vision labels" if self.sample_mode else f"NVIDIA NIM ({config.NIM_MODEL})"
        self.tracer.log(self.name, "start",
                        f"Checking {len(reports)} dump reports oldest-first using {source}, "
                        "so a later re-report of the same pile is the one marked duplicate.")

        for report in sorted(reports, key=lambda r: r["reported_at"]):
            vision = self._assess_photo(report)
            match = self._find_duplicate(report, results)
            status, why = self._decide(vision, match)
            result = {
                "report_id": report["id"],
                "zone": report["zone"],
                "lat": report["lat"],
                "lon": report["lon"],
                "est_kg": report["est_kg"],
                "reported_at": report["reported_at"],
                "status": status,
                "confidence": vision["confidence"],
                "model_duplicate": vision["duplicate"],
                "model_reason": vision["reason"],
                "model_error": vision.get("error", False),
                "duplicate_of": match["report_id"] if match else None,
                "decision_reason": why,
            }
            results.append(result)
            self.world.record_verification(result)
            self.tracer.log(self.name, f"verdict {report['id']}", f"{status.upper()}: {why}",
                            confidence=vision["confidence"], model_duplicate=vision["duplicate"],
                            duplicate_of=result["duplicate_of"])

        counts = {s: sum(r["status"] == s for r in results)
                  for s in ("verified", "duplicate", "needs_review", "rejected")}
        self.tracer.log(self.name, "summary",
                        f"Only the {counts['verified']} verified report(s) go to Dispatch; "
                        f"{counts['needs_review']} held for human review, {counts['duplicate']} merged as duplicates, "
                        f"{counts['rejected']} rejected.", counts=counts)
        return results

    # ---- decision logic -------------------------------------------------

    def _decide(self, vision, match):
        if match:
            agree = "the model agrees" if vision["duplicate"] else "the model did not flag it"
            return "duplicate", (f"{match['_dist_m']:.0f} m and {match['_hours']:.1f} h from "
                                 f"{match['report_id']} - same pile ({agree}).")
        if vision.get("error"):
            return "needs_review", f"Model could not assess the photo ({vision['reason']}); not treating missing evidence as a rejection."
        if vision["duplicate"]:
            return "needs_review", ("Model suspects a reused photo but no nearby earlier report confirms it; "
                                    "conflicting evidence goes to a human.")
        c = vision["confidence"]
        if c >= config.VERIFY_ACCEPT:
            return "verified", f"Confidence {c:.2f} >= {config.VERIFY_ACCEPT} accept bar. {vision['reason']}"
        if c >= config.VERIFY_REJECT:
            return "needs_review", (f"Confidence {c:.2f} is between {config.VERIFY_REJECT} and "
                                    f"{config.VERIFY_ACCEPT}: too weak to dispatch, too strong to discard. {vision['reason']}")
        return "rejected", f"Confidence {c:.2f} < {config.VERIFY_REJECT}. {vision['reason']}"

    def _find_duplicate(self, report, earlier):
        t = datetime.fromisoformat(report["reported_at"])
        for prev in earlier:
            if prev["status"] not in ("verified", "needs_review"):
                continue
            dist = haversine_m(report, prev)
            hours = abs((t - datetime.fromisoformat(prev["reported_at"])).total_seconds()) / 3600
            if dist <= config.DUPLICATE_RADIUS_M and hours <= config.DUPLICATE_WINDOW_H:
                return {**prev, "_dist_m": dist, "_hours": hours}
        return None

    # ---- photo assessment -----------------------------------------------

    def _assess_photo(self, report):
        if self.sample_mode:
            return dict(report["simulated_vision"])
        return self._call_nim(report)

    def _call_nim(self, report):
        def fail(reason):
            return {"confidence": 0.0, "duplicate": False, "reason": reason, "error": True}

        if time.monotonic() > self.deadline:
            return fail("run time budget exhausted before this report")
        if not config.NVIDIA_API_KEY:
            return fail("NVIDIA_API_KEY not set in .env")
        photo = config.ROOT / report["photo_path"]
        if not photo.exists():
            return fail(f"photo file missing: {report['photo_path']}")

        b64 = base64.b64encode(photo.read_bytes()).decode()
        if len(b64) > config.NIM_MAX_IMAGE_B64:
            return fail("photo too large for inline upload (resize below ~130 KB)")
        mime = mimetypes.guess_type(photo.name)[0] or "image/jpeg"

        prompt = (
            "You verify citizen reports of illegal waste dumps for a waste-collection service in Buea, Cameroon. "
            f"Reporter's description: \"{report['description']}\". "
            "Look at the photo and reply with ONLY a JSON object, no other text: "
            '{"confidence": <number 0-1 that the photo shows a real, uncollected waste dump>, '
            '"duplicate": <true if the photo looks like a screenshot, stock image or re-upload, else false>, '
            '"reason": "<one short sentence>"}'
        )
        try:
            text = nim.chat(f'{prompt} <img src="data:{mime};base64,{b64}" />')
        except nim.NIMError as exc:
            return fail(str(exc))
        self.tracer.log(self.name, f"model reply {report['id']}", "Raw vision-model output received.", raw=text[:500])
        return parse_vision_response(text)
