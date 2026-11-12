from datetime import date, datetime, timedelta, timezone

import pytest

from sweatsheet import metrics as m
from sweatsheet.models import (METERS_PER_MILE, MetricsError, RunRecord, TrackPoint,
                    format_pace)

MILE = METERS_PER_MILE


def run(day: date, miles: float, minutes_per_mile: float = 9.0) -> RunRecord:
    """Helper: a run of `miles` on `day` at a steady pace."""
    return RunRecord(
        start=datetime(day.year, day.month, day.day, 7, 0),
        distance_m=miles * MILE,
        duration_s=miles * minutes_per_mile * 60,
        source="test",
    )


def weekly_totals(miles_per_week, start=date(2026, 1, 5)):  # a Monday
    """One run per week with the given mileage."""
    return m.summarize(
        [run(start + timedelta(weeks=i), mi)
         for i, mi in enumerate(miles_per_week)],
        period="week",
    )


# ---------- distance accuracy & pace ----------

def test_haversine_one_degree_at_equator():
    # 1 degree of longitude on the equator is ~111.195 km
    assert m.haversine_m(0, 0, 0, 1) == pytest.approx(111_195, rel=1e-3)


def test_three_point_gpx_distance_and_pace():
    t0 = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    pts = [
        TrackPoint(0.0, 0.00, t0),
        TrackPoint(0.0, 0.01, t0 + timedelta(minutes=10)),
        TrackPoint(0.0, 0.02, t0 + timedelta(minutes=20)),
    ]
    rec = m.run_from_track(pts, name="three_points.gpx")
    # 0.02 degrees of longitude on the equator ~ 2223.9 m ~ 1.3819 mi
    assert rec.distance_m == pytest.approx(2223.9, rel=1e-3)
    assert rec.distance_miles == pytest.approx(1.3819, rel=1e-3)
    assert rec.duration_s == 1200
    assert rec.pace_s_per_mile == pytest.approx(868.4, rel=1e-3)
    assert format_pace(rec.pace_s_per_mile) == "14:28"


def test_pace_exact_mile():
    assert m.pace_s_per_mile(MILE, 480) == pytest.approx(480)
    assert format_pace(480) == "8:00"


def test_format_pace_rounds_and_handles_none():
    assert format_pace(479.6) == "8:00"   # carries into the minute
    assert format_pace(None) == "--"


# ---------- aggregation ----------

def test_weekly_totals_counts_and_longest():
    runs = [
        run(date(2026, 1, 5), 3),    # Mon, week 1
        run(date(2026, 1, 8), 5),    # Thu, week 1
        run(date(2026, 1, 11), 2),   # Sun, still week 1
        run(date(2026, 1, 12), 10),  # Mon, week 2
    ]
    weeks = m.summarize(runs, "week")
    assert [w.period_start for w in weeks] == [date(2026, 1, 5),
                                               date(2026, 1, 12)]
    assert weeks[0].total_miles == pytest.approx(10)
    assert weeks[0].run_count == 3
    assert weeks[0].longest_run_miles == pytest.approx(5)
    assert weeks[1].total_miles == pytest.approx(10)
    assert weeks[1].run_count == 1


def test_monthly_totals():
    runs = [run(date(2026, 1, 30), 4), run(date(2026, 2, 2), 6),
            run(date(2026, 2, 20), 8)]
    months = m.summarize(runs, "month")
    assert [x.period_start for x in months] == [date(2026, 1, 1),
                                                date(2026, 2, 1)]
    assert months[0].total_miles == pytest.approx(4)
    assert months[1].total_miles == pytest.approx(14)
    assert months[1].longest_run_miles == pytest.approx(8)


def test_gap_weeks_are_filled_with_zero():
    runs = [run(date(2026, 1, 5), 10), run(date(2026, 1, 26), 10)]
    weeks = m.summarize(runs, "week")
    assert len(weeks) == 4
    assert weeks[1].total_miles == 0 and weeks[1].run_count == 0
    assert weeks[1].avg_pace_s_per_mile is None
    assert len(m.summarize(runs, "week", fill_gaps=False)) == 2


