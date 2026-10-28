# Deploying LearnCode

Answers to the open questions in `TODO.md`, in the order they were asked.

---

## 1. Where does the DeepSeek key live in production?

**In a `.env` file on the server** (`/opt/learncode/.env`), mounted into the container by
`docker-compose.yml`'s `env_file`. It never enters the image (it is `.gitignore`d and not copied by
the `Dockerfile`) and never passes through GitHub Actions.

- **Sign-in is checked at startup**: `ensure_auth_configured()` (`backend/app/main.py` → `auth.py`)
  aborts the process if the OAuth client id/secret or the HTTPS base URL is missing, so a misconfigured
  deploy fails immediately instead of showing "sign-in is not configured" to every visitor.
- The **model key is not** checked at startup — a missing one fails on the learner's first turn, not on
  boot. That is a gap worth closing (see the checklist at the end of this file).
- Rotate it by editing that file and running `docker compose up -d` -- no rebuild.
- A copy of the current key sits in `backend/.env`. **If that key is real, rotate it**: it has been in
  a working tree and possibly in shell history.

Better alternatives, in order of preference:

| Option | When to use it |
|---|---|
| `.env` on the host | This project, now. One host, one file, `chmod 600`. |
| Host/cloud secret store injected as an environment variable | The moment there is more than one host. AWS SSM Parameter Store (free tier for standard parameters) or Secrets Manager (~$0.40/secret/month). The app needs no code change: it reads plain environment variables (`backend/.env.example` lists the names). |
| Secrets manager + short-lived credentials | Multi-tenant or compliance-sensitive. Overkill for a single-instance learning app. |

---

## 2. GitHub user token — is it needed, and past 100 users?

Not needed now, **but it must be a repository-level token**, not each learner's own.

The code (`backend/app/github_lookup.py:_headers`) already prefers the signed-in learner's OAuth token
and falls back to `GITHUB_TOKEN`/`GH_TOKEN` from the environment. The limits that matter:

- **Unauthenticated**: 60 requests/hour **per IP**. Behind one server, every learner shares that —
  roughly 30 turns/hour before GitHub starts answering 403.
- **With any token**: 5,000 requests/hour. A turn uses at most 2 searches + 3 README reads
  (`LEARNCODE_GITHUB_SEARCHES_PER_TURN` / `READMES_PER_TURN`), so 5,000 supports ~1,000 turns/hour.

So **yes, set `GITHUB_TOKEN` in production**, and no, it is not a scaling wall at 100 users. A
fine-grained, read-only public-repo token needs **no scopes** — it exists only to raise the limit.
Past a few thousand turns/hour, use a GitHub App installation token instead of a personal token.

---

## 3. The pipeline (fully automated, no cloud portal)

Two workflows:

- **`.github/workflows/ci.yml`** — every push and PR: backend suite, type-checked frontend build, and a
  Docker build that is then **started and health-checked**, so "the image builds" and "the image runs"
  are both proven.
- **`.github/workflows/deploy.yml`** — push to `main`: test → push image to GHCR → ssh to the host →
  pull the commit-pinned image → restart → wait for `healthy`. A failed health check fails the deploy
  and prints the container's logs.

### One-time setup (the only manual steps)

1. Create the host (Hetzner/EC2/GCP — see costs below) and install Docker:
   `curl -fsSL https://get.docker.com | sh && usermod -aG docker $USER`
2. Write `/opt/learncode/.env` on the host from `backend/.env.example` (model key, OAuth app,
   `LEARNCODE_HOST=0.0.0.0`, `LEARNCODE_DATA_DIR=/data`). `chmod 600`.
3. Add the repository **secrets**:
   `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (a dedicated ed25519 key, not your personal one).
   Add the repository **variable** `DEPLOY_ENABLED=true`.
4. Create an OAuth app at github.com/settings/developers with the callback
   `https://<your-domain>/api/auth/callback`.

Nothing above happens in a cloud console — all of it is a shell or a GitHub settings page.

### Rolling back

Every image is also tagged with its commit. On the host:

