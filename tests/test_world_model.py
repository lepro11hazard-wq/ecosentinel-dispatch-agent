"""World Model contracts, mirrored from the Meterless conformance suite."""
import pytest

from world_model import WorldModel, decide, entity_id_from_name, normalize_name

OBS = [
    {"externalId": "a1", "title": "Fed holds rates", "at": "2026-05-17T14:00:00.000Z",
     "source": {"kind": "rss"}, "entities": [{"type": "person", "name": "Jerome Powell"}]},
    {"externalId": "a2", "title": "Powell signals patience", "at": "2026-05-17T16:30:00.000Z",
     "source": {"kind": "rss"}, "entities": [{"type": "person", "name": "Jerome Powell"}]},
]


def src(at):
    return {"kind": "manual", "at": at}


def test_stable_ids():
    assert normalize_name("José  García") == normalize_name("Jose Garcia")
    assert entity_id_from_name("person", "José García") == entity_id_from_name("person", "jose garcia")
    assert entity_id_from_name("org", "Acme Corp") != entity_id_from_name("org", "Acme Corporation")
    assert entity_id_from_name("person", "x") != entity_id_from_name("org", "x")


def test_merge_bands():
    assert [decide(s) for s in (0.85, 0.84, 0.82, 0.81)] == ["auto-merge", "review", "review", "new"]


def test_no_provenance_no_write():
    with pytest.raises(ValueError):
        WorldModel("t").upsert_entity("person", None, name="No Provenance")


def test_ingest_is_idempotent_and_detects_edits():
    world = WorldModel("t")
    world.ingest(OBS)
    shape = world.state_shape_canonical()
    assert world.ingest(OBS)["changed"] is False
    assert world.state_shape_canonical() == shape
    edited = [{**OBS[0], "title": OBS[0]["title"] + " (edited)"}, OBS[1]]
    assert world.ingest(edited)["changed"] is True
    assert world.state_shape_canonical() != shape


def test_superseding_edge_and_point_in_time_query():
    world = WorldModel("t")
    for type_, key, at in (("player", "p1", "2087-01-01"), ("team", "a", "2087-01-01"), ("team", "b", "2088-03-01")):
        world.upsert_entity(type_, src(at), external_key={"system": "g", "id": key})
    world.relate("player:g:p1", "plays-for", "team:g:a", src("2087-01-01"), valid_from="2087-01-01")
    world.relate("player:g:p1", "plays-for", "team:g:b", src("2088-03-01"), valid_from="2088-03-01")
    assert [e["id"] for e in world.neighbors_of("player:g:p1", via="plays-for")] == ["team:g:b"]
    assert [e["id"] for e in world.neighbors_of("player:g:p1", via="plays-for", at="2087-06-01")] == ["team:g:a"]


def test_audit_is_ordered_and_state_survives_reload(tmp_path):
    path = tmp_path / "world.json"
    world = WorldModel("t", path=path)
    world.upsert_entity("x", src("2026-01-01"), name="Audit One")
    world.assert_fact("x:demo:1", "status", "old", src("2026-01-02"))
    world.assert_fact("x:demo:1", "status", "new", src("2026-01-03"))
    world.save()
    seqs = [a["seq"] for a in world.audit()]
    assert seqs == sorted(set(seqs)) and len(seqs) >= 3
    assert world.current_fact("x:demo:1", "status")["value"] == "new"
    reloaded = WorldModel("t", path=path)
    assert reloaded.state_shape_canonical() == world.state_shape_canonical()
    assert world.replay_matches()
