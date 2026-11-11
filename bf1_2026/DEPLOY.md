# Deploying BF1-2026

Runbook for a fresh deploy, and for moving an existing install to the current
version. Every command runs from the `bf1_2026/` directory on the server.

**Upgrading from an older version?** Read *Upgrading an existing install* at
the end first — the short version is: rebuild the image and recreate the
database, it only holds data that is re-downloaded in a minute.

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

### LAN-only deployment (Synology and similar)

This system is designed to run on your own hardware, on your own network. If
nothing outside the LAN can reach it, the API keys are optional:

```bash
REQUIRE_API_KEYS=false
API_BIND=0.0.0.0          # so other devices on the network reach :8000
```

`API_BIND=0.0.0.0` matters here: the default binds the API to loopback, which
on a NAS means only the NAS itself can reach port 8000. Other machines on the
network still get in through nginx on `:80` either way.

With the guards off, the API announces it at boot:

```
SECURITY: REQUIRE_API_KEYS=false — every write route and the entire admin
surface is UNAUTHENTICATED. Only valid if this port is truly unreachable
from the internet
```

Two things to keep in mind. Anyone who can reach the port can create races and
drivers — which poisons the ELO grid the strategies are derived from — so this
assumes you trust every device on the network. And if you later expose the box
(port forwarding, a reverse proxy, Synology QuickConnect, Tailscale Funnel),
set `REQUIRE_API_KEYS=true` and generate the two keys at that point.

A correct key is still enforced when one is set, so the flag can be flipped
without removing your keys.

---

### The pool site (optional, recommended)

```bash
POOL_BASE_URL=https://<the pool's address>     # exact, no trailing slash
POOL_EMAIL=<your login on the pool>
POOL_PASSWORD=<your password on the pool>
```

With these set the bot reads the pool's JSON API — the same one the pool's own
pages use — for the real standings (`/classificacao`), the betting deadline as
the pool has it, the live rules, and the bet you have already placed. It is
strictly read-only: nothing here ever submits or changes a bet.

The deadline matters more than it looks. The pool closes bets at *its* time for
each race, which is not always the official start: its 2026 Qatar entry is
three hours earlier. `/analysis` shows the pool's deadline once this is
configured.

These are real credentials for a real account, in a plain-text file. Keep
`.env` out of version control (it is git-ignored and docker-ignored) and
readable only by you: `chmod 600 .env`.

---

## 2. Build and start

```bash
docker compose up -d --build
```

`.dockerignore` keeps `.env` out of the image.

If a build ever fails at `pip install` with `ResolutionImpossible`, you are on
an old `requirements.txt`: it pinned `httpx==0.28.0`, which
`python-telegram-bot` does not accept, so the file could not be installed at
all. The current one resolves.

---

## 3. Apply the database schema

```bash
docker exec bf1_api alembic upgrade head
docker exec bf1_api alembic current      # should print e752dac48b63 (head)
```

The schema is owned by Alembic. The API does not create tables in production;
on boot it logs the schema revision, or an error telling you to run the
upgrade.

---

## 4. Load data

```bash
docker exec bf1_api python -m scripts.pipeline
```

One command: it seeds a starting grid and calendar, syncs every season of the
current regulation era plus the previous few from the results API
(`api.jolpi.ca`), and builds ratings and KPIs. Expect about three minutes and roughly
180 API requests — well inside the API's 500-per-hour limit.

The pipeline is a **sync**, so it is also how you refresh: run it again at any
time. It fills in finished rounds that are missing, follows calendar changes
(2026 gained a 23rd round mid-season), and rewrites the active roster from the
latest entry list. `--refresh` re-downloads rounds that are already stored.

Lines worth recognising in the output:

- `Synced 2026: 23 rounds on the calendar, 20 new result sets, 6 sprints …`
- `Roster synced from 2026 R20 Brazilian Grand Prix: 22 active drivers`
- `regulation era boundary — regressed ELO 50% toward 1500` — once, at 2025→2026.

---

## 5. Verify

```bash
docker exec bf1_api python -m scripts.healthcheck
```

