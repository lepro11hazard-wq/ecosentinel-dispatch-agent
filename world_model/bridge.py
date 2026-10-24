"""JSON bridge so the Meterless TypeScript conformance runner can drive the Python World Model.

    echo '{"op": "decide", "args": [0.85]}' | python -m world_model.bridge

Reads one request from stdin and writes {"ok": true, "result": ...} or
{"ok": false, "error": "..."} to stdout. Instance ops name a `state` file;
the world is replayed from it, the op applied, and the file saved again.
Used by world_model/conformance/index.ts.
"""
import json
import sys

from world_model.core import WorldModel, decide, entity_id_from_name, normalize_name

PURE = {
    "normalizeName": lambda name: normalize_name(name),
    "entityIdFromName": lambda type_, name: entity_id_from_name(type_, name),
    "decide": lambda similarity: decide(similarity),
}


def _instance_op(world, op, a):
    if op == "upsertEntity":
        return world.upsert_entity(a.get("type"), a.get("source"), name=a.get("name"),
                                   external_key=a.get("externalKey"), attrs=a.get("attrs"),
                                   state=a.get("state") or "active")
    if op == "upsertContext":
        return world.upsert_context(a.get("type"), a.get("source"), external_key=a.get("externalKey"),
                                    attrs=a.get("attrs"), parent=a.get("parent"))
    if op == "relate":
        return world.relate(a["from"], a["type"], a["to"], a.get("source"), context=a.get("context"),
                            attrs=a.get("attrs"), valid_from=a.get("validFrom"), valid_to=a.get("validTo"))
    if op == "assertFact":
        return world.assert_fact(a["about"], a["predicate"], a.get("value"), a.get("source"),
                                 confidence=a.get("confidence", 0.5))
    if op == "ingest":
        return world.ingest(a["observations"])
    if op == "neighborsOf":
        return world.neighbors_of(a["id"], via=a.get("via"), at=a.get("at"))
    if op == "audit":
        return world.audit()
    if op == "stateShapeCanonical":
        return world.state_shape_canonical()
    raise ValueError(f"unknown op {op!r}")


def handle(request):
    op = request["op"]
    if op in PURE:
        return PURE[op](*request.get("args", []))
    world = WorldModel(request["namespace"], path=request["state"])
    result = _instance_op(world, op, request.get("args") or {})
    world.save()
    return result


def main():
    try:
        reply = {"ok": True, "result": handle(json.loads(sys.stdin.read()))}
    except Exception as exc:  # reported back to the caller, which rethrows it
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(reply, ensure_ascii=False))


if __name__ == "__main__":
    main()
