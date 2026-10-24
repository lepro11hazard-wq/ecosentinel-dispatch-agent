"""Meterless World Model (engines/world-model/AGENTS.md), ported to Python.

Event-sourced: state is a fold over an append-only event log, and every write
also adds an audit entry. Rules follow the Meterless TypeScript reference:

- Stable IDs: externally keyed records use `type:system:externalId`; name-keyed
  entities use ent_<sha256(type:normalizedName)[:16]>.
- Merge bands: similarity >= 0.85 auto-merges, [0.82, 0.85) queues for review.
- Every write carries provenance (kind + at). No provenance, no write.
- Relationships are bitemporal: a new open edge with the same (from, type,
  context) and a different target closes the old one (validTo + supersededBy).
- Ingest is idempotent: re-ingesting the same batch appends nothing.
- Record timestamps come from provenance (`source.at`), never the wall clock.

Standard library only. Storage is in-memory, or a JSON file that is replayed
on load (`path=`).
"""
import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
AUTO_MERGE_AT = 0.85
REVIEW_LOWER = 0.82

_ZERO_WIDTH = re.compile("[​-‍﻿]")
_SPACES = re.compile(r"\s+")


# ---- stable ids ----------------------------------------------------------

def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value):
    """Sorted keys, no whitespace: the same state always serialises identically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_name(name):
    """NFKD -> strip combining marks -> strip zero-width -> lowercase -> collapse whitespace."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _ZERO_WIDTH.sub("", text).lower()
    return _SPACES.sub(" ", text).strip()


def external_id(type_, system, ext_id):
    return f"{type_}:{system}:{ext_id}"


def entity_id_from_name(type_, name):
    return "ent_" + sha256_hex(f"{type_}:{normalize_name(name)}")[:16]


def context_id_from_attrs(type_, attrs, parent=None):
    return "ctx_" + sha256_hex(f"{type_}:{canonical_json(attrs)}:{parent or ''}")[:16]


def relationship_id(from_, type_, to, context=None, valid_from=None):
    return "rel_" + sha256_hex(f"{from_}|{type_}|{to}|{context or ''}|{valid_from or ''}")[:16]


def fact_id(about, predicate, value, asserted_at, source_kind):
    return "fact_" + sha256_hex(f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}")[:16]


# ---- resolution ------------------------------------------------------------

def levenshtein(a, b):
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def name_similarity(raw_a, raw_b):
    a, b = normalize_name(raw_a), normalize_name(raw_b)
    longest = max(len(a), len(b))
    return 1.0 if longest == 0 else 1 - levenshtein(a, b) / longest


def decide(similarity, auto_merge_at=AUTO_MERGE_AT, review_lower=REVIEW_LOWER):
    """Merge band for a name similarity. Boundary values: 0.85 merges, 0.82 queues."""
    if similarity >= auto_merge_at:
        return "auto-merge"
    if similarity >= review_lower:
        return "review"
    return "new"


# ---- the world -------------------------------------------------------------

