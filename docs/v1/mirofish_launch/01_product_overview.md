# MiroFish v2 — product overview

## What it is

MiroFish v2 is an open-source platform for running named, schema-driven
synthetic-audience studies. A study is one identity CSV (the panel) plus
dimension CSVs (propensities, voice, vocabulary) plus a brief — the thing
whose reception you want to predict.

The runner projects each row in the identity CSV into a persona, scores
salience between the persona and the brief, walks an engagement gate
(`DO_NOTHING`, `LIKE_POST`, `REPOST`, `CREATE_POST`, `REPLY`) per round,
and emits a markdown report grounded in the panel's recorded behaviour.

## What's new in v2

- Schema-direct study format: study.json + CSVs, no bespoke loader per study.
- Upload UI at `/v2`: drop a zip, registers and validates in one round-trip.
- Per-study Neo4j graph: every study writes to its own `graph_id`, so
  multiple studies can coexist on a single Neo4j instance and be inspected
  independently. The browser view at `/v2/graph` lets you click through the
  identity nodes, edges, and target nodes.
- Inspectable run artifacts: every run writes `run.json`, `posts.jsonl`,
  `decisions.jsonl`, `metrics.json`, and `report.md` to disk. No black box.
- Per-row delete on the studies table.
- Build/version badge in the corner of every page so you always know which
  build of the UI you're looking at and whether it matches the server.

## What it is not

- It is not a replacement for recruited respondents. Synthetic respondents
  are not respondents.
- It is not a hot-take generator. Predictions are constrained by each
  panelist's `recorded_behaviour`, voice register, required/forbidden
  vocabulary, and propensities.
- It is not a single-prompt tool. Studies are reproducible and seedable —
  you can re-run, diff, and version them.

## The honest pitch

MiroFish is a hypothesis-pruning tool. Run twenty cheap studies before
commissioning one expensive panel. Use it to triage which concepts are
even worth taking to a recruited audience.
