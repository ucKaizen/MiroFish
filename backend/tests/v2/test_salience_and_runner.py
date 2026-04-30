"""Salience scorer + runner gate tests with stub OpenAI clients.

We exercise the pure logic without going to the network. Embeddings are
faked (high cosine for matching topics, low cosine otherwise) and chat
completions are scripted via a stub.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.v2.loaders import load_study
from app.v2.persona import Persona, project_personas
from app.v2.runner import GateDecision, MiniRunner, _gate
from app.v2.salience import EmbeddingCache, Post, SalienceScore, SalienceScorer


REPO_ROOT = Path(__file__).resolve().parents[3]
BBC_PANEL = REPO_ROOT / "backend" / "seeds" / "bbc_panel" / "study.json"


# ---------- stubs ----------

@dataclass
class _StubEmbedding:
    embedding: list[float]


@dataclass
class _StubEmbeddingResponse:
    data: list[_StubEmbedding]


class _StubEmbeddingsClient:
    """Returns deterministic embeddings keyed by simple keyword match."""

    def __init__(self):
        self.embeddings = self
        self.calls = 0

    def create(self, *, model: str, input: list[str]) -> _StubEmbeddingResponse:
        self.calls += 1
        out: list[_StubEmbedding] = []
        for text in input:
            t = text.lower()
            vec = [
                1.0 if "political" in t else 0.0,
                1.0 if "drama"     in t else 0.0,
                1.0 if "sport"     in t else 0.0,
                1.0 if "comedy"    in t else 0.0,
                1.0 if "iplayer"   in t else 0.0,
                1.0 if "documentary" in t else 0.0,
            ]
            out.append(_StubEmbedding(embedding=vec))
        return _StubEmbeddingResponse(data=out)


class _StubChatClient:
    """Returns canned JSON action responses."""

    def __init__(self, scripted: list[str]):
        self.scripted = list(scripted)
        self.chat = self
        self.completions = self
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        content = self.scripted[idx % len(self.scripted)]

        @dataclass
        class _Msg:
            content: str

        @dataclass
        class _Choice:
            message: _Msg

        @dataclass
        class _Resp:
            choices: list[_Choice]

        return _Resp(choices=[_Choice(message=_Msg(content=content))])


# ---------- gate ----------

def test_gate_skips_when_below_threshold():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    aisha = next(p for p in personas if p.panelist_id == "aisha")
    score = SalienceScore("aisha", "p1", 0.1, 0.1, 0.1, total=0.05)
    decision, reason = _gate(aisha, score, action_counts={"aisha": 0})
    assert decision == "skip"
    assert reason == "below_threshold"


def test_gate_skips_when_capped():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    geoffrey = next(p for p in personas if p.panelist_id == "geoffrey")
    score = SalienceScore("geoffrey", "p1", 1.0, 1.0, 1.0, total=0.95)
    counts = {"geoffrey": geoffrey.engagement.daily_action_cap}
    decision, reason = _gate(geoffrey, score, counts)
    assert decision == "skip"
    assert reason == "daily_cap_reached"


def test_gate_engages_above_threshold_under_cap():
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    geoffrey = next(p for p in personas if p.panelist_id == "geoffrey")
    score = SalienceScore("geoffrey", "p1", 0.9, 0.9, 0.9, total=0.9)
    decision, reason = _gate(geoffrey, score, action_counts={"geoffrey": 0})
    assert decision == "engage"
    assert reason == "passed_gate"


# ---------- salience ----------

def test_salience_scorer_caches_embeddings(tmp_path: Path):
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    stub = _StubEmbeddingsClient()
    scorer = SalienceScorer(client=stub, cache_path=tmp_path / "cache.sqlite")
    scorer.warm_personas(personas)
    n_calls_first = stub.calls
    # Re-warm with same personas → all embeddings cached → 0 new calls.
    scorer2 = SalienceScorer(client=stub, cache_path=tmp_path / "cache.sqlite")
    scorer2.warm_personas(personas)
    assert stub.calls == n_calls_first  # cache hit only


def test_salience_blends_cosine_genre_slot(tmp_path: Path):
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    marcus = next(p for p in personas if p.panelist_id == "marcus")
    stub = _StubEmbeddingsClient()
    scorer = SalienceScorer(client=stub, cache_path=tmp_path / "cache.sqlite")
    scorer.warm_personas([marcus])

    on_topic = Post(post_id="p1", author="t", text="political drama on iplayer",
                    genre="political_drama", slot="sunday_2100_bbc1")
    off_topic = Post(post_id="p2", author="t", text="reality TV sport comedy",
                     genre="reality_tv", slot="late_night")
    s_on = scorer.score(marcus, on_topic)
    s_off = scorer.score(marcus, off_topic)
    assert s_on.total > s_off.total
    # On-topic post should clear marcus's threshold; off-topic should not.
    assert s_on.total > marcus.engagement.salience_threshold


def test_embedding_cache_round_trip(tmp_path: Path):
    cache = EmbeddingCache(tmp_path / "c.sqlite", "test-model")
    assert cache.get("hello") is None
    cache.put("hello", [0.1, 0.2, 0.3])
    assert cache.get("hello") == [0.1, 0.2, 0.3]
    assert cache.stats() == {"rows": 1, "model": "test-model"}


# ---------- runner (with stubs) ----------

def test_runner_with_stubs_round1_only(tmp_path: Path):
    """End-to-end runner with no real network. Runs round 1 against the
    BBC panel and verifies that low-engagement personas are skipped at the
    gate while high-engagement personas reach the LLM call."""
    study = load_study(BBC_PANEL)
    personas, _ = project_personas(study)
    embed = _StubEmbeddingsClient()
    scorer = SalienceScorer(client=embed, cache_path=tmp_path / "cache.sqlite")
    chat = _StubChatClient(scripted=[
        '{"action": "CREATE_POST", "text": "Strong slow-burn writing."}',
        '{"action": "DO_NOTHING", "text": ""}',
        '{"action": "CREATE_COMMENT", "text": "Took my breath."}',
    ])
    runner = MiniRunner(client=chat, model="stub-model")
    result = runner.run(study, personas, scorer, rounds=1)

    assert result.rounds == 1
    assert len(result.persona_ids) == 10
    # Some personas must be skipped at the gate (their salience lands below
    # threshold for the brief). Otherwise the engagement gate is broken.
    skips = [d for d in result.decisions if d.decision == "skip"]
    engages = [d for d in result.decisions if d.decision == "engage"]
    assert len(skips) > 0, "no one skipped — engagement gate is not biting"
    assert len(engages) > 0, "no one engaged — gate is too tight"
    # LLM is called exactly once per engaged decision in round 1.
    assert result.llm_calls == len(engages)
