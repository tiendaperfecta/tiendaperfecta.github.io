#!/usr/bin/env python3
"""
georgalos.py — Datos del Panel de ventas de Georgalos (georgalos/*.json).

Baja de GesCom la venta de Georgalos y la guarda dia por dia, para que el
panel calcule cualquier rango de fechas del año:

    georgalos/meses/AAAA-MM.json  una fila por (dia, vendedor, cliente, marca)
                                  con bruta, neta, costo y unidades
    georgalos/maestro.json        clientes (segmento, datos de contacto),
                                  ruta de cada vendedor y nombres
    georgalos/indice.json         meses disponibles y fecha de actualizacion

El panel (georgalos/index.html) arma a partir de eso, para el rango elegido:

    por vendedor   venta, descuento, margen y cobertura, en total y por marca
    por taxonomia  venta y clientes no compradores por segmento A/B/C/D

Fuentes (todas de la API de GesCom, las mismas que usa tools/gescom.py):

    ventas/api/v2/get          ventas con sus items (paginado, filtro por fecha
                               de creacion, fechahasta exclusiva)
    inventario/api/v2/get-articulos   para saber que articulo es de Georgalos
                               (codigoProveedor 101) y su marca
    ventas/api/v1/get-clientes segmento y ruta de preventa (el universo de
                               cada vendedor)
    ventas/api/v1/get-vendedores  nombres

Formulas (las mismas del tablero de Mar del Plata, verificadas contra el
instructivo):

    bruta     = precioUnitario x cantidad
    neta      = importeNeto            (lo que se muestra como "Venta")
    descuento = bruta - neta           % desc = descuento / bruta
    margen    = neta - costo           % margen = margen / neta
    costo     = precioCosto x cantidad

Devoluciones (DEV-RE, DEV-CA, AJU-MEN, COM-PD) restan. Todo lo que no esta en
SIGNO se ignora.

Credenciales: GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD.
Sin credenciales no hace nada y quedan publicados los datos anteriores.

Uso:
    python tools/georgalos.py                 mes en curso (y el anterior los
                                              primeros 7 dias, por lo que se
                                              factura tarde)
    python tools/georgalos.py 2026-03 2026-04 esos meses
    python tools/georgalos.py --anio 2026     todos los meses del año hasta hoy
"""
import datetime as dt
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402

DIR = Path(__file__).resolve().parent.parent / "georgalos"
PROVEEDOR_GEORGALOS = "101"
TZ_AR = dt.timezone(dt.timedelta(hours=-3))

SIGNO = {
    "VEN": 1, "AJU-MAS": 1, "DEB": 1, "SC": 1, "COM-P": 1,
    "DEV-RE": -1, "DEV-CA": -1, "AJU-MEN": -1, "COM-PD": -1,
}
# Codigos que no son vendedores de calle (deposito, pruebas, usuario de
# sistema). Sus ventas cuentan en el total pero no tienen fila propia.
NO_VENDEDORES = {"20", "80", "1018", "1020", "22", "50", "1176"}
SEGMENTOS = ["A", "B", "C", "D"]
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

# El campo codigoMarca del maestro vale "1" para todo Georgalos, asi que la
# marca se deduce de la descripcion. El orden importa: gana la primera regla.
MARCAS = [
    ("Mantecol", r"MANTECOL"),
    ("Toddy", r"TODDY"),
    ("Full Maní", r"FULL\s*MAN"),
    ("Fullmint", r"FULLMINT"),
    ("Flow", r"\bFLOW\b|GRANOLA"),
    ("Flynn Paff", r"FLYN|FPAFF"),
    ("Palitos de la Selva", r"PALITOS|PAL\.?\s*D[E]?\s*LA\s*SELVA"),
    ("Bazooka", r"BAZZ?OOKA"),
    ("Lenguetazo", r"LENGUETAZO"),
    ("Nucrem", r"NUCREM"),
    ("Tokke", r"TOKKE"),
    ("Pequeños Placeres", r"PEQ"),
    ("Zucoa", r"ZUCOA"),
    ("Daqui", r"DAQUI"),
    ("Colmenita", r"COLMENITA"),
    ("Suchard", r"SUCHARD"),
    ("Chocolates Georgalos", r"CHOC|\bGEO\b|GEOR"),
    ("Turrones y garrapiñadas", r"TURRON|GARRAP|CROCANTE|PELADILLA|\bMANI\b"),
    ("Pan dulce", r"PAN DULCE"),
    ("Combos y exhibidores", r"COMBO|\bKIT\b|EXH"),
]
MARCAS_RE = [(m, re.compile(p, re.I)) for m, p in MARCAS]
OTRAS = "Otras"


