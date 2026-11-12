"""Laufende Aufzeichnung von FLARM- und OGN-Trackern im Umkreis des Flugplatzes (Open Glider Network).

Hört den Live-Strom der OGN-APRS-Server für den Umkreis mit und schreibt die Positionen nach
flugdaten/<Datum>-flarm.json fort (Tage nach UTC). Die Karte zeigt sie zusammen mit den ADS-B-Spuren
des Tages in der Ebene "Flugspuren". Je Flug wird vermerkt, welche Empfangsstationen ihn gehört haben –
so sieht man, ob der Platz überhaupt abgedeckt ist (nächste Station: EDAX2, rund 7 km entfernt).

Nutzung (aus dem Projektordner, läuft bis Strg+C):
  python tools/flugdaten_flarm.py
  python tools/flugdaten_flarm.py --radius 5

Regeln des OGN (https://www.glidernet.org/ogn-data-usage), Lizenz ODbL:
  - Die Wünsche der Halter in der Gerätedatenbank (DDB) gelten: Geräte mit "nicht verfolgen" werden verworfen,
    Geräte ohne Freigabe der Identität bleiben ohne Kennzeichen.
  - OGN-Daten, die älter als 24 Stunden sind, dürfen nicht weitergegeben werden. Die Dateien sind also nur
    für die eigene Auswertung gedacht – ältere FLARM-Tage nicht veröffentlichen.
"""
import datetime, json, re, socket, sys, time

import flugspur as fs

SERVER, PORT = "aprs.glidernet.org", 14580
DDB = "https://ddb.glidernet.org/download/?j=1&t=1"
QUELLE = "Open Glider Network (ODbL), https://www.glidernet.org – nicht weitergeben, wenn älter als 24 h"
ZUSATZ = "-flarm"
SCHREIBEN_S, KEEPALIVE_S, DDB_NEU_S = 30, 240, 86400
NETZ = {"OGFLR": "FLARM", "OGFLR6": "FLARM", "OGFLR7": "FLARM", "OGNTRK": "OGN-Tracker", "OGADSL": "ADS-L",
        "OGPAW": "PilotAware", "OGNFNT": "FANET", "OGFNT": "FANET", "OGNSKY": "SafeSky", "OGSPOT": "Spot",
        "OGNAVI": "Naviter", "OGFLYM": "Flymaster", "OGSKYL": "Skylines", "OGLT24": "Livetrack24"}
OHNE = {"OGADSB"}                       # ADS-B kommt schon von adsb.lol, sonst erschiene jedes Flugzeug doppelt
ART = {1: "Segelflugzeug", 2: "Schleppflugzeug", 3: "Hubschrauber", 4: "Fallschirm", 5: "Absetzflugzeug",
       6: "Drachen", 7: "Gleitschirm", 8: "Motorflugzeug", 9: "Jet", 11: "Ballon", 12: "Luftschiff", 13: "Drohne",
       15: "Hindernis"}
ZEILE = re.compile(r"^(?P<von>[^>]+)>(?P<ziel>[^,]+),(?P<weg>[^:]*):/(?P<zeit>\d{6})h"
                   r"(?P<lat>\d{4}\.\d{2})(?P<ns>[NS]).(?P<lon>\d{5}\.\d{2})(?P<ew>[EW]).(?P<rest>.*)$")
KURS = re.compile(r"^(\d{3})/(\d{3})")
HOEHE = re.compile(r"/A=(-?\d{5,6})")
FEIN = re.compile(r"!W(\d)(\d)!")
GERAET = re.compile(r"\bid([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\b")


def opt(name, standard):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else standard


def lies_zeile(zeile, jetzt=None):
    """APRS-Zeile -> dict oder None (Stationsmeldungen, Kommentare und Unlesbares fallen heraus)."""
    m = ZEILE.match(zeile)
    if not m or m["ziel"] in OHNE:
        return None
    g = GERAET.search(m["rest"])
    if not g:
        return None                                   # Empfangsstation oder fremdes Format
    kenn = int(g[1], 16)
    if kenn & 0x40:                                   # "no track" am Gerät gesetzt
        return None
    fein = FEIN.search(m["rest"])
    lat = int(m["lat"][:2]) + (float(m["lat"][2:]) + (int(fein[1]) / 1000 if fein else 0)) / 60
    lon = int(m["lon"][:3]) + (float(m["lon"][3:]) + (int(fein[2]) / 1000 if fein else 0)) / 60
    lat, lon = (-lat if m["ns"] == "S" else lat), (-lon if m["ew"] == "W" else lon)
    jetzt = jetzt or datetime.datetime.now(datetime.timezone.utc)
    z = m["zeit"]
    t = jetzt.replace(hour=int(z[:2]), minute=int(z[2:4]), second=int(z[4:]), microsecond=0)
    if t - jetzt > datetime.timedelta(hours=1):       # Meldung von kurz vor Mitternacht
        t -= datetime.timedelta(days=1)
    k, h = KURS.match(m["rest"]), HOEHE.search(m["rest"])
    return {"adresse": g[2].lower(), "art": (kenn >> 2) & 0xF, "netz": NETZ.get(m["ziel"], m["ziel"]),
            "empf": m["weg"].split(",")[-1], "t": t.timestamp(), "lat": lat, "lon": lon,
            "gs": int(k[2]) if k else None, "h": round(int(h[1]) * fs.FT, 1) if h else None}


