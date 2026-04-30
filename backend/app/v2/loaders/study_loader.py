"""
Path A study loader.

Reads a study.json + its CSV files, validates against
app/v2/schemas/study.schema.json, and produces typed in-memory objects ready
to be written into Neo4j by the graph_writer module.

No LLM. No fuzzy matching. Hard fail on schema/type/key violations.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import jsonschema


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "study.schema.json"


class StudyLoadError(ValueError):
    """Raised when a study fails to load. Always caused by user input."""


# ---------- typed objects ----------

@dataclass(frozen=True)
class Brief:
    content_id: str
    title: str
    genre: str | None = None
    slot: str | None = None
    channel: str | None = None
    runtime_minutes: int | None = None
    air_date: str | None = None
    synopsis: str | None = None
    rules: tuple[str, ...] = ()


@dataclass
class IdentityNode:
    """One row of the identity CSV → one Neo4j node."""
    label: str
    key_field: str
    key_value: str
    properties: dict[str, Any] = field(default_factory=dict)
    # Attribute dimensions land here as nested dicts/lists keyed by `as` name.
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DimensionEdge:
    """One row of an edge dimension CSV → one typed edge in Neo4j."""
    edge_type: str
    source_label: str
    source_key_field: str
    source_key_value: str
    target_label: str
    target_key_field: str
    target_key_value: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class DimensionAttribute:
    """Marker for an attribute dimension after merge — kept for traceability."""
    name: str
    rendered_as: str
    row_count: int


@dataclass
class Study:
    study_id: str
    name: str
    description: str
    brief: Brief
    engagement: dict[str, Any]
    nodes: list[IdentityNode]                    # identity nodes
    edges: list[DimensionEdge]                   # cross-table edges
    attribute_summary: list[DimensionAttribute]  # what merged onto identity nodes
    target_nodes: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    # ^ key: (label, key_value) → properties — all targets referenced by edges
    raw_study_dict: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_label(self) -> str:
        return self.nodes[0].label if self.nodes else ""

    def by_key(self, key_value: str) -> IdentityNode:
        for n in self.nodes:
            if n.key_value == key_value:
                return n
        raise StudyLoadError(f"identity key not found: {key_value!r}")


# ---------- public entry point ----------

def load_study(study_json_path: str | Path) -> Study:
    study_path = Path(study_json_path).resolve()
    if not study_path.exists():
        raise StudyLoadError(f"study file does not exist: {study_path}")

    base_dir = study_path.parent
    raw = _load_json(study_path)
    _validate(raw)

    identity_spec = raw["identity"]
    nodes = _load_identity(base_dir, identity_spec)
    nodes_by_key = {n.key_value: n for n in nodes}

    edges: list[DimensionEdge] = []
    attr_summary: list[DimensionAttribute] = []
    target_nodes: dict[tuple[str, str], dict[str, Any]] = {}

    for dim in raw.get("dimensions") or []:
        if dim["kind"] == "edge":
            new_edges, new_targets = _load_edge_dimension(
                base_dir, dim, identity_spec, nodes_by_key
            )
            edges.extend(new_edges)
            for tk, tv in new_targets.items():
                # last-write-wins is fine; rows are deterministically ordered.
                target_nodes[tk] = {**target_nodes.get(tk, {}), **tv}
        elif dim["kind"] == "attribute":
            summary = _load_attribute_dimension(
                base_dir, dim, identity_spec, nodes_by_key
            )
            attr_summary.append(summary)
        else:                                                 # pragma: no cover
            raise StudyLoadError(f"unknown dimension kind: {dim['kind']!r}")

    brief_dict = raw["brief"]
    brief = Brief(
        content_id=brief_dict["content_id"],
        title=brief_dict["title"],
        genre=brief_dict.get("genre"),
        slot=brief_dict.get("slot"),
        channel=brief_dict.get("channel"),
        runtime_minutes=brief_dict.get("runtime_minutes"),
        air_date=brief_dict.get("air_date"),
        synopsis=brief_dict.get("synopsis"),
        rules=tuple(brief_dict.get("rules") or ()),
    )

    return Study(
        study_id=raw["study_id"],
        name=raw.get("name", raw["study_id"]),
        description=raw.get("description", ""),
        brief=brief,
        engagement=raw.get("engagement") or {},
        nodes=nodes,
        edges=edges,
        attribute_summary=attr_summary,
        target_nodes=target_nodes,
        raw_study_dict=raw,
    )


# ---------- internals ----------

def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise StudyLoadError(f"invalid JSON in {path}: {e}") from e


def _validate(raw: dict[str, Any]) -> None:
    schema = _load_json(SCHEMA_PATH)
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as e:
        loc = ".".join(str(p) for p in e.absolute_path) or "<root>"
        raise StudyLoadError(f"schema violation at {loc}: {e.message}") from e


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise StudyLoadError(f"CSV referenced by study not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
    return rows


def _coerce(value: str, col_type: str, *, where: str) -> Any:
    if value is None:
        value = ""
    if col_type == "string":
        return value
    if col_type == "int":
        try:
            return int(value)
        except ValueError as e:
            raise StudyLoadError(f"{where}: cannot coerce {value!r} to int") from e
    if col_type == "float":
        try:
            return float(value)
        except ValueError as e:
            raise StudyLoadError(f"{where}: cannot coerce {value!r} to float") from e
    if col_type == "bool":
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y"):
            return True
        if v in ("false", "0", "no", "n", ""):
            return False
        raise StudyLoadError(f"{where}: cannot coerce {value!r} to bool")
    if col_type == "string_list":
        return [s.strip() for s in value.split(";") if s.strip()]
    raise StudyLoadError(f"{where}: unknown column type {col_type!r}")  # pragma: no cover


def _validate_columns(rows: list[dict[str, str]], columns: list[dict[str, Any]],
                      *, where: str) -> None:
    declared = {c["name"] for c in columns}
    if not rows:
        raise StudyLoadError(f"{where}: CSV has no rows")
    actual = set(rows[0].keys())
    missing = declared - actual
    if missing:
        raise StudyLoadError(f"{where}: CSV missing declared columns: {sorted(missing)}")
    # Extra columns are tolerated; we just don't read them.


def _coerce_row(row: dict[str, str], columns: list[dict[str, Any]],
                *, where: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        name = col["name"]
        required = col.get("required", True)
        raw_value = row.get(name, "")
        if (raw_value is None or raw_value == "") and required:
            raise StudyLoadError(f"{where}: required column {name!r} is empty")
        coerced = _coerce(raw_value, col["type"], where=f"{where}.{name}")
        if "enum" in col and coerced != "" and coerced not in col["enum"]:
            raise StudyLoadError(
                f"{where}.{name}: value {coerced!r} not in enum {col['enum']}"
            )
        out[name] = coerced
    return out


def _load_identity(base_dir: Path, spec: dict[str, Any]) -> list[IdentityNode]:
    csv_path = (base_dir / spec["csv"]).resolve()
    rows = _read_csv(csv_path)
    _validate_columns(rows, spec["columns"], where=f"identity({spec['csv']})")

    seen: set[str] = set()
    nodes: list[IdentityNode] = []
    for i, row in enumerate(rows):
        coerced = _coerce_row(
            row, spec["columns"],
            where=f"identity({spec['csv']}) row {i + 1}"
        )
        key_value = str(coerced[spec["key"]])
        if key_value in seen:
            raise StudyLoadError(
                f"identity({spec['csv']}): duplicate key {key_value!r}"
            )
        seen.add(key_value)
        nodes.append(IdentityNode(
            label=spec["label"],
            key_field=spec["key"],
            key_value=key_value,
            properties=coerced,
        ))
    return nodes


def _load_edge_dimension(base_dir: Path, dim: dict[str, Any],
                         identity_spec: dict[str, Any],
                         nodes_by_key: dict[str, IdentityNode]
                         ) -> tuple[list[DimensionEdge], dict[tuple[str, str], dict[str, Any]]]:
    csv_path = (base_dir / dim["csv"]).resolve()
    rows = _read_csv(csv_path)
    _validate_columns(rows, dim["columns"], where=f"edge({dim['csv']})")

    target_label = dim["target"]["label"]
    target_key_field = dim["target"]["key"]

    # Build out the target node properties map. If a target_csv is provided
    # we read it; otherwise targets are minted from edge rows with just the key.
    target_props: dict[tuple[str, str], dict[str, Any]] = {}
    if "csv" in dim["target"]:
        t_csv = (base_dir / dim["target"]["csv"]).resolve()
        t_rows = _read_csv(t_csv)
        t_cols = dim["target"].get("columns") or []
        if t_cols:
            _validate_columns(t_rows, t_cols, where=f"edge_target({dim['target']['csv']})")
            for i, t_row in enumerate(t_rows):
                coerced = _coerce_row(
                    t_row, t_cols,
                    where=f"edge_target({dim['target']['csv']}) row {i + 1}"
                )
                target_props[(target_label, str(coerced[target_key_field]))] = coerced

    join_on = dim["join_on"]
    edge_type = dim["edge"]
    source_label = identity_spec["label"]
    source_key_field = identity_spec["key"]

    edges: list[DimensionEdge] = []
    for i, row in enumerate(rows):
        where = f"edge({dim['csv']}) row {i + 1}"
        coerced = _coerce_row(row, dim["columns"], where=where)
        src_key = str(coerced[join_on])
        if src_key not in nodes_by_key:
            raise StudyLoadError(
                f"{where}: {join_on}={src_key!r} does not match any identity row"
            )
        tgt_key = str(coerced[target_key_field])
        if not tgt_key:
            raise StudyLoadError(f"{where}: empty target key {target_key_field!r}")

        # Auto-mint a stub target node if no target CSV was provided.
        target_props.setdefault(
            (target_label, tgt_key),
            {target_key_field: tgt_key},
        )

        edge_props = {k: v for k, v in coerced.items()
                      if k not in (join_on, target_key_field)}
        edges.append(DimensionEdge(
            edge_type=edge_type,
            source_label=source_label,
            source_key_field=source_key_field,
            source_key_value=src_key,
            target_label=target_label,
            target_key_field=target_key_field,
            target_key_value=tgt_key,
            properties=edge_props,
        ))
    return edges, target_props


def _load_attribute_dimension(base_dir: Path, dim: dict[str, Any],
                              identity_spec: dict[str, Any],
                              nodes_by_key: dict[str, IdentityNode]
                              ) -> DimensionAttribute:
    csv_path = (base_dir / dim["csv"]).resolve()
    rows = _read_csv(csv_path)
    _validate_columns(rows, dim["columns"], where=f"attribute({dim['csv']})")

    join_on = dim["join_on"]
    rendered_as = dim.get("as") or dim.get("name") or csv_path.stem

    # Two CSV shapes are common for sparse attributes:
    #   (a) (key, polarity, term)   → grouped dict-of-lists
    #   (b) (key, idx, value)       → ordered list
    #   (c) (key, k1, v1, k2, v2)   → list of dicts
    # We flatten by grouping all non-join_on columns under the row.
    grouped: dict[str, list[dict[str, Any]]] = {pid: [] for pid in nodes_by_key}
    for i, row in enumerate(rows):
        coerced = _coerce_row(
            row, dim["columns"],
            where=f"attribute({dim['csv']}) row {i + 1}"
        )
        key = str(coerced[join_on])
        if key not in nodes_by_key:
            raise StudyLoadError(
                f"attribute({dim['csv']}) row {i + 1}: "
                f"{join_on}={key!r} does not match any identity row"
            )
        rest = {k: v for k, v in coerced.items() if k != join_on}
        grouped[key].append(rest)

    # Three flattening shapes are supported (Neo4j only stores primitives or
    # arrays of primitives — we never produce a list[dict] property):
    #
    #   (a) (idx, value)              → list[primitive] ordered by idx
    #   (b) (enum_col, value_col)     → one list[primitive] per enum value,
    #                                    rendered as ``{rendered_as}_{enum_value}``
    #   (c) anything else             → list[primitive] of "k1=v1; k2=v2" strings
    #                                    (best-effort fallback, rarely used)
    other_cols = [c for c in dim["columns"] if c["name"] != join_on]

    is_indexed_list = (
        len(other_cols) == 2
        and any(c["name"] in {"idx", "index", "i"} for c in other_cols)
    )

    enum_grouped = (
        len(other_cols) == 2
        and any("enum" in c for c in other_cols)
        and not is_indexed_list
    )

    for key, items in grouped.items():
        node = nodes_by_key[key]
        if is_indexed_list:
            sort_col = next(c["name"] for c in other_cols
                            if c["name"] in {"idx", "index", "i"})
            value_col = next(c["name"] for c in other_cols if c["name"] != sort_col)
            items_sorted = sorted(items, key=lambda d, k=sort_col: d.get(k, 0))
            node.attributes[rendered_as] = [it[value_col] for it in items_sorted]
        elif enum_grouped:
            enum_col = next(c["name"] for c in other_cols if "enum" in c)
            value_col = next(c["name"] for c in other_cols if c["name"] != enum_col)
            buckets: dict[str, list[Any]] = {}
            for it in items:
                buckets.setdefault(str(it[enum_col]), []).append(it[value_col])
            for enum_value, values in buckets.items():
                node.attributes[f"{rendered_as}_{enum_value}"] = values
        else:
            # Fallback — flatten each row to a key=value;... string. Keeps
            # Neo4j happy without losing information.
            node.attributes[rendered_as] = [
                "; ".join(f"{k}={v}" for k, v in it.items())
                for it in items
            ]

    return DimensionAttribute(
        name=dim.get("name") or rendered_as,
        rendered_as=rendered_as,
        row_count=len(rows),
    )
