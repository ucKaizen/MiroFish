"""
Drop-in replacement for the zep_cloud SDK.

Exposes the subset of the Zep Cloud API surface that MiroFish uses, backed
by a self-hosted Graphiti + Neo4j stack. Every Zep method call is bridged
to Graphiti via a thread-local event loop so Flask's threaded sync
handlers stay unchanged.

Imports in the rest of the codebase change from:
    from zep_cloud.client import Zep
    from zep_cloud import EpisodeData, EntityEdgeSourceTarget, InternalServerError
    from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

to the module-level names exported here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from pydantic import BaseModel

import os as _os

from ..config import Config

# Graphiti's OpenAIRerankerClient and internal fallbacks instantiate
# AsyncOpenAI(api_key=config.api_key) — if api_key is None they crash before
# we can inject our own client. Mirror LLM_API_KEY into OPENAI_API_KEY so
# every Graphiti subsystem sees a usable key. Must happen before Graphiti
# is imported.
if Config.LLM_API_KEY and not _os.environ.get("OPENAI_API_KEY"):
    _os.environ["OPENAI_API_KEY"] = Config.LLM_API_KEY

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.llm_client import LLMConfig, OpenAIClient
from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig


class _SafeOpenAIEmbedder(OpenAIEmbedder):
    """Wraps OpenAIEmbedder to short-circuit calls with empty input arrays.

    Graphiti 0.11.6 occasionally calls the embedder with an empty list when
    a dedup pass leaves nothing new to embed. OpenAI's embeddings endpoint
    rejects this with HTTP 400 "Invalid 'input': input cannot be an empty
    array.", which crashes the entire graph build. Returning an empty list
    in that case is the correct semantic — there's nothing to embed."""

    async def create(self, input_data, *args, **kwargs):
        # input_data may be a string or list[str]. Empty list / empty string -> empty result.
        if input_data is None:
            return []
        if isinstance(input_data, list) and len(input_data) == 0:
            return []
        if isinstance(input_data, str) and not input_data.strip():
            return []
        return await super().create(input_data, *args, **kwargs)

    async def create_batch(self, input_data_list, *args, **kwargs):
        if not input_data_list:
            return []
        # Filter out empty strings before forwarding
        cleaned = [s for s in input_data_list if s and (not isinstance(s, str) or s.strip())]
        if not cleaned:
            return []
        return await super().create_batch(cleaned, *args, **kwargs)

logger = logging.getLogger("mirofish.zep_compat")

# ---------------------------------------------------------------------------
# Shim types so `from zep_compat import EpisodeData, InternalServerError, ...`
# works identically to the old zep_cloud imports.
# ---------------------------------------------------------------------------


@dataclass
class EpisodeData:
    """Replaces zep_cloud.EpisodeData — the minimal shape MiroFish constructs."""
    data: str
    type: str = "text"


@dataclass
class EntityEdgeSourceTarget:
    """Replaces zep_cloud.EntityEdgeSourceTarget — a (source, target) pair used
    to describe which entity types a given edge type may connect."""
    source: str
    target: str


class InternalServerError(Exception):
    """Replaces zep_cloud.InternalServerError. Raised by the adapter when
    Graphiti itself raises an unexpected error; Neo4j-native errors bubble
    through unwrapped."""


# Ontology-module shims — MiroFish subclasses these to describe its ontology.
class EntityModel(BaseModel):
    """Base class for entity-type definitions. Identical role to
    zep_cloud.external_clients.ontology.EntityModel but backed by Graphiti,
    which accepts any pydantic BaseModel as an entity type."""


class EdgeModel(BaseModel):
    """Base class for edge-type definitions."""


# Zep used `EntityText` as a typing marker; str works for Graphiti.
EntityText = str


# ---------------------------------------------------------------------------
# Per-thread Graphiti instance + asyncio event loop.
#
# Graphiti is async-only and caches connection state on the loop that created
# it. Flask runs request handlers in threads, and MiroFish also spawns
# background threads for long-running builds. To keep things simple and safe
# we give each thread its own Graphiti client and its own event loop, created
# lazily on first use.
# ---------------------------------------------------------------------------


