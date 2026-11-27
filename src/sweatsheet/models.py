"""models.py -- shared data shapes for sweatsheet.

Everything the different parts of the project pass to each other lives here,
so the parser, metrics, exporter and CLI can all import from this one file
without importing each other.

Conventions (keep these consistent across the whole project):
  * Weeks start on MONDAY (ISO weeks); months are calendar months.
  * Distances are stored in METERS, durations in SECONDS.
  * Miles and pace are derived values (pace = seconds per mile).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

METERS_PER_MILE = 1609.344


class MetricsError(ValueError):
    """Raised for bad input. Messages are written to be shown to the user."""


# --------------------------------------------------------------------------
# Parser output
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackPoint:
    """One GPS sample from a GPX file."""
    lat: float
    lon: float
    time: datetime | None = None


@dataclass(frozen=True)
class RunRecord:
    """One run, in the single shape that both GPX and CSV parsers produce.

    start       when the run began (datetime)
    distance_m  total distance in meters (>= 0)
    duration_s  total elapsed time in seconds (>= 0)
    source      where it came from, e.g. "gpx", "csv" (for debugging/reports)
    name        optional label, e.g. the file name or Strava activity title
    gps_distance_m
                distance in meters calculated from the GPS points of the
                activity's GPX/FIT file (haversine), or None when there is no
                file or no GPS data (e.g. an indoor run). It sits next to
                distance_m (the number Strava wrote in activities.csv) so the
                two can be compared; nothing uses it for totals yet.
    """
    start: datetime
    distance_m: float
    duration_s: float
    source: str = "unknown"
    name: str = ""
    gps_distance_m: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime):
            raise MetricsError(f"Run '{self.name}': start must be a datetime.")
        checks = [("distance", self.distance_m),
                  ("duration", self.duration_s)]
        if self.gps_distance_m is not None:
            checks.append(("GPS distance", self.gps_distance_m))
        for label, value in checks:
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise MetricsError(
                    f"Run '{self.name}': {label} is not a valid number.")
            if value < 0:
                raise MetricsError(
                    f"Run '{self.name}': {label} cannot be negative.")

    @property
    def run_date(self) -> date:
        return self.start.date()

    @property
    def distance_miles(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def gps_distance_miles(self) -> float | None:
        if self.gps_distance_m is None:
            return None
        return self.gps_distance_m / METERS_PER_MILE

    @property
    def pace_s_per_mile(self) -> float | None:
        """Seconds per mile, or None if the run has no distance."""
        if self.distance_m <= 0:
            return None
        return self.duration_s / self.distance_miles


# --------------------------------------------------------------------------
# Display helper (lives here because PeriodSummary uses it)
# --------------------------------------------------------------------------

def format_pace(seconds_per_mile: float | None) -> str:
    """Seconds per mile -> 'm:ss' (e.g. 487.0 -> '8:07'). None -> '--'."""
    if seconds_per_mile is None or not math.isfinite(seconds_per_mile):
        return "--"
    total = int(round(seconds_per_mile))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------
# Metrics output (read by the exporter and chart code)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PeriodSummary:
    period_start: date               # Monday (week) or 1st of month
    total_miles: float
    run_count: int
    longest_run_miles: float
    avg_pace_s_per_mile: float | None  # None if no miles in the period

    @property
    def avg_pace(self) -> str:
        return format_pace(self.avg_pace_s_per_mile)


@dataclass(frozen=True)
class JumpWarning:
    week_start: date
    previous_miles: float
    current_miles: float
    pct_increase: float   # 0.5 means +50%
    message: str