```sh
cd /opt/learncode
LEARNCODE_IMAGE=ghcr.io/<owner>/learncode:<old-sha> docker compose -f docker-compose.yml -f deploy-compose.prod.yml up -d
```

---

## 4. Cheaper / more scalable alternatives

The app is **single-process by design** right now — the progress feed, per-session turn locks and rate
limits are in process memory (`backend/app/progress.py`, `routes.py`, `ratelimit.py`), and the
`Dockerfile` pins `--workers 1`. That means **one container, scaled up, not out**. It is not a code
limitation to fix by adding workers: shipping a second worker would make progress polling land on the
wrong process and turns look dead.

| Option | Monthly | Notes |
|---|---|---|
| **One small VPS** (Hetzner CX22, 2 vCPU / 4 GB) | **~€4** | The pragmatic choice. Handles the default 4 concurrent turns with room. Cheapest per unit of real work. |
| Fly.io / Railway / Render | $5–20 | Managed deploys, but a long-running turn (20–60s) can hit their per-request timeouts, and in-memory state needs sticky sessions or a single instance. |
| AWS ECS Fargate (0.5 vCPU / 1 GB) | ~$15–20 | Good if you already live in AWS. Needs an ALB (~$16/mo more) or a public IP. |
| AWS Lambda | n/a | **No**: a turn outlives the 15-minute ceiling only in theory, but the in-memory progress feed and session locks cannot survive between invocations. |
| EC2 t4g.small | ~$12 | ARM, and the image is multi-arch safe (python/node/alpine-free), but no cheaper than the VPS above. |

**Scaling past one box** requires three changes first, in this order:
1. Move the progress feed to Redis (pub/sub or a stream) — it is the only state a *polling* client needs.
2. Move turn locks and rate limits to Redis or Postgres rows.
3. Then raise `LEARNCODE_MAX_CONCURRENT_TURNS` and run N containers behind an ALB/nginx.

The database is already ready for that: `LEARNCODE_DATABASE_URL` accepts a Postgres URL, so only the
three in-memory pieces above block horizontal scaling.

**Costs to budget beyond the host:** DeepSeek API (~$0.14 per 1M input tokens — a turn is a few
thousand tokens, so a *few cents per learner per session*), and optional object storage for backups.

---

## 5. Remaining gaps, in priority order

Fixed for this deploy:

- The retry that could strand a turn (`raise last` when `last` was `None`) and, underneath it, the real
  cause: seedagent swallows the malformed-reply error, so a model repeating the broken tool call ran to
  `AGENT_MAX_STEPS` and the turn "succeeded" with no page. `_streamed_send` now retries an empty reply,
  and every attempt is bounded by `LEARNCODE_MAX_STEPS_PER_ATTEMPT` (default 24) so one bad reply costs
  a few calls instead of 300.
- No rate limiting on model-spending routes → `app/ratelimit.py`, applied to `/start`, `/action`,
  `/ask` and `/clarify`.
- No `/health` → added, and it checks the database.
- CORS hardcoded to localhost → `LEARNCODE_CORS_ORIGINS`.
- `pytest` would hang forever instead of failing → `--timeout=120` (needs `pytest-timeout`, now in the
  dev dependencies).
- A brittle test that asserted on the `anthropic` SDK's *module docstring* — it could never fail for a
  reworded error. It now drives the SDK's real stream parser.

Still open (all are **single-instance** blockers, not correctness bugs):

1. **Progress feed is in memory.** A redeploy mid-turn loses the feed and the browser polls forever.
2. **Rate-limit windows are in memory.** Restarting the container resets every learner's budget — fine
   for one box, wrong once there are replicas.
3. **No automated backups of the volume.** `learncode.db` plus `backend/data/workspaces/` are the whole
   product; a `docker run --rm -v learncode-data:/d alpine tar czf - -C /d . > backup.tgz` on a cron
   would cover it.
4. **`backend/data/` holds a real 1.2 MB `learncode.db` and a 1.3 MB `github_cache.json`** from local
   development. They are git-ignored, but make sure the deploy does not copy them into the image (the
   `Dockerfile` copies `backend/` wholesale — add a `.dockerignore` if that matters).

