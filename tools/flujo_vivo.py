"""Flujo de Caja TP-RL -> flujo/vivo.enc.json  (corre en GitHub Actions, workflow flujo-vivo.yml)

La "recaudacion real" del panel es la suma de:
  - Caja de Tesoreria: ingresos del dia de la planilla Finanzas_Diarias (Apps Script de la caja,
    accion 'resumenMes'). Mientras esa accion no este publicada, la caja sale de las planillas
    de W:\\Tesoreria que mete tools/flujo.py en data.enc.json, o de importarla a mano en el panel.
  - Control de Transferencias: las transferencias (tipo de valor TRANS) que declaro cada fletero
    en la rendicion de su reparto. Se leen de la BASE PROPIA (/api/rendicion-valores), que es de
    donde las lee el panel de Control de Transferencias. No se le pregunta nada a Gescom.

El repo es publico: sale cifrado con FLUJO_CLAVE (en minusculas, igual que tools/flujo.py).
En el registro de la Action solo se imprimen cantidades, nunca importes ni nombres.

Variables:
    BASE_CLAVE     clave de lectura de la base propia (secreto ya existente)
    FLUJO_CLAVE    clave del panel
    CAJA_URL       (opcional) URL /exec del Apps Script de la caja
    CAJA_SECRETO   (opcional) secreto de la accion resumenMes
    FLUJO_DESDE    (opcional) primer dia a leer, por defecto 2026-08-01 (inicio de la base)
"""
import base64
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "flujo"
ART = dt.timezone(dt.timedelta(hours=-3))
BASE = "https://base.tienda-perfecta.workers.dev"
ITER = 200_000
TRANSFERENCIA = "TRANS"   # mismo filtro que TIPOS_VERIFICABLES del Control de Transferencias


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


def tramos(desde, hasta, dias=31):
    a = desde
    while a <= hasta:
        b = min(hasta, a + dt.timedelta(days=dias - 1))
        yield a, b
        a = b + dt.timedelta(days=1)


def base_get(s, ruta, clave, **params):
    r = s.get(BASE + ruta, params=params, headers={"x-clave": clave}, timeout=120)
    if r.status_code == 401:
        raise SystemExit("La base rechazo BASE_CLAVE (401).")
    r.raise_for_status()
    return r.json()


def transferencias(s, clave, desde, hasta):
    """Por dia del reparto: lista de [ruta, fletero, importe, cantidad] (solo TRANS), mas
    los totales por tipo de valor y los repartos cerrados que todavia no tienen rendicion."""
    por_reparto = {}
    tipos = defaultdict(lambda: defaultdict(float))
    con_rendicion = set()
    filas = 0
    for a, b in tramos(desde, hasta):
        for v in base_get(s, "/api/rendicion-valores", clave, desde=a.isoformat(), hasta=b.isoformat()):
            filas += 1
            fecha = (v.get("fecha") or "")[:10]
            if not fecha:
                continue
            rid = v.get("reparto_id")
            con_rendicion.add(rid)
            tipo = str(v.get("tipo_valor") or "").upper()
            imp = float(v.get("importe") or 0)
            tipos[fecha][tipo] += imp
            if tipo != TRANSFERENCIA or not imp:
                continue
            k = (fecha, rid)
            if k not in por_reparto:
                por_reparto[k] = [(v.get("ruta") or "").strip(), (v.get("nombre_chofer") or "").strip(), 0.0, 0]
            por_reparto[k][2] += imp
            por_reparto[k][3] += 1

    # repartos rendidos/cerrados que la base todavia no tiene: el panel lo avisa
    sin = defaultdict(int)
    total_rep = 0
    for a, b in tramos(desde, hasta):
        for r in base_get(s, "/api/repartos", clave, desde=a.isoformat(), hasta=b.isoformat()):
            if r.get("cancelado") or r.get("borrado"):
                continue
            total_rep += 1
            etapa = str(r.get("etapa") or "")
            if (r.get("cerrado") or "rendicion" in etapa.lower()) and r.get("id") not in con_rendicion:
                sin[(r.get("fecha") or "")[:10]] += 1

    out = defaultdict(list)
    for (fecha, _rid), (ruta, fletero, imp, n) in sorted(por_reparto.items()):
        out[fecha].append([ruta, fletero, round(imp, 2), n])
    print("rendicion-valores: %d filas, %d repartos con rendicion, %d dias con transferencias; "
          "repartos en la base: %d, cerrados sin rendicion: %d"
          % (filas, len(con_rendicion), len(out), total_rep, sum(sin.values())))
    print("tipos de valor vistos:", sorted({t for d in tipos.values() for t in d}))
    return (dict(out),
            {f: {t: round(x, 2) for t, x in d.items()} for f, d in tipos.items()},
            dict(sin))


