# Deploying BF1-2026

Runbook for a fresh deploy or a redeploy after the 2026 review. Every command
runs from the `bf1_2026/` directory on the server.

---

## 1. Configure the environment

```bash
cp .env.template .env
```

Then edit `.env`. Four things must be set — the app now **refuses** guarded
routes rather than falling back to a default:

```bash
# Generate three independent random values
openssl rand -hex 32   # -> SECRET_KEY
openssl rand -hex 32   # -> WRITE_API_KEY
openssl rand -hex 32   # -> ADMIN_API_KEY
```

| Variable | Why it matters |
|---|---|
| `POSTGRES_PASSWORD` | Must match the password embedded in `DATABASE_URL`. It appears twice; a mismatch fails every query with `InvalidPasswordError`. |
| `WRITE_API_KEY` | Guards `POST /races`, `/drivers`, `/bets` and `/simulate`. Sent as `X-API-Key`. |
| `ADMIN_API_KEY` | Guards every `/api/v1/admin/*` route. Sent as `X-Admin-Key`. Deliberately separate from the write key. |
| `ALLOWED_ORIGINS` | Set a real origin list. `*` disables credentialed CORS. |

Leave `ENABLE_DOCS=false` in production — Swagger publishes the admin surface
to anonymous visitors.

A key that is blank, shorter than 16 characters, or still a placeholder is
treated as unconfigured: guarded routes return **503** and the reason is logged
at boot. That is deliberate — it fails closed rather than accepting an empty
header.

### Running without API keys

If the deployment is genuinely unreachable from the internet — bound to
loopback, or behind a private network or VPN — you can turn the guards off:

```bash
REQUIRE_API_KEYS=false
```

Every write route and the whole admin surface is then open to anything that can
reach the port, and the API says so at boot:

```
SECURITY: REQUIRE_API_KEYS=false — every write route and the entire admin
surface is UNAUTHENTICATED. Only valid if this port is truly unreachable
from the internet
```

**A public hostname does not qualify**, including a `*.ondigitalocean.app`
URL — those are published to the internet by default. If the app answers on a
public address, anyone who finds it can create races and drivers (which poisons
the ELO grid the strategies are built from) and can pin the container's CPU
through `/simulate`. Generating two keys takes about ten seconds and is the
better trade unless you are certain the port is private.

A correct key still wins when one is set, so you can turn this off temporarily
without removing your keys.

---

## 2. Build and start

```bash
docker compose up -d --build
```

`.dockerignore` now keeps `.env` out of the image. If you built before this
change, rebuild — the old image has your secrets baked into a layer.

---

## 3. Apply the database schema

The schema is owned by Alembic. `alembic.ini` now lives at the project root, so
this works from `/app`:

```bash
docker exec bf1_api alembic upgrade head
```

Verify:

```bash
docker exec bf1_api alembic current      # should print a revision, not blank
```

The API no longer creates tables in production. On boot it checks the schema
and logs the current revision; if it cannot, it logs an error telling you to
run the upgrade. It will not silently serve a tableless database.

---

## 4. Load the 2026 season

```bash
docker exec bf1_api python -m scripts.seed
```

Expected: `Seed complete: 11 teams, 22 drivers, 22 new 2026 races`.

This is safe to re-run — it updates existing rows rather than duplicating, and
the new unique constraints (`uq_races_season_round`, `uq_race_results_race_driver`,
and the rest) now enforce that at the database level.

---

## 5. Ingest history and build intelligence

```bash
docker exec bf1_api python -m scripts.pipeline 2022 2026
```

Starting at **2022** is deliberate: 2026 is a regulation reset, and the KPI
layer scopes to the current era anyway. Ingesting further back mostly adds rows
the model will down-weight or ignore.

This runs seed → ingest → ELO/KPI build → model training. It talks to
`api.jolpi.ca` (the Ergast successor — `ergast.com` was frozen after 2024), so
the server needs outbound HTTPS to that host.

Watch for these in the logs:

- `Truncated response from ...` — raise `PAGE_LIMIT` in `f1_api_fallback.py`.
- `no results returned — leaving it un-ingested` — a transient failure. Re-run
  the pipeline; that round will be retried rather than being permanently
  skipped.
- `regulation era boundary — regressed ELO 50% toward 1500` — expected once, on
  the 2025→2026 transition.

---

## 6. Verify

```bash
docker exec bf1_api python -m scripts.healthcheck
```

All checks should pass. `Historical results ingested` failing means step 5 did
not reach the data source.

Then confirm the API actually serves data — this is the check that matters most
after the review, because the previous build returned **500 on every
database-backed endpoint** while `/health` stayed green:

```bash
curl -fsS https://yourdomain.com/health
curl -fsS https://yourdomain.com/api/v1/races/ | head -c 400
```

`/api/v1/races/` must return a JSON array of 22 races. A 500 here means the
deploy did not pick up the new code.

Check the guards are live:

```bash
# expect 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://yourdomain.com/api/v1/drivers/
# expect 200
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Admin-Key: $ADMIN_API_KEY" \
  https://yourdomain.com/api/v1/admin/dashboard
# expect 404 — docs must not be public
curl -s -o /dev/null -w '%{http_code}\n' https://yourdomain.com/docs
```

---

## 7. Ongoing

The scheduler runs in its own container (`bf1_scheduler`) and is no longer
started inside the API as well — that made every job fire twice. All times UTC:

| Job | Schedule | What it does |
|---|---|---|
| `daily_data_update` | 02:00 daily | Ingests finished races, rebuilds ELO and KPIs |
| `update_weather` | 08:00 daily | Forecasts for races within 7 days |
| `check_race_alerts` | every 30 min | 24h and 1h Telegram reminders |
| `weekly_retrain` | Mon 03:00 | Full model retrain |

To run the API and scheduler in one process instead, set
`RUN_SCHEDULER_IN_API=true` and do not start the scheduler container.

---

## Still open

Not addressed by this deploy — see the review for detail:

- **Training has look-ahead bias.** Features use current aggregates and the
  scaler is fitted before the time split, so reported metrics are optimistic.
  The Monte Carlo layer does not depend on the trained model.
- **Team KPIs follow the current roster.** `RaceResult` has no `team_id`, so a
  driver's history is attributed to whichever team they drive for now.
- **`POST /bets` is API-only.** The Telegram bot writes to the database
  directly and does not go through it.

---

## Rollback

```bash
docker exec bf1_api alembic downgrade -1
git checkout <previous-tag> && docker compose up -d --build
```

The initial migration drops its enum types on downgrade, so
downgrade-then-upgrade is clean; verified over three cycles.
