# sweatsheet — SPEC

## 1\. Overview

*TODO: One paragraph: what it does, for whom (Emily Michelsen).*

## 2\. Input

*TODO: What folder layout is accepted (activities.csv \+ activities/)*

*TODO: Which file types: GPX, FIT, .gz*

*TODO: Which activities count as runs ("Activity Type" \== "Run")*

*TODO: What happens with a bad, empty, or missing file*

## 3\. Data model

All shared data shapes live in models.py. Distances are stored in **meters** and durations in **seconds**; miles and pace are derived.

### TrackPoint

One GPS sample from a GPX file.

| Field | Type | Notes |
| :---- | :---- | :---- |
| lat | float | degrees, must be in \-90..90 |
| lon | float | degrees, must be in \-180..180 |
| time | datetime or None | timestamp of the sample |

 

### RunRecord

One run, in the single shape that both the GPX and CSV parsers produce. Immutable (frozen=True) and validated when created.

| Field | Type | Notes |
| :---- | :---- | :---- |
| start | datetime | when the run began |
| distance\_m | float | total distance in meters, \>= 0 |
| duration\_s | float | total elapsed time in seconds, \>= 0 |
| source | str | where it came from, e.g. "gpx", "csv" (default "unknown") |
| name | str | optional label, e.g. file name or activity title |
~
 

Derived properties:

| Property | Meaning |
| :---- | :---- |
| run\_date | the date part of start |
| distance\_miles | distance\_m / 1609.344 |
| pace\_s\_per\_mile | duration\_s / distance\_miles in seconds per mile, or None if distance is 0 |

 

Validation (raises MetricsError): start must be a datetime; distance and duration must be finite numbers (not NaN or infinity) and not negative.

### PeriodSummary

One row of the weekly or monthly summary (the same class is used for both).

| Field | Type | Notes |
| :---- | :---- | :---- |
| period\_start | date | the Monday (weekly) or the 1st of the month (monthly) |
| total\_miles | float | sum of run distances in the period |
| run\_count | int | number of runs in the period |
| longest\_run\_miles | float | longest single run, 0.0 if no runs |
| avg\_pace\_s\_per\_mile | float or None | distance-weighted pace; None if no miles in the period |

 

avg\_pace property: the same pace formatted as m:ss, or \-- if there is none.

### JumpWarning

One weekly-mileage warning.

| Field | Type | Notes |
| :---- | :---- | :---- |
| week\_start | date | Monday of the week that jumped |
| previous\_miles | float | miles in the week before |
| current\_miles | float | miles in this week |
| pct\_increase | float | 0.5 means \+50% |
| message | str | friendly text for the user |

 

### MetricsError

A ValueError subclass raised for bad input. Its message is written to be shown to the user as-is. 

## 4\. Calculations

All calculations are in metrics.py.

### Distance

•       Haversine (great-circle) distance between consecutive trackpoints, summed in file order.

•       Earth radius: **6,371,008.8 m** (mean radius). Meters per mile: **1609.344**.

•       No smoothing or filtering of GPS noise is applied.

### Duration

•       Elapsed seconds from the first trackpoint's timestamp to the last one's. Paused time is included.

### Pace

•       Pace \= duration / distance, in seconds per mile.

•       Shown as m:ss per mile, rounded to the nearest second (e.g. 487.0 s becomes 8:07). No pace is shown as \--.

•       A run with zero distance has no pace (None).

•       The pace of a week or month is **distance-weighted**: total time of the runs that covered distance divided by their total miles. It is not the average of the individual run paces. Example: 1 mi at 8:00 plus 3 mi at 10:00 gives 9:30, not 9:00.

### Week and month

•       A week runs Monday to Sunday (ISO weeks). A month is a calendar month.

•       A run belongs to the period containing the date of its start, exactly as recorded. metrics.py does not convert time zones, so the parser must supply start in the runner's local time if local weeks are wanted.

•       Periods with no runs between the first and last run are included as zero-mile rows (fill\_gaps=True, the default). They can be left out with fill\_gaps=False.

•       Empty input gives an empty summary.

### Pace trend

•       The slope is a least-squares line through the pace of each period, in seconds per mile per period. Negative means getting faster, positive means getting slower. Periods without a pace are skipped; fewer than 2 periods with a pace gives no slope.

