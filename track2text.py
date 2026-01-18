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


USER_AGENT = env_first(
    ["NOMINATIM_USER_AGENT"],
    "track2text/1.0 (local script; contact: none)",
)
GEOCODER = env_first(["TRACK2TEXT_GEOCODER", "GPXER_GEOCODER"], "nominatim").lower()
LOCALITY_GEOCODER = env_first(
    ["TRACK2TEXT_LOCALITY_GEOCODER", "GPXER_LOCALITY_GEOCODER"],
    "photon",
).lower()


@dataclass
class Point:
    lat: float
    lon: float


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
        if f.lower().endswith((".gpx", ".fit"))
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
        "Fehler: fitparse ist nicht installiert. Bitte `pip install -r requirements.txt` ausfuehren."
        if lang == "DE"
        else "Error: fitparse is not installed. Please run `pip install -r requirements.txt`."
    )
    raise RuntimeError(msg)


def format_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


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
        f"{ascent[0]} {ascent[1] or 'm'}" if ascent else None,
    )
    if ascent:
        summary_data["ascent"] = f"{ascent[0]} {ascent[1] or 'm'}"
    add_line(
        "Abstieg",
        "Descent",
        f"{descent[0]} {descent[1] or 'm'}" if descent else None,
    )
    if descent:
        summary_data["descent"] = f"{descent[0]} {descent[1] or 'm'}"

    max_grade = session_values.get("max_grade")
    add_line(
        "Max. Anstieg",
        "Max grade",
        f"{max_grade[0]} {max_grade[1] or '%'}" if max_grade else None,
    )
    if max_grade:
        summary_data["max_grade"] = f"{max_grade[0]} {max_grade[1] or '%'}"

    max_alt = session_values.get("max_altitude") or session_values.get("max_elevation")
    add_line(
        "Max. Hoehe",
        "Max altitude",
        f"{max_alt[0]} {max_alt[1] or 'm'}" if max_alt else None,
    )
    if max_alt:
        summary_data["max_altitude"] = f"{max_alt[0]} {max_alt[1] or 'm'}"

    avg_power = session_values.get("avg_power")
    add_line(
        "Durchschnittliche Watt",
        "Average power",
        f"{avg_power[0]} {avg_power[1] or 'W'}" if avg_power else None,
    )
    if avg_power:
        summary_data["avg_power"] = f"{avg_power[0]} {avg_power[1] or 'W'}"
    total_distance = session_values.get("total_distance")
    add_line(
        "Distanz",
        "Distance",
        f"{total_distance[0]} {total_distance[1] or 'm'}" if total_distance else None,
    )
    if total_distance:
        summary_data["distance"] = f"{total_distance[0]} {total_distance[1] or 'm'}"
    avg_speed = session_values.get("avg_speed")
    max_speed = session_values.get("max_speed")
    if avg_speed or max_speed:
        avg_part = f"{avg_speed[0]} {avg_speed[1] or 'm/s'}" if avg_speed else "-"
        max_part = f"{max_speed[0]} {max_speed[1] or 'm/s'}" if max_speed else "-"
        add_line(
            "Geschwindigkeit (avg/max)",
            "Speed (avg/max)",
            f"{avg_part} / {max_part}",
        )
        summary_data["speed_avg_max"] = f"{avg_part} / {max_part}"
    avg_hr = session_values.get("avg_heart_rate")
    max_hr = session_values.get("max_heart_rate")
    if avg_hr or max_hr:
        avg_part = f"{avg_hr[0]} {avg_hr[1] or 'bpm'}" if avg_hr else "-"
        max_part = f"{max_hr[0]} {max_hr[1] or 'bpm'}" if max_hr else "-"
        add_line(
            "Puls (avg/max)",
            "Heart rate (avg/max)",
            f"{avg_part} / {max_part}",
        )
        summary_data["heart_rate_avg_max"] = f"{avg_part} / {max_part}"
    avg_cadence = session_values.get("avg_cadence")
    add_line(
        "Durchschnittliche Kadenz",
        "Average cadence",
        f"{avg_cadence[0]} {avg_cadence[1] or 'rpm'}" if avg_cadence else None,
    )
    if avg_cadence:
        summary_data["avg_cadence"] = f"{avg_cadence[0]} {avg_cadence[1] or 'rpm'}"

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


