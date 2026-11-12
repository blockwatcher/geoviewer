"""Lädt die 3D-Gebäudemodelle (LoD2) des Landes M-V für das Atlasgebiet.

Quelle: Downloaddienst 3D-Gebäudedaten MV (https://www.geodaten-mv.de/dienste/gebaeude_atom), CityGML in
Kacheln von 2 x 2 km, © GeoBasis-DE/M-V (CC BY 4.0).

Ausgabe: gebaeude/lod2.geojson – je Gebäude der Grundriss mit Höhen in Metern über Grund:
  h      mittlere Dachhöhe (damit zeichnet die Karte den Baukörper)
  first  höchster Punkt des Dachs, traufe  tiefster Punkt des Dachs
  boden  Geländehöhe am Gebäude (m NHN), fkt  Gebäudefunktion (ALKIS-Schlüssel), dach  Dachform (Schlüssel)
MapLibre kann Baukörper nur als senkrechte Prismen zeichnen; die Dachformen des LoD2 (Sattel-, Walmdach …)
gehen dabei in der mittleren Dachhöhe auf.

Nutzung (aus dem Projektordner):
  python tools/gebaeude_laden.py

Benötigt: rasterio (für die Umrechnung UTM 33N -> WGS84).
"""
import io, json, os, re, subprocess, sys, zipfile
import xml.etree.ElementTree as ET

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = "https://www.geodaten-mv.de/dienste/gebaeude_atom"
E0, N0, E1, N1 = 348200, 5906700, 352000, 5910500           # Atlasgebiet (EPSG:25833)
KACHEL_KM = 2
NS = {"b": "http://www.opengis.net/citygml/building/1.0", "g": "http://www.opengis.net/gml"}
B, G = "{%s}" % NS["b"], "{%s}" % NS["g"]


def curl(url, timeout=180):
    r = subprocess.run(["curl", "-sL", "-m", str(timeout), "-A", "luftbildatlas-daten/1.0", url], capture_output=True)
    if r.returncode != 0:
        raise OSError(f"curl {r.returncode}: {url}")
    return r.stdout


def kachel_urls():
    """Adressen der Kacheln aus dem ATOM-Feed lesen (die Dataset-ID kann sich ändern)."""
    dienst = curl(FEED).decode("utf-8", "replace")
    m = re.search(r'href="([^"]*type=dataset[^"]*)"', dienst)
    if not m:
        sys.exit("Im ATOM-Feed wurde kein Datensatz gefunden.")
    feed = curl(m.group(1).replace("&amp;", "&")).decode("utf-8", "replace")
    urls = {}
    for u in re.findall(r'href="([^"]*gebaeude_download[^"]*)"', feed):
        u = u.replace("&amp;", "&")
        k = re.search(r"lod2_33_(\d+)_(\d+)_", u)
        if k:
            urls[(int(k.group(1)), int(k.group(2)))] = u
    return urls


def ringe(polygon):
    """gml:Polygon -> [aussen, loch, ...], je Ring eine Liste (x, y, z)"""
    out = []
    for art in ("exterior", "interior"):
        for r in polygon.findall(f"{G}{art}/{G}LinearRing"):
            pl = r.find(f"{G}posList")
            z = [float(v) for v in (pl.text or "").split()] if pl is not None else []
            if not z:
                z = [float(v) for p in r.findall(f"{G}pos") for v in p.text.split()]
            out.append([(z[i], z[i + 1], z[i + 2]) for i in range(0, len(z) - 2, 3)])
    return out


def gebaeude(xml):
    """CityGML -> Liste von Gebäuden (Gebäudeteile erben Funktion und Dachform ihres Gebäudes)."""
    wurzel = ET.fromstring(xml)
    out = []
    for haus in wurzel.iter(f"{B}Building"):
        teile = [haus] + list(haus.iter(f"{B}BuildingPart"))
        for t in teile:
            boden, dach_z = [], []
            for fl in t.findall(f"{B}boundedBy/*"):
                polys = list(fl.iter(f"{G}Polygon"))
                if fl.tag == f"{B}GroundSurface":
                    boden += [ringe(p) for p in polys]
                elif fl.tag == f"{B}RoofSurface":
                    dach_z += [p[2] for poly in polys for p in ringe(poly)[0]]
            if not boden or not dach_z:
                continue
            z0 = min(p[2] for poly in boden for p in poly[0])
            mh = t.findtext(f"{B}measuredHeight") or haus.findtext(f"{B}measuredHeight")
            first = max(dach_z) - z0
            traufe = max(0.0, min(dach_z) - z0)
            out.append({"id": haus.get(f"{G}id"), "boden": round(z0, 2), "first": round(first, 2), "traufe": round(traufe, 2),
                        "h": round(max(2.0, (first + traufe) / 2), 2), "gemessen": float(mh) if mh else None,
                        "fkt": t.findtext(f"{B}function") or haus.findtext(f"{B}function") or "",
                        "dach": t.findtext(f"{B}roofType") or haus.findtext(f"{B}roofType") or "",
                        "flaechen": boden})
    return out


def main():
    from rasterio.warp import transform
    urls = kachel_urls()
    alle = []
    for e in range(E0 // 1000 // KACHEL_KM * KACHEL_KM, -(-E1 // 1000), KACHEL_KM):
        for n in range(N0 // 1000 // KACHEL_KM * KACHEL_KM, -(-N1 // 1000), KACHEL_KM):
            u = urls.get((e, n))
            if not u:
                print(f"  Kachel {e}_{n}: nicht im Feed (keine Gebäude)")
                continue
            roh = curl(u)
            try:
                z = zipfile.ZipFile(io.BytesIO(roh))
                neu = gebaeude(z.read(z.infolist()[0]))
            except (zipfile.BadZipFile, ET.ParseError) as err:
                print(f"  Kachel {e}_{n}: nicht lesbar ({err})")
                continue
            alle += neu
            print(f"  Kachel {e}_{n}: {len(neu)} Gebäude", flush=True)

    features = []
    for g in alle:
        if not any(E0 <= p[0] <= E1 and N0 <= p[1] <= N1 for poly in g["flaechen"] for p in poly[0]):
            continue
        for poly in g["flaechen"]:
            koord = []
            for ring in poly:
                lon, lat = transform("EPSG:25833", "EPSG:4326", [p[0] for p in ring], [p[1] for p in ring])
                koord.append([[round(x, 7), round(y, 7)] for x, y in zip(lon, lat)])
            features.append({"type": "Feature",
                             "properties": {k: g[k] for k in ("id", "h", "first", "traufe", "boden", "fkt", "dach")},
                             "geometry": {"type": "Polygon", "coordinates": koord}})
    ziel = os.path.join(WURZEL, "gebaeude")
    os.makedirs(ziel, exist_ok=True)
    pfad = os.path.join(ziel, "lod2.geojson")
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "quelle": "© GeoBasis-DE/M-V, 3D-Gebäudemodell LoD2, CC BY 4.0",
                   "features": features}, f, ensure_ascii=False, separators=(",", ":"))
    hs = sorted(x["properties"]["first"] for x in features)
    print(f"Fertig: {len(features)} Gebäude im Atlasgebiet -> {pfad} ({os.path.getsize(pfad) // 1024} kB)"
          + (f", Firsthöhe bis {hs[-1]:.1f} m, Median {hs[len(hs) // 2]:.1f} m" if hs else ""))


if __name__ == "__main__":
    main()
