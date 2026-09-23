#!/usr/bin/env python3
"""
georgalos.py — Datos del Panel de ventas de Georgalos (georgalos/data.json).

Baja de GesCom el mes en curso y lo reduce a lo que muestra el panel:

    por vendedor   venta, descuento, margen y cobertura, en total y por marca
    por taxonomia  venta y clientes compradores / no compradores por segmento
                   (A/B/C/D, campo codigoSegmento del cliente)

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
Sin credenciales no hace nada y queda publicado el ultimo data.json.

Uso:  python tools/georgalos.py [AAAA-MM-DD]   (fecha de corte, default hoy)
"""
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402

SALIDA = Path(__file__).resolve().parent.parent / "georgalos" / "data.json"
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
        r = self.s.get(gescom.API + path, params=params, timeout=timeout,
                       headers={"Authorization": "Bearer " + self._tok,
                                "Accept": "application/json"})
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else (d.get("data") or [])

    def ventas(self, desde, hasta_excl):
        todas, skip = [], 0
        while True:
            pag = self.get("/data/cmd/ventas/api/v2/get",
                           {"fechadesde": desde, "fechahasta": hasta_excl,
                            "pagesize": 500, "pagestotake": 2, "pagestoskip": skip})
            todas.extend(pag)
            if len(pag) < 1000:
                return todas
            skip += 2


def fecha_venta(v):
    """Fecha del comprobante; si todavia no se facturo, la del pedido."""
    comp = v.get("comprobantePrincipal") or {}
    f = comp.get("fechaComprobante") or v.get("fechaPedido") or ""
    return f[:10], bool(comp.get("fechaComprobante"))


# --------------------------------------------------------------------------- #
# Armado
# --------------------------------------------------------------------------- #
def acum():
    return {"bruta": 0.0, "neta": 0.0, "costo": 0.0}


def sumar(a, bruta, neta, costo):
    a["bruta"] += bruta
    a["neta"] += neta
    a["costo"] += costo


def redondear(a):
    return {k: round(v, 2) for k, v in a.items()}


