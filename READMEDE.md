# Track2Text

Erzeugt eine Rohfassung einer Wegbeschreibung aus der neuesten GPX-Datei im
Ordner `inbox`. Die Ausgabe ist bewusst stichpunktartig und fuer eine spaetere
Umformulierung (z. B. mit ChatGPT) gedacht. Fuer mehr Details sind `.fit`-Dateien
zu bevorzugen.

## Was macht das Projekt genau?

1. Sucht die neueste `*.gpx` oder `*.fit` Datei in `inbox/` (FIT wird bevorzugt,
   da es mehr Details enthalten kann).
2. Liest Trackpunkte (`trkpt`) oder, falls keine vorhanden sind, Routenpunkte
   (`rtept`).
3. Reduziert die Punktzahl (Downsampling), um die Zahl der Geocoding-Abfragen
   zu begrenzen.
4. Fuehrt Reverse-Geocoding durch, um Strassen, Orte und Ortsteile zu finden.
5. Erzeugt Stichpunkte fuer Strassenwechsel und Abschnittsmarker (Standard:
   alle 3 km).
6. Schreibt eine Textdatei `inbox/<dateiname>.txt` mit Rohdaten und
   Stichpunkten.

Waehren des Laufs gibt das Script detaillierte Fortschrittsmeldungen auf
Englisch aus (inkl. Koordinaten, aktueller Strasse/Ort/Ortsteil und
Fortschrittsanzeige). Wenn das Terminal es unterstuetzt, werden Statuszeilen
farbig dargestellt (mit `NO_COLOR=1` deaktivierbar oder `TRACK2TEXT_COLOR=0`).

## Voraussetzungen

- Python 3
- Internetzugang (Reverse-Geocoding ueber Nominatim/OpenStreetMap und optional
  Photon/Komoot)

Fuer FIT-Unterstuetzung muessen Abhaengigkeiten installiert werden. Auf macOS
mit Homebrew-Python solltest du dafuer eine virtuelle Umgebung nutzen, damit
das System-Python nicht beeinflusst wird.

### Schritt-fuer-Schritt (auch ohne Python-Erfahrung)

1. Terminal oeffnen und in den Projektordner wechseln:

```bash
cd /pfad/zu/Track2Text
```

2. Virtuelle Umgebung anlegen (einmalig):

```bash
python3 -m venv .venv
```

3. Umgebung aktivieren (bei jedem neuen Terminal-Fenster noetig):

```bash
source .venv/bin/activate
```

Im Terminal erscheint dann `(.venv)`.

4. Abhaengigkeiten installieren:

```bash
pip install -r requirements.txt
```

Falls `pip` nicht gefunden wird, nutze:

```bash
python3 -m pip install -r requirements.txt
```

5. Script starten:

```bash
python3 track2text.py
```

6. Danach kann die Umgebung deaktiviert werden:

```bash
deactivate
```

## Ausfuehren

Im Projektordner:

```bash
python3 track2text.py
```

## Eingabe / Ausgabe

- Eingabe: neueste `*.gpx` oder `*.fit` in `inbox`
- Ausgabe: `inbox/<dateiname>.txt` (gleicher Basisname wie GPX/FIT)

## Ausgabesprache

Die Sprache der generierten Textdatei kann auf Deutsch oder Englisch gesetzt
werden.

- Konfigdatei: `output_language=DE` oder `output_language=EN` in `config.txt`
- Konfigdatei: optional `input_file=...` fuer eine bestimmte GPX/FIT-Datei
- CLI-Override: `--output-language DE` oder `--output-language EN`

Die CLI-Angabe hat Vorrang vor `config.txt`.

## Kommandozeilen-Attribute (anstatt Env-Variablen)

Du kannst wichtige Einstellungen direkt nach dem Python-Aufruf uebergeben:

- `--TRACK2TEXT_MAX_SAMPLES=5` begrenzt die Anzahl der Samples. Weniger Samples
  bedeuten schnellere Laufzeit und kuerzere Ausgabe.
- `--TRACK2TEXT_SECTION_KM=9999` legt die Distanz fuer Abschnittsmarker fest.
  Ein sehr hoher Wert deaktiviert die Marker praktisch.
- `--TRACK2TEXT_INCLUDE_START_GOAL=0` deaktiviert die Start- und Zielzeilen.
- `--TRACK2TEXT_GEOCODER=nominatim|photon` waehlt den Haupt-Geocoder.
- `--TRACK2TEXT_LOCALITY_GEOCODER=nominatim|photon` waehlt den Ortsnamen-Geocoder.
- `--TRACK2TEXT_LOCALITY_ZOOM=12` setzt die Ortsnamen-Genauigkeit.
- `--TRACK2TEXT_MIN_DIST_M=50` setzt den Mindestabstand zwischen Samples
  (hoeher = weniger Punkte).
- `--file inbox/meintrack.fit` verarbeitet eine bestimmte GPX/FIT-Datei
  (absoluter oder relativer Pfad, oder Dateiname in `inbox/`).
