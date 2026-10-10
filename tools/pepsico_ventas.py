#!/usr/bin/env python3
"""
pepsico_ventas.py - datos del panel Avance Pepsico via API de GesCom.
Genera no_compradores_detalle.json, no_compradores_por_dia.json,
pehuamar90_no_comprado.json, invendible_detalle.json, rechazos_detalle.json,
cobertura_marca_vendedor.json, ccc_segmento.json, subproductos_vendedor.json,
subproductos_no_comprado.json, venta_vendedor.json, avance_kg_vendedor.json,
historial_diario.json y censo_clientes.json.

DESDE EL 30/9/2026 NO USA report/render (el usuario de API nuevo recibe 403). Los
tres reportes se reemplazaron:
  - "Detallado de ventas extendido" -> renglones Pepsico de la BASE PROPIA
    (https://base.tienda-perfecta.workers.dev/api/lineas-proveedor, secreto
    BASE_CLAVE), por fecha de pedido (= fecha de carga). Proveedor, marca y peso
    salen del catalogo de articulos de la base; la marca, de get-marcas (oficial).
  - "Avance de Ventas Pepsico" (kg) -> calculado con los mismos renglones por fecha
    de ENTREGA, solo ventas, grupos *P01/*P02 por las etiquetas del articulo y
    kg = cantidad x factorPeso / 1000. Comparado contra el reporte del 28/9/2026:
    ~2% por debajo (el reporte cuenta pedidos todavia sin facturar, que
    ventas/api/v2/get no devuelve). Los OBJETIVOS por vendedor no tienen endpoint:
    salen de pepsico/objetivos_kg.json (los de septiembre, congelados).
    acumuladoMN = kg del canal "MiNegocio".
  - Censo "Tienda Perfecta" -> endpoint oficial pepsico/get-resultado-tienda-perfecta.

(Lo que sigue describe como era antes, con los reportes.)
Avance kg: viene directo del reporte "Avance de Ventas Pepsico" de Gescom (el
mismo motor generico de reportes que usa la UI, /data/cmd/report/render),
Objetivo/Acumulado/Avance%/Promedio/Media Necesaria/Tendencia/venta real de
ultimas 2 visitas ya prorrateados y clasificados por el propio sistema en
Platino+Gold (*P01) / Silver&Bronze (*P02) -- son grupos de PRODUCTO, no de
segmento de cliente. El id de "Objetivo" que pide la API (objetivoId) es
distinto del numero que se ve en el combo de la UI y hay que verificarlo a
mano cada tanto (ver OBJETIVO_ID_BASE mas abajo).

No compradores/Cobertura/Subproductos/CCC/Invendible/Rechazos: vienen del
reporte generico "Detallado de ventas extendido" (report/render, mismo motor
que Avance kg), filtrado por Fecha de Carga real (columna FechaCarga del
reporte) -- el endpoint crudo /data/cmd/ventas/api/v2/get NO tiene ese campo
(solo fechaPedido/fechaEntrega), confirmado con un diagnostico contra datos
reales (25/09: 0 de 10174 ventas con fechaCarga via ese endpoint). El reporte
ya trae Proveedor, Marca y PesoKg como campos legibles -- no hace falta cruzar
contra el catalogo de articulos ni matchear marca por substring.

Credenciales por variables de entorno (GitHub Secrets):
BASE_CLAVE (clave de lectura de la base propia; ya no usa credenciales de Gescom)
"""
import calendar
import csv
import datetime as dt
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom
import requests

DIR = Path(__file__).resolve().parent.parent / "pepsico"
TZ_AR = dt.timezone(dt.timedelta(hours=-3))

# La columna Marca del reporte viene con casing inconsistente segun el
# proveedor la haya cargado ("Lays" vs "PEHUAMAR"); se normaliza por key en
# mayusculas al label de exhibicion.
MARCA_LABEL = {
    "3DS": "3Ds", "CHEETOS": "Cheetos", "DORITOS": "Doritos", "LAYS": "Lays",
    "PEHUAMAR": "Pehuamar", "PEP": "Pep", "QUAKER": "Quaker", "TOSTITOS": "Tostitos", "TWISTOS": "Twistos",
}
SEGMENTOS = ["A", "B", "C", "D"]
CCC_OBJETIVO_SEG = {"A": 371, "B": 318, "C": 675, "D": 913}     # octubre 2026: 2.277 clientes

# Avance de Ventas Pepsico (kg): reporte generico de Gescom, mismo motor que
# usa la UI (Reportes > Objetivos > Avance de Ventas Pepsico). Da Objetivo,
# Acumulado, Avance%, Promedio, Media Necesaria, Tendencia y venta real de las
# ultimas 2 visitas, ya prorrateado y clasificado por Gescom (Platino+Gold /
# Silver&Bronze son *P01/*P02 en el reporte, por PRODUCTO, no por cliente).
GUID_AVANCE_PEPSICO = "81da66a7-03ac-4a5f-bb14-9f85a7844a89"

# El id que pide la API para "objetivoId" NO es el numero que se ve en el
# combo de la UI ("19 - TN SEPTIEMBRE 2026" se manda como id=31, no como 19):
# es el id interno de la fila en la tabla de Objetivos de Gescom. Confirmado
# a mano en la UI: id=31 = Septiembre 2026, y viene subiendo de a 1 por mes
# sin saltos desde Enero 2026 (23=Ene26 ... 31=Sep26). Si Gescom intercala
# algun objetivo de otro proveedor (paso alguna vez en 2025) la secuencia se
# corre; por eso el chequeo de mas abajo no pisa el archivo anterior si el
# reporte viene vacio, para poder corregir OBJETIVO_ID_BASE a mano.
OBJETIVO_ID_BASE = 31
OBJETIVO_ID_BASE_ANIO = 2026
OBJETIVO_ID_BASE_MES = 9


def objetivo_id_de(anio, mes):
    return OBJETIVO_ID_BASE + (anio - OBJETIVO_ID_BASE_ANIO) * 12 + (mes - OBJETIVO_ID_BASE_MES)


# Detallado de ventas extendido (todos los proveedores, ambas empresas): trae
# Fecha de Carga real, Proveedor, Marca y PesoKg como columnas legibles.
# tipoVentaId=2 ("Segun Fecha de Creacion") es el que filtra por FechaCarga,
# confirmado contra datos reales (25/09: 100% de las filas devueltas traen
# FechaCarga = fecha pedida). facturadaId=2 = "Todas" (facturadas y no).
GUID_DETALLE_VENTAS = "d3e26fc0-576a-45aa-aab9-10871a347fc7"

PEHUAMAR_SKUS = {"PEHUA PAPA LISA 90GX22 RM", "PEHUA PAPA ACANA 90GX22 RM"}

# Sub-desglose de "Cobertura por marca": grupo -> {clave de salida -> set de descripciones exactas de SKU}.
SUBPRODUCTOS = {
    "pehuamar": {
        "obj": 30,
        "labels": {"Matambrito": "Pehuamar Matambrito", "Chimichurri": "Pehuamar Chimichurri",
                   "LisaAcanalada90": "Pehuamar Lisa + Acanalada x90g"},
        "skus": {
            "Matambrito": {"PEHUAMAR ACAN MATAMBRITO 80X25X1"},
            "Chimichurri": {"PEHUAMAR ACAN CHIMICHURRI 80X25X1"},
            "LisaAcanalada90": PEHUAMAR_SKUS,
        },
    },
    "lays": {
        "obj": 20,
        "labels": {"Caprese": "Lays Rústicas Caprese", "SalMarina": "Lays Rústicas Sal Marina",
                   "LimonHierbas": "Lays Rústicas Limón y Hierbas"},
        "skus": {
            "Caprese": {"Lays Rusticas Caprese 77 GRAMOS"},
            "SalMarina": {"Lays Rusticas Sal Marina 85 GRAMOS"},
            "LimonHierbas": set(),
        },
    },
    "tostitos": {
        "obj": 20,
        "labels": {"G144": "Tostitos Hierbas y Limón 144g", "G90": "Tostitos Hierbas y Limón 90g"},
        "skus": {
            "G144": {"TOSTITOS ROUND HIERB Y LIMON 144GX19X1"},
            "G90": {"TOSTITOS ROUND HIERB Y LIMON 90GX26X1"},
        },
    },
}
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_CAP = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

# Solo los 12 vendedores de calle de Pepsico (codigos 1-12). El cliente trae
# a veces otros codigos (deposito, mayorista, otros canales) que no son parte
# de este tablero.
VENDEDORES_PEPSICO = {str(i) for i in range(1, 13)}

# TipoDeVenta tal como lo devuelve el reporte (texto legible, no codigo).
# Confirmado contra el mes completo (25/09): Venta, Devolucion por Rechazo,
# Devolucion por Canje, Ajuste por Liquidacion (+/-), Debito, mas dos tipos de
# Comodato (prestamo/devolucion de heladeras y exhibidores, no son ventas de
# producto y se ignoran por completo, igual que antes).
TIPO_VENTA = "Venta"
TIPO_RECHAZO = "Devolución por Rechazo"
TIPO_CANJE = "Devolución por Canje"


def cod(v):
    return str(v if v is not None else "").strip()


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def num_ar(s):
    """Numero con formato AR del reporte CSV: '.' separador de miles, ','
    decimal (ej '12.562,00')."""
    if isinstance(s, (int, float)):
        return float(s)
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


