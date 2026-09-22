#!/usr/bin/env python3
"""
axum_detalle.py — Detalle por cliente: tiempos, cobertura y LDR.

Arma los JSON que alimentan las pestanas nuevas del panel, cruzando el GPS
con los pedidos de Orders360:

    pdv.json        una fila por cliente con actividad hoy: cuanto tiempo estuvo
                    el vendedor, si registro visita, y si ademas le cargo pedido.
    tiempos.json    estadisticas de tiempos por vendedor + linea de tiempo del dia
                    + alertas de llegada tarde / salida temprana.
    cobertura.json  cartera de cada vendedor y a que clientes NO paso.
    ldr.json        posicion de los camiones.

Los metodos de Axum que usa se verificaron contra el sistema real (ver
axum/README.md). Los reportes propios de Axum para esto (reporteTiempoEnPDVDiario,
coberturaVendedor, timeToSellVendedor) responden 500 o vienen vacios, por eso se
calcula todo desde los datos crudos.
"""
import datetime as dt
from collections import defaultdict

import axum_nombres
import axum_zonas

# Umbrales de las alertas de jornada (hora argentina).
HORA_LLEGADA = dt.time(9, 0)
HORA_SALIDA = dt.time(14, 0)


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #
def _minutos(tiempo):
    """'7:55' -> 7.92 minutos. Axum lo manda como mm:ss."""
    try:
        m, s = str(tiempo).split(":")
        return round(int(m) + int(s) / 60.0, 1)
    except (ValueError, AttributeError):
        return 0.0


def _hora(texto):
    """'22/09/2026 09:36:24' -> datetime. Tambien acepta el formato de EEUU
    '9/22/2026 8:10:15 AM' que usan otros metodos del mismo sistema."""
    texto = str(texto).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def _hhmm(momento):
    return momento.strftime("%H:%M") if momento else None


def _cartera(filas):
    """allClientsPositionByVendedor devuelve CSV:
    id,lat,lng,NOMBRE (rubro),canal,direccion,??"""
    out = {}
    for fila in filas:
        p = [x.strip() for x in str(fila).split(",")]
        if len(p) < 4 or not p[0]:
            continue
        try:
            lat, lng = float(p[1]), float(p[2])
        except ValueError:
            lat = lng = None
        # El nombre trae el rubro entre parentesis al final; lo separamos.
        nombre = p[3]
        rubro = ""
        if "(" in nombre and nombre.endswith(")"):
            nombre, _, rubro = nombre.partition("(")
            nombre, rubro = nombre.strip(), rubro.rstrip(")")
        out[p[0]] = {"nombre": nombre, "rubro": rubro,
                     "canal": p[4] if len(p) > 4 else "",
                     "lat": lat, "lng": lng}
    return out


