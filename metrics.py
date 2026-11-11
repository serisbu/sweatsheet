"""
metrics.py -- math & analytics for sweatsheet.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Sequence

from models import (
    METERS_PER_MILE,
    JumpWarning,
    MetricsError,
    PeriodSummary,
    RunRecord,
    TrackPoint,
)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

EARTH_RADIUS_M = 6371008.8  # mean Earth radius

# Warn when this week is MORE than 10% above last week. Tune here only.
WEEKLY_JUMP_THRESHOLD = 0.10


# --------------------------------------------------------------------------
# Distance, time, pace
# --------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points (degrees)."""
    for lat in (lat1, lat2):
        if not -90 <= lat <= 90:
            raise MetricsError(f"Latitude {lat} is out of range (-90 to 90).")
    for lon in (lon1, lon2):
        if not -180 <= lon <= 180:
            raise MetricsError(
                f"Longitude {lon} is out of range (-180 to 180)."
            )

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def track_distance_m(points: Sequence[TrackPoint]) -> float:
    """Sum of haversine distances between consecutive trackpoints."""
    if len(points) < 2:
        raise MetricsError(
            "Need at least 2 GPS trackpoints to measure a distance "
            f"(got {len(points)}).")
    return sum(
        haversine_m(a.lat, a.lon, b.lat, b.lon)
        for a, b in zip(points, points[1:])
    )


def track_duration_s(points: Sequence[TrackPoint]) -> float:
    """Elapsed seconds from the first to the last trackpoint."""
    if len(points) < 2:
        raise MetricsError(
            "Need at least 2 GPS trackpoints to measure a duration "
            f"(got {len(points)}).")
    first, last = points[0].time, points[-1].time
    if first is None or last is None:
        raise MetricsError("GPS trackpoints are missing timestamps.")
    try:
        seconds = (last - first).total_seconds()
    except TypeError:
        raise MetricsError(
            "GPS timestamps mix timezone-aware and naive times.") from None
    if seconds <= 0:
        raise MetricsError("GPS timestamps do not move forward in time.")
    return seconds


def run_from_track(points: Sequence[TrackPoint], source: str = "gpx",
                   name: str = "") -> RunRecord:
    """Build a RunRecord from GPX trackpoints (distance via haversine)."""
    distance = track_distance_m(points)
    duration = track_duration_s(points)
    return RunRecord(start=points[0].time, distance_m=distance,
                     duration_s=duration, source=source, name=name)


def pace_s_per_mile(distance_m: float, duration_s: float) -> float:
    """Average pace in seconds per mile."""
    if distance_m <= 0:
        raise MetricsError("Pace is undefined for a run with zero distance.")
    if duration_s < 0:
        raise MetricsError("Duration cannot be negative.")
    return duration_s / (distance_m / METERS_PER_MILE)


# --------------------------------------------------------------------------
# Weekly / monthly aggregation
# --------------------------------------------------------------------------

