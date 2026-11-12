"""Laufende Aufzeichnung der Flugbewegungen im Umkreis des Flugplatzes (ADS-B von adsb.lol).

Fragt alle paar Sekunden die Live-Schnittstelle von adsb.lol für den Umkreis ab und schreibt die Positionen
nach flugdaten/<Datum>.json fort (Tage nach UTC, wie im Archiv). Die Karte zeigt die Datei als Ebene
"Flugspuren". Genutzt wird nur die dokumentierte Schnittstelle api.adsb.lol; die Tagesspuren der Webseite
(globe/data/traces) sperrt der Betreiber für automatische Abrufe, sie werden darum nicht geholt.
Vollständige Spuren eines vergangenen Tages liefert tools/flugdaten_laden.py aus dem offenen Archiv.

Nutzung (aus dem Projektordner, läuft bis Strg+C):
  python tools/flugdaten_sammeln.py
  python tools/flugdaten_sammeln.py --radius 5 --takt 6

Der Dienst drosselt bei zu vielen Abfragen (bei 5 s Takt etwa alle 5 Minuten eine Pause); Standard ist darum
6 s. Bitte den Takt nicht unter 2 s setzen – der Dienst ist ein Gemeinschaftsprojekt. Lizenz der Daten: ODbL 1.0.
"""
import datetime, json, sys, time

import flugspur as fs

API = "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}"
SCHREIBEN_S = 30


def opt(name, standard):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else standard


def heute():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


class Sammler:
    def __init__(self, radius, takt):
        self.radius, self.takt = radius, takt
        self.datum = heute()
        self.lade()

    def lade(self):
        """Stand eines früheren Laufs am selben Tag übernehmen."""
        self.fest, self.flz, self.geaendert = [], {}, False
        alt = fs.lies_tag(self.datum)
        for f in (alt or {}).get("fluege", []):
            if f.get("herkunft") == "live":
                z = self.flz.setdefault(f["hex"], {"stamm": {k: f.get(k, "") for k in ("hex", "reg", "typ", "art")},
                                                   "punkte": []})
                z["punkte"] += [p + [f.get("ruf", "")] for p in f["punkte"]]
            else:
                self.fest.append(f)

    def abfrage(self):
        nm = max(1, int(self.radius / 1.852) + 1)
        roh = fs.curl(API.format(lat=fs.MITTE_LAT, lon=fs.MITTE_LON, nm=nm), timeout=20)
        if not roh.strip().startswith(b"{"):
            raise OSError("keine Daten, vermutlich Drosselung des Dienstes")
        d = json.loads(roh)
        jetzt, n = d.get("now", time.time() * 1000) / 1000, 0
        for a in d.get("ac", []):
            lat, lon = a.get("lat"), a.get("lon")
            if lat is None or lon is None or fs.km(lat, lon) > self.radius:
                continue
            t = round(jetzt - (a.get("seen_pos") or 0), 1)
            z = self.flz.setdefault(a["hex"], {"stamm": {}, "punkte": []})
            z["stamm"] = {"hex": a["hex"], "reg": a.get("r", ""), "typ": a.get("t", ""), "art": a.get("desc", "")}
            n += 1
            if z["punkte"] and t <= z["punkte"][-1][0] + 0.5:
                continue                                    # keine neue Position seit der letzten Abfrage
            h, boden, hq = fs.hoehe(a.get("alt_baro"), a.get("alt_geom"))
            z["punkte"].append([t, round(lon, 6), round(lat, 6), h, boden, a.get("gs"), hq, (a.get("flight") or "").strip()])
            self.geaendert = True
        return n

    def schreibe(self):
        fluege = list(self.fest)
        for z in self.flz.values():
            fluege += fs.in_fluege(z["punkte"], {**z["stamm"], "ruf": "", "herkunft": "live"})
        fs.schreibe_tag(self.datum, fluege, self.radius)
        self.geaendert = False

    def lauf(self):
        print(f"Sammle Flugbewegungen im Umkreis von {self.radius:g} km um {fs.MITTE_LAT}, {fs.MITTE_LON} "
              f"(alle {self.takt:g} s, Strg+C beendet)", flush=True)
        letzte, pause, vorher = 0, self.takt, None
        try:
            while True:
                if heute() != self.datum:                   # Tageswechsel (UTC)
                    self.schreibe()
                    self.datum = heute()
                    self.lade()
                try:
                    n = self.abfrage()
                    pause = self.takt
                    if n != vorher:
                        print(f"{datetime.datetime.now():%H:%M:%S}  {n} Flugzeug(e) im Umkreis", flush=True)
                        vorher = n
                except (OSError, ValueError, KeyError) as e:
                    pause = min(300, max(15, pause * 2))    # bei Fehlern oder Drosselung langsamer werden
                    print(f"{datetime.datetime.now():%H:%M:%S}  Abfrage fehlgeschlagen ({e}), nächster Versuch in {pause:g} s", flush=True)
                if self.geaendert and time.time() - letzte > SCHREIBEN_S:
                    self.schreibe()
                    letzte = time.time()
                time.sleep(pause)
        except KeyboardInterrupt:
            pass
        finally:
            if self.geaendert:
                self.schreibe()
            print("Beendet, Stand gespeichert.")


if __name__ == "__main__":
    Sammler(opt("--radius", fs.RADIUS_KM), max(2.0, opt("--takt", 6.0))).lauf()
