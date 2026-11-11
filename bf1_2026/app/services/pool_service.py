"""What the app needs from the pool site, in the app's own terms.

Thin layer over ``PoolClient``: picks out the participant's own row, turns the
pool's rule payload into ``PoolRules``, and copies the pool's betting deadlines
onto the local races. Everything degrades to "not available" when the pool is
not configured or cannot be reached — the engine never depends on it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.pool_client import (
    PoolClient,
    PoolError,
    fold_name,
    parse_pool_datetime,
)
from app.config import settings
from app.models.circuit import Circuit
from app.models.race import Race
from app.rules.pool_rules import DEFAULT_RULES, PoolRules

_client: PoolClient | None = None
_cache: dict[str, tuple[float, object]] = {}
STANDINGS_TTL = 300.0
EVENT_TTL = 120.0


def pool_enabled() -> bool:
    return bool(settings.pool_base_url and settings.pool_email and settings.pool_password)


def get_pool_client() -> PoolClient:
    global _client
    if _client is None:
        _client = PoolClient()
    return _client


def clear_cache() -> None:
    _cache.clear()


def _season(season: int | None) -> int:
    return season or datetime.now(timezone.utc).year


async def _cached(key: str, ttl: float, loader):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = await loader()
    _cache[key] = (time.monotonic(), value)
    return value


def _my_name(client: PoolClient) -> str:
    return settings.pool_participant_name or str(client.profile.get("nome", ""))


def find_me(entries: list[dict], name: str, key: str = "participant") -> dict | None:
    """The participant's own row. Exact name first, then family name."""
    if not name:
        return None
    for e in entries:
        if str(e.get(key, "")).strip().lower() == name.strip().lower():
            return e
    folded = fold_name(name)
    matches = [e for e in entries if fold_name(str(e.get(key, ""))) == folded]
    return matches[0] if len(matches) == 1 else None


async def fetch_standings(season: int | None = None, client: PoolClient | None = None) -> dict:
    """The pool's classification, with the participant's row picked out.

    Returns {"entries": [...], "me": {...} | None, "leader": {...}, "season"}.
    Each entry has position, participant, valid_total (after the provisional
    discard), difference to the row above, and eleventh_hits.
    """
    client = client or get_pool_client()
    season = _season(season)

    async def load():
        data = await client.classification(season)
        entries = sorted(
            data.get("entries", []), key=lambda e: e.get("position", 10**6)
        )
        me = find_me(entries, _my_name(client))
        return {
            "season": season,
            "entries": entries,
            "me": me,
            "leader": entries[0] if entries else None,
            "discard_active": bool(data.get("discard_active")),
        }

    return await _cached(f"standings:{season}", STANDINGS_TTL, load)


async def fetch_next_event(season: int | None = None, client: PoolClient | None = None) -> dict:
    """The event currently open for betting on the pool, and the rules for it.

    Returns name, type ("Normal"/"Sprint"), deadline (aware datetime), is_open,
    rules (PoolRules), drivers [{name, team}], current_bet and weather.
    """
    client = client or get_pool_client()
    season = _season(season)

    async def load():
        form = await client.race_bets(season)
        telemetry = await client.telemetry(season)
        selected = form.get("selected_race") or {}
        nxt = telemetry.get("next_race") or {}
        # `current_rules` is the complete rule set; the form's `rules` is a
        # subset that spells the same-team flag differently.
        payload = {**(form.get("rules") or {}), **(telemetry.get("current_rules") or {})}
        return {
            "season": season,
            "id": selected.get("id") or nxt.get("id"),
            "name": selected.get("name") or nxt.get("name"),
            "type": selected.get("type") or nxt.get("type") or "Normal",
            "circuit_ref": nxt.get("circuit_id"),
            "deadline": parse_pool_datetime(selected.get("deadline") or nxt.get("starts_at")),
            "is_open": bool(selected.get("is_open")),
            "rules": PoolRules.from_pool_payload(payload),
            "drivers": form.get("drivers") or [],
            "current_bet": form.get("current_bet"),
            "has_bet": bool(nxt.get("has_bet")) or form.get("current_bet") is not None,
            "weather": nxt.get("weather") or {},
            "automatic_bet": telemetry.get("automatic_bet") or {},
        }

    return await _cached(f"event:{season}", EVENT_TTL, load)


async def current_rules(season: int | None = None) -> PoolRules:
    """The pool's live rules, or the published defaults if it cannot be asked."""
    if not pool_enabled():
        return DEFAULT_RULES
    try:
        return (await fetch_next_event(season))["rules"]
    except PoolError as e:
        logger.warning(f"Could not read the pool's rules ({e}); using the defaults")
        return DEFAULT_RULES


async def sync_pool_deadlines(session: AsyncSession, season: int | None = None) -> int:
    """Copy the pool's own betting deadlines onto the local races.

    The pool closes bets at the time *it* has for each race, which is not
    always the official start (its 2026 Qatar entry is three hours earlier).
    Since that is the deadline that actually binds, it overrides the one
    derived from the F1 calendar. Events are matched on the circuit reference
    the pool shares with the results API. Returns the number of races updated.
    """
    if not pool_enabled():
        return 0
    season = _season(season)
    try:
        events = await get_pool_client().calendar(season)
    except PoolError as e:
        logger.warning(f"Pool deadlines not synced: {e}")
        return 0

    races = (
        await session.execute(
            select(Race, Circuit.ref)
            .join(Circuit, Circuit.id == Race.circuit_id)
            .where(Race.season == season)
        )
    ).all()
    by_ref = {ref: race for race, ref in races if ref}

    updated = 0
    for ev in events:
        if ev.get("type") != "Normal" or ev.get("status") == "Inativa":
            continue
        race = by_ref.get(ev.get("circuit_id"))
        # The pool keeps times in Brasília time (UTC-3), as its own UI assumes.
        deadline = parse_pool_datetime(f"{ev.get('date')}T{ev.get('time')}:00-03:00")
        if race is None or deadline is None:
            continue
        deadline = deadline.astimezone(timezone.utc)
        if race.deadline_bets != deadline:
            race.deadline_bets = deadline
            updated += 1
    if updated:
        logger.info(f"Pool deadlines synced for {updated} race(s)")
    return updated


def match_pool_drivers(pool_drivers: list[dict], drivers: list) -> dict[str, str]:
    """Map the pool's driver names onto local drivers: {folded name: driver id}."""
    by_family = {fold_name(d.name): str(d.id) for d in drivers}
    out: dict[str, str] = {}
    for pd in pool_drivers:
        key = fold_name(pd.get("name", ""))
        if key in by_family:
            out[key] = by_family[key]
    return out
