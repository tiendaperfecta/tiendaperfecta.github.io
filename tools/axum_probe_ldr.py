#!/usr/bin/env python3
"""
axum_probe_ldr.py — ¿Dónde guarda Axum lo que cobran los choferes en la app LDR?

Prueba de solo lectura, por única vez (30/9/2026). Los choferes cargan en AXUM 2.0 (módulo
LDR), por cliente, cómo y cuánto les pagaron. Se busca en la web de Axum
(www.axum.com.ar/tiendaperfecta) la pantalla que muestra eso y la dirección de datos que usa.

OJO: el repo es PÚBLICO y el log de GitHub se ve desde afuera. Solo se imprimen nombres de
opciones del menú, comandos, títulos de página y direcciones (URLs) que usa cada pantalla:
NUNCA datos de clientes, importes ni tokens.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axum_fetch import Orders360  # noqa: E402

PANEL = "https://www.axum.com.ar/tiendaperfecta/controllerView.aspx"
BUSCADAS = re.compile(r"ldr|repart|cobr|rendi|recib|valor|pago|liquid|entreg|chofer|camion|logist|caja|tesor", re.I)
ADIVINADAS = ["LDR", "Ldr", "ldr", "LDR_Panel", "PanelLDR", "Reparto", "Repartos", "VerRepartos", "Cobranzas",
              "VerCobranzas", "Cobros", "Recibos", "VerRecibos", "Rendicion", "Rendiciones", "RendicionLDR",
              "Liquidacion", "LiquidacionReparto", "Entregas", "VerEntregas", "Logistica", "Distribucion"]


def sin_token(url):
    return re.sub(r"(token=)[A-Za-z0-9_\-\.]+", r"\1<oculto>", url)


def titulo(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""


def direcciones(html):
    """URLs de datos que usa la pantalla (api, asmx, ashx, aspx/Metodo, json)."""
    urls = set(re.findall(r"""["'](https?://[^"'\s]+|/?[A-Za-z0-9_\-./]*(?:api/|\.asmx|\.ashx|\.svc|WebMethod)[^"'\s]*)["']""", html))
    urls |= set(re.findall(r"""url\s*:\s*["']([^"']+)["']""", html))
    return sorted(sin_token(u) for u in urls if not re.search(r"\.(css|png|jpg|gif|svg|woff|ico)(\?|$)", u))[:40]


def main():
    o = Orders360(os.environ.get("AXUM_ORDERS_USER", ""), os.environ.get("AXUM_ORDERS_PASS", ""))
    s, tok = o.s, o.token
    r = s.get(PANEL, params={"cmd": "Orders360", "token": tok}, timeout=60)
    print("panel:", r.status_code, titulo(r.text), "| bytes", len(r.text))
    # Opciones del menú: todos los cmd= con su texto
    opciones = {}
    for m in re.finditer(r"""<a[^>]+href=["']([^"']*cmd=([A-Za-z0-9_&=\-]+)[^"']*)["'][^>]*>(.*?)</a>""", r.text, re.S | re.I):
        cmd = m.group(2)
        texto = re.sub(r"<[^>]+>|\s+", " ", m.group(3)).strip()
        opciones.setdefault(cmd, texto)
    for m in re.finditer(r"""cmd=([A-Za-z0-9_\-]+)""", r.text):
        opciones.setdefault(m.group(1), "")
    print("opciones de menú encontradas:", len(opciones))
    for cmd, texto in sorted(opciones.items()):
        marca = "  <==" if BUSCADAS.search(cmd + " " + texto) else ""
        print(f"   cmd={cmd:40} {texto[:60]}{marca}")
    # Otros enlaces que no son cmd= (por si LDR es otra web)
    otros = sorted({sin_token(u) for u in re.findall(r"""href=["'](https?://[^"']+)["']""", r.text)})
    print("enlaces a otras webs:", otros[:30])

    probar = [c for c, t in opciones.items() if BUSCADAS.search(c + " " + t)] + ADIVINADAS
    vistos = set()
    for cmd in probar:
        base = cmd.split("&")[0]
        if base in vistos:
            continue
        vistos.add(base)
        try:
            rr = s.get(PANEL, params={"cmd": base, "token": tok}, timeout=60)
            html = rr.text
            error = bool(re.search(r"no existe|error|not found|excepci", titulo(html) + html[:3000], re.I))
            print(f"\n== cmd={base}: {rr.status_code} | {len(html)} bytes | título: {titulo(html)!r}{' | parece error' if error else ''}")
            if rr.ok and len(html) > 2000 and not error:
                for u in direcciones(html):
                    print("     usa:", u)
        except Exception as e:  # noqa: BLE001
            print(f"\n== cmd={base}: ERROR {str(e)[:120]}")


if __name__ == "__main__":
    main()
