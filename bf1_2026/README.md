# BF1-2026 Quantitative System

A betting assistant for the BF1 Formula 1 pool. Given a starting grid it
simulates the race, then recommends the bet with the highest expected score
under the pool's rules: how to split the 15 chips, and who to name for 11th
place.

```
 results API ─┐                              ┌─ Telegram bot  (/analysis, /classificacao …)
 (Jolpica)    ├─► Postgres ─► race simulator ─► exact optimiser ─┤
 formula1.com ┘   form, ELO,   (calibrated on    (pool rules)    └─ REST API
 (starting grid)  reliability   real results)
 pool site ───────► rules, deadlines, standings, your bets (read-only)
```

**Stack:** Python 3.12 · FastAPI · PostgreSQL · NumPy · Telegram Bot · Docker

## The pool's rules, as implemented

Encoded once, in `app/rules/pool_rules.py`, from the pool's published
regulation and the machine-readable rules its API returns. The scorer was
checked against bets the pool itself scored — 25 of 26 match exactly.

| Rule | |
|------|--|
| 15 chips per event, at most 5 on one driver | |
| At least 5 drivers, **never two from the same team** | |
| Score | `chips × driver's points + 11th-place bonus − retirement penalties` |
| Driver's points | 25-18-15-12-10-8-6-4-2-1 (sprint: 8-7-6-5-4-3-2-1) |
| 11th place | name the driver who finishes 11th: **+50** |
| Retirement | **−10 per retired driver, flat** — not per chip. A DNS is not penalised |
| Sprint | a separate event, whole score doubled (maximum 304 vs 332 for a race) |
| Deadline | bets close at the start of the session |
| Season | worst event discarded; champion / runner-up / constructor bonuses |

Under these rules the best bet always has the shape **5-5-3-1-1** on five
drivers of five teams. Spreading chips more thinly lowers the average *and* the
bad-case score, so the four "risk profiles" almost never change the answer.

## What the model is, and how good it is

`app/models_ml/race_model.py` — a grid-anchored simulation:

```
score = 0.8·grid + 0.2·recent form − rating/300 + noise (+ occasional incident)
```

ranked among the cars still running, with retirements drawn from each
team/driver's own reliability record in the current regulation era. Every
parameter was fitted on 2023-25 and checked out-of-sample on 2026.

`python -m scripts.backtest 2023 2026` replays 114 real events, asking the
engine for a bet using only what was known before each start and scoring it
with the pool's rules:

| | 2026 total (26 events) | 2023-26 per event |
|--|--:|--:|
| Pool maximum | 8,464 | 325 |
| **This engine** | **5,234** | **212.8** |
| Baseline: bet the starting grid | 5,494 | 213.6 |
| Previous engine, default profile | 4,356 (chips only) | — |
| Previous engine, most aggressive | 5,076 (chips only) | — |

Read that honestly: **the starting grid carries almost all of the signal.** No
model variant beat "bet the grid" by more than one standard error. What the
engine adds is correctness under the rules (chip shape, one per team, flat
penalty, reliability, the 11th-place pick) and calibrated probabilities — not
a secret edge over the grid. The practical consequence is in the bot's own
advice: re-run `/analysis` after qualifying.

The 11th-place pick is a genuine lottery: the best candidate is right about
one time in ten.

## Data sources

| Source | Used for | Notes |
|--------|----------|-------|
| Jolpica (`api.jolpi.ca`, the Ergast successor) | calendar, results, sprints, qualifying | season-level paginated requests, ~180 for five seasons (limit: 500/hour) |
| formula1.com | the **official starting grid** before the race | the results API only has it afterwards; URLs resolved from the season index |
| The pool site | rules, betting deadlines, standings, your own bets | authenticated JSON API, **read-only** — it never places a bet |
| OpenWeatherMap | forecast (optional) | |

## Installation

```bash
cp .env.template .env            # then edit it — see DEPLOY.md
docker compose up -d --build
docker exec bf1_api alembic upgrade head
docker exec bf1_api python -m scripts.pipeline
docker exec bf1_api python -m scripts.healthcheck
```

`DEPLOY.md` has the full runbook, including the Synology / LAN-only setup and
how to move an existing database to this version.

### Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
docker compose up -d postgres redis
alembic upgrade head
python -m scripts.pipeline
uvicorn app.main:app --reload --port 8000     # API
python -m app.telegram.bot                    # bot (separate terminal)
python -m pytest tests/ -q
```

## Scripts

| Command | What it does |
|---------|--------------|
| `python -m scripts.pipeline` | seed → sync results → build ratings/KPIs |
| `python -m scripts.ingest [start end] [--refresh]` | sync calendar and results from the API; safe to re-run |
| `python -m scripts.build_intelligence` | replay ratings and KPIs from stored results |
| `python -m scripts.backtest [start end] [--events]` | score the engine against real results |
| `python -m scripts.healthcheck` | verify data, a sample bet, and the pool connection |

## Telegram bot

| Command | |
|---------|--|
| `/analysis` | recommended bet for the next event: chips, 11th place, expected score, deadline, and your current bet on the pool site |
| `/simulate` | every driver's odds: win, podium, top 10, 11th, retirement |
| `/classificacao` | the pool's real standings, your position and the gaps around you |
| `/calendar` | the season calendar, from the database |
| `/bet` | record the recommended bet here so it is scored after the race (it does **not** place it on the pool site) |
| `/settings` | risk profile |

## API

| Method | Endpoint | |
|--------|----------|--|
| GET | `/health` | liveness |
| GET | `/api/v1/races` · `/races/{id}` | calendar |
| GET | `/api/v1/races/{id}/strategy?sprint=false` | recommended bet |
| GET | `/api/v1/drivers` | active grid |
| POST | `/api/v1/predictions/race/{id}/simulate` | whole-field odds 🔑 |
| POST | `/api/v1/bets` | record a bet 🔑 |
| GET | `/api/v1/bets/user/{telegram_id}` · `/bets/leaderboard` | recorded bets |
| POST | `/api/v1/admin/update-data` · `/refresh-grid` · `/settle-bets` · `/recalculate-elo` | operations 🛡 |

🔑 needs `X-API-Key`, 🛡 needs `X-Admin-Key` (unless `REQUIRE_API_KEYS=false`
on a private network).

## Scheduled jobs (UTC)

| Job | When | |
|-----|------|--|
| `daily_data_update` | 02:00, 14:00 | sync results, rebuild ratings, settle recorded bets, sync pool deadlines |
| `refresh_grid` | every 20 min, race weekends only | fetch the official starting grid |
| `check_race_alerts` | every 30 min | 24 h and 1 h reminders |
| `update_weather` | 08:00 | forecasts for races within 7 days |

## What is deliberately not here

* **No trained ML model on the prediction path.** The XGBoost model
  (`app/models_ml/xgboost_model.py`) was trained with look-ahead features and
  is kept only as an experiment; the Bayesian model was removed. The
  simulator's handful of parameters are checked by the backtest instead.
* **No automatic bet placement.** Reading the pool is automated; betting on it
  is yours to do.

## Guardrails

`app/utils/guardrails.py` clamps model outputs to the physically possible
(probabilities in [0, 1], positions within the grid, 1-3 pit stops in a dry
race) and logs whenever it had to correct something.