def marca_de(descripcion):
    for m, rx in MARCAS_RE:
        if rx.search(descripcion or ""):
            return m
    return OTRAS


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def cod(v):
    return str(v if v is not None else "").strip()


# --------------------------------------------------------------------------- #
# GesCom
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self.s = requests.Session()
        self._tok, self._t = None, 0

    def get(self, path, params=None, timeout=180):
        # El token dura 300 s; se renueva a los 240 por las dudas.
        ahora = dt.datetime.now().timestamp()
        if not self._tok or ahora - self._t > 240:
            self._tok, self._t = gescom._token(self.s), ahora
        # GesCom a veces responde 502/504 o corta la conexion: se reintenta.
        for intento in range(4):
            try:
                r = self.s.get(gescom.API + path, params=params, timeout=timeout,
                               headers={"Authorization": "Bearer " + self._tok,
                                        "Accept": "application/json"})
                if r.status_code < 500:
                    break
            except requests.ConnectionError:
                if intento == 3:
                    raise
            time.sleep(10 * (intento + 1))
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else (d.get("data") or [])

    def ventas(self, desde, hasta_excl):
        # La API responde 400 si se pagina mas alla de 10.000 registros, asi
        # que se pide de a 7 dias.
        todas, d, fin = [], dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta_excl)
        while d < fin:
            h = min(d + dt.timedelta(days=7), fin)
            skip = 0
            while True:
                pag = self.get("/data/cmd/ventas/api/v2/get",
                               {"fechadesde": d.isoformat(), "fechahasta": h.isoformat(),
                                "pagesize": 500, "pagestotake": 2, "pagestoskip": skip})
                todas.extend(pag)
                if len(pag) < 1000:
                    break
                skip += 2
            d = h
        return todas


def fecha_venta(v):
    """Fecha del comprobante; si todavia no se facturo, la del pedido."""
    comp = v.get("comprobantePrincipal") or {}
    f = comp.get("fechaComprobante") or v.get("fechaPedido") or ""
    return f[:10], bool(comp.get("fechaComprobante"))


# --------------------------------------------------------------------------- #
# Armado
# --------------------------------------------------------------------------- #
def maestro_articulos(articulos):
    """(codigo, empresa) -> marca, solo para articulos de Georgalos.

    La Pex (99) usa el maestro de la empresa 1 y hay articulos cargados solo en
    la otra empresa: se prueba la propia y despues la otra (ver
    connector/README.md del tablero MdP)."""
    geo = {}
    for a in articulos:
        if cod(a.get("codigoProveedor")) == PROVEEDOR_GEORGALOS:
            geo[(cod(a.get("codigo")), cod(a.get("codigoEmpresa")))] = marca_de(a.get("descripcion"))
    todos = {(cod(a.get("codigo")), cod(a.get("codigoEmpresa"))) for a in articulos}

    def marca(codigo, empresa):
        e = "1" if empresa == "99" else empresa
        otra = "2" if e == "1" else "1"
        clave = (codigo, e) if (codigo, e) in todos else (codigo, otra)
        return geo.get(clave)
    return marca


