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

from pathlib import Path

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
DILATACION = 1          # manzana de halo, para que el borde caiga en la calle
CIERRE = 8              # une manzanas separadas por menos de ~1,6 km, para que
                        # la zona quede como un solo perimetro y no como islas


def _proyectar(puntos):
    """(lat,lng) -> metros planos, con el centro del grupo como origen."""
    lat0 = _mediana([p[0] for p in puntos])
    mlat = 110540.0
    mlng = 111320.0 * math.cos(math.radians(lat0))
    return mlat, mlng


def _vecinas(celda):
    i, j = celda
    return [(i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)
            if (di, dj) != (0, 0)]


def _grupos(celdas, alcance):
    """Separa las manzanas en grupos: dos manzanas son del mismo grupo si estan
    a menos de `alcance` celdas. Se trabaja grupo por grupo porque una ruta
    puede ir de Mar del Plata a Sierra de los Padres, y barrer el rectangulo
    que las contiene a las dos seria recorrer 50 km de campo vacio."""
    pendientes, grupos = set(celdas), []
    while pendientes:
        semilla = pendientes.pop()
        grupo, pila = {semilla}, [semilla]
        while pila:
            i, j = pila.pop()
            cerca = [c for c in pendientes
                     if abs(c[0] - i) <= alcance and abs(c[1] - j) <= alcance]
            for c in cerca:
                pendientes.discard(c)
                grupo.add(c)
                pila.append(c)
        grupos.append(grupo)
    return grupos


def _dilatar(celdas, r):
    """Dilatacion de Chebyshev: cada manzana se expande a un cuadrado de radio r."""
    if r <= 0:
        return set(celdas)
    out = set()
    for i, j in celdas:
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                out.add((i + di, j + dj))
    return out


def _erosionar(celdas, r):
    """Contraccion: sobrevive la manzana que tiene todo su cuadrado adentro."""
    if r <= 0:
        return set(celdas)
    out = set()
    for i, j in celdas:
        if all((i + di, j + dj) in celdas
               for di in range(-r, r + 1) for dj in range(-r, r + 1)):
            out.add((i, j))
    return out


def _rellenar_huecos(celdas):
    """Tapa los patios internos: la zona es un territorio macizo, no un queso
    gruyere. Se inunda desde afuera; lo vacio que no se moja, es hueco."""
    if not celdas:
        return celdas
    xs = [c[0] for c in celdas]
    ys = [c[1] for c in celdas]
    x0, x1 = min(xs) - 1, max(xs) + 1
    y0, y1 = min(ys) - 1, max(ys) + 1
    afuera, pila = set(), [(x0, y0)]
    while pila:
        c = pila.pop()
        if c in afuera or c in celdas:
            continue
        i, j = c
        if not (x0 <= i <= x1 and y0 <= j <= y1):
            continue
        afuera.add(c)
        pila.extend([(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)])
    return {(i, j) for i in range(x0, x1 + 1) for j in range(y0, y1 + 1)
            if (i, j) not in afuera}


def _celdas(puntos, mlat, mlng):
    """Manzanas de la zona, grupo por grupo: las que tienen clientes, cerradas
    entre si y con los patios tapados, para que cada area quede con un solo
    perimetro continuo."""
    base = set()
    for lat, lng in puntos:
        base.add((int(math.floor(lng * mlng / CELDA_M)),
                  int(math.floor(lat * mlat / CELDA_M))))
    out = set()
    for grupo in _grupos(base, 2 * CIERRE):
        cerrado = _erosionar(_dilatar(grupo, CIERRE), CIERRE) | grupo
        out |= _rellenar_huecos(_dilatar(cerrado, DILATACION))
    return out


def _anillos(lados):
    """Hilvana lados sueltos en anillos cerrados. `lados` son pares de vertices
    (cualquier cosa hasheable: celdas de la grilla o indices de nodo)."""
    vecinos = {}
    for a, b in lados:
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
                if v == previo or (actual, v) in usados or (v, actual) in usados:
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
        if len(anillo) >= 3:
            anillos.append(anillo)
    return anillos


def _lados_sueltos(figuras):
    """Lados que pertenecen a una sola figura: el contorno de la union. Los que
    comparten dos figuras vecinas se cancelan."""
    cuenta = {}
    for anillo in figuras:
        n = len(anillo)
        for i in range(n):
            a, b = anillo[i], anillo[(i + 1) % n]
            if a == b:
                continue
            clave = (a, b) if a < b else (b, a)
            cuenta[clave] = cuenta.get(clave, 0) + 1
    return [k for k, v in cuenta.items() if v == 1]


def _contorno(celdas):
    """Anillos del borde de la union de celdas de la grilla."""
    cuadros = [[(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)] for i, j in celdas]
    return _anillos(_lados_sueltos(cuadros))


def _simplificar(anillo):
    """Saca los vertices que estan en linea recta entre sus vecinos."""
    out = []
    n = len(anillo)
    for k in range(n):
        a, b, c = anillo[k - 1], anillo[k], anillo[(k + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0]):
            out.append(b)
    return out or anillo


_MANZANAS = None


def manzanas():
    """Manzanas reales de OSM (las arma tools/construir_manzanas.py). Si el
    archivo no esta, se cae a la grilla de 100 m."""
    global _MANZANAS
    if _MANZANAS is None:
        f = Path(__file__).with_name("manzanas.json")
        try:
            _MANZANAS = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            _MANZANAS = {}
        if _MANZANAS:
            # indice por celda de 100 m, para encontrar rapido las manzanas de
            # una zona sin recorrer las 20.000
            idx = {}
            for k, (lat, lng) in enumerate(_MANZANAS["centros"]):
                idx.setdefault((round(lat, 3), round(lng, 3)), []).append(k)
            _MANZANAS["indice"] = idx
    return _MANZANAS


def zona_por_manzanas(puntos):
    """Anillos que siguen la linea de las calles: se toma el territorio cerrado
    (la grilla) y se lo reemplaza por las manzanas de verdad cuyo centro cae
    adentro. El contorno de esa union son las calles del borde."""
    datos = manzanas()
    if not datos:
        return None
    mlat, mlng = _proyectar(puntos)
    celdas = _celdas(puntos, mlat, mlng)
    if not celdas:
        return None

    # candidatas: manzanas cuyo centro esta en el area de la zona
    lats = [j * CELDA_M / mlat for _, j in celdas]
    lngs = [i * CELDA_M / mlng for i, _ in celdas]
    elegidas = []
    vistas = set()
    for clave, ks in datos["indice"].items():
        if not (min(lats) - 0.002 <= clave[0] <= max(lats) + 0.002 and
                min(lngs) - 0.002 <= clave[1] <= max(lngs) + 0.002):
            continue
        for k in ks:
            if k in vistas:
                continue
            lat, lng = datos["centros"][k]
            celda = (int(math.floor(lng * mlng / CELDA_M)),
                     int(math.floor(lat * mlat / CELDA_M)))
            if celda in celdas:
                vistas.add(k)
                elegidas.append(datos["bloques"][k])
    if not elegidas:
        return None

    nodos = datos["nodos"]
    anillos = []
    for anillo in _anillos(_lados_sueltos(elegidas)):
        anillos.append([nodos[i] for i in anillo])
    anillos.sort(key=len, reverse=True)
    return anillos or None


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
        # Primero se intenta con las manzanas reales de OSM; si no estan, con la
        # grilla de 100 m.
        anillos = zona_por_manzanas(limpio) or zona_por_cuadras(limpio)
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
