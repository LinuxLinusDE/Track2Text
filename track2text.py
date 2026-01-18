#!/usr/bin/env python3
"""Create a German route description from the newest GPX in inbox/."""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


INBOX_DIR = os.path.join(os.path.dirname(__file__), "inbox")


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


def newest_gpx(inbox_dir: str) -> str:
    if not os.path.isdir(inbox_dir):
        raise FileNotFoundError(f"Inbox-Ordner nicht gefunden: {inbox_dir}")
    gpx_files = [
        os.path.join(inbox_dir, f)
        for f in os.listdir(inbox_dir)
        if f.lower().endswith(".gpx")
    ]
    if not gpx_files:
        raise FileNotFoundError("Keine GPX-Dateien im inbox-Ordner gefunden.")
    return max(gpx_files, key=os.path.getmtime)


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


def build_description(points: List[Point]) -> Tuple[List[str], int]:
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

    lines = []
    last_road = None
    last_locality = None
    last_ortsteil = None
    cumulative_m = 0.0
    next_section_m = section_km * 1000.0 if section_km > 0 else float("inf")

    for idx, p in enumerate(sampled):
        if idx > 0:
            time.sleep(1.0)  # Nominatim usage policy
            cumulative_m += haversine_m(sampled[idx - 1], p)
        try:
            data = reverse_geocode(p, zoom=18)
        except Exception as exc:
            lines.append(f"Hinweis: Reverse-Geocoding fehlgeschlagen ({exc}).")
            continue

        address = data.get("address", {})
        road = pick_road(address)
        locality = pick_locality(address)
        ortsteil = pick_ortsteil(address)

        if cumulative_m >= next_section_m:
            km_marker = int(next_section_m / 1000.0)
            title = f"Abschnitt: ab km {km_marker}"
            section_locality = locality
            section_ortsteil = ortsteil
            if locality_zoom:
                time.sleep(1.0)
                try:
                    loc_data = reverse_geocode_locality(p, zoom=locality_zoom)
                    loc_address = loc_data.get("address", {})
                    section_locality = pick_locality(loc_address) or section_locality
                    section_ortsteil = pick_ortsteil(loc_address) or section_ortsteil
                except Exception:
                    pass
            if section_locality:
                title += f" (Ort: {section_locality})"
            if section_ortsteil:
                title += f", Ortsteil: {section_ortsteil}"
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
                    time.sleep(1.0)
                    try:
                        loc_data = reverse_geocode_locality(p, zoom=locality_zoom)
                        loc_address = loc_data.get("address", {})
                        last_locality = pick_locality(loc_address) or last_locality
                        last_ortsteil = pick_ortsteil(loc_address) or last_ortsteil
                    except Exception:
                        pass
                start_entry = "- Start"
                if road:
                    start_entry += f": {road}"
                if locality or ortsteil:
                    start_entry += " ("
                    if locality:
                        start_entry += f"Ort: {locality}"
                    if ortsteil:
                        if locality:
                            start_entry += ", "
                        start_entry += f"Ortsteil: {ortsteil}"
                    start_entry += ")"
                lines.append(start_entry)
            continue
        if road and road != last_road:
            entry = f"- Straßenwechsel: {road}"
            location_bits = []
            if locality and locality != last_locality:
                location_bits.append(f"Ort: {locality}")
                last_locality = locality
            if ortsteil and ortsteil != last_ortsteil:
                location_bits.append(f"Ortsteil: {ortsteil}")
                last_ortsteil = ortsteil
            if location_bits:
                entry += " (" + ", ".join(location_bits) + ")"
            lines.append(entry)

        if road:
            last_road = road

    if include_start_goal and sampled:
        goal_entry = "- Ziel"
        if last_road:
            goal_entry += f": {last_road}"
        if last_locality or last_ortsteil:
            goal_entry += " ("
            if last_locality:
                goal_entry += f"Ort: {last_locality}"
            if last_ortsteil:
                if last_locality:
                    goal_entry += ", "
                goal_entry += f"Ortsteil: {last_ortsteil}"
            goal_entry += ")"
        lines.append(goal_entry)

    return lines, len(sampled)


def main() -> int:
    try:
        gpx_path = newest_gpx(INBOX_DIR)
    except Exception as exc:
        print(f"Fehler: {exc}")
        return 1

    points = parse_gpx_points(gpx_path)
    if not points:
        print("Fehler: Keine Track- oder Routenpunkte gefunden.")
        return 1

    total_dist_m = route_distance_m(points)
    lines, sample_count = build_description(points)

    base = os.path.splitext(os.path.basename(gpx_path))[0]
    out_path = os.path.join(os.path.dirname(gpx_path), f"{base}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("Rohfassung Wegbeschreibung\n")
        f.write("=" * 23 + "\n\n")
        f.write("Hinweis: Diese Liste ist eine Rohfassung. Bitte mit ChatGPT zu einer\n")
        f.write("gut lesenden Wegbeschreibung zusammenfassen.\n\n")
        f.write("Format: Stichpunkte mit Straßenwechseln und Ortsangaben.\n")
        f.write("Abschnitte: automatisch nach Distanz gegliedert.\n\n")
        f.write(f"Rohdaten: Trackpunkte={len(points)}, Samples={sample_count}, ")
        f.write(f"Distanz≈{total_dist_m/1000:.2f} km\n\n")
        f.write(f"Quelle: {os.path.basename(gpx_path)}\n\n")
        f.write("\n".join(lines))
        f.write("\n")

    print(f"Fertig: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