class Api:
    def __init__(self):
        self.s = requests.Session()
        self._tok, self._t = None, 0

    def get(self, path, params=None, timeout=180):
        ahora = dt.datetime.now().timestamp()
        if not self._tok or ahora - self._t > 240:
            self._tok, self._t = gescom._token(self.s), ahora
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

    def post(self, path, body, timeout=180):
        ahora = dt.datetime.now().timestamp()
        if not self._tok or ahora - self._t > 240:
            self._tok, self._t = gescom._token(self.s), ahora
        for intento in range(4):
            try:
                r = self.s.post(gescom.API + path, json=body, timeout=timeout,
                                 headers={"Authorization": "Bearer " + self._tok,
                                          "Accept": "application/json"})
                if r.status_code < 500:
                    break
            except requests.ConnectionError:
                if intento == 3:
                    raise
            time.sleep(10 * (intento + 1))
        r.raise_for_status()
        return r.json()

    def render_report(self, report_id, parameters):
        """Motor generico de reportes de Gescom (el mismo que usa la UI). El
        cuerpo va anidado en reportInput.parameters -- mandar los parametros
        sueltos en la raiz devuelve 200 OK pero con las tablas vacias."""
        body = {"id": report_id, "reportInput": {"filtersInput": {}, "parameters": parameters}}
        return self.post("/data/cmd/report/render", body)

    def render_csv(self, report_id, parameters, timeout=240):
        """Algunos reportes (los de detalle, con muchas filas) devuelven CSV
        crudo en vez del JSON con datasources. Viene en CP1252 (los acentos
        salen mal si se decodifica como UTF-8), separado por ';' y con
        numeros en formato AR ('12.562,00', usar num_ar para parsear)."""
        ahora = dt.datetime.now().timestamp()
        if not self._tok or ahora - self._t > 240:
            self._tok, self._t = gescom._token(self.s), ahora
        body = {"id": report_id, "reportInput": {"filtersInput": {}, "parameters": parameters}}
        for intento in range(4):
            try:
                r = self.s.post(gescom.API + "/data/cmd/report/render", json=body, timeout=timeout,
                                 headers={"Authorization": "Bearer " + self._tok})
                if r.status_code < 500:
                    break
            except requests.ConnectionError:
                if intento == 3:
                    raise
            time.sleep(10 * (intento + 1))
        r.raise_for_status()
        texto = r.content.decode("cp1252")
        return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


# --- BASE PROPIA DE GESCOM -----------------------------------------------------------
BASE = "https://base.tienda-perfecta.workers.dev"
PROVEEDOR_PEPSICO = "100"
TIPO_POR_CODIGO = {"VEN": None, "DEV-RE": None, "DEV-CA": None}   # se completa abajo
OBJETIVOS_KG = DIR / "objetivos_kg.json"


def hay_base():
    return bool(os.environ.get("BASE_CLAVE", "").strip())


def base_get(ruta, **params):
    for intento in range(3):
        try:
            r = requests.get(BASE + ruta, params=params, timeout=240,
                             headers={"x-clave": os.environ["BASE_CLAVE"].strip()})
            if r.status_code < 500:
                break
        except requests.ConnectionError:
            if intento == 2:
                raise
        time.sleep(10 * (intento + 1))
    r.raise_for_status()
    return r.json()


_catalogo = {}


def articulos_pepsico():
    """(codigo, empresa) -> articulo, con proveedor Pepsico. La empresa 99 no tiene
    articulos propios: usa los de la 1 (y si no, los de la 2)."""
    if "arts" not in _catalogo:
        arts = {}
        for a in base_get("/api/catalogo/articulos"):
            if cod(a.get("codigoProveedor")) != PROVEEDOR_PEPSICO:
                continue
            arts[(cod(a.get("codigo")), cod(a.get("codigoEmpresa")))] = a
            arts.setdefault((cod(a.get("codigo")), ""), a)
        _catalogo["arts"] = arts
    return _catalogo["arts"]


def articulo_de(item, emp):
    arts = articulos_pepsico()
    for e in ([emp] if emp not in ("99", "") else []) + ["1", "2", ""]:
        a = arts.get((item, e))
        if a:
            return a
    return None


def nombre_proveedor_pepsico():
    if "prov" not in _catalogo:
        prov = {cod(p.get("codigo")): (p.get("nombre") or "").strip() for p in base_get("/api/catalogo/proveedores")}
        _catalogo["prov"] = prov.get(PROVEEDOR_PEPSICO) or "PEPSICO"
    return _catalogo["prov"]


def lineas_pepsico(desde, hasta, por):
    """Renglones de articulos Pepsico de la base, en tramos de 31 dias."""
    filas, d = [], desde
    while d <= hasta:
        h = min(hasta, d + dt.timedelta(days=30))
        filas += base_get("/api/lineas-proveedor", proveedor=PROVEEDOR_PEPSICO,
                          desde=d.isoformat(), hasta=h.isoformat(), por=por)
        d = h + dt.timedelta(days=1)
    return filas


def filas_desde_base(api, desde, hasta, por="pedido"):
    """Reemplaza al CSV "Detallado de ventas extendido": mismas claves que usaba el
    loop (Proveedor, TipoDeVenta, Cliente, Articulo, CantBase, CodVendedor, Vendedor,
    Marca, ImporteNetoItem, MotivoDevolucion, FechaComprobante). Las devoluciones
    van en negativo, como venian en el reporte. `por` = "pedido" (fecha de carga) o
    "entrega" (fecha de entrega, la que usa Pepsico para la cobertura)."""
    tipos = {"VEN": TIPO_VENTA, "DEV-RE": TIPO_RECHAZO, "DEV-CA": TIPO_CANJE}
    marcas = {cod(m.get("codigo")): (m.get("descripcion") or "").strip()
              for m in api.get("/data/cmd/inventario/api/v1/get-marcas")}
    vend = {cod(v.get("codigo")): (v.get("nombre") or "").strip() for v in base_get("/api/catalogo/vendedores")}
    prov = nombre_proveedor_pepsico()
    filas, sin_articulo = [], 0
    for l in lineas_pepsico(desde, hasta, por):
        tipo = tipos.get(l.get("tipo"))
        if not tipo:
            continue
        a = articulo_de(cod(l.get("item")), cod(l.get("emp")))
        if not a:
            sin_articulo += 1
            continue
        signo = 1 if tipo == TIPO_VENTA else -1
        filas.append({
            "Proveedor": prov, "TipoDeVenta": tipo, "Cliente": cod(l.get("cli")),
            "Articulo": (a.get("descripcion") or "").strip(),
            "CantBase": signo * abs(num(l.get("cant"))),
            "CodVendedor": cod(l.get("ven")), "Vendedor": vend.get(cod(l.get("ven")), ""),
            "Marca": marcas.get(cod(a.get("codigoMarca")), ""),
            "ImporteNetoItem": signo * abs(num(l.get("neto"))),
            "MotivoDevolucion": (l.get("motivo") or "").strip(),
            "FechaComprobante": (l.get("fcomp") or "")[:10],
        })
    if sin_articulo:
        print("AVISO: %d renglones Pepsico sin articulo en el catalogo de la base" % sin_articulo)
    return filas


OVERRIDE_FILAS = DIR / "override_filas_gescom.json"
_ESTADO_OVERRIDE = {}


def aplicar_override_filas(filas, hoy):
    """Parche temporal mientras la base propia no sincroniza con Gescom.

    Si existe pepsico/override_filas_gescom.json (el "Detallado de ventas extendido Pepsico" exportado de
    Gescom y reducido a las columnas que usa el loop), es DE HOY y tiene mas filas que la base, se usa ese
    detalle para todos los indicadores (venta, cobertura, rechazos, invendibles, no compradores, CCC,
    innovaciones). Apenas la base trae igual o mas filas, el parche se ignora solo."""
    try:
        ov = json.loads(OVERRIDE_FILAS.read_text(encoding="utf-8"))
    except Exception:
        return filas
    if ov.get("fecha") != hoy.isoformat():
        return filas
    nuevas = ov.get("filas") or []
    if len(nuevas) <= len(filas):
        return filas
    _ESTADO_OVERRIDE["usado"] = True
    _ESTADO_OVERRIDE["hoy_con_pedidos"] = any(
        f.get("TipoDeVenta") == TIPO_VENTA and f.get("FechaComprobante") == hoy.isoformat() for f in nuevas)
    print("AVISO: la base trae %d filas y el detalle de Gescom %d (extraido %s); se usa el de Gescom" %
          (len(filas), len(nuevas), ov.get("extraido", "")))
    return nuevas


OVERRIDE_FILAS_ENTREGA = DIR / "override_filas_entrega_gescom.json"


def aplicar_override_filas_entrega(filas_e, hoy):
    """Igual que aplicar_override_filas pero para el detalle por FECHA DE ENTREGA (mes completo), que usa la
    cobertura. Se usa solo si es de hoy y el parche de ventas por pedido tambien se esta usando (es decir, la
    base esta atrasada respecto de Gescom)."""
    if not _ESTADO_OVERRIDE.get("usado"):
        return filas_e
    try:
        ov = json.loads(OVERRIDE_FILAS_ENTREGA.read_text(encoding="utf-8"))
    except Exception:
        return filas_e
    if ov.get("fecha") != hoy.isoformat():
        return filas_e
    nuevas = ov.get("filas") or []
    if len(nuevas) <= len(filas_e):
        return filas_e
    print("AVISO: cobertura por entrega: la base trae %d filas y el detalle de Gescom %d (extraido %s); se usa el de Gescom" %
          (len(filas_e), len(nuevas), ov.get("extraido", "")))
    return nuevas


def objetivos_kg(anio, mes):
    """{nombre vendedor: (p1o, p2o)} del mes, o los del ultimo mes cargado."""
    try:
        todos = json.loads(OBJETIVOS_KG.read_text(encoding="utf-8"))
    except Exception:
        return {}, None
    claves_validas = [k for k in todos if isinstance(todos[k], dict)]
    clave = "%04d-%02d" % (anio, mes)
    if clave not in claves_validas:
        previos = sorted(k for k in claves_validas if k < clave) or sorted(claves_validas)
        if not previos:
            return {}, None
        clave = previos[-1]
    return {n: (num(v.get("p1o")), num(v.get("p2o"))) for n, v in todos[clave].items()}, clave


def dias_habiles_mes(anio, mes, hasta=None):
    """Cuenta dias habiles del mes: lunes a viernes valen 1, el sabado vale 0,5
    (se trabaja medio dia) y el domingo no cuenta. Si `hasta` se pasa, cuenta
    solo hasta esa fecha inclusive (dias trabajados)."""
    d = dt.date(anio, mes, 1)
    total = 0.0
    while d.month == mes:
        if d.weekday() != 6:
            if hasta is None or d <= hasta:
                total += 0.5 if d.weekday() == 5 else 1.0
        d += dt.timedelta(days=1)
    return total


