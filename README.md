# Luftbildatlas Lärz – MapLibre 3D

Nachbau des [Luftbildatlas Lärz](https://soenkeba.github.io/Luftbildatlas_Laerz_oeffentlich/) (Müritz Airpark)
mit MapLibre GL JS, 3D-Gelände und dem DGM1 als Höhenquelle.

## Starten

```bash
python -m http.server 8765
# → http://localhost:8765
```

Die Seite braucht einen Webserver (kein Doppelklick auf `index.html`), weil sie Dateien nachlädt.

## Daten

```bash
python tools/daten_laden.py            # Geländemodell + historische Bilder
python tools/daten_laden.py --dgm      # nur Geländemodell
python tools/daten_laden.py --bilder   # nur Bilder
```

Benötigt `numpy`, `rasterio`, `scipy`, `Pillow`.

| Ordner | Inhalt | Quelle |
|---|---|---|
| `dgm/` | DGM1 (1 m, 4 × 4 Teile) für das Atlasgebiet, DGM25 fürs Umland, `dgm.json` | WCS `dgm_wcs` des LAiV M-V |
| `bilder/` | Mosaike 1944/45 (UTM 33N) und die drei NCAP-Frames | ursprünglicher Atlas |
| `lib/` | MapLibre GL JS 5.24.1 | unpkg |

Alle amtlichen Ebenen (DOP, DOP 1953/1991, ALKIS, TK25, DTK25, Schummerung, TopPlusOpen, basemap.de,
Sentinel-2) kommen live von den Diensten des Landes M-V und des BKG.

## 3D-Gebäude und Bauleitpläne

```bash
python tools/gebaeude_laden.py                 # LoD2-Gebäude des Landes für das Atlasgebiet
```

- **Gebäude 3D:** `gebaeude/lod2.geojson` aus dem Downloaddienst 3D-Gebäudedaten MV (CityGML, Kacheln 2 x 2 km).
  Die Karte zeigt sie in der 3D-Ansicht als Baukörper mit mittlerer Dachhöhe; MapLibre kennt nur senkrechte
  Prismen, Dachformen werden nicht dargestellt. Klick zeigt First- und Traufhöhe.
- **Bebauungs- und Flächennutzungspläne:** live vom Dienst `bauleitplan_wms` (Bau- und Planungsportal M-V,
  dl-de/by-2-0). Gezeigt werden die Geltungsbereiche; ein Klick fragt Plan, Rechtsstand und den Link zu den
  Unterlagen ab. Der Bestand im Portal ist nicht vollständig.

## Flugspuren (ADS-B)

```bash
python tools/flugdaten_laden.py 2026-11-07     # vergangener Tag aus dem offenen Archiv von adsb.lol
python tools/flugdaten_sammeln.py              # laufende Aufzeichnung, bis Strg+C
```

Beide schreiben `flugdaten/<Datum>.json` (Tage nach UTC) und `flugdaten/index.json`; die Karte zeigt sie im
Abschnitt „Flugspuren“. Standard ist ein Umkreis von 5 km um den Platz (`--radius`).

- **Archiv:** enthält je Tag alle Flugzeuge der Welt (rund 3,4 GB) ohne Gebietsausschnitt. Das Skript liest es
  im Durchlauf und speichert nur die Treffer; übertragen wird das ganze Archiv.
- **Sammler:** fragt alle 5 s `api.adsb.lol` für den Umkreis ab. Läuft er, aktualisiert die Karte den heutigen
  Tag alle 30 s.
- **Höhen:** GNSS-Höhe minus Geoidhöhe (38,5 m) = Höhe über NHN; fehlt sie, die Druckhöhe ohne QNH-Korrektur.
  Höhe über Grund = Höhe über NHN minus DGM.
- **Grenzen:** nur Flugzeuge mit ADS-B, und nur soweit Empfänger der Community sie in Bodennähe erfassen.

Daten: adsb.lol, ODbL 1.0.

### FLARM (Open Glider Network)

```bash
python tools/flugdaten_flarm.py                # laufende Aufzeichnung, bis Strg+C
```

Hört den Live-Strom der OGN-APRS-Server für den Umkreis mit und schreibt `flugdaten/<Datum>-flarm.json`.
Die Karte fasst ADS-B und FLARM je Tag zusammen; je Flug steht dabei, über welche Station er empfangen wurde
(nächste aktive Station: EDAX2, rund 7 km vom Platz). Ein Archiv gibt es nicht.

Regeln des OGN: Halterwünsche aus der Gerätedatenbank werden beachtet (nicht verfolgen → verworfen, nicht
identifiziert → ohne Kennzeichen). Daten, die älter als 24 Stunden sind, dürfen nicht weitergegeben werden –
ältere FLARM-Tage also nicht veröffentlichen.

## Aufbau

- **Gelände:** Die Höhen liegen als UTM-Raster vor (PNG, Zentimeter in 16 Bit). Das Protokoll `dgm://`
  in `index.html` rechnet daraus beim Abruf Terrarium-Kacheln für MapLibre (`raster-dem`). Daraus entstehen
  3D-Gelände, Schummerung, Höhenfarben und die Höhenanzeige am Mauszeiger.
- **WMS M-V:** Der Dienst stempelt jedes Bild mit einem Copyright-Vermerk. Das Protokoll `mvwms://`
  ruft jede Kachel mit Rand ab und schneidet ihn weg.
- **NCAP-Frames:** projektiv entzerrt (Homographie) in ein UTM-ausgerichtetes Canvas, dann als
  Canvas-Quelle in die Karte gelegt.
- **Koordinaten:** Messpunkte und Marker liegen wie im Original in Karten-Metern
  (x = E − 348600, y = 5909100 − N, EPSG:25833). Sitzungsdateien des Originals lassen sich laden.

## Nicht im Repository

- `bilder/f*.jpg` – die drei NCAP-Frames (gekaufte Scans, Urheberrecht NCAP). `python tools/daten_laden.py --bilder`
  holt sie lokal; fehlen sie, lädt die Karte sie vom ursprünglichen Atlas.
- `flugdaten/*-flarm.json` – FLARM-Aufzeichnungen (Weitergabe nach 24 Stunden nicht erlaubt).
- `airparkinternal/` – verschlüsselter Bereich des ursprünglichen Atlas.

## Lizenzen

DOP, ALKIS, TK25, DTK25, DGM © GeoBasis-DE/M-V (CC BY 4.0) · Luftbilder 1944/45 (Vorschauen) © GeoBasis-DE/MV,
CC BY 4.0 (Quelle verändert) · TopPlusOpen, Sentinel-2, basemap.de © BKG (dl-de/by-2-0) ·
NCAP-Frames © National Collection of Aerial Photography (Nutzungsbedingungen der NCAP beachten) ·
Google Satellite © Google. Georeferenzierung und ursprünglicher Atlas: Sönke Bartling.

Keine Kampfmittelauskunft – für Bauvorhaben gilt allein die Auskunft des MBD M-V.
