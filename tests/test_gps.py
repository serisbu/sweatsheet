"""Tests for gps.py (GPX/FIT reading) and the GPS distance the loader adds.

All GPX and FIT files here are generated in the tests from made-up points, so
no real GPS data is needed. FIT tests are skipped if garmin-fit-sdk isn't
installed.
"""

import csv
import gzip
import importlib.util
import math
import struct
import sys
from datetime import datetime, timedelta, timezone

import pytest

from sweatsheet import cli, gps, parser
from sweatsheet.models import MetricsError, RunRecord
from sweatsheet.gps import NoGpsError, UnsupportedTrackError

MILE = 1609.344
T0 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
# 3 points on the equator, 0.01 degrees of longitude apart: 2 x ~1111.95 m
THREE_POINT_M = 2223.9

requires_fit = pytest.mark.skipif(
    importlib.util.find_spec("garmin_fit_sdk") is None,
    reason="garmin-fit-sdk is not installed")


# --------------------------------------------------------------------------
# Building test files
# --------------------------------------------------------------------------

THREE = [(0.0, 0.00, "2026-01-05T12:00:00Z"),
         (0.0, 0.01, "2026-01-05T12:10:00Z"),
         (0.0, 0.02, "2026-01-05T12:20:00Z")]


def gpx_bytes(*segments):
    """A Strava-style GPX file. Each segment is a list of (lat, lon, time|None)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx creator="test" version="1.1" '
           'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Run</name>']
    for seg in segments:
        out.append("<trkseg>")
        for lat, lon, when in seg:
            time = f"<time>{when}</time>" if when else ""
            out.append(f'<trkpt lat="{lat}" lon="{lon}"><ele>10.0</ele>{time}</trkpt>')
        out.append("</trkseg>")
    out.append("</trk></gpx>")
    return "".join(out).encode()


_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
_INVALID_SINT32 = 0x7FFFFFFF


def _crc16(data, crc=0):
    for byte in data:
        tmp = _CRC[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC[byte & 0xF]
        tmp = _CRC[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC[(byte >> 4) & 0xF]
    return crc


def _semicircles(degrees):
    return _INVALID_SINT32 if degrees is None else round(degrees * 2**31 / 180)


def fit_bytes(records):
    """A minimal FIT activity file. records: list of (datetime, lat|None, lon|None).

    None means "no GPS fix for this sample" (FIT's invalid value).
    """
    body = b""
    # definition + data: file_id (global message 0, local type 0)
    body += struct.pack("<BBBHB", 0x40, 0, 0, 0, 4)
    body += bytes([0, 1, 0x00,  1, 2, 0x84,  2, 2, 0x84,  4, 4, 0x86])
    body += struct.pack("<BBHHI", 0, 4, 255, 0,
                        int((records[0][0] - _FIT_EPOCH).total_seconds()))
    # definition: record (global message 20, local type 1)
    body += struct.pack("<BBBHB", 0x41, 0, 0, 20, 3)
    body += bytes([253, 4, 0x86,  0, 4, 0x85,  1, 4, 0x85])
    for when, lat, lon in records:
        body += struct.pack("<BIii", 0x01,
                            int((when - _FIT_EPOCH).total_seconds()),
                            _semicircles(lat), _semicircles(lon))
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    file = header + body
    return file + struct.pack("<H", _crc16(file))


def three_point_fit():
    return fit_bytes([(T0 + timedelta(minutes=10 * i), 0.0, 0.01 * i)
                      for i in range(3)])


# --------------------------------------------------------------------------
# GPX
# --------------------------------------------------------------------------

def test_three_point_gpx_distance_and_times(tmp_path):
    f = tmp_path / "run.gpx"
    f.write_bytes(gpx_bytes(THREE))
    assert gps.gps_distance_m(f) == pytest.approx(THREE_POINT_M, rel=1e-3)
    points = gps.read_track(f)
    assert len(points) == 3
    assert points[0].time == T0 and points[2].time == T0 + timedelta(minutes=20)


def test_gpx_segments_are_joined_in_order(tmp_path):
    # two segments (a pause): the gap between them is bridged by a straight line
    f = tmp_path / "run.gpx"
    f.write_bytes(gpx_bytes([(0, 0.00, None), (0, 0.01, None)],
                            [(0, 0.02, None), (0, 0.03, None)]))
    assert gps.gps_distance_m(f) == pytest.approx(3 * 1111.95, rel=1e-3)


def test_gpx_without_namespace_or_times(tmp_path):
    f = tmp_path / "run.gpx"
    f.write_bytes(b'<gpx><trk><trkseg><trkpt lat="0" lon="0"/>'
                  b'<trkpt lat="0" lon="0.01"/></trkseg></trk></gpx>')
    points = gps.read_track(f)
    assert [p.time for p in points] == [None, None]
    assert gps.gps_distance_m(f) == pytest.approx(1111.95, rel=1e-3)


def test_gpx_gz_is_read(tmp_path):
    f = tmp_path / "run.gpx.gz"
    f.write_bytes(gzip.compress(gpx_bytes(THREE)))
    assert gps.gps_distance_m(f) == pytest.approx(THREE_POINT_M, rel=1e-3)


def test_unreadable_time_is_just_none(tmp_path):
    f = tmp_path / "run.gpx"
    f.write_bytes(gpx_bytes([(0, 0, "not a time"), (0, 0.01, None)]))
    assert [p.time for p in gps.read_track(f)] == [None, None]


def test_empty_gpx_gives_clear_error(tmp_path):
    f = tmp_path / "empty.gpx"
    f.write_bytes(b"")
    with pytest.raises(MetricsError, match="empty.gpx is empty"):
        gps.read_track(f)


def test_malformed_gpx_gives_clear_error(tmp_path):
    f = tmp_path / "broken.gpx"
    f.write_bytes(b'<gpx><trk><trkseg><trkpt lat="0" lon="0">')
    with pytest.raises(MetricsError, match="not valid GPX"):
        gps.read_track(f)


def test_xml_that_is_not_gpx_gives_clear_error(tmp_path):
    f = tmp_path / "page.gpx"
    f.write_bytes(b"<html><body>hello</body></html>")
    with pytest.raises(MetricsError, match="does not look like a GPX"):
        gps.read_track(f)


def test_gpx_without_points_means_no_gps(tmp_path):
    f = tmp_path / "none.gpx"
    f.write_bytes(gpx_bytes([]))
    with pytest.raises(NoGpsError):
        gps.read_track(f)


def test_single_point_has_no_distance(tmp_path):
    f = tmp_path / "one.gpx"
    f.write_bytes(gpx_bytes([(0, 0, None)]))
    with pytest.raises(NoGpsError, match="fewer than 2"):
        gps.gps_distance_m(f)


def test_gpx_bad_coordinates_give_clear_errors(tmp_path):
    f = tmp_path / "bad.gpx"
    f.write_bytes(b'<gpx><trk><trkseg><trkpt lat="abc" lon="0"/></trkseg></trk></gpx>')
    with pytest.raises(MetricsError, match="GPS point 1 has no valid lat/lon"):
        gps.read_track(f)
    g = tmp_path / "far.gpx"
    g.write_bytes(gpx_bytes([(95, 0, None), (0, 0, None)]))
    with pytest.raises(MetricsError, match="Latitude"):
        gps.gps_distance_m(g)


def test_damaged_gz_gives_clear_error(tmp_path):
    f = tmp_path / "run.gpx.gz"
    f.write_bytes(b"this is not gzip data")
    with pytest.raises(MetricsError, match="could not be unzipped"):
        gps.read_track(f)
    g = tmp_path / "cut.gpx.gz"
    g.write_bytes(gzip.compress(gpx_bytes(THREE))[:-20])   # cut off
    with pytest.raises(MetricsError, match="could not be unzipped"):
        gps.read_track(g)


def test_unsupported_file_types(tmp_path):
    for name in ("run.tcx", "run.tcx.gz", "run"):
        with pytest.raises(UnsupportedTrackError) as info:
            gps.read_track(tmp_path / name)      # type is checked before reading
        assert "not supported" in str(info.value)
    with pytest.raises(UnsupportedTrackError) as info:
        gps.read_track(tmp_path / "run.tcx.gz")
    assert ".tcx" in info.value.reason


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        gps.read_track(tmp_path / "nope.gpx")


# --------------------------------------------------------------------------
# FIT
# --------------------------------------------------------------------------

@requires_fit
def test_three_point_fit_matches_gpx(tmp_path):
    # Regression guard: calling Decoder.read() after check_integrity() on the
    # same decoder silently returns no messages (every run would look "indoor").
    f = tmp_path / "run.fit"
    f.write_bytes(three_point_fit())
    assert gps.gps_distance_m(f) == pytest.approx(THREE_POINT_M, rel=1e-3)
    points = gps.read_track(f)
    assert len(points) == 3
    assert points[2].lat == pytest.approx(0.0, abs=1e-6)
    assert points[2].lon == pytest.approx(0.02, abs=1e-6)    # semicircles -> degrees
    assert points[0].time == T0


@requires_fit
def test_fit_gz_is_read(tmp_path):
    f = tmp_path / "run.fit.gz"
    f.write_bytes(gzip.compress(three_point_fit()))
    assert gps.gps_distance_m(f) == pytest.approx(THREE_POINT_M, rel=1e-3)


@requires_fit
def test_fit_samples_without_a_fix_are_skipped(tmp_path):
    f = tmp_path / "run.fit"
    f.write_bytes(fit_bytes([(T0, 0.0, 0.0),
                             (T0 + timedelta(minutes=5), None, None),
                             (T0 + timedelta(minutes=10), 0.0, 0.01)]))
    assert len(gps.read_track(f)) == 2
    assert gps.gps_distance_m(f) == pytest.approx(1111.95, rel=1e-3)


@requires_fit
def test_fit_without_any_gps_means_no_gps(tmp_path):
    f = tmp_path / "treadmill.fit"
    f.write_bytes(fit_bytes([(T0, None, None),
                             (T0 + timedelta(minutes=5), None, None)]))
    with pytest.raises(NoGpsError):
        gps.read_track(f)


@requires_fit
@pytest.mark.parametrize("label, make, message", [
    ("truncated", lambda d: d[:-30], "damaged"),
    ("bit flipped", lambda d: d[:40] + bytes([d[40] ^ 0xFF]) + d[41:], "damaged"),
    ("not a fit file", lambda d: b"hello, this is not a FIT file", "not a FIT file"),
    ("empty", lambda d: b"", "is empty"),
])
def test_bad_fit_files_give_clear_errors(tmp_path, label, make, message):
    f = tmp_path / "bad.fit"
    f.write_bytes(make(three_point_fit()))
    with pytest.raises(MetricsError, match=message) as info:
        gps.read_track(f)
    assert not isinstance(info.value, NoGpsError)


def test_fit_without_the_library_is_reported_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "garmin_fit_sdk", None)   # makes the import fail
    f = tmp_path / "run.fit"
    f.write_bytes(b"whatever")
    with pytest.raises(UnsupportedTrackError) as info:
        gps.read_track(f)
    assert "garmin-fit-sdk" in info.value.reason


# --------------------------------------------------------------------------
# RunRecord.gps_distance_m
# --------------------------------------------------------------------------

def test_gps_distance_defaults_to_none_and_converts_to_miles():
    run = RunRecord(T0, 1000, 600)
    assert run.gps_distance_m is None and run.gps_distance_miles is None
    run = RunRecord(T0, 1000, 600, gps_distance_m=MILE)
    assert run.gps_distance_miles == pytest.approx(1.0)
    assert run.distance_m == 1000          # the CSV distance is untouched


def test_bad_gps_distance_is_rejected():
    with pytest.raises(MetricsError, match="GPS distance cannot be negative"):
        RunRecord(T0, 1000, 600, gps_distance_m=-1)
    with pytest.raises(MetricsError, match="GPS distance is not a valid number"):
        RunRecord(T0, 1000, 600, gps_distance_m=math.nan)


# --------------------------------------------------------------------------
# The loader: parser.load_export fills in gps_distance_m
# --------------------------------------------------------------------------

HEADER = ["Activity Date", "Activity Name", "Activity Type", "Elapsed Time",
          "Distance", "Filename", "Elapsed Time", "Distance"]


def make_export(folder, rows, files=None, header=HEADER):
    """Write folder/activities.csv and folder/<files>.

    rows:  (UTC date, name, type, miles, filename)   the CSV's own distance
    files: {"activities/1.gpx": bytes, ...}
    """
    folder.mkdir()
    with open(folder / "activities.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for when, name, kind, miles, filename in rows:
            secs, meters = round(miles * 9 * 60), miles * MILE
            writer.writerow([when, name, kind, secs, f"{meters / 1000:.3f}",
                             filename, secs, f"{meters:.1f}"])
    for rel, data in (files or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return folder


D1, D2, D3 = ("Jan 5, 2026, 12:00:00 PM", "Jan 6, 2026, 12:00:00 PM",
              "Jan 7, 2026, 12:00:00 PM")


def test_csv_distance_is_kept_and_gps_distance_is_added(tmp_path):
    export = make_export(tmp_path / "e",
                         [(D1, "Morning Run", "Run", 1.5, "activities/1.gpx")],
                         {"activities/1.gpx": gpx_bytes(THREE)})
    result = parser.load_export(export)
    run = result.runs[0]
    assert run.distance_m == pytest.approx(1.5 * MILE, abs=0.1)     # from the CSV
    assert run.gps_distance_m == pytest.approx(THREE_POINT_M, rel=1e-3)  # haversine
    assert run.source == "csv" and result.notes == [] and result.problems == []


@requires_fit
def test_fit_gz_file_is_used_by_the_loader(tmp_path):
    export = make_export(tmp_path / "e",
                         [(D1, "Run", "Run", 1.5, "activities/1.fit.gz")],
                         {"activities/1.fit.gz": gzip.compress(three_point_fit())})
    run = parser.load_export(export).runs[0]
    assert run.gps_distance_m == pytest.approx(THREE_POINT_M, rel=1e-3)


def test_run_without_a_filename_is_not_an_error(tmp_path):
    export = make_export(tmp_path / "e", [(D1, "By hand", "Run", 3, "")])
    result = parser.load_export(export)
    assert result.runs[0].gps_distance_m is None and result.notes == []


def test_file_with_no_gps_is_not_an_error(tmp_path):
    export = make_export(tmp_path / "e",
                         [(D1, "Treadmill", "Run", 3, "activities/1.gpx")],
                         {"activities/1.gpx": gpx_bytes([])})
    result = parser.load_export(export)
    assert result.runs[0].gps_distance_m is None and result.notes == []


@requires_fit
def test_indoor_fit_is_not_an_error(tmp_path):
    indoor = fit_bytes([(T0, None, None), (T0 + timedelta(minutes=5), None, None)])
    export = make_export(tmp_path / "e",
                         [(D1, "Treadmill", "Run", 3, "activities/1.fit")],
                         {"activities/1.fit": indoor})
    result = parser.load_export(export)
    assert result.runs[0].gps_distance_m is None and result.notes == []


def test_missing_files_are_reported_once_and_runs_are_kept(tmp_path):
    export = make_export(tmp_path / "e", [
        (D1, "a", "Run", 3, "activities/a.fit.gz"),
        (D2, "b", "Run", 4, "activities/b.fit.gz"),
        (D3, "c", "Run", 5, "activities/c.gpx")])
    result = parser.load_export(export)
    assert [r.gps_distance_m for r in result.runs] == [None, None, None]
    assert len(result.runs) == 3
    assert len(result.notes) == 1
    assert "3 activity file(s)" in result.notes[0] and "not found" in result.notes[0]


def test_damaged_file_keeps_the_run_with_its_csv_distance(tmp_path):
    export = make_export(tmp_path / "e", [
        (D1, "Broken Run", "Run", 3, "activities/1.gpx"),
        (D2, "Fine Run", "Run", 4, "activities/2.gpx")],
        {"activities/1.gpx": b'<gpx><trk><trkseg><trkpt lat="0" lon="0">',
         "activities/2.gpx": gpx_bytes(THREE)})
    result = parser.load_export(export)
    broken, fine = result.runs
    assert broken.gps_distance_m is None
    assert broken.distance_m == pytest.approx(3 * MILE, abs=0.1)
    assert fine.gps_distance_m == pytest.approx(THREE_POINT_M, rel=1e-3)
    assert len(result.notes) == 1
    assert "Broken Run" in result.notes[0] and "using the CSV distance" in result.notes[0]
    assert result.problems == []


def test_unsupported_types_are_reported_once_per_kind(tmp_path):
    export = make_export(tmp_path / "e", [
        (D1, "a", "Run", 3, "activities/a.tcx.gz"),
        (D2, "b", "Run", 4, "activities/b.tcx.gz")])
    result = parser.load_export(export)
    assert len(result.runs) == 2
    assert result.notes == [
        "2 activity file(s) were not read: '.tcx' files are not supported yet."]


def test_only_runs_have_their_files_read(tmp_path):
    export = make_export(tmp_path / "e", [
        (D1, "Ride", "Ride", 20, "activities/missing.fit.gz"),
        (D2, "Run", "Run", 3, "")])
    result = parser.load_export(export)
    assert len(result.runs) == 1 and result.notes == []


def test_missing_filename_column_gives_one_note(tmp_path):
    header = ["Activity Date", "Activity Name", "Activity Type", "Elapsed Time",
              "Distance", "Elapsed Time", "Distance"]
    folder = tmp_path / "e"
    folder.mkdir()
    with open(folder / "activities.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerow([D1, "Run", "Run", 1800, "4.8", 1800, "4800"])
    result = parser.load_export(folder)
    assert len(result.runs) == 1 and result.runs[0].gps_distance_m is None
    assert len(result.notes) == 1 and "'Filename' column" in result.notes[0]


def test_read_gps_false_opens_nothing(tmp_path):
    export = make_export(tmp_path / "e",
                         [(D1, "Run", "Run", 3, "activities/missing.gpx")])
    result = parser.load_export(export, read_gps=False)
    assert result.runs[0].gps_distance_m is None and result.notes == []


def test_file_outside_the_export_folder_is_not_read(tmp_path):
    (tmp_path / "outside.gpx").write_bytes(gpx_bytes(THREE))
    export = make_export(tmp_path / "e",
                         [(D1, "Run", "Run", 3, "../outside.gpx")])
    result = parser.load_export(export)
    assert result.runs[0].gps_distance_m is None
    assert len(result.notes) == 1 and "outside the export folder" in result.notes[0]


def test_bad_csv_row_is_still_skipped_not_noted(tmp_path):
    export = make_export(tmp_path / "e", [
        ("not a date", "Bad", "Run", 3, "activities/1.gpx"),
        (D1, "Good", "Run", 3, "")],
        {"activities/1.gpx": gpx_bytes(THREE)})
    result = parser.load_export(export)
    assert [r.name for r in result.runs] == ["Good"]
    assert len(result.problems) == 1 and result.notes == []


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def test_cli_warns_about_a_damaged_gps_file_but_still_succeeds(tmp_path, capsys):
    export = make_export(
        tmp_path / "e",
        [(D1, "Broken Run", "Run", 3, "activities/1.gpx")],
        {"activities/1.gpx": b"<gpx>"}
    )

    assert cli.main([str(export), "--gps"]) == 0

    captured = capsys.readouterr()
    assert "warning: " in captured.err
    assert "using the CSV distance" in captured.err
    assert "Read 1 runs." in captured.out
    assert "3.0" in captured.out


def test_gps_distance_survives_the_local_time_conversion(tmp_path):
    export = make_export(tmp_path / "e",
                         [(D1, "Run", "Run", 1.5, "activities/1.gpx")],
                         {"activities/1.gpx": gpx_bytes(THREE)})
    runs = cli.to_local_time(parser.load_export(export).runs)
    assert runs[0].gps_distance_m == pytest.approx(THREE_POINT_M, rel=1e-3)


def test_files_next_to_the_csv_are_found(tmp_path):
    # The CSV says 'activities/1.gpx' but the files sit beside activities.csv
    export = make_export(tmp_path / "activities",
                         [(D1, "Run", "Run", 1.5, "activities/1.gpx")],
                         {"1.gpx": gpx_bytes(THREE)})
    result = parser.load_export(export)
    assert result.runs[0].gps_distance_m == pytest.approx(THREE_POINT_M, rel=1e-3)
    assert result.notes == []
