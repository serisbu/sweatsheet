"""Mileage bar chart for sweatsheet."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # save to a file
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from .models import PeriodSummary


def plot_mileage(periods: list[PeriodSummary], path: Path,
                 period: str = "week", warn_weeks: set | None = None,
                 label: str = "Recent") -> None:
    """Save a bar chart of miles per week or month as a PNG."""
    warn_weeks = warn_weeks or set()
    fmt = "%b %d" if period == "week" else "%b %Y"   # "Oct 12" or "Oct 2026"
    labels = [p.period_start.strftime(fmt) for p in periods]
    miles = [p.total_miles for p in periods]

    fig, ax = plt.subplots(figsize=(9, 4))
    bars = ax.bar(labels, miles, color="lightgray", edgecolor="black")

    # Jump weeks (white with stripes)
    for bar, p in zip(bars, periods):
        if p.period_start in warn_weeks:
            bar.set_facecolor("white")
            bar.set_hatch("//")

    # Miles above each bar (skipped if too many)
    if len(periods) <= 16:
        ax.bar_label(bars, fmt="%.1f", fontsize=8)

    word = "Weekly" if period == "week" else "Monthly"
    ax.set_title(f"{label} - {word} Mileage")   # ex: "2025 - Weekly Mileage"
    ax.set_xlabel("Week of" if period == "week" else "Month")
    ax.set_ylabel("Miles")
    if period == "week":
        ax.legend(handles=[
            Patch(facecolor="lightgray", edgecolor="black", label="Normal week"),
            Patch(facecolor="white", edgecolor="black", hatch="//",
                  label="Mileage jump (more than 10%)")])
    # Display max ~12 date labels so they don't overlap
    step = max(1, len(labels) // 12)
    ax.set_xticks(list(range(0, len(labels), step)), labels[::step],
                  rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
