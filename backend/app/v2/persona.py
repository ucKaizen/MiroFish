"""
Step 2 — deterministic persona projector.

Reads the typed Panelist nodes produced by the v2 loader and emits one
``Persona`` per panelist that downstream stages (salience, runner, narrator)
consume. No LLM. The bio paragraph is rendered from a pure Python template;
the engagement profile is derived directly from the recorded propensities.

A ``Persona`` is an OASIS-shaped surrogate: voice rules, an action menu,
sampling parameters, and per-(genre, slot) propensities. The runner uses it
to decide whether to call the LLM at all and, if so, what system prompt to
build.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .loaders import IdentityNode, Study


# ---------- types ----------

@dataclass(frozen=True)
class EngagementProfile:
    salience_threshold: float
    daily_action_cap: int
    base_rate: float                                  # 0..1
    delayed_exposure_propensity: float
    clarity_sensitivity: str                          # low | medium | high
    rater_bias: float                                 # may shift AI score


@dataclass(frozen=True)
class StyleProfile:
    voice_register: str
    voice_examples: tuple[str, ...]
    vocabulary_required: tuple[str, ...]
    vocabulary_forbidden: tuple[str, ...]
    length_min_chars: int
    length_max_chars: int


@dataclass(frozen=True)
class SamplingConfig:
    temperature: float
    top_p: float
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0


@dataclass(frozen=True)
class Persona:
    panelist_id: str
    name: str
    age: int
    region: str
    occupation: str
    household: str
    bio: str
    style: StyleProfile
    engagement: EngagementProfile
    sampling: SamplingConfig
    available_actions: tuple[str, ...]
    genre_propensity: dict[str, float]
    slot_propensity: dict[str, float]
    recorded_behaviour: str

    def system_prompt(self, brief_synopsis: str | None = None) -> str:
        """Render the persona system prompt used by the LLM persona stage.

        Encodes the no-fork engagement bias explicitly in the prompt so that
        even when the LLM is invoked, it has a strong instruction to prefer
        DO_NOTHING for off-register content.
        """
        return _render_system_prompt(self, brief_synopsis)


@dataclass(frozen=True)
class ProjectionStats:
    persona_count: int
    avg_genre_propensity: float
    avg_slot_propensity: float
    actions_per_persona: dict[str, int]


# ---------- public entry ----------

def project_personas(study: Study) -> tuple[list[Persona], ProjectionStats]:
    """Project every identity node in the study to a Persona."""
    if not study.nodes:
        raise ValueError("study has no identity nodes — nothing to project")

    # Reconstruct per-panelist genre/slot dicts from the typed edges.
    genre_by_pid: dict[str, dict[str, float]] = {}
    slot_by_pid: dict[str, dict[str, float]] = {}
    for e in study.edges:
        if e.edge_type == "PROPENSITY_FOR_GENRE":
            genre_by_pid.setdefault(e.source_key_value, {})[e.target_key_value] = \
                float(e.properties.get("propensity", 0.0))
        elif e.edge_type == "PROPENSITY_FOR_SLOT":
            slot_by_pid.setdefault(e.source_key_value, {})[e.target_key_value] = \
                float(e.properties.get("propensity", 0.0))

    engagement_defaults = study.engagement or {}
    default_threshold = float(engagement_defaults.get("salience_threshold", 0.35))
    default_cap = int(engagement_defaults.get("daily_action_cap", 6))

    personas: list[Persona] = []
    actions_count: dict[str, int] = {}
    for node in study.nodes:
        persona = _project_one(
            node,
            genre_by_pid.get(node.key_value, {}),
            slot_by_pid.get(node.key_value, {}),
            default_threshold,
            default_cap,
        )
        personas.append(persona)
        actions_count[persona.panelist_id] = len(persona.available_actions)

    if not personas:
        raise ValueError("no personas could be projected")

    stats = ProjectionStats(
        persona_count=len(personas),
        avg_genre_propensity=_avg(p.genre_propensity for p in personas),
        avg_slot_propensity=_avg(p.slot_propensity for p in personas),
        actions_per_persona=actions_count,
    )
    return personas, stats


# ---------- internals ----------

# Voice register → sampling defaults. Higher temperature for slangy / loose
# personas; tighter for precise / formal ones. These are the per-agent dials
# we promised in the no-fork plan.
_REGISTER_SAMPLING: dict[str, SamplingConfig] = {
    "precise_focused":      SamplingConfig(temperature=0.55, top_p=0.90),
    "slangy_terse":         SamplingConfig(temperature=1.05, top_p=0.95),
    "articulate_qualified": SamplingConfig(temperature=0.70, top_p=0.92),
    "warm_distracted":      SamplingConfig(temperature=0.85, top_p=0.93),
    "thoughtful_referential": SamplingConfig(temperature=0.70, top_p=0.92),
    "measured_clinical":    SamplingConfig(temperature=0.55, top_p=0.90),
    "precise_formal":       SamplingConfig(temperature=0.50, top_p=0.88),
    "warm_plain":           SamplingConfig(temperature=0.85, top_p=0.93),
    "dry_sceptical":        SamplingConfig(temperature=0.75, top_p=0.92),
    "warm_careful":         SamplingConfig(temperature=0.65, top_p=0.91),
}
_FALLBACK_SAMPLING = SamplingConfig(temperature=0.7, top_p=0.92)


# Map propensity strength → action menu width. Low-engagement personas get a
# narrow menu (DO_NOTHING + LIKE_POST), high-engagement get the full set.
_ACTION_MENU_LURKER       = ("DO_NOTHING", "LIKE_POST")
_ACTION_MENU_LIGHT        = ("DO_NOTHING", "LIKE_POST", "REPOST")
_ACTION_MENU_VOCAL        = ("DO_NOTHING", "LIKE_POST", "REPOST", "CREATE_POST")
_ACTION_MENU_FULL         = ("DO_NOTHING", "LIKE_POST", "REPOST", "CREATE_POST",
                             "CREATE_COMMENT", "QUOTE_POST")


def _project_one(node: IdentityNode,
                 genre_propensity: dict[str, float],
                 slot_propensity: dict[str, float],
                 default_threshold: float,
                 default_cap: int) -> Persona:
    p = node.properties
    a = node.attributes
    voice_examples = tuple(a.get("voice_examples") or ())
    vocab_required = tuple(a.get("vocabulary_required") or ())
    vocab_forbidden = tuple(a.get("vocabulary_forbidden") or ())

    style = StyleProfile(
        voice_register=p["voice_register"],
        voice_examples=voice_examples,
        vocabulary_required=vocab_required,
        vocabulary_forbidden=vocab_forbidden,
        length_min_chars=int(p.get("length_min_chars", 40)),
        length_max_chars=int(p.get("length_max_chars", 200)),
    )

    base_rate = _base_rate(genre_propensity, slot_propensity)
    threshold = _threshold_for(p, base_rate, default_threshold)
    cap = _cap_for(p, base_rate, default_cap)
    actions = _action_menu(base_rate)
    sampling = _REGISTER_SAMPLING.get(p["voice_register"], _FALLBACK_SAMPLING)

    bio = _render_bio(p, base_rate)

    return Persona(
        panelist_id=node.key_value,
        name=p["name"],
        age=int(p["age"]),
        region=p["region"],
        occupation=p["occupation"],
        household=p["household"],
        bio=bio,
        style=style,
        engagement=EngagementProfile(
            salience_threshold=threshold,
            daily_action_cap=cap,
            base_rate=base_rate,
            delayed_exposure_propensity=float(p.get("delayed_exposure_propensity", 0.0)),
            clarity_sensitivity=str(p.get("clarity_sensitivity", "medium")),
            rater_bias=float(p.get("rater_bias", 0.0)),
        ),
        sampling=sampling,
        available_actions=actions,
        genre_propensity=dict(genre_propensity),
        slot_propensity=dict(slot_propensity),
        recorded_behaviour=str(p.get("recorded_behaviour", "")),
    )


def _base_rate(genre_propensity: dict[str, float],
               slot_propensity: dict[str, float]) -> float:
    """A coarse 0..1 measure of how engaged this persona is on average.

    Mean of the persona's mean genre and mean slot propensity. Used to widen
    or narrow the action menu and to nudge the salience threshold.
    """
    if not genre_propensity and not slot_propensity:
        return 0.3
    g = sum(genre_propensity.values()) / max(1, len(genre_propensity))
    s = sum(slot_propensity.values()) / max(1, len(slot_propensity))
    return round((g + s) / 2.0, 4)


def _threshold_for(props: dict[str, Any], base_rate: float,
                   default_threshold: float) -> float:
    """Lower base_rate → *higher* threshold. A lurker's bar to engage is
    higher; a heavy viewer engages more readily.
    """
    bias = (0.5 - base_rate) * 0.4   # ±0.2 swing
    return round(max(0.05, min(0.85, default_threshold + bias)), 4)


def _cap_for(props: dict[str, Any], base_rate: float, default_cap: int) -> int:
    """Lower base_rate → smaller daily action cap. Lurkers post less."""
    if base_rate < 0.2:
        return max(1, default_cap // 2)
    if base_rate > 0.55:
        return default_cap + 2
    return default_cap


def _action_menu(base_rate: float) -> tuple[str, ...]:
    if base_rate < 0.2:
        return _ACTION_MENU_LURKER
    if base_rate < 0.4:
        return _ACTION_MENU_LIGHT
    if base_rate < 0.6:
        return _ACTION_MENU_VOCAL
    return _ACTION_MENU_FULL


def _render_bio(props: dict[str, Any], base_rate: float) -> str:
    age = props["age"]
    name = props["name"]
    occ = props["occupation"]
    region = props["region"]
    house = props["household"]
    register = props["voice_register"].replace("_", " ")
    if base_rate < 0.25:
        engagement_line = (
            "You are not a heavy social-media commenter. You scroll past "
            "most posts. You only react when a post directly intersects "
            "with your interests."
        )
    elif base_rate > 0.55:
        engagement_line = (
            "You are an engaged viewer who happily comments and shares. "
            "You still skip content outside your stated interests."
        )
    else:
        engagement_line = (
            "You are a measured commenter. You react when a post matches "
            "your interests; otherwise you keep scrolling."
        )
    return (
        f"You are {name}, age {age}, from {region}. {occ}. {house}. "
        f"Voice register: {register}. {engagement_line}"
    )


def _render_system_prompt(p: Persona, brief_synopsis: str | None) -> str:
    fav_genres = sorted(p.genre_propensity.items(),
                        key=lambda kv: kv[1], reverse=True)[:3]
    fav_slots = sorted(p.slot_propensity.items(),
                       key=lambda kv: kv[1], reverse=True)[:3]
    style_lines: list[str] = []
    if p.style.vocabulary_required:
        style_lines.append("MUST use words: " + ", ".join(p.style.vocabulary_required))
    if p.style.vocabulary_forbidden:
        style_lines.append("MUST NOT use: " + ", ".join(p.style.vocabulary_forbidden))
    if p.style.voice_examples:
        style_lines.append(
            "Voice examples (mimic length and register, NOT topic): "
            + " | ".join(p.style.voice_examples[:3])
        )
    style_block = "\n".join(f"- {line}" for line in style_lines) or "- (no specific style constraints)"

    brief_block = brief_synopsis.strip() if brief_synopsis else "(no brief)"

    actions_list = ", ".join(p.available_actions)

    return f"""You are simulating a single panel viewer reacting on social media.

# Identity
{p.bio}

Recorded prior behaviour:
{p.recorded_behaviour}

# Style rules
{style_block}
- Output {p.style.length_min_chars}-{p.style.length_max_chars} characters. Stay in voice register: {p.style.voice_register.replace('_', ' ')}.

# Engagement rules — IMPORTANT
You watched the following content:

{brief_block}

Your top interests by genre:
{_format_props(fav_genres)}
Your top viewing slots:
{_format_props(fav_slots)}

If a post you see is OUTSIDE these interests, your action MUST be DO_NOTHING.
Real viewers ignore most content; do not engage just because you can.

# Action menu
{actions_list}

Pick exactly ONE action. If unsure, prefer DO_NOTHING.
"""


def _format_props(pairs: list[tuple[str, float]]) -> str:
    return "\n".join(f"  - {k}: {v:.2f}" for k, v in pairs) or "  - (none)"


def _avg(maps) -> float:
    flat: list[float] = []
    for m in maps:
        if isinstance(m, dict):
            flat.extend(m.values())
    if not flat:
        return 0.0
    return round(sum(flat) / len(flat), 4)