def traer_avance_kg(api, hoy, dias_habiles, dias_trabajados):
    """Avance de kg calculado desde la base (el reporte de Gescom da 403 desde el 30/9).
    Mismas formulas y mismo JSON de salida que armaba con el reporte."""
    if not hay_base():
        raise RuntimeError("falta el secreto BASE_CLAVE para leer la base")
    ratio = dias_trabajados / dias_habiles if dias_habiles else 0
    objetivos, mes_obj = objetivos_kg(hoy.year, hoy.month)
    vend = {cod(v.get("codigo")): (v.get("nombre") or "").strip() for v in base_get("/api/catalogo/vendedores")}
    acc = {}
    # El acumulado cuenta TODAS las entregas del mes (incluidas las ya programadas para los proximos dias),
    # igual que el reporte "Avance de Ventas Pepsico" de Gescom, cuya fecha final por defecto es el fin de mes.
    fin_mes = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
    for l in lineas_pepsico(hoy.replace(day=1), fin_mes, "entrega"):
        if l.get("tipo") != "VEN":
            continue
        codven = cod(l.get("ven"))
        if codven not in VENDEDORES_PEPSICO:
            continue
        a = articulo_de(cod(l.get("item")), cod(l.get("emp")))
        if not a:
            continue
        tags = a.get("tags") or []
        g = "p1" if "*P01" in tags else "p2" if "*P02" in tags else None
        if not g:
            continue
        kg = num(l.get("cant")) * num(a.get("factorPeso")) / 1000
        x = acc.setdefault(codven, {"p1a": 0.0, "p2a": 0.0, "mn": 0.0, "dias": {}})
        x[g + "a"] += kg
        if l.get("canal") == "MiNegocio":
            x["mn"] += kg
    # Ultima / penultima visita / real: kg (*P01 + *P02) del mismo dia de la semana hace 7 y 14
    # dias, y hoy, por fecha de ENTREGA. Verificado el 3/10/2026 contra el reporte de Gescom:
    # el acumulado (3.110 kg) y el "Real" (761 kg) coinciden con la fecha de entrega, no con la
    # de pedido (con pedido el acumulado daba 1.691 kg porque los pedidos del viernes se entregan el sabado).
    for l in lineas_pepsico(hoy - dt.timedelta(days=45), hoy, "entrega"):
        if l.get("tipo") != "VEN" or cod(l.get("ven")) not in VENDEDORES_PEPSICO:
            continue
        a = articulo_de(cod(l.get("item")), cod(l.get("emp")))
        tags = (a or {}).get("tags") or []
        if not a or not ("*P01" in tags or "*P02" in tags):
            continue
        x = acc.setdefault(cod(l.get("ven")), {"p1a": 0.0, "p2a": 0.0, "mn": 0.0, "dias": {}})
        fp = (l.get("fe") or "")[:10]
        x["dias"][fp] = x["dias"].get(fp, 0.0) + num(l.get("cant")) * num(a.get("factorPeso")) / 1000
    # Real / Ultima visita / Penultima visita: el reporte de Gescom los calcula por fecha de PEDIDO (lo que los
    # preventistas cargaron ese dia), no por entrega. Verificado el 5/10/2026: por pedido el 21/9 da 1.536 kg y el
    # 28/9 da 81 kg, contra 1.533 y 79 kg de Penultima y Ultima en el reporte. El acumulado sigue siendo por entrega.
    dias_pedido = {}
    dias_pedido_grupo = {}     # fecha de pedido -> [kg *P01, kg *P02] (para reconstruir el acumulado de un dia pasado)
    for l in lineas_pepsico(hoy - dt.timedelta(days=45), hoy, "pedido"):
        if l.get("tipo") != "VEN" or cod(l.get("ven")) not in VENDEDORES_PEPSICO:
            continue
        a = articulo_de(cod(l.get("item")), cod(l.get("emp")))
        tags = (a or {}).get("tags") or []
        if not a or not ("*P01" in tags or "*P02" in tags):
            continue
        fpd = (l.get("fp") or l.get("fcomp") or "")[:10]
        dp = dias_pedido.setdefault(cod(l.get("ven")), {})
        kg_l = num(l.get("cant")) * num(a.get("factorPeso")) / 1000
        dp[fpd] = dp.get(fpd, 0.0) + kg_l
        gx = dias_pedido_grupo.setdefault(fpd, [0.0, 0.0])
        gx[0 if "*P01" in tags else 1] += kg_l
    hoy_iso = hoy.isoformat()
    hace7, hace14 = (hoy - dt.timedelta(days=7)).isoformat(), (hoy - dt.timedelta(days=14)).isoformat()
    kg_vendedores = []
    for codven in sorted(set(acc) | {c for c in VENDEDORES_PEPSICO if vend.get(c) in objetivos}, key=lambda c: int(c)):
        x = acc.get(codven, {"p1a": 0.0, "p2a": 0.0, "mn": 0.0, "dias": {}})
        nombre = vend.get(codven, codven)
        p1o, p2o = objetivos.get(nombre, (0.0, 0.0))
        p1a, p2a = x["p1a"], x["p2a"]
        to, ta = round(p1o + p2o, 2), round(p1a + p2a, 2)
        kg_vendedores.append({
            "n": nombre,
            "p1o": round(p1o, 2), "p1a": round(p1a, 2),
            "p1p": round(p1a / (p1o * ratio) * 100, 2) if p1o and ratio else 0.0,
            "p2o": round(p2o, 2), "p2a": round(p2a, 2),
            "p2p": round(p2a / (p2o * ratio) * 100, 2) if p2o and ratio else 0.0,
            "to": to, "ta": ta,
            "tp": round(ta / (to * ratio) * 100, 2) if to and ratio else 0.0,
            "promedio": round(ta / dias_trabajados, 2) if dias_trabajados else 0.0,
            "medianec": round((to - ta) / (dias_habiles - dias_trabajados), 2) if dias_habiles > dias_trabajados else 0.0,
            "tendencia": round(ta / dias_trabajados * dias_habiles, 2) if dias_trabajados else 0.0,
            "real": round(dias_pedido.get(codven, {}).get(hoy_iso, 0.0), 2),
            "penult": round(dias_pedido.get(codven, {}).get(hace14, 0.0), 2),
            "ultima": round(dias_pedido.get(codven, {}).get(hace7, 0.0), 2),
            "acumuladoMN": round(x["mn"], 2),
        })
    print("Avance kg desde la base: objetivos de %s%s" % (mes_obj, "" if mes_obj == hoy.strftime("%Y-%m") else " (congelados)"))

    # Historial diario (selector de fecha): kg de los pedidos tomados cada dia, igual que el "Real" de Gescom.
    historial_por_dia = {}
    for dp in dias_pedido.values():
        for fecha, kg in dp.items():
            historial_por_dia[fecha] = historial_por_dia.get(fecha, 0.0) + kg
    historial_out = [{"fecha": f, "kg": round(k, 2), "p1": round(dias_pedido_grupo.get(f, [0.0, 0.0])[0], 2),
                      "p2": round(dias_pedido_grupo.get(f, [0.0, 0.0])[1], 2)}
                     for f, k in sorted(historial_por_dia.items()) if abs(k) > 0.005]
    escribir("historial_diario.json", historial_out)
    print("Historial diario: %d dias (por fecha de pedido)" % len(historial_out))

    return {
        "objetivoPG": round(sum(v["p1o"] for v in kg_vendedores), 2),
        "objetivoSB": round(sum(v["p2o"] for v in kg_vendedores), 2),
        "diasHabiles": dias_habiles, "diasTrabajados": dias_trabajados,
        "vendedores": kg_vendedores,
        "fuente": "base propia (renglones por fecha de entrega); objetivos de %s" % mes_obj,
    }


def escribir_productos_segmento(api):
    """productos_segmento.json: que articulos Pepsico entran en Platino + Gold (*P01) y en Silver & Bronze (*P02),
    con su marca y gramos. Es el mismo catalogo que se usa para sumar los kg."""
    marcas = {cod(m.get("codigo")): (m.get("descripcion") or "").strip()
              for m in api.get("/data/cmd/inventario/api/v1/get-marcas")}
    vistos, out = set(), {"p1": [], "p2": []}
    for a in articulos_pepsico().values():
        clave = cod(a.get("codigo"))
        if clave in vistos:
            continue
        tags = a.get("tags") or []
        g = "p1" if "*P01" in tags else "p2" if "*P02" in tags else None
        if not g:
            continue
        vistos.add(clave)
        out[g].append({"descripcion": (a.get("descripcion") or "").strip(), "marca": marcas.get(cod(a.get("codigoMarca")), "") or "Otras",
                       "gramos": round(num(a.get("factorPeso")))})
    for g in out:
        out[g].sort(key=lambda x: (x["marca"], x["descripcion"]))
    escribir("productos_segmento.json", out)
    print("Productos por segmento: Platino+Gold %d | Silver&Bronze %d" % (len(out["p1"]), len(out["p2"])))


def leer_historial_previo():
    """{fecha: kg} del historial_diario.json de la corrida anterior (antes de que esta corrida lo reescriba)."""
    try:
        return {x["fecha"]: {"kg": float(x.get("kg") or 0), "p1": float(x.get("p1") or 0), "p2": float(x.get("p2") or 0)}
                for x in json.loads((DIR / "historial_diario.json").read_text(encoding="utf-8"))}
    except Exception:
        return {}


