#!/usr/bin/env python3
"""
ctacte.py — Cuentas corrientes de clientes, desde GesCom.

Fuentes (todas de la instancia tiendaperfecta):

    POST /data/cmd/ctacte.clientes/get-info     saldo real de cada cliente:
         saldo (deuda), credito (a favor), saldoTotal (neto) y
         fechaVencimientoMenor (el vencimiento impago mas viejo)
    GET  ventas/api/v2/get                      facturas de los ultimos N dias
    GET  ventas/api/v1/get-clientes             maestro: condicion de pago,
                                                tope de dias, ruta, vendedor
    GET  ventas/api/v1/get-condiciones-pago     dias de cada condicion

La API no expone el detalle de comprobantes impagos de la cuenta corriente, asi
que el saldo se reparte asi: los cobros cancelan primero lo mas viejo, entonces
el saldo del cliente corresponde a sus facturas mas nuevas. Se recorren de la
mas nueva a la mas vieja hasta cubrir el saldo; cada tramo vence en
fecha del comprobante + dias de su condicion de pago. Lo que no alcanza a
cubrirse con las facturas de la ventana queda como "saldo anterior", con el
vencimiento mas viejo que informa GesCom.

Exclusiones (las del tablero de rentabilidad): vendedor 1176 y clientes con
"1176" en el codigo (EMFACO, traspasos intercompany).

El repo es publico: el JSON sale cifrado con AES-256-GCM, clave derivada con
PBKDF2-SHA256 de CTACTE_CLAVE (GitHub Secret). El panel la pide y descifra en
el navegador.

Variables de entorno:
    GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
    CTACTE_CLAVE
    CTACTE_DIAS   ventana de facturas en dias hacia atras (default 120)

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
        if skip and skip % 8 == 0:              # el token dura 300 s
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
    r = s.post(gescom.API + "/data/cmd/ctacte.clientes/get-info", json={},
               headers={"Authorization": "Bearer " + tok}, timeout=180)
    r.raise_for_status()
    saldos = r.json()
    clientes = gescom._traer(s, tok, "ventas/api/v1/get-clientes")
    conds = gescom._traer(s, tok, "ventas/api/v1/get-condiciones-pago", 60)
    vends = gescom._traer(s, tok, "ventas/api/v1/get-vendedores", 60)
    return desde, ventas, saldos, clientes, conds, vends


def armar(desde, ventas, saldos, clientes, conds, vends):
    hoy = dt.datetime.now(ART).date()
    hace30 = (hoy - dt.timedelta(days=30)).isoformat()
    cdias = {c["codigo"]: int(c.get("dias") or 0) for c in conds}
    maestro = {str(c.get("codigo")): c for c in clientes}

    # facturas de la ventana, por cliente
    facts = defaultdict(list)
    vta30 = defaultdict(float)
    for v in ventas:
        cli = str(v.get("codigoCliente") or "")
        if (str(v.get("codigoVendedor")) == EXCLUIR or EXCLUIR in cli
                or not v.get("codigoEmpresa") or v.get("codigoTipoVenta") not in SUMAN):
            continue
        cp = v.get("comprobantePrincipal") or {}
        if not cp.get("numeroComprobante") or cp.get("estado") == "Rechazado":
            continue
        f = _fecha(cp.get("fechaComprobante")) or _fecha(v.get("fechaEntrega"))
        cond = v.get("codigoCondicionPago") or ""
        d = cdias.get(cond, 0)
        imp = round(float(v.get("importeTotal") or 0), 2)
        if f >= hace30:
            vta30[cli] += imp
        facts[cli].append({
            "id": v["id"], "c": cli, "e": str(v["codigoEmpresa"]),
            "t": cp.get("codigoTipoComprobante") or v.get("codigoTipoVenta"),
            "n": "%04d-%08d" % (int(v.get("codigoPuntoVenta") or 0),
                                 int(cp.get("numeroComprobante") or 0)),
            "f": f, "v": (dt.date.fromisoformat(f) + dt.timedelta(days=d)).isoformat(),
            "cp": cond, "imp": imp,
            "vd": str(v.get("codigoVendedor") or ""), "r": str(v.get("codigoRuta") or ""),
        })

    cli, tramos = {}, []
    for s in saldos:
        cod = str(s.get("clienteCodigo") or "")
        if not cod or EXCLUIR in cod:
            continue
        deuda = round(float(s.get("saldo") or 0), 2)
        cred = round(float(s.get("credito") or 0), 2)
        if deuda <= 1 and cred <= 1:
            continue
        m = maestro.get(cod, {})
        rp = (m.get("rutasPreventa") or [{}])[0] or {}
        vmin = _fecha(s.get("fechaVencimientoMenor"))
        cli[cod] = {
            "n": (m.get("nombre") or s.get("clienteNombre") or cod).strip(),
            "rs": (m.get("razonSocial") or "").strip(),
            "doc": m.get("nroDocumento") or "",
            "dir": m.get("direccionEntrega") or "",
            "loc": m.get("localidad") or "",
            "tel": m.get("telefono") or m.get("contacto") or "",
            "mail": m.get("email") or "",
            "cp": m.get("codigoCondicionPago") or "",
            "tope": m.get("topeSaldoDias"),
            "topeImp": m.get("topeSaldoDinero"),
            "ruta": str(rp.get("codigoRuta") or ""),
            "vend": str(rp.get("codigoVendedor") or ""),
            "emp": str(m.get("codigoEmpresa") or ""),
            "tags": m.get("tags") or [],
            "activo": bool(m),
            "saldo": deuda, "cred": cred,
            "neto": round(float(s.get("saldoTotal") or 0), 2),
            "vmin": vmin,
            "vta30": round(vta30.get(cod, 0), 2),
        }
        # repartir la deuda sobre las facturas mas nuevas
        resto = deuda
        for f in sorted(facts.get(cod, []), key=lambda f: (f["f"], f["id"]), reverse=True):
            if resto <= 1:
                break
            x = round(min(f["imp"], resto), 2)
            if x <= 0:
                continue
            tramos.append(dict(f, s=x))
            resto -= x
        if resto > 1:
            emp = cli[cod]["emp"] or next((f["e"] for f in facts.get(cod, [])), "")
            tramos.append({"id": 0, "c": cod, "e": emp, "t": "ANT", "n": "Saldo anterior",
                           "f": None, "v": vmin or desde.isoformat(), "cp": cli[cod]["cp"],
                           "imp": round(resto, 2), "s": round(resto, 2),
                           "vd": cli[cod]["vend"], "r": cli[cod]["ruta"]})
    tramos.sort(key=lambda t: (t["v"], t["c"]))

    return {
        "generado": dt.datetime.now(ART).strftime("%Y-%m-%dT%H:%M"),
        "hoy": hoy.isoformat(),
        "desde": desde.isoformat(),
        "empresas": {"1": "Tienda Perfecta", "99": "TP Pex", "2": "Rambla"},
        "condiciones": {c["codigo"]: {"d": c.get("descripcion") or c["codigo"],
                                       "dias": int(c.get("dias") or 0)} for c in conds},
        "vendedores": {str(v["codigo"]): v["nombre"] for v in vends},
        "clientes": cli,
        "tramos": tramos,
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
    data = armar(*bajar(int(os.environ.get("CTACTE_DIAS") or 120)))
    OUT.mkdir(exist_ok=True)
    (OUT / "data.enc.json").write_text(json.dumps(cifrar(data, clave)), encoding="utf-8")
    (OUT / "fecha.txt").write_text("Ultima actualizacion: %s\n" % data["hoy"], encoding="utf-8")
    print("ctacte: %d clientes, %d tramos, deuda $%.0f"
          % (len(data["clientes"]), len(data["tramos"]),
             sum(c["saldo"] for c in data["clientes"].values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
