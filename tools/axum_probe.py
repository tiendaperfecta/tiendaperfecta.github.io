#!/usr/bin/env python3
"""
axum_probe.py — Exploracion del sistema de Axum GPS.

No es parte del refresco diario: lo llama axum_fetch.py solo cuando existe el
archivo `tools/probe.on`. Sirve para descubrir que datos hay disponibles
(tiempos en cliente, frecuencias/cartera, LDR/choferes, eventos de zona) sin
tener que adivinar, porque la documentacion interna ya demostro estar
desactualizada mas de una vez.

Deja el resultado en axum/data/_probe.json, que la Action commitea junto al
resto. Cuando termina la exploracion se borra `tools/probe.on` y este archivo
deja de ejecutarse.
"""
import re
import json

BASE = "https://gps.axumvm.com.ar"
CORTE = 400          # caracteres de muestra por respuesta


def _muestra(v):
    """Descripcion corta de una respuesta, para no volcar miles de filas."""
    out = {"tipo": type(v).__name__}
    if isinstance(v, str):
        # Varios metodos devuelven el JSON serializado adentro de un string.
        try:
            v = json.loads(v)
            out["tipo"] = "str->" + type(v).__name__
        except ValueError:
            out["valor"] = v[:CORTE]
            return out
    if isinstance(v, list):
        out["filas"] = len(v)
        out["item"] = json.dumps(v[0], ensure_ascii=False)[:CORTE] if v else None
    elif isinstance(v, dict):
        out["claves"] = list(v)[:25]
        out["muestra"] = json.dumps(v, ensure_ascii=False)[:CORTE]
    else:
        out["valor"] = str(v)[:CORTE]
    return out


def catalogo(sesion):
    """Metodos declarados por el proxy JS del web service: nombre -> parametros."""
    metodos = {}
    for svc in ("LocationService", "RuleService"):
        try:
            js = sesion.get("%s/%s.asmx/js" % (BASE, svc), timeout=60).text
        except Exception as e:
            metodos[svc] = {"error": str(e)[:200]}
            continue
        encontrados = {}
        for m in re.finditer(r"(\w+)\s*:\s*function\s*\(([^)]*)\)", js):
            nombre, args = m.group(1), m.group(2)
            if nombre in ("prototype", "initializeBase", "registerClass"):
                continue
            params = [a.strip() for a in args.split(",") if a.strip()
                      and a.strip() not in ("succeededCallback", "failedCallback",
                                            "userContext")]
            encontrados[nombre] = params
        metodos[svc] = encontrados
    return metodos


def _por_tema(catalogo_ls):
    """Agrupa los metodos por lo que estamos buscando, para leerlo de un vistazo."""
    temas = {
        "tiempos":    ("tiempo", "pdv", "permanencia", "duracion", "estadia"),
        "frecuencia": ("frecuencia", "cartera", "allclients", "clientesde", "ruta"),
        "ldr":        ("ldr", "chofer", "conductor", "driver", "reparto", "camion"),
        "eventos":    ("evento", "alerta", "regla", "zona", "entrada", "salida"),
        "recorrido":  ("location", "trayect", "recorrid", "km", "travelled"),
    }
    out = {}
    for tema, claves in temas.items():
        out[tema] = sorted(n for n in catalogo_ls
                           if any(c in n.lower() for c in claves))
    return out


def pantalla_ldr(sesion):
    """Que servicios consume la pantalla Ldr.aspx (no esta documentada)."""
    try:
        r = sesion.get("%s/Ldr.aspx" % BASE, timeout=60)
    except Exception as e:
        return {"error": str(e)[:200]}
    if r.status_code != 200:
        return {"status": r.status_code}
    html = r.text
    return {
        "status": r.status_code,
        "bytes": len(html),
        "titulo": (re.search(r"<title>(.*?)</title>", html, re.S | re.I).group(1).strip()
                   if re.search(r"<title>(.*?)</title>", html, re.S | re.I) else ""),
        # Llamadas a web services que aparezcan en el JS de la pagina.
        "servicios": sorted(set(re.findall(r"(\w+Service\.asmx/\w+)", html)))[:40],
        "metodos_js": sorted(set(re.findall(r"LocationService\.(\w+)\(", html)))[:40],
        "grillas": sorted(set(re.findall(r'id="(\w*[Gg]rid\w*)"', html)))[:20],
    }


