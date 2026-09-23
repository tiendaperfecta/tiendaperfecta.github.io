#!/usr/bin/env python3
"""
ctacte.py — Cuentas corrientes de clientes a partir de GesCom.

La API de integracion de GesCom (usuario de servicio) NO expone el modulo de
cuentas corrientes ni las cobranzas/recibos: se probaron ~2.000 rutas
(/data/cmd, /data/qry, tesoreria, ventas, ...) y solo responden ventas,
clientes, condiciones de pago y catalogos. Por eso el saldo se reconstruye asi:

    factura a plazo  = venta VEN/DEB con condicion de pago de mas de 0 dias
    vencimiento      = fecha del comprobante + dias de la condicion de la venta
    saldo            = total de la factura - notas de credito / devoluciones
                       que la referencian (ventaReferenciada)
                       - cobros (si hay ctacte/cobros.json, ver abajo)

Los cobros se descuentan cuando exista la fuente: si alguien sube
ctacte/cobros.json (lista de {"cliente","importe"} o {"venta","importe"}) se
aplican primero contra la venta indicada y si no FIFO contra las facturas mas
viejas del cliente. Sin ese archivo el panel lo avisa: la deuda es la
facturada a plazo en la ventana, sin cobros imputados.

Exclusiones (las mismas del tablero de rentabilidad): vendedor 1176 y clientes
con "1176" en el codigo (traspasos intercompany).

El repo es publico: el JSON sale cifrado con AES-256-GCM, clave derivada con
PBKDF2-SHA256 de CTACTE_CLAVE (GitHub Secret). El panel la pide y descifra en
el navegador. Sin la clave el archivo es ilegible.

Variables de entorno:
    GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
    CTACTE_CLAVE
    CTACTE_DIAS   ventana en dias hacia atras (default 120)

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

SUMAN = {"VEN", "DEB"}
RESTAN = {"DEV-RE", "DEV-CA", "AJU-MEN"}
EXCLUIR = "1176"
ITER = 200_000


def _fecha(s):
    return (s or "")[:10] or None


def bajar(dias):
    s = requests.Session()
    tok = gescom._token(s)
    hoy = dt.datetime.now(ART).date()
    desde = hoy - dt.timedelta(days=dias)
    hasta = hoy + dt.timedelta(days=1)          # fechahasta es exclusiva
    ventas, skip = [], 0
    while True:
        # el token dura 300 s: uno nuevo cada tanto
        if skip and skip % 8 == 0:
            tok = gescom._token(s)
        pag = gescom._traer(
            s, tok,
            "ventas/api/v2/get?fechadesde=%s&fechahasta=%s&pagesize=500"
            "&pagestotake=2&pagestoskip=%d" % (desde, hasta, skip), timeout=300)
        if not pag:
            break
        for v in pag:
            v.pop("items", None)
            v.pop("combos", None)
        ventas += pag
        skip += 2
        print("ventas: %d" % len(ventas), file=sys.stderr)
    tok = gescom._token(s)
    clientes = gescom._traer(s, tok, "ventas/api/v1/get-clientes")
    conds = gescom._traer(s, tok, "ventas/api/v1/get-condiciones-pago", 60)
    vends = gescom._traer(s, tok, "ventas/api/v1/get-vendedores", 60)
    return desde, ventas, clientes, conds, vends


def armar(desde, ventas, clientes, conds, vends, cobros=None):
    hoy = dt.datetime.now(ART).date()
    cdias = {c["codigo"]: int(c.get("dias") or 0) for c in conds}
    excl = lambda v: (str(v.get("codigoVendedor")) == EXCLUIR
                      or EXCLUIR in str(v.get("codigoCliente") or ""))

    facturas, notas = {}, []
    for v in ventas:
        if excl(v) or not v.get("codigoEmpresa"):
            continue
        cp = v.get("comprobantePrincipal") or {}
        if cp.get("estado") in ("Rechazado",):
            continue
        tipo = v.get("codigoTipoVenta")
        if tipo in SUMAN and cdias.get(v.get("codigoCondicionPago"), 0) > 0:
            if not cp.get("numeroComprobante"):
                continue            # todavia sin facturar
            f = _fecha(cp.get("fechaComprobante")) or _fecha(v.get("fechaEntrega"))
            d = cdias[v["codigoCondicionPago"]]
            venc = (dt.date.fromisoformat(f) + dt.timedelta(days=d)).isoformat()
            facturas[v["id"]] = {
                "id": v["id"], "c": str(v["codigoCliente"]), "e": str(v["codigoEmpresa"]),
                "t": cp.get("codigoTipoComprobante") or tipo,
                "n": "%04d-%08d" % (int(v.get("codigoPuntoVenta") or 0),
                                     int(cp.get("numeroComprobante") or 0)),
                "f": f, "v": venc, "cp": v["codigoCondicionPago"], "d": d,
                "vd": str(v.get("codigoVendedor") or ""), "r": str(v.get("codigoRuta") or ""),
                "imp": round(float(v.get("importeTotal") or 0), 2),
                "nc": 0.0, "co": 0.0,
            }
        elif tipo in RESTAN:
            notas.append(v)

    # notas de credito / devoluciones: contra la factura que referencian, o
    # FIFO contra el cliente si no referencian ninguna factura a plazo
    por_cli = defaultdict(list)
    for f in facturas.values():
        por_cli[f["c"]].append(f)
    for lst in por_cli.values():
        lst.sort(key=lambda f: (f["f"], f["id"]))

    def aplicar(lst, monto, campo):
        for f in lst:
            if monto <= 0.005:
                break
            libre = f["imp"] - f["nc"] - f["co"]
            if libre <= 0.005:
                continue
            x = min(libre, monto)
            f[campo] = round(f[campo] + x, 2)
            monto -= x
        return monto

    for n in notas:
        monto = abs(float(n.get("importeTotal") or 0))
        ref = (n.get("ventaReferenciada") or {}).get("id")
        if ref in facturas:
            aplicar([facturas[ref]], monto, "nc")
        elif ref is None and str(n.get("codigoCliente")) in por_cli:
            aplicar(por_cli[str(n["codigoCliente"])], monto, "nc")

    for co in cobros or []:
        monto = float(co.get("importe") or 0)
        if co.get("venta") in facturas:
            monto = aplicar([facturas[co["venta"]]], monto, "co")
        cli = str(co.get("cliente") or (facturas.get(co.get("venta")) or {}).get("c") or "")
        if cli in por_cli:
            aplicar(por_cli[cli], monto, "co")

    comps = []
    for f in facturas.values():
        f["s"] = round(f["imp"] - f["nc"] - f["co"], 2)
        if f["s"] > 1:
            comps.append(f)
    comps.sort(key=lambda f: (f["v"], f["c"]))

    usados = {f["c"] for f in facturas.values()}
    cli = {}
    for c in clientes:
        cod = str(c.get("codigo"))
        cpago = c.get("codigoCondicionPago") or ""
        if cod not in usados and cdias.get(cpago, 0) <= 0:
            continue        # contado y sin facturas a plazo: no interesa
        rp = (c.get("rutasPreventa") or [{}])[0] or {}
        cli[cod] = {
            "n": c.get("nombre") or c.get("razonSocial") or cod,
            "rs": c.get("razonSocial") or "",
            "doc": c.get("nroDocumento") or "",
            "dir": c.get("direccionEntrega") or "",
            "loc": c.get("localidad") or "",
            "tel": c.get("telefono") or c.get("contacto") or "",
            "mail": c.get("email") or "",
            "cp": cpago,
            "tope": c.get("topeSaldoDias"),
            "topeImp": c.get("topeSaldoDinero"),
            "ruta": str(rp.get("codigoRuta") or ""),
            "vend": str(rp.get("codigoVendedor") or ""),
            "emp": str(c.get("codigoEmpresa") or ""),
            "seg": c.get("codigoSegmento") or "",
            "tags": c.get("tags") or [],
        }

    return {
        "generado": dt.datetime.now(ART).strftime("%Y-%m-%dT%H:%M"),
        "hoy": hoy.isoformat(),
        "desde": desde.isoformat() if hasattr(desde, "isoformat") else desde,
        "cobrosImputados": bool(cobros),
        "empresas": {"1": "Tienda Perfecta", "99": "TP Pex", "2": "Rambla"},
        "condiciones": {c["codigo"]: {"d": c.get("descripcion") or c["codigo"],
                                       "dias": int(c.get("dias") or 0)} for c in conds},
        "vendedores": {str(v["codigo"]): v["nombre"] for v in vends},
        "clientes": cli,
        "comps": comps,
        "stats": {"facturas": len(facturas), "notas": len(notas), "ventas": len(ventas)},
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
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER,
            "salt": b64(sal), "iv": b64(iv), "ct": b64(ct)}


def main():
    clave = os.environ.get("CTACTE_CLAVE", "").strip()
    if not gescom.hay_credenciales() or not clave:
        print("Faltan credenciales de GesCom o CTACTE_CLAVE: no se refresca.")
        return 0
    dias = int(os.environ.get("CTACTE_DIAS") or 120)
    desde, ventas, clientes, conds, vends = bajar(dias)
    cobros = None
    fc = OUT / "cobros.json"
    if fc.exists():
        cobros = json.loads(fc.read_text(encoding="utf-8"))
    data = armar(desde, ventas, clientes, conds, vends, cobros)
    OUT.mkdir(exist_ok=True)
    (OUT / "data.enc.json").write_text(json.dumps(cifrar(data, clave)), encoding="utf-8")
    (OUT / "fecha.txt").write_text("Ultima actualizacion: %s\n" % data["hoy"], encoding="utf-8")
    print("ctacte: %d comprobantes con saldo, %d clientes, %d facturas a plazo"
          % (len(data["comps"]), len(data["clientes"]), data["stats"]["facturas"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
