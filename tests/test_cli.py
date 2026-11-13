"""Tests for cli.py and plot.py using a tiny made-up Strava export."""

import csv

import pytest

from sweatsheet import cli

# Setup: small fake Strava export
MILE = 1609.344
# Strava columns we read
HEADER = [
    "Activity Date",
    "Activity Type",
    "Elapsed Time",
    "Distance",
    "Elapsed Time",
    "Distance",
]


def make_export(folder, rows):
    """Write folder/activities.csv. Each row is (UTC date, type, miles)."""
    folder.mkdir()
    with open(folder / "activities.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for when, kind, miles in rows:
            secs = miles * 9 * 60  # 9:00 per mile
            writer.writerow([when, kind, secs, miles * 1.609, secs, miles * MILE])
    return folder


@pytest.fixture
def export(tmp_path):
    """20 miles, then 30 miles the next week, plus a bike ride."""
    return make_export(
        tmp_path / "export",
        [
            ("Jan 5, 2026, 12:00:00 PM", "Run", 20),
            ("Jan 6, 2026, 12:00:00 PM", "Ride", 50),
            ("Jan 12, 2026, 12:00:00 PM", "Run", 30),
        ],
    )


# Read a saved CSV into a list of rows
def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# 1. Help
def test_help_works(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert "usage: sweatsheet" in capsys.readouterr().out


# 2. CSV output and warnings
def test_csv_has_right_columns_and_values(export, tmp_path):
    out = tmp_path / "weekly.csv"
    assert cli.main([str(export), "--out", str(out)]) == 0
    rows = read_rows(out)
    assert list(rows[0]) == cli.CSV_COLUMNS
    assert [r["period_start"] for r in rows] == ["2026-01-05", "2026-01-12"]
    assert rows[0]["total_miles"] == "20.00"
    assert rows[0]["run_count"] == "1"  # ride is ignored
    assert rows[1]["avg_pace"] == "9:00"


def test_jump_warning_is_printed_and_saved(export, tmp_path, capsys):
    out = tmp_path / "weekly.csv"
    cli.main([str(export), "--out", str(out)])
    assert "50% more" in capsys.readouterr().out
    assert [r["warning"] for r in read_rows(out)] == ["", "yes"]


def test_monthly(export, tmp_path):
    out = tmp_path / "monthly.csv"
    cli.main([str(export), "--monthly", "--out", str(out)])
    rows = read_rows(out)
    assert len(rows) == 1
    assert rows[0]["total_miles"] == "50.00"


# 3. Week boundaries (Boston time)
def test_late_sunday_run_stays_in_sunday_week(tmp_path):
    # Mon Jan 12, 2 AM UTC is Sun Jan 11, 9 PM in Boston.
    export = make_export(tmp_path / "export", [("Jan 12, 2026, 2:00:00 AM", "Run", 5)])
    out = tmp_path / "weekly.csv"
    cli.main([str(export), "--out", str(out)])
    assert read_rows(out)[0]["period_start"] == "2026-01-05"


# 4. Chart
def test_plot_makes_a_png(export, tmp_path):
    png = tmp_path / "weekly.png"
    assert cli.main([str(export), "--plot", str(png)]) == 0
    assert png.read_bytes().startswith(b"\x89PNG")


# 5. Errors
def test_missing_folder_gives_error(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope")]) == 1
    assert capsys.readouterr().err.startswith("error: folder not found")


def test_no_runs_gives_error(tmp_path, capsys):
    export = make_export(
        tmp_path / "export", [("Jan 5, 2026, 12:00:00 PM", "Ride", 10)]
    )
    assert cli.main([str(export)]) == 1
    assert "error: no runs found" in capsys.readouterr().err


def test_bad_row_is_skipped(export, capsys):
    with open(export / "activities.csv", "a", newline="") as f:
        csv.writer(f).writerow(["not a date", "Run", 1, 1, 1, 1])
    assert cli.main([str(export)]) == 0
    captured = capsys.readouterr()
    assert "warning: skipped line 5" in captured.err
    assert "Read 2 runs." in captured.out


# 6. --last and --year options
def test_last_must_be_positive(export):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([str(export), "--last", "0"])
    assert exit_info.value.code == 2


def test_year_shows_only_that_year(tmp_path, capsys):
    export = make_export(
        tmp_path / "export",
        [
            ("Dec 1, 2025, 12:00:00 PM", "Run", 10),
            ("Jan 5, 2026, 12:00:00 PM", "Run", 12),
        ],
    )
    out = tmp_path / "weekly.csv"
    assert cli.main([str(export), "--year", "2026", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "2026-01-05" in printed and "2025-12-01" not in printed
    assert len(read_rows(out)) == 6  # the CSV still has every week


def test_year_with_no_runs_gives_error(export, capsys):
    assert cli.main([str(export), "--year", "2019"]) == 1
    assert "error: no runs in 2019" in capsys.readouterr().err
