#!/usr/bin/env python3
"""Create a draft route description from the newest GPX in inbox/."""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import socket
import xml.etree.ElementTree as ET
import argparse
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from fitparse import FitFile
except Exception:  # pragma: no cover - optional dependency
    FitFile = None


INBOX_DIR = os.path.join(os.path.dirname(__file__), "inbox")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.txt")
TRACK_FILE_EXTENSIONS = (".gpx", ".fit")
RUNTIME_ENV_KEYS = (
    "TRACK2TEXT_MAX_SAMPLES",
    "TRACK2TEXT_SECTION_KM",
    "TRACK2TEXT_INCLUDE_START_GOAL",
    "TRACK2TEXT_GEOCODER",
    "TRACK2TEXT_LOCALITY_GEOCODER",
    "TRACK2TEXT_LOCALITY_ZOOM",
    "TRACK2TEXT_MIN_DIST_M",
    "NOMINATIM_USER_AGENT",
)
RUNTIME_KEY_ALIASES = {
    "TRACK2TEXT_MAX_SAMPLES": ("TRACK2TEXT_MAX_SAMPLES", "GPXER_MAX_SAMPLES"),
    "TRACK2TEXT_SECTION_KM": ("TRACK2TEXT_SECTION_KM", "GPXER_SECTION_KM"),
    "TRACK2TEXT_INCLUDE_START_GOAL": (
        "TRACK2TEXT_INCLUDE_START_GOAL",
        "GPXER_INCLUDE_START_GOAL",
    ),
    "TRACK2TEXT_GEOCODER": ("TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"),
    "TRACK2TEXT_LOCALITY_GEOCODER": (
        "TRACK2TEXT_LOCALITY_GEOCODER",
        "GPXER_LOCALITY_GEOCODER",
    ),
    "TRACK2TEXT_LOCALITY_ZOOM": (
        "TRACK2TEXT_LOCALITY_ZOOM",
        "GPXER_LOCALITY_ZOOM",
    ),
    "TRACK2TEXT_MIN_DIST_M": ("TRACK2TEXT_MIN_DIST_M", "GPXER_MIN_DIST_M"),
    "NOMINATIM_USER_AGENT": ("NOMINATIM_USER_AGENT",),
}

