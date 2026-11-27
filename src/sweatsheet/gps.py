"""
gps.py -- read GPS tracks from the activity files in a Strava export.

WHAT THIS FILE DOES
    Strava's export keeps a raw file for most activities in the activities/
    folder: .gpx, .fit, or .fit.gz (a gzip-compressed .fit). This module opens
    one of those files, pulls out its GPS points, and measures the distance
    along them with the haversine formula (metrics.py).

    It only reads files. Deciding which file belongs to which run happens in
    parser.py (activities.csv has a "Filename" column).

HOW THE REST OF THE PROJECT USES IT
    from sweatsheet.gps import read_track, gps_distance_m

    read_track("activities/123.fit.gz")   -> list[TrackPoint]
    gps_distance_m("activities/123.gpx")  -> distance in meters (float)

SUPPORTED FILES
    .gpx   .gpx.gz   .fit   .fit.gz
    Anything else (e.g. .tcx) raises UnsupportedTrackError.
    FIT needs the `garmin-fit-sdk` package (uv add garmin-fit-sdk). It is only
    imported when a FIT file is opened, so GPX-only use works without it.

ERRORS (all messages are written to be shown to the user)
    FileNotFoundError     the file is not there
    NoGpsError            the file is fine but has no usable GPS points
                          (an indoor/treadmill run, GPS off).
                          Not a real problem.
    UnsupportedTrackError the file type isn't supported, or the FIT package
                          isn't installed. `.reason` is a short label so the
                          caller can count these instead of printing hundreds.
    MetricsError          anything else wrong with the file: empty, damaged,
                          truncated .gz, not valid GPX/FIT, bad coordinates.
    (NoGpsError and UnsupportedTrackError are MetricsErrors too.)

NOTES
    * Elevation, heart rate, etc. are ignored; only position and time are kept.
    * If a GPX file has several track segments (pauses), they are joined in
      file order, so a pause is bridged by a straight line between the last
      point before it and the first point after it.
    * FIT stores coordinates as "semicircles"; they are converted to degrees.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime
from pathlib import Path

from .metrics import track_distance_m
from .models import MetricsError, TrackPoint

# FIT stores latitude/longitude as signed 32-bit "semicircles":
# degrees = semicircles * 180 / 2**31
SEMICIRCLES_TO_DEGREES = 180 / 2**31


class NoGpsError(MetricsError):
    """The file is readable but has no GPS positions (e.g. an indoor run)."""


class UnsupportedTrackError(MetricsError):
    """The file can't be read here (unsupported type, or FIT package missing).

    `reason` is a short, shareable label (e.g. "'.tcx' files are not supported
    yet") that callers can use to group many files into one message.
    """

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------------------
# Public functions
# --------------------------------------------------------------------------

def read_track(path: str | Path) -> list[TrackPoint]:
    """
    Read the GPS points (in file order) from a .gpx/.fit file, gzipped or not.
    """
    path = Path(path)
    lowered = path.name.lower()
    gzipped = lowered.endswith(".gz")
    stem = lowered[:-3] if gzipped else lowered
    if stem.endswith(".gpx"):
        parse = _points_from_gpx
    elif stem.endswith(".fit"):
        parse = _points_from_fit
    else:
        ext = Path(stem).suffix or "(no extension)"
        raise UnsupportedTrackError(
            f"{path.name}: this file type is not supported.",
            reason=f"'{ext}' files are not supported yet")

    try:
        raw = path.read_bytes()
        data = gzip.decompress(raw) if gzipped else raw
    except FileNotFoundError:
        raise
    except (OSError, EOFError, zlib.error) as exc:
        what = "could not be unzipped (the .gz is damaged or cut off)" \
            if gzipped else "could not be read"
        raise MetricsError(f"{path.name} {what}: {exc}.") from exc

    return parse(data, path.name)


def gps_distance_m(path: str | Path) -> float:
    """Distance in meters along the file's GPS points (haversine)."""
    path = Path(path)
    points = read_track(path)
    if len(points) < 2:
        raise NoGpsError(
            f"{path.name} has fewer than 2 GPS points, so it has no distance.")
    return track_distance_m(points)


# --------------------------------------------------------------------------
# GPX
# --------------------------------------------------------------------------

def _local(tag: str) -> str:
    """'{http://www.topografix.com/GPX/1/1}trkpt' -> 'trkpt'."""
    return tag.rsplit("}", 1)[-1]


def _gpx_time(trkpt: ET.Element) -> datetime | None:
    """The point's <time> as a datetime, or None if missing/unreadable."""
    for child in trkpt:
        if _local(child.tag) == "time" and child.text:
            try:
                # fromisoformat() only understands "Z" on Python 3.11+
                return datetime.fromisoformat(
                    child.text.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


def _points_from_gpx(data: bytes, name: str) -> list[TrackPoint]:
    if not data.strip():
        raise MetricsError(f"{name} is empty.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise MetricsError(
            f"{name} is not valid GPX (the XML is broken: {exc}).") from exc
    if _local(root.tag) != "gpx":
        raise MetricsError(f"{name} does not look like a GPX file.")

    points: list[TrackPoint] = []
    trkpts = (el for el in root.iter() if _local(el.tag) == "trkpt")
    for n, el in enumerate(trkpts, start=1):
        try:
            lat, lon = float(el.attrib["lat"]), float(el.attrib["lon"])
        except (KeyError, ValueError):
            raise MetricsError(
                f"{name}: GPS point {n} has no valid lat/lon.") from None
        points.append(TrackPoint(lat, lon, _gpx_time(el)))

    if not points:
        raise NoGpsError(f"{name} has no GPS trackpoints.")
    return points


# --------------------------------------------------------------------------
# FIT
# --------------------------------------------------------------------------

def _points_from_fit(data: bytes, name: str) -> list[TrackPoint]:
    try:
        from garmin_fit_sdk import Decoder, Stream
    except ImportError:
        raise UnsupportedTrackError(
            f"{name}: reading FIT files needs the garmin-fit-sdk package.",
            reason="FIT files need the garmin-fit-sdk package "
                   "(install it with: uv add garmin-fit-sdk)") from None

    if not data:
        raise MetricsError(f"{name} is empty.")

    def fresh_decoder() -> Decoder:
        # Every Decoder method reads the stream it was given, and a Decoder
        # does not rewind. Calling read() after check_integrity() on the same
        # decoder silently returns NO messages, so each step gets its own.
        return Decoder(Stream.from_byte_array(bytearray(data)))

    try:
        if not fresh_decoder().is_fit():
            raise MetricsError(f"{name} is not a FIT file.")
        if not fresh_decoder().check_integrity():
            raise MetricsError(
                f"{name} is damaged (it failed the FIT integrity check; it "
                "may be cut off or corrupted).")
        messages, errors = fresh_decoder().read()
    except MetricsError:
        raise
    except Exception as exc:  # the decoder's own failures are undocumented
        raise MetricsError(
            f"{name} could not be decoded as a FIT file: {exc}.") from exc
    if errors:
        raise MetricsError(
            f"{name} could not be decoded as a FIT file: {errors[0]}.")
    if not messages:
        raise MetricsError(
            f"{name} could not be decoded: no data found in it."
        )

    points: list[TrackPoint] = []
    for record in messages.get("record_mesgs", []):
        lat, lon = record.get("position_lat"), record.get("position_long")
        if lat is None or lon is None:
            continue  # this sample has no GPS fix
        when = record.get("timestamp")
        points.append(TrackPoint(
            lat * SEMICIRCLES_TO_DEGREES, lon * SEMICIRCLES_TO_DEGREES,
            when if isinstance(when, datetime) else None))

    if not points:
        raise NoGpsError(f"{name} has no GPS positions (an indoor run?).")
    return points