•       Label: slope below \-2 s/mi per period is "getting faster", above \+2 is "getting slower", otherwise "holding steady". With no slope: "not enough data to see a pace trend yet".

•       Rolling pace: the mean pace over the last N periods that had a pace (default N \= 4), aligned with the summary rows.

### Weekly mileage warning

•       A week triggers a warning when its miles are **more than 10%** above the previous week's (WEEKLY\_JUMP\_THRESHOLD \= 0.10). Exactly \+10% does not warn.

•       20 to 30 miles (+50%) warns. 20 to 21 miles (+5%) does not. A decrease never warns.

•       If the previous week has 0 miles there is no baseline, so there is no warning.

•       Optional min\_previous\_miles ignores jumps from very light weeks (default 0, so off).

•       Message format: Heads up\! Week of Jan 12: 30.0 mi is 50% more than the week before (20.0 mi). Ramping up gradually (about 10% per week) helps prevent injury.

### Invalid input

Each of these raises MetricsError with a readable message instead of crashing:

| Situation | Message starts with |
| :---- | :---- |
| fewer than 2 trackpoints (including an empty track) | Need at least 2 GPS trackpoints |
| trackpoint without a timestamp | GPS trackpoints are missing timestamps. |
| timestamps mix timezone-aware and naive times | GPS timestamps mix timezone-aware and naive times. |
| timestamps do not increase | GPS timestamps do not move forward in time. |
| latitude outside \-90..90 | Latitude ... is out of range |
| longitude outside \-180..180 | Longitude ... is out of range |
| pace asked for a zero-distance run | Pace is undefined for a run with zero distance. |
| negative, NaN or infinite distance or duration | Run '\<name\>': distance ... or duration ... |
| period is not week or month | period must be 'week' or 'month'. |

## 5\. Command line

The command line is in cli.py. It reads a Strava export, summarizes the runs with metrics.py, prints a table, and can save a CSV and a chart (plot.py).

**Usage**

sweatsheet FOLDER \[--monthly\] \[--out FILE.csv\] \[--plot FILE.png\] \[--last N\] \[--year YYYY\]

| Option | Meaning | Default |
| ----- | ----- | ----- |
| FOLDER | Strava export folder containing activities.csv | required |
| \--monthly | group by calendar month instead of week | weekly |
| \--out FILE.csv | save every period as a CSV | not saved |
| \--plot FILE.png | save a bar chart of miles per period | not saved |
| \--last N | number of recent periods to print and chart | 12 |
| \--year YYYY | print and chart one year instead of the recent periods | off |

**Screen output:** the number of runs read; a table (Start, Miles, Runs, Longest, Pace) with \! marking mileage-jump weeks; the pace trend; and the warning messages for the periods shown. Warnings are weekly only.

**CSV columns, in order:** period\_start, total\_miles, run\_count, longest\_run\_miles, avg\_pace, warning. Every period is included, oldest first, even with \--last or \--year. warning is yes or empty.

**Chart:** one bar per period, titled “Recent \- Weekly Mileage” or “\<year\> \- Weekly Mileage” (Monthly with \--monthly). Gray bars are normal weeks; white striped bars are mileage jumps. A legend explains both. Gray and stripes are used instead of color so the chart prints clearly in black and white.

**Errors**

| Situation | What happens | Exit code |
| ----- | ----- | ----- |
| success | summary printed | 0 |
| a bad row in activities.csv | warning: skipped line N: ...; other rows are still used | 0 |
| folder missing | error: folder not found: ... | 1 |
| no activities.csv | error: no activities.csv in ... | 1 |
| no runs | error: no runs found in ... | 1 |
| \--year with no runs | error: no runs in YYYY | 1 |
| bad option (e.g. \--last 0\) | usage message | 2 |

Errors print one line to stderr, with no traceback.

Time zones: under discussion (see Section 4).

## 6\. Tests

*TODO: Test cases for other modules*

### Distance accuracy and pace

