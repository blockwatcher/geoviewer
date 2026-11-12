"""Lädt Flugplatz-Infrastruktur und historische Objekte aus OpenStreetMap für das Atlasgebiet.

Fragt die Overpass-Schnittstelle einmal ab und schreibt osm/flugplatz.geojson; die Karte zeigt die Datei als
Ebene "Flugplatz + Geschichte (OSM)". So fragt die Seite nicht bei jedem Aufruf OSM ab.

Enthalten: Start-/Landebahnen, Rollwege, Vorfelder, Flugzeugbunker (Shelter), stillgelegte Flugbetriebsflächen,
Tower/Terminal/Hangars, historische Objekte, Ruinen, militärische Anlagen (auch aufgegebene), alte Bahntrassen.

Nutzung (aus dem Projektordner):
  python tools/osm_laden.py

Daten: © OpenStreetMap-Mitwirkende, ODbL 1.0. Von Freiwilligen erfasst, nicht amtlich, unterschiedlich vollständig.
"""
import json, os, subprocess, sys

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = "53.2795,12.7215,53.3145,12.7800"            # Atlasgebiet (Süd, West, Nord, Ost)
ABFRAGE = f"""[out:json][timeout:120][bbox:{BBOX}];
(
  nwr[aeroway];
  nwr["disused:aeroway"]; nwr["abandoned:aeroway"];
  nwr[historic]; nwr[ruins]; nwr[building=ruins];
  nwr[military]; nwr["disused:military"]; nwr["abandoned:military"]; nwr[bunker_type];
  nwr[building=hangar];
  way[railway~"^(abandoned|disused|razed)$"];
);
out tags geom;"""

RW = {"taxiway": "Rollweg", "runway": "Start- und Landebahn", "apron": "Vorfeld", "shelter": "Flugzeugbunker (Shelter)",
      "hangar": "Hangar", "terminal": "Abfertigungsgebäude", "tower": "Tower", "windsock": "Windsack",
      "aerodrome": "Flugplatz", "threshold": "Schwelle", "helipad": "Hubschrauberlandeplatz", "fuel": "Tankstelle",
      "navigationaid": "Navigationshilfe", "holding_position": "Rollhalt", "parking_position": "Abstellposition"}
HIST = {"aircraft": "Ausgestelltes Flugzeug", "ruins": "Ruine", "archaeological_site": "Bodendenkmal", "wreck": "Wrack",
        "memorial": "Gedenkstätte", "monument": "Denkmal", "bunker": "Bunker", "building": "Historisches Gebäude",
        "boundary_stone": "Grenzstein", "wayside_cross": "Wegkreuz", "tank": "Panzer", "cannon": "Geschütz"}
MIL = {"bunker": "Bunker", "range": "Schießbahn", "airfield": "Militärflugplatz", "barracks": "Kaserne",
       "danger_area": "Gefahrenbereich", "training_area": "Übungsgelände", "trench": "Graben", "ammunition": "Munitionslager"}
FLAECHE = {"apron", "shelter", "hangar", "terminal", "tower", "helipad"}
MERKEN = ("name", "ref", "description", "start_date", "end_date", "bunker_type", "surface", "width", "length", "ele",
          "operator", "wikipedia", "wikidata", "inscription", "aircraft:type", "model", "ruins", "access")


