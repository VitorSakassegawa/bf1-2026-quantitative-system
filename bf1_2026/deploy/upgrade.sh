#!/usr/bin/env bash
# One-command install / upgrade for BF1-2026.
#
#   cd bf1_2026 && sudo ./deploy/upgrade.sh
#
# What it does, in order:
#   1. makes sure .env exists and has what this version needs
#      (asks for the bolão site's address and login once, if they are missing)
#   2. rebuilds the images
#   3. brings the database to the current schema — recreating it when it was
#      created by an older version that did not use migrations
#   4. loads the data (calendar, results, ratings)
#   5. runs the health check
#
# Options:
#   --yes        do not ask before recreating the database
#   --fresh      recreate the database even if it could be migrated in place
#   --no-pool    do not ask for the bolão site's login
#
# The database only holds data that is downloaded again in about three
# minutes, plus any bets recorded through the bot. Recreating it loses those
# recorded bets and nothing else.

set -euo pipefail

cd "$(dirname "$0")/.."

ASSUME_YES=0; FRESH=0; ASK_POOL=1
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --fresh) FRESH=1 ;;
    --no-pool) ASK_POOL=0 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "Docker Compose not found. On Synology, install Container Manager."
fi
docker info >/dev/null 2>&1 || die "Cannot talk to Docker. Run this with sudo."

