"""Metrics computation + offline narrator rendering tests."""
from __future__ import annotations

from pathlib import Path

from app.v2.loaders import load_study
from app.v2.metrics import HeadlineMetrics, ReportData, compute_metrics
from app.v2.narrator import render_report_offline
from app.v2.persona import project_personas
from app.v2.runner import GateDecision, PostRecord, RunResult


REPO_ROOT = Path(__file__).resolve().parents[3]
BBC_PANEL = REPO_ROOT / "backend" / "seeds" / "bbc_panel" / "study.json"


def _build_run(personas, posts: list[PostRecord]) -> RunResult:
    """Helper — wrap a fixed post list in a RunResult."""
    decisions = [GateDecision(
        persona_id=p.panelist_id, round_idx=1, post_id="brief",
        salience=None, decision="engage", reason="test"
    ) for p in personas if any(r.persona_id == p.panelist_id for r in posts)]
    return RunResult(
        study_id="test_study", brief_id="test_brief",
        started_at="2025-01-01T00:00:00Z", finished_at="2025-01-01T00:01:00Z",
        rounds=1, posts=posts, decisions=decisions,
        persona_ids=[p.panelist_id for p in personas],
        llm_calls=len(posts), posts_created=len(posts),
        cache_stats={"rows": 0, "model": "test"},
    )


def _post(persona_id: str, persona_name: str, text: str,
          round_idx: int = 1, action: str = "CREATE_POST") -> PostRecord:
    return PostRecord(
        post_id=f"p_{persona_id}", persona_id=persona_id,
        persona_name=persona_name, round_idx=round_idx,
        action=action, text=text, parent_post_id="brief",
        salience=0.7, timestamp="2025-01-01T00:00:30Z",
    )


def test_metrics_count_panel_size():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    run = _build_run(personas, [])
    report = compute_metrics(personas, run)
    assert report.headline.panel_size == 10
    assert report.headline.reach == 0
    assert report.headline.engagement == 0
    assert report.headline.appreciation_index is None


def test_metrics_infer_watched_buckets():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    posts = [
        _post("aisha", pmap["aisha"].name, "Didn't watch. Anatomy paper Tuesday."),
        _post("marcus", pmap["marcus"].name,
              "Watched the full hour. Ensemble work holds the politics."),
        _post("sophie", pmap["sophie"].name,
              "Started it but couldn't keep up. Lost the thread, baby woke up."),
    ]
    run = _build_run(personas, posts)
    report = compute_metrics(personas, run)
    rows = {r.persona_id: r for r in report.per_persona}
    assert rows["aisha"].watched == "none"
    assert rows["marcus"].watched == "all"
    assert rows["sophie"].watched in ("less_than_half", "about_half")
    assert rows["sophie"].clarity == "unclear"


def test_metrics_appreciation_index_computed():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    posts = [
        _post("marcus", pmap["marcus"].name, "Watched in full. Powerful stuff."),
        _post("geoffrey", pmap["geoffrey"].name,
              "Watched the full hour. Constitutionally serious work."),
    ]
    run = _build_run(personas, posts)
    report = compute_metrics(personas, run)
    assert report.headline.reach == 2
    assert report.headline.engagement == 2     # both watched all
    assert report.headline.appreciation_index is not None
    assert 60 <= report.headline.appreciation_index <= 100


def test_strict_raters_stay_strict():
    """Aisha's negative rater_bias should keep her below the average AI even
    when she watches the same proportion as a generous rater."""
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    posts = [
        _post("aisha", pmap["aisha"].name,
              "Watched about half. Solid script."),
        _post("marcus", pmap["marcus"].name,
              "Watched about half. Solid script."),
    ]
    run = _build_run(personas, posts)
    report = compute_metrics(personas, run)
    rows = {r.persona_id: r for r in report.per_persona}
    # marcus has rater_bias=0.05 and base_rate ~0.6; aisha has rater_bias=-0.05
    # and base_rate ~0.1. Marcus must score higher.
    assert rows["marcus"].ai_score > rows["aisha"].ai_score


def test_offline_narrator_renders_all_panelists():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    posts = [_post("marcus", "Marcus Bennett",
                    "Watched the full hour. Ensemble work holds.")]
    run = _build_run(personas, posts)
    report = compute_metrics(personas, run)
    md = render_report_offline("BBC Panel", "Sherwood S2 E1", report)
    assert "# BBC Panel — Sherwood S2 E1" in md
    assert "Marcus Bennett" in md
    assert "Reach** 1/10" in md
    # Every panelist should have a row in the table, even ones who didn't post.
    for p in personas:
        assert p.name in md, f"{p.name} missing from report"


def test_metrics_clarity_risk_counts_unclear_flags():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    pmap = {p.panelist_id: p for p in personas}
    posts = [
        _post("rita", pmap["rita"].name,
              "Couldn't keep track of the names — too many storylines, love."),
        _post("margaret", pmap["margaret"].name,
              "Too many character names introduced too quickly; I lost the thread."),
        _post("marcus", pmap["marcus"].name, "Watched in full. Solid."),
    ]
    run = _build_run(personas, posts)
    report = compute_metrics(personas, run)
    assert report.headline.clarity_risk == 2
