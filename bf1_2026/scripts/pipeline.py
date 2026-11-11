"""Run the full data pipeline end-to-end.

    python -m scripts.pipeline                 # current regulation era -> now
    python -m scripts.pipeline 2022 2026       # custom season range

Order matters: seed a starting grid and calendar, sync real results over the
top of them, then derive ratings and KPIs. The season in progress is always
included — an earlier default stopped at last year, so the system never saw
the races it most needed.

The XGBoost training step is gone from this pipeline. The model was never on
solid ground (look-ahead features, ten constant columns) and predictions now
come from the calibrated simulator; run `python -m scripts.backtest` to see
how that is doing against real results.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from loguru import logger

from app.utils.season_window import current_season, era_start_for
from scripts.build_intelligence import build
from scripts.ingest import ingest
from scripts.seed import seed


async def run(start: int, end: int, refresh: bool = False) -> None:
    logger.info("STEP 1/3 — seeding grid and calendar")
    await seed()

    logger.info(f"STEP 2/3 — syncing seasons {start}-{end} from the results API")
    await ingest(start, end, refresh=refresh)

    logger.info("STEP 3/3 — building intelligence (ELO + KPIs)")
    await build()

    logger.info("Pipeline finished. Run `python -m scripts.healthcheck` to verify.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) >= 2:
        s, e = int(args[0]), int(args[1])
    else:
        year = current_season(datetime.now(timezone.utc))
        s, e = min(era_start_for(year), year - 4), year
    asyncio.run(run(s, e, refresh="--refresh" in sys.argv))