# ---------------------------------------------------------------- .env ----
env_get() {   # env_get KEY -> value ('' when missing)
  [ -f .env ] || { echo ""; return; }
  { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2-
}

env_set() {   # env_set KEY VALUE (replace in place, or append)
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" .env; then
    # Rewrite the line without interpreting the value (it may contain & / \).
    KEY="$key" VALUE="$value" awk 'BEGIN{k=ENVIRON["KEY"]; v=ENVIRON["VALUE"]}
      index($0, k"=")==1 {print k"="v; next} {print}' .env > "$tmp"
  else
    cat .env > "$tmp"
    [ -z "$(tail -c1 .env)" ] || echo >> "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > .env && rm -f "$tmp"
}

random_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

say "Checking .env"
if [ ! -f .env ]; then
  cp .env.template .env
  chmod 600 .env
  pw="$(random_hex | cut -c1-32)"
  env_set POSTGRES_PASSWORD "$pw"
  env_set DATABASE_URL "postgresql+asyncpg://bf1user:${pw}@postgres:5432/bf1_2026"
  env_set SECRET_KEY "$(random_hex)"
  env_set WRITE_API_KEY "$(random_hex)"
  env_set ADMIN_API_KEY "$(random_hex)"
  note "Created .env from the template, with generated passwords and API keys."
  if [ -t 0 ]; then
    read -r -p "    Telegram bot token (from @BotFather; empty to skip): " token
    [ -z "$token" ] || env_set TELEGRAM_BOT_TOKEN "$token"
  fi
else
  chmod 600 .env 2>/dev/null || true
  note ".env found."
fi

# On a private network the API runs without keys. Keep whatever was chosen;
# only fill the gap for an .env written before these settings existed.
if [ -z "$(env_get REQUIRE_API_KEYS)" ]; then
  if [ -z "$(env_get WRITE_API_KEY)" ] && [ -z "$(env_get ADMIN_API_KEY)" ]; then
    env_set REQUIRE_API_KEYS false
    [ -n "$(env_get API_BIND)" ] || env_set API_BIND 0.0.0.0
    note "No API keys set: configured for a private network (REQUIRE_API_KEYS=false)."
    note "If this machine is ever reachable from the internet, set keys instead."
  else
    env_set REQUIRE_API_KEYS true
  fi
fi
[ -n "$(env_get DISPLAY_TIMEZONE)" ] || env_set DISPLAY_TIMEZONE America/Sao_Paulo

# The bolão's site: address and login, so the bot can read the standings,
# the real betting deadlines and the bet already placed. Read-only.
if [ -z "$(env_get POOL_BASE_URL)" ]; then
  url="${POOL_BASE_URL:-}"; email="${POOL_EMAIL:-}"; password="${POOL_PASSWORD:-}"
  if [ -z "$url" ] && [ "$ASK_POOL" = 1 ] && [ -t 0 ]; then
    echo
    echo "    The bot can read the bolão's site (standings, deadlines, your bets)."
    echo "    It only reads — it never places a bet. Leave the address empty to skip."
    read -r -p "    Site address (https://...): " url
    if [ -n "$url" ]; then
      read -r -p "    Your e-mail on the site: " email
      read -r -s -p "    Your password on the site: " password; echo
    fi
  fi
  if [ -n "$url" ] && [ -n "$email" ] && [ -n "$password" ]; then
    env_set POOL_BASE_URL "${url%/}"
    env_set POOL_EMAIL "$email"
    env_set POOL_PASSWORD "$password"
    note "Bolão site configured."
  else
    note "Bolão site not configured — /classificacao will only show bets recorded in the bot."
  fi
else
  note "Bolão site already configured."
fi

PG_USER="$(env_get POSTGRES_USER)"; PG_USER="${PG_USER:-bf1user}"
PG_DB="$(env_get POSTGRES_DB)";     PG_DB="${PG_DB:-bf1_2026}"

# ------------------------------------------------------------ database ----
# A database created by an old version has tables but no migration history,
# and cannot be migrated: it has to be recreated.
needs_recreate() {
  [ "$FRESH" = 1 ] && return 0
  docker inspect bf1_postgres >/dev/null 2>&1 || return 1          # nothing there yet
  "${COMPOSE[@]}" up -d postgres >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    docker exec bf1_postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1 && break
    sleep 2
  done
  local tables revision
  tables="$(docker exec bf1_postgres psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null || echo 0)"
  revision="$(docker exec bf1_postgres psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "select version_num from alembic_version" 2>/dev/null || true)"
  [ "${tables:-0}" -gt 0 ] && [ -z "$revision" ]
}

say "Checking the database"
if needs_recreate; then
  note "The database has to be recreated (it predates migrations, or --fresh was given)."
  note "Everything in it is downloaded again; bets recorded in the bot are lost."
  if [ "$ASSUME_YES" != 1 ]; then
    [ -t 0 ] || die "Refusing to recreate the database without --yes."
    read -r -p "    Recreate it now? [y/N] " answer
    case "$answer" in y|Y|yes|s|S|sim) ;; *) die "Stopped. Nothing was changed in the database." ;; esac
  fi
  "${COMPOSE[@]}" down
  volumes="$(docker volume ls -q --filter label=com.docker.compose.volume=postgres_data)"
  project="$("${COMPOSE[@]}" config 2>/dev/null | awk '/^name:/{print $2; exit}')"
  if [ -n "$project" ] && docker volume inspect "${project}_postgres_data" >/dev/null 2>&1; then
    volumes="${project}_postgres_data"
  fi
  count="$(printf '%s\n' "$volumes" | grep -c . || true)"
  [ "$count" = 1 ] || die "Could not identify the Postgres volume uniquely (found: ${volumes:-none}). Remove it by hand with 'docker volume rm <name>' and run this again."
  docker volume rm "$volumes" >/dev/null
  note "Removed volume $volumes."
else
  note "The database can be migrated in place."
fi

# ---------------------------------------------------------------- build ----
say "Building and starting the containers (the first build takes a while)"
"${COMPOSE[@]}" up -d --build

say "Waiting for the API container"
ready=0
for _ in $(seq 1 60); do
  if docker exec bf1_api python -c "import app.config" >/dev/null 2>&1; then ready=1; break; fi
  sleep 3
done
[ "$ready" = 1 ] || die "bf1_api did not start. Look at: docker logs bf1_api"

say "Applying database migrations"
docker exec bf1_api alembic upgrade head

say "Loading data (calendar, results, ratings) — about three minutes"
docker exec bf1_api python -m scripts.pipeline 2>&1 \
  | grep -E "Seed complete|Synced|Roster synced|Pipeline finished|ERROR|Traceback|Error" || true

say "Health check"
if docker exec bf1_api python -m scripts.healthcheck; then
  # Pick up any .env change in the long-running services.
  "${COMPOSE[@]}" up -d >/dev/null 2>&1 || true
  say "Done"
  note "In Telegram: /analysis for the recommended bet, /classificacao for the standings."
  note "Run /analysis again after qualifying — the starting grid is the main input."
else
  die "The health check failed — see the lines marked FAIL above."
fi
