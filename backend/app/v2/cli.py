"""
v2 CLI — single entry point for the no-fork end-to-end loop.

    python -m app.v2.cli run \\
        --study seeds/bbc_panel/study.json \\
        --out   uploads/v2_runs/bbc_panel \\
        --rounds 2

Steps run in this order: load study (Path A) → write to Neo4j → project
personas → warm embeddings → MiniRunner with engagement gates → metrics →
narrator. Each step prints a one-line summary.

Skip Neo4j with ``--skip-neo4j`` for offline checks. Skip the LLM narrator
with ``--no-llm-narrator``; the report still has all metrics.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .loaders import load_study
from .graph_writer import GraphWriter
from .persona import project_personas
from .salience import SalienceScorer
from .runner import MiniRunner
from .metrics import compute_metrics
from .narrator import render_report_offline, render_report_with_llm


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mirofish-v2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="end-to-end run")
    run.add_argument("--study", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--rounds", type=int, default=2)
    run.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    run.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER", "neo4j"))
    run.add_argument("--neo4j-password",
                     default=os.environ.get("NEO4J_PASSWORD", "mirofish-local-password"))
    run.add_argument("--graph-id", default=None)
    run.add_argument("--skip-neo4j", action="store_true")
    run.add_argument("--no-llm-narrator", action="store_true")
    run.add_argument("--quiet", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd != "run":
        ap.error("unknown command")
        return 2

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] loading study {args.study} ...", flush=True)
    study = load_study(args.study)
    print(f"    study_id          {study.study_id}")
    print(f"    panelists         {len(study.nodes)}")
    print(f"    typed edges       {len(study.edges)}")
    print(f"    target nodes      {len(study.target_nodes)}")

    if not args.skip_neo4j:
        print(f"[2/6] writing graph to Neo4j ...", flush=True)
        gw = GraphWriter(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
        try:
            stats = gw.write_study(study, graph_id=args.graph_id)
            print(f"    graph_id          {stats.graph_id}")
            print(f"    identity nodes    {stats.identity_nodes}")
            print(f"    target nodes      {stats.target_nodes}")
            print(f"    edges by type     {stats.edge_types}")
        finally:
            gw.close()
    else:
        print(f"[2/6] skip-neo4j flag set; not writing", flush=True)

    print(f"[3/6] projecting personas ...", flush=True)
    personas, proj_stats = project_personas(study)
    for p in personas:
        print(f"    {p.panelist_id:<10} thr={p.engagement.salience_threshold:.2f} "
              f"cap={p.engagement.daily_action_cap} "
              f"actions={len(p.available_actions)} "
              f"temp={p.sampling.temperature:.2f}")

    print(f"[4/6] scoring salience + engagement gates + LLM reactions ...", flush=True)
    scorer = SalienceScorer()
    runner = MiniRunner()
    run = runner.run(study, personas, scorer, rounds=args.rounds)
    (out / "run.json").write_text(json.dumps(run.as_dict(), indent=2,
                                             default=str), encoding="utf-8")
    posts_path, trace_path = run.write_jsonl(out)
    print(f"    rounds            {run.rounds}")
    print(f"    posts             {len(run.posts)}")
    print(f"    decisions         {len(run.decisions)}")
    print(f"    LLM calls         {run.llm_calls}")
    print(f"    embedding cache   {run.cache_stats}")
    print(f"    posts.jsonl       {posts_path}")
    print(f"    trace.jsonl       {trace_path}")

    print(f"[5/6] computing metrics ...", flush=True)
    report = compute_metrics(personas, run)
    h = report.headline
    print(f"    reach             {h.reach}/{h.panel_size}")
    print(f"    engagement        {h.engagement}/{h.panel_size}")
    ai = "—" if h.appreciation_index is None else f"{h.appreciation_index:.1f}"
    print(f"    AI                {ai}")
    print(f"    clarity_risk      {h.clarity_risk}/{h.panel_size}")
    (out / "metrics.json").write_text(
        json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    print(f"[6/6] rendering report ...", flush=True)
    if args.no_llm_narrator:
        md = render_report_offline(study.name, study.brief.title, report)
    else:
        md = render_report_with_llm(study.name, study.brief.title, report, run)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(f"    report.md         {out / 'report.md'}")
    print()
    print(f"DONE  outputs in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
