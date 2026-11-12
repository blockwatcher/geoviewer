"""Lädt die lokalen Daten für den MapLibre-Luftbildatlas Lärz.

1. Digitales Geländemodell (echte Höhenwerte, nicht nur Schummerung) vom WCS des LAiV M-V:
     - DGM1  (1 m Raster)  für das Atlasgebiet  E 348200–352000, N 5906700–5910500 (EPSG:25833)
     - DGM25 (25 m Raster) für das Umland, damit das 3D-Gelände am Gebietsrand nicht abbricht
   Ausgabe: dgm/fein_<Spalte>_<Zeile>.png, dgm/umland.png, dgm/dgm.json
   Kodierung der PNGs: Höhe = (R*256 + G) / 100 - 100  [m], also Zentimeter in 16 Bit.
   Die App rechnet daraus im Browser Terrarium-Kacheln für MapLibre (raster-dem).

2. Historische Luftbilder (Mosaike 1944/45, NCAP-Frames) und Icons aus dem Original-Atlas
   https://soenkeba.github.io/Luftbildatlas_Laerz_oeffentlich/  ->  bilder/, airparkinternal/

Nutzung (aus dem Projektordner):
  python tools/daten_laden.py            # alles
  python tools/daten_laden.py --dgm      # nur Geländemodell
  python tools/daten_laden.py --bilder   # nur Bilder

Benötigt: numpy, rasterio, scipy, Pillow.
Quelle DGM: © GeoBasis-DE/M-V (CC BY 4.0).
"""
import io, json, os, sys, urllib.request

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WCS = "https://www.geodaten-mv.de/dienste/dgm_wcs"
ORIGINAL = "https://soenkeba.github.io/Luftbildatlas_Laerz_oeffentlich/app/"

# Atlasgebiet (wie im Original) und Umland
E0, N0, E1, N1 = 348200, 5906700, 352000, 5910500
UM_E0, UM_N0, UM_E1, UM_N1 = 326000, 5886000, 374000, 5932000   # Süden: Landesgrenze = Ende der Daten
KACHELN = 4                 # DGM1 in 4 x 4 Teilen, die App lädt nur die sichtbaren
OFFSET_M, SKALA = 100.0, 100.0