Every line should be `PASS` (the pool lines are `SKIP` if you did not configure
it). The checks that catch the failures this system has actually had:

- `2026 results are up to date — 20 of 20 completed rounds stored`. Older
  versions never stored a single result of the season in progress.
- `One driver per team` and `Bet passes the pool's own validation`.
- `Pool site readable — 17 participants, you are Nº`.
- `Pool rules match the engine's defaults`. If the pool's rules ever change,
  this turns into a `SKIP` saying the live rules will be used instead.

Then ask the engine how it has been doing:

```bash
docker exec bf1_api python -m scripts.backtest 2026
```

and message the bot: `/analysis`, `/classificacao`.

If the API is exposed beyond your own network, also confirm the guards:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<host>:8000/api/v1/drivers/   # 401
curl -s -o /dev/null -w '%{http_code}\n' http://<host>:8000/docs                      # 404
```

---

## 6. Race-weekend routine

Nothing to run by hand — the scheduler container does it — but this is what
happens and when to look:

1. **After qualifying**, `refresh_grid` (every 20 minutes on a race weekend)
   stores the official starting grid from formula1.com as soon as it is
   published. Until then `/analysis` says the grid is *provisório* or the
   *ordem da classificação*.
2. **Run `/analysis` once the grid is official.** The grid is the model's main
   input; a recommendation made before qualifying is a rough first look.
3. **Place the bet on the pool site** before the deadline `/analysis` shows.
4. **After the race**, the twice-daily data update pulls the result, rebuilds
   the ratings and scores any bet you recorded with `/bet`.

To force any of it: `POST /api/v1/admin/refresh-grid`, `/update-data`,
`/settle-bets`, or run `python -m scripts.pipeline` again.

| Job | Schedule (UTC) | What it does |
|---|---|---|
| `daily_data_update` | 02:00, 14:00 | Sync results, rebuild ratings, settle bets, sync pool deadlines |
| `refresh_grid` | every 20 min, within 3 days of a race | Official starting grid |
| `check_race_alerts` | every 30 min | 24 h and 1 h Telegram reminders |
| `update_weather` | 08:00 daily | Forecasts for races within 7 days |

The scheduler runs in its own container (`bf1_scheduler`). To run it inside the
API process instead, set `RUN_SCHEDULER_IN_API=true` and do not start that
container.

---

## Upgrading an existing install

The database holds nothing that cannot be re-downloaded (results, ratings,
KPIs) plus any bets recorded through the bot. The schema gained stable
identifiers and per-result team attribution, and older installs created their
tables without Alembic, so the clean path is to recreate it:

```bash
git pull
docker compose down
docker volume ls | grep postgres           # find the Postgres volume's name
docker volume rm <that volume>             # drops the old database
docker compose up -d --build               # rebuild: dependencies changed
docker exec bf1_api alembic upgrade head
docker exec bf1_api python -m scripts.pipeline
docker exec bf1_api python -m scripts.healthcheck
```

Then add the `POOL_*` settings to `.env` (section 1) and restart the bot:
`docker compose up -d bf1_bot`.

What changes for you after the upgrade:

- The recommended bet is **5-5-3-1-1 on five teams plus an 11th-place pick**.
  The old default spread chips over six or seven drivers and had no 11th-place
  pick at all.
- `/classificacao` shows the pool's real table. `/leaderboard` still works as
  an alias.
- `/calendar` shows the real calendar. It used to be a hand-typed list with
  the wrong races.

---

## Still open

- **Sprint weekends have no dedicated bot flow yet.** The engine, the rules and
  the API (`?sprint=true`) handle a sprint as its own event; the bot switches
  to it when the pool's open event is a sprint, but that path has only been
  exercised on past data — 2026 has no sprint left to try it live.
- **Recorded bets cover the race only**, not the sprint.
- **The XGBoost model is unused.** It is kept as an experiment; see the README.

---

## Rollback

```bash
docker exec bf1_api alembic downgrade -1
git checkout <previous-tag> && docker compose up -d --build
```

Both migrations reverse cleanly (verified over repeated upgrade/downgrade
cycles).
