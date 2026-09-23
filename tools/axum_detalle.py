#!/usr/bin/env python3
"""
axum_detalle.py — Detalle por cliente: tiempos, cobertura, histórico y LDR.

Cruza tres fuentes:

    Axum GPS    por donde paso el vendedor y cuanto tiempo estuvo (pasoPoprPDVAt)
    Orders360   que pedidos cargo
    GesCom      quien es cada cliente, donde esta de verdad, y a quien le tocaba
                visitar ese dia (rutasPreventa)

La geolocalizacion y la cartera de Axum no se usan: sus coordenadas no son
confiables y no tiene cargadas las frecuencias. GesCom tiene el 100% de los
clientes con coordenada y la ruta de preventa por dia, asi que la cobertura se
mide contra lo que realmente le tocaba hacer.

Publica:
    pdv.json        una fila por cliente con actividad
    tiempos.json    tiempos, linea de tiempo y alertas de jornada
    cobertura.json  ruta del dia vs. lo que hizo, y que le quedo pendiente
    dia-<fecha>.json + dias.json          lo mismo, dia por dia
    ldr.json        choferes y camiones
"""
import datetime as dt
from collections import defaultdict

import axum_nombres
import axum_zonas

# Umbrales de las alertas de jornada (hora argentina).
HORA_LLEGADA = dt.time(9, 0)
HORA_SALIDA = dt.time(14, 0)

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_HISTORIA = 30          # ventana que se intenta tener completa
BACKFILL_POR_CORRIDA = 3    # dias viejos por corrida, para no estirar la Action


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #
def _minutos(tiempo):
    """'7:55' -> 7.9 minutos. Axum lo manda como mm:ss."""
    try:
        m, s = str(tiempo).split(":")
        return round(int(m) + int(s) / 60.0, 1)
    except (ValueError, AttributeError):
        return 0.0


def _hora(texto):
    """Acepta los dos formatos de fecha que conviven en el sistema."""
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


def afinar(track, metros=45):
    """Deja un punto cada `metros` para poder dibujar el trazo sin publicar
    decenas de miles de posiciones."""
    salida, ultimo = [], None
    for momento, lat, lng in track:
        if ultimo is not None:
            dlat = (lat - ultimo[0]) * 110540
            dlng = (lng - ultimo[1]) * 87500      # a esta latitud
            if (dlat * dlat + dlng * dlng) ** 0.5 < metros:
                continue
        ultimo = (lat, lng)
        salida.append([round(lat, 5), round(lng, 5), momento.strftime("%H:%M")])
    return salida


def _fecha_pedido(o):
    """(fecha, hora) del pedido, desde orderDate '2026-09-21T04:31:37'."""
    txt = str(o.get("orderDate") or "")
    if "T" in txt:
        f, _, h = txt.partition("T")
        return f, h[:5]
    return txt[:10], None


class Maestro:
    """Clientes de GesCom: nombre, coordenada, localidad, rubro."""

    def __init__(self, datos):
        self.campos = (datos or {}).get("campos", [])
        self.clientes = (datos or {}).get("clientes", {})

    def _campo(self, cid, nombre):
        fila = self.clientes.get(str(cid))
        if not fila or nombre not in self.campos:
            return None
        return fila[self.campos.index(nombre)]

    def nombre(self, cid):
        return self._campo(cid, "nombre") or ("Cliente " + str(cid))

    def localidad(self, cid):
        return self._campo(cid, "localidad") or ""

    def ramo(self, cid):
        return self._campo(cid, "ramo") or ""


