#!/usr/bin/env python3
"""
pepsico_probe.py — Exploracion puntual de la API de GesCom para el panel
Avance PepsiCo.

No es parte del refresco: se corre a mano (workflow_dispatch) para descubrir
que endpoints/campos existen antes de escribir el generador real. Deja el
resultado en tools/_pepsico_probe.json, que la Action commitea para poder
leerlo. Cuando termina la exploracion, se puede borrar este archivo.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402
import requests

OUT = Path(__file__).resolve().parent / "_pepsico_probe.json"


def muestra(v, corte=1500):
    if isinstance(v, list):
        return {"tipo": "list", "filas": len(v), "item0": v[0] if v else None}
    if isinstance(v, dict):
        return {"tipo": "dict", "claves": list(v)[:40], "muestra": v}
    return {"tipo": type(v).__name__, "valor": str(v)[:corte]}


def main():
    if not gescom.hay_credenciales():
        print("Sin credenciales de GesCom.")
        return 1

    s = requests.Session()
    tok = gescom._token(s)
    out = {}

    def get(path, params=None, timeout=90):
        r = s.get(gescom.API + path, params=params, timeout=timeout,
                   headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:500])

    # 1) Articulos: buscar el codigoProveedor de Pepsico y ver que marcas trae.
    st, arts = get("/data/cmd/inventario/api/v2/get-articulos")
    arts = arts.get("data", arts) if isinstance(arts, dict) else arts
    out["articulos_status"] = st
    out["articulos_total"] = len(arts) if isinstance(arts, list) else None
    if isinstance(arts, list) and arts:
        out["articulos_campos"] = list(arts[0].keys())
        # Proveedores distintos con su primer articulo de ejemplo
        provs = {}
        for a in arts:
            p = str(a.get("codigoProveedor"))
            if p not in provs:
                provs[p] = a.get("descripcion") or a.get("nombreProveedor") or ""
        out["proveedores_muestra"] = dict(list(provs.items())[:60])
        pepsi_like = [a for a in arts if any(
            k in (a.get("descripcion") or "").upper()
            for k in ("LAYS", "DORITOS", "CHEETOS", "PEHUAMAR", "TOSTITOS", "QUAKER", "3D ", "TWISTOS"))]
        out["pepsico_articulos_ejemplos"] = pepsi_like[:8]
        out["pepsico_proveedores_codigos"] = sorted({str(a.get("codigoProveedor")) for a in pepsi_like})

    # 2) Una venta chica de ayer/hoy para ver la forma completa del registro
    #    (buscamos si existe fechaCarga y algun campo de origen/canal).
    import datetime as dt
    hoy = dt.date.today()
    desde = (hoy - dt.timedelta(days=3)).isoformat()
    hasta = (hoy + dt.timedelta(days=1)).isoformat()
    st, ventas = get("/data/cmd/ventas/api/v2/get",
                      {"fechadesde": desde, "fechahasta": hasta, "pagesize": 5, "pagestotake": 1, "pagestoskip": 0})
    ventas = ventas.get("data", ventas) if isinstance(ventas, dict) else ventas
    out["ventas_status"] = st
    out["ventas_muestra"] = muestra(ventas)
    if isinstance(ventas, list) and ventas:
        out["ventas_campos_header"] = list(ventas[0].keys())
        items = ventas[0].get("items") or []
        if items:
            out["ventas_campos_item"] = list(items[0].keys())

    # 3) codigos de TipoDeVenta / motivos de devolucion, si existe catalogo
    for ruta in ["ventas/api/v1/get-tipos-venta", "ventas/api/v1/get-tipoventa",
                 "ventas/api/v1/get-motivos-devolucion", "ventas/api/v1/get-motivos"]:
        st, d = get("/data/cmd/" + ruta)
        out.setdefault("catalogos", {})[ruta] = {"status": st, "muestra": muestra(d) if st == 200 else d}

    # 4) Algo de "Tienda Perfecta" / portafolio / censo / relevamiento
    for ruta in ["ventas/api/v1/get-relevamientos", "ventas/api/v1/get-censo",
                 "ventas/api/v1/get-tienda-perfecta", "encuestas/api/v1/get",
                 "ventas/api/v1/get-portafolio", "ventas/api/v1/get-taxonomia"]:
        st, d = get("/data/cmd/" + ruta)
        out.setdefault("censo_probes", {})[ruta] = {"status": st, "muestra": muestra(d) if st == 200 else d}

    # 5) Combos / promociones
    for ruta in ["ventas/api/v1/get-combos", "ventas/api/v1/get-promociones",
                 "ventas/api/v2/get-combos"]:
        st, d = get("/data/cmd/" + ruta)
        out.setdefault("combos_probes", {})[ruta] = {"status": st, "muestra": muestra(d) if st == 200 else d}

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("Guardado", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