_thread_local = threading.local()


def _get_graphiti() -> Tuple[Graphiti, asyncio.AbstractEventLoop]:
    """Return this thread's Graphiti client + event loop, creating both on
    first call."""
    if not hasattr(_thread_local, "loop"):
        _thread_local.loop = asyncio.new_event_loop()
    if not hasattr(_thread_local, "graphiti"):
        # Build LLM and embedder clients using MiroFish's LLM_* env vars so
        # Graphiti doesn't demand its own OPENAI_API_KEY environment variable
        # and so the same provider (OpenAI / Azure OpenAI / Qwen via OpenAI
        # SDK) powers entity extraction as powers the rest of the app.
        llm_conf = LLMConfig(
            api_key=Config.LLM_API_KEY,
            base_url=Config.LLM_BASE_URL,
            model=Config.LLM_MODEL_NAME,
        )
        embedder_conf = OpenAIEmbedderConfig(
            api_key=Config.LLM_API_KEY,
            base_url=Config.LLM_BASE_URL,
            embedding_model="text-embedding-3-small",
        )
        _thread_local.graphiti = Graphiti(
            uri=Config.NEO4J_URI,
            user=Config.NEO4J_USER,
            password=Config.NEO4J_PASSWORD,
            llm_client=OpenAIClient(config=llm_conf),
            # Use the safe wrapper so dedup passes that yield empty
            # batches don't crash the entire graph build.
            embedder=_SafeOpenAIEmbedder(config=embedder_conf),
        )
        # Build indices once. Safe to call repeatedly; subsequent threads
        # hit "equivalent schema rule already exists" which we can ignore.
        try:
            _thread_local.loop.run_until_complete(
                _thread_local.graphiti.build_indices_and_constraints()
            )
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.debug("Indices already exist, skipping: %s", e)
            else:
                raise
        logger.info(
            "Graphiti client initialised on thread %s (uri=%s)",
            threading.current_thread().name,
            Config.NEO4J_URI,
        )
    return _thread_local.graphiti, _thread_local.loop


T = TypeVar("T")


def _run_async(coro) -> Any:
    """Run an async Graphiti coroutine from sync code using this thread's
    dedicated event loop."""
    _, loop = _get_graphiti()
    return loop.run_until_complete(coro)