def _llamar(gps, metodo, params, con_username=True):
    """Llamada cruda. Hace falta porque varios metodos NO declaran userName y
    agregarselo (como hace Gps.call) los hace responder 500."""
    cuerpo = dict(params)
    if con_username:
        cuerpo.setdefault("userName", gps.user)
    r = gps.s.post("%s/LocationService.asmx/%s" % (BASE, metodo),
                   data=json.dumps(cuerpo),
                   headers={"Content-Type": "application/json",
                            "Accept": "application/json"},
                   timeout=90)
    if r.status_code != 200:
        return {"error": "HTTP %s" % r.status_code}
    try:
        d = r.json()
    except ValueError:
        return {"error": "no-json", "valor": r.text[:200]}
    return _muestra(d["d"] if isinstance(d, dict) and "d" in d else d)


def _llamar_crudo(gps, metodo, params, con_username=True):
    cuerpo = dict(params)
    if con_username:
        cuerpo.setdefault("userName", gps.user)
    r = gps.s.post("%s/LocationService.asmx/%s" % (BASE, metodo),
                   data=json.dumps(cuerpo),
                   headers={"Content-Type": "application/json",
                            "Accept": "application/json"}, timeout=90)
    r.raise_for_status()
    d = r.json()
    d = d["d"] if isinstance(d, dict) and "d" in d else d
    if isinstance(d, str):
        d = json.loads(d)
    return d


def correr(gps, fecha, sellers):
    """gps: instancia de Gps (ya logueada). Devuelve el informe completo."""
    import datetime as dt
    hoy = dt.date.fromisoformat(fecha)
    uno = sellers[0] if sellers else "1"
    informe = {"fecha": fecha, "sellerProbado": uno, "sellers": sellers[:15]}

    # .NET numera los dias con domingo=0; probamos las dos convenciones.
    dia_dotnet = (hoy.weekday() + 1) % 7          # lunes=1 ... domingo=0
    dia_iso = hoy.isoweekday()                    # lunes=1 ... domingo=7

    # Camion de referencia: lo saca de lastTruckPositions (mismo CSV que los
    # vendedores, el id va en el campo 3).
    try:
        crudo_camiones = _llamar_crudo(gps, "lastTruckPositions", {})
        camion = str(crudo_camiones[0]).split(",")[2] if crudo_camiones else "1"
    except Exception:
        crudo_camiones, camion = [], "1"
    informe["camiones"] = [str(c) for c in crudo_camiones][:10]
    informe["camionProbado"] = camion

    pruebas = [
        # --- zonas del vendedor, para detectar la entrada a la zona ---
        ("allZonasByVendedor", {"codigoVendedor": uno}, True),
        ("allZoneByVendedor", {"codigoVendedor": uno}, True),
        ("allClientsZonaByVendedor", {"idVendedor": uno}, True),
        ("allZonasFacturacion", {}, True),
        ("allZonasPOIByVendedor", {"codigoVendedor": uno}, True),
        # --- recorrido con hora, para cruzar contra la zona ---
        ("FindLocationsOfVendedorIdEnDia", {"idVendedor": uno, "dia": fecha}, True),
    ]
    resultados = {}
    for metodo, params, con_user in pruebas:
        real = metodo.rstrip("2")      # los "2" son variantes de parametros
        etiqueta = metodo + ("" if real == metodo else " (variante)")
        try:
            resultados[etiqueta] = _llamar(gps, real, params, con_user)
        except Exception as e:
            resultados[etiqueta] = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    informe["pruebas"] = resultados

    # Resumen del metodo clave: cuantas filas, cuantas con visito=SI/NO, que
    # forma tienen los tiempos, y cuantos clientes distintos aparecen.
    try:
        filas = _llamar_crudo(gps, "pasoPoprPDVAt", {"aDate": fecha})
        si = [f for f in filas if str(f.get("visito", "")).upper() == "SI"]
        no = [f for f in filas if str(f.get("visito", "")).upper() == "NO"]
        informe["pasoPorPDV"] = {
            "filas": len(filas),
            "visitoSI": len(si),
            "visitoNO": len(no),
            "vendedores": sorted({str(f.get("sellerId")) for f in filas}),
            "clientesDistintos": len({str(f.get("clientId")) for f in filas}),
            "ejemploSI": si[0] if si else None,
            "ejemploNO": no[0] if no else None,
            "tiemposSI": [f.get("tiempo") for f in si[:12]],
        }
    except Exception as e:
        informe["pasoPorPDV"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    informe["diasProbados"] = {"dotnet": dia_dotnet, "iso": dia_iso}
    return informe
