#!/usr/bin/env python3
"""
axum_repartos.py — Repartos del día y rechazos por cliente, desde GesCom.

El Panel de Fleteros da el total por chofer; esto abre el detalle: que paso en
cada reparto y, sobre todo, **que cliente rechazo la entrega**.

Como se reconoce un rechazo (mismo criterio que usa el robot de fleteros, para
que los numeros cierren entre los dos paneles):

    venta tipo DEV-RE  +  fecha de pedido >= fecha del reparto  ->  RECHAZO
    venta tipo DEV-RE  +  fecha de pedido  <  fecha del reparto  ->  devolucion
                                                                    programada

Es decir: si la devolucion se genera el mismo dia que sale el camion, el cliente
no la recibio. Si venia de antes, es una devolucion acordada y cuenta como
entrega hecha.

Usa las credenciales de GesCom (ver gescom.py). Sin ellas devuelve None.
"""
import datetime as dt
from collections import defaultdict

import gescom

# Tipos de comprobante que maneja GesCom en un reparto.
VENTA = "VEN"
RECHAZO = "DEV-RE"
DEVOLUCION_CAMBIO = "DEV-CA"

# Dias a mirar hacia adelante para juntar las boletas de un reparto.
VENTANA_FACTURACION = 5


def _fecha(txt):
    """'2026-09-23T00:00:00-03:00' -> '2026-09-23'"""
    return str(txt or "")[:10]


def _ventas_del_dia(sesion, tok, desde, hasta, tope_paginas=12):
    """Todas las ventas del rango. La API pagina de a 500."""
    out = []
    for pagina in range(tope_paginas):
        lote = gescom._traer(
            sesion, tok,
            "ventas/api/v2/get?fechadesde=%s&fechahasta=%s"
            "&pagesize=500&pagestoskip=%d&pagestotake=1" % (desde, hasta, pagina))
        out.extend(lote)
        if len(lote) < 500:
            break
    return out