def construir(ventas, articulos, clientes, vendedores, desde, corte):
    # Maestro: (codigo, empresa) -> articulo. La Pex (99) usa el maestro de la
    # empresa 1 y hay articulos cargados solo en la otra empresa: se prueba la
    # propia y despues la otra (ver connector/README.md del tablero MdP).
    maestro = {(cod(a.get("codigo")), cod(a.get("codigoEmpresa"))): a for a in articulos}

    def articulo(codigo, empresa):
        e = "1" if empresa == "99" else empresa
        return maestro.get((codigo, e)) or maestro.get((codigo, "2" if e == "1" else "1"))

    # Universo: clientes en la ruta de preventa de cada vendedor.
    info_cli, universo = {}, defaultdict(set)
    for c in clientes:
        cc = cod(c.get("codigo"))
        if not cc:
            continue
        seg = cod(c.get("codigoSegmento")).upper() or "—"
        dias_vend = defaultdict(list)
        for r in c.get("rutasPreventa") or []:
            v = cod(r.get("codigoVendedor"))
            if not v:
                continue
            universo[v].add(cc)
            dias_vend[v] += [d for d in DIAS if r.get(d)]
        info_cli[cc] = {
            "nombre": (c.get("nombre") or c.get("razonSocial") or "").strip(),
            "direccion": (c.get("direccionEntrega") or "").strip(),
            "localidad": (c.get("localidad") or "").strip(),
            "telefono": (c.get("telefono") or "").strip(),
            "seg": seg,
            "dias": dias_vend,
        }

    tot = acum()
    por_vend = defaultdict(acum)
    por_vend_marca = defaultdict(acum)          # (vend, marca)
    por_marca = defaultdict(acum)
    por_seg = defaultdict(acum)
    por_vend_seg = defaultdict(acum)            # (vend, seg)
    cant_cli = defaultdict(float)               # cliente -> unidades netas
    cant_cli_marca = defaultdict(float)         # (cliente, marca)
    sin_facturar = 0.0
    lineas = 0

    for v in ventas:
        signo = SIGNO.get(cod(v.get("codigoTipoVenta")))
        if signo is None or cod(v.get("estado")).lower().startswith("anul"):
            continue
        fecha, facturada = fecha_venta(v)
        if not (desde <= fecha <= corte):
            continue
        emp, vend, cli = cod(v.get("codigoEmpresa")), cod(v.get("codigoVendedor")), cod(v.get("codigoCliente"))
        for it in v.get("items") or []:
            a = articulo(cod(it.get("codigoItem")), emp)
            if not a or cod(a.get("codigoProveedor")) != PROVEEDOR_GEORGALOS:
                continue
            cant = num(it.get("cantidad")) * num(it.get("unidadFactor") or 1)
            bruta = num(it.get("precioUnitario")) * num(it.get("cantidad")) * signo
            neta = num(it.get("importeNeto")) * signo
            costo = num(it.get("precioCosto")) * num(it.get("cantidad")) * signo
            m = marca_de(a.get("descripcion"))
            seg = info_cli.get(cli, {}).get("seg", "—")
            for bucket in (tot, por_vend[vend], por_vend_marca[(vend, m)], por_marca[m],
                           por_seg[seg], por_vend_seg[(vend, seg)]):
                sumar(bucket, bruta, neta, costo)
            cant_cli[cli] += cant * signo
            cant_cli_marca[(cli, m)] += cant * signo
            if not facturada:
                sin_facturar += neta
            lineas += 1

    compradores = {c for c, q in cant_cli.items() if q > 0}
    marcas = sorted(por_marca, key=lambda m: -por_marca[m]["neta"])

    nombres = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores}
    # Solo los que vendieron Georgalos en el mes: hay fuerzas de venta con
    # ruta propia (30, 31, 33...) que no llevan la linea y solo inflarian el
    # universo.
    codigos = {v for v in por_vend if v and v not in NO_VENDEDORES and por_vend[v]["bruta"] > 0}

    filas = []
    for v in codigos:
        uni = universo.get(v, set())
        cob_marca = {}
        for m in marcas:
            n = sum(1 for c in uni if cant_cli_marca.get((c, m), 0) > 0)
            cob_marca[m] = n
        segs = {}
        for s in SEGMENTOS:
            u = [c for c in uni if info_cli[c]["seg"] == s]
            segs[s] = {"universo": len(u),
                       "compradores": sum(1 for c in u if c in compradores),
                       **redondear(por_vend_seg.get((v, s), acum()))}
        filas.append({
            "cod": v,
            "nombre": nombres.get(v) or ("Vendedor " + v),
            "universo": len(uni),
            "compradores": sum(1 for c in uni if c in compradores),
            **redondear(por_vend.get(v, acum())),
            "marcas": {m: {**redondear(por_vend_marca.get((v, m), acum())),
                           "clientes": cob_marca[m]} for m in marcas},
            "segmentos": segs,
        })
    filas.sort(key=lambda f: -f["neta"])

    # No compradores: un cliente por vendedor en cuya ruta esta.
    no_compran = []
    for v in codigos:
        for c in sorted(universo.get(v, ())):
            if c in compradores:
                continue
            i = info_cli[c]
            no_compran.append({"vend": v, "codigo": c, "nombre": i["nombre"],
                               "direccion": i["direccion"], "localidad": i["localidad"],
                               "telefono": i["telefono"], "seg": i["seg"],
                               "dias": ", ".join(d.capitalize() for d in i["dias"].get(v, []))})

    todos_uni = set().union(*[universo[v] for v in codigos]) if codigos else set()
    seg_total = {}
    for s in SEGMENTOS:
        u = [c for c in todos_uni if info_cli[c]["seg"] == s]
        seg_total[s] = {"universo": len(u), "compradores": sum(1 for c in u if c in compradores),
                        **redondear(por_seg.get(s, acum()))}

    return {
        "desde": desde, "corte": corte,
        "generado": dt.datetime.now(TZ_AR).isoformat(timespec="minutes"),
        "total": {**redondear(tot), "universo": len(todos_uni),
                  "compradores": len(todos_uni & compradores),
                  "sinFacturar": round(sin_facturar, 2), "lineas": lineas},
        "marcas": [{"marca": m, **redondear(por_marca[m]),
                    "clientes": sum(1 for (c, mm), q in cant_cli_marca.items() if mm == m and q > 0 and c in todos_uni)}
                   for m in marcas],
        "segmentos": seg_total,
        "vendedores": filas,
        "noCompradores": no_compran,
        "otrosSinFila": round(sum(por_vend[v]["neta"] for v in por_vend if v not in codigos), 2),
    }


def main():
    if not gescom.hay_credenciales():
        print("Sin credenciales de GesCom: queda publicado el data.json anterior.")
        return 0
    corte = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(TZ_AR).date().isoformat()
    d_corte = dt.date.fromisoformat(corte)
    desde = d_corte.replace(day=1).isoformat()
    # Filtro de la API = fecha de creacion. Se arrastra desde el 24 del mes
    # anterior para no perder lo creado antes y facturado en este mes.
    arrastre = (d_corte.replace(day=1) - dt.timedelta(days=8)).isoformat()
    hasta_excl = (d_corte + dt.timedelta(days=1)).isoformat()

    api = Api()
    ventas = api.ventas(arrastre, hasta_excl)
    articulos = api.get("/data/cmd/inventario/api/v2/get-articulos")
    clientes = api.get("/data/cmd/ventas/api/v1/get-clientes")
    vendedores = api.get("/data/cmd/ventas/api/v1/get-vendedores")

    data = construir(ventas, articulos, clientes, vendedores, desde, corte)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    t = data["total"]
    print("Georgalos %s..%s: venta $%.0f, %d vendedores, %d/%d clientes compradores -> %s"
          % (desde, corte, t["neta"], len(data["vendedores"]), t["compradores"], t["universo"], SALIDA))
    return 0


if __name__ == "__main__":
    sys.exit(main())
