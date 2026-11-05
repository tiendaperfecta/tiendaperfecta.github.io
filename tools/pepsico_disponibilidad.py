#!/usr/bin/env python3
"""
pepsico_disponibilidad.py — Datos de la pagina disponibilidad/ (Disponibilidad PepsiCo).

PepsiCo exige disponibilidad del 100%: que dentro del mes se compre, al menos una vez, cada
articulo de su lista de activos. La pagina calcula:

    disponibilidad = activos del mes con algun remito de ingreso de PepsiCo (REMI-I) con fecha
                     de comprobante dentro del mes / total de activos del mes

Este script arma los JSON que lee la pagina:

    disponibilidad/datos/AAAA-MM.json  lista de activos del mes (del Excel) + compras del mes
                                       (articulo -> fechas de los REMI-I)
    disponibilidad/datos/stock.json    stock de hoy y nombre de cada articulo PepsiCo
    disponibilidad/datos/meses.json    los meses que hay
    disponibilidad/fecha.txt           "Ultima actualizacion: AAAA-MM-DD" (lo lee la Central)

Lista mensual de activos: el Excel de PepsiCo se sube a disponibilidad/listas/ con el mes en el
nombre ("2026-11.xlsx" o "Disponible de pepsico nov 26.xlsx"). Se toma la columna
"Material SAP" como codigo y solo las filas con Vigencia = 1.

Compras: van directo a Gescom (cmd/compras/api/v1/get, proveedor 100), igual que Georgalos,
porque la base propia no guarda los renglones de las compras. Las FAC-A repiten los renglones
de los remitos y no se cuentan. Se refrescan el mes en curso y el anterior (remitos cargados
tarde); los meses viejos quedan como estaban.

Stock y nombres: de la base propia (BASE_CLAVE), sin llamar a Gescom.

Secretos: GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD y BASE_CLAVE. Lo que
no se pueda bajar se saltea y queda el dato anterior.
"""
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402  (login directo a Gescom; NO se llama a usar_base)