def hole(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "luftbildatlas-daten/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def wcs(coverage, e0, n0, e1, n1):
    import rasterio
    url = (f"{WCS}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID={coverage}"
           f"&FORMAT=image/tiff&SUBSET=x({e0},{e1})&SUBSET=y({n0},{n1})")
    roh = hole(url)
    if roh[:2] not in (b"II", b"MM"):
        sys.exit(f"WCS-Fehler ({coverage}): {roh[:300]!r}")
    with rasterio.open(io.BytesIO(roh)) as d:
        return d.read(1).astype("float32")


def kodieren(h):
    import numpy as np
    v = np.clip(np.rint((h + OFFSET_M) * SKALA), 0, 65535).astype("uint16")
    rgb = np.zeros(h.shape + (3,), "uint8")
    rgb[..., 0] = v >> 8
    rgb[..., 1] = v & 255
    return rgb


def speichern(h, pfad):
    from PIL import Image
    Image.fromarray(kodieren(h), "RGB").save(pfad, optimize=True)
    return os.path.getsize(pfad)


def dgm():
    import numpy as np
    from scipy import ndimage
    ziel = os.path.join(WURZEL, "dgm")
    os.makedirs(ziel, exist_ok=True)

    print("DGM1 (1 m) ...", flush=True)
    seite = (E1 - E0) // 2
    fein = np.zeros((N1 - N0, E1 - E0), "float32")
    for sp in range(2):          # vier Abrufe zu je 1900 x 1900 m
        for ze in range(2):
            e, n = E0 + sp * seite, N1 - ze * seite
            teil = wcs("mv_dgm", e, n - seite, e + seite, n)
            if teil.shape != (seite, seite):
                sys.exit(f"Unerwartete Größe {teil.shape}")
            fein[ze * seite:(ze + 1) * seite, sp * seite:(sp + 1) * seite] = teil
            print(f"  Teil {sp},{ze} ok", flush=True)
    px = fein.shape[0] // KACHELN
    rand = np.pad(fein, ((0, 1), (0, 1)), mode="edge")   # 1 px Überlappung für die Interpolation
    gesamt = 0
    for sp in range(KACHELN):
        for ze in range(KACHELN):
            gesamt += speichern(rand[ze * px:(ze + 1) * px + 1, sp * px:(sp + 1) * px + 1],
                                os.path.join(ziel, f"fein_{sp}_{ze}.png"))
    print(f"  {KACHELN * KACHELN} Dateien, {gesamt // 1024} kB, Höhe {fein.min():.1f} … {fein.max():.1f} m")

    print("DGM25 (Umland) ...", flush=True)
    grob = wcs("mv_dgm25", UM_E0, UM_N0, UM_E1, UM_N1)
    leer = grob <= 0.05          # außerhalb von M-V liefert der Dienst 0
    if leer.any() and not leer.all():
        idx = ndimage.distance_transform_edt(leer, return_distances=False, return_indices=True)
        gefuellt = grob[tuple(idx)]
        weich = ndimage.gaussian_filter(gefuellt, 8)
        grob = np.where(leer, weich, grob)           # jenseits der Landesgrenze: weich fortgesetzt
    kb = speichern(grob, os.path.join(ziel, "umland.png")) // 1024
    print(f"  {grob.shape[1]} x {grob.shape[0]} px, {kb} kB, {100 * leer.mean():.0f} % außerhalb M-V ergänzt")

    p = np.percentile(fein, [1, 50, 99])
    meta = {
        "quelle": "© GeoBasis-DE/M-V, DGM1 und DGM25 (WCS dgm_wcs), CC BY 4.0",
        "kodierung": {"offset_m": OFFSET_M, "skala": SKALA},
        "fein": {"e0": E0, "n1": N1, "res": 1, "breite": fein.shape[1], "hoehe": fein.shape[0],
                 "kacheln": KACHELN, "kachel_px": px, "muster": "fein_{sp}_{ze}.png"},
        "umland": {"e0": UM_E0, "n1": UM_N1, "res": 25, "breite": int(grob.shape[1]), "hoehe": int(grob.shape[0]),
                   "datei": "umland.png"},
        "statistik": {"min": float(fein.min()), "max": float(fein.max()),
                      "p1": float(p[0]), "median": float(p[1]), "p99": float(p[2])},
    }
    with open(os.path.join(ziel, "dgm.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print("  dgm/dgm.json geschrieben")


BILDER = ["luftbild_1944-05-10.webp", "luftbild_1944-05-28.webp", "luftbild_1944-08-04.webp",
          "luftbild_1944-09-13.webp", "luftbild_1944-12-24.webp", "luftbild_1945-04-08.webp",
          "luftbild_1945-04-11.webp", "f3054.jpg", "f4071.jpg", "f4053.jpg"]
ICONS = ["icon.svg", "icon-180.png", "icon-512.png"]


def bilder():
    def lade(name, zielordner, quelle=ORIGINAL):
        pfad = os.path.join(WURZEL, zielordner, name)
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        try:
            roh = hole(quelle + name)
        except Exception as e:           # noqa: BLE001
            print(f"  {name}: FEHLER {e}")
            return None
        with open(pfad, "wb") as f:
            f.write(roh)
        print(f"  {zielordner}/{name}  {len(roh) // 1024} kB")
        return roh

    print("Historische Luftbilder ...")
    for b in BILDER:
        lade(b, "bilder")
    print("Icons ...")
    for i in ICONS:
        lade(i, ".")
    print("Airpark intern (verschlüsselt) ...")
    roh = lade("index.json", "airparkinternal", ORIGINAL + "airparkinternal/")
    if roh:
        # Die Dateinamen stehen im verschlüsselten Manifest; die App lädt fehlende Dateien vom Original nach.
        print("  (verschlüsselte Ebenen werden beim Entsperren vom Original-Atlas geladen)")


if __name__ == "__main__":
    nur_dgm, nur_bilder = "--dgm" in sys.argv, "--bilder" in sys.argv
    if not nur_bilder:
        dgm()
    if not nur_dgm:
        bilder()
    print("Fertig.")
