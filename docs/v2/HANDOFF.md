# MiroFish v2 — handoff

Snapshot at the close of the no-fork revamp. Captures what was built, what
works, what was deferred, and how to drive it.

## What v2 is

A schema-direct, deterministic rebuild of the MiroFish pipeline that lives
under `backend/app/v2/` alongside (not on top of) the legacy LLM-driven
services. Same Flask process, same Neo4j, same Vue SPA — new path mounted
at `/api/v2/*` and surfaced in the browser at `/v2`.

The legacy stack is fully intact and still serves the original UI on `/`.
Nothing in the v1 path was deleted.

## Pipeline at a glance

```
study.json + CSVs ─► loader (no LLM) ─► Neo4j ─► persona projector (no LLM)
                                                     │
                                                     ▼
                              salience scorer (OpenAI embeddings, cached)
                                                     │
                                                     ▼
              MiniRunner ─► engagement gate per (agent, post)
                              │           │
                              │           └─► ManualAction(DO_NOTHING)  (no LLM)
                              │
                              └─► LLMAction ─► chat completion (per agent sampling)
                                                     │
                                                     ▼
                              metrics.py (deterministic) ─► narrator (1 LLM call,
                                                              quotes verbatim,
                                                              numbers from metrics)
                                                     │
                                                     ▼
                                            report.md  +  posts.jsonl  +  trace.jsonl
```

The engagement gate is the keystone of the no-fork story: agents below
their salience threshold or over their daily action cap never reach the
LLM. The orchestrator emits OASIS's existing `ManualAction(DO_NOTHING)`
for them — zero tokens spent.

## Layout

| Path | Role |
|---|---|
| `backend/app/v2/schemas/study.schema.json` | JSON-Schema for the star-schema study format |
| `backend/app/v2/loaders/study_loader.py` | Schema-validated CSV→typed-objects loader |
| `backend/app/v2/graph_writer.py` | Idempotent Neo4j writer keyed by `graph_id` |
| `backend/app/v2/persona.py` | Identity nodes → Persona (bio, style, engagement, sampling) |
| `backend/app/v2/salience.py` | OpenAI embedding scorer + SQLite cache |
| `backend/app/v2/runner.py` | Two-round orchestrator with engagement gate |
| `backend/app/v2/metrics.py` | Deterministic Reach/Engagement/AI/Clarity |
| `backend/app/v2/narrator.py` | Thin LLM narrator + offline fallback |
| `backend/app/v2/cli.py` | `python -m app.v2.cli run --study ... --out ...` |
| `backend/app/v2/api.py` | Flask blueprint at `/api/v2/*` |
| `backend/app/v2/fixtures/derive_bbc_panel.py` | tinytroupe-panel YAML → BBC seed CSVs |
| `backend/seeds/v2/bbc_panel/` | study.json + 5 CSVs (10 panelists) |
| `backend/tests/v2/` | 29 tests, all passing |
| `frontend/src/views/V2RunView.vue` | Single-page run UI with embedded graph picture |
| `frontend/src/views/V2GraphView.vue` | Standalone graph inspector at /v2/graph |
| `frontend/src/api/v2.js` | Typed client for `/api/v2/*` |

## HTTP endpoints (mounted at `/api/v2`)

| Method | Path | Use |
|---|---|---|
| POST | `/studies/from-disk` | Register a seed dir as a study |
| GET  | `/studies` | List registered studies |
| POST | `/runs` | Start a background run |
| GET  | `/runs` | List runs |
| GET  | `/runs/<id>` | Status + headline metrics + log |
| GET  | `/runs/<id>/report` | Rendered markdown |
| GET  | `/runs/<id>/posts` | posts.jsonl |
| GET  | `/runs/<id>/trace` | trace.jsonl (one line per gate decision) |
| GET  | `/runs/<id>/log` | streaming log entries |
| GET  | `/graphs` | List all `graph_id`s with per-label counts |
| GET  | `/graphs/<graph_id>` | Full nodes + edges as JSON; `?label=` to filter |

## How to drive it

### Browser

1. Open http://localhost:5001/v2 (or the Railway URL)
2. Register the seed: paste `seeds/v2/bbc_panel/study.json`, click **Register study**
3. Select the study, choose rounds (default 2)
4. Click **Run simulation**
5. After ~60 s the page shows: log → headline metrics → graph picture → markdown report

### CLI

```bash
python -m app.v2.cli run \
    --study seeds/v2/bbc_panel/study.json \
    --out   uploads/v2_runs/bbc_panel \
    --rounds 2
```

Outputs `posts.jsonl`, `trace.jsonl`, `metrics.json`, `run.json`, `report.md`.

### HTTP

