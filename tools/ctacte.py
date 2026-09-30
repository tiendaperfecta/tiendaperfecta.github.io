#!/usr/bin/env python3
"""
ctacte.py — Cuentas corrientes de clientes, desde la API oficial de GesCom.

Desde el 30/9/2026 usa el endpoint OFICIAL de cuenta corriente (el usuario de API
nuevo recibe 403 en report/render, que es lo que se usaba antes):

    GET ctacte/api/v4/get-ctacte-clientes-detalle   cada deuda y credito pendiente:
         tipo, numero, cuota, importe original, saldo, fecha, vencimiento,
         condicion de pago, vendedor, comentario, empresa y el CLIENTE PAGADOR
         (la cuenta madre), todo en una sola llamada y sin paginar.
    GET distribucion/api/v2/get-repartos (ultimos 180 dias)   reparto y chofer
         de cada deuda, por la venta que la genero (ventaId). Las deudas que no
         vienen de una venta de esos repartos quedan sin reparto/chofer.

Incluye las deudas de ventas todavia no finalizadas (comprobanteFinalizado =
false), como el reporte de antes. Solo moneda ARS.

Ademas:
    GET  ventas/api/v1/get-clientes             maestro: ruta, tope, contacto
    GET  ventas/api/v1/get-condiciones-pago     codigos de condicion
    GET  ventas/api/v1/get-vendedores
    GET  ventas/api/v2/get (ultimos 30 dias)    compras recientes por cliente

Los comprobantes con saldo positivo (facturas, notas de debito, ajustes de
debito, cheques rechazados) van a "tramos"; los negativos (notas de credito,
ajustes de credito, pagos a cuenta, retenciones) a "creditos". Sin exclusiones:
el total es el del reporte.

El repo es publico: el JSON sale cifrado con AES-256-GCM, clave derivada con
PBKDF2-SHA256 de CTACTE_CLAVE (GitHub Secret). El panel la pide y descifra en
el navegador.

Variables de entorno:
    GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
    CTACTE_CLAVE
    CTACTE_DISPATCH  token para el boton "Actualizar" del panel (opcional)

Salida: ctacte/data.enc.json   (cifrado)
        ctacte/fecha.txt       fecha de actualizacion, para la tarjeta del panel

Requiere: pip install requests cryptography
"""
import os
import sys
import json
import base64
import datetime as dt
from pathlib import Path
from collections import defaultdict

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ctacte"
ART = dt.timezone(dt.timedelta(hours=-3))
ITER = 200_000

REP_DIAS = 180   # ventana de repartos para ponerle reparto/chofer a cada deuda


def repartos_por_venta(s, tok, desde, hasta):
    """ventaId -> (numero de reparto, chofer). Vienen ordenados por fecha: si una
    venta estuvo en mas de un reparto, queda el ultimo."""
    out, skip = {}, 0
    while True:
        pag = gescom._traer(
            s, tok, "distribucion/api/v2/get-repartos?fechadesde=%s&fechahasta=%s"
            "&pagesize=500&pagestotake=2&pagestoskip=%d" % (desde, hasta, skip), 180)
        for r in pag:
            if r.get("cancelado"):
                continue
            for v in r.get("ventas") or []:
                if v.get("ventaId") and not v.get("noEntregada"):
                    out[v["ventaId"]] = (str(r.get("codigo") or ""), (r.get("nombreChofer") or "").strip())
        if len(pag) < 1000 or skip > 40:
            return out
        skip += 2