def corregir_historial_diario(filas, previo, hoy):
    """Un dia ya cerrado no puede 'bajar': si la base propia perdio pedidos (pasa cuando se corta la sincronizacion),
    el historial de esa fecha quedaba mas bajo que lo real. Se queda con el MAYOR entre lo que trae la base, lo que
    traia la corrida anterior y, si se esta usando el detalle de Gescom, lo que da ese detalle por fecha de pedido."""
    def vacio():
        return {"kg": 0.0, "p1": 0.0, "p2": 0.0}

    def maximo(a, b):
        return {k: max(a.get(k, 0.0), b.get(k, 0.0)) for k in ("kg", "p1", "p2")}

    try:
        actual = {x["fecha"]: {"kg": float(x.get("kg") or 0), "p1": float(x.get("p1") or 0), "p2": float(x.get("p2") or 0)}
                  for x in json.loads((DIR / "historial_diario.json").read_text(encoding="utf-8"))}
    except Exception:
        actual = {}
    if _ESTADO_OVERRIDE.get("usado"):
        por_desc = {}
        for a in articulos_pepsico().values():
            por_desc.setdefault((a.get("descripcion") or "").strip().upper(), a)
        det = {}
        for f in filas:
            if f.get("TipoDeVenta") != TIPO_VENTA or "PEPSICO" not in (f.get("Proveedor") or "").upper():
                continue
            if cod(f.get("CodVendedor")) not in VENDEDORES_PEPSICO:
                continue
            a = por_desc.get((f.get("Articulo") or "").strip().upper())
            tags = (a or {}).get("tags") or []
            if not a or not ("*P01" in tags or "*P02" in tags):
                continue
            fecha = (f.get("FechaComprobante") or "")[:10]
            if fecha:
                kg_f = num_ar(f.get("CantBase")) * num(a.get("factorPeso")) / 1000
                d_ = det.setdefault(fecha, vacio())
                d_["kg"] += kg_f
                d_["p1" if "*P01" in tags else "p2"] += kg_f
        for fecha, d_ in det.items():
            actual[fecha] = maximo(actual.get(fecha, vacio()), d_)
    for fecha, d_ in previo.items():
        actual[fecha] = maximo(actual.get(fecha, vacio()), d_)
    salida = [{"fecha": f, "kg": round(v["kg"], 2), "p1": round(v["p1"], 2), "p2": round(v["p2"], 2)}
              for f, v in sorted(actual.items()) if abs(v["kg"]) > 0.005]
    escribir("historial_diario.json", salida)
    print("Historial diario corregido: %d dias (nunca baja respecto de la corrida anterior)" % len(salida))


OVERRIDE_GESCOM = DIR / "override_gescom_avance.json"


def aplicar_override_gescom(avance, hoy, dias_habiles, dias_trabajados):
    """Parche temporal mientras la base propia no sincroniza con Gescom.

    Si existe pepsico/override_gescom_avance.json (acumulado por vendedor sacado a mano del reporte
    "Avance de Ventas Pepsico" de Gescom), es DE HOY y suma mas kg que lo que trae la base, se usan esos
    valores (acumulado, Mi Negocio, ultima/penultima visita y real) y se recalculan los derivados con
    los objetivos del mes. Apenas la base alcanza ese acumulado, el parche se ignora solo.
    Devuelve (avance, total_gescom o None)."""
    try:
        ov = json.loads(OVERRIDE_GESCOM.read_text(encoding="utf-8"))
    except Exception:
        return avance, None
    if ov.get("fecha") != hoy.isoformat():
        return avance, None
    por = {}
    for f in ov.get("filas", []):
        d = por.setdefault(f["vendedor"], {"p1a": 0.0, "p2a": 0.0, "mn": 0.0, "ultima": 0.0, "penult": 0.0, "real": 0.0})
        d["p1a" if f["grupo"] == "*P01" else "p2a"] += num(f.get("acumulado"))
        d["mn"] += num(f.get("acumuladoMN"))
        d["ultima"] += num(f.get("ultima"))
        d["penult"] += num(f.get("penult"))
        d["real"] += num(f.get("real"))
    total_ov = sum(d["p1a"] + d["p2a"] for d in por.values())
    total_base = sum(v["ta"] for v in avance["vendedores"])
    if total_ov <= total_base + 1:
        return avance, None
    ratio = dias_trabajados / dias_habiles if dias_habiles else 0
    for v in avance["vendedores"]:
        o = por.get(v["n"])
        if not o:
            continue
        p1o, p2o = v["p1o"], v["p2o"]
        p1a, p2a = round(o["p1a"], 2), round(o["p2a"], 2)
        to, ta = round(p1o + p2o, 2), round(p1a + p2a, 2)
        v.update({
            "p1a": p1a, "p1p": round(p1a / (p1o * ratio) * 100, 2) if p1o and ratio else 0.0,
            "p2a": p2a, "p2p": round(p2a / (p2o * ratio) * 100, 2) if p2o and ratio else 0.0,
            "ta": ta, "tp": round(ta / (to * ratio) * 100, 2) if to and ratio else 0.0,
            "promedio": round(ta / dias_trabajados, 2) if dias_trabajados else 0.0,
            "medianec": round((to - ta) / (dias_habiles - dias_trabajados), 2) if dias_habiles > dias_trabajados else 0.0,
            "tendencia": round(ta / dias_trabajados * dias_habiles, 2) if dias_trabajados else 0.0,
            "real": round(o["real"], 2), "ultima": round(o["ultima"], 2), "penult": round(o["penult"], 2),
            "acumuladoMN": round(o["mn"], 2),
        })
    avance["fuente"] = "Gescom (reporte Avance de Ventas Pepsico, extraido %s) mientras la base no sincroniza; objetivos de octubre" % ov.get("extraido", "")
    try:
        hist = json.loads((DIR / "historial_diario.json").read_text(encoding="utf-8"))
        hoy_iso = hoy.isoformat()
        kg_hoy = round(sum(d["real"] for d in por.values()), 2)
        hist = [h for h in hist if h.get("fecha") != hoy_iso] + [{"fecha": hoy_iso, "kg": kg_hoy}]
        escribir("historial_diario.json", sorted(hist, key=lambda h: h["fecha"]))
    except Exception as e:
        print("No se pudo actualizar el historial diario con el dato de Gescom:", e)
    return avance, total_ov


def traer_avance_kg_reporte(api, hoy, dias_habiles, dias_trabajados):
    """(ANTES, sin uso desde el 30/9/2026: report/render da 403.) Trae el Avance de Ventas Pepsico (kg) directo del reporte de Gescom, ya
    prorrateado y clasificado por el propio sistema (Platino+Gold = *P01,
    Silver&Bronze = *P02). Reemplaza la aproximacion anterior (objetivo fijo
    prorrateado por universo de clientes + clasificacion de producto a mano)
    por los valores exactos que arma Gescom."""
    ratio = dias_trabajados / dias_habiles if dias_habiles else 0
    inicio_mes = hoy.replace(day=1)
    fin_mes = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
    ahora_iso = dt.datetime.now(TZ_AR).isoformat(timespec="milliseconds")
    parametros = {
        "fechaInicioVentas": inicio_mes.isoformat() + "T03:00:00.000Z",
        "fechaFinVentas": fin_mes.isoformat() + "T03:00:00.000Z",
        "objetivoId": objetivo_id_de(hoy.year, hoy.month),
        "medidaId": 3,
        "tipoVentaId": 0,
        "fechaProxVisita": ahora_iso,
        "fechaefectividad": ahora_iso,
        "cantDiasHabiles": dias_habiles,
        "cantDiasTrabajados": dias_trabajados,
        "vendedorId": 0, "supervisorId": 0, "proveedorId": 0,
        "unidadesCCC": 0, "importeCCC": 1, "oportunidad": 0, "objetivocumplimiento": 5,
    }
    data = api.render_report(GUID_AVANCE_PEPSICO, parametros)
    tabla = next((d["table"] for d in data.get("datasources") or [] if d["name"] == "Avance"), None)
    if not tabla or len(tabla) < 2:
        raise RuntimeError(
            "El reporte Avance de Ventas Pepsico vino vacio con objetivoId=%s. "
            "Probablemente cambio la numeracion del Objetivo del mes: revisar "
            "a mano en Gescom (Reportes > Objetivos > Avance de Ventas Pepsico, "
            "combo Objetivo) cual id corresponde y actualizar OBJETIVO_ID_BASE "
            "en este archivo." % parametros["objetivoId"])

    header = tabla[0]
    idx = {nombre: i for i, nombre in enumerate(header)}
    por_vendedor = {}
    for fila in tabla[1:]:
        nombre = fila[idx["VendedorNombre"]]
        acc = por_vendedor.setdefault(nombre, {
            "codven": cod(fila[idx["VendedorCodigo"]]),
            "ultima": 0.0, "penult": 0.0, "real": 0.0,
        })
        clave = "p1" if fila[idx["GrupoCodigo"]] == "*P01" else "p2"
        acc[clave + "o"] = num(fila[idx["ValorObjetivo"]])
        acc[clave + "a"] = num(fila[idx["Acumulado"]])
        acc[clave + "p"] = num(fila[idx["Avance"]])
        if "AcumuladoMN" in idx:
            acc["mn"] = acc.get("mn", 0.0) + num(fila[idx["AcumuladoMN"]])
        acc["ultima"] += num(fila[idx["UltimaVisita"]])
        acc["penult"] += num(fila[idx["PenultimaVisita"]])
        acc["real"] += num(fila[idx["Real"]])

    kg_vendedores = []
    for nombre, acc in sorted(por_vendedor.items(), key=lambda kv: int(kv[1]["codven"] or 0)):
        p1o, p1a = acc.get("p1o", 0.0), acc.get("p1a", 0.0)
        p2o, p2a = acc.get("p2o", 0.0), acc.get("p2a", 0.0)
        to, ta = round(p1o + p2o, 2), round(p1a + p2a, 2)
        kg_vendedores.append({
            "n": nombre,
            "p1o": round(p1o, 2), "p1a": round(p1a, 2), "p1p": round(acc.get("p1p", 0.0), 2),
            "p2o": round(p2o, 2), "p2a": round(p2a, 2), "p2p": round(acc.get("p2p", 0.0), 2),
            "to": to, "ta": ta,
            "tp": round(ta / (to * ratio) * 100, 2) if to and ratio else 0.0,
            "promedio": round(ta / dias_trabajados, 2) if dias_trabajados else 0.0,
            "medianec": round((to - ta) / (dias_habiles - dias_trabajados), 2) if dias_habiles > dias_trabajados else 0.0,
            "tendencia": round(ta / dias_trabajados * dias_habiles, 2) if dias_trabajados else 0.0,
            "real": round(acc["real"], 2),
            "penult": round(acc["penult"], 2),
            "ultima": round(acc["ultima"], 2),
            "acumuladoMN": round(acc["mn"], 2) if "mn" in acc else None,
        })

    return {
        "objetivoPG": round(sum(v["p1o"] for v in kg_vendedores), 2),
        "objetivoSB": round(sum(v["p2o"] for v in kg_vendedores), 2),
        "diasHabiles": dias_habiles, "diasTrabajados": dias_trabajados,
        "vendedores": kg_vendedores,
    }


