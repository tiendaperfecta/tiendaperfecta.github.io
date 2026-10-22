#!/usr/bin/env python3
"""
pepsico_frescura.py — pepsico/frescura_data.json.

Fuente: frescura.tienda-perfecta.workers.dev, un dashboard propio (no GesCom)
que ya calcula la cuota semanal de productos por vencer y lo vendido desde el
21/09 por vendedor. Es un endpoint publico sin login: no necesita credenciales.

El HTML de esa pagina trae los datos embebidos en:
    <script id="frescura-data" type="application/json">{...}</script>

Probado (24/09/2026): responde 200, ~76 KB, con ese script tag presente.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = "https://frescura.tienda-perfecta.workers.dev/"
OUT = Path(__file__).resolve().parent.parent / "pepsico" / "frescura_data.json"
OVERRIDE = Path(__file__).resolve().parent.parent / "pepsico" / "override_frescura_vendido.json"
TZ_AR = timezone(timedelta(hours=-3))


def aplicar_override(data):
    """Parche mientras el tablero de Frescura no se actualiza: 'vendido' recalculado con el export de Gescom.

    Si existe pepsico/override_frescura_vendido.json, es de hoy y de la misma semana (misma foto, campo "tomada"),
    se usa para cada articulo cuyo total sea mayor o igual al del tablero. Apenas el tablero trae igual o mas,
    se deja el suyo. La cuota no se toca."""
    try:
        ov = json.loads(OVERRIDE.read_text(encoding="utf-8"))
    except Exception:
        return
    hoy = datetime.now(TZ_AR).date().isoformat()
    if ov.get("tomada") != data.get("tomada") or ov.get("fecha") != hoy:
        return
    por = ov.get("articulos") or {}
    usados = 0
    for a in data["articulos"]:
        o = por.get(str(a.get("codigo")))
        if not o or o.get("total", 0) < (a.get("vendidoCodigo") or 0):
            continue
        a["vendidoCodigo"] = o["total"]
        for v in a.get("porVendedor") or []:
            vend = (o.get("vendedores") or {}).get(str(v.get("codigo")), 0)
            cuota = v.get("cuota") or 0
            v["vendido"] = vend
            v["pct"] = min(vend / cuota, 1) if cuota else 0
            v["falta"] = max(cuota - vend, 0)
        usados += 1
    if usados:
        data["vendidoHasta"] = ov.get("extraido")
        print("AVISO: vendido de %d articulos tomado de Gescom (extraido %s)" % (usados, ov.get("extraido")))


def main():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; pepsico-panel-bot/1.0)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8")
    except Exception as e:
        print("Error bajando frescura:", type(e).__name__, e)
        return 1

    m = re.search(r'<script id="frescura-data" type="application/json">(\{.*?\})</script>', html, re.S)
    if not m:
        print("No se encontro el script tag frescura-data en la pagina.")
        return 1

    crudo = json.loads(m.group(1))
    # El worker trae varios grupos (uno por proveedor); el panel solo usa Pepsico.
    grupos = crudo.get("grupos") or []
    pepsico = next((g for g in grupos if g.get("id") == "pepsico"), None)
    if pepsico is None:
        print("No hay grupo 'pepsico' en la respuesta. Grupos disponibles:",
              [g.get("id") for g in grupos])
        return 1

    data = {"tomada": pepsico.get("tomada"), "articulos": pepsico.get("articulos") or []}
    aplicar_override(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Guardado %s: %d articulos" % (OUT, len(data["articulos"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