def caja(s, desde, hasta):
    """Ingresos de la caja por dia, del Apps Script (accion resumenMes). Si la accion todavia
    no esta publicada devuelve None y el panel sigue con lo que trae data.enc.json."""
    url, secreto = os.environ.get("CAJA_URL", "").strip(), os.environ.get("CAJA_SECRETO", "").strip()
    if not url or not secreto:
        print("caja: sin CAJA_URL/CAJA_SECRETO, se saltea")
        return None
    # solo el mes en curso y el anterior: los meses viejos ya vienen de las planillas de
    # W:\Tesoreria (data.enc.json) y cada mes le cuesta al Apps Script abrir una planilla
    out, meses = {}, []
    previo = (hasta.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    desde = max(desde, previo)
    y, m = desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        meses.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for y, m in meses:
        try:
            # text/plain como la app de la caja: el Apps Script lee e.postData.contents
            r = s.post(url, data=json.dumps({"accion": "resumenMes", "secreto": secreto, "anio": y, "mes": m}),
                       headers={"Content-Type": "text/plain;charset=utf-8"}, timeout=120)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print("caja %d-%02d: no respondio (%s)" % (y, m, type(e).__name__))
            continue
        if d.get("error"):
            print("caja %d-%02d: %s" % (y, m, d["error"][:120]))
            continue
        dias = d.get("dias") or {}
        for fecha, items in dias.items():
            out[fecha] = [[str(x[0])[:60], str(x[1])[:40], round(float(x[2]), 2)] for x in items]
        print("caja %d-%02d: %d dias con ingresos" % (y, m, len(dias)))
    return out


def main():
    clave_base = os.environ.get("BASE_CLAVE", "").strip()
    clave = os.environ.get("FLUJO_CLAVE", "").strip().lower()
    if not clave_base or not clave:
        raise SystemExit("Faltan BASE_CLAVE o FLUJO_CLAVE: no se refresca.")
    hoy = dt.datetime.now(ART).date()
    desde = dt.date.fromisoformat(os.environ.get("FLUJO_DESDE", "2026-08-01"))
    s = requests.Session()
    transf, tipos, sin = transferencias(s, clave_base, desde, hoy)
    if not transf:
        # mejor dejar el archivo anterior que publicar uno vacio
        raise SystemExit("La base no devolvio ninguna transferencia: se deja el archivo anterior.")
    datos = {
        "generado": dt.datetime.now(ART).isoformat(timespec="seconds"),
        "desde": desde.isoformat(),
        "hasta": hoy.isoformat(),
        "transferencias": transf,
        "valoresPorTipo": tipos,
        "sinRendicion": sin,
    }
    cj = caja(s, desde, hoy)
    if cj is not None:
        datos["caja"] = cj
    OUT.mkdir(exist_ok=True)
    (OUT / "vivo.enc.json").write_text(json.dumps(cifrar(datos, clave)), encoding="utf-8")
    print("listo: flujo/vivo.enc.json (%s)" % datos["generado"])


if __name__ == "__main__":
    main()
