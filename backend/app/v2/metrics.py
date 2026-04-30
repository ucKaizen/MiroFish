"""
Step 6 — deterministic metrics over a RunResult.

Pure compute. No LLM. Produces the four headline numbers (Reach, Engagement,
Appreciation Index, Clarity risk) plus a per-persona table that the narrator
will wrap into prose. Numbers in this module are the only place numbers are
allowed to come from — the narrator never invents.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .persona import Persona
from .runner import PostRecord, RunResult


# ---------- types ----------

@dataclass(frozen=True)
class HeadlineMetrics:
    panel_size: int
    reach: int                          # any-engagement count
    engagement: int                     # CREATE_POST or CREATE_COMMENT count
    clarity_risk: int                   # personas flagging unclear
    appreciation_index: float | None    # mean AI score across watchers


@dataclass(frozen=True)
class PerPersonaRow:
    persona_id: str
    persona_name: str
    watched: str                        # all | more_than_half | about_half | less_than_half | none
    ai_score: int | None                # 0..100 or None if didn't watch
    themes: tuple[str, ...]
    clarity: str                        # clear | unclear | "—"
    reaction: str                       # short quote


@dataclass
class ReportData:
    study_id: str
    brief_id: str
    headline: HeadlineMetrics
    per_persona: list[PerPersonaRow]

    def as_dict(self) -> dict:
        return {
            "study_id":   self.study_id,
            "brief_id":   self.brief_id,
            "headline":   asdict(self.headline),
            "per_persona": [asdict(r) for r in self.per_persona],
        }


# ---------- public entry ----------

def compute_metrics(personas: Sequence[Persona],
                    run: RunResult) -> ReportData:
    by_pid = {p.panelist_id: p for p in personas}
    posts_by_pid = _group_posts(run.posts)
    decisions_by_pid = _group_decisions(run.decisions)

    rows: list[PerPersonaRow] = []
    for persona in personas:
        rows.append(_build_row(persona, posts_by_pid.get(persona.panelist_id, []),
                               decisions_by_pid.get(persona.panelist_id, [])))

    headline = HeadlineMetrics(
        panel_size=len(personas),
        reach=sum(1 for r in rows if r.watched != "none"),
        engagement=sum(1 for r in rows if r.watched in ("more_than_half", "all")),
        clarity_risk=sum(1 for r in rows if r.clarity == "unclear"),
        appreciation_index=_round(_mean(r.ai_score for r in rows if r.ai_score is not None)),
    )
    return ReportData(
        study_id=run.study_id,
        brief_id=run.brief_id,
        headline=headline,
        per_persona=rows,
    )


# ---------- internals ----------

def _build_row(persona: Persona, posts: list[PostRecord],
               decisions: list) -> PerPersonaRow:
    if not posts:
        # Did the persona engage with the brief at all in round 1?
        round1_engaged = any(d.round_idx == 1 and d.decision == "engage"
                             for d in decisions)
        if round1_engaged:
            # Engaged but the LLM picked DO_NOTHING. Treat as "less_than_half".
            return PerPersonaRow(
                persona_id=persona.panelist_id,
                persona_name=persona.name,
                watched="less_than_half",
                ai_score=_anchor_score(persona, "less_than_half"),
                themes=_default_themes(persona),
                clarity="clear",
                reaction="(no public post)",
            )
        return PerPersonaRow(
            persona_id=persona.panelist_id,
            persona_name=persona.name,
            watched="none",
            ai_score=None,
            themes=(),
            clarity="—",
            reaction="(did not watch)",
        )

    # Round 1 post is the canonical reaction; others are diffusion replies.
    round1 = next((p for p in posts if p.round_idx == 1), posts[0])
    text = round1.text
    watched = _infer_watched(text, persona)
    clarity = _infer_clarity(text, persona)
    themes = _infer_themes(text, persona)
    score = _anchor_score(persona, watched, clarity=clarity, text=text)
    return PerPersonaRow(
        persona_id=persona.panelist_id,
        persona_name=persona.name,
        watched=watched,
        ai_score=score,
        themes=themes,
        clarity=clarity,
        reaction=text[:240],
    )


# Watch-proportion inference is heuristic but deterministic — pulls cues
# from the persona's own text rather than asking the LLM to label itself.
_WATCHED_NONE_PHRASES = ("didn't watch", "did not watch", "skipped", "didnt watch",
                          "no time", "nah", "won't watch", "wont watch")
_WATCHED_LESS_PHRASES = ("started", "switched off", "switched after", "ten minutes",
                          "20 minutes", "25 minutes", "couldn't keep", "lost the thread",
                          "fell asleep", "half-watched", "half watched")
_WATCHED_HALF_PHRASES = ("about half", "half of it", "caught most", "first half")
_WATCHED_MOST_PHRASES = ("most of it", "more than half", "stuck with", "watched through")
_WATCHED_ALL_PHRASES = ("watched the full", "in full", "all of it", "the whole thing")


def _infer_watched(text: str, persona: Persona) -> str:
    t = text.lower()
    if any(p in t for p in _WATCHED_NONE_PHRASES):
        return "none"
    if any(p in t for p in _WATCHED_ALL_PHRASES):
        return "all"
    if any(p in t for p in _WATCHED_MOST_PHRASES):
        return "more_than_half"
    if any(p in t for p in _WATCHED_HALF_PHRASES):
        return "about_half"
    if any(p in t for p in _WATCHED_LESS_PHRASES):
        return "less_than_half"
    # Default by base_rate — high engagers default to more_than_half,
    # low engagers default to less_than_half.
    if persona.engagement.base_rate > 0.5:
        return "more_than_half"
    if persona.engagement.base_rate > 0.3:
        return "about_half"
    return "less_than_half"


_CLARITY_FLAGS = ("unclear", "confusing", "couldn't keep up", "couldn't keep track",
                   "lost the thread", "couldn't follow", "too many character names",
                   "couldn't tell you", "too many storylines")


def _infer_clarity(text: str, persona: Persona) -> str:
    t = text.lower()
    if any(flag in t for flag in _CLARITY_FLAGS):
        return "unclear"
    return "clear"


# Mapping from textual cues to the canonical six themes used by BBC AI.
_THEME_CUES: dict[str, tuple[str, ...]] = {
    "informative":          ("informative", "factual", "learned", "taught", "facts"),
    "reflected_uk_life":    ("uk life", "britain", "british", "country", "community", "place"),
    "representative":       ("representative", "represented", "diverse", "ethnic", "communities"),
    "culturally_significant": ("culturally", "historical", "heritage", "important", "matters"),
    "thought_provoking":    ("thoughtful", "reflect", "thought-provoking", "complex",
                             "nuance", "nuanced", "politically", "political"),
    "emotionally_engaging": ("moving", "emotional", "touching", "gripping",
                             "powerful", "tense", "violent", "dark"),
}


def _infer_themes(text: str, persona: Persona) -> tuple[str, ...]:
    t = text.lower()
    tags: list[str] = []
    for theme, cues in _THEME_CUES.items():
        if any(c in t for c in cues):
            tags.append(theme)
    if not tags:
        # Soft fallback: grab the top genre's theme proxy.
        top = max(persona.genre_propensity.items(),
                  key=lambda kv: kv[1], default=(None, 0))
        if top[0] == "political_drama":
            tags.append("thought_provoking")
        elif top[0] in ("documentary", "news"):
            tags.append("informative")
    return tuple(tags[:3])


def _anchor_score(persona: Persona, watched: str, *, clarity: str = "clear",
                  text: str = "") -> int | None:
    """Anchor an AI score 0..100 from the persona's recorded rater_pattern.

    Generous raters stay generous; strict raters stay strict. Derived from
    the persona's average top-genre propensity nudged by rater_bias and
    penalised when watched < half or clarity == unclear.
    """
    if watched == "none":
        return None
    base = 60.0 + persona.engagement.base_rate * 40.0   # 60..100 range
    base += persona.engagement.rater_bias * 100.0        # ±10 typical
    if watched == "all":
        base += 4
    elif watched == "more_than_half":
        base += 2
    elif watched == "about_half":
        base -= 2
    elif watched == "less_than_half":
        base -= 6
    if clarity == "unclear":
        base -= 5
    return int(max(0, min(100, round(base))))


def _default_themes(persona: Persona) -> tuple[str, ...]:
    top = max(persona.genre_propensity.items(),
              key=lambda kv: kv[1], default=(None, 0))[0]
    if top == "political_drama":
        return ("thought_provoking",)
    if top in ("documentary", "news"):
        return ("informative",)
    if top == "panel_show":
        return ("emotionally_engaging",)
    return ()


def _group_posts(posts: Iterable[PostRecord]) -> dict[str, list[PostRecord]]:
    out: dict[str, list[PostRecord]] = {}
    for p in posts:
        out.setdefault(p.persona_id, []).append(p)
    for v in out.values():
        v.sort(key=lambda r: r.round_idx)
    return out


def _group_decisions(decisions) -> dict[str, list]:
    out: dict[str, list] = {}
    for d in decisions:
        out.setdefault(d.persona_id, []).append(d)
    return out


def _mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return statistics.fmean(vals)


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None
