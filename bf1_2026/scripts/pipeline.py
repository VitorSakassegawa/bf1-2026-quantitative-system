"""Run the full data pipeline end-to-end.

Order matters: seed the grid, ingest history, then derive intelligence.

    python -m scripts.pipeline                 # seed + last 5 seasons + build
    python -m scripts.pipeline 2021 2025       # custom season range
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from loguru import logger

from scripts.build_intelligence import build
from scripts.ingest import ingest
from scripts.seed import seed
from scripts.train_model import train


async def run(start: int, end: int) -> None:
    logger.info("STEP 1/4 — seeding current grid")
    await seed()

    logger.info(f"STEP 2/4 — ingesting seasons {start}-{end}")
    await ingest(start, end)

    logger.info("STEP 3/4 — building intelligence (ELO + KPIs)")
    await build()

    logger.info("STEP 4/4 — training XGBoost model")
    await train()

    logger.info("Pipeline finished. The database is ready for predictions.")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        s, e = int(sys.argv[1]), int(sys.argv[2])
    else:
        current = datetime.now().year
        s, e = current - 5, current - 1
    asyncio.run(run(s, e))