ANSI_CODES = {
    "reset": "\x1b[0m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "cyan": "\x1b[36m",
}


def color_enabled() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    value = os.environ.get("TRACK2TEXT_COLOR")
    if value is None:
        return True
    return value.strip() not in ("0", "false", "off", "no")


def colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    code = ANSI_CODES.get(color)
    if not code:
        return text
    return f"{code}{text}{ANSI_CODES['reset']}"


def env_first(keys: Iterable[str], default: str) -> str:
    for key in keys:
        value = os.environ.get(key)
        if value is not None:
            return value
    return default


def build_user_agent(config: Dict[str, str]) -> str:
    """Build User-Agent from config or env, with fallback."""
    user_agent = env_first(
        ["NOMINATIM_USER_AGENT"],
        config.get("NOMINATIM_USER_AGENT", ""),
    )
    if user_agent and user_agent.strip():
        return user_agent.strip()
    # Fallback: generic but compliant
    return "track2text/1.0 (contact: local-user)"


GEOCODER = "nominatim"
LOCALITY_GEOCODER = "photon"


def refresh_runtime_geocoders() -> None:
    global GEOCODER, LOCALITY_GEOCODER
    GEOCODER = env_first(["TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"], "nominatim").lower()
    LOCALITY_GEOCODER = env_first(
        ["TRACK2TEXT_LOCALITY_GEOCODER", "GPXER_LOCALITY_GEOCODER"],
        "photon",
    ).lower()


refresh_runtime_geocoders()


@dataclass
class Point:
    lat: float
    lon: float


class DependencyError(RuntimeError):
    pass


class GeocodingError(RuntimeError):
    pass


def newest_track_file(inbox_dir: str, lang: str) -> str:
    if not os.path.isdir(inbox_dir):
        msg = (
            f"Inbox-Ordner nicht gefunden: {inbox_dir}"
            if lang == "DE"
            else f"Inbox folder not found: {inbox_dir}"
        )
        raise FileNotFoundError(msg)
    track_files = [
        os.path.join(inbox_dir, f)
        for f in os.listdir(inbox_dir)
        if f.lower().endswith(TRACK_FILE_EXTENSIONS)
    ]
    if not track_files:
        msg = (
            "Keine GPX- oder FIT-Dateien im inbox-Ordner gefunden."
            if lang == "DE"
            else "No GPX or FIT files found in the inbox folder."
        )
        raise FileNotFoundError(msg)
    return max(track_files, key=os.path.getmtime)


def parse_gpx_points(gpx_path: str) -> List[Point]:
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    pts = []
    for el in root.findall(".//{*}trkpt"):
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            continue
        pts.append(Point(float(lat), float(lon)))

    if not pts:
        for el in root.findall(".//{*}rtept"):
            lat = el.get("lat")
            lon = el.get("lon")
            if lat is None or lon is None:
                continue
            pts.append(Point(float(lat), float(lon)))

    return pts


def semicircles_to_degrees(value: float) -> float:
    return value * (180.0 / 2**31)


@dataclass
class FieldSummary:
    count: int = 0
    numeric_count: int = 0
    total: float = 0.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    last_value: Optional[str] = None
    unit: Optional[str] = None
    unique_values: Optional[set] = None

    def add_value(self, value, unit: Optional[str]) -> None:
        self.count += 1
        if unit and not self.unit:
            self.unit = unit
        if isinstance(value, (int, float)):
            self.numeric_count += 1
            self.total += float(value)
            if self.min_value is None or value < self.min_value:
                self.min_value = float(value)
            if self.max_value is None or value > self.max_value:
                self.max_value = float(value)
            self.last_value = f"{value}"
            return
        if self.unique_values is None:
            self.unique_values = set()
        if len(self.unique_values) < 5:
            self.unique_values.add(str(value))
        self.last_value = str(value)

    def format_value(self) -> str:
        if self.numeric_count > 1:
            avg = self.total / self.numeric_count
            unit = f" {self.unit}" if self.unit else ""
            return f"min={self.min_value}, max={self.max_value}, avg={avg:.2f}{unit}"
        if self.numeric_count == 1:
            unit = f" {self.unit}" if self.unit else ""
            return f"{self.last_value}{unit}"
        if self.unique_values:
            values = ", ".join(sorted(self.unique_values))
            suffix = "" if self.count <= len(self.unique_values) else "..."
            return f"{values}{suffix}"
        return self.last_value or ""


def ensure_fitparse(lang: str) -> None:
    if FitFile is not None:
        return
    msg = (
        "FIT-Dateien benoetigen das Python-Paket `fitparse`.\n"
        "Installiere es im Projektordner mit:\n"
        "  python3 -m pip install -r requirements.txt\n"
        "Wenn du eine virtuelle Umgebung nutzt, aktiviere sie vorher mit:\n"
        "  source .venv/bin/activate"
        if lang == "DE"
        else "FIT files require the Python package `fitparse`.\n"
        "Install it from the project folder with:\n"
        "  python3 -m pip install -r requirements.txt\n"
        "If you use a virtual environment, activate it first with:\n"
        "  source .venv/bin/activate"
    )
    raise DependencyError(msg)


def format_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_speed(value: Optional[float], unit: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if unit in (None, "m/s"):
        return f"{value * 3.6:.1f} km/h"
    return f"{value} {unit}"


def format_distance(value: Optional[float], unit: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if unit in (None, "m"):
        return f"{value / 1000.0:.2f} km"
    return f"{value} {unit}"


def format_altitude(value: Optional[float], unit: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if unit in (None, "m"):
        return f"{value:.0f} m"
    return f"{value} {unit}"


def format_scalar(
    value: Optional[float],
    unit: Optional[str],
    decimals: int,
    default_unit: Optional[str] = None,
) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if decimals <= 0:
            formatted = f"{value:.0f}"
        else:
            formatted = f"{value:.{decimals}f}"
    else:
        formatted = str(value)
    unit_label = unit or default_unit
    return f"{formatted} {unit_label}".rstrip() if unit_label else formatted


def parse_fit_points_and_summary(
    fit_path: str, lang: str
) -> Tuple[List[Point], List[str], List[str], Dict[str, object]]:
    ensure_fitparse(lang)
    fitfile = FitFile(fit_path)
    points: List[Point] = []
    session_values: Dict[str, Tuple[Optional[float], Optional[str]]] = {}
    record_temps: List[float] = []
    full_summary: Dict[str, Dict[str, FieldSummary]] = {}
    message_counts: Dict[str, int] = {}

    for message in fitfile.get_messages():
        message_name = message.name
        message_counts[message_name] = message_counts.get(message_name, 0) + 1
        fields = full_summary.setdefault(message_name, {})
        field_map = {field.name: field for field in message}
        lat_field = field_map.get("position_lat")
        lon_field = field_map.get("position_long")
        if (
            lat_field
            and lon_field
            and lat_field.value is not None
            and lon_field.value is not None
        ):
            points.append(
                Point(
                    semicircles_to_degrees(lat_field.value),
                    semicircles_to_degrees(lon_field.value),
                )
            )
        if message_name == "record":
            temp_field = field_map.get("temperature")
            if temp_field and temp_field.value is not None:
                record_temps.append(float(temp_field.value))
        if message_name == "session":
            for field_name in (
                "total_timer_time",
                "total_elapsed_time",
                "total_ascent",
                "total_descent",
                "max_grade",
                "max_altitude",
                "max_elevation",
                "avg_power",
                "total_distance",
                "avg_speed",
                "max_speed",
                "avg_heart_rate",
                "max_heart_rate",
                "avg_cadence",
            ):
                field = field_map.get(field_name)
                if field and field.value is not None:
                    session_values[field_name] = (field.value, field.units)
        for field in message:
            if field.value is None:
                continue
            field_summary = fields.setdefault(field.name, FieldSummary())
            field_summary.add_value(field.value, field.units)

    summary_lines: List[str] = []
    debug_lines: List[str] = []
    summary_data: Dict[str, object] = {}
    title = "FIT Summary" if lang == "EN" else "FIT-Zusammenfassung"
    summary_lines.append(title)
    summary_lines.append("-" * len(title))

    def add_line(label_de: str, label_en: str, value: Optional[str]) -> None:
        if value is None:
            return
        label = label_de if lang == "DE" else label_en
        summary_lines.append(f"{label}: {value}")

    total_time = None
    if "total_timer_time" in session_values:
        total_time = session_values["total_timer_time"][0]
    elif "total_elapsed_time" in session_values:
        total_time = session_values["total_elapsed_time"][0]
    total_time_formatted = format_duration(total_time)
    add_line("Gesamte Zeit", "Total time", total_time_formatted)
    if total_time_formatted:
        summary_data["total_time"] = total_time_formatted

    ascent = session_values.get("total_ascent")
    descent = session_values.get("total_descent")
    add_line(
        "Anstieg",
        "Ascent",
        format_altitude(ascent[0], ascent[1]) if ascent else None,
    )
    if ascent:
        summary_data["ascent"] = format_altitude(ascent[0], ascent[1])
    add_line(
        "Abstieg",
        "Descent",
        format_altitude(descent[0], descent[1]) if descent else None,
    )
    if descent:
        summary_data["descent"] = format_altitude(descent[0], descent[1])

    max_grade = session_values.get("max_grade")
    add_line(
        "Max. Anstieg",
        "Max grade",
        format_scalar(max_grade[0], max_grade[1], 1, "%") if max_grade else None,
    )
    if max_grade:
        summary_data["max_grade"] = format_scalar(max_grade[0], max_grade[1], 1, "%")

    max_alt = session_values.get("max_altitude") or session_values.get("max_elevation")
    add_line(
        "Max. Hoehe",
        "Max altitude",
        format_altitude(max_alt[0], max_alt[1]) if max_alt else None,
    )
    if max_alt:
        summary_data["max_altitude"] = format_altitude(max_alt[0], max_alt[1])

    avg_power = session_values.get("avg_power")
    add_line(
        "Durchschnittliche Watt",
        "Average power",
        format_scalar(avg_power[0], avg_power[1], 0, "W") if avg_power else None,
    )
    if avg_power:
        summary_data["avg_power"] = format_scalar(avg_power[0], avg_power[1], 0, "W")
    total_distance = session_values.get("total_distance")
    add_line(
        "Distanz",
        "Distance",
        format_distance(total_distance[0], total_distance[1])
        if total_distance
        else None,
    )
    if total_distance:
        summary_data["distance"] = format_distance(
            total_distance[0], total_distance[1]
        )
    avg_speed = session_values.get("avg_speed")
    max_speed = session_values.get("max_speed")
    if avg_speed or max_speed:
        avg_part = (
            format_speed(avg_speed[0], avg_speed[1]) if avg_speed else "-"
        )
        max_part = (
            format_speed(max_speed[0], max_speed[1]) if max_speed else "-"
        )
        add_line(
            "Geschwindigkeit (avg/max)",
            "Speed (avg/max)",
            f"{avg_part} / {max_part}",
        )
        summary_data["speed_avg_max"] = f"{avg_part} / {max_part}"
    avg_hr = session_values.get("avg_heart_rate")
    max_hr = session_values.get("max_heart_rate")
    if avg_hr or max_hr:
        avg_part = (
            format_scalar(avg_hr[0], avg_hr[1], 0, "bpm") if avg_hr else "-"
        )
        max_part = (
            format_scalar(max_hr[0], max_hr[1], 0, "bpm") if max_hr else "-"
        )
        summary_data["heart_rate_avg_max"] = f"{avg_part} / {max_part}"
    avg_cadence = session_values.get("avg_cadence")
    add_line(
        "Durchschnittliche Kadenz",
        "Average cadence",
        format_scalar(avg_cadence[0], avg_cadence[1], 0, "rpm")
        if avg_cadence
        else None,
    )
    if avg_cadence:
        summary_data["avg_cadence"] = format_scalar(
            avg_cadence[0], avg_cadence[1], 0, "rpm"
        )

    if record_temps:
        min_temp = min(record_temps)
        max_temp = max(record_temps)
        avg_temp = sum(record_temps) / len(record_temps)
        add_line(
            "Temperatur (min/max/avg)",
            "Temperature (min/max/avg)",
            f"{min_temp:.1f}/{max_temp:.1f}/{avg_temp:.1f} C",
        )
        summary_data["temperature_min_max_avg"] = (
            f"{min_temp:.1f}/{max_temp:.1f}/{avg_temp:.1f} C"
        )
    else:
        min_temp = session_values.get("min_temperature")
        max_temp = session_values.get("max_temperature")
        avg_temp = session_values.get("avg_temperature")
        if min_temp or max_temp or avg_temp:
            add_line(
                "Temperatur (min/max/avg)",
                "Temperature (min/max/avg)",
                f"{min_temp[0] if min_temp else '-'}"
                f"/{max_temp[0] if max_temp else '-'}"
                f"/{avg_temp[0] if avg_temp else '-'} C",
            )
            summary_data["temperature_min_max_avg"] = (
                f"{min_temp[0] if min_temp else '-'}"
                f"/{max_temp[0] if max_temp else '-'}"
                f"/{avg_temp[0] if avg_temp else '-'} C"
            )

    debug_title = "Debug" if lang == "EN" else "Debug"
    debug_lines.append(debug_title)
    debug_lines.append("-" * len(debug_title))
    for message_name in sorted(full_summary.keys()):
        count = message_counts.get(message_name, 0)
        header = (
            f"Message: {message_name} (count={count})"
            if lang == "EN"
            else f"Nachricht: {message_name} (Anzahl={count})"
        )
        debug_lines.append(header)
        for field_name in sorted(full_summary[message_name].keys()):
            value = full_summary[message_name][field_name].format_value()
            debug_lines.append(f"{field_name}: {value}")
        debug_lines.append("")
    if debug_lines and debug_lines[-1] == "":
        debug_lines.pop()

    return points, summary_lines, debug_lines, summary_data


def haversine_m(a: Point, b: Point) -> float:
    r = 6371000.0
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    s = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(s))


def downsample(points: List[Point], min_dist_m: float) -> List[Point]:
    if not points:
        return []
    keep = [points[0]]
    last = points[0]
    for p in points[1:]:
        if haversine_m(last, p) >= min_dist_m:
            keep.append(p)
            last = p
    if keep[-1] != points[-1]:
        keep.append(points[-1])
    return keep


def sample_points(points: List[Point], target_max: int = 200) -> List[Point]:
    try:
        min_dist = float(
            env_first(["TRACK2TEXT_MIN_DIST_M", "GPXER_MIN_DIST_M"], "50")
        )
    except ValueError:
        min_dist = 50.0
    sampled = downsample(points, min_dist)
    while len(sampled) > target_max:
        min_dist *= 1.5
        sampled = downsample(points, min_dist)
    return sampled


def fetch_json(url: str, user_agent: str, timeout: int = 20) -> Dict:
    """Fetch JSON with proper error handling and user-agent.
    
    Raises:
        urllib.error.URLError: On network/connection errors
        urllib.error.HTTPError: On HTTP errors (e.g., 429 rate limit)
        json.JSONDecodeError: On invalid JSON
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise urllib.error.URLError(f"HTTP {e.code}: {e.reason}") from e
    except socket.timeout as e:
        raise urllib.error.URLError(f"Socket timeout after {timeout}s") from e
    except urllib.error.URLError as e:
        raise


def is_geocoding_policy_error(exc: BaseException) -> bool:
    text = str(exc)
    return "HTTP 403" in text or "HTTP 429" in text


def user_agent_looks_placeholder(user_agent: str) -> bool:
    lowered = user_agent.lower()
    return "your.email@example.com" in lowered or "local-user" in lowered


def geocoding_policy_message(
    exc: BaseException,
    lang: str,
    user_agent: str,
    geocoder_name: str,
) -> str:
    status = "HTTP 429" if "HTTP 429" in str(exc) else "HTTP 403"
    if lang == "DE":
        lines = [
            f"Reverse-Geocoding wurde vom Dienst abgelehnt ({status}).",
            f"Geocoder: {geocoder_name}",
            f"User-Agent: {user_agent}",
        ]
        if geocoder_name == "nominatim":
            if user_agent_looks_placeholder(user_agent):
                lines.append(
                    "Der Nominatim User-Agent ist noch ein Platzhalter. "
                    "Setze in config.txt eine echte Kontaktadresse:"
                )
                lines.append(
                    "  NOMINATIM_USER_AGENT=track2text/1.0 "
                    "(contact: dein.name@example.com)"
                )
            lines.append(
                "Alternativ im interaktiven Modus als Haupt-Geocoder `photon` waehlen."
            )
            lines.append(
                "Bei HTTP 429 bitte spaeter erneut versuchen oder weniger Samples nutzen."
            )
        else:
            lines.append(
                "Bitte spaeter erneut versuchen oder weniger Samples bzw. einen anderen "
                "Geocoder nutzen."
            )
        return "\n".join(lines)

    lines = [
        f"Reverse geocoding was rejected by the service ({status}).",
        f"Geocoder: {geocoder_name}",
        f"User-Agent: {user_agent}",
    ]
    if geocoder_name == "nominatim":
        if user_agent_looks_placeholder(user_agent):
            lines.append(
                "The Nominatim User-Agent is still a placeholder. "
                "Set a real contact address in config.txt:"
            )
            lines.append(
                "  NOMINATIM_USER_AGENT=track2text/1.0 "
                "(contact: your.name@example.com)"
            )
        lines.append("Alternatively choose `photon` as the main geocoder in interactive mode.")
        lines.append("For HTTP 429, try again later or use fewer samples.")
    else:
        lines.append("Try again later or use fewer samples / another geocoder.")
    return "\n".join(lines)


def reverse_geocode_nominatim(point: Point, user_agent: str, zoom: int = 18) -> Dict:
    params = {
        "format": "jsonv2",
        "lat": f"{point.lat:.7f}",
        "lon": f"{point.lon:.7f}",
        "zoom": str(zoom),
        "addressdetails": "1",
        "extratags": "1",
        "namedetails": "1",
    }
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        params
    )
    return fetch_json(url, user_agent)


def reverse_geocode_photon(point: Point, user_agent: str) -> Dict:
    params = {
        "lat": f"{point.lat:.7f}",
        "lon": f"{point.lon:.7f}",
    }
    url = "https://photon.komoot.io/reverse?" + urllib.parse.urlencode(params)
    data = fetch_json(url, user_agent)
    features = data.get("features") or []
    if not features:
        return {}
    return features[0]


def normalize_photon(feature: Dict) -> Dict:
    props = feature.get("properties") or {}
    address = {}
    if "street" in props:
        address["road"] = props["street"]
    if "city" in props:
        address["city"] = props["city"]
    if "district" in props:
        address["district"] = props["district"]
    if "locality" in props:
        address["locality"] = props["locality"]
    if "postcode" in props:
        address["postcode"] = props["postcode"]
    if "county" in props:
        address["county"] = props["county"]
    if "state" in props:
        address["state"] = props["state"]
    if "country" in props:
        address["country"] = props["country"]
    return {
        "address": address,
        "name": props.get("name"),
        "category": props.get("osm_key"),
    }


def reverse_geocode(point: Point, user_agent: str, zoom: int = 18) -> Dict:
    if GEOCODER == "photon":
        return normalize_photon(reverse_geocode_photon(point, user_agent))
    return reverse_geocode_nominatim(point, user_agent, zoom=zoom)


def reverse_geocode_locality(point: Point, user_agent: str, zoom: int = 12) -> Dict:
    if LOCALITY_GEOCODER == "photon":
        return normalize_photon(reverse_geocode_photon(point, user_agent))
    return reverse_geocode_nominatim(point, user_agent, zoom=zoom)


def pick_road(address: Dict) -> Optional[str]:
    for key in (
        "road",
        "pedestrian",
        "cycleway",
        "path",
        "footway",
        "steps",
        "track",
        "bridleway",
    ):
        if key in address:
            return address[key]
    return None


def route_distance_m(points: List[Point]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += haversine_m(a, b)
    return total


def pick_locality(address: Dict) -> Optional[str]:
    for key in ("city", "town", "village", "suburb", "hamlet", "municipality"):
        if key in address:
            return address[key]
    return None


def pick_ortsteil(address: Dict) -> Optional[str]:
    for key in (
        "neighbourhood",
        "quarter",
        "locality",
        "borough",
        "city_district",
        "district",
        "municipality",
        "isolated_dwelling",
    ):
        if key in address:
            return address[key]
    return None


def build_description(
    points: List[Point],
    total_dist_m: float,
    user_agent: str,
    lang: str,
) -> Tuple[List[str], int]:
    try:
        target_max = int(
            env_first(["TRACK2TEXT_MAX_SAMPLES", "GPXER_MAX_SAMPLES"], "200")
        )
    except ValueError:
        target_max = 200
    try:
        section_km = float(
            env_first(["TRACK2TEXT_SECTION_KM", "GPXER_SECTION_KM"], "3")
        )
    except ValueError:
        section_km = 3.0
    try:
        locality_zoom = int(
            env_first(["TRACK2TEXT_LOCALITY_ZOOM", "GPXER_LOCALITY_ZOOM"], "12")
        )
    except ValueError:
        locality_zoom = 12
    include_start_goal = (
        env_first(
            ["TRACK2TEXT_INCLUDE_START_GOAL", "GPXER_INCLUDE_START_GOAL"], "1"
        )
        == "1"
    )
    sampled = sample_points(points, target_max=target_max)

    use_color = color_enabled()
    started_at = time.monotonic()
    print(
        colorize("Starting processing:", "cyan", use_color),
        f"raw points={len(points)}, samples={len(sampled)},",
        f"target_max={target_max}, section_km={section_km}",
    )

    lines = []
    last_road = None
    last_locality = None
    last_ortsteil = None
    cumulative_m = 0.0
    next_section_m = section_km * 1000.0 if section_km > 0 else float("inf")

    for idx, p in enumerate(sampled):
        if idx > 0:
            print(
                colorize(
                    "Waiting 1s to respect reverse geocoding usage policy.",
                    "blue",
                    use_color,
                )
            )
            time.sleep(1.0)  # Nominatim usage policy
            cumulative_m += haversine_m(sampled[idx - 1], p)
        try:
            data = reverse_geocode(p, user_agent, zoom=18)
        except (urllib.error.URLError, socket.timeout) as exc:
            if is_geocoding_policy_error(exc):
                raise GeocodingError(
                    geocoding_policy_message(exc, lang, user_agent, GEOCODER)
                ) from exc
            print(
                colorize("Reverse geocoding failed:", "red", use_color),
                f"sample {idx + 1}/{len(sampled)},",
                f"coords={p.lat:.6f},{p.lon:.6f}, error={exc}",
            )
            lines.append(
                (
                    f"Hinweis: Reverse-Geocoding fehlgeschlagen ({exc})."
                    if lang == "DE"
                    else f"Note: reverse geocoding failed ({exc})."
                )
            )
            continue

        address = data.get("address", {})
        road = pick_road(address)
        locality = pick_locality(address)
        ortsteil = pick_ortsteil(address)
        progress_pct = (idx + 1) / len(sampled) * 100.0
        dist_pct = (cumulative_m / total_dist_m * 100.0) if total_dist_m else 0.0
        road_label = road or "unknown road"
        locality_label = locality or "unknown locality"
        ortsteil_label = ortsteil or "unknown district"
        elapsed = time.monotonic() - started_at
        eta = None
        if idx >= 0:
            avg_per = elapsed / (idx + 1)
            remaining = avg_per * (len(sampled) - idx - 1)
            eta = format_duration(remaining)
        eta_part = f", eta≈{eta}" if eta else ""
        print(
            colorize("Progress:", "green", use_color),
            f"sample {idx + 1}/{len(sampled)} ({progress_pct:.1f}%),",
            f"distance≈{cumulative_m/1000:.2f} km of {total_dist_m/1000:.2f} km",
            f"({dist_pct:.1f}%){eta_part}, coords={p.lat:.6f},{p.lon:.6f},",
            f"road='{road_label}', locality='{locality_label}', district='{ortsteil_label}'",
        )

        if cumulative_m >= next_section_m:
            km_marker = int(next_section_m / 1000.0)
            title = (
                f"Abschnitt: ab km {km_marker}"
                if lang == "DE"
                else f"Section: from km {km_marker}"
            )
            section_locality = locality
            section_ortsteil = ortsteil
            if locality_zoom:
                print(
                    colorize(
                        "Fetching locality context for section marker.",
                        "yellow",
                        use_color,
                    )
                )
                time.sleep(1.0)
                try:
                    loc_data = reverse_geocode_locality(p, user_agent, zoom=locality_zoom)
                    loc_address = loc_data.get("address", {})
                    section_locality = pick_locality(loc_address) or section_locality
                    section_ortsteil = pick_ortsteil(loc_address) or section_ortsteil
                except (urllib.error.URLError, socket.timeout) as exc:
                    if is_geocoding_policy_error(exc):
                        raise GeocodingError(
                            geocoding_policy_message(
                                exc, lang, user_agent, LOCALITY_GEOCODER
                            )
                        ) from exc
                    print(
                        colorize(
                            "Locality reverse geocoding failed for section marker.",
                            "red",
                            use_color,
                        )
                    )
                    pass
            if section_locality:
                title += (
                    f" (Ort: {section_locality})"
                    if lang == "DE"
                    else f" (Place: {section_locality})"
                )
            if section_ortsteil:
                title += (
                    f", Ortsteil: {section_ortsteil}"
                    if lang == "DE"
                    else f", District: {section_ortsteil}"
                )
            lines.append(f"- {title}")
            next_section_m += section_km * 1000.0
            last_locality = section_locality
            last_ortsteil = section_ortsteil

        if idx == 0:
            if road:
                last_road = road
            if locality:
                last_locality = locality
            if ortsteil:
                last_ortsteil = ortsteil
            if include_start_goal:
                if locality_zoom:
                    print(
                        colorize(
                            "Fetching locality context for start marker.",
                            "yellow",
                            use_color,
                        )
                    )
                    time.sleep(1.0)
                    try:
                        loc_data = reverse_geocode_locality(p, user_agent, zoom=locality_zoom)
                        loc_address = loc_data.get("address", {})
                        last_locality = pick_locality(loc_address) or last_locality
                        last_ortsteil = pick_ortsteil(loc_address) or last_ortsteil
                    except (urllib.error.URLError, socket.timeout) as exc:
                        if is_geocoding_policy_error(exc):
                            raise GeocodingError(
                                geocoding_policy_message(
                                    exc, lang, user_agent, LOCALITY_GEOCODER
                                )
                            ) from exc
                        print(
                            colorize(
                                "Locality reverse geocoding failed for start marker.",
                                "red",
                                use_color,
                            )
                        )
                        pass
                start_entry = "- Start" if lang == "DE" else "- Start"
                if road:
                    start_entry += f": {road}"
                if locality or ortsteil:
                    start_entry += " ("
                    if locality:
                        start_entry += (
                            f"Ort: {locality}"
                            if lang == "DE"
                            else f"Place: {locality}"
                        )
                    if ortsteil:
                        if locality:
                            start_entry += ", "
                        start_entry += (
                            f"Ortsteil: {ortsteil}"
                            if lang == "DE"
                            else f"District: {ortsteil}"
                        )
                    start_entry += ")"
                lines.append(start_entry)
            continue
        if road and road != last_road:
            entry = (
                f"- Straßenwechsel: {road}"
                if lang == "DE"
                else f"- Road change: {road}"
            )
            location_bits = []
            if locality and locality != last_locality:
                location_bits.append(
                    f"Ort: {locality}"
                    if lang == "DE"
                    else f"Place: {locality}"
                )
                last_locality = locality
            if ortsteil and ortsteil != last_ortsteil:
                location_bits.append(
                    f"Ortsteil: {ortsteil}"
                    if lang == "DE"
                    else f"District: {ortsteil}"
                )
                last_ortsteil = ortsteil
            if location_bits:
                entry += " (" + ", ".join(location_bits) + ")"
            lines.append(entry)

        if road:
            last_road = road

    if include_start_goal and sampled:
        goal_entry = "- Ziel" if lang == "DE" else "- Finish"
        if last_road:
            goal_entry += f": {last_road}"
        if last_locality or last_ortsteil:
            goal_entry += " ("
            if last_locality:
                goal_entry += (
                    f"Ort: {last_locality}"
                    if lang == "DE"
                    else f"Place: {last_locality}"
                )
            if last_ortsteil:
                if last_locality:
                    goal_entry += ", "
                goal_entry += (
                    f"Ortsteil: {last_ortsteil}"
                    if lang == "DE"
                    else f"District: {last_ortsteil}"
                )
            goal_entry += ")"
        lines.append(goal_entry)

    return lines, len(sampled)


def load_config(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    config = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


def normalize_output_language(value: Optional[str]) -> str:
    if not value:
        return "DE"
    value = value.strip().upper()
    if value in ("DE", "DEU", "GERMAN"):
        return "DE"
    if value in ("EN", "ENG", "ENGLISH"):
        return "EN"
    return "DE"


def resolve_input_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if os.path.isabs(value):
        return value
    if os.path.sep in value or (os.path.altsep and os.path.altsep in value):
        return os.path.abspath(value)
    return os.path.join(INBOX_DIR, value)


def summary_at_glance(
    lang: str,
    track_points: int,
    samples: int,
    total_dist_m: float,
    fit_summary_data: Optional[Dict[str, object]],
) -> List[str]:
    title = "Summary at a glance" if lang == "EN" else "Kurzueberblick"
    lines = [title, "-" * len(title)]
    lines.append(
        ("Distance: " if lang == "EN" else "Distanz: ")
        + f"{total_dist_m/1000:.2f} km"
    )
    lines.append(
        ("Track points: " if lang == "EN" else "Trackpunkte: ")
        + f"{track_points}"
    )
    lines.append(("Samples: " if lang == "EN" else "Samples: ") + f"{samples}")
    if fit_summary_data:
        label_map = {
            "total_time": ("Total time", "Gesamte Zeit"),
            "ascent": ("Ascent", "Anstieg"),
            "descent": ("Descent", "Abstieg"),
            "max_grade": ("Max grade", "Max. Anstieg"),
            "max_altitude": ("Max altitude", "Max. Hoehe"),
            "avg_power": ("Average power", "Durchschnittliche Watt"),
            "distance": ("Distance", "Distanz"),
            "speed_avg_max": ("Speed (avg/max)", "Geschwindigkeit (avg/max)"),
            "heart_rate_avg_max": ("Heart rate (avg/max)", "Puls (avg/max)"),
            "avg_cadence": ("Average cadence", "Durchschnittliche Kadenz"),
            "temperature_min_max_avg": (
                "Temperature (min/max/avg)",
                "Temperatur (min/max/avg)",
            ),
        }
        for key in (
            "total_time",
            "ascent",
            "descent",
            "max_grade",
            "max_altitude",
            "avg_power",
            "speed_avg_max",
            "avg_cadence",
            "temperature_min_max_avg",
        ):
            value = fit_summary_data.get(key)
            if not value:
                continue
            label_en, label_de = label_map[key]
            label = label_en if lang == "EN" else label_de
            lines.append(f"{label}: {value}")
    lines.append("")
    return lines


def list_track_files(inbox_dir: str) -> List[str]:
    if not os.path.isdir(inbox_dir):
        return []
    paths = [
        os.path.join(inbox_dir, name)
        for name in os.listdir(inbox_dir)
        if name.lower().endswith(TRACK_FILE_EXTENSIONS)
        and os.path.isfile(os.path.join(inbox_dir, name))
    ]
    return sorted(paths, key=os.path.getmtime, reverse=True)


def describe_file(path: str) -> str:
    name = os.path.basename(path)
    try:
        changed = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    except OSError:
        changed = "unbekannt"
    return f"{name} ({changed})"


def prompt_text(label: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        value = input(f"{label}{suffix}: ")
    except EOFError:
        print()
        return default or ""
    value = value.strip()
    if not value and default is not None:
        return default
    return value


def prompt_choice(label: str, choices: List[str], default: str) -> str:
    choice_map = {choice.lower(): choice for choice in choices}
    selected_default = choice_map.get(default.lower(), choices[0])
    hint = "/".join(choices)
    while True:
        value = prompt_text(f"{label} ({hint})", selected_default)
        selected = choice_map.get(value.lower())
        if selected:
            return selected
        print(f"Bitte eine dieser Optionen eingeben: {hint}")


def prompt_int(label: str, default: str, min_value: Optional[int] = None) -> str:
    while True:
        value = prompt_text(label, default)
        try:
            parsed = int(value)
        except ValueError:
            print("Bitte eine ganze Zahl eingeben.")
            continue
        if min_value is not None and parsed < min_value:
            print(f"Bitte mindestens {min_value} eingeben.")
            continue
        return str(parsed)


def prompt_float(label: str, default: str, min_value: Optional[float] = None) -> str:
    while True:
        value = prompt_text(label, default)
        normalized = value.replace(",", ".")
        try:
            parsed = float(normalized)
        except ValueError:
            print("Bitte eine Zahl eingeben.")
            continue
        if min_value is not None and parsed < min_value:
            print(f"Bitte mindestens {min_value} eingeben.")
            continue
        return f"{parsed:g}"


def prompt_yes_no(
    label: str,
    default: bool,
    default_on_eof: Optional[bool] = None,
) -> bool:
    hint = "J/n" if default else "j/N"
    try:
        value = input(f"{label} ({hint}): ").strip().lower()
    except EOFError:
        print()
        return default if default_on_eof is None else default_on_eof
    if not value:
        return default
    if value in ("j", "ja", "y", "yes", "1", "true", "wahr"):
        return True
    if value in ("n", "nein", "no", "0", "false", "falsch"):
        return False
    print("Bitte mit j oder n antworten.")
    return prompt_yes_no(label, default, default_on_eof)


def apply_config_env_defaults(config: Dict[str, str]) -> None:
    for key in RUNTIME_ENV_KEYS:
        aliases = RUNTIME_KEY_ALIASES.get(key, (key,))
        value = next((config[alias] for alias in aliases if alias in config), None)
        if value is not None and not any(alias in os.environ for alias in aliases):
            os.environ[key] = value


def apply_preset(name: str) -> None:
    if name == "quick-test":
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "5"
        os.environ["TRACK2TEXT_SECTION_KM"] = "9999"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "0"
    elif name == "fast":
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "80"
        os.environ["TRACK2TEXT_MIN_DIST_M"] = "120"
        os.environ["TRACK2TEXT_SECTION_KM"] = "5"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "1"
    elif name == "detailed":
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "400"
        os.environ["TRACK2TEXT_MIN_DIST_M"] = "25"
        os.environ["TRACK2TEXT_SECTION_KM"] = "2"
        os.environ["TRACK2TEXT_LOCALITY_ZOOM"] = "14"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "1"


def apply_arg_overrides(args: argparse.Namespace) -> None:
    if args.quick_test:
        apply_preset("quick-test")
    if args.fast:
        apply_preset("fast")
    if args.detailed:
        apply_preset("detailed")
    if args.file is not None:
        os.environ["TRACK2TEXT_INPUT_FILE"] = args.file
    for key in RUNTIME_ENV_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            os.environ[key] = value


def prompt_preset() -> Optional[str]:
    print("\nPreset:")
    print("  [1] Manuell einstellen")
    print("  [2] Quick test (sehr kurz)")
    print("  [3] Fast (schneller, weniger Details)")
    print("  [4] Detailed (langsamer, mehr Details)")
    options = {
        "1": None,
        "custom": None,
        "2": "quick-test",
        "quick": "quick-test",
        "quick-test": "quick-test",
        "3": "fast",
        "fast": "fast",
        "4": "detailed",
        "detail": "detailed",
        "detailed": "detailed",
    }
    while True:
        value = prompt_text("Auswahl", "1").lower()
        if value in options:
            preset = options[value]
            if preset:
                apply_preset(preset)
            return preset
        print("Bitte 1, 2, 3 oder 4 eingeben.")


def prompt_track_file(config: Dict[str, str]) -> None:
    current = env_first(["TRACK2TEXT_INPUT_FILE"], "") or config.get("input_file", "")
    track_files = list_track_files(INBOX_DIR)
    default_path = resolve_input_path(current) if current else None
    if default_path is None and track_files:
        default_path = track_files[0]

    print("\nTrack-Datei:")
    if track_files:
        for idx, path in enumerate(track_files, start=1):
            marker = " (neueste)" if idx == 1 else ""
            print(f"  [{idx}] {describe_file(path)}{marker}")
        print("  [p] Eigenen Pfad eingeben")
        default_label = describe_file(default_path) if default_path else "neueste Datei"
        while True:
            value = prompt_text("Auswahl", default_label).lower()
            if value == default_label.lower():
                if default_path:
                    os.environ["TRACK2TEXT_INPUT_FILE"] = default_path
                return
            if value in ("p", "pfad", "path"):
                custom_path = prompt_text("Pfad zur GPX/FIT-Datei", current)
                if custom_path:
                    os.environ["TRACK2TEXT_INPUT_FILE"] = custom_path
                return
            try:
                index = int(value)
            except ValueError:
                print("Bitte Nummer oder p eingeben.")
                continue
            if 1 <= index <= len(track_files):
                os.environ["TRACK2TEXT_INPUT_FILE"] = track_files[index - 1]
                return
            print(f"Bitte eine Nummer zwischen 1 und {len(track_files)} eingeben.")
    else:
        print("  Keine GPX/FIT-Dateien im inbox-Ordner gefunden.")
        custom_path = prompt_text("Pfad zur GPX/FIT-Datei leer lassen fuer neueste", current)
        if custom_path:
            os.environ["TRACK2TEXT_INPUT_FILE"] = custom_path


def print_interactive_summary(lang: str, user_agent: str) -> None:
    input_file = env_first(["TRACK2TEXT_INPUT_FILE"], "") or "neueste Datei in inbox/"
    print("\nZusammenfassung:")
    print(f"  Datei: {input_file}")
    print(f"  Ausgabesprache: {lang}")
    print(
        "  Samples: "
        + env_first(["TRACK2TEXT_MAX_SAMPLES", "GPXER_MAX_SAMPLES"], "200")
    )
    print(
        "  Mindestabstand: "
        + env_first(["TRACK2TEXT_MIN_DIST_M", "GPXER_MIN_DIST_M"], "50")
        + " m"
    )
    print(
        "  Abschnittslaenge: "
        + env_first(["TRACK2TEXT_SECTION_KM", "GPXER_SECTION_KM"], "3")
        + " km"
    )
    print(
        "  Start/Ziel: "
        + (
            "ja"
            if env_first(
                ["TRACK2TEXT_INCLUDE_START_GOAL", "GPXER_INCLUDE_START_GOAL"], "1"
            )
            == "1"
            else "nein"
        )
    )
    print(
        "  Geocoder: "
        + env_first(["TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"], "nominatim")
    )
    print(
        "  Orts-Geocoder: "
        + env_first(["TRACK2TEXT_LOCALITY_GEOCODER", "GPXER_LOCALITY_GEOCODER"], "photon")
    )
    print(
        "  Orts-Zoom: "
        + env_first(["TRACK2TEXT_LOCALITY_ZOOM", "GPXER_LOCALITY_ZOOM"], "12")
    )
    print(f"  User-Agent: {user_agent}")


def run_interactive_cli(args: argparse.Namespace, config: Dict[str, str]) -> Optional[str]:
    use_color = color_enabled()
    print(colorize("Track2Text interaktiv", "cyan", use_color))
    print("Enter uebernimmt jeweils den Wert in eckigen Klammern.")

    lang = prompt_choice(
        "Ausgabesprache",
        ["DE", "EN"],
        normalize_output_language(args.output_language or config.get("output_language")),
    )
    prompt_track_file(config)
    selected_preset = prompt_preset()

    if selected_preset is None:
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = prompt_int(
            "Maximale Samples",
            env_first(["TRACK2TEXT_MAX_SAMPLES", "GPXER_MAX_SAMPLES"], "200"),
            min_value=1,
        )
        os.environ["TRACK2TEXT_MIN_DIST_M"] = prompt_float(
            "Mindestabstand zwischen Samples in Metern",
            env_first(["TRACK2TEXT_MIN_DIST_M", "GPXER_MIN_DIST_M"], "50"),
            min_value=0,
        )
        os.environ["TRACK2TEXT_SECTION_KM"] = prompt_float(
            "Abschnittslaenge in km (0 = aus)",
            env_first(["TRACK2TEXT_SECTION_KM", "GPXER_SECTION_KM"], "3"),
            min_value=0,
        )
        include_start_goal = prompt_yes_no(
            "Start- und Zielzeile ausgeben",
            env_first(["TRACK2TEXT_INCLUDE_START_GOAL", "GPXER_INCLUDE_START_GOAL"], "1")
            == "1",
        )
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "1" if include_start_goal else "0"
        os.environ["TRACK2TEXT_GEOCODER"] = prompt_choice(
            "Haupt-Geocoder",
            ["nominatim", "photon"],
            env_first(["TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"], "nominatim").lower(),
        )
        os.environ["TRACK2TEXT_LOCALITY_GEOCODER"] = prompt_choice(
            "Orts-Geocoder",
            ["nominatim", "photon"],
            env_first(
                ["TRACK2TEXT_LOCALITY_GEOCODER", "GPXER_LOCALITY_GEOCODER"],
                "photon",
            ).lower(),
        )
        os.environ["TRACK2TEXT_LOCALITY_ZOOM"] = prompt_int(
            "Orts-Zoom (0 = Ortsabfrage aus)",
            env_first(["TRACK2TEXT_LOCALITY_ZOOM", "GPXER_LOCALITY_ZOOM"], "12"),
            min_value=0,
        )
        current_user_agent = build_user_agent(config)
        os.environ["NOMINATIM_USER_AGENT"] = prompt_text(
            "Nominatim User-Agent",
            current_user_agent,
        )
    elif (
        env_first(["TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"], "nominatim").lower()
        == "nominatim"
        and user_agent_looks_placeholder(build_user_agent(config))
    ):
        print(
            "\nHinweis: Nominatim lehnt Platzhalter-User-Agents oft mit HTTP 403 ab."
        )
        os.environ["NOMINATIM_USER_AGENT"] = prompt_text(
            "Nominatim User-Agent mit echter Kontaktadresse",
            build_user_agent(config),
        )

    print_interactive_summary(lang, build_user_agent(config))
    if not prompt_yes_no("Verarbeitung jetzt starten", True, default_on_eof=False):
        print("Abgebrochen. Es wurden keine Dateien erzeugt.")
        return None
    return lang


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a route description from the newest GPX/FIT in inbox/."
    )
    parser.add_argument(
        "--output-language",
        choices=["DE", "EN"],
        type=str.upper,
        help="Language for the output text file.",
    )
    parser.add_argument(
        "--TRACK2TEXT_MAX_SAMPLES",
        help="Override max samples (same as TRACK2TEXT_MAX_SAMPLES env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_SECTION_KM",
        help="Override section length in km (same as TRACK2TEXT_SECTION_KM env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_INCLUDE_START_GOAL",
        help="Override start/goal inclusion (same as TRACK2TEXT_INCLUDE_START_GOAL env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_GEOCODER",
        help="Override geocoder (same as TRACK2TEXT_GEOCODER env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_LOCALITY_GEOCODER",
        help="Override locality geocoder (same as TRACK2TEXT_LOCALITY_GEOCODER env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_LOCALITY_ZOOM",
        help="Override locality zoom (same as TRACK2TEXT_LOCALITY_ZOOM env var).",
    )
    parser.add_argument(
        "--TRACK2TEXT_MIN_DIST_M",
        help="Override min distance between samples (same as TRACK2TEXT_MIN_DIST_M env var).",
    )
    parser.add_argument(
        "--file",
        help="Process a specific GPX/FIT file (absolute or relative path, or filename in inbox/).",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Start an interactive prompt for the common Track2Text settings.",
    )
    parser.add_argument(
        "--quick-test",
        action="store_true",
        help="Run with very small output (5 samples, no sections, no start/finish).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast preset: fewer samples, larger spacing, less detail.",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Detailed preset: more samples, smaller spacing, more detail.",
    )
    parser.add_argument(
        "--NOMINATIM_USER_AGENT",
        help="Override Nominatim user agent (same as NOMINATIM_USER_AGENT env var).",
    )
    args = parser.parse_args()

    config = load_config(CONFIG_PATH)
    apply_config_env_defaults(config)
    apply_arg_overrides(args)
    if args.interactive:
        interactive_lang = run_interactive_cli(args, config)
        if interactive_lang is None:
            return 0
        args.output_language = interactive_lang
    refresh_runtime_geocoders()

    lang = normalize_output_language(
        args.output_language or config.get("output_language")
    )
    user_agent = build_user_agent(config)

    try:
        input_path = resolve_input_path(
            env_first(["TRACK2TEXT_INPUT_FILE"], "") or config.get("input_file")
        )
        track_path = input_path or newest_track_file(INBOX_DIR, lang)
        _, ext = os.path.splitext(track_path)
        ext = ext.lower()
        fit_summary_lines: List[str] = []
        fit_debug_lines: List[str] = []
        fit_summary_data: Dict[str, object] = {}
        if ext == ".fit":
            (
                points,
                fit_summary_lines,
                fit_debug_lines,
                fit_summary_data,
            ) = parse_fit_points_and_summary(track_path, lang)
        else:
            points = parse_gpx_points(track_path)
        if not points:
            msg = (
                "Fehler: Keine Track- oder Routenpunkte gefunden."
                if lang == "DE"
                else "Error: No track or route points found."
            )
            use_color = color_enabled()
            print(colorize(msg, "red", use_color))
            return 1

        total_dist_m = route_distance_m(points)
        lines, sample_count = build_description(points, total_dist_m, user_agent, lang)

        base = os.path.splitext(os.path.basename(track_path))[0]
        out_path = os.path.join(os.path.dirname(track_path), f"{base}.txt")
        json_path = os.path.join(os.path.dirname(track_path), f"{base}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            if lang == "DE":
                f.write("Rohfassung Wegbeschreibung\n")
                f.write("=" * 23 + "\n\n")
                f.write(
                    "Hinweis: Diese Liste ist eine Rohfassung. Bitte mit ChatGPT zu einer\n"
                )
                f.write("gut lesenden Wegbeschreibung zusammenfassen.\n\n")
                f.write("Format: Stichpunkte mit Straßenwechseln und Ortsangaben.\n")
                f.write("Abschnitte: automatisch nach Distanz gegliedert.\n\n")
                f.write(f"Rohdaten: Trackpunkte={len(points)}, Samples={sample_count}, ")
                f.write(f"Distanz≈{total_dist_m/1000:.2f} km\n\n")
                f.write(f"Quelle: {os.path.basename(track_path)}\n\n")
            else:
                f.write("Draft Route Description\n")
                f.write("=" * 23 + "\n\n")
                f.write(
                    "Note: This list is a draft. Please summarize it into a readable\n"
                )
                f.write("route description (e.g. with ChatGPT).\n\n")
                f.write("Format: bullets with road changes and place names.\n")
                f.write("Sections: automatically grouped by distance.\n\n")
                f.write(f"Raw data: track points={len(points)}, samples={sample_count}, ")
                f.write(f"distance≈{total_dist_m/1000:.2f} km\n\n")
                f.write(f"Source: {os.path.basename(track_path)}\n\n")
            overview_lines = summary_at_glance(
                lang, len(points), sample_count, total_dist_m, fit_summary_data
            )
            f.write("\n".join(overview_lines))
            if fit_summary_lines:
                f.write("\n")
                f.write("\n".join(fit_summary_lines))
                f.write("\n\n")
            if lines:
                section_title = "Route Details" if lang == "EN" else "Strecken-Details"
                f.write(section_title + "\n")
                f.write("-" * len(section_title) + "\n")
                f.write("\n".join(lines))
                f.write("\n")
            if fit_debug_lines:
                f.write("\n\n")
                f.write("\n".join(fit_debug_lines))
                f.write("\n")

        json_payload = {
            "source_file": os.path.basename(track_path),
            "output_language": lang,
            "track_points": len(points),
            "samples": sample_count,
            "distance_km": round(total_dist_m / 1000.0, 3),
            "fit_summary": fit_summary_data or None,
            "route_lines": lines,
        }
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(json_payload, jf, ensure_ascii=False, indent=2)

        use_color = color_enabled()
        print(
            colorize(("Fertig: " if lang == "DE" else "Done: "), "cyan", use_color)
            + out_path
        )
        print(
            colorize(("JSON: " if lang == "EN" else "JSON: "), "cyan", use_color)
            + json_path
        )
        return 0
    except DependencyError as exc:
        use_color = color_enabled()
        label = "Fehlende Abhaengigkeit:" if lang == "DE" else "Missing dependency:"
        print(colorize(label, "red", use_color))
        print(str(exc))
        return 1
    except GeocodingError as exc:
        use_color = color_enabled()
        label = "Geocoding abgebrochen:" if lang == "DE" else "Geocoding aborted:"
        print(colorize(label, "red", use_color))
        print(str(exc))
        return 1
    except FileNotFoundError as exc:
        use_color = color_enabled()
        print(colorize(str(exc), "red", use_color))
        hint = (
            f"Lege eine .gpx- oder .fit-Datei in {INBOX_DIR} "
            "oder starte mit --file PFAD bzw. --interactive."
            if lang == "DE"
            else f"Put a .gpx or .fit file into {INBOX_DIR} "
            "or run with --file PATH or --interactive."
        )
        print(hint)
        return 1
    except KeyboardInterrupt:
        use_color = color_enabled()
        msg = "Abbruch durch Benutzer." if lang == "DE" else "Aborted by user."
        print(colorize(msg, "yellow", use_color))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