def einordnen(t):
    """Merkmale -> (Kategorie, Bezeichnung) oder None"""
    for vor, zusatz in (("disused:", "stillgelegt"), ("abandoned:", "aufgegeben")):
        a = t.get(vor + "aeroway")
        if a:
            return ("still_flaeche" if a in FLAECHE else "still_linie"), f"{RW.get(a, a)} ({zusatz})"
        m = t.get(vor + "military")
        if m == "bunker" and t.get("bunker_type") == "hardened_aircraft_shelter":
            return "shelter", f"Flugzeugbunker (Shelter, {zusatz})"
        if m:
            return "militaer", f"{MIL.get(m, m)} ({zusatz})"
    a = t.get("aeroway")
    if a:
        if a in ("windsock", "threshold", "holding_position", "parking_position", "navigationaid"):
            return "punkt", RW.get(a, a)
        kat = {"runway": "bahn", "taxiway": "rollweg", "apron": "vorfeld", "shelter": "shelter", "aerodrome": "platz"}.get(a, "gebaeude")
        return kat, RW.get(a, a)
    if t.get("bunker_type") or t.get("military") == "bunker":
        if t.get("bunker_type") == "hardened_aircraft_shelter":
            return "shelter", "Flugzeugbunker (Shelter)"
        return "militaer", "Munitionsbunker" if t.get("bunker_type") == "munitions" else "Bunker"
    if t.get("military"):
        return "militaer", MIL.get(t["military"], t["military"])
    if t.get("historic"):
        return "historisch", HIST.get(t["historic"], "Historisches Objekt")
    if t.get("ruins") or t.get("building") == "ruins":
        r = t.get("ruins", "yes")
        return "historisch", "Ruine" if r in ("yes", "no") else f"Ruine ({r})"
    if t.get("railway"):
        return "bahntrasse", "Ehemalige Bahntrasse"
    if t.get("building") == "hangar":
        return "gebaeude", "Hangar"
    return None


def feature(e, t, kat, art, geom):
    p = {"kat": kat, "art": art, "osm": f"{e['type']}/{e['id']}"}
    p.update({k.replace(":", "_"): t[k] for k in MERKEN if t.get(k)})
    return {"type": "Feature", "properties": p, "geometry": geom}


def geometrie(punkte, kat, t):
    k = [[round(p["lon"], 7), round(p["lat"], 7)] for p in punkte]
    zu = len(k) > 3 and k[0] == k[-1]
    als_flaeche = zu and (kat in ("vorfeld", "shelter", "gebaeude", "still_flaeche", "historisch", "militaer")
                          or t.get("area") == "yes" or "building" in t)
    if kat == "platz" or not als_flaeche:
        return {"type": "LineString", "coordinates": k}
    return {"type": "Polygon", "coordinates": [k]}


def main():
    r = subprocess.run(["curl", "-s", "-m", "180", "-A", "luftbildatlas-daten/1.0", "--data-urlencode", "data=" + ABFRAGE, OVERPASS],
                       capture_output=True)
    try:
        elemente = json.loads(r.stdout)["elements"]
    except (ValueError, KeyError):
        sys.exit(f"Overpass-Abfrage fehlgeschlagen: {r.stdout[:300]!r}")
    features, zahl = [], {}
    for e in elemente:
        t = e.get("tags") or {}
        ein = einordnen(t)
        if not ein:
            continue
        kat, art = ein
        if e["type"] == "node":
            features.append(feature(e, t, kat, art, {"type": "Point", "coordinates": [round(e["lon"], 7), round(e["lat"], 7)]}))
        elif e["type"] == "way" and e.get("geometry"):
            features.append(feature(e, t, kat, art, geometrie(e["geometry"], kat, t)))
        elif e["type"] == "relation":
            for m in e.get("members", []):                  # Multipolygone: die äußeren Ringe einzeln
                if m.get("type") == "way" and m.get("geometry") and m.get("role") in ("outer", ""):
                    features.append(feature(e, t, kat, art, geometrie(m["geometry"], kat, t)))
        else:
            continue
        zahl[art] = zahl.get(art, 0) + 1
    ziel = os.path.join(WURZEL, "osm")
    os.makedirs(ziel, exist_ok=True)
    pfad = os.path.join(ziel, "flugplatz.geojson")
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "quelle": "© OpenStreetMap-Mitwirkende, ODbL 1.0", "features": features},
                  f, ensure_ascii=False, separators=(",", ":"))
    for art, n in sorted(zahl.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {art}")
    print(f"Fertig: {len(features)} Objekte -> {pfad} ({os.path.getsize(pfad) // 1024} kB)")


if __name__ == "__main__":
    main()