def week_start(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def _next_period(start: date, period: str) -> date:
    if period == "week":
        return start + timedelta(days=7)
    year, month = (start.year + 1, 1) if start.month == 12 \
        else (start.year, start.month + 1)
    return date(year, month, 1)


def summarize(runs: Iterable[RunRecord], period: str = "week",
              fill_gaps: bool = True) -> list[PeriodSummary]:
    """Group runs into weekly or monthly summaries, oldest first.

    fill_gaps=True inserts zero-mile periods for weeks/months with no runs,
    which keeps the jump warning and the chart honest about rest weeks.
    """
    if period not in ("week", "month"):
        raise MetricsError("period must be 'week' or 'month'.")
    key = week_start if period == "week" else month_start

    buckets: dict[date, list[RunRecord]] = defaultdict(list)
    for run in runs:
        buckets[key(run.run_date)].append(run)
    if not buckets:
        return []

    if fill_gaps:
        starts: list[date] = []
        cursor, last = min(buckets), max(buckets)
        while cursor <= last:
            starts.append(cursor)
            cursor = _next_period(cursor, period)
    else:
        starts = sorted(buckets)

    summaries = []
    for start in starts:
        group = buckets.get(start, [])
        miles = sum(r.distance_miles for r in group)
        # pace is distance-weighted: only runs that actually covered distance
        moving = [r for r in group if r.distance_m > 0]
        moving_miles = sum(r.distance_miles for r in moving)
        pace = (sum(r.duration_s for r in moving) / moving_miles
                if moving_miles > 0 else None)
        summaries.append(PeriodSummary(
            period_start=start,
            total_miles=miles,
            run_count=len(group),
            longest_run_miles=max((r.distance_miles for r in group),
                                  default=0.0),
            avg_pace_s_per_mile=pace,
        ))
    return summaries


# --------------------------------------------------------------------------
# Pace trend
# --------------------------------------------------------------------------

def rolling_pace(summaries: Sequence[PeriodSummary],
                 window: int = 4) -> list[float | None]:
    """Mean pace over the last `window` periods that had a pace.

    Aligned 1:1 with `summaries`; None where no pace is available yet.
    """
    if window < 1:
        raise MetricsError("window must be at least 1.")
    out: list[float | None] = []
    for i in range(len(summaries)):
        recent = [s.avg_pace_s_per_mile
                  for s in summaries[max(0, i - window + 1): i + 1]
                  if s.avg_pace_s_per_mile is not None]
        out.append(sum(recent) / len(recent) if recent else None)
    return out


def pace_trend_slope(summaries: Sequence[PeriodSummary]) -> float | None:
    """Least-squares slope of pace over time, in seconds/mile per period.

    Negative = getting faster, positive = getting slower.
    None if fewer than 2 periods have a pace.
    """
    pts = [(i, s.avg_pace_s_per_mile) for i, s in enumerate(summaries)
           if s.avg_pace_s_per_mile is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    mean_x = sum(x for x, _ in pts) / n
    mean_y = sum(y for _, y in pts) / n
    denom = sum((x - mean_x) ** 2 for x, _ in pts)
    return sum((x - mean_x) * (y - mean_y) for x, y in pts) / denom


def describe_trend(slope: float | None, steady_band_s: float = 2.0) -> str:
    """Plain-English label for a slope (within +/- steady_band_s = steady)."""
    if slope is None:
        return "not enough data to see a pace trend yet"
    if slope < -steady_band_s:
        return "getting faster"
    if slope > steady_band_s:
        return "getting slower"
    return "holding steady"


# --------------------------------------------------------------------------
# Weekly mileage jump warning
# --------------------------------------------------------------------------

def weekly_jump_warnings(weekly: Sequence[PeriodSummary],
                         threshold: float = WEEKLY_JUMP_THRESHOLD,
                         min_previous_miles: float = 0.0
                         ) -> list[JumpWarning]:
    """Flag weeks whose mileage is MORE than `threshold` above the prior week.

    `weekly` must be consecutive weeks (summarize(..., fill_gaps=True) does
    this). A prior week with 0 miles has no baseline, so it never warns.
    min_previous_miles can be raised to ignore noise from very light weeks.
    """
    if threshold < 0:
        raise MetricsError("threshold cannot be negative.")
    warnings: list[JumpWarning] = []
    for prev, cur in zip(weekly, weekly[1:]):
        if prev.total_miles <= 0 or prev.total_miles < min_previous_miles:
            continue
        increase = (cur.total_miles - prev.total_miles) / prev.total_miles
        if increase > threshold:
            msg = (f"Heads up! Week of {cur.period_start:%b} "
                   f"{cur.period_start.day}: {cur.total_miles:.1f} mi is "
                   f"{increase:.0%} more than the week before "
                   f"({prev.total_miles:.1f} mi). Ramping up gradually "
                   f"(about {threshold:.0%} per week) helps prevent injury.")
            warnings.append(JumpWarning(cur.period_start, prev.total_miles,
                                        cur.total_miles, increase, msg))
    return warnings
