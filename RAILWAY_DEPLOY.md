# Deploying MiroFish to Railway

This fork is pre-configured for Railway: prod-mode Dockerfile, Vite preview serving built assets, Flask backend on an internal port, and a healthcheck. Follow the steps below.

## Prerequisites

- A Railway account (https://railway.app)
- An OpenAI-SDK-compatible LLM API key (OpenAI, Azure OpenAI, Qwen via Alibaba Bailian, or any compatible gateway)
- A Zep Cloud API key (https://app.getzep.com) — free tier is enough to start

## 1. Create the service

1. Railway dashboard -> **New Project** -> **Deploy from GitHub repo**
2. Pick `ucKaizen/MiroFish`
3. Railway detects the `Dockerfile` and `railway.toml` automatically

## 2. Configure environment variables

In the service's **Variables** tab, add:

| Variable | Example | Notes |
|---|---|---|
| `LLM_API_KEY` | `sk-...` | Your LLM provider key |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-SDK-compatible base URL |
| `LLM_MODEL_NAME` | `gpt-4o-mini` | Any model your key can reach |
| `ZEP_API_KEY` | `z_...` | From getzep.com |

Optional "boost" model (set all three or none):

| Variable | Notes |
|---|---|
| `LLM_BOOST_API_KEY` | Used for faster/cheaper auxiliary calls |
| `LLM_BOOST_BASE_URL` | OpenAI-SDK-compatible |
| `LLM_BOOST_MODEL_NAME` | e.g. `gpt-4o-mini` |

`PORT` is injected by Railway automatically — do not set it manually.

## 3. Add a persistent volume

Uploaded seed documents live in `/app/backend/uploads`. Without a volume they disappear on every redeploy.

Service **Settings** -> **Volumes** -> **+ New Volume**:
- Mount path: `/app/backend/uploads`
- Size: 1 GB is plenty to start

## 4. Expose the service publicly

Service **Settings** -> **Networking** -> **Generate Domain**.

Railway picks the port from `$PORT` (set to 3000 by default in the Dockerfile). The public URL will look like `https://mirofish-production-xxxx.up.railway.app`.

The Flask backend on port 5001 stays internal. The Vite preview server proxies `/api/*` and `/health` to it.

## 5. Deploy

Railway auto-deploys on push to `main`. First build takes ~5-10 minutes (installs Node + Python deps + builds the Vite bundle).

Watch the build logs; the service is ready once you see the Flask banner and the Vite preview `Local:` line.

## Notes and gotchas

- **License**: MiroFish is AGPL-3.0. If you modify the code and expose it over a network (including to colleagues inside NIQ), you owe users the modified source. Unmodified is fine.
- **LLM cost**: the upstream README warns against long sims early on. Keep rounds under 40 until you calibrate spend.
- **Data handling**: every seed document gets chunked and sent to whichever LLM provider you configure, plus Zep Cloud for agent memory. Avoid feeding client data on a shared/public instance; use a sanitized or synthetic seed first.
- **Cold starts / long sims**: Railway has no per-request timeout (unlike Cloud Run), so long multi-round simulations are fine. The container stays warm as long as the service is running.
- **Scaling**: the default plan is single-instance. Horizontal scaling is not supported by this app (simulation state lives in-process and on the local volume).

## Rolling back

If a deploy breaks, Railway's **Deployments** tab lets you redeploy any previous successful build with one click.