def ruta_de(cliente_raw):
    """(dia, codven) de la ruta de preventa del cliente. Un cliente puede
    tener mas de una ruta cargada (ej. calle + mayorista/deposito, o dos
    vendedores de calle distintos); tomar siempre la primera entrada del
    array (como se hacia antes) mezclaba el dia de una ruta con el vendedor
    de OTRA. Ahora se saca el (dia, codven) de la MISMA entrada, y si hay
    varias se prioriza la que tenga un vendedor de los 12 de calle."""
    candidatos = []
    for r in cliente_raw.get("rutasPreventa") or []:
        for i, d in enumerate(DIAS):
            if r.get(d):
                candidatos.append((DIAS_CAP[i], cod(r.get("codigoVendedor"))))
                break
    if not candidatos:
        return None, ""
    for dia, codven in candidatos:
        if codven in VENDEDORES_PEPSICO:
            return dia, codven
    return candidatos[0]


MES_ARCHIVO = None
SOLO_ARCHIVO = False


def actualizar_indice_archivo(mes):
    ruta = DIR / "archive" / "index.json"
    meses = []
    if ruta.exists():
        try:
            meses = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            meses = []
    if mes not in meses:
        meses.append(mes)
        meses.sort()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(meses, ensure_ascii=False, indent=2), encoding="utf-8")


def escribir(nombre, data):
    texto = json.dumps(data, ensure_ascii=False, indent=2)
    if not SOLO_ARCHIVO:
        ruta = DIR / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")
        print("Guardado", ruta)
    if MES_ARCHIVO:
        ruta_archivo = DIR / "archive" / MES_ARCHIVO / nombre
        ruta_archivo.parent.mkdir(parents=True, exist_ok=True)
        ruta_archivo.write_text(texto, encoding="utf-8")
        print("Guardado (archivo)", ruta_archivo)


# Censo Tienda Perfecta: mismo motor generico de reportes, sin rango de
# fechas (es una foto del estado actual, no un acumulado del mes). Gescom ya
# calcula el flag "TiendaPerfecta" (Si/No, >=80% portafolio + validacion) y
# "SupOk" (si el supervisor ya lo valido) -- no hace falta recalcular la
# logica de negocio, solo agregar por segmento y vendedor.
GUID_TIENDA_PERFECTA = "81221fe2-5545-47ec-a149-6c95d47afb44"
# Subcanales que entran en el universo de Tienda Perfecta (confirmado por el
# usuario). El censo trae TODOS los subcanales (incluye Mayoristas, Colegios,
# Hotel, etc.) que no son parte de este universo.
SUBCANALES_TP = {"Kiosco/Maxikiosco", "Almacen/Despensa", "Autoservicio Tradicional",
                  "Estacion de Servicio - NO OFICIALES", "Fiambreria"}
TP_OBJETIVO_SEG = {"A": 246, "B": 184, "C": 328, "D": 442}      # octubre 2026: 1.200 tiendas perfectas


def es_si(v):
    return (v or "").strip().lower() == "si"


# Del endpoint oficial, calibrado el 30/9/2026 contra el ultimo censo del reporte (28/9:
# universo 2262, TP 1131, a validar 35). Entre los clientes con >= 80% de portafolio,
# perfectStoreType 3 = 1163, 2 = 33, 1 = 39: el 3 es la tienda CONFIRMADA por el supervisor
# y el 2 la que falta validar (33 ~ 35).
TP_TIPOS_TP = {2, 3}
TP_TIPOS_VALIDADO = {3}
# Seguro: si el censo nuevo se aleja mas de esto del anterior (universo o a validar), no se
# publica: queda el anterior y se avisa en el log.
TP_MAX_DESVIO = 0.25


def traer_tienda_perfecta(api, nombre_por_codven, clientes):
    crudos = api.get("/data/cmd/ventas/api/v1/pepsico/get-resultado-tienda-perfecta")
    if not crudos:
        raise RuntimeError("El censo Tienda Perfecta vino vacio.")
    vistos, filas, por_tipo, por_sub, por_ramo = set(), [], {}, {}, {}
    for r in crudos:
        cc = cod(r.get("codigoCliente"))
        if cc in vistos:          # un cliente puede venir repetido
            continue
        vistos.add(cc)
        c = clientes.get(cc)
        # Como el reporte: solo clientes activos (en get-clientes) con ruta de un vendedor
        # Pepsico (1-12). El endpoint trae ademas inactivos y de otros vendedores.
        if not c or c.get("codven") not in VENDEDORES_PEPSICO:
            continue
        tipo = int(num(r.get("perfectStoreType")))
        es_tp = tipo in TP_TIPOS_TP
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        sub = (r.get("subRamo") or "").strip()
        ramo = (r.get("ramo") or "").strip()
        por_sub[sub] = por_sub.get(sub, 0) + 1
        por_ramo[ramo] = por_ramo.get(ramo, 0) + 1
        filas.append({
            # El subcanal del reporte puede ser el ramo o el subramo: se usa el que este en
            # la lista de SUBCANALES_TP.
            "SubCanal": sub if sub in SUBCANALES_TP else ramo,
            "Segmento": (c.get("seg") or cod(r.get("perfectStoreState"))).upper(),
            "TiendaPerfecta": "Si" if es_tp else "No",
            "SupOk": "Si" if tipo in TP_TIPOS_VALIDADO else "No",
            "Vendedor_ID": c.get("codven") or "",
        })
    top = lambda d: sorted(d.items(), key=lambda kv: -kv[1])[:12]
    print("Censo TP (oficial): %d clientes; por perfectStoreType: %s" % (len(filas), por_tipo))
    print("  subRamo mas frecuentes: %s" % top(por_sub))
    print("  ramo mas frecuentes: %s" % top(por_ramo))
    universo = [f for f in filas if (f.get("SubCanal") or "").strip() in SUBCANALES_TP]

    por_seg = {s: {"universo": 0, "tp": 0, "validar": 0} for s in SEGMENTOS}
    por_vend_seg = {}
    for f in universo:
        seg = (f.get("Segmento") or "").strip()
        if seg not in SEGMENTOS:
            continue
        es_tp = es_si(f.get("TiendaPerfecta"))
        sup_ok = es_si(f.get("SupOk"))
        por_seg[seg]["universo"] += 1
        if es_tp:
            por_seg[seg]["tp"] += 1
            if not sup_ok:
                por_seg[seg]["validar"] += 1
        codven = cod(f.get("Vendedor_ID"))
        acc = por_vend_seg.setdefault(codven, {s: {"universo": 0, "tp": 0} for s in SEGMENTOS})
        acc[seg]["universo"] += 1
        if es_tp:
            acc[seg]["tp"] += 1

    universo_total = sum(v["universo"] for v in por_seg.values())
    tp_total = sum(v["tp"] for v in por_seg.values())
    validar_total = sum(v["validar"] for v in por_seg.values())

    keymap = {"A": ("ao", "ac"), "B": ("bo", "bc"), "C": ("co", "cc"), "D": ("do_", "dc")}
    universo_seg_total_vend = {s: sum(v.get(s, {}).get("universo", 0)
                                       for cv, v in por_vend_seg.items() if cv in VENDEDORES_PEPSICO)
                                for s in SEGMENTOS}
    vendedores_out = []
    for codven in sorted(por_vend_seg, key=lambda x: int(x) if x.isdigit() else 999):
        if codven not in VENDEDORES_PEPSICO:
            continue
        fila = {"n": nombre_por_codven.get(codven, codven)}
        acc = por_vend_seg[codven]
        for s in SEGMENTOS:
            ko, kc = keymap[s]
            uni_total_seg = universo_seg_total_vend[s]
            uni_v = acc[s]["universo"]
            fila[ko] = round(TP_OBJETIVO_SEG[s] * uni_v / uni_total_seg) if uni_total_seg else 0
            fila[kc] = acc[s]["tp"]
        vendedores_out.append(fila)

    objetivo_oficial = dict(TP_OBJETIVO_SEG)
    objetivo_oficial["total"] = sum(TP_OBJETIVO_SEG.values())

    try:
        previo = json.loads((DIR / "tienda_perfecta.json").read_text(encoding="utf-8"))
    except Exception:
        previo = None
    if previo:
        for campo, nuevo in (("universoTotal", universo_total), ("validarTotal", validar_total)):
            ant = previo.get(campo) or 0
            if ant and abs(nuevo - ant) / ant > TP_MAX_DESVIO and abs(nuevo - ant) > 20:
                raise RuntimeError("censo TP no publicado: %s da %d y el anterior %d (desvio > %d%%)"
                                   % (campo, nuevo, ant, TP_MAX_DESVIO * 100))
    return {
        "universoTotal": universo_total,
        "tpTotal": tp_total,
        "noTpTotal": universo_total - tp_total,
        "validarTotal": validar_total,
        "objetivoOficial": objetivo_oficial,
        "porSegmento": por_seg,
        "vendedores": vendedores_out,
    }


