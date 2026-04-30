# MiroFish v2 — schema-direct, no-fork

This package is the no-fork rebuild of the MiroFish pipeline. It runs in
parallel with the legacy `app/services/*` modules; nothing in v1 has been
deleted, so the old `/api/graph/*`, `/api/simulation/*`, `/api/report/*`
routes still serve the existing UI. The new path is mounted under
`/api/v2/*` and surfaced in the browser at `/v2`.

## Layout

| Module | Role |
|---|---|
| `schemas/study.schema.json` | JSON-Schema for star-schema studies (identity table + dimensions + brief + engagement defaults) |
| `loaders/study_loader.py` | Path A loader — JSON-Schema validated, hard-fails on dup keys / dangling FKs / type/enum violations |
| `graph_writer.py` | Idempotent Neo4j writer keyed by `graph_id`; one Cypher MERGE per `(label, edge_type)` group |
| `persona.py` | Deterministic projector — Identity node → Persona with bio, style, engagement profile, sampling config, action menu |
| `salience.py` | OpenAI embedding-based salience scorer + SQLite cache. Cosine + genre/slot propensity boost |
| `runner.py` | OASIS-shaped `MiniRunner` — engagement gate emits `LLMAction` vs `ManualAction(DO_NOTHING)` per (agent, round). Studyable trace log on every decision |
| `metrics.py` | Pure compute — Reach, Engagement, Appreciation Index, Clarity risk + per-panelist row from post text |
| `narrator.py` | Thin LLM wrap over the metrics + verbatim quotes; offline fallback identical except for narrative paragraph |
| `cli.py` | `python -m app.v2.cli run --study ... --out ...` |
| `api.py` | Flask blueprint at `/api/v2/*` |
| `fixtures/derive_bbc_panel.py` | One-shot — converts tinytroupe-panel/data/*.yaml → seeds/bbc_panel/*.csv |

## End-to-end CLI

```bash
python -m app.v2.cli run \
    --study seeds/bbc_panel/study.json \
    --out   uploads/v2_runs/bbc_panel \
    --rounds 2
```

## End-to-end HTTP

```bash
# 1. register a study from disk
curl -X POST http://localhost:5001/api/v2/studies/from-disk \
    -H 'content-type: application/json' \
    -d '{"path":"seeds/bbc_panel/study.json"}'

# 2. start a run
curl -X POST http://localhost:5001/api/v2/runs \
    -H 'content-type: application/json' \
    -d '{"study_id":"bbc_panel_sherwood_s2_2024","rounds":2}'

# 3. poll status until done
curl http://localhost:5001/api/v2/runs/<run_id>

# 4. read the report
curl http://localhost:5001/api/v2/runs/<run_id>/report
```

## End-to-end browser

`http://localhost:5001/v2` — single-page workflow.

## Engagement model (no-fork — does not modify OASIS)

Three layered gates, all controlled from the orchestrator:

1. **Persona prompt** — every system prompt encodes "ignore most posts" with
   explicit `DO_NOTHING` preference. Set in `persona.system_prompt()`.
2. **Action menu per persona** — low base-rate personas only see
   `[DO_NOTHING, LIKE_POST]`; high base-rate see the full menu.
3. **Engagement gate** — for each (agent, post): salience scored from
   embedding cosine + genre/slot propensity. Below threshold or daily cap →
   the LLM is never called; we emit `ManualAction(DO_NOTHING)`. This is the
   keystone of the no-fork engagement story.

When this runs on Railway with OASIS installed, the only swap is replacing
the `MiniRunner` body with a call into `OasisEnv.step(actions=...)`. The
engagement gate, persona projection, salience scorer, and metrics module
all stay identical.

## What got deprecated in v1 but is still wired

The legacy LLM-driven pipeline still works for backward compatibility but
its known defects (forced 10 entity types, programme/region/role
hallucinations, `RELATES_TO`-only edges, identity duplication) make it
unsuitable for new studies. Use Path A through `/api/v2/*` instead.

Modules still present, safe to delete in a follow-up cleanup once no
endpoint references them:

- `app/services/zep_compat.py` — Zep→Graphiti shim. Path A talks to Neo4j
  directly via `app/v2/graph_writer.py`.
- `app/services/zep_entity_reader.py` — read-side filter for the LLM-typed
  graph. Path A graphs are clean by construction so no filter is needed.
- `app/services/zep_graph_memory_updater.py` — appended simulation events
  back into the Zep-shaped graph. Path A writes a typed event log instead.
- `app/services/zep_tools.py` — Zep API surface no longer used.
- `app/utils/zep_paging.py` — Zep pagination helper.

After deleting those, also drop:
- `Config.ZEP_API_KEY = 'neo4j-local'` alias in `app/config.py`
- All `Config.ZEP_API_KEY` checks in `app/api/graph.py` and
  `app/api/simulation.py`

The fixed-10-entity-type rule in `app/services/ontology_generator.py` has
already been softened on this branch — the prompt now says "1 to 10 types,
sized to the data" and tells the LLM not to default-include the
`Person`/`Organization` catch-alls.
