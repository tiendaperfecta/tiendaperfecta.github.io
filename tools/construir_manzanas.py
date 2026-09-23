#!/usr/bin/env python3
"""
construir_manzanas.py — Manzanas reales a partir de la traza de calles de OSM.

Las zonas del panel se dibujan sobre manzanas de verdad, para que el limite siga
la linea de las cuadras y no una grilla inventada. Este script arma esas
manzanas una sola vez y las deja en tools/manzanas.json; el refresco horario solo
las lee, asi que no depende de Overpass ni necesita shapely.

    python tools/construir_manzanas.py

Como funciona: baja las calles de OpenStreetMap por mosaicos (con cache en
disco, porque Overpass es lento y a veces contesta 504), las poligoniza —los
anillos cerrados que forman las calles entre si SON las manzanas— y guarda las
que estan cerca de algun cliente.

Las manzanas se guardan como indices a una tabla de nodos: dos manzanas vecinas
comparten exactamente los mismos nodos, y eso permite despues unir varias y
quedarse con el contorno sin necesidad de una libreria de geometria.

Requiere: pip install requests shapely
"""
import json
import math
import time
import sys
from pathlib import Path

import requests
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

RAIZ = Path(__file__).resolve().parents[1]
CLIENTES = RAIZ / "axum" / "data" / "clientes.json"
SALIDA = Path(__file__).with_name("manzanas.json")
CACHE = Path(__file__).with_name("_osm_cache")

ESPEJOS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.osm.jp/api/interpreter"]

# Solo calles de verdad: sin veredas, senderos, escaleras ni calles internas de
# playas de estacionamiento, que parten las manzanas en pedacitos.
TIPOS = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
         "living_street|motorway_link|trunk_link|primary_link|secondary_link|"
         "tertiary_link|road")

MOSAICO = 0.06          # grados de lado por consulta (~6,6 km): mas grande
                        # que esto y Overpass empieza a contestar 504
CERCA_M = 800           # se guardan las manzanas a menos de esto de un cliente


def clientes_validos():
    d = json.loads(CLIENTES.read_text(encoding="utf-8"))
    campos = d["campos"]
    il, ig = campos.index("lat"), campos.index("lng")
    out = []
    descartados = 0
    for f in d["clientes"].values():
        lat, lng = f[il], f[ig]
        if lat is None or lng is None:
            continue
        # GesCom tiene registros con la latitud copiada en la longitud.
        if abs(lat - lng) < 1e-6 or not (-39.5 < lat < -36.5 and -59.5 < lng < -56.0):
            descartados += 1
            continue
        out.append((lat, lng))
    print("clientes con coordenada usable: %d (descartados %d)" % (len(out), descartados))
    return out


def mosaicos(puntos):
    """Solo los cuadros que tienen clientes: el resto del partido es campo y no
    hace falta bajarlo."""
    cuadros = set()
    for lat, lng in puntos:
        cuadros.add((math.floor(lat / MOSAICO), math.floor(lng / MOSAICO)))
    return sorted((i * MOSAICO, j * MOSAICO, (i + 1) * MOSAICO, (j + 1) * MOSAICO)
                  for i, j in cuadros)


def bajar(mosaico):
    s, o, n, e = mosaico
    CACHE.mkdir(exist_ok=True)
    archivo = CACHE / ("%.3f_%.3f_%.3f_%.3f.json" % mosaico)
    if archivo.exists():
        return json.loads(archivo.read_text(encoding="utf-8"))
    consulta = ('[out:json][timeout:180];way["highway"~"^(%s)$"](%.4f,%.4f,%.4f,%.4f);'
                '(._;>;);out skel qt;' % (TIPOS, s, o, n, e))
    for intento in range(6):
        url = ESPEJOS[intento % len(ESPEJOS)]
        try:
            r = requests.post(url, data=consulta.encode("utf-8"),
                              headers={"User-Agent": "panel-axum/1.0 (tiendaperfecta)",
                                       "Content-Type": "text/plain; charset=utf-8"},
                              timeout=240)
            if r.status_code == 200:
                d = r.json()
                archivo.write_text(json.dumps(d), encoding="utf-8")
                return d
            print("    %s -> %s" % (url.split("/")[2], r.status_code))
        except Exception as ex:
            print("    %s -> %s" % (url.split("/")[2], type(ex).__name__))
        time.sleep(10 + intento * 15)
    print("    mosaico sin datos, se sigue")
    return {"elements": []}


def main():
    puntos = clientes_validos()
    trozos = mosaicos(puntos)
    print("mosaicos a bajar: %d" % len(trozos))

    nodos, lineas = {}, []
    for k, m in enumerate(trozos, 1):
        d = bajar(m)
        print("  %d/%d %s" % (k, len(trozos), "ok" if d["elements"] else "vacio"))
        for el in d["elements"]:
            if el["type"] == "node":
                nodos[el["id"]] = (el["lon"], el["lat"])
        for el in d["elements"]:
            if el["type"] != "way":
                continue
            pts = [nodos[i] for i in el.get("nodes", []) if i in nodos]
            if len(pts) >= 2:
                lineas.append(LineString(pts))

    print("poligonizando %d calles..." % len(lineas))
    t0 = time.time()
    manzanas = [m for m in polygonize(unary_union(lineas)) if m.is_valid]
    print("manzanas: %d (%.1fs)" % (len(manzanas), time.time() - t0))

    # Solo las que estan cerca de algun cliente: el resto es campo.
    grados = CERCA_M / 111000.0
    arbol = STRtree([Point(lng, lat) for lat, lng in puntos])
    cerca = []
    for m in manzanas:
        c = m.centroid
        if len(arbol.query(c.buffer(grados))) > 0:
            cerca.append(m)
    print("manzanas cerca de clientes: %d" % len(cerca))

    # Se guardan como indices a una tabla de nodos, con los vertices redondeados,
    # para que dos manzanas vecinas compartan exactamente el mismo borde.
    tabla, indice, bloques, centros = [], {}, [], []
    for m in cerca:
        anillo = []
        for x, y in m.exterior.coords[:-1]:
            clave = (round(y, 7), round(x, 7))
            if clave not in indice:
                indice[clave] = len(tabla)
                tabla.append([clave[0], clave[1]])
            if not anillo or anillo[-1] != indice[clave]:
                anillo.append(indice[clave])
        if len(anillo) >= 3:
            bloques.append(anillo)
            centros.append([round(m.centroid.y, 6), round(m.centroid.x, 6)])

    SALIDA.write_text(json.dumps(
        {"nodos": tabla, "bloques": bloques, "centros": centros},
        separators=(",", ":")), encoding="utf-8")
    print("guardado %s: %d manzanas, %d nodos, %.1f MB" % (
        SALIDA.name, len(bloques), len(tabla), SALIDA.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
