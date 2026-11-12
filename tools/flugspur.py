"""Gemeinsame Funktionen für die Flugdaten (ADS-B von adsb.lol, ODbL 1.0).

Tagesdatei  flugdaten/<JJJJ-MM-TT>.json:
  {"datum", "quelle", "mitte": [lon, lat], "radius_km",
   "fluege": [{"hex", "reg", "typ", "art", "ruf", "herkunft": "archiv"|"live",
               "punkte": [[t_unix, lon, lat, hoehe_m_NHN | null, boden 0/1, gs_kt | null, hquelle], ...]}]}
  hquelle: "g" = GNSS-Höhe (um die Geoidhöhe auf NHN umgerechnet), "b" = Druckhöhe (1013 hPa, ohne QNH-Korrektur)
Übersicht  flugdaten/index.json: {"tage": [{"datum", "fluege", "flugzeuge", "datei"}]}  (ein Eintrag je Datei;
  ADS-B in <Datum>.json, FLARM in <Datum>-flarm.json – die Karte fasst die Dateien eines Tages zusammen)
"""
import datetime, json, math, os, subprocess

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDNER = os.path.join(WURZEL, "flugdaten")
MITTE_LAT, MITTE_LON = 53.3064, 12.7532      # Flugplatz Rechlin-Lärz (EDAX), Bahnmitte
RADIUS_KM = 5.0
GEOID_M = 38.5                                # Geoidhöhe bei Lärz: GNSS-Höhe (Ellipsoid) minus 38,5 m = Höhe über NHN
FT = 0.3048
PAUSE_S = 300                                 # Lücke, ab der eine Spur als neuer Flug zählt
QUELLE = "adsb.lol (ODbL 1.0), https://www.adsb.lol"
KOPF = "luftbildatlas-flugdaten/1.0"


def curl(url, timeout=60, extra=()):
    """Abruf über curl: nutzt den Zertifikatsspeicher des Systems (Python findet die Kette von GitHub hier nicht)."""
    r = subprocess.run(["curl", "-sL", "--compressed", "-m", str(timeout), "-A", KOPF, *extra, url], capture_output=True)
    if r.returncode != 0:
        raise OSError(f"curl {r.returncode}: {url}")
    return r.stdout


def km(lat, lon, lat0=MITTE_LAT, lon0=MITTE_LON):
    dy = (lat - lat0) * 111.19
    dx = (lon - lon0) * 111.19 * math.cos(math.radians(lat0))
    return math.hypot(dx, dy)


def hoehe(alt_baro, alt_geom):
    """-> (Höhe m NHN | None, boden 0/1, Quelle)"""
    if alt_baro == "ground":
        return None, 1, "b"
    if isinstance(alt_geom, (int, float)):
        return round(alt_geom * FT - GEOID_M, 1), 0, "g"
    if isinstance(alt_baro, (int, float)):
        return round(alt_baro * FT, 1), 0, "b"
    return None, 0, "b"


def aus_trace(d, radius_km=RADIUS_KM):
    """readsb-Spur (trace_full) -> Punkte im Umkreis, dazu das zuletzt gesendete Rufzeichen."""
    t0, punkte, ruf = d.get("timestamp", 0), [], ""
    for p in d.get("trace", []):
        if len(p) > 8 and isinstance(p[8], dict) and p[8].get("flight"):
            ruf = p[8]["flight"].strip()
        lat, lon = p[1], p[2]
        if lat is None or lon is None or km(lat, lon) > radius_km:
            continue
        h, boden, hq = hoehe(p[3], p[10] if len(p) > 10 else None)
        punkte.append([round(t0 + p[0], 1), round(lon, 6), round(lat, 6), h, boden, p[4], hq, ruf])
    return punkte


def in_fluege(punkte, stamm):
    """Punkte eines Flugzeugs an Lücken > PAUSE_S in einzelne Flüge teilen."""
    fluege, akt = [], []
    for p in sorted(punkte, key=lambda q: q[0]):
        if akt and p[0] - akt[-1][0] > PAUSE_S:
            fluege.append(akt)
            akt = []
        akt.append(p)
    if akt:
        fluege.append(akt)
    out = []
    for f in fluege:
        if len(f) < 2:
            continue
        ruf = next((p[7] for p in reversed(f) if len(p) > 7 and p[7]), stamm.get("ruf", ""))
        out.append({**stamm, "ruf": ruf, "punkte": [p[:7] for p in f]})
    return out


def schreibe_tag(datum, fluege, radius_km=RADIUS_KM, quelle=QUELLE, zusatz=""):
    """zusatz: Namensanhang der Datei, z. B. "-flarm" – je Quelle eine eigene Datei, die Karte fasst sie je Tag zusammen."""
    os.makedirs(ORDNER, exist_ok=True)
    fluege = sorted(fluege, key=lambda f: f["punkte"][0][0])
    tag = {"datum": datum, "quelle": quelle, "mitte": [MITTE_LON, MITTE_LAT], "radius_km": radius_km,
           "stand": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "fluege": fluege}
    pfad = os.path.join(ORDNER, datum + zusatz + ".json")
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        json.dump(tag, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(pfad + ".tmp", pfad)
    index_neu()
    return pfad


def lies_tag(datum, zusatz=""):
    try:
        with open(os.path.join(ORDNER, datum + zusatz + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def index_neu():
    tage = []
    for name in sorted(os.listdir(ORDNER)):
        if not (name.endswith(".json") and name[:4].isdigit()):
            continue
        try:
            with open(os.path.join(ORDNER, name), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        tage.append({"datum": d["datum"], "fluege": len(d["fluege"]),
                     "flugzeuge": len({x["hex"] for x in d["fluege"]}), "datei": name})
    with open(os.path.join(ORDNER, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"tage": tage}, f, ensure_ascii=False, indent=1)
