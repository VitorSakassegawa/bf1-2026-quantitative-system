# BF1-2026 Quantitative System

A quantitative predictive analysis engine for the BF1-2026 championship, functioning as a trading-style model applied to F1 betting.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Telegram    │────▶│   FastAPI     │────▶│  PostgreSQL  │
│  Bot         │     │   API         │     │  Database    │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
       ┌───────────┐ ┌──────────┐ ┌──────────┐
       │  XGBoost   │ │ Bayesian │ │  Monte   │
       │  Model     │ │ Model    │ │  Carlo   │
       └───────────┘ └──────────┘ └──────────┘
              │            │            │
              └────────────┼────────────┘
                           ▼
                    ┌─────────────┐
                    │  Token       │
                    │  Optimizer   │
                    └─────────────┘
```

**Stack:** Python 3.12 · FastAPI · PostgreSQL · Redis · XGBoost · PyMC · Telegram Bot · Docker

## BF1-2026 Rules Implemented

| Rule | Implementation |
|------|---------------|
| 15 total tokens | Hard constraint in optimizer |
| Max 5 per driver | Hard constraint in optimizer |
| Min 5 drivers from different teams | Hard constraint in optimizer |
| Sprint = 2× points | Sprint multiplier in EV calculator |
| DNF = -10 points | Penalty in Monte Carlo + EV |
| Fastest lap bonus (+1 if top 10) | Included in points calculation |
| Betting deadline (1h before race) | Enforced in bet API + bot |

## Installation

### Local Development

```bash
# Clone
git clone <repo-url>
cd bf1_2026

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment variables
cp .env.template .env
# Edit .env with your values

# Start services (PostgreSQL + Redis)
docker compose up -d postgres redis

# Run API
uvicorn app.main:app --reload --port 8000

# Run Telegram bot (separate terminal)
python -m app.telegram.bot
```

### Docker (Production)

```bash
cp .env.template .env
# Edit .env with production values
docker compose up -d
```

## Configuration (.env)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Admin chat ID for alerts |
| `OPENWEATHER_API_KEY` | OpenWeatherMap API key |
| `SECRET_KEY` | Application secret key |
| `MONTE_CARLO_SIMULATIONS` | Number of MC simulations (default: 20000) |
| `DEFAULT_AGGRESSIVENESS` | Default strategy level |

## Deploy on DigitalOcean

```bash
# On a fresh Ubuntu 24.04 droplet:
chmod +x deploy/digitalocean_setup.sh
./deploy/digitalocean_setup.sh

# Then:
cd /opt/bf1-2026
git clone <repo-url> .
cp .env.template .env
# Edit .env
docker compose up -d
```

## First-Run Data Pipeline

The containers boot with empty tables. Populate them once (and whenever you want
to refresh history) with the pipeline scripts, run inside the API container:

```bash
# Full pipeline: seed grid -> ingest last 5 seasons -> compute ELO + KPIs
docker exec bf1_api python -m scripts.pipeline

# Or run steps individually:
docker exec bf1_api python -m scripts.seed                 # teams + current driver grid
docker exec bf1_api python -m scripts.ingest 2021 2025     # historical results (Ergast/OpenF1)
docker exec bf1_api python -m scripts.build_intelligence   # ELO ratings + circuit/team KPIs
```

All scripts are idempotent: `seed` upserts, `ingest` skips races already present,
and `build_intelligence` recomputes ELO deterministically from scratch. The daily
scheduler keeps data fresh after this initial load.

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + main menu |
| `/calendar` | 2026 race calendar with countdown |
| `/analysis` | Complete analysis for the next race |
| `/simulate` | Run Monte Carlo simulation (20K sims) |
| `/bet` | Place your 15-token allocation |
| `/leaderboard` | User rankings by BF1 points |
| `/settings` | Configure aggressiveness level |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/v1/races` | List races (season filter) |
| GET | `/api/v1/races/{id}` | Race details |
| GET | `/api/v1/races/{id}/analysis` | Race analysis |
| GET | `/api/v1/races/{id}/strategy` | Optimal strategy |
| GET | `/api/v1/drivers` | List active drivers |
| GET | `/api/v1/drivers/{id}/kpis` | Driver KPIs |
| POST | `/api/v1/bets` | Register a bet |
| GET | `/api/v1/bets/user/{telegram_id}` | User bets |
| GET | `/api/v1/bets/leaderboard` | Rankings |
| POST | `/api/v1/predictions/race/{id}/simulate` | Run simulation |

## ML Models

### XGBoost
Position prediction model trained on 17 features including driver ELO, team reliability, weather risk, and circuit characteristics. Uses `TimeSeriesSplit` to prevent future data leakage.

### Bayesian Hierarchical
PyMC model with driver-level priors that naturally capture uncertainty. Updated online after each race using posterior-as-prior methodology.

### Monte Carlo Simulator
20,000 race simulations per run. Each simulation applies:
- Gaussian grid noise
- Stochastic DNF events (Bernoulli)
- Safety car probability
- Weather effects on performance
- ELO-based performance adjustment

## KPIs

**Circuit:** overtaking index, winner grid average, safety car frequency, DNF rate, rain frequency, tire degradation, temperature sensitivity

**Driver:** average finish/grid position, positions gained, performance std, DNF rate, wet performance, sprint performance, consistency index, temperature performance

**Team:** qualifying pace, race pace, pit stop performance, strategic error rate, mechanical reliability

**Derived:** momentum, volatility, risk index, opportunity index, form trend

## Guardrails (Anti-Hallucination)

Models trained on sparse or unstable data can emit values outside the physical
or regulatory domain of F1 (e.g. a "5 pit stop" projection, a probability above
1.0, or a finishing position of 0). `app/utils/guardrails.py` is a central layer
that **clamps every model output and logs a warning whenever a raw value had to
be corrected** — so out-of-domain behavior is detected, not silently passed on.

| Guardrail | Bound |
|-----------|-------|
| Pit stops, dry GP | 1–3 (4 only with high confidence **and** extreme degradation) |
| Pit stops, Sprint **weekend** | max 2 (fewer fresh tyre sets remain) |
| Pit stops, Sprint **race** | 0–1 |
| Low confidence (<40%) | anchors to the historical norm instead of extrapolating |
| Probabilities | clamped to [0, 1]; `P(top3) ≤ P(top10)` enforced |
| Finishing position | clamped to [1, grid size] |
| Expected points | clamped to [−10 (DNF), 26] (×2 on sprints) |

The `PitStopProjector` treats an extreme raw estimate as a **critical-degradation
signal** (a tyre-management race), not as a literal stop count — and says so in
the strategy notes. Covered by `tests/test_guardrails.py`.

## Monitoring

- Health check: `GET /health`
- Logs: `/app/logs/` (daily rotation, 30-day retention)
- Scheduled jobs: daily data update (2 AM), weather update (8 AM), weekly retrain (Monday 3 AM)
- PostgreSQL backups: daily at 4 AM, 30-day retention