def del_dia(fecha, maestro=None):
    """Repartos y rechazos de esa fecha. None si no hay credenciales."""
    if not gescom.hay_credenciales():
        return None
    import requests
    sesion = requests.Session()
    tok = gescom._token(sesion)

    dia = dt.date.fromisoformat(fecha)
    siguiente = (dia + dt.timedelta(days=1)).isoformat()
    # GesCom factura las boletas de un reparto uno o dos dias DESPUES de que
    # sale el camion, asi que para ver un reparto completo hay que mirar las
    # ventas de los dias siguientes, no solo las del dia.
    hasta_ventas = (dia + dt.timedelta(days=VENTANA_FACTURACION)).isoformat()

    repartos = gescom._traer(
        sesion, tok, "distribucion/api/v1/get-repartos?fechadesde=%s&fechahasta=%s"
        % (fecha, siguiente))
    ventas = _ventas_del_dia(sesion, tok, fecha, hasta_ventas)

    info = {}
    for r in repartos:
        info[str(r.get("codigo"))] = {
            "codigo": str(r.get("codigo")),
            "fecha": _fecha(r.get("fecha")),
            "chofer": (r.get("nombreChofer") or "").strip() or "Sin chofer",
            "choferId": str(r.get("codigoChofer") or ""),
            "vehiculo": str(r.get("codigoVehiculo") or ""),
            "turno": r.get("turno"),
            "etapa": r.get("etapa") or "",
            "estado": r.get("estado") or "",
            "cerrado": bool(r.get("cerrado")),
            "cancelado": bool(r.get("cancelado")),
            "clientesAsignados": len(r.get("clientes") or []),
        }

    acum = defaultdict(lambda: {"boletas": 0, "entregadas": 0, "rechazos": 0,
                                "importe": 0.0, "importeRechazado": 0.0,
                                "bultosRechazados": 0.0, "clientes": set()})
    rechazos = []

    for v in ventas:
        cod = str(v.get("codigoReparto") or "")
        if not cod or cod not in info:
            continue
        rep = info[cod]
        tipo = str(v.get("codigoTipoVenta") or "")
        cliente = str(v.get("codigoCliente") or "")
        a = acum[cod]
        a["clientes"].add(cliente)

        if tipo == VENTA and not v.get("ventaDirecta"):
            a["boletas"] += 1
            a["entregadas"] += 1
            a["importe"] += (v.get("importeTotal") or 0)
        elif tipo == DEVOLUCION_CAMBIO:
            a["boletas"] += 1
            a["entregadas"] += 1
        elif tipo == RECHAZO:
            programada = _fecha(v.get("fechaPedido")) < rep["fecha"]
            a["boletas"] += 1
            if programada:
                a["entregadas"] += 1
                continue
            a["rechazos"] += 1
            importe = abs(v.get("importeTotal") or 0)
            bultos = sum(abs(float(i.get("cantidad") or 0)) *
                         float(i.get("unidadFactor") or 1)
                         for i in (v.get("items") or []))
            a["importeRechazado"] += importe
            a["bultosRechazados"] += bultos
            rechazos.append({
                "fecha": fecha,
                "clientId": cliente,
                "nombre": maestro.nombre(cliente) if maestro else ("Cliente " + cliente),
                "taxonomia": maestro.taxonomia(cliente) if maestro else "",
                "localidad": maestro.localidad(cliente) if maestro else "",
                "chofer": rep["chofer"],
                "vehiculo": rep["vehiculo"],
                "reparto": rep["codigo"],
                "fechaReparto": rep["fecha"],
                "hora": str(v.get("fechaPedido") or "")[11:16],
                "motivo": str(v.get("codigoMotivoCambio") or ""),
                "importe": round(importe, 2),
                "bultos": round(bultos, 1),
                "comprobante": str(v.get("identificador") or ""),
            })

    filas = []
    for cod in info:
        a = acum[cod]
        rep = dict(info[cod])
        rep.update({
            "boletas": a["boletas"],
            "entregadas": a["entregadas"],
            "rechazos": a["rechazos"],
            "clientes": len(a["clientes"]),
            "importe": round(a["importe"], 2),
            "importeRechazado": round(a["importeRechazado"], 2),
            "bultosRechazados": round(a["bultosRechazados"], 1),
            "efectividad": round(a["entregadas"] * 100.0 / a["boletas"], 1)
                           if a["boletas"] else None,
        })
        filas.append(rep)
    filas.sort(key=lambda r: (-r["rechazos"], -(r["boletas"] or 0)))
    rechazos.sort(key=lambda r: -r["importe"])

    # Un mismo cliente puede rechazar mas de una boleta en el dia.
    por_cliente = defaultdict(lambda: {"rechazos": 0, "importe": 0.0, "bultos": 0.0})
    for r in rechazos:
        c = por_cliente[r["clientId"]]
        c["rechazos"] += 1
        c["importe"] += r["importe"]
        c["bultos"] += r["bultos"]
        c.update({k: r[k] for k in ("nombre", "taxonomia", "localidad", "chofer")})
    clientes = [dict(clientId=k, **v) for k, v in por_cliente.items()]
    for c in clientes:
        c["importe"] = round(c["importe"], 2)
        c["bultos"] = round(c["bultos"], 1)
    clientes.sort(key=lambda c: -c["importe"])

    total_b = sum(r["boletas"] for r in filas)
    total_e = sum(r["entregadas"] for r in filas)
    facturado = sum(1 for r in filas if r["boletas"])
    return {
        "fecha": fecha,
        "repartos": filas,
        "rechazos": rechazos,
        "porCliente": clientes,
        "totales": {
            "repartos": len(filas),
            "choferes": len({r["chofer"] for r in filas}),
            "boletas": total_b,
            "entregadas": total_e,
            "rechazos": len(rechazos),
            "repartosFacturados": facturado,
            "clientesQueRechazaron": len(clientes),
            "importe": round(sum(r["importe"] for r in filas), 2),
            "importeRechazado": round(sum(r["importeRechazado"] for r in filas), 2),
            "efectividad": round(total_e * 100.0 / total_b, 1) if total_b else None,
        },
    }