def test_period_pace_is_distance_weighted():
    # 1 mi @ 8:00 and 3 mi @ 10:00 -> 38 min / 4 mi = 9:30
    # (a plain average of the two paces would wrongly give 9:00)
    runs = [run(date(2026, 1, 5), 1, 8), run(date(2026, 1, 6), 3, 10)]
    week = m.summarize(runs, "week")[0]
    assert week.avg_pace_s_per_mile == pytest.approx(570)
    assert week.avg_pace == "9:30"


def test_empty_input_gives_empty_summary():
    assert m.summarize([], "week") == []


# ---------- weekly jump warning ----------

def test_20_to_30_warns():
    warns = m.weekly_jump_warnings(weekly_totals([20, 30]))
    assert len(warns) == 1
    assert warns[0].pct_increase == pytest.approx(0.5)
    assert "50%" in warns[0].message


def test_20_to_21_does_not_warn():
    assert m.weekly_jump_warnings(weekly_totals([20, 21])) == []


def test_exactly_at_threshold_does_not_warn():
    assert m.weekly_jump_warnings(weekly_totals([20, 22])) == []


def test_decrease_does_not_warn():
    assert m.weekly_jump_warnings(weekly_totals([30, 10])) == []


def test_zero_previous_week_does_not_crash_or_warn():
    runs = [run(date(2026, 1, 5), 10), run(date(2026, 1, 19), 15)]
    weeks = m.summarize(runs, "week")  # middle week is 0 miles
    assert m.weekly_jump_warnings(weeks) == []


# ---------- pace trend ----------

def test_pace_trend_faster_slower_steady():
    def weeks(paces):
        start = date(2026, 1, 5)
        return m.summarize(
            [run(start + timedelta(weeks=i), 5, p)
             for i, p in enumerate(paces)], "week")

    faster = m.pace_trend_slope(weeks([10, 9.5, 9, 8.5]))
    slower = m.pace_trend_slope(weeks([8.5, 9, 9.5, 10]))
    steady = m.pace_trend_slope(weeks([9, 9, 9, 9]))
    assert faster is not None and slower is not None
    assert faster < 0 < slower
    assert faster == pytest.approx(-30)  # 30 s/mi faster per week
    assert m.describe_trend(faster) == "getting faster"
    assert m.describe_trend(slower) == "getting slower"
    assert m.describe_trend(steady) == "holding steady"
    assert m.pace_trend_slope(weeks([9])) is None


def test_rolling_pace_window():
    start = date(2026, 1, 5)
    ws = m.summarize([run(start + timedelta(weeks=i), 5, p)
                      for i, p in enumerate([10, 9, 8])], "week")
    rolled = m.rolling_pace(ws, window=2)
    assert rolled[0] == pytest.approx(600)
    assert rolled[2] == pytest.approx((540 + 480) / 2)


# ---------- fault tolerance: clear errors, no crashes ----------

def test_single_trackpoint_gives_clear_error():
    with pytest.raises(MetricsError, match="at least 2"):
        m.run_from_track([TrackPoint(0, 0, datetime(2026, 1, 5))])


def test_empty_track_gives_clear_error():
    with pytest.raises(MetricsError, match="at least 2"):
        m.run_from_track([])


def test_missing_timestamps_gives_clear_error():
    with pytest.raises(MetricsError, match="timestamps"):
        m.run_from_track([TrackPoint(0, 0), TrackPoint(0, 0.01)])


def test_backwards_timestamps_give_clear_error():
    t = datetime(2026, 1, 5, 7, 0)
    with pytest.raises(MetricsError, match="forward"):
        m.run_from_track([TrackPoint(0, 0, t),
                          TrackPoint(0, 0.01, t - timedelta(seconds=5))])


def test_bad_coordinates_give_clear_error():
    with pytest.raises(MetricsError, match="Latitude"):
        m.haversine_m(95, 0, 0, 0)


def test_zero_distance_run_has_no_pace_but_is_valid():
    rec = RunRecord(datetime(2026, 1, 5), 0, 600)
    assert rec.pace_s_per_mile is None
    with pytest.raises(MetricsError, match="zero distance"):
        m.pace_s_per_mile(0, 600)


def test_negative_or_nan_values_rejected():
    with pytest.raises(MetricsError):
        RunRecord(datetime(2026, 1, 5), -5, 600)
    with pytest.raises(MetricsError):
        RunRecord(datetime(2026, 1, 5), float("nan"), 600)
