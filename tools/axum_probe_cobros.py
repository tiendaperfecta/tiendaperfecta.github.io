#!/usr/bin/env python3
"""
axum_probe_cobros.py — ¿Axum tiene lo que cobran los choferes (transferencias)?

Prueba de solo lectura, por única vez (30/9/2026), para el Control de Transferencias:
Gescom dejó de dar la rendición de valores (403) y los choferes cargan los cobros en Axum.

OJO: este repo es PÚBLICO y los registros de GitHub Actions se ven desde afuera. Por eso
este script NO imprime datos: solo nombres de métodos, cantidad de filas, nombres de campos
y, en campos de medio de pago, los códigos distintos que aparecen.
"""
import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axum_fetch import Orders360, Gps  # noqa: E402

ART = dt.timezone(dt.timedelta(hours=-3))


def forma(x, prof=0):
    """Estructura sin valores: tipos y nombres de campos."""
    if isinstance(x, dict):
        if prof > 2:
            return "{...}"
        return {k: forma(v, prof + 1) for k, v in list(x.items())[:60]}
    if isinstance(x, list):
        return [forma(x[0], prof + 1)] if x else []
    if isinstance(x, str) and x[:1] in "[{":
        try:
            return {"(json en texto)": forma(json.loads(x), prof + 1)}
        except ValueError:
            pass
    return type(x).__name__


def main():
    hoy = dt.datetime.now(ART).date()
    dias = [hoy - dt.timedelta(days=d) for d in (1, 2, 3)]

    print("=== GPS (gps.axumvm.com.ar) ===")
    try:
        gps = Gps(os.environ["GPS_USER"], os.environ["GPS_PASS"])
        for metodo, params in [
            ("duesAt", lambda d: {"aDate": d.strftime("%m/%d/%Y")}),
            ("duesAt", lambda d: {"aDate": d.isoformat()}),
            ("FindClientesConEntrega", lambda d: {"unDia": d.strftime("%m/%d/%Y")}),
            ("clientesConEntregaEnDia", lambda d: {"unDia": d.strftime("%m/%d/%Y")}),
            ("clientesFacturacion", lambda d: {}),
        ]:
            for d in dias[:2]:
                try:
                    r = gps.call(metodo, **params(d))
                    n = len(r) if isinstance(r, (list, dict)) else 1
                    print(f"{metodo} {params(d)}: {type(r).__name__}, {n} elementos, forma: {json.dumps(forma(r), ensure_ascii=False)[:700]}")
                    if n:
                        break
                except Exception as e:  # noqa: BLE001
                    print(f"{metodo} {params(d)}: ERROR {str(e)[:150]}")
    except Exception as e:  # noqa: BLE001
        print("GPS: no se pudo entrar:", str(e)[:200])

    print("=== Orders360 (pedidos) ===")
    try:
        o = Orders360(os.environ.get("AXUM_ORDERS_USER", ""), os.environ.get("AXUM_ORDERS_PASS", ""))
        r = o.orders(dias[1].isoformat(), hoy.isoformat(), 0, 200)
        pedidos = r.get("data") if isinstance(r, dict) else r
        pedidos = pedidos or []
        print("pedidos:", len(pedidos), "| total:", r.get("total") if isinstance(r, dict) else "?")
        if pedidos:
            print("campos del pedido:", sorted(pedidos[0].keys()))
            for campo in ("paymentMethod", "axumPaymentMethodCode", "erpPaymentMethodDescription", "erpState", "source", "origin"):
                if campo in pedidos[0]:
                    print(f"  {campo}: {Counter(str(p.get(campo)) for p in pedidos).most_common(12)}")
            extra = [p.get("extraData") for p in pedidos if p.get("extraData")]
            print("  extraData con contenido:", len(extra), "| forma:", json.dumps(forma(extra[0]), ensure_ascii=False)[:500] if extra else "-")
        for ruta in ("/api/EventStream/GetEventsIndex", "/api/Settings/Features"):
            try:
                rr = o.s.get("https://masuno-order360.axumweb.com" + ruta,
                             headers={"Authorization": f"Bearer {o.token}", "Accept": "application/json"}, timeout=60)
                print(ruta, rr.status_code, json.dumps(forma(rr.json()), ensure_ascii=False)[:500] if rr.ok else rr.text[:120])
                if rr.ok and ruta.endswith("Features"):
                    # Son nombres de funciones del sistema, no datos de clientes.
                    for f in rr.json():
                        print("   funcion:", f.get("feature"), "=", f.get("isAllowed"))
            except Exception as e:  # noqa: BLE001
                print(ruta, "ERROR", str(e)[:150])
    except Exception as e:  # noqa: BLE001
        print("Orders360: no se pudo entrar:", str(e)[:200])


if __name__ == "__main__":
    main()
