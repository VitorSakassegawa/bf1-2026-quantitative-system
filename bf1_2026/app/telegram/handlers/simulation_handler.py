"""Simulation handler – run Monte Carlo and display results."""

from __future__ import annotations

import time

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.formatters import escape_md


async def simulate_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /simulate [race_id] – run Monte Carlo simulation."""
    if update.effective_message is None:
        return

    logger.info(f"/simulate from {update.effective_user}")

    await update.effective_message.reply_text("Running 20,000 simulations...")

    start = time.time()

    # Placeholder simulation results (in production, call MonteCarloSimulator)
    sim_results = {
        "VER": {"avg_pos": 2.1, "top3": 0.72, "win": 0.35},
        "NOR": {"avg_pos": 3.4, "top3": 0.58, "win": 0.22},
        "LEC": {"avg_pos": 3.8, "top3": 0.51, "win": 0.18},
        "HAM": {"avg_pos": 5.2, "top3": 0.32, "win": 0.08},
        "PIA": {"avg_pos": 5.5, "top3": 0.28, "win": 0.07},
    }

    elapsed = time.time() - start

    lines = [
        "━━━━━━━━━━━━━━━━━",
        "MONTE CARLO SIMULATION",
        f"20,000 simulations \\| {elapsed:.1f}s",
        "━━━━━━━━━━━━━━━━━",
        "",
        "*Most Likely Winners:*",
    ]

    for code, data in sorted(
        sim_results.items(), key=lambda x: x[1]["win"], reverse=True
    ):
        bar_len = int(data["win"] * 40)
        bar = "█" * bar_len + "░" * (40 - bar_len)
        lines.append(
            f"`{escape_md(code)}: {bar} {data['win']:.0%}`"
        )

    lines.append("")
    lines.append("*Average Positions:*")
    for code, data in sorted(
        sim_results.items(), key=lambda x: x[1]["avg_pos"]
    ):
        lines.append(
            f"  {escape_md(code)}: P{data['avg_pos']:.1f} "
            f"\\(Top3: {data['top3']:.0%}\\)"
        )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2"
    )
