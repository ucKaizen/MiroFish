# Deploying MiroFish to Railway (two-service self-hosted stack)

This fork runs on Railway as **two services in one project**:

1. **mirofish** — the Flask backend + built Vue frontend (this repo, via `Dockerfile`)
2. **neo4j** — the graph database used by Graphiti (official `neo4j:5.24-community` Docker image)

MiroFish talks to Neo4j over Railway's private network (`neo4j.railway.internal`), so Neo4j stays internal and only the MiroFish service gets a public URL.

## Prerequisites

- Railway account with **Hobby plan** ($5/mo) — the free-tier 4 GB image cap rejects MiroFish's ~12 GB image
- An OpenAI-SDK-compatible LLM API key (OpenAI, Azure OpenAI, etc.)
- This fork connected to Railway via GitHub

## Step 1 — Add the Neo4j service

In your Railway project:

1. Click **+ New** → **Database** → **Add Neo4j**
   *(If Neo4j isn't in the database list, choose **+ New** → **Empty Service**, then **Source** → **Docker Image** → `neo4j:5.24-community`.)*
2. Rename the service to **`neo4j`** — MiroFish will address it as `neo4j.railway.internal`.
3. In **Variables**, set:

   | Variable | Value |
   |---|---|
   | `NEO4J_AUTH` | `neo4j/<pick-a-strong-password>` |
   | `NEO4J_PLUGINS` | `["apoc"]` |
   | `NEO4J_server_memory_heap_max__size` | `1G` |
   | `NEO4J_server_memory_pagecache_size` | `512m` |

4. In **Settings**:
   - Add a **volume** mounted at `/data` (1–5 GB) — persists graph data across redeploys.
   - No public domain needed. Leave the networking tab alone; the private DNS is automatic.

Wait for Neo4j to come up green before adding the next service.

## Step 2 — Add the MiroFish service

1. **+ New** → **GitHub Repo** → select `ucKaizen/MiroFish`.
2. Railway detects the `Dockerfile` + `railway.toml`. First build takes 8–12 min.
3. In **Variables**, set:

   | Variable | Value |
   |---|---|
   | `LLM_API_KEY` | `sk-...` (your OpenAI key) |
   | `LLM_BASE_URL` | `https://api.openai.com/v1` |
   | `LLM_MODEL_NAME` | `gpt-4o-mini` |
   | `NEO4J_URI` | `bolt://neo4j.railway.internal:7687` |
   | `NEO4J_USER` | `neo4j` |
   | `NEO4J_PASSWORD` | the password you set in Step 1's `NEO4J_AUTH` |
   | `APP_LOCALE` | `en` |
   | `OASIS_DEFAULT_MAX_ROUNDS` | `3` (keeps first-run spend low) |
   | `REPORT_AGENT_MAX_TOOL_CALLS` | `2` |
   | `REPORT_AGENT_MAX_REFLECTION_ROUNDS` | `1` |

   **Remove** any stale variables from a previous deploy attempt — particularly `ZEP_API_KEY`, `LLM_BOOST_*`. They're ignored now.

4. In **Settings**:
   - Add a **volume** mounted at `/app/backend/uploads` (1 GB) — persists uploaded seed files and simulation state.
   - Under **Networking**, click **Generate Domain** to get a `*.up.railway.app` URL.
   - `PORT` is injected automatically — the Dockerfile reads it.

## Step 3 — Verify

1. Both services should go green in the Railway dashboard within ~12 min total.
2. Hit `https://<your-domain>/health` — should return `{"status":"ok","service":"MiroFish Backend"}`.
3. Open the root URL — the MiroFish UI should load.
4. Upload a seed document and kick off a graph build. Watch the service logs in Railway for confirmation.

## Step 4 — Cost control (important on Hobby)

Both services idle at ~0.3 vCPU and ~0.5–1 GB RAM. At 24/7 uptime expect roughly $15–25/mo — well over the $5 Hobby floor. To stay near $5:

1. In **mirofish** → **Settings** → **Deploy** → enable **Auto-sleep after idle**.
2. In **neo4j** → same. Set the sleep threshold to 10–15 min of no traffic.
3. Accept a cold-start delay (~20–40 s) the first time you hit a sleeping service. Subsequent calls are instant until idle again.

This keeps the bill close to the floor if you only use MiroFish in bursts rather than 24/7.

## Troubleshooting

- **MiroFish logs show `Could not connect to Neo4j`**: check that the Neo4j service is running and that `NEO4J_URI` exactly matches the Neo4j service name (`neo4j` → `bolt://neo4j.railway.internal:7687`). Service name and hostname must match.
- **Build fails with image size error**: confirm the Hobby plan is active. The free tier's 4 GB cap still applies until you upgrade.
- **Neo4j fails with "invalid memory settings"**: lower `NEO4J_server_memory_heap_max__size` to `512m` and `NEO4J_server_memory_pagecache_size` to `256m` if you picked a small Neo4j instance.
- **MiroFish returns 500 with `OPENAI_API_KEY` error**: double-check `LLM_API_KEY` is set (MiroFish mirrors it into `OPENAI_API_KEY` at startup, but only if it's non-empty).

## Rolling back

Each successful build appears in the Railway service's **Deployments** tab. Click any past deployment to redeploy it — one-click rollback.

## License reminder

MiroFish is AGPL-3.0. Running unmodified is fine. If you fork and modify, and your Railway URL is accessible to anyone else, you owe them the modified source under AGPL terms.
