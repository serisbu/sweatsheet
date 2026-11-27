"""
parser.py -- read a Strava export's activities.csv into RunRecords.

WHAT THIS FILE DOES
    Strava's export produces a file called activities.csv with one row per activity 
    (runs, rides, yoga, ...).

    This module reads that CSV file, and (for now) keeps only the "runs". 
    It then turns each run into a RunRecord (defined in models.py). 

    For each run, the GPX/FIT file named in the CSV's "Filename" column (in the
    export's activities/ folder) is also read. The distance measured along its
    GPS points (haversine) is stored in RunRecord.gps_distance_m. The CSV's own
    distance stays in RunRecord.distance_m, so the two can be compared later.
    The file reading itself is in gps.py.
    Time zones are not handled yet.

HOW THE REST OF THE PROJECT USES IT
    Import the function:        `from sweatsheet.parser import load_export`
    Pass the Strava folder:     `result = load_export("path/to/export_folder")`
    Main output:                `result.runs` -> `list[RunRecord]`(oldest first)
    Error handling:             `result.problems` -> `list[str]` (one readable message per skipped row)
    GPS file warnings:          `result.notes` -> `list[str]` (run kept, see below)
    Skip the GPS files:         `load_export(folder, read_gps=False)` (faster)

ERROR HANDLING (not fully implemented yet)
    * Problems with the whole file (missing, empty, not a Strava export)
      raise MetricsError, and the load stops:
        - the folder doesn't exist, or isn't a folder
        - there is no activities.csv in the folder
        - activities.csv can't be read (no permission, not UTF-8 text)
        - activities.csv is empty
        - the header row is missing a required column
          (Activity Date, Activity Type, Elapsed Time, Distance)
        - the file has no runs

    * Problems with a single row (bad date, missing distance) do NOT stop the load.
      The row is skipped and one message is added to `result.problems`:
        - the date isn't in Strava's format (e.g. "Nov 3, 2026, 12:01:22 PM")
        - distance or elapsed time is empty or not a number
        - distance or elapsed time is negative, NaN or infinite
        
      Message format:
        "activities.csv line 14 ('Morning Run'): Could not read the date 'Nov 31, 2026'."

    * Problems with a run's GPS file do NOT skip the run. The run keeps its CSV
      distance, gps_distance_m stays None, and a message is added to `result.notes`:
        - the file is damaged or isn't valid GPX/FIT (one message per file)
        - files listed in the CSV that aren't in the folder (one message in total)
        - file types that can't be read, e.g. .tcx, or FIT files when the
          garmin-fit-sdk package isn't installed (one message per kind)
        - the file path points outside the export folder (one message per file)
        - the CSV has no "Filename" column (one message)

    * Silently ignored (not an error, no message):
        - blank lines
        - activities that aren't runs (rides, yoga, walks, ...)
        - a run with no Filename (e.g. entered by hand), or whose file has no
          GPS positions (e.g. an indoor run): gps_distance_m is just None
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .models import MetricsError, RunRecord
from .gps import NoGpsError, UnsupportedTrackError, gps_distance_m



# CSV_NAME:     the file we look for inside the export folder.
# RUN_TYPES:    which "Activity Type" values count as a run.
# STRAVA_DATE_FORMAT: how Strava writes dates, e.g. "Nov 3, 2026, 12:01:22 PM".
CSV_NAME = "activities.csv"
RUN_TYPES = frozenset({"Run"})
STRAVA_DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"



# A small container for the two things a load produces: runs and any problems.
# So the CLI can decide how to show them, and tests can check them.
# field(default_factory=list) gives every LoadResult its own new empty list.
@dataclass
class LoadResult:
    """What a load produced: the runs, plus anything that was skipped.

    problems: rows that were skipped (the run is NOT in `runs`).
    notes:    GPS-file warnings (the run IS in `runs`, with its CSV distance).
    """
    runs: list[RunRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# GPS-file problems that would repeat hundreds of times (every file missing,
# every file the same unsupported type) are counted here and reported once.
@dataclass
class _GpsTally:
    missing: list[str] = field(default_factory=list)            # file names
    unsupported: dict[str, int] = field(default_factory=dict)   # reason -> count


def _gps_distance_for(export_dir: Path, filename: str, label: str,
                      result: LoadResult, tally: _GpsTally) -> float | None:
    """GPS distance in meters for one run's activity file, or None.

    Never raises: whatever is wrong with the file, the run is kept.
    `export_dir` must already be resolved (absolute).
    """
    filename = filename.strip()
    if not filename:
        return None  # e.g. an activity entered by hand: there is no file
    target = (export_dir / filename).resolve()
    if not target.is_relative_to(export_dir):
        result.notes.append(
            f"The file '{filename}' is outside the export folder, so it was "
            f"not read (run '{label}'; using the CSV distance).")
        return None
    if not target.exists():
        # Strava lists files as 'activities/123.fit.gz'. If the export was
        # unzipped so the files sit next to activities.csv (no 'activities'
        # subfolder), look for the bare file name there instead.
        beside_csv = export_dir / Path(filename).name
        if beside_csv.is_file():
            target = beside_csv
    try:
        return gps_distance_m(target)
    except NoGpsError:
        pass  # a file with no GPS positions (indoor run) is not a problem
    except FileNotFoundError:
        tally.missing.append(filename)
    except UnsupportedTrackError as exc:
        tally.unsupported[exc.reason] = tally.unsupported.get(exc.reason, 0) + 1
    except MetricsError as exc:
        result.notes.append(f"{exc} (run '{label}'; using the CSV distance)")
    return None



# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------

def parse_strava_date(text: str) -> datetime:
    """'Nov 3, 2026, 12:01:22 PM' -> datetime(2026, 11, 3, 12, 1, 22)."""
    try:
        return datetime.strptime(text.strip(), STRAVA_DATE_FORMAT)
    except ValueError as exc:
        raise MetricsError(f"Could not read the date '{text}'.") from exc


def _parse_number(text: str) -> float | None:
    """
    For a missing value, returns None instead of raising error.
    """
    text = text.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None



# --------------------------------------------------------------------------
# Column Header lookup
# --------------------------------------------------------------------------
# Takes Strava's activities.csv header row (a list of column names), 
# returns a dictionary with the column names as the keys. 
# 
# Some columns in the CSV are duplicates;  e.g. "Distance" appears twice (in km and in meters).
# A regular {name: position} dict would overwrite the 1st "Distance" with the 2nd (same key, new value)
#
#     header = ["Activity ID", "Distance", "Distance"]
#     result = {"Activity ID": [0], "Distance": [1, 2]}

def _column_positions(header: list[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        positions.setdefault(name.strip(), []).append(i)
    return positions


# --------------------------------------------------------------------------
# Reading activities.csv
# --------------------------------------------------------------------------

def read_activities_csv(path: str | Path, read_gps: bool = True) -> LoadResult:
    """Read runs out of a Strava activities.csv.

    Columns used:
      Activity Date    -> RunRecord.start
      Activity Type    -> only rows whose type is in RUN_TYPES ("Run") are kept
      Activity Name    -> RunRecord.name (for messages and reports)
      Distance (2nd)   -> RunRecord.distance_m, in meters
      Distance (1st)   -> fallback if the 2nd is empty: km * 1000
      Elapsed Time(1st)-> RunRecord.duration_s, in seconds (includes pauses)
      Filename         -> the run's GPX/FIT file (path relative to the folder
                          holding activities.csv), read for RunRecord.gps_distance_m

    read_gps=False doesn't open the activity files: faster, and
    gps_distance_m stays None.
    """
    path = Path(path)
    result = LoadResult()

    # ---- 1: read the whole file into a list of rows ----------------
    # encoding="utf-8-sig" strips the invisible byte-order mark at the start of the file, if present.
    # newline="" so quoted fields that contain line breaks (e.g. descriptions) are read correctly.
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        raise MetricsError(f"Could not find {path}.") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise MetricsError(f"Could not read {path.name}: {exc}") from exc

    if not rows:
        raise MetricsError(f"{path.name} is empty.")

    # ---- 2: check the header looks like a Strava export ------------
    # If a required column is missing, the file is probably not a Strava csv.
    cols = _column_positions(rows[0])
    required = ("Activity Date", "Activity Type", "Elapsed Time", "Distance")
    missing = [c for c in required if c not in cols]
    if missing:
        raise MetricsError(
            f"{path.name} does not look like a Strava export "
            f"(missing columns: {', '.join(missing)})."
        )

    # ---- Helper: get one cell by column name ----------------------
    # cell(row, "Distance", 1) = the value in the 2nd "Distance" column.
    # Returns "" (instead of crashing) if the column doesn't exist, 
    # or the row is shorter than the header (a truncated/broken line).
    def cell(row: list[str], name: str, nth: int = 0) -> str:
        idx = cols.get(name, [])
        if nth >= len(idx) or idx[nth] >= len(row):
            return ""
        return row[idx[nth]]

    # ---- GPS files: where they are and whether we can look for them -
    export_dir = path.parent.resolve()
    tally = _GpsTally()
    gps_on = read_gps and "Filename" in cols
    if read_gps and not gps_on:
        result.notes.append(
            f"{path.name} has no 'Filename' column, so GPS distances were "
            "not calculated.")

    # ---- 3: turn each run row into a RunRecord ---------------------
    # line_no starts at 2 (line 1 is the header); used in error messages.

    for line_no, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # completely blank line: ignore
        if cell(row, "Activity Type").strip() not in RUN_TYPES:
            continue  # ride, yoga, walk, ...: not a "Run"

        # Turn this row into one RunRecord and append it to result.runs.
        # `label` names the run in error messages (the line number, if the activity has no name).
        label = cell(row, "Activity Name").strip() or f"line {line_no}"

        try:
            start = parse_strava_date(cell(row, "Activity Date"))

            # Prefer the meters column; fall back to km if empty.
            distance_m = _parse_number(cell(row, "Distance", 1))
            if distance_m is None:
                km = _parse_number(cell(row, "Distance", 0))
                distance_m = km * 1000 if km is not None else None

            duration_s = _parse_number(cell(row, "Elapsed Time", 0))

            if distance_m is None or duration_s is None:
                raise MetricsError("distance or elapsed time is missing.")

            # RunRecord checks its own values (no negatives, no NaN) and
            # raises MetricsError if they are bad.
            record = RunRecord(
                start=start,
                distance_m=distance_m,
                duration_s=duration_s,
                source="csv",
                name=label,
            )

            # Only now (the row is valid, so the run is kept) look at its GPS
            # file. A bad file never removes the run; see _gps_distance_for.
            if gps_on:
                gps_m = _gps_distance_for(export_dir, cell(row, "Filename"),
                                          label, result, tally)
                if gps_m is not None:
                    record = replace(record, gps_distance_m=gps_m)
            result.runs.append(record)

        # Any MetricsError for this row: record it and move on to the next row.
        except MetricsError as exc:
            result.problems.append(
                f"{path.name} line {line_no} ('{label}'): {exc}")

    # ---- GPS problems that are reported once, not once per file ----
    if tally.missing:
        result.notes.append(
            f"{len(tally.missing)} activity file(s) listed in {path.name} "
            f"were not found (for example '{tally.missing[0]}'), so GPS "
            "distance was not calculated for those runs.")
    for reason, count in tally.unsupported.items():
        result.notes.append(f"{count} activity file(s) were not read: {reason}.")

    return result



# --------------------------------------------------------------------------
# Entry point for the CLI
# --------------------------------------------------------------------------
# The CLI only needs to call this one function with a folder path. 
# It finds activities.csv inside, reads it, and sorts the runs by start time (oldest first).

def load_export(folder: str | Path, read_gps: bool = True) -> LoadResult:
    """Load all runs from a Strava export folder (via its activities.csv).

    Raises MetricsError if the folder or CSV is missing, or has no runs.
    read_gps=False skips the GPX/FIT files (see read_activities_csv).
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise MetricsError(f"{folder} is not a folder.")

    csv_path = folder / CSV_NAME
    if not csv_path.is_file():
        raise MetricsError(
            f"No {CSV_NAME} found in {folder}. Point sweatsheet at the "
            "unzipped Strava export folder.")

    result = read_activities_csv(csv_path, read_gps=read_gps)

    if not result.runs:
        raise MetricsError(
            f"No runs found in {csv_path}. Only activities with "
            f"Activity Type {', '.join(sorted(RUN_TYPES))} are counted.")

    result.runs.sort(key=lambda r: r.start)
    return result

# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------
# load_export() returns a LoadResult. Each run in result.runs is a RunRecord
# (see models.py). Example: this row in activities.csv
#
#     Activity Date = "Nov 3, 2026, 12:01:22 PM"
#     Activity Name = "Morning Run"
#     Activity Type = "Run"
#     Elapsed Time  = 3240                  (1st "Elapsed Time" column)
#     Distance      = 6.68 ... 6683.6       (1st in km, 2nd in meters)
#
# becomes:
#
#     RunRecord(
#         start=datetime(2026, 11, 3, 12, 1, 22),
#         distance_m=6683.6,
#         duration_s=3240.0,
#         source="csv",
#         name="Morning Run",
#     )
#
# Error handling: result.problems is [] if no rows were skipped.