- `--NOMINATIM_USER_AGENT="track2text/1.0 (contact: you@example.com)"` setzt
  einen passenden Nominatim-User-Agent.

## Schnelltest (kurze Ausgabe)

Um zu pruefen, ob alles laeuft, kannst du einen Mini-Test mit nur 5 Samples
starten, damit die Ausgabe sehr kurz bleibt.

```bash
python3 track2text.py --quick-test
```

Oeffne danach `inbox/<dateiname>.txt` und pruefe, ob im Kopf `Samples=5`
angezeigt wird. Die Stichpunktliste ist dann nur wenige Zeilen lang.

## Presets

- `--fast` fuer einen schnellen, weniger detaillierten Lauf (weniger Samples,
  groesserer Abstand).
- `--detailed` fuer einen langsameren, detaillierten Lauf (mehr Samples,
  kleinerer Abstand).

## Weitere Beispiele

Kurzer Lauf auf Englisch mit Abschnittsmarkern alle 2 km:

```bash
python3 track2text.py --output-language EN --TRACK2TEXT_SECTION_KM=2 \
--TRACK2TEXT_MAX_SAMPLES=50 --TRACK2TEXT_INCLUDE_START_GOAL=1
```

Schneller Debug-Lauf (wenige Samples, keine Abschnitte, kein Start/Ziel):

```bash
python3 track2text.py --TRACK2TEXT_MAX_SAMPLES=10 --TRACK2TEXT_SECTION_KM=9999 \
--TRACK2TEXT_INCLUDE_START_GOAL=0
```

Preset nutzen:

```bash
python3 track2text.py --fast
```

```bash
python3 track2text.py --detailed
```

## Beispiel

Beispielausgabe (Auszug, Englisch-Modus):

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

Bei FIT-Dateien wird zusaetzlich eine Zusammenfassung mit den wichtigsten
Kennzahlen oberhalb der Stichpunkte ausgegeben. Im Teil "Debug" sind alle
erkannten FIT-Felder je Message-Typ aufgefuehrt.

Zusaetzlich gibt es einen kurzen Block "Kurzueberblick" nahe am Anfang.

## Format der Ausgabe

- Stichpunkte mit Strassenwechseln, Ortsangaben und Ortsteilen
- Abschnittszeilen nach Distanz
- Rohdatenzeile mit Trackpunkten, Sample-Anzahl und ungefaehrer Distanz

## Konfiguration (optional)

Hinweis: Die alten `GPXER_*` Variablen werden weiterhin akzeptiert.

### Geocoder waehlen

Standard ist Nominatim fuer Strassen und Photon fuer Ortsnamen/Ortsteile.
Photon kann bei Ortsnamen/Ortsteilen abweichen:

```bash
TRACK2TEXT_GEOCODER=photon python3 track2text.py
```

### Kombi: Nominatim fuer Strassen, Photon fuer Ortsnamen

```bash
TRACK2TEXT_GEOCODER=nominatim TRACK2TEXT_LOCALITY_GEOCODER=photon python3 track2text.py
```

### Anzahl der Reverse-Geocoding-Anfragen reduzieren

```bash
TRACK2TEXT_MAX_SAMPLES=30 python3 track2text.py
```

### Abschnittslaenge einstellen

Standard sind 3 km pro Abschnitt:

```bash
TRACK2TEXT_SECTION_KM=3 python3 track2text.py
```

### Ortsnamen-Genauigkeit (Zoom)

Standard ist Zoom 12 fuer Ortsnamen/Ortsteile. Hoeher = lokaler, niedriger =
grober:

```bash
TRACK2TEXT_LOCALITY_ZOOM=12 python3 track2text.py
```

### Start/Ziel mit Ort ausgeben

Standard ist aktiv (1). Zum Abschalten:

```bash
TRACK2TEXT_INCLUDE_START_GOAL=0 python3 track2text.py
```

### User-Agent fuer Nominatim setzen

Nominatim erwartet einen aussagekraeftigen User-Agent:

```bash
NOMINATIM_USER_AGENT="track2text/1.0 (contact: you@example.com)" python3 track2text.py
```

## Datenschutz / Sensitive Daten

Das Script sendet GPX-Koordinaten an die verwendeten Geocoding-Dienste
(Nominatim/Photon). Achte darauf, keine privaten Tracks hochzuladen, wenn das
nicht gewuenscht ist. Es werden keine API-Keys verwendet oder gespeichert.

## Hinweise

- Nominatim hat Nutzungsregeln und Rate Limits. Das Script wartet zwischen
  Anfragen (ca. 1 Sekunde), um diese einzuhalten.
- Falls keine GPX-Datei vorhanden ist, endet das Script mit einer Fehlermeldung.

## Projektstruktur

- `track2text.py` - Hauptscript
- `inbox/` - Eingabeordner fuer GPX-Dateien und Ausgabe der TXT-Dateien
- `config.txt` - Einstellung der Ausgabesprache
