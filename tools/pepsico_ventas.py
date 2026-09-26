#!/usr/bin/env python3
"""
pepsico_ventas.py - datos del panel Avance Pepsico via API de GesCom.
Genera no_compradores_detalle.json, no_compradores_por_dia.json,
pehuamar90_no_comprado.json, invendible_detalle.json, rechazos_detalle.json,
cobertura_marca_vendedor.json, ccc_segmento.json, subproductos_vendedor.json,
venta_vendedor.json y avance_kg_vendedor.json.

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
GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
"""
import calendar
import csv
import datetime as dt
import io
import json
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
CCC_OBJETIVO_SEG = {"A": 384, "B": 337, "C": 669, "D": 904}

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


def dias_habiles_mes(anio, mes, hasta=None):
    """Cuenta dias lunes a sabado (domingo no es habil) del mes. Si `hasta`
    se pasa, cuenta solo hasta esa fecha inclusive (dias trabajados)."""
    d = dt.date(anio, mes, 1)
    total = 0
    while d.month == mes:
        if d.weekday() != 6:
            if hasta is None or d <= hasta:
                total += 1
        d += dt.timedelta(days=1)
    return total


def traer_avance_kg(api, hoy, dias_habiles, dias_trabajados):
    """Trae el Avance de Ventas Pepsico (kg) directo del reporte de Gescom, ya
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


def escribir(nombre, data):
    ruta = DIR / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Guardado", ruta)


def main():
    if not gescom.hay_credenciales():
        print("Sin credenciales de GesCom: no se corre.")
        return 0

    hoy = dt.datetime.now(TZ_AR).date()
    inicio_mes = hoy.replace(day=1)

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

    # DIAG Tienda Perfecta: explorar el reporte "Censo Tienda Perfecta" para
    # ver que parametros pide y que columnas devuelve.
    GUID_TP = "81221fe2-5545-47ec-a149-6c95d47afb44"
    info = api.post("/data/cmd/report/info", {"id": GUID_TP})
    print("DIAG TP info:", json.dumps(info, ensure_ascii=False)[:6000])
    try:
        params_default = {}
        for grupo in info.get("parametersGroups") or []:
            for p in grupo.get("parameters") or []:
                if p.get("default") is not None:
                    params_default[p["name"]] = p["default"]
        print("DIAG TP params por default:", json.dumps(params_default, ensure_ascii=False))
        ahora = dt.datetime.now().timestamp()
        if not api._tok or ahora - api._t > 240:
            api._tok, api._t = gescom._token(api.s), ahora
        body = {"id": GUID_TP, "reportInput": {"filtersInput": {}, "parameters": params_default}}
        r = api.s.post(gescom.API + "/data/cmd/report/render", json=body, timeout=240,
                        headers={"Authorization": "Bearer " + api._tok})
        ct = r.headers.get("Content-Type", "")
        print("DIAG TP render status:", r.status_code, "content-type:", ct, "len:", len(r.content))
        if "json" in ct:
            data = r.json()
            print("DIAG TP datasources:", [(d.get("name"), len(d.get("table", []))) for d in data.get("datasources") or []])
            for d in data.get("datasources") or []:
                tabla = d.get("table") or []
                print("DIAG TP", d.get("name"), "header:", tabla[0] if tabla else None)
                if len(tabla) > 2:
                    print("DIAG TP", d.get("name"), "fila1:", tabla[1])
                    print("DIAG TP", d.get("name"), "fila2:", tabla[2])
                # valores distintos de columnas clave para entender el formato
                if tabla:
                    header = tabla[0]
                    for col in ("TiendaPerfecta", "SupOk", "Segmento", "SubCanal", "SubCanalTiendaPerfecta", "Vendedor_ID"):
                        if col in header:
                            i = header.index(col)
                            valores = {}
                            for fila in tabla[1:]:
                                v = fila[i]
                                valores[v] = valores.get(v, 0) + 1
                            print("DIAG TP valores de", col, ":", valores)
        else:
            texto = r.content.decode("cp1252", errors="replace")
            print("DIAG TP CSV primera linea:", texto.split(chr(13))[0][:2000])
            print("DIAG TP CSV total lineas:", texto.count(chr(10)))
    except Exception as e:
        print("DIAG TP render error:", type(e).__name__, e)

    vendedores_raw = api.get("/data/cmd/ventas/api/v1/get-vendedores")
    nombre_por_codven = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores_raw}

    fin_mes_excl = hoy + dt.timedelta(days=1)
    filas = api.render_csv(GUID_DETALLE_VENTAS, {
        "empId": 0,
        "fechaIni": inicio_mes.isoformat() + "T03:00:00.000Z",
        "fechaFin": fin_mes_excl.isoformat() + "T03:00:00.000Z",
        "facturadaId": 2,
        "supervisorId": 0,
        "sedeId": 0,
        "tipoVentaId": 2,
        "ventaEmpl": 0,
        "cliActivos": 0,
    })
    print("Filas del detalle de ventas (mes en curso, todos los proveedores):", len(filas))

    compra_cliente = {}
    compra_cliente_marca = {}
    compra_cliente_subgrupo = {}
    pehuamar_compra = {}
    invendible_por_vend = {}
    rechazos_por_vend = {}
    venta_importe_por_vend = {}

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
            acc[0] += abs(q)
            acc[1] += importe

    print("DIAG items de venta Pepsico contados:", items_pepsico_vistos,
          "| clientes con al menos 1 unidad:", len(compra_cliente))

    DIA_CLAVE = {"Lunes": "lu", "Martes": "ma", "Miercoles": "mi",
                 "Jueves": "ju", "Viernes": "vi", "Sabado": "sa"}

    hoy_key = DIAS_CAP[hoy.weekday()]
    no_compradores = []
    pehuamar_no_comprado = []
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
        cumple_sub = sub_cumple_por_vend.setdefault(codven, {})
        for sub, cant in compra_cliente_subgrupo.get(codigo, {}).items():
            if cant >= 3:
                cumple_sub[sub] = cumple_sub.get(sub, 0) + 1
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
    dias_trabajados = dias_habiles_mes(hoy.year, hoy.month, hasta=hoy)
    try:
        avance_kg = traer_avance_kg(api, hoy, dias_habiles, dias_trabajados)
        escribir("avance_kg_vendedor.json", avance_kg)
        print("Avance kg: %d vendedores | dias %d/%d | objetivo PG %.1f kg | objetivo SB %.1f kg" %
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
