"""
v2 Flask blueprint — schema-direct study runs over HTTP.

Endpoints (all under ``/api/v2``):

  POST  /studies/from-disk    register a known seed dir as a study
  POST  /studies/upload       upload a study (zip of study.json + CSVs)
  GET   /studies              list registered studies
  GET   /studies/<id>/json    download the raw study.json
  GET   /studies/<id>/bundle  download the full study dir as a zip
  POST  /runs                 start a run for a study (background thread)
  GET   /runs                 list runs
  GET   /runs/<run_id>        run status + headline metrics
  GET   /runs/<run_id>/report rendered markdown
  GET   /runs/<run_id>/posts  jsonl posts
  GET   /runs/<run_id>/trace  jsonl decisions
  GET   /runs/<run_id>/log    streaming run log

Runs are persisted under ``backend/uploads/v2_runs/<run_id>/``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request, send_file

from .cli import main as _cli_main_unused              # noqa: F401  (lint anchor)
from .graph_writer import GraphWriter
from .loaders import load_study
from .metrics import compute_metrics
from .narrator import render_report_offline, render_report_with_llm
from .persona import project_personas
from .runner import MiniRunner
from .salience import SalienceScorer


logger = logging.getLogger("mirofish.v2.api")
v2_bp = Blueprint("v2", __name__)


# ---------- on-disk layout ----------

UPLOADS_ROOT = Path(__file__).resolve().parents[2] / "uploads"
STUDIES_INDEX = UPLOADS_ROOT / "v2_studies.json"
RUNS_ROOT = UPLOADS_ROOT / "v2_runs"

UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
RUNS_ROOT.mkdir(parents=True, exist_ok=True)


# ---------- version stamp ----------

def _git_short_sha() -> str:
    import subprocess
    try:
        repo_root = Path(__file__).resolve().parents[3]
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "nogit"
    except Exception:
        pass
    return "nogit"


_SERVER_GIT_SHA = _git_short_sha()
_SERVER_STARTED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@v2_bp.route("/version", methods=["GET"])
def version() -> Response:
    return jsonify({
        "success": True,
        "data": {
            "git_sha":    _SERVER_GIT_SHA,
            "started_at": _SERVER_STARTED_AT,
        },
    })


# ---------- in-process state ----------

_runs_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}                  # run_id -> mutable status


# ---------- studies ----------

@v2_bp.route("/studies/from-disk", methods=["POST"])
def register_study_from_disk() -> Response:
    """Register a study that lives on disk. Body: {"path": "..."}.

    Used to pin one of the seed studies under ``backend/seeds/`` to a friendly
    id without uploading anything.
    """
    body = request.get_json(silent=True) or {}
    path_str = body.get("path", "").strip()
    if not path_str:
        return jsonify({"success": False, "error": "missing 'path'"}), 400
    p = Path(path_str)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[2] / p).resolve()
    if not p.exists():
        return jsonify({"success": False, "error": f"path not found: {p}"}), 404

    try:
        s = load_study(p)
    except Exception as e:
        return jsonify({"success": False, "error": f"failed to load: {e}"}), 400

    record = {
        "study_id":    s.study_id,
        "name":        s.name,
        "description": s.description,
        "path":        str(p),
        "panelists":   len(s.nodes),
        "edges":       len(s.edges),
        "brief": {
            "content_id": s.brief.content_id,
            "title":      s.brief.title,
            "air_date":   s.brief.air_date,
        },
        "registered_at": _iso_now(),
    }
    _index_put(record)
    return jsonify({"success": True, "data": record})


@v2_bp.route("/studies", methods=["GET"])
def list_studies() -> Response:
    return jsonify({"success": True, "data": _index_load()})


@v2_bp.route("/studies/upload", methods=["POST"])
def upload_study() -> Response:
    """Register a study by uploading a zip bundle (study.json + its CSVs).

    A bare study.json is accepted only if it references no external CSVs —
    in practice that never happens, so prefer zipping the whole study dir.
    """
    if "file" not in request.files:
        return jsonify({"success": False,
                         "error": "missing 'file' in multipart form"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"success": False, "error": "empty filename"}), 400

    import shutil
    import zipfile

    studies_dir = UPLOADS_ROOT / "v2_studies"
    studies_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(f.filename).stem.strip().replace(" ", "_") or "upload"
    target = studies_dir / f"{stem}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    target.mkdir(parents=True, exist_ok=False)

    raw = target / f.filename
    f.save(raw)

    try:
        if zipfile.is_zipfile(raw):
            with zipfile.ZipFile(raw) as zf:
                zf.extractall(target)
            raw.unlink(missing_ok=True)
            study_json = next(target.rglob("study.json"), None)
            if study_json is None:
                shutil.rmtree(target, ignore_errors=True)
                return jsonify({"success": False,
                                 "error": "zip did not contain a study.json"}), 400
            study_dir = study_json.parent
        elif raw.suffix.lower() == ".json":
            study_dir = raw.parent
            if raw.name != "study.json":
                renamed = study_dir / "study.json"
                raw.rename(renamed)
        else:
            shutil.rmtree(target, ignore_errors=True)
            return jsonify({"success": False,
                             "error": "expected a .zip or study.json"}), 400

        try:
            s = load_study(study_dir / "study.json")
        except Exception as e:
            shutil.rmtree(target, ignore_errors=True)
            return jsonify({"success": False,
                             "error": f"failed to load study: {e}"}), 400
    except Exception as e:                                  # pragma: no cover
        shutil.rmtree(target, ignore_errors=True)
        return jsonify({"success": False, "error": f"upload failed: {e}"}), 500

    record = {
        "study_id":    s.study_id,
        "name":        s.name,
        "description": s.description,
        "path":        str(study_dir / "study.json"),
        "panelists":   len(s.nodes),
        "edges":       len(s.edges),
        "brief": {
            "content_id": s.brief.content_id,
            "title":      s.brief.title,
            "air_date":   s.brief.air_date,
        },
        "registered_at": _iso_now(),
    }
    _index_put(record)
    return jsonify({"success": True, "data": record})


@v2_bp.route("/studies/<study_id>/json", methods=["GET"])
def download_study_json(study_id: str) -> Response:
    """Serve the raw study.json for a registered study as a download."""
    record = next((s for s in _index_load() if s["study_id"] == study_id), None)
    if record is None:
        return jsonify({"success": False, "error": "unknown study_id"}), 404
    p = Path(record["path"])
    if p.is_dir():
        p = p / "study.json"
    if not p.exists():
        return jsonify({"success": False, "error": f"file missing on disk: {p}"}), 404
    return send_file(p, mimetype="application/json",
                     as_attachment=True, download_name=f"{study_id}.json")


@v2_bp.route("/studies/<study_id>/bundle", methods=["GET"])
def download_study_bundle(study_id: str) -> Response:
    """Zip the entire study dir (study.json + all CSVs) and serve it."""
    import io
    import zipfile

    record = next((s for s in _index_load() if s["study_id"] == study_id), None)
    if record is None:
        return jsonify({"success": False, "error": "unknown study_id"}), 404
    src = Path(record["path"])
    study_dir = src.parent if src.is_file() else src
    if not study_dir.exists():
        return jsonify({"success": False,
                         "error": f"study dir missing: {study_dir}"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in sorted(study_dir.iterdir()):
            if child.is_file():
                zf.write(child, arcname=child.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True,
                     download_name=f"{study_id}.zip")


# ---------- runs ----------

@v2_bp.route("/runs", methods=["POST"])
def start_run() -> Response:
    body = request.get_json(silent=True) or {}
    study_id = (body.get("study_id") or "").strip()
    rounds = int(body.get("rounds") or 2)
    skip_neo4j = bool(body.get("skip_neo4j", False))
    no_llm_narrator = bool(body.get("no_llm_narrator", False))

    studies = {s["study_id"]: s for s in _index_load()}
    if study_id not in studies:
        return jsonify({"success": False,
                         "error": f"unknown study_id {study_id!r}; "
                                  f"register it via /api/v2/studies/from-disk first"}), 404
    study_record = studies[study_id]

    run_id = f"v2_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    status = {
        "run_id":        run_id,
        "study_id":      study_id,
        "study_name":    study_record["name"],
        "status":        "starting",
        "step":          0,
        "step_total":    6,
        "started_at":    _iso_now(),
        "finished_at":   None,
        "rounds":        rounds,
        "skip_neo4j":    skip_neo4j,
        "no_llm_narrator": no_llm_narrator,
        "posts_dir":     str(run_dir),
        "error":         None,
        "headline":      None,
        "log":           [],
    }
    with _runs_lock:
        _runs[run_id] = status

    t = threading.Thread(
        target=_execute_run, args=(run_id, study_record, run_dir),
        name=f"v2-run-{run_id}", daemon=True,
    )
    t.start()
    return jsonify({"success": True, "data": status}), 202


@v2_bp.route("/runs", methods=["GET"])
def list_runs() -> Response:
    with _runs_lock:
        snapshot = list(_runs.values())
    snapshot.sort(key=lambda r: r["started_at"], reverse=True)
    # Strip log blob from listing.
    return jsonify({"success": True,
                    "data": [{k: v for k, v in r.items() if k != "log"}
                             for r in snapshot]})


@v2_bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id: str) -> Response:
    with _runs_lock:
        status = _runs.get(run_id)
    if status is None:
        return jsonify({"success": False, "error": "run not found"}), 404
    return jsonify({"success": True, "data": status})


@v2_bp.route("/runs/<run_id>/report", methods=["GET"])
def get_run_report(run_id: str) -> Response:
    md_path = RUNS_ROOT / run_id / "report.md"
    if not md_path.exists():
        return jsonify({"success": False, "error": "report not yet generated"}), 404
    return Response(md_path.read_text(encoding="utf-8"),
                    mimetype="text/markdown; charset=utf-8")


@v2_bp.route("/runs/<run_id>/posts", methods=["GET"])
def get_run_posts(run_id: str) -> Response:
    return _serve_jsonl(RUNS_ROOT / run_id / "posts.jsonl")


@v2_bp.route("/runs/<run_id>/trace", methods=["GET"])
def get_run_trace(run_id: str) -> Response:
    return _serve_jsonl(RUNS_ROOT / run_id / "trace.jsonl")


@v2_bp.route("/runs/<run_id>/log", methods=["GET"])
def get_run_log(run_id: str) -> Response:
    with _runs_lock:
        status = _runs.get(run_id)
    if status is None:
        return jsonify({"success": False, "error": "run not found"}), 404
    return jsonify({"success": True, "data": status["log"]})


# ---------- graph inspection ----------
#
# Read-only endpoints that surface the typed graph that v2 wrote into Neo4j.
# Useful when the Neo4j Browser isn't reachable (e.g. on Railway where the
# graphdb service is private).

@v2_bp.route("/graphs", methods=["GET"])
def list_graphs() -> Response:
    """List every graph_id that has at least one node, with per-label counts."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return jsonify({"success": False, "error": "neo4j driver unavailable"}), 503

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "mirofish-local-password")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            rows = session.run(
                "MATCH (n) WHERE n.graph_id IS NOT NULL "
                "RETURN n.graph_id AS gid, labels(n)[0] AS label, count(*) AS c "
                "ORDER BY gid, label"
            )
            agg: dict[str, dict[str, int]] = {}
            for r in rows:
                agg.setdefault(r["gid"], {})[r["label"]] = r["c"]
        driver.close()
    except Exception as e:
        return jsonify({"success": False, "error": f"neo4j query failed: {e}"}), 503
    return jsonify({"success": True,
                    "data": [{"graph_id": gid, "label_counts": cnt}
                             for gid, cnt in sorted(agg.items())]})


