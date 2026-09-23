#!/usr/bin/env python3
"""
gescom.py — Maestro de clientes, rutas de preventa y zonas dibujadas.

La geolocalizacion de Axum no es confiable. GesCom, en cambio, tiene el 100% de
los clientes con coordenadas, y ademas la ruta de preventa de cada uno: que
vendedor lo visita y que dias. Con eso se arma:

    clientes.json  maestro liviano: nombre, coordenada, localidad
    rutas.json     a quien le toca visitar cada vendedor, por dia de la semana
    zonas.json     la zona de cada vendedor por dia, dibujada sobre las
                   manzanas donde estan sus clientes: el borde cae por la calle,
                   no por la puerta de los comercios

Credenciales por variables de entorno (GitHub Secrets), nunca en el repo:

    GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD

Si no estan cargadas, el refresco se saltea y quedan los archivos de la ultima
corrida buena (estan commiteados).
"""
import os
import json
import math

import requests

AUTH = "https://auth.gescom.online"
API = "https://tiendaperfecta.gescom.online"
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def hay_credenciales():
    return all(os.environ.get(k, "").strip() for k in
               ("GESCOM_REALM", "GESCOM_CLIENT_ID", "GESCOM_USERNAME", "GESCOM_PASSWORD"))


def _token(sesion):
    r = sesion.post(
        "%s/realms/%s/protocol/openid-connect/token" % (AUTH, os.environ["GESCOM_REALM"]),
        data={"grant_type": "password",
              "client_id": os.environ["GESCOM_CLIENT_ID"],
              "username": os.environ["GESCOM_USERNAME"],
              "password": os.environ["GESCOM_PASSWORD"]},
        timeout=40)
    r.raise_for_status()
    return r.json()["access_token"]


def bajar_clientes():
    """Lista cruda de clientes de GesCom."""
    s = requests.Session()
    tok = _token(s)
    r = s.get(API + "/data/cmd/ventas/api/v1/get-clientes",
              headers={"Authorization": "Bearer " + tok, "Accept": "application/json"},
              timeout=180)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else (d.get("data") or [])


# --------------------------------------------------------------------------- #
# Geometria
# --------------------------------------------------------------------------- #
# Una cuadra de Mar del Plata mide ~100 m. La zona se arma con celdas de ese
# tamano: el borde cae entonces por la calle, entre manzanas, y no por la puerta
# de los clientes como pasaba con la envolvente.
CELDA_M = 100
DILATACION = 1          # celdas de halo, para unir clientes de manzanas vecinas


def _proyectar(puntos):
    """(lat,lng) -> metros planos, con el centro del grupo como origen."""
    lat0 = _mediana([p[0] for p in puntos])
    mlat = 110540.0
    mlng = 111320.0 * math.cos(math.radians(lat0))
    return mlat, mlng


def _celdas(puntos, mlat, mlng):
    """Celdas ocupadas, mas su halo."""
    base = set()
    for lat, lng in puntos:
        base.add((int(math.floor(lng * mlng / CELDA_M)),
                  int(math.floor(lat * mlat / CELDA_M))))
    if DILATACION <= 0:
        return base
    out = set(base)
    for _ in range(DILATACION):
        nuevo = set()
        for i, j in out:
            nuevo.update({(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)})
        out |= nuevo
    return out


def _contorno(celdas):
    """Anillos del borde de la union de celdas.

    Cada celda aporta sus cuatro lados; los lados que comparten dos celdas
    vecinas se cancelan, y los que quedan son exactamente el contorno. Despues
    se hilvanan en anillos cerrados."""
    lados = {}
    for i, j in celdas:
        for a, b in (((i, j), (i + 1, j)), ((i + 1, j), (i + 1, j + 1)),
                     ((i + 1, j + 1), (i, j + 1)), ((i, j + 1), (i, j))):
            clave = (a, b) if a < b else (b, a)
            lados[clave] = lados.get(clave, 0) + 1
    borde = [k for k, n in lados.items() if n == 1]

    vecinos = {}
    for a, b in borde:
        vecinos.setdefault(a, []).append(b)
        vecinos.setdefault(b, []).append(a)

    usados, anillos = set(), []
    for arranque in list(vecinos):
        if all((arranque, v) in usados or (v, arranque) in usados
               for v in vecinos[arranque]):
            continue
        anillo, actual, previo = [arranque], arranque, None
        while True:
            siguiente = None
            for v in vecinos[actual]:
                if v == previo:
                    continue
                if (actual, v) in usados or (v, actual) in usados:
                    continue
                siguiente = v
                break
            if siguiente is None:
                break
            usados.add((actual, siguiente))
            previo, actual = actual, siguiente
            if actual == arranque:
                break
            anillo.append(actual)
        if len(anillo) >= 4:
            anillos.append(anillo)
    return anillos


