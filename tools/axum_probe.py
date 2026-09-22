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


def correr(gps, fecha, sellers):
    """gps: instancia de Gps (ya logueada). Devuelve el informe completo."""
    informe = {"fecha": fecha, "sellersProbados": sellers[:3]}
    cat = catalogo(gps.s)
    ls = cat.get("LocationService", {})
    informe["catalogo"] = {
        "LocationService": len(ls) if isinstance(ls, dict) else ls,
        "RuleService": len(cat.get("RuleService", {})),
    }
    informe["porTema"] = _por_tema(ls) if isinstance(ls, dict) else {}
    informe["firmas"] = {n: ls[n] for n in sorted(ls)} if isinstance(ls, dict) else {}
    informe["pantallaLdr"] = pantalla_ldr(gps.s)

    uno = sellers[0] if sellers else "1"
    pruebas = [
        ("reporteTiempoEnPDVDiario", {"desde": fecha, "hasta": fecha}),
        ("reporteTiempoEnPDVDiarioBySeller", {"desde": fecha, "hasta": fecha, "seller": uno}),
        ("clientesVisitadosAt", {"aDate": fecha, "sellerID": uno}),
        ("cantidadDeClientesVisitadosPorVendedor", {"fecha": fecha}),
        ("allClientsByVendedor", {"idVendedor": uno}),
        ("FindLocationsOfVendedorIdEnDiaFromTo",
         {"idVendedor": uno, "from": fecha + " 00:00", "to": fecha + " 23:59"}),
        ("lastEvents", {}),
        ("allZonasReparto", {}),
        ("vendedoresConPedidos", {}),
        ("trucks", {}),
    ]
    resultados = {}
    for metodo, params in pruebas:
        try:
            resultados[metodo] = _muestra(gps.call(metodo, **params))
        except Exception as e:
            resultados[metodo] = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    informe["pruebas"] = resultados
    return informe
