#!/usr/bin/env python3
"""
axum_ldr.py — Datos de LDR (choferes / reparto).

Axum GPS solo expone la posicion de los camiones: todos sus reportes de reparto
(km por camion, eventos, clientes entregados, aceite, gastos) responden vacio o
error. Las estadisticas de los choferes ya existen en el Panel de Fleteros, que
las baja de la API de GesCom y las publica en GitHub Pages con CORS abierto.

Se leen de ahi en vez de duplicar ese robot: una sola fuente de verdad, y si el
panel de fleteros cambia, esto lo sigue solo.
"""
import json

import requests

DATA = "https://tiendaperfecta.github.io/fleteros/data.js"
MESES = "https://tiendaperfecta.github.io/fleteros/historial-meses.json"


def _extraer_array(texto, clave):
    """Saca `clave: [ ... ]` de un .js contando corchetes, porque el archivo no
    es JSON: es una asignacion a window.__TP_DATA__."""
    i = texto.find(clave)
    if i < 0:
        return []
    i = texto.find("[", i)
    if i < 0:
        return []
    nivel, fin, en_texto, escape = 0, None, False, False
    for j in range(i, len(texto)):
        c = texto[j]
        if en_texto:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_texto = False
            continue
        if c == '"':
            en_texto = True
        elif c == "[":
            nivel += 1
        elif c == "]":
            nivel -= 1
            if nivel == 0:
                fin = j + 1
                break
    if fin is None:
        return []
    try:
        return json.loads(texto[i:fin])
    except ValueError:
        return []


def construir(fecha, camiones):
    """Devuelve el dict que se publica como ldr.json."""
    sesion = requests.Session()
    sesion.headers.update({"User-Agent": "panel-axum"})
    out = {
        "date": fecha,
        "camiones": camiones,
        "fuente": "Panel de Fleteros (API GesCom) + posiciones de Axum GPS",
        "nota": "Axum solo expone la posición de los camiones: sus reportes de "
                "reparto, km y eventos por camión responden vacío o error. Las "
                "estadísticas de choferes salen del Panel de Fleteros.",
    }
    try:
        js = sesion.get(DATA, timeout=60)
        js.raise_for_status()
        registros = _extraer_array(js.text, "registros")
    except Exception as e:
        out["error"] = "fleteros: %s" % e
        return out

    if not registros:
        out["error"] = "fleteros: no se pudo leer data.js"
        return out

    # El panel de fleteros se actualiza una vez por dia: se toma el ultimo dia
    # con datos, que puede ser ayer si todavia no corrio el robot de hoy.
    ultimo = max(r.get("fecha", "") for r in registros)
    dia = [r for r in registros if r.get("fecha") == ultimo]

    filas = []
    for r in dia:
        asign = r.get("entregas_asignadas") or 0
        real = r.get("entregas_realizadas") or 0
        cart_a = r.get("cartones_a_retornar") or 0
        cart_r = r.get("cartones_retornados") or 0
        filas.append({
            "fletero": r.get("fletero", ""),
            "zona": r.get("zona", ""),
            "repartos": r.get("repartos") or 0,
            "asignadas": asign,
            "realizadas": real,
            "efectividad": round(real * 100.0 / asign, 1) if asign else None,
            "cartonesARetornar": cart_a,
            "cartonesRetornados": cart_r,
            "efCartones": round(cart_r * 100.0 / cart_a, 1) if cart_a else None,
        })
    filas.sort(key=lambda r: -(r["efectividad"] or 0))

    tot_a = sum(r["asignadas"] for r in filas)
    tot_r = sum(r["realizadas"] for r in filas)
    # Los cartones se cargan uno o dos dias despues, cuando cierran los
    # camiones. Mostrar 0 el mismo dia haria parecer que no volvio ninguno.
    cartones_cargados = sum(r["cartonesARetornar"] for r in filas) > 0
    out["fleteros"] = {
        "fecha": ultimo,
        "esDeHoy": ultimo == fecha,
        "cartonesCargados": cartones_cargados,
        "rows": filas,
        "totales": {
            "choferes": len(filas),
            "repartos": sum(r["repartos"] for r in filas),
            "asignadas": tot_a,
            "realizadas": tot_r,
            "efectividad": round(tot_r * 100.0 / tot_a, 1) if tot_a else None,
        },
    }

    # Acumulado del mes: ranking y efectividad general que ya calcula el panel.
    try:
        meses = sesion.get(MESES, timeout=60).json()
        clave = fecha[:7]
        mes = meses.get(clave) or meses.get(max(meses)) if meses else None
        if mes:
            out["mes"] = {
                "clave": clave if clave in meses else max(meses),
                "efGeneral": mes.get("efGeneral"),
                "cartonGeneral": mes.get("cartonGeneral"),
                "repartos": mes.get("repartos"),
                "entregadas": mes.get("entregadas"),
                "boletas": mes.get("boletas"),
                "actualizado": mes.get("actualizado"),
                "ranking": mes.get("ranking", []),
            }
    except Exception as e:
        out["mesError"] = str(e)[:160]

    return out