def _simplificar(anillo):
    """Saca los vertices que estan en linea recta entre sus vecinos."""
    out = []
    n = len(anillo)
    for k in range(n):
        a, b, c = anillo[k - 1], anillo[k], anillo[(k + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0]):
            out.append(b)
    return out or anillo


def zona_por_cuadras(puntos):
    """Anillos en (lat,lng) que cubren las manzanas donde estan los clientes."""
    if len(puntos) < 2:
        return []
    mlat, mlng = _proyectar(puntos)
    anillos = []
    for anillo in _contorno(_celdas(puntos, mlat, mlng)):
        simple = _simplificar(anillo)
        anillos.append([[round(j * CELDA_M / mlat, 6), round(i * CELDA_M / mlng, 6)]
                        for i, j in simple])
    anillos.sort(key=len, reverse=True)
    return anillos


def _mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def sin_outliers(puntos, factor=4.0):
    """Saca los clientes sueltos a kilometros del grupo, que estirarian la zona
    hasta volverla inutil (suelen ser cargas mal geolocalizadas)."""
    if len(puntos) < 5:
        return puntos
    clat, clng = _mediana([p[0] for p in puntos]), _mediana([p[1] for p in puntos])
    dist = [math.hypot(p[0] - clat, p[1] - clng) for p in puntos]
    corte = _mediana(dist) * factor or 1e-9
    limpio = [p for p, d in zip(puntos, dist) if d <= corte]
    return limpio if len(limpio) >= 3 else puntos


# --------------------------------------------------------------------------- #
# Armado
# --------------------------------------------------------------------------- #
def construir(crudo):
    """(clientes, rutas, zonas) listos para publicar."""
    clientes, rutas = {}, {d: {} for d in DIAS}
    puntos_por_zona = {}

    for c in crudo:
        cod = str(c.get("codigo") or "")
        coord = c.get("coordenadas") or {}
        lat, lng = coord.get("latitud"), coord.get("longitud")
        if not cod:
            continue
        nombre = (c.get("nombre") or c.get("razonSocial") or "").strip()
        clientes[cod] = [nombre, lat, lng, (c.get("localidad") or "").strip(),
                         (c.get("codigoRamo") or "").strip(),
                         (c.get("codigoSegmento") or "").strip()]

        for r in (c.get("rutasPreventa") or []):
            vend = str(r.get("codigoVendedor") or "")
            if not vend:
                continue
            for dia in DIAS:
                if not r.get(dia):
                    continue
                rutas[dia].setdefault(vend, []).append(
                    {"cliente": cod, "orden": r.get("orden") or 0})
                if lat is not None and lng is not None:
                    puntos_por_zona.setdefault((vend, dia), []).append((lat, lng))

    for dia in DIAS:
        for vend in rutas[dia]:
            rutas[dia][vend].sort(key=lambda x: x["orden"])

    zonas = {}
    for (vend, dia), pts in puntos_por_zona.items():
        limpio = sin_outliers(pts)
        anillos = zona_por_cuadras(limpio)
        if anillos:
            zonas.setdefault(vend, {})[dia] = {
                "poligonos": anillos,
                "clientes": len(pts),
                "descartados": len(pts) - len(limpio),
                "manzanas": len(_celdas(limpio, *_proyectar(limpio))),
            }
    return clientes, rutas, zonas


def refrescar(escribir, meta):
    """Baja GesCom y publica los tres archivos. Devuelve True si actualizo."""
    if not hay_credenciales():
        meta["gescom"] = "sin credenciales: se usan los archivos ya publicados"
        return False
    try:
        crudo = bajar_clientes()
        clientes, rutas, zonas = construir(crudo)
    except Exception as e:
        meta.setdefault("errors", []).append("gescom: %s: %s" % (type(e).__name__, e))
        return False
    escribir("clientes.json",
             {"campos": ["nombre", "lat", "lng", "localidad", "ramo", "segmento"],
              "clientes": clientes})
    escribir("rutas.json", rutas)
    escribir("zonas.json", zonas)
    meta["gescom"] = {"clientes": len(clientes),
                      "zonas": sum(len(v) for v in zonas.values())}
    return True
