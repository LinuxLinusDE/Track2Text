# Track2Text

Erzeugt eine deutsche Rohfassung einer Wegbeschreibung aus der neuesten GPX-Datei
im Ordner `inbox`. Die Ausgabe ist bewusst stichpunktartig und fuer eine
spaetere Umformulierung (z. B. mit ChatGPT) gedacht.

## Was macht das Projekt genau?

1. Sucht die neueste `*.gpx` Datei in `inbox/`.
2. Liest Trackpunkte (`trkpt`) oder, falls keine vorhanden sind, Routenpunkte
   (`rtept`).
3. Reduziert die Punktzahl (Downsampling), um die Zahl der Geocoding-Abfragen
   zu begrenzen.
4. Fuehrt Reverse-Geocoding durch, um Strassen, Orte und Ortsteile zu finden.
5. Erzeugt Stichpunkte fuer Strassenwechsel und Abschnittsmarker (Standard:
   alle 3 km).
6. Schreibt eine Textdatei `inbox/<dateiname>.txt` mit Rohdaten und
   Stichpunkten.

## Voraussetzungen

- Python 3
- Internetzugang (Reverse-Geocoding ueber Nominatim/OpenStreetMap und optional
  Photon/Komoot)

Es gibt keine externen Python-Abhaengigkeiten (nur Standardbibliothek). Die
`requirements.txt` ist daher leer.

## Ausfuehren

Im Projektordner:

```bash
python3 track2text.py
```

## Eingabe / Ausgabe

- Eingabe: neueste `*.gpx` in `inbox`
- Ausgabe: `inbox/<dateiname>.txt` (gleicher Basisname wie GPX)

## Beispiel

Beispielausgabe (Auszug):

```
Rohfassung Wegbeschreibung
==========================

Hinweis: Diese Liste ist eine Rohfassung. Bitte mit ChatGPT zu einer
gut lesenden Wegbeschreibung zusammenfassen.

Format: Stichpunkte mit Strassenwechseln und Ortsangaben.
Abschnitte: automatisch nach Distanz gegliedert.

Rohdaten: Trackpunkte=3245, Samples=180, Distanz≈27.31 km
Quelle: 2024-06-15.gpx

- Start: Hohestrasse (Ort: Koeln, Ortsteil: Altstadt-Nord)
- Strassenwechsel: Dagobertstrasse (Ort: Koeln)
- Abschnitt: ab km 3 (Ort: Koeln), Ortsteil: Neustadt-Nord
- Strassenwechsel: Maybachstrasse
- ...
- Ziel: Am Heumarkt (Ort: Koeln, Ortsteil: Altstadt-Sued)
```

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

Die Anzahl der Abfragen kann fuer schnelle Tests reduziert werden:

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
groeber:

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
