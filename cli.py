"""Command line for sweatsheet: supports both summarizing a Strava export by week (default) or month."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import metrics
import plot
from models import PeriodSummary, RunRecord

#Settings
LOCAL_TZ = ZoneInfo("America/New_York")   # Strava times are UTC
DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"    # ex: "Apr 12, 2024, 1:26:04 AM"
CSV_COLUMNS = ["period_start", "total_miles", "run_count",
               "longest_run_miles", "avg_pace", "warning"]


# 1. Read what user typed
def build_parser() -> argparse.ArgumentParser:
    """List the command's options. argparse also makes --help."""
    parser = argparse.ArgumentParser(
        prog="sweatsheet", description="Summarize a Strava export.")
    parser.add_argument("folder", type=Path,
                        help="Strava export folder with activities.csv")
    parser.add_argument("--monthly", action="store_true",
                        help="group by month instead of week")
    parser.add_argument("--out", type=Path, help="save the summary as a CSV")
    parser.add_argument("--plot", type=Path, help="save a chart as a PNG")
    parser.add_argument("--last", type=int, default=12,
                        help="how many recent periods to show (default 12)")
    parser.add_argument("--year", type=int,
                        help="show one year instead of the recent periods")
    return parser


# 2. Load runs
# TEMPORARY: replacing with parser when ready
def load_runs(folder: Path) -> list[RunRecord]:
    """Read the runs from FOLDER/activities.csv."""
    path = folder / "activities.csv"
    if not path.is_file():
        raise ValueError(f"no activities.csv in {folder}")

    # Read every row of the file, then find the columns we need
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    kind = header.index("Activity Type")
    when = header.index("Activity Date")
    # Strava repeats these names; the 2nd ones are meters and seconds.
    meters = [i for i, name in enumerate(header) if name == "Distance"][1]
    seconds = [i for i, name in enumerate(header) if name == "Elapsed Time"][1]

    # Keep only runs; skip any bad row with a warning
    runs = []
    for line, row in enumerate(rows[1:], start=2):
        if row[kind] != "Run":
            continue
        try:
            # Convert Strava's UTC time to Boston time (EST)
            utc = datetime.strptime(row[when], DATE_FORMAT)
            start = utc.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
            runs.append(RunRecord(start, float(row[meters] or 0),
                                  float(row[seconds] or 0), source="csv"))
        except ValueError as e:
            print(f"warning: skipped line {line}: {e}", file=sys.stderr)
    return runs


# 3. Results
def print_table(periods: list[PeriodSummary], warn_weeks: set) -> None:
    """Print one line per period. '!' marks a mileage jump."""
    print(f"{'Start':<11} {'Miles':>6} {'Runs':>5} {'Longest':>8} {'Pace':>6}")
    for p in periods:
        flag = "  !" if p.period_start in warn_weeks else ""
        print(f"{p.period_start}  {p.total_miles:6.1f} {p.run_count:5d} "
              f"{p.longest_run_miles:8.1f} {p.avg_pace:>6}{flag}")


def write_csv(path: Path, periods: list[PeriodSummary],
              warn_weeks: set) -> None:
    """Save every period as one CSV row."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for p in periods:
            writer.writerow([p.period_start, f"{p.total_miles:.2f}",
                             p.run_count, f"{p.longest_run_miles:.2f}",
                             p.avg_pace,
                             "yes" if p.period_start in warn_weeks else ""])


# 4. Everything together
def main(argv: list[str] | None = None) -> int:
    """Run sweatsheet. Returns 0 if it worked, 1 if the input was bad."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Check the options
    if args.last < 1:
        parser.error("--last must be at least 1")   # exits with code 2
    period = "month" if args.monthly else "week"

    # Any bad input below is printed as one 'error:' line
    try:
        if not args.folder.is_dir():
            raise ValueError(f"folder not found: {args.folder}")
        # Load the runs
        runs = load_runs(args.folder)
        if not runs:
            raise ValueError(f"no runs found in {args.folder}")

        # Math (from metrics.py)
        periods = metrics.summarize(runs, period)
        warnings = [] if args.monthly else metrics.weekly_jump_warnings(periods)
        warn_weeks = {w.week_start for w in warnings}
        # Pick which periods to show: one year or most recent ones
        if args.year:
            shown = [p for p in periods if p.period_start.year == args.year]
            if not shown:
                raise ValueError(f"no runs in {args.year}")
        else:
            shown = periods[-args.last:]

        # Print table, pace trend, and warnings for shown periods
        print(f"Read {len(runs)} runs.\n")
        print_table(shown, warn_weeks)
        print(f"\nPace trend: "
              f"{metrics.describe_trend(metrics.pace_trend_slope(shown))}")
        for w in warnings:
            if shown[0].period_start <= w.week_start <= shown[-1].period_start:
                print(f"\n{w.message}")

        # Save files only if user asked
        if args.out:
            write_csv(args.out, periods, warn_weeks)
            print(f"\nSaved {args.out}")
        if args.plot:
            label = str(args.year) if args.year else "Recent"
            plot.plot_mileage(shown, args.plot, period, warn_weeks, label)
            print(f"Saved {args.plot}")
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


# Runs main() when this file is started from terminal
if __name__ == "__main__":
    sys.exit(main())
