# Track2Text

Creates a draft route description from the newest GPX file in `inbox/`.
The output is intentionally bullet-style and meant to be rewritten later
(e.g. with ChatGPT).

## What does the project do?

1. Finds the newest `*.gpx` or `*.fit` file in `inbox/` (FIT is preferred,
   because it can include more detailed metrics).
2. Reads track points (`trkpt`) or, if missing, route points (`rtept`).
3. Downsamples points to limit reverse-geocoding requests.
4. Reverse-geocodes streets, places, and districts.
5. Produces bullet points for road changes and section markers (default: every 3 km).
6. Writes `inbox/<filename>.txt` with raw data and bullet points.

While running, the script prints detailed English progress output including
coordinates, current road/place/district, and estimated progress. When the
terminal supports it, status lines are colored (set `NO_COLOR=1` to disable or
`TRACK2TEXT_COLOR=0`).

## Requirements

- Python 3
- Internet access (reverse geocoding via Nominatim/OpenStreetMap and optional
  Photon/Komoot)

For FIT support, install dependencies. On macOS with Homebrew Python you should
use a virtual environment to avoid system Python restrictions.

### Step-by-step (no Python experience needed)

1. Open Terminal and go to the project folder:

```bash
cd /path/to/Track2Text
```

2. Create a virtual environment (one-time):

```bash
python3 -m venv .venv
```

3. Activate the environment (required each time you open a new Terminal):

```bash
source .venv/bin/activate
```

You should now see `(.venv)` in your terminal prompt.

4. Install dependencies:

```bash
pip install -r requirements.txt
```

If `pip` is not found, use:

```bash
python3 -m pip install -r requirements.txt
```

5. Run the script:

```bash
python3 track2text.py
```

6. When you are done, you can deactivate the environment:

```bash
deactivate
```

## Run

```bash
python3 track2text.py
```

## Input / Output

- Input: newest `*.gpx` or `*.fit` in `inbox/`
- Output: `inbox/<filename>.txt` (same base name as GPX/FIT)

## Output language

You can choose whether the generated text file is German or English.

- Config file: set `output_language=DE` or `output_language=EN` in `config.txt`
- Config file: optional `input_file=...` to select a specific GPX/FIT file
- CLI override: `--output-language DE` or `--output-language EN`

CLI takes precedence over `config.txt`.

## Command-line flags (instead of env vars)

You can pass common settings directly after the Python command:

- `--TRACK2TEXT_MAX_SAMPLES=5` limits how many sampled points are processed.
  Fewer samples = faster run and shorter output.
- `--TRACK2TEXT_SECTION_KM=9999` changes the distance interval for section
  markers. A very large number effectively disables section markers.
- `--TRACK2TEXT_INCLUDE_START_GOAL=0` disables the Start/Finish lines.
- `--TRACK2TEXT_GEOCODER=nominatim|photon` chooses the main geocoder.
- `--TRACK2TEXT_LOCALITY_GEOCODER=nominatim|photon` chooses the locality geocoder.
- `--TRACK2TEXT_LOCALITY_ZOOM=12` sets the locality zoom level.
- `--TRACK2TEXT_MIN_DIST_M=50` sets the minimum distance between samples
  (higher = fewer points).
- `--file inbox/myride.fit` processes a specific GPX/FIT file (absolute or
  relative path, or filename in `inbox/`).
- `--NOMINATIM_USER_AGENT="track2text/1.0 (contact: you@example.com)"` sets a
  proper Nominatim user agent.

## Quick test (short output)

To check that everything works, you can run a tiny test with only 5 samples so
the output stays very short.

```bash
python3 track2text.py --quick-test
```

Open the generated `inbox/<filename>.txt` and confirm that the header contains
`Samples=5`. The bullet list will be only a few lines long.

## Presets

- `--fast` for a quicker, less detailed run (fewer samples, larger spacing).
- `--detailed` for a slower, more detailed run (more samples, smaller spacing).

## More examples

Short run in English with section markers every 2 km:

```bash
python3 track2text.py --output-language EN --TRACK2TEXT_SECTION_KM=2 \
--TRACK2TEXT_MAX_SAMPLES=50 --TRACK2TEXT_INCLUDE_START_GOAL=1
```

Fast debug run (few samples, no sections, no start/finish):

```bash
python3 track2text.py --TRACK2TEXT_MAX_SAMPLES=10 --TRACK2TEXT_SECTION_KM=9999 \
--TRACK2TEXT_INCLUDE_START_GOAL=0
```

Use a preset:

```bash
python3 track2text.py --fast
```

```bash
python3 track2text.py --detailed
```

## Example

Example output (excerpt, English mode):

```
Draft Route Description
=======================

Note: This list is a draft. Please summarize it into a readable
route description (e.g. with ChatGPT).

Format: bullets with road changes and place names.
Sections: automatically grouped by distance.

Raw data: track points=3245, samples=180, distance≈27.31 km
Source: 2024-06-15.gpx

- Start: Hohestrasse (Place: Koeln, District: Altstadt-Nord)
- Road change: Dagobertstrasse (Place: Koeln)
- Section: from km 3 (Place: Koeln), District: Neustadt-Nord
- Road change: Maybachstrasse
- ...
- Finish: Am Heumarkt (Place: Koeln, District: Altstadt-Sued)
```

For FIT files, an additional summary section is written above the bullet list
with selected key metrics. A separate Debug section lists all recognized FIT
fields per message type.

The output also includes a short “Summary at a glance” block near the top.

## Output format

- Bullet points with road changes, places, and districts
- Section lines grouped by distance
- Raw data line with track points, sample count, and approximate distance

## Configuration (optional)

Note: legacy `GPXER_*` variables are still accepted.

### Pick the geocoder

Default is Nominatim for streets and Photon for places/districts. Photon can
produce slightly different place names:

```bash
TRACK2TEXT_GEOCODER=photon python3 track2text.py
```

### Combo: Nominatim for streets, Photon for places

```bash
TRACK2TEXT_GEOCODER=nominatim TRACK2TEXT_LOCALITY_GEOCODER=photon python3 track2text.py
```

### Reduce reverse-geocoding requests

```bash
TRACK2TEXT_MAX_SAMPLES=30 python3 track2text.py
```

### Section length

Default is 3 km per section:

```bash
TRACK2TEXT_SECTION_KM=3 python3 track2text.py
```

### Place name precision (zoom)

Default is zoom 12 for places/districts. Higher = more local, lower = more
coarse:

```bash
TRACK2TEXT_LOCALITY_ZOOM=12 python3 track2text.py
```

### Include start/finish location

Default is enabled (1). Disable with:

```bash
TRACK2TEXT_INCLUDE_START_GOAL=0 python3 track2text.py
```

### Set a proper Nominatim user agent

```bash
NOMINATIM_USER_AGENT="track2text/1.0 (contact: you@example.com)" python3 track2text.py
```

## Privacy / sensitive data

The script sends GPX coordinates to the configured geocoding services
(Nominatim/Photon). Avoid uploading private tracks if this is not desired.
No API keys are used or stored.

## Notes

- Nominatim has usage rules and rate limits; the script waits between requests
  (~1 second) to comply.
- If no GPX file is present, the script exits with an error.

## Project structure

- `track2text.py` - main script
- `inbox/` - input folder for GPX files and output TXT files
- `config.txt` - output language setting
