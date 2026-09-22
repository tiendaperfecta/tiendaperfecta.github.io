#!/usr/bin/env python3
"""
axum_zonas.py — Entrada y salida de la zona del dia.

Cada vendedor tiene en Axum una zona por dia de la semana (LUNES, MARTES, ...),
que es el area que le toca recorrer ese dia. Cruzando el poligono de la zona de
hoy con el recorrido GPS del vendedor se obtiene la hora real en que entro y en
que se fue, que es mas fiel que mirar la primera y ultima visita registrada.

`allZoneByVendedor` no devuelve JSON: devuelve un literal con comillas simples
(`[{'id': '271','name':'LUNES','path': 'lat;lng,lat;lng,...'}]`), asi que se
extrae con expresion regular.
"""
import re
import unicodedata
import datetime as dt

_ZONA = re.compile(
    r"\{\s*'id'\s*:\s*'([^']*)'\s*,\s*'name'\s*:\s*'([^']*)'\s*,\s*'path'\s*:\s*'([^']*)'\s*\}")

DIAS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t))
                   if unicodedata.category(c) != "Mn").upper().strip()


def dia_de(fecha):
    """'2026-09-22' -> 'MARTES'"""
    return DIAS[dt.date.fromisoformat(fecha).weekday()]


def parsear(texto):
    """Lista de zonas: [{id, nombre, poligono:[(lat,lng), ...]}]"""
    zonas = []
    for zid, nombre, path in _ZONA.findall(str(texto)):
        puntos = []
        for par in path.split(","):
            if ";" not in par:
                continue
            a, _, b = par.partition(";")
            try:
                puntos.append((float(a), float(b)))
            except ValueError:
                continue
        if len(puntos) >= 3:
            zonas.append({"id": zid, "nombre": _sin_acentos(nombre), "poligono": puntos})
    return zonas


def dentro(punto, poligono):
    """Ray casting: cuenta cruces de una semirrecta contra los lados."""
    x, y = punto
    adentro = False
    n = len(poligono)
    for i in range(n):
        x1, y1 = poligono[i]
        x2, y2 = poligono[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            corte = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < corte:
                adentro = not adentro
    return adentro


def parsear_recorrido(filas, hora):
    """'lat,lng,idVendedor,9/22/2026 8:08:04 AM,' -> [(momento, lat, lng)]"""
    out = []
    for f in filas:
        p = str(f).split(",")
        if len(p) < 4:
            continue
        try:
            lat, lng = float(p[0]), float(p[1])
        except ValueError:
            continue
        momento = hora(p[3])
        if momento:
            out.append((momento, lat, lng))
    out.sort(key=lambda x: x[0])
    return out


def jornada(gps, sid, fecha, hora):
    """(entrada, salida, zonas_usadas, puntos, puntos_dentro) de la zona de hoy.
    Si el vendedor no tiene zona para hoy se usan todas sus zonas."""
    zonas = parsear(gps.zonas(sid))
    hoy = dia_de(fecha)
    del_dia = [z for z in zonas if z["nombre"] == hoy]
    usar = del_dia or zonas
    if not usar:
        return None, None, [], 0, 0

    track = parsear_recorrido(gps.recorrido(sid, fecha), hora)
    entrada = salida = None
    adentro = 0
    for momento, lat, lng in track:
        if any(dentro((lat, lng), z["poligono"]) for z in usar):
            adentro += 1
            if entrada is None:
                entrada = momento
            salida = momento
    return entrada, salida, [z["nombre"] for z in usar], len(track), adentro
