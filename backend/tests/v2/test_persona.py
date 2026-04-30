"""Persona projector tests — pure deterministic, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.v2.loaders import load_study
from app.v2.persona import (
    EngagementProfile,
    Persona,
    SamplingConfig,
    StyleProfile,
    project_personas,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BBC_PANEL = REPO_ROOT / "backend" / "seeds" / "bbc_panel" / "study.json"


def test_projection_count_matches_identity_count():
    study = load_study(BBC_PANEL)
    personas, stats = project_personas(study)
    assert len(personas) == len(study.nodes) == 10
    assert stats.persona_count == 10


def test_projection_is_deterministic():
    study = load_study(BBC_PANEL)
    a, _ = project_personas(study)
    b, _ = project_personas(study)
    assert [p.bio for p in a] == [p.bio for p in b]
    assert [p.engagement.salience_threshold for p in a] == \
           [p.engagement.salience_threshold for p in b]
    assert [p.sampling.temperature for p in a] == \
           [p.sampling.temperature for p in b]


def test_lurker_gets_narrow_action_menu_and_higher_threshold():
    """Aisha is a low-engagement medical student in tinytroupe-panel.
    Her persona should reflect that with a small action menu and a higher
    salience threshold than a heavy viewer."""
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    aisha = pmap["aisha"]
    geoffrey = pmap["geoffrey"]
    # Aisha should be a lurker (≤ light), Geoffrey should be vocal/full.
    assert len(aisha.available_actions) <= 3
    assert len(geoffrey.available_actions) >= 4
    # Aisha's threshold to engage is higher than Geoffrey's.
    assert aisha.engagement.salience_threshold > geoffrey.engagement.salience_threshold


def test_voice_register_drives_temperature():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    # slangy_terse (Jaden) > precise_formal (Geoffrey).
    assert pmap["jaden"].sampling.temperature > pmap["geoffrey"].sampling.temperature


def test_system_prompt_includes_anti_engagement_instruction():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    aisha = next(p for p in personas if p.panelist_id == "aisha")
    prompt = aisha.system_prompt(brief_synopsis="Test brief.")
    # The "ignore most posts" framing is the keystone of the no-fork
    # engagement story; if it disappears, the LLM will over-engage.
    assert "DO_NOTHING" in prompt
    assert "Real viewers ignore most content" in prompt
    assert "Test brief." in prompt
    assert "MUST NOT" in prompt or "MUST use" in prompt   # vocab block present


def test_genre_and_slot_propensities_carry_through():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    marcus = next(p for p in personas if p.panelist_id == "marcus")
    # Marcus's panel_seed propensity for political_drama is 0.95 — the loader
    # and projector must preserve that exactly.
    assert marcus.genre_propensity["political_drama"] == 0.95
    assert marcus.slot_propensity["sunday_2100_bbc1"] == 0.95
