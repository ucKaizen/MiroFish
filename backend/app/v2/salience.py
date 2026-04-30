"""
Step 3 — salience scorer using OpenAI embeddings.

For each (persona, post) pair we want a cheap, deterministic 0..1 score that
estimates how interested the persona would be in that post. The runner uses
this to decide ``LLMAction`` vs ``ManualAction(DO_NOTHING)`` per round, with
no LLM call required for the gating decision itself.

Score blends three signals:
  1. cosine similarity of post text to persona's interest vector (concat of
     bio + favourite genres + voice examples)
  2. genre-tag boost — direct lookup of ``persona.genre_propensity[post.genre]``
  3. slot-tag boost — same idea, ``persona.slot_propensity[post.slot]``

Embeddings are cached to a SQLite file so re-runs over the same seed cost
nothing. Cache key = (model, sha256(text)).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import openai

from .persona import Persona


logger = logging.getLogger("mirofish.v2.salience")

DEFAULT_MODEL = "text-embedding-3-small"


# ---------- types ----------

@dataclass(frozen=True)
class Post:
    post_id: str
    author: str                      # name string, not a panelist_id
    text: str
    genre: str | None = None
    slot: str | None = None


@dataclass(frozen=True)
class SalienceScore:
    persona_id: str
    post_id: str
    cosine: float                    # 0..1
    genre_boost: float               # 0..1
    slot_boost: float                # 0..1
    total: float                     # 0..1, the value the gate uses


# ---------- public scorer ----------

class SalienceScorer:
    def __init__(self,
                 client: openai.OpenAI | None = None,
                 model: str = DEFAULT_MODEL,
                 cache_path: str | Path | None = None,
                 weights: tuple[float, float, float] = (0.5, 0.35, 0.15)):
        self._model = model
        self._client = client or _make_client()
        cache = Path(cache_path) if cache_path else _default_cache_path()
        self._cache = EmbeddingCache(cache, model)
        self._weights = weights
        self._persona_vectors: dict[str, list[float]] = {}

    def warm_personas(self, personas: Sequence[Persona]) -> None:
        """Pre-compute and cache the interest vector for each persona."""
        texts = {p.panelist_id: _persona_interest_text(p) for p in personas}
        ids_to_embed = list(texts.values())
        embeddings = self._embed_many(ids_to_embed)
        for persona, vec in zip(personas, embeddings):
            self._persona_vectors[persona.panelist_id] = vec

    def score(self, persona: Persona, post: Post) -> SalienceScore:
        if persona.panelist_id not in self._persona_vectors:
            self.warm_personas([persona])
        post_vec = self._embed_many([post.text])[0]
        persona_vec = self._persona_vectors[persona.panelist_id]
        cos = _cosine(persona_vec, post_vec)
        cos01 = (cos + 1.0) / 2.0
        g_boost = persona.genre_propensity.get(post.genre or "", 0.0) if post.genre else 0.0
        s_boost = persona.slot_propensity.get(post.slot or "", 0.0) if post.slot else 0.0
        w_cos, w_g, w_s = self._weights
        total = round(w_cos * cos01 + w_g * g_boost + w_s * s_boost, 4)
        return SalienceScore(
            persona_id=persona.panelist_id,
            post_id=post.post_id,
            cosine=round(cos01, 4),
            genre_boost=round(g_boost, 4),
            slot_boost=round(s_boost, 4),
            total=total,
        )

    def score_many(self, persona: Persona,
                   posts: Iterable[Post]) -> list[SalienceScore]:
        return [self.score(persona, p) for p in posts]

    # ---- internals ----

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float] | None] = [None] * len(texts)
        misses: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                out[i] = cached
            else:
                misses.append((i, text))
        if misses:
            t0 = time.perf_counter()
            resp = self._client.embeddings.create(
                model=self._model,
                input=[text for _, text in misses],
            )
            dt = time.perf_counter() - t0
            logger.info("embedded %d new texts in %.2fs (cache hits: %d/%d)",
                        len(misses), dt, len(texts) - len(misses), len(texts))
            for (i, text), emb in zip(misses, resp.data):
                vec = list(emb.embedding)
                out[i] = vec
                self._cache.put(text, vec)
        return [v for v in out if v is not None]


# ---------- helpers ----------

def _make_client() -> openai.OpenAI:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("OPENAI_API_BASE_URL") or os.environ.get("LLM_BASE_URL")
    if not key:
        raise RuntimeError(
            "no OPENAI_API_KEY / LLM_API_KEY in environment for embeddings"
        )
    return openai.OpenAI(api_key=key, base_url=base_url) if base_url else openai.OpenAI(api_key=key)


def _persona_interest_text(p: Persona) -> str:
    fav_genres = sorted(p.genre_propensity.items(),
                        key=lambda kv: kv[1], reverse=True)[:5]
    fav_slots = sorted(p.slot_propensity.items(),
                       key=lambda kv: kv[1], reverse=True)[:3]
    parts = [
        p.bio,
        "Recorded behaviour: " + p.recorded_behaviour,
        "Top genres: " + ", ".join(f"{g}({prop:.2f})" for g, prop in fav_genres),
        "Top slots: " + ", ".join(f"{s}({prop:.2f})" for s, prop in fav_slots),
    ]
    if p.style.voice_examples:
        parts.append("Voice: " + " | ".join(p.style.voice_examples))
    return "\n".join(parts)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _default_cache_path() -> Path:
    repo = Path(__file__).resolve().parents[2]
    return repo / "uploads" / "embedding_cache.sqlite"


# ---------- cache ----------

class EmbeddingCache:
    """SQLite-backed cache. Single-writer; safe for repeated CLI runs."""

    def __init__(self, path: Path, model: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._model = model
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS embeddings (
                model    TEXT NOT NULL,
                text_sha TEXT NOT NULL,
                vector   TEXT NOT NULL,
                PRIMARY KEY (model, text_sha)
            )"""
        )
        self._conn.commit()

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> list[float] | None:
        sha = self._key(text)
        with self._lock:
            cur = self._conn.execute(
                "SELECT vector FROM embeddings WHERE model = ? AND text_sha = ?",
                (self._model, sha),
            )
            row = cur.fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def put(self, text: str, vector: list[float]) -> None:
        sha = self._key(text)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO embeddings (model, text_sha, vector) "
                "VALUES (?, ?, ?)",
                (self._model, sha, json.dumps(vector)),
            )
            self._conn.commit()

    def stats(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT count(*) FROM embeddings WHERE model = ?", (self._model,))
            return {"rows": int(cur.fetchone()[0]), "model": self._model}