def bajar():
    s = requests.Session()
    tok = gescom._token(s)
    hoy = dt.datetime.now(ART).date()
    v4 = gescom._traer(s, tok, "ctacte/api/v4/get-ctacte-clientes-detalle", 300)
    sin_finalizar = sum(1 for d in v4 if (d.get("saldo") or 0) > 0 and d.get("comprobanteFinalizado") is False)
    otra_moneda = sum(1 for d in v4 if (d.get("monedaCodigo") or "ARS") != "ARS")
    # Las deudas de ventas todavia no finalizadas SI entran: el reporte de antes las
    # incluia (comparado el 30/9/2026: sin ellas faltaban ~700 deudas y ~220 clientes).
    v4 = [d for d in v4 if (d.get("monedaCodigo") or "ARS") == "ARS"]
    print("ctacte v4: %d comprobantes (%d de ventas sin finalizar; fuera: %d en otra moneda)"
          % (len(v4), sin_finalizar, otra_moneda))
    rep = repartos_por_venta(s, tok, hoy - dt.timedelta(days=REP_DIAS), hoy + dt.timedelta(days=1))
    detallado, padre_de = [], {}
    for d in v4:
        cod = str(d.get("clienteCodigo"))
        pag = str(d.get("clientePagadorCodigo") or "")
        if pag and pag != cod:
            padre_de[cod] = pag
        rp = rep.get(d.get("ventaId"), ("", ""))
        # Mismas claves que tenian las filas del reporte, para que armar() no cambie.
        detallado.append({
            "CodCliente": cod, "CodEmpresa": d.get("empresaCodigo"),
            "CodTipoComprobante": d.get("tipoComprobanteCodigo"),
            "CodPdv": d.get("puntoVentaComprobante") or 0,
            "NumeroComprobante": d.get("numeroComprobante") or 0,
            "NumeroCuota": d.get("numeroCuota") or 0,
            "FechaComprobante": d.get("fechaComprobante"),
            "FechaVencimientoDeuda": d.get("fechaVencimiento"),
            "CP": d.get("condicionPagoCodigo") or "",
            "ImporteOriginal": d.get("importe"), "Importe": d.get("saldo"),
            "CodVendedor": d.get("vendedorCodigo"),
            "Reparto": rp[0], "Chofer": rp[1],
            "Comentario": d.get("comentario"),
            "NombreCliente": d.get("clienteNombre"),
        })
    tok = gescom._token(s)
    clientes = gescom._traer(s, tok, "ventas/api/v1/get-clientes")
    conds = gescom._traer(s, tok, "ventas/api/v1/get-condiciones-pago", 60)
    vends = gescom._traer(s, tok, "ventas/api/v1/get-vendedores", 60)
    # compras de los ultimos 30 dias (para "sigue comprando con deuda")
    ventas, skip = [], 0
    while True:
        tok = gescom._token(s) if skip % 8 == 0 else tok
        pag = gescom._traer(
            s, tok, "ventas/api/v2/get?fechadesde=%s&fechahasta=%s&pagesize=500"
            "&pagestotake=2&pagestoskip=%d" % (hoy - dt.timedelta(days=30),
                                               hoy + dt.timedelta(days=1), skip), timeout=300)
        if not pag:
            break
        ventas += [{"c": v.get("codigoCliente"), "t": v.get("codigoTipoVenta"),
                    "imp": v.get("importeTotal"),
                    "ok": bool((v.get("comprobantePrincipal") or {}).get("numeroComprobante"))}
                   for v in pag]
        skip += 2
    return detallado, padre_de, clientes, conds, vends, ventas


def _iso(f):
    """'27-01-2026' o '2026-01-27T00:00:00' -> '2026-01-27'."""
    if not f:
        return None
    f = str(f)[:10]
    if f[2] == "-":
        return f[6:10] + "-" + f[3:5] + "-" + f[0:2]
    return f


