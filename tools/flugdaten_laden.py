"""Flugspuren eines vergangenen Tages aus dem offenen Archiv von adsb.lol holen.

Das Archiv (github.com/adsblol/globe_history_<Jahr>) enthält je Tag alle Flugzeuge der Welt, je Flugzeug
eine Datei – rund 3,4 GB. Es gibt keinen Gebietsausschnitt. Darum wird das Archiv im Durchlauf gelesen:
jede Spur wird gegen den Umkreis des Flugplatzes geprüft, gespeichert werden nur die Treffer
(flugdaten/<Datum>.json, wenige kB). Auf der Platte landet nichts vom Archiv, übertragen wird es ganz.

Nutzung (aus dem Projektordner):
  python tools/flugdaten_laden.py 2026-11-07
  python tools/flugdaten_laden.py 2026-08-01 2026-08-02      # mehrere Tage nacheinander
  python tools/flugdaten_laden.py 2026-11-07 --radius 5      # Umkreis in km (Standard 5)
  python tools/flugdaten_laden.py 2026-11-07 --neu           # vorhandenen Tag erneut holen

Lizenz der Daten: ODbL 1.0, Quelle adsb.lol.
"""
import gzip, json, subprocess, sys, tarfile, time

import flugspur as fs


def anlagen(datum):
    jahr, tag = datum[:4], "v" + datum.replace("-", ".") + "-planes-readsb-prod-0"
    roh = fs.curl(f"https://api.github.com/repos/adsblol/globe_history_{jahr}/releases/tags/{tag}")
    d = json.loads(roh)
    teile = sorted((a["name"], a["browser_download_url"], a["size"]) for a in d.get("assets", []) if ".tar" in a["name"])
    if not teile:
        sys.exit(f"Kein Archiv für {datum} gefunden ({d.get('message', 'keine Dateien')}).")
    return teile


def main():
    daten = [a for a in sys.argv[1:] if not a.startswith("--") and len(a) == 10 and a[4] == "-"]
    if not daten:
        sys.exit(__doc__)
    radius = float(sys.argv[sys.argv.index("--radius") + 1]) if "--radius" in sys.argv else fs.RADIUS_KM
    for datum in daten:
        if fs.lies_tag(datum) and "--neu" not in sys.argv:
            print(f"{datum}: schon vorhanden, übersprungen (--neu holt den Tag erneut)", flush=True)
            continue
        try:
            tag(datum, radius)
        except SystemExit as e:            # ein fehlender Tag soll die übrigen nicht aufhalten
            print(f"{datum}: {e}", flush=True)


def tag(datum, radius):
    teile = anlagen(datum)
    gesamt = sum(t[2] for t in teile)
    print(f"{datum}: {len(teile)} Teil(e), {gesamt / 2**30:.2f} GiB werden durchgesehen (nicht gespeichert)", flush=True)

    # Grobfilter auf dem Text: Breitengrad im Umkreis beginnt mit einer dieser Ziffernfolgen
    dlat = radius / 111.19
    praefixe = {f"{x / 10:.1f}".encode() for x in range(int((fs.MITTE_LAT - dlat) * 10), int((fs.MITTE_LAT + dlat) * 10) + 1)}

    # curl hängt die Teile in der richtigen Reihenfolge aneinander; tarfile liest den Strom
    p = subprocess.Popen(["curl", "-sL", "--retry", "5", "--retry-delay", "5", "-A", fs.KOPF, *[t[1] for t in teile]],
                         stdout=subprocess.PIPE, bufsize=1 << 20)
    fluege, n, treffer, t0 = [], 0, 0, time.time()
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|") as tar:
            for m in tar:
                if not (m.isfile() and "trace_full_" in m.name):
                    continue
                n += 1
                roh = tar.extractfile(m).read()
                try:
                    roh = gzip.decompress(roh)
                except OSError:
                    pass
                if n % 20000 == 0:
                    print(f"  {n} Flugzeuge geprüft, {treffer} im Umkreis, {time.time() - t0:.0f} s", flush=True)
                if not any(b"," + pr in roh for pr in praefixe):
                    continue
                try:
                    d = json.loads(roh)
                except ValueError:
                    continue
                punkte = fs.aus_trace(d, radius)
                if len(punkte) < 2:
                    continue
                stamm = {"hex": d.get("icao", ""), "reg": d.get("r", ""), "typ": d.get("t", ""),
                         "art": d.get("desc", ""), "ruf": "", "herkunft": "archiv"}
                neu = fs.in_fluege(punkte, stamm)
                if neu:
                    treffer += 1
                    fluege += neu
                    print(f"  + {stamm['hex']} {stamm['reg'] or '-':8} {stamm['typ'] or '-':5} {len(neu)} Flug/Flüge", flush=True)
    finally:
        p.stdout.close()
        p.wait()
    if p.returncode not in (0, None) and n == 0:
        sys.exit(f"Download fehlgeschlagen (curl {p.returncode}).")
    pfad = fs.schreibe_tag(datum, fluege, radius)
    print(f"Fertig: {n} Flugzeuge geprüft, {treffer} im Umkreis von {radius:g} km, {len(fluege)} Flüge -> {pfad}"
          f" ({time.time() - t0:.0f} s)")
    if p.returncode:
        print(f"Achtung: curl endete mit Code {p.returncode} – das Archiv wurde möglicherweise nicht vollständig gelesen.")


if __name__ == "__main__":
    main()