def _json_safe(value: Any) -> Any:
    """Recursively coerce Neo4j return values into JSON-serialisable types.
    The neo4j driver returns DateTime / Date / Time / Duration objects for
    temporal properties, and Flask's JSON encoder rejects them. Coerce
    anything non-primitive to its string representation; the API layer only
    needs the values for display."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    # Neo4j temporal types have iso_format(); fall back to str() for the rest.
    iso = getattr(value, "iso_format", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            pass
    return str(value)


def _cypher(query: str, **params: Any) -> List[Dict[str, Any]]:
    """Execute a Cypher query and return a plain list of dict records with
    all values coerced to JSON-safe types. AsyncDriver.execute_query returns
    EagerResult(records, summary, keys); this unwraps it and normalises
    record access so downstream code can use `r["key"]` / `r.get("key")`."""
    graphiti, _ = _get_graphiti()
    result = _run_async(graphiti.driver.execute_query(query, **params))
    records = getattr(result, "records", None)
    if records is None and isinstance(result, tuple):
        # Some driver versions return a (records, summary, keys) tuple
        records = result[0]
    records = records or []
    return [{k: _json_safe(v) for k, v in dict(r).items()} for r in records]


# ---------------------------------------------------------------------------
# Lightweight result objects returned to callers. We deliberately mirror the
# attribute names MiroFish reads from Zep responses (e.g. `.uuid_`,
# `.processed`, `.name`, `.summary`, `.attributes`, `.labels`).
# ---------------------------------------------------------------------------


@dataclass
class _EpisodeResult:
    uuid_: str
    processed: bool = True


@dataclass
class _NodeResult:
    uuid_: str
    name: str
    summary: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    labels: List[str] = field(default_factory=list)
    group_id: Optional[str] = None

    # Zep returns nodes sometimes with .uuid (no trailing underscore) too
    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class _EdgeResult:
    uuid_: str
    name: str
    fact: str = ""
    source_node_uuid: Optional[str] = None
    target_node_uuid: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    group_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Namespace classes — these mirror the Zep SDK's `client.graph.node.*`,
# `client.graph.edge.*`, `client.graph.episode.*` structure.
# ---------------------------------------------------------------------------


class _NodeNS:
    def get(self, uuid_: str) -> Optional[_NodeResult]:
        """Return a single node by UUID — backed by a direct Cypher query."""
        query = (
            "MATCH (n) WHERE n.uuid = $uuid "
            "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, "
            "labels(n) AS labels, properties(n) AS props, n.group_id AS group_id "
            "LIMIT 1"
        )
        records = _cypher(query, uuid=uuid_)
        for r in records:
            props = dict(r.get("props") or {})
            # Everything except the core fields goes into attributes
            for k in ("uuid", "name", "summary", "group_id", "name_embedding", "created_at"):
                props.pop(k, None)
            return _NodeResult(
                uuid_=r["uuid"],
                name=r.get("name") or "",
                summary=r.get("summary") or "",
                attributes=props,
                labels=list(r.get("labels") or []),
                group_id=r.get("group_id"),
            )
        return None

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: Optional[int] = None,
        uuid_cursor: Optional[str] = None,
        **_: Any,
    ) -> List[_NodeResult]:
        """All nodes in a given group (Zep calls this graph_id). The `limit`
        and `uuid_cursor` kwargs come from zep_paging's pagination loop;
        Graphiti returns all nodes in one shot so we use limit as a hard cap
        and return an empty list on subsequent cursor pages to terminate the
        loop cleanly."""
        # After the first page, pagination would use uuid_cursor; we already
        # returned everything on page 1, so signal "no more" by returning [].
        if uuid_cursor is not None:
            return []
        query = (
            "MATCH (n) WHERE n.group_id = $gid "
            "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, "
            "labels(n) AS labels, properties(n) AS props, n.group_id AS group_id"
        )
        if limit:
            query += f" LIMIT {int(limit) * 10}"  # generous cap — zep_paging stops itself
        records = _cypher(query, gid=graph_id)
        results: List[_NodeResult] = []
        for r in records:
            props = dict(r.get("props") or {})
            for k in ("uuid", "name", "summary", "group_id", "name_embedding", "created_at"):
                props.pop(k, None)
            results.append(_NodeResult(
                uuid_=r["uuid"],
                name=r.get("name") or "",
                summary=r.get("summary") or "",
                attributes=props,
                labels=list(r.get("labels") or []),
                group_id=r.get("group_id"),
            ))
        return results

    def get_entity_edges(self, node_uuid: str) -> List[_EdgeResult]:
        """All edges adjacent to a node."""
        query = (
            "MATCH (n)-[r]-(m) WHERE n.uuid = $uuid "
            "RETURN r.uuid AS uuid, type(r) AS name, r.fact AS fact, "
            "startNode(r).uuid AS source, endNode(r).uuid AS target, "
            "properties(r) AS props, r.group_id AS group_id"
        )
        records = _cypher(query, uuid=node_uuid)
        results: List[_EdgeResult] = []
        for r in records:
            props = dict(r.get("props") or {})
            for k in ("uuid", "fact", "group_id", "created_at"):
                props.pop(k, None)
            results.append(_EdgeResult(
                uuid_=r["uuid"],
                name=r.get("name") or "",
                fact=r.get("fact") or "",
                source_node_uuid=r.get("source"),
                target_node_uuid=r.get("target"),
                attributes=props,
                group_id=r.get("group_id"),
            ))
        return results


class _EdgeNS:
    def get_by_graph_id(
        self,
        graph_id: str,
        limit: Optional[int] = None,
        uuid_cursor: Optional[str] = None,
        **_: Any,
    ) -> List[_EdgeResult]:
        if uuid_cursor is not None:
            return []
        query = (
            "MATCH ()-[r]->() WHERE r.group_id = $gid "
            "RETURN r.uuid AS uuid, type(r) AS name, r.fact AS fact, "
            "startNode(r).uuid AS source, endNode(r).uuid AS target, "
            "properties(r) AS props, r.group_id AS group_id"
        )
        if limit:
            query += f" LIMIT {int(limit) * 10}"
        records = _cypher(query, gid=graph_id)
        results: List[_EdgeResult] = []
        for r in records:
            props = dict(r.get("props") or {})
            for k in ("uuid", "fact", "group_id", "created_at"):
                props.pop(k, None)
            results.append(_EdgeResult(
                uuid_=r["uuid"],
                name=r.get("name") or "",
                fact=r.get("fact") or "",
                source_node_uuid=r.get("source"),
                target_node_uuid=r.get("target"),
                attributes=props,
                group_id=r.get("group_id"),
            ))
        return results


class _EpisodeNS:
    def get(self, uuid_: str) -> _EpisodeResult:
        """Return an episode by UUID. Graphiti processes episodes
        synchronously inside add_episode, so by the time the caller has the
        uuid the episode is already fully processed — we can return
        processed=True unconditionally."""
        return _EpisodeResult(uuid_=uuid_, processed=True)


class _GraphNS:
    """The `client.graph.*` namespace in Zep — the main surface MiroFish
    uses."""

    def __init__(self) -> None:
        self.node = _NodeNS()
        self.edge = _EdgeNS()
        self.episode = _EpisodeNS()
        # Ontology is stored across calls because Zep's set_ontology is a
        # one-shot operation but Graphiti takes entity/edge types per
        # add_episode call.
        self._entity_types: Dict[str, type] = {}
        self._edge_types: Dict[str, type] = {}
        self._edge_type_map: Dict[Tuple[str, str], List[str]] = {}

    # ---- Graph lifecycle ----
    def create(self, graph_id: str, name: str = "", description: str = "") -> None:
        """In Zep this explicitly creates a named graph. Graphiti creates
        groups implicitly on first episode add, so this is a no-op — we just
        log the intent so the caller's progress reporting still makes sense."""
        logger.info("graph.create no-op (graphiti creates groups on first episode): %s", graph_id)

    def delete(self, graph_id: str) -> None:
        """Remove every node and edge in a Graphiti group via Cypher."""
        _cypher(
            "MATCH (n) WHERE n.group_id = $gid DETACH DELETE n",
            gid=graph_id,
        )
        logger.info("graph.delete removed group %s", graph_id)

    # ---- Ontology ----
    def set_ontology(
        self,
        graph_ids: Optional[List[str]] = None,
        entities: Optional[Dict[str, type]] = None,
        edges: Optional[Dict[str, Tuple[type, List[EntityEdgeSourceTarget]]]] = None,
    ) -> None:
        """Remember the ontology for later add_batch calls. Graphiti accepts
        entity/edge types per-episode rather than as a persistent graph
        setting, so we stash them here."""
        self._entity_types = entities or {}
        edge_types: Dict[str, type] = {}
        edge_type_map: Dict[Tuple[str, str], List[str]] = {}
        for edge_name, value in (edges or {}).items():
            # Zep's shape: (edge_class, [EntityEdgeSourceTarget, ...])
            edge_class, source_targets = value
            edge_types[edge_name] = edge_class
            for st in source_targets:
                key = (st.source, st.target)
                edge_type_map.setdefault(key, []).append(edge_name)
        self._edge_types = edge_types
        self._edge_type_map = edge_type_map
        logger.info(
            "set_ontology: %d entity types, %d edge types, graph_ids=%s",
            len(self._entity_types),
            len(self._edge_types),
            graph_ids,
        )

    # ---- Content ingest ----
    def add_batch(
        self,
        graph_id: str,
        episodes: List[EpisodeData],
    ) -> List[_EpisodeResult]:
        """Push a list of episodes to the graph. Iterates add_episode under
        the hood. Returns a list of _EpisodeResult so callers can read .uuid_.
        """
        graphiti, _ = _get_graphiti()
        results: List[_EpisodeResult] = []

        async def _do():
            for ep in episodes:
                now = datetime.now(timezone.utc)
                name = f"episode_{_uuid.uuid4().hex[:8]}"
                # graphiti-core 0.11.x: only entity_types is supported.
                # Edge-type schemas are auto-inferred — the ontology's edge
                # type list is not enforced.
                result = await graphiti.add_episode(
                    name=name,
                    episode_body=ep.data,
                    source=EpisodeType.text,
                    reference_time=now,
                    source_description="MiroFish seed",
                    group_id=graph_id,
                    entity_types=self._entity_types or None,
                )
                ep_uuid = getattr(result.episode, "uuid", None) or getattr(result.episode, "uuid_", None)
                if ep_uuid:
                    results.append(_EpisodeResult(uuid_=ep_uuid, processed=True))
            return results

        return _run_async(_do())

    def add(
        self,
        group_id: Optional[str] = None,
        graph_id: Optional[str] = None,
        data: Optional[str] = None,
        type: str = "text",
        **kwargs: Any,
    ) -> _EpisodeResult:
        """Single-episode add used by zep_graph_memory_updater during sim."""
        graphiti, _ = _get_graphiti()
        group = group_id or graph_id
        now = datetime.now(timezone.utc)
        name = f"episode_{_uuid.uuid4().hex[:8]}"

        async def _do():
            return await graphiti.add_episode(
                name=name,
                episode_body=data or "",
                source=EpisodeType.text,
                reference_time=now,
                source_description=kwargs.get("source_description", "MiroFish memory"),
                group_id=group,
                entity_types=self._entity_types or None,
            )

        result = _run_async(_do())
        ep_uuid = getattr(result.episode, "uuid", None) or getattr(result.episode, "uuid_", None) or ""
        return _EpisodeResult(uuid_=ep_uuid, processed=True)

    # ---- Search ----
    def search(
        self,
        query: str,
        graph_ids: Optional[List[str]] = None,
        group_ids: Optional[List[str]] = None,
        limit: int = 10,
        scope: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Hybrid semantic+keyword search. Returns an object with .nodes and
        .edges attributes (both lists)."""
        graphiti, _ = _get_graphiti()
        group_param = group_ids or graph_ids

        async def _do():
            return await graphiti.search(
                query=query,
                group_ids=group_param,
                num_results=limit,
            )

        raw_edges = _run_async(_do())

        # Graphiti's search returns edge-oriented results. We reshape into
        # something with .edges and .nodes so MiroFish's call sites keep
        # working without branching on source.
        edges: List[_EdgeResult] = []
        node_uuids_seen = set()
        nodes: List[_NodeResult] = []
        for e in raw_edges or []:
            edges.append(_EdgeResult(
                uuid_=getattr(e, "uuid", "") or getattr(e, "uuid_", "") or "",
                name=getattr(e, "name", "") or "",
                fact=getattr(e, "fact", "") or "",
                source_node_uuid=getattr(e, "source_node_uuid", None),
                target_node_uuid=getattr(e, "target_node_uuid", None),
                group_id=getattr(e, "group_id", None),
            ))
            for uid_attr in ("source_node_uuid", "target_node_uuid"):
                uid = getattr(e, uid_attr, None)
                if uid and uid not in node_uuids_seen:
                    node_uuids_seen.add(uid)

        # If the caller asked for node-oriented results, resolve the nodes.
        if scope in (None, "nodes") and node_uuids_seen:
            node_ns = _NodeNS()
            for uid in node_uuids_seen:
                n = node_ns.get(uid)
                if n is not None:
                    nodes.append(n)

        @dataclass
        class _SearchResult:
            edges: List[_EdgeResult]
            nodes: List[_NodeResult]

        return _SearchResult(edges=edges, nodes=nodes)


# ---------------------------------------------------------------------------
# Top-level Zep-compatible client. Drop-in for `zep_cloud.client.Zep`.
# ---------------------------------------------------------------------------


class Zep:
    """Zep Cloud SDK-shaped client, backed by Graphiti + Neo4j."""

    def __init__(self, api_key: Optional[str] = None, **_: Any) -> None:
        # api_key intentionally ignored — we authenticate to Neo4j via
        # NEO4J_* environment variables from config.
        self.api_key = api_key
        self.graph = _GraphNS()
        # Warm up lazily — the first real call triggers _get_graphiti().