RAIZ = Path(__file__).resolve().parent.parent / "disponibilidad"
DATOS = RAIZ / "datos"
LISTAS = RAIZ / "listas"
BASE = "https://base.tienda-perfecta.workers.dev"
PROVEEDOR = "100"
TIPO_REMITO = "REMI-I"
AR = timezone(timedelta(hours=-3))
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def cod(v):
    """Codigo de articulo como texto: 300065477.0 -> '300065477'."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def escribir(ruta, datos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nuevo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if ruta.exists() and ruta.read_text(encoding="utf-8") == nuevo:
        return False
    ruta.write_text(nuevo, encoding="utf-8")
    return True


def leer(ruta, defecto=None):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def mes_de(d):
    return d.strftime("%Y-%m")


def mes_anterior(m):
    a, n = map(int, m.split("-"))
    return "%d-%02d" % (a - 1, 12) if n == 1 else "%d-%02d" % (a, n - 1)


# ---------------------------------------------------------------- listas de activos (Excel)

def mes_del_archivo(nombre):
    base = norm(Path(nombre).stem)
    m = re.search(r"(20\d\d)[-_ .](\d{1,2})\b", base)
    if m and 1 <= int(m.group(2)) <= 12:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = re.search(r"\b(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[a-z]*[-_ .]*(\d{2}|\d{4})\b", base)
    if m:
        anio = int(m.group(2))
        return "%d-%02d" % (anio + 2000 if anio < 100 else anio, MESES[m.group(1)])
    return None


def leer_lista(ruta):
    """Filas vigentes del Excel de PepsiCo: [{c: codigo, d: descripcion, b: BDU}]."""
    import openpyxl
    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    for hoja in libro.worksheets:
        filas = list(hoja.iter_rows(values_only=True))
        for i, fila in enumerate(filas[:30]):
            titulos = [norm(x) for x in fila]
            if "material sap" not in titulos:
                continue
            col = {"c": titulos.index("material sap")}
            for j, t in enumerate(titulos):
                if "vigencia" in t and "v" not in col:
                    col["v"] = j
                elif ("bdu" in t or "unidad de negocio" in t) and "b" not in col:
                    col["b"] = j
                elif j != col["c"] and "d" not in col and any(
                        p in t for p in ("descrip", "texto", "producto", "articulo", "nombre")):
                    col["d"] = j
            if "v" not in col:
                raise ValueError("%s: no encuentro la columna Vigencia" % ruta.name)
            activos, vistos = [], set()
            for f in filas[i + 1:]:
                c = cod(f[col["c"]]) if col["c"] < len(f) else ""
                if not c or c in vistos:
                    continue
                vig = f[col["v"]] if col["v"] < len(f) else None
                if cod(vig) != "1":
                    continue
                vistos.add(c)
                activos.append({
                    "c": c,
                    "d": str(f[col["d"]]).strip() if "d" in col and f[col["d"]] is not None else "",
                    "b": str(f[col["b"]]).strip().upper() if "b" in col and f[col["b"]] is not None else "",
                })
            return activos
    raise ValueError("%s: no encuentro la columna 'Material SAP'" % ruta.name)


def listas():
    """mes -> (nombre del archivo, activos). Si hay dos archivos del mismo mes, el mas nuevo."""
    res = {}
    for ruta in sorted(LISTAS.glob("*.xls*"), key=lambda p: p.stat().st_mtime):
        if ruta.name.startswith("~$"):
            continue
        mes = mes_del_archivo(ruta.name)
        if not mes:
            print("aviso: %s no tiene el mes en el nombre (usar AAAA-MM.xlsx); se ignora" % ruta.name)
            continue
        try:
            res[mes] = (ruta.name, leer_lista(ruta))
            print("lista %s: %s -> %d activos" % (mes, ruta.name, len(res[mes][1])))
        except Exception as e:  # una lista rota no frena el resto
            print("ERROR lista %s: %s" % (ruta.name, e))
    return res


# ---------------------------------------------------------------- compras (Gescom directo)

def compras_pepsico(desde, hasta):
    """Remitos de ingreso de PepsiCo cargados entre desde y hasta -> {mes: {codigo: [fechas]}}."""
    sesion = requests.Session()
    tok = gescom._token(sesion)
    todas, pagina = [], 0
    while True:
        r = sesion.get(gescom.API + "/data/cmd/compras/api/v1/get", timeout=180,
                       headers={"Authorization": "Bearer " + tok, "Accept": "application/json"},
                       params={"fechadesde": desde.isoformat(), "fechahasta": hasta.isoformat() + "T23:59:59",
                               "proveedores": PROVEEDOR, "pagesize": 500, "pagestoskip": pagina})
        r.raise_for_status()
        d = r.json()
        lote = d if isinstance(d, list) else (d.get("data") or [])
        todas.extend(lote)
        if len(lote) < 500 or pagina >= 20:
            break
        pagina += 1
        time.sleep(2)
    tipos = {}
    por_mes = {}
    for c in todas:
        tipo = (c.get("codigoTipoDeComprobante") or "").upper()
        tipos[tipo] = tipos.get(tipo, 0) + 1
        if cod(c.get("codigoProveedor")) not in ("", PROVEEDOR) or tipo != TIPO_REMITO:
            continue
        fecha = (c.get("fechaComprobante") or c.get("fechaCarga") or "")[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}$", fecha):
            continue
        mes = por_mes.setdefault(fecha[:7], {})
        for it in c.get("items") or []:
            codigo = cod(it.get("codigoItem"))
            cant = it.get("cantidad")
            if not codigo or (cant is not None and cant <= 0):
                continue
            fechas = mes.setdefault(codigo, [])
            if fecha not in fechas:
                fechas.append(fecha)
    print("compras PepsiCo %s a %s: %d comprobantes %s" % (desde, hasta, len(todas), tipos))
    return {m: {k: sorted(v) for k, v in arts.items()} for m, arts in por_mes.items()}


# ---------------------------------------------------------------- stock y nombres (base)

def base_get(ruta):
    for intento in range(3):
        try:
            r = requests.get(BASE + ruta, timeout=240,
                             headers={"Authorization": "Bearer " + os.environ["BASE_CLAVE"].strip()})
            if r.status_code < 500:
                break
        except requests.ConnectionError:
            if intento == 2:
                raise
        time.sleep(10 * (intento + 1))
    r.raise_for_status()
    return r.json()


def stock_pepsico():
    arts = {}
    for a in base_get("/api/catalogo/articulos"):
        if cod(a.get("codigoProveedor")) != PROVEEDOR:
            continue
        c = cod(a.get("codigo"))
        if c in arts and cod(a.get("codigoEmpresa")) != "1":
            continue   # mismo articulo en otra empresa: queda el de Tienda Perfecta
        pb = a.get("unidadesPorBulto") or a.get("unidadesBultoVenta") or 0
        arts[c] = {"d": (a.get("descripcion") or "").strip(), "pb": pb if isinstance(pb, (int, float)) and pb > 1 else 0, "u": 0}
    stock = base_get("/gescom/data/cmd/inventario/api/v1/get-stock")
    if isinstance(stock, dict):
        stock = stock.get("data") or []
    for s in stock:
        c = cod(s.get("codigoItem"))
        if c in arts:
            arts[c]["u"] += sum((x.get("cantidad") or 0) for x in s.get("stock") or [])
    for a in arts.values():
        a["u"] = round(a["u"], 2)
    print("stock: %d articulos PepsiCo" % len(arts))
    return arts


# ---------------------------------------------------------------- main

def main():
    ahora = datetime.now(AR)
    hoy = ahora.date()
    actual = mes_de(hoy)
    anterior = mes_anterior(actual)
    marca = ahora.isoformat(timespec="minutes")
    DATOS.mkdir(parents=True, exist_ok=True)

    por_lista = listas()
    meses = set(por_lista) | {actual, anterior}
    meses |= {p.stem for p in DATOS.glob("20??-??.json")}

    # Compras: el mes en curso, el anterior y cualquier mes que todavia no tenga compras bajadas.
    a_bajar = {m for m in meses if m in (actual, anterior) or not (leer(DATOS / (m + ".json")) or {}).get("compras_ok")}
    a_bajar = {m for m in a_bajar if m <= actual}
    compras, compras_ok = {}, False
    if a_bajar and gescom.hay_credenciales():
        desde = date.fromisoformat(min(a_bajar) + "-01")
        try:
            compras = compras_pepsico(desde, hoy)
            compras_ok = True
        except Exception as e:
            print("ERROR compras (quedan las anteriores): %s" % e)
    elif a_bajar:
        print("Sin credenciales de Gescom: las compras quedan como estaban.")

    cambio = False
    for m in sorted(meses):
        if m > actual:
            continue
        ruta = DATOS / (m + ".json")
        previo = leer(ruta, {}) or {}
        dato = {"mes": m, "lista": previo.get("lista"), "compras": previo.get("compras") or {},
                "compras_ok": bool(previo.get("compras_ok")), "actualizado": previo.get("actualizado")}
        if m in por_lista:
            nombre, activos = por_lista[m]
            dato["lista"] = {"archivo": nombre, "articulos": activos}
        if compras_ok and m in a_bajar:
            dato["compras"] = compras.get(m, {})
            dato["compras_ok"] = True
        sin_marca = {k: v for k, v in dato.items() if k != "actualizado"}
        if sin_marca != {k: v for k, v in previo.items() if k != "actualizado"}:
            dato["actualizado"] = marca
        cambio |= escribir(ruta, dato)

    if os.environ.get("BASE_CLAVE", "").strip():
        try:
            cambio |= escribir(DATOS / "stock.json", {"actualizado": marca, "articulos": stock_pepsico()})
        except Exception as e:
            print("ERROR stock (queda el anterior): %s" % e)
    else:
        print("Sin BASE_CLAVE: el stock queda como estaba.")

    disponibles = sorted((p.stem for p in DATOS.glob("20??-??.json")), reverse=True)
    escribir(DATOS / "meses.json", {"meses": disponibles})
    if compras_ok:
        (RAIZ / "fecha.txt").write_text("Ultima actualizacion: %s\n" % hoy.isoformat(), encoding="utf-8")
    print("listo%s" % ("" if cambio else " (sin cambios)"))


if __name__ == "__main__":
    main()