# --------------------------------------------------------------------------- #
# Un dia
# --------------------------------------------------------------------------- #
def armar_dia(gps, fecha, orders, maestro, rutas, con_km=True, con_zona=True,
              ahora=None, avisos=None):
    """Todo lo que se sabe de un dia. No escribe nada."""
    avisos = avisos if avisos is not None else []
    ahora = ahora or dt.datetime.now()
    jornada_cerrada = fecha < ahora.date().isoformat() or ahora.time() >= HORA_SALIDA
    dia_semana = DIAS[dt.date.fromisoformat(fecha).weekday()]
    ruta_del_dia = (rutas or {}).get(dia_semana, {})

    # ---- pedidos del dia, por cliente ----
    pedidos = defaultdict(lambda: {"pedidos": 0, "monto": 0.0, "sellerId": "",
                                   "fecha": fecha, "hora": None})
    for o in orders or []:
        cid = str(o.get("clientId") or "")
        if not cid:
            continue
        f, h = _fecha_pedido(o)
        p = pedidos[cid]
        p["pedidos"] += 1
        p["monto"] += (o.get("total") or 0)
        p["sellerId"] = str(o.get("sellerId") or "")
        p["fecha"] = f or fecha
        if h and (p["hora"] is None or h < p["hora"]):
            p["hora"] = h

    # ---- por donde paso ----
    try:
        paso = gps.paso_por_pdv(fecha)
    except Exception as e:
        paso = []
        avisos.append("pasoPorPDV %s: %s" % (fecha, e))

    sellers = sorted({str(f.get("sellerId")) for f in paso if f.get("sellerId")} |
                     {v["sellerId"] for v in pedidos.values() if v["sellerId"]} |
                     set(ruta_del_dia))

    en_ruta = {sid: {str(x["cliente"]) for x in ruta_del_dia.get(sid, [])}
               for sid in sellers}

    filas, vistos = [], set()
    acum = defaultdict(lambda: {"visitas": 0, "minutos": 0.0, "pedidos": 0,
                                "monto": 0.0, "sinVisita": 0, "pasoSinVender": 0,
                                "primera": None, "ultima": None, "visitados": set()})

    for f in paso:
        sid, cid = str(f.get("sellerId") or ""), str(f.get("clientId") or "")
        if not cid:
            continue
        vistos.add((sid, cid))
        visito = str(f.get("visito", "")).upper() == "SI"
        minutos = _minutos(f.get("tiempo"))
        momento = _hora(f.get("horario"))
        ped = pedidos.get(cid, {})
        propio = bool(ped) and (not ped["sellerId"] or ped["sellerId"] == sid)
        filas.append({
            "fecha": fecha, "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "clientId": cid, "nombre": maestro.nombre(cid),
            "localidad": maestro.localidad(cid), "ramo": maestro.ramo(cid),
            "enRuta": cid in en_ruta.get(sid, set()),
            "minutos": minutos, "visito": visito, "hora": _hhmm(momento),
            "pedidos": ped.get("pedidos", 0) if propio else 0,
            "monto": round(ped.get("monto", 0), 2) if propio else 0,
            "fechaVenta": ped.get("fecha") if propio else None,
            "horaVenta": ped.get("hora") if propio else None,
            "estado": ("vendio" if (propio and visito) else
                       "paso_sin_vender" if visito else
                       "vendio_sin_pasar" if propio else "sin_visita"),
        })
        r = acum[sid]
        if visito:
            r["visitas"] += 1
            r["minutos"] += minutos
            r["visitados"].add(cid)
            if momento:
                r["primera"] = min(r["primera"] or momento, momento)
                r["ultima"] = max(r["ultima"] or momento, momento)
            if not propio:
                r["pasoSinVender"] += 1
        else:
            r["sinVisita"] += 1
        if propio:
            r["pedidos"] += ped.get("pedidos", 0)
            r["monto"] += ped.get("monto", 0)

    # Pedidos sin ningun paso registrado por ese cliente.
    for cid, ped in pedidos.items():
        sid = ped["sellerId"]
        if (sid, cid) in vistos:
            continue
        filas.append({
            "fecha": fecha, "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "clientId": cid, "nombre": maestro.nombre(cid),
            "localidad": maestro.localidad(cid), "ramo": maestro.ramo(cid),
            "enRuta": cid in en_ruta.get(sid, set()),
            "minutos": 0.0, "visito": False, "hora": None,
            "pedidos": ped["pedidos"], "monto": round(ped["monto"], 2),
            "fechaVenta": ped["fecha"], "horaVenta": ped["hora"],
            "estado": "vendio_sin_pasar",
        })
        r = acum[sid]
        r["pedidos"] += ped["pedidos"]
        r["monto"] += ped["monto"]

    filas.sort(key=lambda r: (r["sellerId"], -(r["minutos"] or 0)))
    resumen = defaultdict(int)
    for r in filas:
        resumen[r["estado"]] += 1

    # ---- linea de tiempo ----
    linea = defaultdict(list)
    try:
        crudo = gps.visitas_con_hora(fecha)
    except Exception as e:
        crudo = []
        avisos.append("visitasConHora %s: %s" % (fecha, e))
    duenio = {}
    for sid, clientes in en_ruta.items():
        for cid in clientes:
            duenio.setdefault(cid, sid)
    for r in filas:
        if r["visito"]:
            duenio.setdefault(r["clientId"], r["sellerId"])
    for item in crudo:
        p = str(item).split(",", 1)
        if len(p) < 2:
            continue
        cid, momento = p[0].strip(), _hora(p[1])
        if momento:
            linea[duenio.get(cid, "")].append(
                {"hora": _hhmm(momento), "clientId": cid,
                 "nombre": maestro.nombre(cid)})
    for sid in linea:
        linea[sid].sort(key=lambda x: x["hora"] or "")

    # ---- km y zona (solo para el dia en curso: son muchas llamadas) ----
    kms, zona_de = {}, {}
    for sid in sellers:
        if con_km:
            try:
                kms[sid] = gps.km_vendedor(sid, fecha)
            except Exception:
                kms[sid] = None
        if con_zona:
            try:
                zona_de[sid] = axum_zonas.jornada(gps, sid, fecha, _hora)
            except Exception as e:
                zona_de[sid] = {}
                avisos.append("zona %s: %s" % (sid, e))

    # ---- estadisticas y alertas ----
    stats = []
    for sid in sellers:
        r = acum[sid]
        z = zona_de.get(sid, {})
        # La alerta toma la primera senal de actividad: entrada a la zona o
        # primera visita, la que sea mas temprana.
        ini = [x for x in (z.get("entrada"), r["primera"]) if x]
        fin = [x for x in (z.get("salida"), r["ultima"]) if x]
        entrada = min(ini) if ini else None
        salida = max(fin) if fin else None
        stats.append({
            "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "inicioJornada": _hhmm(entrada), "finJornada": _hhmm(salida),
            "entradaZona": _hhmm(z.get("entrada")), "salidaZona": _hhmm(z.get("salida")),
            "zonaConfiable": bool(z.get("puntosEnZona")),
            "primera": _hhmm(r["primera"]), "ultima": _hhmm(r["ultima"]),
            "visitas": r["visitas"],
            "minutosTotal": round(r["minutos"], 1),
            "minutosPromedio": round(r["minutos"] / r["visitas"], 1) if r["visitas"] else 0,
            "pedidos": r["pedidos"], "monto": round(r["monto"], 2),
            "pasoSinVender": r["pasoSinVender"],
            "km": kms.get(sid),
            "llegoTarde": bool(entrada and entrada.time() > HORA_LLEGADA),
            "salioTemprano": bool(jornada_cerrada and salida and
                                  salida.time() < HORA_SALIDA),
        })
    stats.sort(key=lambda r: -(r["visitas"] or 0))

    # ---- cobertura contra la ruta que le tocaba ese dia ----
    cobertura, pendientes = [], {}
    for sid in sellers:
        ruta = en_ruta.get(sid, set())
        visitados = acum[sid]["visitados"]
        hechos = ruta & visitados
        faltan = sorted(ruta - visitados)
        fuera = sorted(visitados - ruta)
        cobertura.append({
            "sellerId": sid, "vendedor": axum_nombres.de(sid),
            "ruta": len(ruta), "visitadosDeRuta": len(hechos),
            "pendientes": len(faltan), "fueraDeRuta": len(fuera),
            "pct": round(len(hechos) * 100.0 / len(ruta), 1) if ruta else None,
        })
        pendientes[sid] = [{"clientId": c, "nombre": maestro.nombre(c),
                            "localidad": maestro.localidad(c)} for c in faltan]
    cobertura.sort(key=lambda r: -(r["pct"] or 0))

    recorridos = {sid: afinar(z.get("track") or [])
                  for sid, z in zona_de.items() if z.get("track")}

    return {
        "recorridos": recorridos,
        "date": fecha, "diaSemana": dia_semana, "jornadaCerrada": jornada_cerrada,
        "umbrales": {"llegada": HORA_LLEGADA.strftime("%H:%M"),
                     "salida": HORA_SALIDA.strftime("%H:%M")},
        "rows": filas, "resumen": dict(resumen), "bySeller": stats,
        "timeline": dict(linea), "cobertura": cobertura, "pendientes": pendientes,
        "totales": {
            "visitas": sum(s["visitas"] for s in stats),
            "minutos": round(sum(s["minutosTotal"] for s in stats), 1),
            "pedidos": sum(s["pedidos"] for s in stats),
            "monto": round(sum(s["monto"] for s in stats), 2),
        },
    }