class WorldModel:
    """One world. All writes go through `_append`, so the log is the only source of truth."""

    def __init__(self, namespace, path=None):
        if not namespace:
            raise ValueError("WorldModel: namespace is required")
        self.namespace = namespace
        self.path = Path(path) if path else None
        self.events = []
        self.audit_log = []
        self.merge_queue = []
        self._reset_state()
        if self.path and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.audit_log = saved.get("audit", [])
            self.merge_queue = saved.get("merge_queue", [])
            for event in saved.get("events", []):
                self.events.append(event)
                self._fold(event)

    def _reset_state(self):
        self.entities, self.contexts, self.relationships = {}, {}, {}
        self.facts, self.aliases, self.observations = {}, {}, {}

    # ---- log -------------------------------------------------------------

    def _append(self, kind, payload, at):
        event = {"seq": len(self.events) + 1, "kind": kind, "payload": payload,
                 "schemaVersion": SCHEMA_VERSION, "at": at}
        self.events.append(event)
        self._fold(event)
        return event

    def _log_audit(self, kind, actor, summary, at, event=None):
        self.audit_log.append({"seq": len(self.audit_log) + 1, "at": at, "kind": kind, "actor": actor,
                               "summary": summary, "eventSeq": event["seq"] if event else None})

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"namespace": self.namespace, "schemaVersion": SCHEMA_VERSION, "events": self.events,
                    "audit": self.audit_log, "merge_queue": self.merge_queue}
            self.path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def _fold(self, event):
        p = copy.deepcopy(event["payload"])
        kind = event["kind"]
        if kind == "upsertEntity":
            existing = self.entities.get(p["id"])
            if existing is None:
                self.entities[p["id"]] = p
            else:
                existing["attrs"].update(p["attrs"])
                existing["updatedAt"] = p["updatedAt"]
                existing["source"] = p["source"]
                if p.get("name"):
                    if p["name"] not in existing["aliases"]:
                        existing["aliases"].append(p["name"])
                    existing["name"] = p["name"]
        elif kind == "upsertContext":
            existing = self.contexts.get(p["id"])
            if existing is None:
                self.contexts[p["id"]] = p
            else:
                existing["attrs"].update(p["attrs"])
                existing["updatedAt"] = p["updatedAt"]
        elif kind == "relate":
            for other in self.relationships.values():
                if (other["from"] == p["from"] and other["type"] == p["type"]
                        and other.get("context") == p.get("context") and other["to"] != p["to"]
                        and not other.get("validTo") and not other.get("supersededBy")):
                    other["validTo"] = p["validFrom"]
                    other["supersededBy"] = p["id"]
            self.relationships.setdefault(p["id"], p)
        elif kind == "assertFact":
            # most-recent-wins; on an equal timestamp the later event wins
            rivals = [f for f in self.facts.values() if f["about"] == p["about"]
                      and f["predicate"] == p["predicate"] and not f.get("supersededBy")]
            newer = [f for f in rivals if f["assertedAt"] > p["assertedAt"]]
            if newer:
                p["supersededBy"] = newer[0]["id"]
            else:
                for other in rivals:
                    other["supersededBy"] = p["id"]
            self.facts[p["id"]] = p
            entity = self.entities.get(p["about"])
            if entity is not None and not p.get("supersededBy"):
                entity["attrs"][p["predicate"]] = p["value"]
                entity["updatedAt"] = p["assertedAt"]
        elif kind == "alias":
            self.aliases[p["aliasId"]] = p["primaryId"]
        elif kind == "observe":
            self.observations[p["externalId"]] = p

    def resolve_alias(self, id_):
        seen = set()
        while id_ in self.aliases and id_ not in seen:
            seen.add(id_)
            id_ = self.aliases[id_]
        return id_

    # ---- writes ----------------------------------------------------------

    @staticmethod
    def _provenance(source):
        if not source or not source.get("kind") or not source.get("at"):
            raise ValueError("World Model: every write carries provenance (kind + at). No provenance, no write.")
        return dict(source)

    def upsert_entity(self, type, source, name=None, external_key=None, attrs=None, state="active"):
        source = self._provenance(source)
        name = name or (attrs or {}).get("name")
        if external_key:
            id_ = external_id(type, external_key["system"], external_key["id"])
        elif name:
            id_ = entity_id_from_name(type, name)
            for other in list(self.entities.values()):
                if other["type"] != type or other["id"] == id_ or not other.get("name"):
                    continue
                similarity = name_similarity(name, other["name"])
                band = decide(similarity)
                if band == "auto-merge":
                    self._append("alias", {"aliasId": id_, "primaryId": other["id"],
                                           "reason": f"similarity {similarity:.3f}"}, source["at"])
                    self._log_audit("auto-merge", "resolver", f"{name} -> {other['name']} ({similarity:.3f})",
                                    source["at"])
                    break
                if band == "review" and not any({q["leftId"], q["rightId"]} == {id_, other["id"]}
                                                for q in self.merge_queue):
                    self.merge_queue.append({"id": f"merge_{len(self.merge_queue) + 1}", "leftId": id_,
                                             "rightId": other["id"], "similarity": similarity,
                                             "status": "pending", "proposedAt": source["at"]})
                    self._log_audit("merge-proposed", "resolver", f"{name} ~ {other['name']} ({similarity:.3f})",
                                    source["at"])
        else:
            raise ValueError("World Model: name-keyed entities need a name (or provide an external_key)")
        return self._upsert_entity_event(self.resolve_alias(id_), type, source, name, attrs, state)

    def _upsert_entity_event(self, id_, type_, source, name, attrs, state="active"):
        entity = {"id": id_, "type": type_, "name": name, "aliases": [name] if name else [],
                  "attrs": dict(attrs or {}), "state": state, "schemaVersion": SCHEMA_VERSION,
                  "source": source, "createdAt": source["at"], "updatedAt": source["at"]}
        event = self._append("upsertEntity", entity, source["at"])
        self._log_audit("upsertEntity", source.get("by", "system"), f"{id_} ({type_})", source["at"], event)
        return self.entities[id_]

    def upsert_context(self, type, source, external_key=None, attrs=None, parent=None):
        source = self._provenance(source)
        id_ = (external_id(type, external_key["system"], external_key["id"]) if external_key
               else context_id_from_attrs(type, attrs or {}, parent))
        context = {"id": id_, "type": type, "attrs": dict(attrs or {}), "parent": parent,
                   "schemaVersion": SCHEMA_VERSION, "source": source,
                   "createdAt": source["at"], "updatedAt": source["at"]}
        event = self._append("upsertContext", context, source["at"])
        self._log_audit("upsertContext", source.get("by", "system"), f"{id_} ({type})", source["at"], event)
        return self.contexts[id_]

    def relate(self, from_, type, to, source, context=None, attrs=None, valid_from=None, valid_to=None):
        source = self._provenance(source)
        from_, to = self.resolve_alias(from_), self.resolve_alias(to)
        valid_from = valid_from or source["at"]
        # Re-stating an edge that is already open is a no-op, so unchanged
        # assignments keep their original validFrom across plan revisions.
        for rel in self.relationships.values():
            if (rel["from"], rel["type"], rel["to"], rel.get("context")) == (from_, type, to, context) \
                    and not rel.get("validTo"):
                return rel
        id_ = relationship_id(from_, type, to, context, valid_from)
        if id_ in self.relationships:
            return self.relationships[id_]
        rel = {"id": id_, "from": from_, "type": type, "to": to, "context": context, "attrs": attrs or {},
               "validFrom": valid_from, "validTo": valid_to, "schemaVersion": SCHEMA_VERSION,
               "source": source, "createdAt": source["at"]}
        event = self._append("relate", rel, source["at"])
        self._log_audit("relate", source.get("by", "system"), f"{from_} -[{type}]-> {to}", source["at"], event)
        return self.relationships[id_]

    def assert_fact(self, about, predicate, value, source, confidence=0.5):
        source = self._provenance(source)
        about = self.resolve_alias(about)
        id_ = fact_id(about, predicate, value, source["at"], source["kind"])
        if id_ in self.facts:
            return self.facts[id_]
        if about not in self.entities:
            type_ = about.split(":")[0] if ":" in about else "entity"
            self._upsert_entity_event(about, type_, source, None, {}, state="emerging")
        fact = {"id": id_, "about": about, "predicate": predicate, "value": value, "confidence": confidence,
                "source": source, "assertedAt": source["at"]}
        event = self._append("assertFact", fact, source["at"])
        self._log_audit("assertFact", source.get("by", "system"), f"{about}.{predicate} = {canonical_json(value)}",
                        source["at"], event)
        return self.facts[id_]

    def ingest(self, observations):
        """Idempotent ingest: unchanged observations (same checksum) append nothing."""
        before = self.state_shape_canonical()
        processed = skipped = 0
        for obs in observations:
            checksum = sha256_hex(canonical_json({"externalId": obs["externalId"], "title": obs["title"],
                                                  "text": obs.get("text", ""), "at": obs["at"]}))
            known = self.observations.get(obs["externalId"])
            if known and known["checksum"] == checksum:
                skipped += 1
                continue
            processed += 1
            source = self._provenance({**(obs.get("source") or {}), "at": obs["at"]})
            type_ = obs.get("type", "item")
            item = self._upsert_entity_event(external_id(type_, "ingest", obs["externalId"]), type_, source,
                                             obs["title"], {"title": obs["title"], "text": obs.get("text")})
            mentioned = []
            for mention in obs.get("entities", []):
                mid = self.resolve_alias(entity_id_from_name(mention["type"], mention["name"]))
                self._upsert_entity_event(mid, mention["type"], source, mention["name"], {})
                mentioned.append(mid)
                self.relate(item["id"], "mentions", mid, source)
            self._append("observe", {"externalId": obs["externalId"], "entityId": item["id"],
                                     "entityIds": sorted(mentioned), "checksum": checksum, "at": obs["at"]},
                         obs["at"])
        changed = self.state_shape_canonical() != before
        at = observations[0]["at"] if observations else None
        self._log_audit("ingest", "pipeline", f"{processed} processed, {skipped} skipped", at)
        return {"processed": processed, "skippedUnchanged": skipped, "changed": changed}

    # ---- reads -----------------------------------------------------------

    def neighbors_of(self, id_, via=None, at=None):
        """Entities linked to id_. Without `at`, only open edges; with `at`, edges valid at that instant."""
        id_ = self.resolve_alias(id_)
        out = {}
        for rel in self.relationships.values():
            if via and rel["type"] != via:
                continue
            if at is None and rel.get("validTo"):
                continue
            if at is not None and not (rel["validFrom"] <= at and (not rel.get("validTo") or at < rel["validTo"])):
                continue
            other = rel["to"] if rel["from"] == id_ else rel["from"] if rel["to"] == id_ else None
            if other in self.entities:
                out[other] = self.entities[other]
        return [out[k] for k in sorted(out)]

    def edges(self, type, open_only=True):
        rels = [r for r in self.relationships.values() if r["type"] == type and not (open_only and r.get("validTo"))]
        return sorted(rels, key=lambda r: r["id"])

    def current_fact(self, about, predicate):
        live = [f for f in self.facts.values()
                if f["about"] == self.resolve_alias(about) and f["predicate"] == predicate and not f.get("supersededBy")]
        return live[-1] if live else None

    def query(self, type=None):
        return [e for _, e in sorted(self.entities.items()) if type is None or e["type"] == type]

    def audit(self):
        return list(self.audit_log)

    def state_shape(self):
        """Everything derived from the log, in sorted order: the idempotency diff surface."""
        def ordered(mapping):
            return [mapping[k] for k in sorted(mapping)]
        return {"entities": ordered(self.entities), "contexts": ordered(self.contexts),
                "relationships": ordered(self.relationships), "facts": ordered(self.facts),
                "aliases": dict(sorted(self.aliases.items())), "observations": ordered(self.observations)}

    def state_shape_canonical(self):
        return canonical_json(self.state_shape())

    def replay_matches(self):
        """Rebuild state from the event log alone and check it equals the live state."""
        replica = WorldModel(self.namespace)
        for event in self.events:
            replica.events.append(event)
            replica._fold(event)
        return replica.state_shape_canonical() == self.state_shape_canonical()