| Test | What it checks |
| :---- | :---- |
| test\_haversine\_one\_degree\_at\_equator | 1 degree of longitude on the equator is about 111,195 m (within 0.1%) |
| test\_three\_point\_gpx\_distance\_and\_pace | A 3-point track gives 2223.9 m (1.3819 mi) in 1200 s, pace 868.4 s/mi \= 14:28 |
| test\_pace\_exact\_mile | 1609.344 m in 480 s gives a pace of 480 s/mi \= 8:00 |
| test\_format\_pace\_rounds\_and\_handles\_none | 479.6 s formats as 8:00; no pace formats as \-- |

 

### Aggregation

| Test | What it checks |
| :---- | :---- |
| test\_weekly\_totals\_counts\_and\_longest | Monday-to-Sunday weeks: correct miles, run count and longest run; the next Monday starts a new week |
| test\_monthly\_totals | Runs are grouped by calendar month, including across a month boundary |
| test\_gap\_weeks\_are\_filled\_with\_zero | Weeks with no runs appear as zero-mile rows with no pace; fill\_gaps=False leaves them out |
| test\_period\_pace\_is\_distance\_weighted | 1 mi at 8:00 plus 3 mi at 10:00 gives 9:30 |
| test\_empty\_input\_gives\_empty\_summary | No runs gives an empty list |

 

### Weekly mileage warning

| Test | What it checks |
| :---- | :---- |
| test\_20\_to\_30\_warns | 20 to 30 miles gives one warning with a 50% increase |
| test\_20\_to\_21\_does\_not\_warn | 20 to 21 miles gives no warning |
| test\_exactly\_at\_threshold\_does\_not\_warn | 20 to 22 miles (exactly \+10%) gives no warning |
| test\_decrease\_does\_not\_warn | 30 to 10 miles gives no warning |
| test\_zero\_previous\_week\_does\_not\_crash\_or\_warn | A week after a 0-mile week has no baseline: no warning, no crash |

 

### Pace trend

| Test | What it checks |
| :---- | :---- |
| test\_pace\_trend\_faster\_slower\_steady | Slope sign, size (-30 s/mi per week) and labels for faster, slower and steady; one week gives no slope |
| test\_rolling\_pace\_window | Rolling pace averages the last N periods |

 

### Fault tolerance

| Test | What it checks |
| :---- | :---- |
| test\_single\_trackpoint\_gives\_clear\_error | One trackpoint gives a clear MetricsError |
| test\_empty\_track\_gives\_clear\_error | No trackpoints gives a clear MetricsError |
| test\_missing\_timestamps\_gives\_clear\_error | Trackpoints without times give a clear MetricsError |
| test\_backwards\_timestamps\_give\_clear\_error | Timestamps that go backwards give a clear MetricsError |
| test\_bad\_coordinates\_give\_clear\_error | A latitude of 95 gives a clear MetricsError |
| test\_zero\_distance\_run\_has\_no\_pace\_but\_is\_valid | A zero-distance run is valid with no pace; asking for its pace raises a clear error |
| test\_negative\_or\_nan\_values\_rejected | Negative or NaN distance is rejected when the record is created |

### Command line (test\_cli.py)

| Test | What it checks |
| ----- | ----- |
| test\_help\_works | \--help prints usage and exits 0 |
| test\_csv\_has\_right\_columns\_and\_values | CSV columns in order; a 20 mi week; a ride is ignored; pace 9:00 |
| test\_jump\_warning\_is\_printed\_and\_saved | 20 → 30 mi prints a 50% warning and writes yes |
| test\_monthly | \--monthly gives one January row of 50 mi |
| test\_late\_sunday\_run\_stays\_in\_sunday\_week | Monday 2 AM UTC (Sunday 9 PM Boston) counts in Sunday’s week |
| test\_plot\_makes\_a\_png | \--plot creates a PNG file |
| test\_missing\_folder\_gives\_error | missing folder exits 1 with error: folder not found |
| test\_no\_runs\_gives\_error | an export with only rides exits 1 |
| test\_bad\_row\_is\_skipped | a bad row prints a warning; the other runs are still read |
| test\_last\_must\_be\_positive | \--last 0 exits 2 |
| test\_year\_shows\_only\_that\_year | \--year 2026 shows only 2026; the CSV still has every week |
| test\_year\_with\_no\_runs\_gives\_error | \--year 2019 exits 1 |

