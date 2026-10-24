"""Meterless World Model: event-sourced shared state for the agent pipeline."""
from world_model.core import (WorldModel, canonical_json, decide, entity_id_from_name, external_id,
                              name_similarity, normalize_name)

__all__ = ["WorldModel", "canonical_json", "decide", "entity_id_from_name", "external_id",
           "name_similarity", "normalize_name"]
