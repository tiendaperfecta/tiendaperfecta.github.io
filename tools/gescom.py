#!/usr/bin/env python3
"""
gescom.py — Maestro de clientes, rutas de preventa y zonas dibujadas.

La geolocalizacion de Axum no es confiable. GesCom, en cambio, tiene el 100% de
los clientes con coordenadas, y ademas la ruta de preventa de cada uno: que
vendedor lo visita y que dias. Con eso se arma:

    clientes.json  maestro liviano: nombre, coordenada, localidad
    rutas.json     a quien le toca visitar cada vendedor, por dia de la semana
    zonas.json     el poligono de cada zona, dibujado con los clientes que la
                   componen (envolvente convexa), en vez de los poligonos
                   cargados a mano en Axum que ya no reflejan la realidad

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
def envolvente(puntos):
    """Envolvente convexa (monotone chain). Devuelve el poligono en orden."""
    p = sorted(set(puntos))
    if len(p) <= 2:
        return p

    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    abajo = []
    for q in p:
        while len(abajo) >= 2 and cruz(abajo[-2], abajo[-1], q) <= 0:
            abajo.pop()
        abajo.append(q)
    arriba = []
    for q in reversed(p):
        while len(arriba) >= 2 and cruz(arriba[-2], arriba[-1], q) <= 0:
            arriba.pop()
        arriba.append(q)
    return abajo[:-1] + arriba[:-1]


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
        poly = envolvente(limpio)
        if len(poly) >= 3:
            zonas.setdefault(vend, {})[dia] = {
                "poligono": [[round(a, 6), round(b, 6)] for a, b in poly],
                "clientes": len(pts),
                "descartados": len(pts) - len(limpio),
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