# --------------------------------------------------------------------------- #
# Publicacion
# --------------------------------------------------------------------------- #
def _resumen_indice(dia):
    return dict({"fecha": dia["date"], "diaSemana": dia["diaSemana"],
                 "vendedores": len([s for s in dia["bySeller"] if s["visitas"]])},
                **dia["totales"])


def construir(gps, fecha, orders, escribir, meta, ahora=None, maestro=None,
              rutas=None, orders_de=None, dias_existentes=()):
    """Arma el dia de hoy, lo publica, y completa los dias que falten."""
    avisos = []
    maestro = Maestro(maestro)
    hoy = armar_dia(gps, fecha, orders, maestro, rutas, con_km=True,
                    con_zona=True, ahora=ahora, avisos=avisos)

    escribir("pdv.json", {"date": fecha, "rows": hoy["rows"],
                          "resumen": hoy["resumen"]})
    escribir("tiempos.json", {k: hoy[k] for k in
                              ("date", "diaSemana", "jornadaCerrada", "umbrales",
                               "bySeller", "timeline")})
    escribir("cobertura.json", {"date": fecha, "diaSemana": hoy["diaSemana"],
                                "bySeller": hoy["cobertura"],
                                "pendientes": hoy["pendientes"]})
    escribir("recorrido-%s.json" % fecha, {"date": fecha,
                                           "porVendedor": hoy.pop("recorridos", {})})
    escribir("dia-%s.json" % fecha, hoy)

    # ---- dias que falten en la ventana de historia ----
    indice = {d["fecha"]: d for d in (dias_existentes or [])}
    indice[fecha] = _resumen_indice(hoy)
    if orders_de:
        base = dt.date.fromisoformat(fecha)
        faltan = [(base - dt.timedelta(days=i)).isoformat()
                  for i in range(1, DIAS_HISTORIA + 1)]
        faltan = [f for f in faltan if f not in indice]
        for f in faltan[:BACKFILL_POR_CORRIDA]:
            try:
                dia = armar_dia(gps, f, orders_de(f), maestro, rutas,
                                con_km=False, con_zona=False, ahora=ahora,
                                avisos=avisos)
                dia.pop("recorridos", None)      # los dias viejos no lo traen
                escribir("dia-%s.json" % f, dia)
                indice[f] = _resumen_indice(dia)
            except Exception as e:
                avisos.append("historia %s: %s" % (f, e))
    escribir("dias.json",
             {"dias": sorted(indice.values(), key=lambda d: d["fecha"], reverse=True)})

    # ---- LDR ----
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
    meta["detalle"] = {"filas": len(hoy["rows"]),
                       "vendedores": len(hoy["bySeller"]),
                       "diasEnHistoria": len(indice)}