def fetch_json(url: str, timeout: int = 20) -> Dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def reverse_geocode_nominatim(point: Point, zoom: int = 18) -> Dict:
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
    return fetch_json(url)


def reverse_geocode_photon(point: Point) -> Dict:
    params = {
        "lat": f"{point.lat:.7f}",
        "lon": f"{point.lon:.7f}",
    }
    url = "https://photon.komoot.io/reverse?" + urllib.parse.urlencode(params)
    data = fetch_json(url)
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


def reverse_geocode(point: Point, zoom: int = 18) -> Dict:
    if GEOCODER == "photon":
        return normalize_photon(reverse_geocode_photon(point))
    return reverse_geocode_nominatim(point, zoom=zoom)


def reverse_geocode_locality(point: Point, zoom: int = 12) -> Dict:
    if LOCALITY_GEOCODER == "photon":
        return normalize_photon(reverse_geocode_photon(point))
    return reverse_geocode_nominatim(point, zoom=zoom)


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
            data = reverse_geocode(p, zoom=18)
        except Exception as exc:
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
                    loc_data = reverse_geocode_locality(p, zoom=locality_zoom)
                    loc_address = loc_data.get("address", {})
                    section_locality = pick_locality(loc_address) or section_locality
                    section_ortsteil = pick_ortsteil(loc_address) or section_ortsteil
                except Exception:
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
                        loc_data = reverse_geocode_locality(p, zoom=locality_zoom)
                        loc_address = loc_data.get("address", {})
                        last_locality = pick_locality(loc_address) or last_locality
                        last_ortsteil = pick_ortsteil(loc_address) or last_ortsteil
                    except Exception:
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
            "heart_rate_avg_max",
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
    if args.quick_test:
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "5"
        os.environ["TRACK2TEXT_SECTION_KM"] = "9999"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "0"
    if args.fast:
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "80"
        os.environ["TRACK2TEXT_MIN_DIST_M"] = "120"
        os.environ["TRACK2TEXT_SECTION_KM"] = "5"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "1"
    if args.detailed:
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = "400"
        os.environ["TRACK2TEXT_MIN_DIST_M"] = "25"
        os.environ["TRACK2TEXT_SECTION_KM"] = "2"
        os.environ["TRACK2TEXT_LOCALITY_ZOOM"] = "14"
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = "1"
    if args.file is not None:
        os.environ["TRACK2TEXT_INPUT_FILE"] = args.file
    if args.TRACK2TEXT_MAX_SAMPLES is not None:
        os.environ["TRACK2TEXT_MAX_SAMPLES"] = args.TRACK2TEXT_MAX_SAMPLES
    if args.TRACK2TEXT_SECTION_KM is not None:
        os.environ["TRACK2TEXT_SECTION_KM"] = args.TRACK2TEXT_SECTION_KM
    if args.TRACK2TEXT_INCLUDE_START_GOAL is not None:
        os.environ["TRACK2TEXT_INCLUDE_START_GOAL"] = (
            args.TRACK2TEXT_INCLUDE_START_GOAL
        )
    if args.TRACK2TEXT_GEOCODER is not None:
        os.environ["TRACK2TEXT_GEOCODER"] = args.TRACK2TEXT_GEOCODER
    if args.TRACK2TEXT_LOCALITY_GEOCODER is not None:
        os.environ["TRACK2TEXT_LOCALITY_GEOCODER"] = args.TRACK2TEXT_LOCALITY_GEOCODER
    if args.TRACK2TEXT_LOCALITY_ZOOM is not None:
        os.environ["TRACK2TEXT_LOCALITY_ZOOM"] = args.TRACK2TEXT_LOCALITY_ZOOM
    if args.TRACK2TEXT_MIN_DIST_M is not None:
        os.environ["TRACK2TEXT_MIN_DIST_M"] = args.TRACK2TEXT_MIN_DIST_M
    if args.NOMINATIM_USER_AGENT is not None:
        os.environ["NOMINATIM_USER_AGENT"] = args.NOMINATIM_USER_AGENT
    config = load_config(CONFIG_PATH)
    lang = normalize_output_language(
        args.output_language or config.get("output_language")
    )

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
        lines, sample_count = build_description(points, total_dist_m, lang)

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
    except KeyboardInterrupt:
        use_color = color_enabled()
        msg = "Abbruch durch Benutzer." if lang == "DE" else "Aborted by user."
        print(colorize(msg, "yellow", use_color))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
