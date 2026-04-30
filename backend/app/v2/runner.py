"""
Steps 4 + 5 — orchestrator engagement gate + per-agent sampling.

This runner is a self-contained, no-OASIS-dependency simulation loop. It
implements the same contract OASIS expects: a per-(agent, round) action map
where each entry is either ``LLMAction`` (call the LLM) or
``ManualAction(DO_NOTHING)`` (skip without burning tokens).

Why a local runner: OASIS pulls torch and runs camel-ai, which won't install
on this Intel Mac (no x86_64 macOS torch wheel for v2.9.x). The runner here
exposes the same shape so the orchestration logic is exercised end-to-end
locally, and the OASIS-backed runner on Railway is a 50-LOC adapter that
swaps the LLM call for ``OasisEnv.step()``.

Two rounds of activity per default:
  - Round 1: exposure — every persona is shown the brief synopsis. Engagement
    gate decides who actually reacts.
  - Round 2: social diffusion — each persona sees the top-N round-1 posts
    that aren't their own; per-post salience determines whether they engage.

The trace log is the studyable artefact: every gate decision is recorded
with reason and salience score so you can replay and tune thresholds.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

_UTC = timezone.utc
from pathlib import Path
from typing import Any, Iterable, Sequence

import openai

from .loaders import Brief, Study
from .persona import Persona
from .salience import Post, SalienceScore, SalienceScorer


logger = logging.getLogger("mirofish.v2.runner")


# ---------- types ----------

@dataclass(frozen=True)
class GateDecision:
    persona_id: str
    round_idx: int
    post_id: str | None              # None for the round-1 exposure call
    salience: SalienceScore | None
    decision: str                    # "engage" | "skip"
    reason: str                      # short tag, e.g. "below_threshold"


@dataclass(frozen=True)
class PostRecord:
    post_id: str
    persona_id: str
    persona_name: str
    round_idx: int
    action: str                      # CREATE_POST | LIKE_POST | REPOST | ...
    text: str
    parent_post_id: str | None
    salience: float | None
    timestamp: str


@dataclass
class RunResult:
    study_id: str
    brief_id: str
    started_at: str
    finished_at: str
    rounds: int
    posts: list[PostRecord]
    decisions: list[GateDecision]
    persona_ids: list[str]
    llm_calls: int                     # calls actually placed (incl. DO_NOTHING returns)
    posts_created: int                 # subset that produced a post record
    cache_stats: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_id":     self.study_id,
            "brief_id":     self.brief_id,
            "started_at":   self.started_at,
            "finished_at":  self.finished_at,
            "rounds":       self.rounds,
            "posts":        [asdict(p) for p in self.posts],
            "decisions":    [_decision_dict(d) for d in self.decisions],
            "persona_ids":  list(self.persona_ids),
            "llm_calls":    self.llm_calls,
            "posts_created": self.posts_created,
            "cache_stats":  self.cache_stats,
        }

    def write_jsonl(self, dir_path: str | Path) -> tuple[Path, Path]:
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        posts_path = dir_path / "posts.jsonl"
        trace_path = dir_path / "trace.jsonl"
        with posts_path.open("w", encoding="utf-8") as f:
            for p in self.posts:
                f.write(json.dumps(asdict(p)) + "\n")
        with trace_path.open("w", encoding="utf-8") as f:
            for d in self.decisions:
                f.write(json.dumps(_decision_dict(d)) + "\n")
        return posts_path, trace_path


# ---------- runner ----------

class MiniRunner:
    """OASIS-shaped local runner. No torch, no OASIS install needed."""

    def __init__(self,
                 client: openai.OpenAI | None = None,
                 model: str | None = None,
                 max_diffusion_posts_per_agent: int = 6):
        self._client = client or _make_client()
        self._model = model or os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
        self._max_diffusion_posts = max_diffusion_posts_per_agent

    def run(self,
            study: Study,
            personas: Sequence[Persona],
            scorer: SalienceScorer,
            *,
            rounds: int = 2,
            run_id: str | None = None) -> RunResult:
        if rounds < 1:
            raise ValueError("rounds must be >= 1")
        run_id = run_id or f"run_{int(time.time())}"
        started = _now_iso()
        scorer.warm_personas(personas)

        brief = study.brief
        brief_post = _brief_to_post(brief)

        all_posts: list[PostRecord] = []
        all_decisions: list[GateDecision] = []
        action_counts: dict[str, int] = {p.panelist_id: 0 for p in personas}
        llm_calls = 0
        posts_created = 0

        # ----- round 1: direct exposure -----
        round_idx = 1
        round1_posts: list[PostRecord] = []
        for persona in personas:
            score = scorer.score(persona, brief_post)
            decision, reason = _gate(persona, score, action_counts)
            all_decisions.append(GateDecision(
                persona_id=persona.panelist_id,
                round_idx=round_idx,
                post_id=brief_post.post_id,
                salience=score,
                decision=decision,
                reason=reason,
            ))
            if decision == "engage":
                action_counts[persona.panelist_id] += 1
                llm_calls += 1
                rec = self._llm_react(
                    persona=persona,
                    parent=brief_post,
                    round_idx=round_idx,
                    salience_total=score.total,
                    brief_synopsis=brief.synopsis or "",
                )
                if rec is not None:
                    round1_posts.append(rec)
                    all_posts.append(rec)
                    posts_created += 1

        if rounds == 1:
            cache_stats = scorer._cache.stats()
            return RunResult(
                study_id=study.study_id,
                brief_id=brief.content_id,
                started_at=started,
                finished_at=_now_iso(),
                rounds=1,
                posts=all_posts,
                decisions=all_decisions,
                persona_ids=[p.panelist_id for p in personas],
                llm_calls=llm_calls,
                posts_created=posts_created,
                cache_stats=cache_stats,
            )

        # ----- rounds 2..N: diffusion -----
        feed_pool = round1_posts[:]
        for round_idx in range(2, rounds + 1):
            round_n_posts: list[PostRecord] = []
            for persona in personas:
                # Filter out the persona's own posts and rank the feed by
                # salience for this persona; only top-N are shown.
                visible = [r for r in feed_pool if r.persona_id != persona.panelist_id]
                if not visible:
                    continue
                visible_scores = [
                    scorer.score(persona, _post_record_to_post(r, brief))
                    for r in visible
                ]
                ranked = sorted(
                    zip(visible, visible_scores),
                    key=lambda pair: pair[1].total,
                    reverse=True,
                )[:self._max_diffusion_posts]
                for parent_rec, score in ranked:
                    decision, reason = _gate(persona, score, action_counts)
                    all_decisions.append(GateDecision(
                        persona_id=persona.panelist_id,
                        round_idx=round_idx,
                        post_id=parent_rec.post_id,
                        salience=score,
                        decision=decision,
                        reason=reason,
                    ))
                    if decision == "engage":
                        action_counts[persona.panelist_id] += 1
                        llm_calls += 1
                        rec = self._llm_react(
                            persona=persona,
                            parent=_post_record_to_post(parent_rec, brief),
                            round_idx=round_idx,
                            salience_total=score.total,
                            brief_synopsis=brief.synopsis or "",
                        )
                        if rec is not None:
                            round_n_posts.append(rec)
                            all_posts.append(rec)
                            posts_created += 1
            feed_pool.extend(round_n_posts)

        return RunResult(
            study_id=study.study_id,
            brief_id=brief.content_id,
            started_at=started,
            finished_at=_now_iso(),
            rounds=rounds,
            posts=all_posts,
            decisions=all_decisions,
            persona_ids=[p.panelist_id for p in personas],
            llm_calls=llm_calls,
            posts_created=posts_created,
            cache_stats=scorer._cache.stats(),
        )

    # ---- internals ----

    def _llm_react(self, *, persona: Persona, parent: Post, round_idx: int,
                   salience_total: float, brief_synopsis: str
                   ) -> PostRecord | None:
        """Call the LLM with the persona's system prompt + a structured
        action menu. Returns None if the LLM picks DO_NOTHING."""
        sys_prompt = persona.system_prompt(brief_synopsis=brief_synopsis)
        action_menu = list(persona.available_actions)

        if round_idx == 1:
            user_prompt = (
                f"You just watched the following content. Produce ONE social-"
                f"media reaction. Pick exactly ONE action from this menu:\n"
                f"  {action_menu}\n\n"
                f"Brief:\n{parent.text}\n\n"
                f"Reply as JSON: {{\"action\": \"<one of menu>\", "
                f"\"text\": \"<your post or empty if DO_NOTHING>\"}}"
            )
        else:
            user_prompt = (
                f"You opened your social feed. You see this post:\n\n"
                f"  by {parent.author}: {parent.text}\n\n"
                f"Pick exactly ONE action from: {action_menu}\n"
                f"If the post does not match your interests, prefer DO_NOTHING.\n"
                f"Reply as JSON: {{\"action\": \"<one of menu>\", "
                f"\"text\": \"<your reply or empty>\"}}"
            )

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=persona.sampling.temperature,
                top_p=persona.sampling.top_p,
                presence_penalty=persona.sampling.presence_penalty,
                frequency_penalty=persona.sampling.frequency_penalty,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=400,
            )
        except Exception as e:                          # pragma: no cover
            logger.error("LLM call failed for %s: %s", persona.panelist_id, e)
            return None

        try:
            content = resp.choices[0].message.content or "{}"
            parsed = json.loads(content)
        except Exception as e:                          # pragma: no cover
            logger.warning("could not parse LLM JSON for %s: %s",
                           persona.panelist_id, e)
            return None

        action = (parsed.get("action") or "DO_NOTHING").strip().upper()
        text = (parsed.get("text") or "").strip()
        if action == "DO_NOTHING" or not text:
            return None

        if action not in action_menu:
            # The LLM picked an action we did not allow; coerce to DO_NOTHING.
            logger.info("persona %s picked unauthorised action %r; coerced",
                        persona.panelist_id, action)
            return None

        post_id = f"p_{persona.panelist_id}_r{round_idx}_{int(time.time()*1000)%100000}"
        return PostRecord(
            post_id=post_id,
            persona_id=persona.panelist_id,
            persona_name=persona.name,
            round_idx=round_idx,
            action=action,
            text=text,
            parent_post_id=parent.post_id,
            salience=salience_total,
            timestamp=_now_iso(),
        )


# ---------- engagement gate ----------

def _gate(persona: Persona,
          score: SalienceScore,
          action_counts: dict[str, int]) -> tuple[str, str]:
    """Return ("engage" | "skip", reason). Pure function; testable in
    isolation. The reason string is the studyable trace tag."""
    if action_counts.get(persona.panelist_id, 0) >= persona.engagement.daily_action_cap:
        return "skip", "daily_cap_reached"
    if score.total < persona.engagement.salience_threshold:
        return "skip", "below_threshold"
    return "engage", "passed_gate"


# ---------- helpers ----------

def _brief_to_post(brief: Brief) -> Post:
    parts = [brief.title]
    if brief.synopsis:
        parts.append(brief.synopsis)
    return Post(
        post_id=f"brief:{brief.content_id}",
        author=brief.title,
        text="\n".join(parts),
        genre=brief.genre,
        slot=brief.slot,
    )


def _post_record_to_post(rec: PostRecord, brief: Brief) -> Post:
    return Post(
        post_id=rec.post_id,
        author=rec.persona_name,
        text=rec.text,
        genre=brief.genre,
        slot=brief.slot,
    )


def _make_client() -> openai.OpenAI:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("OPENAI_API_BASE_URL") or os.environ.get("LLM_BASE_URL")
    if not key:
        raise RuntimeError(
            "no OPENAI_API_KEY / LLM_API_KEY in environment for runner"
        )
    return openai.OpenAI(api_key=key, base_url=base_url) if base_url else openai.OpenAI(api_key=key)


def _now_iso() -> str:
    return datetime.now(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decision_dict(d: GateDecision) -> dict[str, Any]:
    out = {
        "persona_id": d.persona_id,
        "round_idx":  d.round_idx,
        "post_id":    d.post_id,
        "decision":   d.decision,
        "reason":     d.reason,
    }
    if d.salience is not None:
        out["salience"] = asdict(d.salience)
    return out
