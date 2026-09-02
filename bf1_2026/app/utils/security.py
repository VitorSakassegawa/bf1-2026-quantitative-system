"""API authentication dependencies.

The HTTP API is an operator/integration surface — the player-facing product is
the Telegram bot, which talks to the database directly and never goes through
these routes. So every route that writes, or that can burn CPU on demand, sits
behind a shared key rather than being open to the internet.

Two keys, so a compromised integration token cannot trigger admin operations:

  WRITE_API_KEY   create races/drivers/bets, run simulations
  ADMIN_API_KEY   the /api/v1/admin/* routes

Both fail closed. If a key is unset, blank, or still the shipped placeholder,
the dependency refuses every request in staging/production instead of quietly
accepting the known default — the previous behaviour, where the admin key fell
back to `SECRET_KEY`, meant an unset value let anyone through with an empty
header.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException
from loguru import logger

from app.config import Environment, settings

# Values that must never be accepted as a real credential.
_PLACEHOLDERS = {
    "",
    "dev-secret-key-change-in-production",
    "CHANGE_ME_RANDOM_64_CHARS",
    "changeme",
    "please-change-me",
}

MIN_KEY_LENGTH = 16


def _configured(key: str | None) -> bool:
    """True only for a key that is set, non-placeholder and long enough."""
    if key is None:
        return False
    return key not in _PLACEHOLDERS and len(key) >= MIN_KEY_LENGTH


def _is_relaxed() -> bool:
    """Whether an unconfigured key is allowed to pass.

    True in development, or when the operator has explicitly declared the
    deployment unreachable from the internet via REQUIRE_API_KEYS=false.
    """
    return (
        settings.environment == Environment.development
        or not settings.require_api_keys
    )


def _check(provided: str, expected: str, label: str) -> None:
    if not _configured(expected):
        if _is_relaxed():
            reason = (
                "ENVIRONMENT=development"
                if settings.environment == Environment.development
                else "REQUIRE_API_KEYS=false"
            )
            logger.warning(
                f"{label} is not configured — allowing the request because "
                f"{reason}. This route is open to anyone who can reach the port."
            )
            return
        logger.error(
            f"{label} is not configured. Refusing every request to this route. "
            f"Set it to a random value of at least {MIN_KEY_LENGTH} characters."
        )
        raise HTTPException(
            status_code=503,
            detail=f"{label} is not configured on this server",
        )

    # compare_digest so a wrong key cannot be recovered by timing the response.
    if not secrets.compare_digest(provided or "", expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def require_write_key(x_api_key: str = Header(default="")) -> None:
    """Guard state-changing and compute-heavy routes."""
    _check(x_api_key, settings.write_api_key, "WRITE_API_KEY")


async def require_admin_key(x_admin_key: str = Header(default="")) -> None:
    """Guard the admin routes."""
    _check(x_admin_key, settings.admin_api_key, "ADMIN_API_KEY")


def startup_security_report() -> list[str]:
    """Problems worth logging loudly at boot. Returns human-readable strings."""
    problems: list[str] = []

    if not settings.require_api_keys and settings.environment != Environment.development:
        problems.append(
            "REQUIRE_API_KEYS=false — every write route and the entire admin "
            "surface is UNAUTHENTICATED. Only valid if this port is truly "
            "unreachable from the internet"
        )

    if not _configured(settings.write_api_key):
        problems.append("WRITE_API_KEY is unset or a placeholder")
    if not _configured(settings.admin_api_key):
        problems.append("ADMIN_API_KEY is unset or a placeholder")
    if settings.environment != Environment.development:
        if settings.allowed_origins.strip() == "*":
            problems.append(
                "ALLOWED_ORIGINS is '*' while credentials are allowed — "
                "set an explicit origin list"
            )
        if not _configured(settings.secret_key):
            problems.append("SECRET_KEY is unset or still the shipped default")
    return problems