def main():
    gescom.usar_base()   # desde el 5/10/2026 todo sale de la base propia, nada de Gescom

    hoy = dt.datetime.now(TZ_AR).date()
    inicio_mes = hoy.replace(day=1)

    global MES_ARCHIVO
    MES_ARCHIVO = hoy.strftime("%Y-%m")
    actualizar_indice_archivo(MES_ARCHIVO)

    clientes_raw, ramos, subramos = gescom.bajar_clientes()
    clientes = {}
    for c in clientes_raw:
        codigo = cod(c.get("codigo"))
        if not codigo:
            continue
        dia, codven = ruta_de(c)
        clientes[codigo] = {
            "codigo": codigo,
            "razon": (c.get("nombre") or c.get("razonSocial") or "").strip(),
            "localidad": (c.get("localidad") or "").strip(),
            "seg": cod(c.get("codigoSegmento")).upper(),
            "dia": dia,
            "codven": codven,
        }

    escribir("censo_clientes.json", [
        {"codigo": c["codigo"], "seg": c["seg"], "dia": c["dia"], "codven": c["codven"]}
        for c in clientes.values() if c["codven"] in VENDEDORES_PEPSICO
    ])

    subgrupo_por_desc = {}
    for grupo, cfg in SUBPRODUCTOS.items():
        for clave, skus in cfg["skus"].items():
            for sku in skus:
                subgrupo_por_desc[sku.upper()] = (grupo, clave)

    api = Api()

    vendedores_raw = api.get("/data/cmd/ventas/api/v1/get-vendedores")
    nombre_por_codven = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores_raw}

    try:
        tienda_perfecta = traer_tienda_perfecta(api, nombre_por_codven, clientes)
        escribir("tienda_perfecta.json", tienda_perfecta)
        print("Tienda Perfecta: universo %d | TP %d | no TP %d | a validar %d" %
              (tienda_perfecta["universoTotal"], tienda_perfecta["tpTotal"],
               tienda_perfecta["noTpTotal"], tienda_perfecta["validarTotal"]))
    except Exception as e:
        print("ERROR trayendo censo Tienda Perfecta (se deja tienda_perfecta.json anterior sin tocar):", e)

    if not hay_base():
        print("Falta el secreto BASE_CLAVE: no se puede leer la base; se dejan los JSON anteriores sin tocar.")
        return 0
    filas = filas_desde_base(api, inicio_mes, hoy)
    print("Filas del detalle de ventas (mes en curso, todos los proveedores):", len(filas))
    filas = aplicar_override_filas(filas, hoy)
    filas_detalle = filas        # la variable "filas" se reutiliza mas abajo (subproductos): esta copia queda intacta

    # Cobertura (marca, subproductos, Pehuamar): igual que el cuadro de Pepsico, por FECHA DE ENTREGA del mes
    # completo y con las cantidades con signo (los rechazos y canjes restan). La venta real, los no compradores
    # y el resto siguen por fecha de pedido.
    fin_mes_cob = (inicio_mes.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    filas_entrega = filas_desde_base(api, inicio_mes, fin_mes_cob, "entrega")
    filas_entrega = aplicar_override_filas_entrega(filas_entrega, hoy)
    print("Filas por fecha de entrega (cobertura):", len(filas_entrega))

    compra_cliente = {}
    compra_cliente_marca = {}
    compra_cliente_subgrupo = {}
    pehuamar_compra = {}
    invendible_por_vend = {}
    rechazos_por_vend = {}
    venta_importe_por_vend = {}
    rechazos_fecha_rows = []

    items_pepsico_vistos = 0
    for fila in filas:
        if "PEPSICO" not in (fila.get("Proveedor") or "").upper():
            continue
        tipo = fila.get("TipoDeVenta") or ""
        cli = cod(fila.get("Cliente"))
        articulo = (fila.get("Articulo") or "").strip()
        q = num_ar(fila.get("CantBase"))
        codven = cod(fila.get("CodVendedor"))
        nombre_vend = (fila.get("Vendedor") or "").strip() or nombre_por_codven.get(codven, codven)

        if tipo == TIPO_VENTA:
            compra_cliente[cli] = compra_cliente.get(cli, 0) + q
            items_pepsico_vistos += 1
            marca = MARCA_LABEL.get((fila.get("Marca") or "").strip().upper())
            if marca:
                porcli = compra_cliente_marca.setdefault(cli, {})
                porcli[marca] = porcli.get(marca, 0) + q
            if articulo.upper() in PEHUAMAR_SKUS:
                pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q
            sub = subgrupo_por_desc.get(articulo.upper())
            if sub:
                porcli_sub = compra_cliente_subgrupo.setdefault(cli, {})
                porcli_sub[sub] = porcli_sub.get(sub, 0) + q
            venta_importe_por_vend[nombre_vend] = venta_importe_por_vend.get(nombre_vend, 0.0) + num_ar(fila.get("ImporteNetoItem"))
        elif tipo in (TIPO_CANJE, TIPO_RECHAZO):
            importe = num_ar(fila.get("ImporteNetoItem"))
            # CantBase/ImporteNetoItem ya vienen negativos en las devoluciones;
            # se usa abs() para la cantidad (se muestra como unidades retiradas,
            # no como delta) y el importe tal cual (ya negativo).
            if tipo == TIPO_CANJE:
                acc = invendible_por_vend.setdefault(nombre_vend, {}).setdefault(articulo, [0.0, 0.0])
            else:
                motivo = (fila.get("MotivoDevolucion") or "").strip() or "SIN MOTIVO"
                acc = rechazos_por_vend.setdefault(nombre_vend, {}).setdefault(motivo, [0.0, 0.0])
                fecha_comp = (fila.get("FechaComprobante") or "").strip()
                if fecha_comp:
                    rechazos_fecha_rows.append({
                        "fecha": fecha_comp, "vendedor": nombre_vend, "cliente": cli,
                        "motivo": motivo, "cant": abs(q), "importe": importe,
                    })
            acc[0] += abs(q)
            acc[1] += importe

    # Cobertura por fecha de entrega y neta de devoluciones (ver arriba): reemplaza lo acumulado por pedido.
    compra_cliente_marca = {}
    compra_cliente_subgrupo = {}
    pehuamar_compra = {}
    for fila in filas_entrega:
        if "PEPSICO" not in (fila.get("Proveedor") or "").upper():
            continue
        if (fila.get("TipoDeVenta") or "") not in (TIPO_VENTA, TIPO_CANJE, TIPO_RECHAZO):
            continue
        cli = cod(fila.get("Cliente"))
        articulo = (fila.get("Articulo") or "").strip()
        q = num_ar(fila.get("CantBase"))     # las devoluciones ya vienen negativas
        marca = MARCA_LABEL.get((fila.get("Marca") or "").strip().upper())
        if marca:
            porcli = compra_cliente_marca.setdefault(cli, {})
            porcli[marca] = porcli.get(marca, 0) + q
        if articulo.upper() in PEHUAMAR_SKUS:
            pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q
        sub = subgrupo_por_desc.get(articulo.upper())
        if sub:
            porcli_sub = compra_cliente_subgrupo.setdefault(cli, {})
            porcli_sub[sub] = porcli_sub.get(sub, 0) + q

    print("DIAG items de venta Pepsico contados:", items_pepsico_vistos,
          "| clientes con al menos 1 unidad:", len(compra_cliente))

    DIA_CLAVE = {"Lunes": "lu", "Martes": "ma", "Miercoles": "mi",
                 "Jueves": "ju", "Viernes": "vi", "Sabado": "sa"}

    hoy_key = DIAS_CAP[hoy.weekday()]
    no_compradores = []
    pehuamar_no_comprado = []
    subproducto_no_comprado = {(g, k): [] for g, cfg in SUBPRODUCTOS.items() for k in cfg["labels"]}
    universo_por_vend = {}
    marca_cumple_por_vend = {}
    sub_cumple_por_vend = {}
    seg_por_vend = {}
    nc_dia_por_vend = {}
    for codigo, c in clientes.items():
        if not c["dia"] or c["codven"] not in VENDEDORES_PEPSICO:
            continue
        nombre_vend_cli = nombre_por_codven.get(c["codven"], c["codven"])
        es_no_comprador = compra_cliente.get(codigo, 0) < 3
        if es_no_comprador:
            no_compradores.append({**{k: c[k] for k in
                                    ("codigo", "razon", "localidad", "seg", "dia")},
                                    "vendedor": nombre_vend_cli})
        if c["dia"] == hoy_key and pehuamar_compra.get(codigo, 0) <= 0:
            pehuamar_no_comprado.append({**{k: c[k] for k in
                                          ("codigo", "razon", "localidad", "seg", "dia")},
                                          "vendedor": nombre_vend_cli})

        codven = c["codven"]
        universo_por_vend[codven] = universo_por_vend.get(codven, 0) + 1
        cumple_marca = marca_cumple_por_vend.setdefault(codven, {})
        for marca, cant in compra_cliente_marca.get(codigo, {}).items():
            if cant >= 3:
                cumple_marca[marca] = cumple_marca.get(marca, 0) + 1
        cli_sub = compra_cliente_subgrupo.get(codigo, {})
        cumple_sub = sub_cumple_por_vend.setdefault(codven, {})
        for sub, cant in cli_sub.items():
            if cant >= 3:
                cumple_sub[sub] = cumple_sub.get(sub, 0) + 1
        for gk in subproducto_no_comprado:
            if cli_sub.get(gk, 0) < 3:
                subproducto_no_comprado[gk].append({**{k: c[k] for k in
                                                    ("codigo", "razon", "localidad", "seg", "dia")},
                                                    "vendedor": nombre_vend_cli})
        seg = c["seg"] if c["seg"] in SEGMENTOS else None
        if seg:
            segdata = seg_por_vend.setdefault(codven, {s: {"universo": 0, "cumple": 0} for s in SEGMENTOS})
            segdata[seg]["universo"] += 1
            if compra_cliente.get(codigo, 0) >= 3:
                segdata[seg]["cumple"] += 1
        clave_dia = DIA_CLAVE.get(c["dia"])
        if clave_dia:
            nc_dia = nc_dia_por_vend.setdefault(codven, {k: [0, 0] for k in DIA_CLAVE.values()})
            nc_dia[clave_dia][1] += 1
            if es_no_comprador:
                nc_dia[clave_dia][0] += 1

    escribir("no_compradores_detalle.json", no_compradores)
    escribir("pehuamar90_no_comprado.json", pehuamar_no_comprado)
    escribir("subproductos_no_comprado.json", {f"{g}_{k}": v for (g, k), v in subproducto_no_comprado.items()})
    print("No compradores:", len(no_compradores), "| Sin Pehuamar 90gr hoy:", len(pehuamar_no_comprado))

    no_compradores_dia_out = []
    for codven in sorted(nc_dia_por_vend, key=lambda x: int(x)):
        dias = nc_dia_por_vend[codven]
        fila = {"n": nombre_por_codven.get(codven, codven)}
        fila.update(dias)
        fila["totNc"] = sum(v[0] for v in dias.values())
        fila["totU"] = sum(v[1] for v in dias.values())
        no_compradores_dia_out.append(fila)
    escribir("no_compradores_por_dia.json", {"vendedores": no_compradores_dia_out})

    venta_vendedor_out = {n: round(v, 2) for n, v in venta_importe_por_vend.items()}
    escribir("venta_vendedor.json", venta_vendedor_out)
    print("Venta $ por vendedor: %d vendedores, total $ %.0f" %
          (len(venta_vendedor_out), sum(venta_vendedor_out.values())))

    cobertura_vendedores = []
    for codven in sorted(universo_por_vend, key=lambda x: int(x)):
        universo = universo_por_vend[codven]
        cumple_marca = marca_cumple_por_vend.get(codven, {})
        fila = {"codven": codven, "n": nombre_por_codven.get(codven, codven), "universo": universo}
        for label in MARCA_LABEL.values():
            cant = cumple_marca.get(label, 0)
            fila[label] = round(cant / universo * 100, 1) if universo else 0.0
        cobertura_vendedores.append(fila)
    escribir("cobertura_marca_vendedor.json", {"vendedores": cobertura_vendedores})

    subproductos_out = {}
    for grupo, cfg in SUBPRODUCTOS.items():
        filas = []
        for codven in sorted(universo_por_vend, key=lambda x: int(x)):
            universo = universo_por_vend[codven]
            cumple_sub = sub_cumple_por_vend.get(codven, {})
            fila = {"codven": codven, "n": nombre_por_codven.get(codven, codven), "universo": universo}
            for clave in cfg["labels"]:
                fila[clave] = cumple_sub.get((grupo, clave), 0)
            filas.append(fila)
        subproductos_out[grupo] = {"obj": cfg["obj"], "labels": cfg["labels"], "vendedores": filas}
    escribir("subproductos_vendedor.json", subproductos_out)
    print("Subproductos: %d grupos" % len(subproductos_out))
    print("Cobertura por marca: %d vendedores" % len(cobertura_vendedores))

    universo_seg_total = {s: sum(seg_por_vend.get(cv, {}).get(s, {}).get("universo", 0)
                                  for cv in universo_por_vend) for s in SEGMENTOS}
    ccc_vendedores = []
    for codven in sorted(universo_por_vend, key=lambda x: int(x)):
        segdata = seg_por_vend.get(codven, {s: {"universo": 0, "cumple": 0} for s in SEGMENTOS})
        fila = {"n": nombre_por_codven.get(codven, codven)}
        keymap = {"A": ("ao", "ac"), "B": ("bo", "bc"), "C": ("co", "cc"), "D": ("do_", "dc")}
        for s in SEGMENTOS:
            ko, kc = keymap[s]
            uni_total_seg = universo_seg_total[s]
            uni_v = segdata[s]["universo"]
            fila[ko] = round(CCC_OBJETIVO_SEG[s] * uni_v / uni_total_seg) if uni_total_seg else 0
            fila[kc] = segdata[s]["cumple"]
        ccc_vendedores.append(fila)
    objetivo_oficial = dict(CCC_OBJETIVO_SEG)
    objetivo_oficial["total"] = sum(CCC_OBJETIVO_SEG.values())
    escribir("ccc_segmento.json", {"vendedores": ccc_vendedores, "objetivoOficial": objetivo_oficial})
    print("CCC por segmento: %d vendedores" % len(ccc_vendedores))

    dias_habiles = dias_habiles_mes(hoy.year, hoy.month)
    # Dias trabajados = dias COMPLETOS (hasta ayer). El dia en curso no cuenta hasta que termina: asi el "avance a la
    # fecha" no se cae al cambiar de dia (antes el dia sumaba entero apenas entraba el primer pedido, con casi nada
    # vendido). El acumulado ya trae las entregas de pedidos de dias anteriores, por eso es consistente.
    dias_trabajados = dias_habiles_mes(hoy.year, hoy.month, hasta=hoy - dt.timedelta(days=1))
    if not dias_trabajados:
        # primer dia del mes (todavia no hay dia completo): se usa el dia en curso para no mostrar 0%
        dias_trabajados = dias_habiles_mes(hoy.year, hoy.month, hasta=hoy)
    print("Dias trabajados (completos, sin contar hoy): %g de %g" % (dias_trabajados, dias_habiles))
    hist_previo = leer_historial_previo()
    try:
        avance_kg = traer_avance_kg(api, hoy, dias_habiles, dias_trabajados)
        avance_kg, total_gescom = aplicar_override_gescom(avance_kg, hoy, dias_habiles, dias_trabajados)
        if total_gescom:
            print("AVISO: la base trae menos kg que Gescom; se usa el acumulado de Gescom (%.1f kg)" % total_gescom)
        escribir("avance_kg_vendedor.json", avance_kg)
        corregir_historial_diario(filas_detalle, hist_previo, hoy)
        try:
            escribir_productos_segmento(api)
        except Exception as e:
            print("No se pudo armar productos_segmento.json:", e)
        print("Avance kg: %d vendedores | dias %g/%g | objetivo PG %.1f kg | objetivo SB %.1f kg" %
              (len(avance_kg["vendedores"]), dias_trabajados, dias_habiles,
               avance_kg["objetivoPG"], avance_kg["objetivoSB"]))
    except Exception as e:
        print("ERROR trayendo Avance de Ventas Pepsico (se deja avance_kg_vendedor.json anterior sin tocar):", e)

    invendible_out = {
        vend: [{"articulo": art, "cant": round(c, 1), "importe": round(i, 2)}
               for art, (c, i) in sorted(d.items(), key=lambda kv: kv[1][1])]
        for vend, d in invendible_por_vend.items()
    }
    rechazos_out = {
        vend: [{"motivo": m, "cant": round(c, 0), "importe": round(i, 2)}
               for m, (c, i) in sorted(d.items(), key=lambda kv: kv[1][1])]
        for vend, d in rechazos_por_vend.items()
    }
    escribir("invendible_detalle.json", invendible_out)
    escribir("rechazos_detalle.json", rechazos_out)
    print("Invendible: %d vendedores | Rechazos: %d vendedores" %
          (len(invendible_out), len(rechazos_out)))

    if rechazos_fecha_rows:
        ultima_fecha = max(r["fecha"] for r in rechazos_fecha_rows)
        agrupado = {}
        for r in rechazos_fecha_rows:
            if r["fecha"] != ultima_fecha:
                continue
            clave = (r["vendedor"], r["cliente"], r["motivo"])
            acc = agrupado.setdefault(clave, [0.0, 0.0])
            acc[0] += r["cant"]
            acc[1] += r["importe"]
        rechazos_ultima_out = {
            "fecha": ultima_fecha,
            "filas": [
                {"vendedor": vend, "cliente": cli, "motivo": mot, "cant": round(c, 0), "importe": round(i, 2)}
                for (vend, cli, mot), (c, i) in sorted(agrupado.items(), key=lambda kv: kv[1][1])
            ],
        }
        escribir("rechazos_ultima_fecha.json", rechazos_ultima_out)
        print("Rechazos ultima fecha (%s): %d filas" % (ultima_fecha, len(rechazos_ultima_out["filas"])))
    return 0


def cmd_backfill(anio, mes):
    """Carga retroactiva de UN mes ya cerrado, solo a pepsico/archive/AAAA-MM/
    (nunca toca los archivos en vivo). No incluye Tienda Perfecta (el censo de
    Gescom es una foto del estado actual, no se puede consultar a una fecha
    pasada) ni pehuamar90_no_comprado.json (es un concepto de "ruta de hoy",
    no tiene sentido para un mes ya cerrado)."""
    gescom.usar_base()   # desde el 5/10/2026 todo sale de la base propia, nada de Gescom

    global MES_ARCHIVO, SOLO_ARCHIVO
    SOLO_ARCHIVO = True
    MES_ARCHIVO = "%04d-%02d" % (anio, mes)
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    hoy = dt.date(anio, mes, ultimo_dia)
    inicio_mes = hoy.replace(day=1)
    print("Backfill de %s (tomando el mes completo, %s a %s)" % (MES_ARCHIVO, inicio_mes, hoy))
    actualizar_indice_archivo(MES_ARCHIVO)

    clientes_raw, ramos, subramos = gescom.bajar_clientes()
    clientes = {}
    for c in clientes_raw:
        codigo = cod(c.get("codigo"))
        if not codigo:
            continue
        dia, codven = ruta_de(c)
        clientes[codigo] = {
            "codigo": codigo,
            "razon": (c.get("nombre") or c.get("razonSocial") or "").strip(),
            "localidad": (c.get("localidad") or "").strip(),
            "seg": cod(c.get("codigoSegmento")).upper(),
            "dia": dia,
            "codven": codven,
        }

    subgrupo_por_desc = {}
    for grupo, cfg in SUBPRODUCTOS.items():
        for clave, skus in cfg["skus"].items():
            for sku in skus:
                subgrupo_por_desc[sku.upper()] = (grupo, clave)

    api = Api()
    vendedores_raw = api.get("/data/cmd/ventas/api/v1/get-vendedores")
    nombre_por_codven = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores_raw}

    if not hay_base():
        print("Falta el secreto BASE_CLAVE: no se puede leer la base; se dejan los JSON anteriores sin tocar.")
        return 0
    filas = filas_desde_base(api, inicio_mes, hoy)
    print("Filas del detalle de ventas (%s, todos los proveedores): %d" % (MES_ARCHIVO, len(filas)))

    compra_cliente = {}
    compra_cliente_marca = {}
    compra_cliente_subgrupo = {}
    invendible_por_vend = {}
    rechazos_por_vend = {}
    venta_importe_por_vend = {}
    rechazos_fecha_rows = []

    for fila in filas:
        if "PEPSICO" not in (fila.get("Proveedor") or "").upper():
            continue
        tipo = fila.get("TipoDeVenta") or ""
        cli = cod(fila.get("Cliente"))
        articulo = (fila.get("Articulo") or "").strip()
        q = num_ar(fila.get("CantBase"))
        codven = cod(fila.get("CodVendedor"))
        nombre_vend = (fila.get("Vendedor") or "").strip() or nombre_por_codven.get(codven, codven)

        if tipo == TIPO_VENTA:
            compra_cliente[cli] = compra_cliente.get(cli, 0) + q
            marca = MARCA_LABEL.get((fila.get("Marca") or "").strip().upper())
            if marca:
                porcli = compra_cliente_marca.setdefault(cli, {})
                porcli[marca] = porcli.get(marca, 0) + q
            sub = subgrupo_por_desc.get(articulo.upper())
            if sub:
                porcli_sub = compra_cliente_subgrupo.setdefault(cli, {})
                porcli_sub[sub] = porcli_sub.get(sub, 0) + q
            venta_importe_por_vend[nombre_vend] = venta_importe_por_vend.get(nombre_vend, 0.0) + num_ar(fila.get("ImporteNetoItem"))
        elif tipo in (TIPO_CANJE, TIPO_RECHAZO):
            importe = num_ar(fila.get("ImporteNetoItem"))
            if tipo == TIPO_CANJE:
                acc = invendible_por_vend.setdefault(nombre_vend, {}).setdefault(articulo, [0.0, 0.0])
            else:
                motivo = (fila.get("MotivoDevolucion") or "").strip() or "SIN MOTIVO"
                acc = rechazos_por_vend.setdefault(nombre_vend, {}).setdefault(motivo, [0.0, 0.0])
                fecha_comp = (fila.get("FechaComprobante") or "").strip()
                if fecha_comp:
                    rechazos_fecha_rows.append({
                        "fecha": fecha_comp, "vendedor": nombre_vend, "cliente": cli,
                        "motivo": motivo, "cant": abs(q), "importe": importe,
                    })
            acc[0] += abs(q)
            acc[1] += importe

    universo_por_vend = {}
    marca_cumple_por_vend = {}
    sub_cumple_por_vend = {}
    seg_por_vend = {}
    nc_dia_por_vend = {}
    no_compradores = []
    subproducto_no_comprado = {(g, k): [] for g, cfg in SUBPRODUCTOS.items() for k in cfg["labels"]}
    DIA_CLAVE = {"Lunes": "lu", "Martes": "ma", "Miercoles": "mi",
                 "Jueves": "ju", "Viernes": "vi", "Sabado": "sa"}
    for codigo, c in clientes.items():
        if not c["dia"] or c["codven"] not in VENDEDORES_PEPSICO:
            continue
        nombre_vend_cli = nombre_por_codven.get(c["codven"], c["codven"])
        es_no_comprador = compra_cliente.get(codigo, 0) < 3
        if es_no_comprador:
            no_compradores.append({**{k: c[k] for k in
                                    ("codigo", "razon", "localidad", "seg", "dia")},
                                    "vendedor": nombre_vend_cli})
        codven = c["codven"]
        universo_por_vend[codven] = universo_por_vend.get(codven, 0) + 1
        cumple_marca = marca_cumple_por_vend.setdefault(codven, {})
        for marca, cant in compra_cliente_marca.get(codigo, {}).items():
            if cant >= 3:
                cumple_marca[marca] = cumple_marca.get(marca, 0) + 1
        cli_sub = compra_cliente_subgrupo.get(codigo, {})
        cumple_sub = sub_cumple_por_vend.setdefault(codven, {})
        for sub, cant in cli_sub.items():
            if cant >= 3:
                cumple_sub[sub] = cumple_sub.get(sub, 0) + 1
        for gk in subproducto_no_comprado:
            if cli_sub.get(gk, 0) < 3:
                subproducto_no_comprado[gk].append({**{k: c[k] for k in
                                                    ("codigo", "razon", "localidad", "seg", "dia")},
                                                    "vendedor": nombre_vend_cli})
        seg = c["seg"] if c["seg"] in SEGMENTOS else None
        if seg:
            segdata = seg_por_vend.setdefault(codven, {s: {"universo": 0, "cumple": 0} for s in SEGMENTOS})
            segdata[seg]["universo"] += 1
            if compra_cliente.get(codigo, 0) >= 3:
                segdata[seg]["cumple"] += 1
        clave_dia = DIA_CLAVE.get(c["dia"])
        if clave_dia:
            nc_dia = nc_dia_por_vend.setdefault(codven, {k: [0, 0] for k in DIA_CLAVE.values()})
            nc_dia[clave_dia][1] += 1
            if es_no_comprador:
                nc_dia[clave_dia][0] += 1

    escribir("no_compradores_detalle.json", no_compradores)
    escribir("subproductos_no_comprado.json", {f"{g}_{k}": v for (g, k), v in subproducto_no_comprado.items()})
    print("No compradores (%s): %d" % (MES_ARCHIVO, len(no_compradores)))

    no_compradores_dia_out = []
    for codven in sorted(nc_dia_por_vend, key=lambda x: int(x)):
        dias = nc_dia_por_vend[codven]
        fila = {"n": nombre_por_codven.get(codven, codven)}
        fila.update(dias)
        fila["totNc"] = sum(v[0] for v in dias.values())
        fila["totU"] = sum(v[1] for v in dias.values())
        no_compradores_dia_out.append(fila)
    escribir("no_compradores_por_dia.json", {"vendedores": no_compradores_dia_out})

    venta_vendedor_out = {n: round(v, 2) for n, v in venta_importe_por_vend.items()}
    escribir("venta_vendedor.json", venta_vendedor_out)

    cobertura_vendedores = []
    for codven in sorted(universo_por_vend, key=lambda x: int(x)):
        universo = universo_por_vend[codven]
        cumple_marca = marca_cumple_por_vend.get(codven, {})
        fila = {"codven": codven, "n": nombre_por_codven.get(codven, codven), "universo": universo}
        for label in MARCA_LABEL.values():
            cant = cumple_marca.get(label, 0)
            fila[label] = round(cant / universo * 100, 1) if universo else 0.0
        cobertura_vendedores.append(fila)
    escribir("cobertura_marca_vendedor.json", {"vendedores": cobertura_vendedores})

    subproductos_out = {}
    for grupo, cfg in SUBPRODUCTOS.items():
        filas_sp = []
        for codven in sorted(universo_por_vend, key=lambda x: int(x)):
            universo = universo_por_vend[codven]
            cumple_sub = sub_cumple_por_vend.get(codven, {})
            fila = {"codven": codven, "n": nombre_por_codven.get(codven, codven), "universo": universo}
            for clave in cfg["labels"]:
                fila[clave] = cumple_sub.get((grupo, clave), 0)
            filas_sp.append(fila)
        subproductos_out[grupo] = {"obj": cfg["obj"], "labels": cfg["labels"], "vendedores": filas_sp}
    escribir("subproductos_vendedor.json", subproductos_out)

    universo_seg_total = {s: sum(seg_por_vend.get(cv, {}).get(s, {}).get("universo", 0)
                                  for cv in universo_por_vend) for s in SEGMENTOS}
    ccc_vendedores = []
    for codven in sorted(universo_por_vend, key=lambda x: int(x)):
        segdata = seg_por_vend.get(codven, {s: {"universo": 0, "cumple": 0} for s in SEGMENTOS})
        fila = {"n": nombre_por_codven.get(codven, codven)}
        keymap = {"A": ("ao", "ac"), "B": ("bo", "bc"), "C": ("co", "cc"), "D": ("do_", "dc")}
        for s in SEGMENTOS:
            ko, kc = keymap[s]
            uni_total_seg = universo_seg_total[s]
            uni_v = segdata[s]["universo"]
            fila[ko] = round(CCC_OBJETIVO_SEG[s] * uni_v / uni_total_seg) if uni_total_seg else 0
            fila[kc] = segdata[s]["cumple"]
        ccc_vendedores.append(fila)
    objetivo_oficial = dict(CCC_OBJETIVO_SEG)
    objetivo_oficial["total"] = sum(CCC_OBJETIVO_SEG.values())
    escribir("ccc_segmento.json", {"vendedores": ccc_vendedores, "objetivoOficial": objetivo_oficial})

    dias_habiles = dias_habiles_mes(anio, mes)
    try:
        avance_kg = traer_avance_kg(api, hoy, dias_habiles, dias_habiles)
        escribir("avance_kg_vendedor.json", avance_kg)
        print("Avance kg (%s): %d vendedores | objetivo PG %.1f kg | objetivo SB %.1f kg" %
              (MES_ARCHIVO, len(avance_kg["vendedores"]), avance_kg["objetivoPG"], avance_kg["objetivoSB"]))
    except Exception as e:
        print("ERROR trayendo Avance de Ventas Pepsico para %s:" % MES_ARCHIVO, e)
        return 1

    invendible_out = {
        vend: [{"articulo": art, "cant": round(c, 1), "importe": round(i, 2)}
               for art, (c, i) in sorted(d.items(), key=lambda kv: kv[1][1])]
        for vend, d in invendible_por_vend.items()
    }
    rechazos_out = {
        vend: [{"motivo": m, "cant": round(c, 0), "importe": round(i, 2)}
               for m, (c, i) in sorted(d.items(), key=lambda kv: kv[1][1])]
        for vend, d in rechazos_por_vend.items()
    }
    escribir("invendible_detalle.json", invendible_out)
    escribir("rechazos_detalle.json", rechazos_out)

    if rechazos_fecha_rows:
        ultima_fecha = max(r["fecha"] for r in rechazos_fecha_rows)
        agrupado = {}
        for r in rechazos_fecha_rows:
            if r["fecha"] != ultima_fecha:
                continue
            clave = (r["vendedor"], r["cliente"], r["motivo"])
            acc = agrupado.setdefault(clave, [0.0, 0.0])
            acc[0] += r["cant"]
            acc[1] += r["importe"]
        rechazos_ultima_out = {
            "fecha": ultima_fecha,
            "filas": [
                {"vendedor": vend, "cliente": cli, "motivo": mot, "cant": round(c, 0), "importe": round(i, 2)}
                for (vend, cli, mot), (c, i) in sorted(agrupado.items(), key=lambda kv: kv[1][1])
            ],
        }
        escribir("rechazos_ultima_fecha.json", rechazos_ultima_out)

    print("Backfill de %s completo." % MES_ARCHIVO)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "backfill":
        sys.exit(cmd_backfill(int(sys.argv[2]), int(sys.argv[3])))

    # El workflow de GitHub Actions no se puede tocar sin permiso extra, asi
    # que un backfill se pide dejando pepsico/.backfill_pendiente con
    # "AAAA-MM" (via la Contents API) y disparando el workflow normal, sin
    # argumentos. Si existe, se corre el backfill primero, se borra el
    # marcador (queda commiteado junto con el resto de pepsico/) y despues
    # sigue la corrida normal del mes actual.
    marcador = DIR / ".backfill_pendiente"
    if marcador.exists():
        mes_pendiente = marcador.read_text(encoding="utf-8").strip()
        marcador.unlink()
        try:
            anio_p, mes_p = mes_pendiente.split("-")
            print("Ejecutando backfill pendiente:", mes_pendiente)
            codigo = cmd_backfill(int(anio_p), int(mes_p))
            if codigo != 0:
                sys.exit(codigo)
        except Exception as e:
            print("ERROR en backfill pendiente de %r:" % mes_pendiente, e)

    sys.exit(main())
