# Sweatsheet

Running summaries from your own Strava export.

Point `sweatsheet` at the unzipped export folder and it gives you weekly or monthly mileage, run counts, longest run, average pace, a pace trend, and a friendly warning when your weekly mileage jumps too fast. You can save the results as a CSV and a bar chart (PNG). Everything runs locally on your own files. There is no Strava login, API key, or subscription.

## Features

- **Weekly or monthly totals**: total miles, number of runs, longest run, and average pace per period.
- **Pace trend**: tells you whether you are getting faster, getting slower, or holding steady.
- **Mileage-jump warning**: flags any week more than 10% above the week before.
- **CSV and chart export**: a full CSV of every period and a black-and-white-friendly bar chart.
- **Forgiving input**: a bad row in `activities.csv` is skipped with a warning instead of crashing the run.

## Install
### TODO: Figure out better installation
Requires Python 3.10 or newer.

```bash
git clone <this repo>
cd sweatsheet
pip install -e .            # add ".[dev]" to also install pytest
```

This installs the `sweatsheet` command. Dependencies are `matplotlib` (charts) and `garmin-fit-sdk` (reading `.fit` files).

## Get your data

1. Request your archive from Strava (Settings → My Account → Download or Delete Your Account → Request Your Archive).
2. Unzip the file Strava emails you.
3. Use the unzipped folder as `FOLDER` below. `activities.csv` must sit directly inside it, not in a subfolder.

## Usage

```
sweatsheet FOLDER [--monthly] [--out FILE.csv] [--plot FILE.png] [--last N] [--year YYYY] [--gps]
```

| Option | Meaning | Default |
| --- | --- | --- |
| `FOLDER` | Unzipped Strava export folder containing `activities.csv` | required |
| `--monthly` | Group by calendar month instead of week | weekly |
| `--out FILE.csv` | Save every period as a CSV | not saved |
| `--plot FILE.png` | Save a bar chart of miles per period | not saved |
| `--last N` | Number of recent periods to print and chart | 12 |
| `--year YYYY` | Print and chart one year instead of the recent periods | off |
| `--gps` | Also read the GPX/FIT files and measure distance from GPS points (see [GPS files](#gps-files)) | off |

### Examples

```bash
# Last 12 weeks, printed to the screen
sweatsheet ~/Downloads/strava-export

# Everything in 2026, saved as a CSV and a chart
sweatsheet ~/Downloads/strava-export --year 2026 --out 2026.csv --plot 2026.png

# Monthly view
sweatsheet ~/Downloads/strava-export --monthly --last 24
```

### Sample output

```
Read 7 runs.

Start        Miles  Runs  Longest   Pace
2026-10-05     6.2     2      3.1  13:41
2026-10-12     9.9     1      9.9   9:03  !
2026-10-19     9.9     2      5.0   9:03
2026-10-26    14.9     1     14.9   8:03  !
2026-11-02     5.0     1      5.0   9:03

Pace trend: getting faster

Heads up! Week of Oct 12: 9.9 mi is 60% more than the week before (6.2 mi). Ramping up gradually (about 10% per week) helps prevent injury.

Heads up! Week of Oct 26: 14.9 mi is 50% more than the week before (9.9 mi). Ramping up gradually (about 10% per week) helps prevent injury.
```

A `!` marks a week whose mileage jumped. Warnings are weekly only; `--monthly` does not show them.

## Output files

**CSV** (`--out`) has one row per period, oldest first, with these columns:

`period_start, total_miles, run_count, longest_run_miles, avg_pace, warning`

The CSV always contains every period, even when `--last` or `--year` limits what is printed. `warning` is `yes` or empty.

**Chart** (`--plot`) has one bar per period. Gray bars are normal weeks; white striped bars are mileage jumps. Gray and stripes (rather than color) keep it readable when printed in black and white.

## How the numbers are worked out

- **Weeks** run Monday to Sunday. **Months** are calendar months. Weeks or months with no runs between your first and last run show as zero-mile rows.
- **Run times** are converted from Strava's UTC to `America/New_York` before runs are grouped, so a Sunday-evening run lands in Sunday's week (see [Limitations](#limitations)).
- **Pace** is elapsed time divided by distance, in minutes:seconds per mile. A period's pace is distance-weighted (total time ÷ total miles), not an average of individual paces: 1 mi at 8:00 plus 3 mi at 10:00 is 9:30.
- **Elapsed time includes pauses**, since that is what Strava's CSV records.
- **Pace trend** is a least-squares line through the period paces. A slope below −2 s/mi per period is "getting faster", above +2 is "getting slower", otherwise "holding steady".
- **Mileage jump** means more than +10% over the previous week. Exactly +10% does not warn, a decrease never warns, and a week after a zero-mile week has no baseline so it does not warn.

## What counts as a run

Only activities whose **Activity Type is exactly `Run`** are counted. Rides, walks, yoga, and also Trail Run and Virtual Run are skipped silently.

## GPS files

Strava exports a GPX or FIT file for most activities. With `--gps`, sweatsheet opens the file named in each run's `Filename` column and measures distance along the GPS points with the haversine formula. Supported: `.gpx`, `.gpx.gz`, `.fit`, `.fit.gz`.

This is off by default. The GPS distance is stored next to the CSV distance so the two can be compared, but **all totals still use the distance from `activities.csv`**. A damaged, missing or unsupported GPS file never removes a run; you get a warning and the run keeps its CSV distance. Indoor runs with no GPS positions are not treated as a problem.

## Errors and warnings

Problems are printed to stderr as a single line with no traceback.

| Situation | Result | Exit code |
| --- | --- | --- |
| Success | Summary printed | 0 |
| A bad row in `activities.csv` (unreadable date, missing or negative distance or time) | `warning: skipped ...`, other rows still used | 0 |
| A problem with a GPS file (only with `--gps`) | `warning: ...`, run kept with its CSV distance | 0 |
| Folder missing, no `activities.csv`, unreadable or empty file, missing columns, or no runs | `error: ...` | 1 |
| `--year` with no runs in that year | `error: no runs in YYYY` | 1 |
| Bad option (for example `--last 0`) | Usage message | 2 |

## Project layout

```
src/sweatsheet/
  cli.py       command line, table and CSV output, local-time conversion
  parser.py    reads activities.csv (and GPS files) into RunRecords
  gps.py       reads GPX/FIT tracks and measures GPS distance
  metrics.py   distance, pace, weekly/monthly summaries, trend, jump warnings
  plot.py      mileage bar chart
  models.py    shared data shapes (RunRecord, PeriodSummary, JumpWarning, ...)
tests/         pytest suite for the CLI, GPS reading and metrics
SPEC.md        detailed specification
```

Distances are stored in meters and durations in seconds throughout; miles and pace are derived.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The FIT-file tests are skipped automatically if `garmin-fit-sdk` is not installed.

## Limitations

- **Time zone is fixed** to `America/New_York` (`LOCAL_TZ` in `src/sweatsheet/cli.py`). Edit that constant if you run elsewhere; there is no option for it yet.
- Only activities of type `Run` are counted.
- `.tcx` files are not supported.
- GPS distance is not yet used in any totals.
- Strava's date format (for example `Nov 3, 2026, 12:01:22 PM`) is expected, which is how an English-language export writes it.