def filas_mes(ventas, marca, mes):
    """Filas [dia, vendedor, cliente, marca, bruta, neta, costo, unidades,
    sin_facturar] de las ventas con fecha dentro de `mes` (AAAA-MM)."""
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for v in ventas:
        signo = SIGNO.get(cod(v.get("codigoTipoVenta")))
        if signo is None or cod(v.get("estado")).lower().startswith("anul"):
            continue
        fecha, facturada = fecha_venta(v)
        if fecha[:7] != mes:
            continue
        emp, vend, cli = cod(v.get("codigoEmpresa")), cod(v.get("codigoVendedor")), cod(v.get("codigoCliente"))
        for it in v.get("items") or []:
            m = marca(cod(it.get("codigoItem")), emp)
            if m is None:
                continue
            q = num(it.get("cantidad"))
            x = acc[(int(fecha[8:10]), vend, cli, m, 0 if facturada else 1)]
            x[0] += num(it.get("precioUnitario")) * q * signo
            x[1] += num(it.get("importeNeto")) * signo
            x[2] += num(it.get("precioCosto")) * q * signo
            x[3] += q * num(it.get("unidadFactor") or 1) * signo
    return [[d, v, c, m, round(b, 2), round(n, 2), round(k, 2), round(u, 3), sf]
            for (d, v, c, m, sf), (b, n, k, u) in sorted(acc.items())]


def construir_maestro(clientes, vendedores):
    cli, rutas = {}, defaultdict(dict)
    for c in clientes:
        cc = cod(c.get("codigo"))
        if not cc:
            continue
        cli[cc] = [(c.get("nombre") or c.get("razonSocial") or "").strip(),
                   (c.get("direccionEntrega") or "").strip(),
                   (c.get("localidad") or "").strip(),
                   (c.get("telefono") or "").strip(),
                   cod(c.get("codigoSegmento")).upper() or "—"]
        for r in c.get("rutasPreventa") or []:
            v = cod(r.get("codigoVendedor"))
            if not v or v in NO_VENDEDORES:
                continue
            dias = [d.capitalize() for d in DIAS if r.get(d)]
            previo = rutas[v].get(cc)
            rutas[v][cc] = ", ".join(filter(None, [previo] + [", ".join(dias)])) if previo else ", ".join(dias)
    return {
        "campos": ["nombre", "direccion", "localidad", "telefono", "segmento"],
        "clientes": cli,
        "rutas": rutas,
        "vendedores": {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores},
        "noVendedores": sorted(NO_VENDEDORES),
    }


def escribir(nombre, data):
    ruta = DIR / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def meses_a_bajar(args, hoy):
    if not args:
        meses = [hoy.strftime("%Y-%m")]
        if hoy.day <= 7:
            meses.insert(0, (hoy.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"))
        return meses
    if args[0] == "--anio":
        anio = int(args[1])
        ultimo = hoy.month if anio == hoy.year else 12
        return ["%d-%02d" % (anio, m) for m in range(1, ultimo + 1)]
    return args


def main():
    if not gescom.hay_credenciales():
        print("Sin credenciales de GesCom: quedan publicados los datos anteriores.")
        return 0
    hoy = dt.datetime.now(TZ_AR).date()
    meses = meses_a_bajar(sys.argv[1:], hoy)

    api = Api()
    marca = maestro_articulos(api.get("/data/cmd/inventario/api/v2/get-articulos"))
    escribir("maestro.json", construir_maestro(
        api.get("/data/cmd/ventas/api/v1/get-clientes"),
        api.get("/data/cmd/ventas/api/v1/get-vendedores")))

    for mes in meses:
        inicio = dt.date.fromisoformat(mes + "-01")
        fin = (inicio + dt.timedelta(days=32)).replace(day=1)
        # Filtro de la API = fecha de creacion (fechahasta exclusiva). Se
        # arrastra desde 8 dias antes para no perder lo creado a fin del mes
        # anterior y facturado en este.
        desde = (inicio - dt.timedelta(days=8)).isoformat()
        hasta = min(fin, hoy + dt.timedelta(days=1)).isoformat()
        filas = filas_mes(api.ventas(desde, hasta), marca, mes)
        escribir("meses/%s.json" % mes, filas)
        print("Georgalos %s: %d filas, venta $%.0f" % (mes, len(filas), sum(f[5] for f in filas)))

    disponibles = sorted(p.stem for p in (DIR / "meses").glob("*.json"))
    escribir("indice.json", {"meses": disponibles,
                             "generado": dt.datetime.now(TZ_AR).isoformat(timespec="minutes")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