# --------------------------------------------------------------------------- #
# Construccion
# --------------------------------------------------------------------------- #
def construir(gps, fecha, orders, escribir, meta, ahora=None):
    """gps: instancia de Gps ya logueada. orders: pedidos de Orders360 de hoy.
    escribir: funcion write_json(nombre, data). meta: dict para dejar avisos.
    ahora: momento de la corrida, para no juzgar una jornada sin terminar."""
    avisos = []
    ahora = ahora or dt.datetime.now()
    jornada_cerrada = ahora.time() >= HORA_SALIDA

    # ---- pedidos del dia por cliente (para el cruce paso / vendio) ---------
    pedidos = defaultdict(lambda: {"pedidos": 0, "monto": 0.0, "sellerId": ""})
    for o in orders:
        cid = str(o.get("clientId") or "")
        if not cid:
            continue
        pedidos[cid]["pedidos"] += 1
        pedidos[cid]["monto"] += (o.get("total") or 0)
        pedidos[cid]["sellerId"] = str(o.get("sellerId") or "")

    # ---- paso por PDV: tiempo en cada cliente y si registro visita ---------
    try:
        paso = gps.paso_por_pdv(fecha)
    except Exception as e:
        paso = []
        avisos.append("pasoPorPDV: %s" % e)

    # ---- vendedores a considerar: los que tienen actividad hoy ------------
    sellers = sorted({str(f.get("sellerId")) for f in paso if f.get("sellerId")} |
                     {v["sellerId"] for v in pedidos.values() if v["sellerId"]})

    # ---- cartera de cada vendedor (nombres + a quien deberia visitar) -----
    cartera_por_vendedor, clientes = {}, {}
    for sid in sellers:
        try:
            c = _cartera(gps.cartera(sid))
        except Exception as e:
            c = {}
            avisos.append("cartera %s: %s" % (sid, e))
        cartera_por_vendedor[sid] = c
        for cid, datos in c.items():
            clientes.setdefault(cid, datos)

    def nombre_de(cid):
        return clientes.get(cid, {}).get("nombre") or ("Cliente " + cid)

    def canal_de(cid):
        return clientes.get(cid, {}).get("canal") or ""

    # ---- filas del dia: uno pasó, le vendió, ninguna, o ambas -------------
    filas, por_vendedor = [], defaultdict(
        lambda: {"visitas": 0, "minutos": 0.0, "pedidos": 0, "monto": 0.0,
                 "sinVisita": 0, "pasoSinVender": 0, "primera": None, "ultima": None})
    vistos = set()

    for f in paso:
        sid, cid = str(f.get("sellerId") or ""), str(f.get("clientId") or "")
        if not cid:
            continue
        vistos.add((sid, cid))
        visito = str(f.get("visito", "")).upper() == "SI"
        minutos = _minutos(f.get("tiempo"))
        momento = _hora(f.get("horario"))
        ped = pedidos.get(cid, {})
        # El pedido cuenta para este vendedor solo si es suyo.
        propio = ped and (not ped["sellerId"] or ped["sellerId"] == sid)
        filas.append({
            "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "clientId": cid, "nombre": nombre_de(cid),
            "canal": canal_de(cid), "minutos": minutos, "visito": visito,
            "hora": _hhmm(momento),
            "pedidos": ped["pedidos"] if propio else 0,
            "monto": round(ped["monto"], 2) if propio else 0,
            "estado": ("vendio" if (propio and visito) else
                       "paso_sin_vender" if visito else
                       "vendio_sin_pasar" if propio else "sin_visita"),
        })
        r = por_vendedor[sid]
        if visito:
            r["visitas"] += 1
            r["minutos"] += minutos
            if momento:
                r["primera"] = min(r["primera"] or momento, momento)
                r["ultima"] = max(r["ultima"] or momento, momento)
            if not propio:
                r["pasoSinVender"] += 1
        else:
            r["sinVisita"] += 1
        if propio:
            r["pedidos"] += ped["pedidos"]
            r["monto"] += ped["monto"]

    # Clientes con pedido de hoy por los que el GPS no registro paso alguno.
    for cid, ped in pedidos.items():
        sid = ped["sellerId"]
        if (sid, cid) in vistos:
            continue
        filas.append({
            "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "clientId": cid, "nombre": nombre_de(cid),
            "canal": canal_de(cid), "minutos": 0.0, "visito": False, "hora": None,
            "pedidos": ped["pedidos"], "monto": round(ped["monto"], 2),
            "estado": "vendio_sin_pasar",
        })
        r = por_vendedor[sid]
        r["pedidos"] += ped["pedidos"]
        r["monto"] += ped["monto"]

    filas.sort(key=lambda r: (r["sellerId"], -(r["minutos"] or 0)))
    resumen = defaultdict(int)
    for r in filas:
        resumen[r["estado"]] += 1
    escribir("pdv.json", {"date": fecha, "rows": filas, "resumen": dict(resumen)})

    # ---- linea de tiempo del dia ------------------------------------------
    try:
        crudo = gps.visitas_con_hora(fecha)      # "1581,9/22/2026 8:10:15 AM"
    except Exception as e:
        crudo = []
        avisos.append("visitasConHora: %s" % e)
    cliente_de_vendedor = {}
    for sid, c in cartera_por_vendedor.items():
        for cid in c:
            cliente_de_vendedor.setdefault(cid, sid)
    linea = defaultdict(list)
    for item in crudo:
        p = str(item).split(",", 1)
        if len(p) < 2:
            continue
        cid, momento = p[0].strip(), _hora(p[1])
        if not momento:
            continue
        sid = cliente_de_vendedor.get(cid, "")
        linea[sid].append({"hora": _hhmm(momento), "clientId": cid,
                           "nombre": nombre_de(cid)})
    for sid in linea:
        linea[sid].sort(key=lambda x: x["hora"] or "")

    # ---- km por vendedor (el reporte diario viene vacio; este anda) --------
    kms = {}
    for sid in sellers:
        try:
            kms[sid] = gps.km_vendedor(sid, fecha)
        except Exception:
            kms[sid] = None

    # ---- entrada y salida de la zona que le toca hoy ----------------------
    # Mas fiel que mirar la primera visita: marca cuando el vendedor realmente
    # piso su zona, aunque todavia no haya registrado ningun cliente.
    zona_de = {}
    for sid in sellers:
        try:
            zona_de[sid] = axum_zonas.jornada(
                gps, sid, fecha, _hora, cartera_por_vendedor.get(sid))
        except Exception as e:
            zona_de[sid] = {}
            avisos.append("zona %s: %s" % (sid, e))

    # ---- estadisticas y alertas de jornada --------------------------------
    stats = []
    for sid in sellers:
        r = por_vendedor[sid]
        primera, ultima = r["primera"], r["ultima"]
        z = zona_de.get(sid, {})
        # La alerta mira la zona; si el vendedor no tiene zona cargada o no hubo
        # recorrido, cae en la primera/ultima visita.
        entrada = z.get("entrada") or primera
        salida = z.get("salida") or ultima
        tarde = bool(entrada and entrada.time() > HORA_LLEGADA)
        # Antes de la hora de corte no se puede decir que alguien "se fue
        # temprano": la jornada sigue. Sin esto, a las 11 AM la alerta salta
        # para todos.
        temprano = bool(jornada_cerrada and salida and salida.time() < HORA_SALIDA)
        stats.append({
            "sellerId": sid,
            "vendedor": axum_nombres.de(sid),
            "entradaZona": _hhmm(z.get("entrada")),
            "salidaZona": _hhmm(z.get("salida")),
            "zonaDelDia": ", ".join(z.get("zonas") or []) or None,
            "puntosEnZona": z.get("puntosEnZona", 0),
            "carteraEnZona": z.get("carteraEnZona"),
            "zonasTotales": z.get("zonasTotales", 0),
            "visitas": r["visitas"],
            "minutosTotal": round(r["minutos"], 1),
            "minutosPromedio": round(r["minutos"] / r["visitas"], 1) if r["visitas"] else 0,
            "pedidos": r["pedidos"],
            "monto": round(r["monto"], 2),
            "pasoSinVender": r["pasoSinVender"],
            "primera": _hhmm(primera),
            "ultima": _hhmm(ultima),
            "km": kms.get(sid),
            "llegoTarde": tarde,
            "salioTemprano": temprano,
        })
    stats.sort(key=lambda r: -(r["visitas"] or 0))
    escribir("tiempos.json", {
        "date": fecha,
        "umbrales": {"llegada": HORA_LLEGADA.strftime("%H:%M"),
                     "salida": HORA_SALIDA.strftime("%H:%M")},
        "diaDeZona": axum_zonas.dia_de(fecha),
        "jornadaCerrada": jornada_cerrada,
        "bySeller": stats,
        "timeline": dict(linea),
    })

    # ---- cobertura: a que clientes de la cartera NO paso -------------------
    paso_por = defaultdict(set)
    for r in filas:
        if r["visito"]:
            paso_por[r["sellerId"]].add(r["clientId"])
    cob, no_visitados = [], {}
    for sid in sellers:
        cart = cartera_por_vendedor.get(sid, {})
        visitados = paso_por[sid] & set(cart)
        faltan = [cid for cid in cart if cid not in visitados]
        cob.append({
            "vendedor": axum_nombres.de(sid),
            # Ojo: la cartera es el total asignado al vendedor, no la ruta del
            # dia. Axum no tiene cargadas las frecuencias (frecuenciaByVendedorDia
            # responde vacio), asi que no hay forma de saber a quien le tocaba hoy.
            "sellerId": sid, "cartera": len(cart), "visitados": len(visitados),
            "noVisitados": len(faltan),
            "pct": round(len(visitados) * 100.0 / len(cart), 1) if cart else None,
        })
        no_visitados[sid] = [{"clientId": cid, "nombre": cart[cid]["nombre"],
                              "canal": cart[cid]["canal"]} for cid in faltan]
    cob.sort(key=lambda r: -(r["pct"] or 0))
    escribir("cobertura.json", {"date": fecha, "bySeller": cob,
                                "noVisitados": no_visitados})

    # ---- LDR: camiones de Axum + choferes del Panel de Fleteros -----------
    try:
        camiones = gps.camiones()
    except Exception as e:
        camiones = []
        avisos.append("camiones: %s" % e)
    import axum_ldr
    try:
        escribir("ldr.json", axum_ldr.construir(fecha, camiones))
    except Exception as e:
        avisos.append("ldr: %s" % e)
        escribir("ldr.json", {"date": fecha, "camiones": camiones})

    if avisos:
        meta.setdefault("errors", []).extend("detalle: " + a for a in avisos)
    meta["detalle"] = {"filas": len(filas), "vendedores": len(sellers),
                       "clientesEnCartera": len(clientes), "camiones": len(camiones)}