@v2_bp.route("/graphs/<graph_id>", methods=["GET"])
def get_graph(graph_id: str) -> Response:
    """Return the full typed graph as a node + edge list. Embeddings stripped.

    Query params:
      ?label=Panelist     filter nodes to one label
      ?include_brief=0    drop the Brief node from the result
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return jsonify({"success": False, "error": "neo4j driver unavailable"}), 503

    label_filter = request.args.get("label")
    include_brief = request.args.get("include_brief", "1") != "0"

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "mirofish-local-password")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            node_rows = session.run(
                "MATCH (n {graph_id: $gid}) "
                "RETURN id(n) AS nid, labels(n) AS labels, "
                "       n._key AS key, properties(n) AS props",
                gid=graph_id,
            )
            nodes = []
            id_to_label_key: dict[int, tuple[str, str]] = {}
            for r in node_rows:
                labels = list(r["labels"]) or ["Node"]
                if label_filter and label_filter not in labels:
                    continue
                if not include_brief and "Brief" in labels:
                    continue
                primary = next((l for l in labels if l != "Entity"), labels[0])
                clean_props = {k: v for k, v in (r["props"] or {}).items()
                               if k not in ("_key_field",) and not k.startswith("_")
                               or k == "_key"}
                nodes.append({
                    "id":     str(r["nid"]),
                    "label":  primary,
                    "key":    r["key"],
                    "props":  clean_props,
                })
                id_to_label_key[r["nid"]] = (primary, r["key"])

            edge_rows = session.run(
                "MATCH (s {graph_id: $gid})-[r]->(t {graph_id: $gid}) "
                "RETURN id(r) AS rid, type(r) AS type, "
                "       id(s) AS sid, id(t) AS tid, properties(r) AS props",
                gid=graph_id,
            )
            edges = []
            for r in edge_rows:
                if r["sid"] not in id_to_label_key or r["tid"] not in id_to_label_key:
                    continue
                edges.append({
                    "id":     str(r["rid"]),
                    "type":   r["type"],
                    "source": str(r["sid"]),
                    "target": str(r["tid"]),
                    "props":  dict(r["props"] or {}),
                })
        driver.close()
    except Exception as e:
        return jsonify({"success": False, "error": f"neo4j query failed: {e}"}), 503

    return jsonify({"success": True, "data": {
        "graph_id": graph_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }})


# ---------- background worker ----------

def _execute_run(run_id: str, study_record: dict[str, Any],
                 run_dir: Path) -> None:
    log = lambda msg: _append_log(run_id, msg)
    try:
        log("loading study")
        _set_status(run_id, status="loading", step=1)
        study = load_study(study_record["path"])

        if not _runs[run_id]["skip_neo4j"]:
            log("writing graph to Neo4j")
            _set_status(run_id, status="graph", step=2)
            uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
            user = os.environ.get("NEO4J_USER", "neo4j")
            password = os.environ.get("NEO4J_PASSWORD", "mirofish-local-password")
            gw = GraphWriter(uri, user, password)
            try:
                stats = gw.write_study(study, graph_id=f"v2_{study.study_id}")
                log(f"graph: {stats.identity_nodes} nodes, "
                    f"{stats.target_nodes} targets, {stats.edges} edges")
            finally:
                gw.close()

        log("projecting personas")
        _set_status(run_id, status="projecting", step=3)
        personas, _ = project_personas(study)
        log(f"projected {len(personas)} personas")

        log("running engagement gate + LLM reactions")
        _set_status(run_id, status="simulating", step=4)
        scorer = SalienceScorer()
        runner = MiniRunner()
        result = runner.run(study, personas, scorer,
                            rounds=_runs[run_id]["rounds"],
                            run_id=run_id)
        result.write_jsonl(run_dir)
        (run_dir / "run.json").write_text(
            json.dumps(result.as_dict(), indent=2, default=str), encoding="utf-8"
        )
        log(f"sim: {result.posts_created} posts, {result.llm_calls} LLM calls, "
            f"{len(result.decisions)} decisions")

        log("computing metrics")
        _set_status(run_id, status="metrics", step=5)
        report = compute_metrics(personas, result)
        (run_dir / "metrics.json").write_text(
            json.dumps(report.as_dict(), indent=2), encoding="utf-8"
        )
        h = report.headline
        log(f"reach={h.reach}/{h.panel_size} engagement={h.engagement}/{h.panel_size} "
            f"AI={'-' if h.appreciation_index is None else round(h.appreciation_index, 1)} "
            f"clarity_risk={h.clarity_risk}/{h.panel_size}")

        log("rendering report")
        _set_status(run_id, status="reporting", step=6)
        if _runs[run_id]["no_llm_narrator"]:
            md = render_report_offline(study.name, study.brief.title, report)
        else:
            md = render_report_with_llm(study.name, study.brief.title, report, result)
        (run_dir / "report.md").write_text(md, encoding="utf-8")

        _set_status(run_id, status="done", step=6,
                    finished_at=_iso_now(),
                    headline=asdict(report.headline))
        log("done")
    except Exception as e:                              # pragma: no cover
        logger.exception("v2 run failed: %s", e)
        log(f"ERROR: {e}")
        _set_status(run_id, status="failed", error=str(e),
                    finished_at=_iso_now())


# ---------- helpers ----------

def _index_load() -> list[dict[str, Any]]:
    if not STUDIES_INDEX.exists():
        return []
    try:
        return json.loads(STUDIES_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []


def _index_put(record: dict[str, Any]) -> None:
    items = _index_load()
    items = [r for r in items if r["study_id"] != record["study_id"]]
    items.append(record)
    items.sort(key=lambda r: r["study_id"])
    STUDIES_INDEX.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _serve_jsonl(path: Path) -> Response:
    if not path.exists():
        return jsonify({"success": False, "error": "file not found"}), 404
    return send_file(path, mimetype="application/x-jsonlines")


def _set_status(run_id: str, **fields) -> None:
    with _runs_lock:
        if run_id in _runs:
            _runs[run_id].update(fields)


def _append_log(run_id: str, msg: str) -> None:
    line = f"{_iso_now()}  {msg}"
    logger.info("[v2 run %s] %s", run_id, msg)
    with _runs_lock:
        if run_id in _runs:
            _runs[run_id]["log"].append(line)


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