def armar(detallado, padre_de, clientes, conds, vends, ventas):
    hoy = dt.datetime.now(ART).date()
    maestro = {str(c.get("codigo")): c for c in clientes}
    desc2cond = {(c.get("descripcion") or "").strip().lower(): c["codigo"] for c in conds}

    clave = lambda cli, r: (str(cli), r["CodTipoComprobante"], r["CodPdv"],
                            r["NumeroComprobante"], r["NumeroCuota"])

    vta30 = defaultdict(float)
    for v in ventas:
        if v["ok"] and v["t"] in ("VEN", "DEB"):
            vta30[str(v["c"])] += float(v["imp"] or 0)

    tramos, creditos, nombres = [], [], {}
    for r in detallado:
        cod = str(r["CodCliente"])
        nombres.setdefault(cod, r)
        cuota = r.get("NumeroCuota")
        cp = r.get("CP") or desc2cond.get((r.get("CondicionPago") or "").strip().lower(), "")
        x = {
            "c": cod, "e": str(r.get("CodEmpresa") or ""),
            "t": r["CodTipoComprobante"],
            "n": "%04d-%08d" % (int(r.get("CodPdv") or 0), int(r.get("NumeroComprobante") or 0))
                 + (" c%d" % cuota if cuota and cuota > 1 else ""),
            "f": _iso(r.get("FechaComprobante")),
            "v": _iso(r.get("FechaVencimientoDeuda")),
            "cp": cp,
            "imp": round(float(r.get("ImporteOriginal") or 0), 2),
            "s": round(float(r.get("Importe") or 0), 2),
            "vd": str(r.get("CodVendedor") or ""),
            "rep": str(r.get("Reparto") or ""),
            "ch": (r.get("Chofer") or "").strip(),
            "com": (r.get("Comentario") or "").strip(),
        }
        if x["s"] > 0:
            x["v"] = x["v"] or x["f"]
            tramos.append(x)
        elif x["s"] < 0:
            creditos.append(x)

    por_cli = defaultdict(lambda: [0.0, 0.0, None])
    for t in tramos:
        a = por_cli[t["c"]]
        a[0] += t["s"]
        a[2] = min(a[2], t["v"]) if a[2] else t["v"]
    for t in creditos:
        por_cli[t["c"]][1] -= t["s"]

    cli = {}
    for cod, (deuda, cred, vmin) in por_cli.items():
        m, r = maestro.get(cod, {}), nombres[cod]
        rp = (m.get("rutasPreventa") or [{}])[0] or {}
        tel = m.get("telefono") or m.get("contacto") or (r.get("TelefonoCliente") or "").strip("- ")
        cli[cod] = {
            "n": (r.get("NombreCliente") or m.get("nombre") or cod).strip(),
            "rs": (m.get("razonSocial") or "").strip(),
            "doc": m.get("nroDocumento") or "",
            "dir": m.get("direccionEntrega") or (r.get("CalleCliente") or "").strip(),
            "loc": m.get("localidad") or (r.get("LocCliente") or "").strip(),
            "tel": tel or "",
            "mail": m.get("email") or "",
            "cp": m.get("codigoCondicionPago") or "",
            "tope": m.get("topeSaldoDias"),
            "ruta": str(rp.get("codigoRuta") or ""),
            "vend": str(rp.get("codigoVendedor") or r.get("CodVendedor") or ""),
            "emp": str(m.get("codigoEmpresa") or ""),
            "tags": m.get("tags") or [],
            "activo": bool(m),
            "padre": padre_de.get(cod, cod),
            "saldo": round(deuda, 2), "cred": round(cred, 2), "neto": round(deuda - cred, 2),
            "vmin": vmin,
            "vta30": round(vta30.get(cod, 0), 2),
        }
    # nombre de las cuentas madre que no tienen saldo propio
    padres = {c["padre"] for c in cli.values()}
    nom_padre = {p: (cli[p]["n"] if p in cli else (maestro.get(p, {}).get("nombre") or p).strip())
                 for p in padres}

    for t in tramos:
        t["r"] = cli[t["c"]]["ruta"]
    tramos.sort(key=lambda t: (t["v"] or "", t["c"]))
    creditos.sort(key=lambda t: (t["f"] or "", t["c"]))

    return {
        "generado": dt.datetime.now(ART).strftime("%Y-%m-%dT%H:%M"),
        "hoy": hoy.isoformat(),
        "fuente": "GesCom API · ctacte v4 (cuenta corriente) + distribucion get-repartos",
        "empresas": {"1": "Tienda Perfecta", "99": "TP Pex", "2": "Rambla"},
        "condiciones": {c["codigo"]: {"d": c.get("descripcion") or c["codigo"],
                                       "dias": int(c.get("dias") or 0)} for c in conds},
        "vendedores": {str(v["codigo"]): v["nombre"] for v in vends},
        "padres": nom_padre,
        "clientes": cli,
        "tramos": tramos,
        "creditos": creditos,
    }


def cifrar(obj, clave):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    sal, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal,
                     iterations=ITER).derive(clave.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "iter": ITER, "salt": b64(sal), "iv": b64(iv), "ct": b64(ct)}


def main():
    clave = os.environ.get("CTACTE_CLAVE", "").strip()
    if not gescom.hay_credenciales() or not clave:
        print("Faltan credenciales de GesCom o CTACTE_CLAVE: no se refresca.")
        return 0
    data = armar(*bajar())
    # Token de GitHub (fine-grained, solo "Actions: read and write" de este repo)
    # para el boton "Actualizar" del panel. Viaja dentro del JSON cifrado.
    disp = os.environ.get("CTACTE_DISPATCH", "").strip()
    if disp:
        data["dispatch"] = disp
    OUT.mkdir(exist_ok=True)
    (OUT / "data.enc.json").write_text(json.dumps(cifrar(data, clave)), encoding="utf-8")
    (OUT / "fecha.txt").write_text("Ultima actualizacion: %s\n" % data["hoy"], encoding="utf-8")
    cl = data["clientes"].values()
    print("ctacte: %d clientes, %d comprobantes deuda, %d creditos, deuda $%.2f, creditos $%.2f, neto $%.2f"
          % (len(data["clientes"]), len(data["tramos"]), len(data["creditos"]),
             sum(c["saldo"] for c in cl), sum(c["cred"] for c in cl), sum(c["neto"] for c in cl)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
