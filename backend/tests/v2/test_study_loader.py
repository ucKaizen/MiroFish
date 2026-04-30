"""Determinism + schema-validation tests for the Path A study loader."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.v2.loaders import (
    Brief,
    DimensionEdge,
    IdentityNode,
    StudyLoadError,
    load_study,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BBC_PANEL = REPO_ROOT / "backend" / "seeds" / "bbc_panel" / "study.json"


# ---------- happy path ----------

def test_bbc_panel_loads():
    s = load_study(BBC_PANEL)
    assert s.study_id == "bbc_panel_sherwood_s2_2024"
    assert s.identity_label == "Panelist"
    assert len(s.nodes) == 10
    # 10 panelists × 9 genres + 10 × 8 slots = 90 + 80
    assert len(s.edges) == 170
    # 9 distinct genres + 8 distinct slots
    assert len(s.target_nodes) == 17


def test_brief_parsed():
    brief = load_study(BBC_PANEL).brief
    assert isinstance(brief, Brief)
    assert brief.content_id == "sherwood_s2_e1_2024-08-25"
    assert brief.air_date == "2024-08-25"
    assert "James Graham" in (brief.synopsis or "")
    assert len(brief.rules) == 4


def test_attribute_dimension_flattens_polarity_enum_into_arrays():
    s = load_study(BBC_PANEL)
    aisha = s.by_key("aisha")
    # Polarity-grouped attribute → split into two list[str] properties.
    assert "vocabulary_forbidden" in aisha.attributes
    assert isinstance(aisha.attributes["vocabulary_forbidden"], list)
    assert all(isinstance(t, str) for t in aisha.attributes["vocabulary_forbidden"])
    # Indexed attribute → ordered list[str].
    assert isinstance(aisha.attributes["voice_examples"], list)
    assert aisha.attributes["voice_examples"][0].startswith("Didn't watch")


def test_typed_edges_have_propensity_property():
    s = load_study(BBC_PANEL)
    sample = next(e for e in s.edges if e.edge_type == "PROPENSITY_FOR_GENRE")
    assert isinstance(sample, DimensionEdge)
    assert sample.source_label == "Panelist"
    assert sample.target_label == "Genre"
    assert "propensity" in sample.properties
    assert isinstance(sample.properties["propensity"], float)


def test_load_is_byte_deterministic():
    """Two consecutive loads must serialise identically. Path A's whole
    selling point: no LLM nondeterminism between runs."""
    a = load_study(BBC_PANEL)
    b = load_study(BBC_PANEL)
    sa = json.dumps(_canonical(a), sort_keys=True, default=str).encode()
    sb = json.dumps(_canonical(b), sort_keys=True, default=str).encode()
    assert hashlib.sha256(sa).hexdigest() == hashlib.sha256(sb).hexdigest()


# ---------- error paths ----------

def test_missing_file(tmp_path: Path):
    with pytest.raises(StudyLoadError, match="does not exist"):
        load_study(tmp_path / "does_not_exist.json")


def test_invalid_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(StudyLoadError, match="invalid JSON"):
        load_study(bad)


def test_schema_violation_caught(tmp_path: Path):
    """A study missing the brief block is rejected by the schema validator,
    not silently."""
    bad_dir = tmp_path / "study"
    bad_dir.mkdir()
    (bad_dir / "panelists.csv").write_text(
        "panelist_id,name\np01,Margaret\n", encoding="utf-8"
    )
    (bad_dir / "study.json").write_text(json.dumps({
        "study_id": "broken",
        "identity": {
            "label": "Panelist",
            "key": "panelist_id",
            "csv": "panelists.csv",
            "columns": [
                {"name": "panelist_id", "type": "string"},
                {"name": "name",        "type": "string"},
            ],
        },
        # NB: brief is required by the schema, intentionally missing.
    }), encoding="utf-8")
    with pytest.raises(StudyLoadError, match="schema violation"):
        load_study(bad_dir / "study.json")


def test_duplicate_identity_key_rejected(tmp_path: Path):
    d = tmp_path / "study"
    d.mkdir()
    (d / "panelists.csv").write_text(
        "panelist_id,name\np01,A\np01,B\n", encoding="utf-8"
    )
    (d / "study.json").write_text(json.dumps({
        "study_id": "dup",
        "identity": {
            "label": "Panelist",
            "key": "panelist_id",
            "csv": "panelists.csv",
            "columns": [
                {"name": "panelist_id", "type": "string"},
                {"name": "name",        "type": "string"},
            ],
        },
        "brief": {"content_id": "x", "title": "X"},
    }), encoding="utf-8")
    with pytest.raises(StudyLoadError, match="duplicate key"):
        load_study(d / "study.json")


def test_edge_to_unknown_identity_rejected(tmp_path: Path):
    d = tmp_path / "study"
    d.mkdir()
    (d / "panelists.csv").write_text(
        "panelist_id,name\np01,A\n", encoding="utf-8"
    )
    (d / "edges.csv").write_text(
        "panelist_id,genre,propensity\nGHOST,drama,0.5\n",
        encoding="utf-8",
    )
    (d / "study.json").write_text(json.dumps({
        "study_id": "ghost",
        "identity": {
            "label": "Panelist",
            "key": "panelist_id",
            "csv": "panelists.csv",
            "columns": [
                {"name": "panelist_id", "type": "string"},
                {"name": "name",        "type": "string"},
            ],
        },
        "dimensions": [{
            "kind": "edge",
            "csv": "edges.csv",
            "join_on": "panelist_id",
            "edge": "PROPENSITY_FOR_GENRE",
            "target": {"label": "Genre", "key": "genre"},
            "columns": [
                {"name": "panelist_id", "type": "string"},
                {"name": "genre",       "type": "string"},
                {"name": "propensity",  "type": "float"},
            ],
        }],
        "brief": {"content_id": "x", "title": "X"},
    }), encoding="utf-8")
    with pytest.raises(StudyLoadError, match="does not match any identity row"):
        load_study(d / "study.json")


# ---------- helpers ----------

def _canonical(s) -> dict:
    """Strip out unhashable bits and reduce to a stable dict."""
    return {
        "study_id":    s.study_id,
        "name":        s.name,
        "description": s.description,
        "brief":       asdict(s.brief),
        "engagement":  s.engagement,
        "nodes": [
            {"label": n.label, "key": n.key_value,
             "props": dict(sorted(n.properties.items())),
             "attrs": dict(sorted(n.attributes.items()))}
            for n in s.nodes
        ],
        "edges": [
            {"type": e.edge_type, "src": e.source_key_value,
             "tgt": e.target_key_value,
             "props": dict(sorted(e.properties.items()))}
            for e in s.edges
        ],
    }
