"""
Step 6 (continued) — thin LLM narrator.

Wraps the deterministic ReportData into a markdown report. The LLM is given
a strict instruction: numbers come from the metrics block; quotes come from
the post log. Don't invent, don't reorder rows, don't combine panelists.

If the LLM is unreachable, ``render_report_offline`` produces a numeric-only
report using only the deterministic data — that is what runs in CI.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import openai

from .metrics import HeadlineMetrics, PerPersonaRow, ReportData
from .runner import RunResult


logger = logging.getLogger("mirofish.v2.narrator")


# ---------- public ----------

def render_report_offline(study_name: str, brief_title: str,
                          report: ReportData) -> str:
    """Pure-data markdown — what we use for tests and as a fallback."""
    return _assemble(
        study_name=study_name,
        brief_title=brief_title,
        report=report,
        narrative=_default_narrative(report),
    )


def render_report_with_llm(study_name: str,
                           brief_title: str,
                           report: ReportData,
                           run: RunResult,
                           *,
                           client: openai.OpenAI | None = None,
                           model: str | None = None) -> str:
    """Generate the narrative paragraph via one LLM call constrained to the
    pre-computed numbers + verbatim quotes. Falls back to offline mode on
    any error."""
    try:
        narrative = _llm_narrative(report, run, client=client, model=model)
    except Exception as e:                                # pragma: no cover
        logger.warning("LLM narrator failed (%s); falling back to offline", e)
        narrative = _default_narrative(report)
    return _assemble(study_name=study_name, brief_title=brief_title,
                     report=report, narrative=narrative)


# ---------- internals ----------

def _assemble(study_name: str, brief_title: str, report: ReportData,
              narrative: str) -> str:
    h = report.headline
    rows = report.per_persona

    table_header = (
        "| Panelist | Watched | AI score | Themes | Clarity | Reaction |\n"
        "|---|---|---|---|---|---|"
    )
    table_lines = [table_header]
    for r in rows:
        score = "—" if r.ai_score is None else str(r.ai_score)
        themes = ", ".join(t.replace("_", " ") for t in r.themes) if r.themes else "—"
        reaction = r.reaction.replace("|", "\\|").replace("\n", " ").strip()
        table_lines.append(
            f"| {r.persona_name} | {_watched_label(r.watched)} | {score} "
            f"| {themes} | {r.clarity} | {reaction} |"
        )
    table_md = "\n".join(table_lines)

    ai_str = "—" if h.appreciation_index is None else f"{h.appreciation_index:.1f}"
    return f"""# {study_name} — {brief_title}

## Headline metrics

- **Reach** {h.reach}/{h.panel_size}
- **Engagement** (more than half) {h.engagement}/{h.panel_size}
- **Appreciation Index (AI)** {ai_str}
- **Clarity risk** {h.clarity_risk}/{h.panel_size}

## Narrative

{narrative.strip()}

## Per-panelist table

{table_md}

## Provenance

- Numbers in this report were computed deterministically from the simulation
  post log. The narrative above is the only LLM-touched text.
- study_id: `{report.study_id}`
- brief_id: `{report.brief_id}`
"""


def _watched_label(value: str) -> str:
    return value.replace("_", " ")


def _default_narrative(report: ReportData) -> str:
    h = report.headline
    return (
        f"{h.reach} of {h.panel_size} viewers watched any part of the "
        f"content; {h.engagement} watched more than half. "
        f"{h.clarity_risk} flagged the message as unclear."
    )


def _llm_narrative(report: ReportData, run: RunResult, *,
                   client: openai.OpenAI | None,
                   model: str | None) -> str:
    client = client or _make_client()
    model = model or os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
    metrics_block = json.dumps(report.as_dict(), indent=2)
    quotes_block = "\n".join(
        f"- {p.persona_name}: {p.text[:240]}" for p in run.posts[:30]
    )
    system = (
        "You write short evaluation summaries for TV programme tests. "
        "You may ONLY use the numbers shown in the metrics block. "
        "You may quote panelists VERBATIM from the quotes block. "
        "Do not invent panelists, ratings, or themes. "
        "Output 4-7 sentences, no headings, no bullet points."
    )
    user = (
        f"Metrics:\n{metrics_block}\n\n"
        f"Verbatim quotes from the simulation:\n{quotes_block}\n\n"
        f"Write a 4-7 sentence narrative that reads off the metrics and "
        f"references at most three panelists by name with a short verbatim "
        f"quote. End with one sentence on whether the launch should worry."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.4,
        max_tokens=400,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _make_client() -> openai.OpenAI:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("OPENAI_API_BASE_URL") or os.environ.get("LLM_BASE_URL")
    if not key:
        raise RuntimeError(
            "no OPENAI_API_KEY / LLM_API_KEY in environment for narrator"
        )
    return openai.OpenAI(api_key=key, base_url=base_url) if base_url else openai.OpenAI(api_key=key)
