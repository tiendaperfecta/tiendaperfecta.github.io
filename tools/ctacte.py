#!/usr/bin/env python3
"""
ctacte.py — Cuentas corrientes de clientes, desde los reportes de GesCom.

Usa los mismos reportes que se ven en GesCom > Cta. Cte. Clientes > Reportes,
ejecutados por la API (POST /data/cmd/report/render), asi que el panel
coincide comprobante por comprobante con lo que muestra GesCom:

    "Saldo actual detallado por cliente"     (442968d3-...)  cada comprobante
         pendiente: tipo, numero, cuota, importe original, saldo, fecha,
         vencimiento, dias de vencido, vendedor, reparto, chofer, comentario
    "Saldo detallado por Cliente Asociados"  (a33d4d21-...)  los mismos
         comprobantes con la cuenta madre (PADRE) de cada cliente asociado

Ademas:
    POST /data/cmd/ctacte.clientes/get-info     clienteId -> codigo (para
                                                traducir la cuenta madre)
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

REP_DETALLADO = "442968d3-f5b5-4eb7-af7f-a35e16be1baf"
REP_ASOCIADOS = "a33d4d21-3dc6-4b22-91fa-acbb53ec2991"


def _post(s, tok, ruta, body, timeout=300):
    r = s.post(gescom.API + "/data/cmd/" + ruta, json=body,
               headers={"Authorization": "Bearer " + tok}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def reporte(s, tok, rid):
    """Ejecuta un reporte de GesCom con los parametros por defecto (todos los
    clientes, todos los vendedores) y devuelve las filas como dicts."""
    info = _post(s, tok, "report/info", {"id": rid}, 60)
    params = {p["name"]: p.get("default")
              for g in info.get("parametersGroups") or [] for p in g["parameters"]}
    res = _post(s, tok, "report/render",
                {"id": rid, "reportInput": {"filtersInput": {}, "parameters": params}})
    tabla = next(d for d in res["datasources"] if d["name"] == res["mainDatasource"])["table"]
    return [dict(zip(tabla[0], fila)) for fila in tabla[1:]]


def bajar():
    s = requests.Session()
    tok = gescom._token(s)
    detallado = reporte(s, tok, REP_DETALLADO)
    asociados = reporte(s, tok, REP_ASOCIADOS)
    tok = gescom._token(s)
    info = _post(s, tok, "ctacte.clientes/get-info", {}, 180)
    clientes = gescom._traer(s, tok, "ventas/api/v1/get-clientes")
    conds = gescom._traer(s, tok, "ventas/api/v1/get-condiciones-pago", 60)
    vends = gescom._traer(s, tok, "ventas/api/v1/get-vendedores", 60)
    # compras de los ultimos 30 dias (para "sigue comprando con deuda")
    hoy = dt.datetime.now(ART).date()
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
    return detallado, asociados, info, clientes, conds, vends, ventas


def _iso(f):
    """'27-01-2026' o '2026-01-27T00:00:00' -> '2026-01-27'."""
    if not f:
        return None
    f = str(f)[:10]
    if f[2] == "-":
        return f[6:10] + "-" + f[3:5] + "-" + f[0:2]
    return f


def armar(detallado, asociados, info, clientes, conds, vends, ventas):
    hoy = dt.datetime.now(ART).date()
    maestro = {str(c.get("codigo")): c for c in clientes}
    id2cod = {int(x["clienteId"]): str(x["clienteCodigo"]) for x in info if x.get("clienteId")}
    desc2cond = {(c.get("descripcion") or "").strip().lower(): c["codigo"] for c in conds}

    clave = lambda cli, r: (str(cli), r["CodTipoComprobante"], r["CodPdv"],
                            r["NumeroComprobante"], r["NumeroCuota"])
    padre_de = {}
    for r in asociados:
        hijo = str(r.get("Clientehijo") or r.get("CodCliente"))
        p = r.get("PADRE")
        if p:
            padre_de[hijo] = id2cod.get(int(p), str(int(p) - 100000))

    vta30 = defaultdict(float)
    for v in ventas:
        if v["ok"] and v["t"] in ("VEN", "DEB"):
            vta30[str(v["c"])] += float(v["imp"] or 0)

    tramos, creditos, nombres = [], [], {}
    for r in detallado:
        cod = str(r["CodCliente"])
        nombres.setdefault(cod, r)
        cuota = r.get("NumeroCuota")
        cp = desc2cond.get((r.get("CondicionPago") or "").strip().lower(), "")
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
        "fuente": "GesCom · Saldo actual detallado por cliente + Saldo detallado por Cliente Asociados",
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