```bash
curl -X POST http://localhost:5001/api/v2/studies/from-disk \
  -H 'content-type: application/json' \
  -d '{"path":"seeds/v2/bbc_panel/study.json"}'

curl -X POST http://localhost:5001/api/v2/runs \
  -H 'content-type: application/json' \
  -d '{"study_id":"bbc_panel_sherwood_s2_2024","rounds":2}'

# poll
curl http://localhost:5001/api/v2/runs/<run_id>

# read report
curl http://localhost:5001/api/v2/runs/<run_id>/report
```

## What works

- Path A end-to-end (CLI, HTTP, browser) on the BBC seed.
- 29/29 tests green: loader (10), persona (6), salience+runner (7), metrics+narrator (6).
- Real OpenAI run produced sensible output: reach 8/10, AI 72.6, clarity 1/10. Aisha & Jaden correctly skipped (lurkers); Marcus + Geoffrey 88; Margaret flagged unclear with verbatim seed quote "I lost the thread." Zero hallucinated panelists.
- Determinism: byte-identical re-load of the BBC seed verified by sha256 in `test_load_is_byte_deterministic`.
- Strict-raters-stay-strict invariant verified by `test_strict_raters_stay_strict`.
- Embedded graph picture: 28 nodes / 170 typed edges in the run results page, drag-to-rearrange, propensity-weighted edges.
- Auto-deploy to Railway on every push to `main`.

## What was deferred

- **OASIS-backed runner.** The MiniRunner exposes the same `LLMAction` vs `ManualAction(DO_NOTHING)` contract OASIS expects, so swapping is a small adapter. Not run locally because torch 2.9.1 has no x86_64-macOS wheel; can be exercised on Railway directly.
- **Wholesale legacy cleanup.** `services/zep_*`, `Config.ZEP_API_KEY`, the legacy Vue views — all listed in `backend/app/v2/README.md` as safe-to-delete in a follow-up PR once you're confident v2 covers everything you need.
- **Path B (LLM-assisted ingestion for unstructured docs).** The legacy `/api/graph/ontology/generate` route still works for this; we softened its forced-10-types rule. Recommended only when source data isn't in CSV.
- **Ground truth column in the report.** Slot left in the metrics module; not wired to any data source yet.
- **CI.** Repo has no GitHub Actions yet. Tests run locally with `pytest backend/tests/v2/` from the backend dir.

## Production state at handoff

- **Branch deployed:** `main` (auto-deploy on push)
- **Service:** `MiroFish` on Railway project `mirofish`
- **URL:** https://mirofish-production-98aa.up.railway.app
- **Routes live:** `/`, `/v2`, `/v2/graph`, all `/api/*` legacy routes, all `/api/v2/*` new routes
- **Volume mounted at:** `/app/backend/uploads` (1 GB) — runs persist across redeploys
- **Neo4j service** (`graphdb`): had a config drift on 2026-04-30 09:30 (builder set to RAILPACK with an image source). Reset by redeploy from the Railway dashboard once the user confirms graphdb is up.

## Decisions locked during the revamp

| Decision | Choice |
|---|---|
| Schema format | JSON Schema |
| Seed source | Derive from `tinytroupe-panel/data/panel_seed.yaml` |
| Branch | `revamp/no-fork` off `main`, then merged via PR #23 |
| Where simulation runs | Railway primarily (local Mac is x86_64; torch wheel issue) |
| Embedding provider | OpenAI `text-embedding-3-small` with SQLite cache |
| End-to-end target | Browser-based (option C) — Vue UI rewired |
| OASIS strategy | No fork. Engagement gating done at the orchestrator + persona prompt + per-agent sampling layers |
| Anti-rules block | Dropped (Path A inserts only what it's told; not needed) |
| Ground truth | Optional, post-hoc only — never feeds simulator |

## Commit log on `main` from this revamp

| SHA | Message |
|---|---|
| `bc4a342` | v2: schema-direct study loader + Neo4j writer (no LLM) |
| `87ddb67` | v2: persona projector, salience scorer, runner, metrics, narrator, CLI |
| `988b580` | v2: Flask API blueprint + minimal Vue UI for end-to-end runs |
| `e4a993b` | v2: soften ontology_generator's "must be exactly 10 types" rule + README |
| `261c1cc` | Merge pull request #23 from ucKaizen/revamp/no-fork |
| `bd15355` | v2: graph inspection endpoints + browser view at /v2/graph |
| `770d545` | v2: embed graph picture into the run results page |

Total: 4 PR commits + 1 merge + 2 follow-up commits ≈ 4,800 LOC across schema,
loader, writer, projector, scorer, runner, metrics, narrator, blueprint, two
Vue views, fixtures, seeds, tests, and docs.
