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
from pathlib import Path

URL = "https://frescura.tienda-perfecta.workers.dev/"
OUT = Path(__file__).resolve().parent.parent / "pepsico" / "frescura_data.json"


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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Guardado %s: %d articulos" % (OUT, len(data["articulos"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