class Geraete:
    """Gerätedatenbank des OGN: Kennzeichen und die Wünsche der Halter."""
    def __init__(self):
        self.d, self.stand = {}, 0

    def lade(self):
        try:
            roh = json.loads(fs.curl(DDB, timeout=120))
            self.d = {g["device_id"].lower(): g for g in roh.get("devices", [])}
            self.stand = time.time()
            print(f"Gerätedatenbank geladen: {len(self.d)} Geräte", flush=True)
        except (OSError, ValueError) as e:
            self.stand = time.time() - DDB_NEU_S + 600        # in 10 min erneut versuchen
            print(f"Gerätedatenbank nicht ladbar ({e})", flush=True)

    def stamm(self, p):
        """-> Stammdaten oder None, wenn der Halter keine Verfolgung wünscht."""
        g = self.d.get(p["adresse"])
        if g and g.get("tracked") != "Y":
            return None
        offen = bool(g) and g.get("identified") == "Y"
        modell = g.get("aircraft_model", "") if offen else ""
        return {"hex": p["adresse"], "reg": g.get("registration", "") if offen else "",
                "typ": "" if modell in ("", "Unknown") else modell, "art": ART.get(p["art"], ""),
                "ruf": g.get("cn", "") if offen else "", "netz": p["netz"]}


class Sammler:
    def __init__(self, radius):
        self.radius, self.ddb = radius, Geraete()
        self.datum = self.heute()
        self.lade()

    @staticmethod
    def heute():
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    def lade(self):
        """Stand eines früheren Laufs am selben Tag übernehmen."""
        self.flz, self.geaendert = {}, False
        for f in (fs.lies_tag(self.datum, ZUSATZ) or {}).get("fluege", []):
            z = self.flz.setdefault(f["hex"], {"stamm": {k: f.get(k, "") for k in ("hex", "reg", "typ", "art", "ruf", "netz")},
                                               "punkte": [], "empf": set()})
            z["punkte"] += f["punkte"]
            z["empf"] |= set(f.get("empf", []))

    def nimm(self, p):
        if fs.km(p["lat"], p["lon"]) > self.radius:
            return
        stamm = self.ddb.stamm(p)
        if stamm is None:
            return
        neu = p["adresse"] not in self.flz
        z = self.flz.setdefault(p["adresse"], {"stamm": stamm, "punkte": [], "empf": set()})
        z["stamm"] = stamm
        z["empf"].add(p["empf"])
        if z["punkte"] and p["t"] <= z["punkte"][-1][0]:
            return                                    # dieselbe Meldung über eine zweite Station
        z["punkte"].append([p["t"], round(p["lon"], 6), round(p["lat"], 6), p["h"], 0, p["gs"], "g"])
        self.geaendert = True
        if neu:
            print(f"{datetime.datetime.now():%H:%M:%S}  + {stamm['reg'] or stamm['hex']} {stamm['art']} "
                  f"({stamm['netz']}, über {p['empf']})", flush=True)

    def schreibe(self):
        fluege = []
        for z in self.flz.values():
            fluege += fs.in_fluege(z["punkte"], {**z["stamm"], "herkunft": "live", "empf": sorted(z["empf"])})
        fs.schreibe_tag(self.datum, fluege, self.radius, QUELLE, ZUSATZ)
        self.geaendert = False

    def verbinde(self):
        s = socket.create_connection((SERVER, PORT), timeout=30)
        s.sendall(f"user N0CALL pass -1 vers luftbildatlas 1.0 filter r/{fs.MITTE_LAT:.4f}/{fs.MITTE_LON:.4f}/{max(1, round(self.radius))}\r\n".encode())
        s.settimeout(20)
        return s

    def lauf(self):
        print(f"Höre FLARM/OGN im Umkreis von {self.radius:g} km um {fs.MITTE_LAT}, {fs.MITTE_LON} (Strg+C beendet)", flush=True)
        letzte, pause = 0, 5
        try:
            while True:
                try:
                    s = self.verbinde()
                except OSError as e:
                    print(f"Verbindung fehlgeschlagen ({e}), neuer Versuch in {pause} s", flush=True)
                    time.sleep(pause)
                    pause = min(300, pause * 2)
                    continue
                print(f"{datetime.datetime.now():%H:%M:%S}  verbunden mit {SERVER}", flush=True)
                puffer, lebt = b"", time.time()
                try:
                    while True:
                        if time.time() - self.ddb.stand > DDB_NEU_S:
                            self.ddb.lade()
                        if self.heute() != self.datum:                 # Tageswechsel (UTC)
                            self.schreibe()
                            self.datum = self.heute()
                            self.lade()
                        try:
                            teil = s.recv(8192)
                            if not teil:
                                raise OSError("Server hat die Verbindung beendet")
                            puffer += teil
                            pause = 5
                        except socket.timeout:
                            pass
                        while b"\n" in puffer:
                            roh, puffer = puffer.split(b"\n", 1)
                            zeile = roh.decode("latin-1").strip()
                            if zeile and not zeile.startswith("#"):
                                p = lies_zeile(zeile)
                                if p:
                                    self.nimm(p)
                        if time.time() - lebt > KEEPALIVE_S:
                            s.sendall(b"#keepalive\r\n")
                            lebt = time.time()
                        if self.geaendert and time.time() - letzte > SCHREIBEN_S:
                            self.schreibe()
                            letzte = time.time()
                except OSError as e:
                    print(f"{datetime.datetime.now():%H:%M:%S}  Verbindung verloren ({e}), verbinde neu in {pause} s", flush=True)
                    time.sleep(pause)
                    pause = min(300, pause * 2)
                finally:
                    s.close()
        except KeyboardInterrupt:
            pass
        finally:
            if self.geaendert:
                self.schreibe()
            print("Beendet, Stand gespeichert.")


if __name__ == "__main__":
    Sammler(opt("--radius", fs.RADIUS_KM)).lauf()
