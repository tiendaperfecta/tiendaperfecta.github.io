#!/usr/bin/env python3
"""
pepsico_ventas.py - datos del panel Avance Pepsico via API de GesCom.
Genera no_compradores_detalle.json, pehuamar90_no_comprado.json,
invendible_detalle.json, rechazos_detalle.json, cobertura_marca_vendedor.json,
ccc_segmento.json, subproductos_vendedor.json y avance_kg_vendedor.json.

Avance kg: Objetivo es un valor fijo mensual (KG_OBJETIVO_PG/SB, actualizar a
mano cuando cambie), prorrateado por vendedor segun composicion de clientes.
Acumulado sale de factorPeso (peso en gramos) x cantidad vendida este mes.
Platino+Gold / Silver&Bronze se arma con codigoSegmento A+B / C+D del cliente
- NO esta confirmado contra la clasificacion original *P01/*P02 del reporte
Avance de Ventas Pepsico de Gescom, es la mejor aproximacion disponible por API.

Cobertura por marca: matching de marca por substring en la descripcion del
articulo (no hay campo de marca legible en la API, solo codigoMarca opaco
tipo "pepsico-15"). Aproximacion, no un campo estructurado.

Validado contra la API real (24/09): No compradores 479 (vs ~490 manual),
sin Pehuamar 90gr hoy 382 (vs 392 manual).

Tipo de venta confirmado contra datos reales: DEV-CA = Devolucion por Canje,
DEV-RE = Devolucion por Rechazo. El motivo esta en el campo motivo de la
venta (no del item).

Credenciales por variables de entorno (GitHub Secrets):
GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
"""
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom
import requests

DIR = Path(__file__).resolve().parent.parent / "pepsico"
TZ_AR = dt.timezone(dt.timedelta(hours=-3))

MARCAS_PEPSICO = ["3D", "CHEETOS", "DORITOS", "LAYS", "PEHUAMAR", "PEP", "QUAKER", "TOSTITOS", "TWISTOS"]
MARCA_LABEL = {
    "3D": "3Ds", "CHEETOS": "Cheetos", "DORITOS": "Doritos", "LAYS": "Lays",
    "PEHUAMAR": "Pehuamar", "PEP": "Pep", "QUAKER": "Quaker", "TOSTITOS": "Tostitos", "TWISTOS": "Twistos",
}
SEGMENTOS = ["A", "B", "C", "D"]
CCC_OBJETIVO_SEG = {"A": 384, "B": 337, "C": 669, "D": 903}

# Objetivo mensual en kg (Platino+Gold / Silver&Bronze), confirmado por el usuario.
# Actualizar a mano cuando cambie el objetivo del mes.
KG_OBJETIVO_PG = 18000.0
KG_OBJETIVO_SB = 8000.0
# Clasificacion de PRODUCTO (no de cliente) en Platino/Gold/Silver/Bronze,
# confirmada por el usuario contra el reporte "Segmentacion del portafolio"
# de Gescom. Platino+Gold y Silver+Bronze se arman agrupando estos 4 tiers.
TIER_POR_DESC = {
    "PEP COMUN 40GX60 PI": "Platinum",
    "PEP RUEDITAS 40GX60 PI": "Platinum",
    "QUAKER AVENA TRADIC 18X280G": "Silver",
    "QUAKER AVENA EXTRA FINA 18X470G": "Silver",
    "PEHUAMAR PALIQUESO 90GX36": "Gold",
    "PEHUAMAR PALISAL 90GX36": "Gold",
    "PEP COMUN 120GRX21": "Gold",
    "PEP COMUN 84GRX36": "Platinum",
    "PEHUAMAR MAICITOS 125GX16": "Silver",
    "PEP RUEDITAS 74GRX36": "Platinum",
    "PEP RUEDITAS 120GRX21": "Gold",
    "TWISTOS MINIT JAMON 155GX20": "Silver",
    "TWISTOS MINIT QUESO 155GX20": "Silver",
    "CHEETOS 23GRX108": "Platinum",
    "LAYS CLASICAS 20GRX76": "Platinum",
    "3DMEGAQUESO23GX120": "Platinum",
    "3D QUESO 143GX18X1": "Silver",
    "3D QUESO 43GX75X1": "Platinum",
    "QUAKER AVENA INSTANT 18X280G ARG": "Silver",
    "QUAKER AVENA INSTANT 18X500G ARG": "Silver",
    "QUAKER AVENA TRADIC 18X550G ARG": "Silver",
    "PEP RAMITAS QUESO 40GX60": "Platinum",
    "PEP RAMITAS QUESO 84GX36": "Platinum",
    "PEP RAMITAS QUESO 120GX21": "Gold",
    "DORITOS QUESO 40GX70X1": "Platinum",
    "DORITOS QUESO 40GX58X1 CH": "Platinum",
    "CHEETOS QUESO 43GX70X1": "Platinum",
    "LAYS CLASICAS 40GX68X1": "Platinum",
    "LAYS ONDAS FH 30GX72": "Platinum",
    "LAYS ONDAS FH 70GX28": "Gold",
    "LAYS QSO Y CEBOLLA 34GX72": "Platinum",
    "LAYS JAMON SERRANO 34GX72": "Platinum",
    "LAYS CLASICAS 85GX25X1": "Gold",
    "LAYS CLASICAS 330GX9": "Silver",
    "LAYS JAMON SERRANO 122GX19": "Silver",
    "DORITOS QUESO 77GX26": "Gold",
    "DORITOS QUESO 129GX19": "Silver",
    "DORITOS QUESO 200GX14": "Silver",
    "PEHUAMAR PAPA LISA 135GX19X1": "Silver",
    "PEHUAMAR PAPA LISA 230GX14X1": "Bronze",
    "PEHUAMAR PAPA ACANA 135GX19": "Silver",
    "PEHUAMAR PALISAL 165GX21X1": "Silver",
    "PEHUAMAR PALISAL 620GX6X1": "Bronze",
    "PEHUAMAR PALIQUESO 165GX21X1": "Silver",
    "PEHUAMAR PALIQUESO 620GX6X1": "Bronze",
    "LAYS KETCHUP 34GX72X1": "Platinum",
    "CHEETOS QUESO 85GX24X1": "Gold",
    "CHEETOS QUESO 140GX18X1": "Silver",
    "CHEETOS QUESO 229GX12X1": "Silver",
    "3D QUESO 85GX27X1": "Gold",
    "MANI SAL CON PIEL 75GX64X1": "Gold",
    "MANI PELADO 135GX40X1": "Gold",
    "MANI PELADO 320GX17X1": "Silver",
    "MANIAX JAPONES JAMON 95GX40X1": "Gold",
    "MANIAX JAPONES SAL 95GX40X1": "Gold",
    "MANIAX SAL Y LIMON 95GX60X1": "Gold",
    "DORITOS QUESO 77GX17 EXP ARG": "Gold",
    "DORITOS QUESO 129GX17 EXP ARG": "Silver",
    "CHEETOS QUESO CREMA 43GX66": "Platinum",
    "CHEETOS QUESO CREMA 85GX24": "Gold",
    "LAYS PROVOLETA 77GX25X1": "Gold",
    "PEHUAMAR MAICITOS 265GX10X1": "Bronze",
    "PEHUAMAR ACANALADA 230GX14X1": "Bronze",
    "TWISTOS MINIT JAMON 95GX30X1": "Gold",
    "TWISTOS MINIT QUESO 95GX30X1": "Gold",
    "MANI SAL PELADO 75GX64X1": "Gold",
    "TWISTOS MINIT QUESO 40GX112X1": "Platinum",
    "TWISTOS MINIT JAMON 40GX112X1": "Platinum",
    "MANI CON PIEL 135GX40X1": "Gold",
    "DORITOS QUESO 20GX88X1": "Platinum",
    "DORITOS SWEET CHILI SB 35GX88X1": "Platinum",
    "DORITOS SWEET CHILI MB 74GX29X1": "Gold",
    "DORITOS DINAMITA FH 45GX110X1": "Platinum",
    "DORITOS DINAMITA FH 82GX38X1": "Gold",
    "PEHUAMAR ACAN CHIMICHURRI 80X25X1": "Gold",
    "PEHUAMAR ACAN MATAMBRITO 80X25X1": "Gold",
    "PEHUAMAR ACANALADA 450X9 RM": "Bronze",
    "PEHUAMAR PAPA LISA 450GX9 RM": "Bronze",
    "PEHUA PAPA ACANA 90GX22 RM": "Gold",
    "PEHUA PAPA LISA 90GX22 RM": "Gold",
    "LAYS PANCETA 77GX25X1": "Gold",
    "LAYS PANCETA 34GX72X1": "Platinum",
    "LAYS BARBACOA 77GX25X1": "Gold",
    "LAYS BARBACOA 34GX72X1": "Platinum",
    "MANI TUBULAR 40GRS": "Platinum",
    "LAYS JAMON SERRANO 77GX25X1": "Gold",
    "PEP RUEDITAS FLAMIN HOT 71G": "Platinum",
    "DINAMITA EXTRA FLAMIN HOT 45G": "Platinum",
    "DINAMITA EXTRA FLAMIN HOT 82G": "Gold",
    "LAYS CLASICAS 134GX18X1": "Silver",
    "LAYS CLASICAS 230GX13X1": "Silver",
    "LAYS QUESO Y CEBOLLA 77GX25X1": "Gold",
    "LAYS KETCHUP 77GX25X1": "Gold",
    "TOSTITOS ROUNDED SAL 100 GRAMOS": "Gold",
    "TOSTITOS ROUNDED SAL 160 GRAMOS": "Silver",
    "TOSTITOS ROUNDED SAL 260 GRAMOS": "Silver",
    "QUAKER AVENA TRADIC 280GX20 ARG": "Silver",
    "QUAKER AVENA EXT FINA 470GX12 ARG": "Silver",
    "QUAKER AVENA INST 280GX20 ARG": "Silver",
    "QUAKER AVENA INST 500GX10 ARG": "Silver",
    "QUAKER AVENA TRADIC 550GX12 ARG": "Silver",
    "LAYS CLASICA 20GX6X10 TIR": "Platinum",
    "DORITOS QUESO 20GX6X10 TIR": "Platinum",
    "CHEETOS ONDULADOS KETCHUP 40GX66": "Platinum",
    "CHEETOS ONDULADOS KETCHUP 80GX24": "Gold",
    "LAYS CLASICA 40GX6X10 TIR": "Platinum",
    "DORITOS PIZZA 74X29X1": "Gold",
    "DORITOS PIZZA 35X88X1": "Platinum",
    "DORITOS QUESO 40GX6X10 TIR": "Platinum",
    "LAYS ACAN ASADO 77GX25X1": "Gold",
    "LAYS ACAN ASADO 34GX72X1 MND": "Platinum",
    "PEHUAMAR ACANALADA 420X9X1 RM": "Bronze",
    "PEHUAMAR PAPA LISA 420GX9X1 RM": "Bronze",
    "TOSTITOS ROUND HIERB Y LIMON 90GX26X1": "Gold",
    "TOSTITOS ROUND HIERB Y LIMON 144GX19X1": "Silver",
    "LAYS RUSTICAS SAL MARINA 85 GRAMOS": "Gold",
    "LAYS RUSTICAS CAPRESE 77 GRAMOS": "Gold",
    "LAYS RUSTICAS LIMON Y HIERBAS 77 GRAMOS": "Gold",
}
TIER_GROUP = {"Platinum": "pg", "Gold": "pg", "Silver": "sb", "Bronze": "sb"}
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

SIGNO = {
    "VEN": 1, "AJU-MAS": 1, "DEB": 1, "SC": 1, "COM-P": 1,
    "DEV-RE": -1, "DEV-CA": -1, "AJU-MEN": -1, "COM-PD": -1,
}
TIPO_RECHAZO = "DEV-RE"
TIPO_CANJE = "DEV-CA"


def cod(v):
    return str(v if v is not None else "").strip()


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def es_pepsico(descripcion):
    d = (descripcion or "").upper()
    return any(m in d for m in MARCAS_PEPSICO)


def marca_de(descripcion):
    d = (descripcion or "").upper()
    for m in MARCAS_PEPSICO:
        if m in d:
            return MARCA_LABEL[m]
    return None


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

    def ventas(self, desde, hasta_excl):
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


def dia_de_ruta(cliente_raw):
    for r in cliente_raw.get("rutasPreventa") or []:
        for i, d in enumerate(DIAS):
            if r.get(d):
                return DIAS_CAP[i]
    return None


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
        clientes[codigo] = {
            "codigo": codigo,
            "razon": (c.get("nombre") or c.get("razonSocial") or "").strip(),
            "localidad": (c.get("localidad") or "").strip(),
            "seg": cod(c.get("codigoSegmento")).upper(),
            "dia": dia_de_ruta(c),
            "codven": cod((c.get("rutasPreventa") or [{}])[0].get("codigoVendedor")) if c.get("rutasPreventa") else "",
        }

    subgrupo_por_desc = {}
    for grupo, cfg in SUBPRODUCTOS.items():
        for clave, skus in cfg["skus"].items():
            for sku in skus:
                subgrupo_por_desc[sku.upper()] = (grupo, clave)

    api = Api()

    vendedores_raw = api.get("/data/cmd/ventas/api/v1/get-vendedores")
    nombre_por_codven = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores_raw}

    articulos = api.get("/data/cmd/inventario/api/v2/get-articulos")


    es_pepsico_por_clave = {}
    es_pehuamar90_por_clave = {}
    descripcion_por_clave = {}
    peso_por_clave = {}
    todas_claves = set()
    for a in articulos:
        clave = (cod(a.get("codigo")), cod(a.get("codigoEmpresa")))
        todas_claves.add(clave)
        desc = (a.get("descripcion") or "").strip()
        descripcion_por_clave[clave] = desc
        peso_por_clave[clave] = num(a.get("factorPeso"))
        if es_pepsico(desc.upper()):
            es_pepsico_por_clave[clave] = True
        if desc.upper() in PEHUAMAR_SKUS:
            es_pehuamar90_por_clave[clave] = True

    def clave_equivalente(codigo, empresa):
        e = "1" if empresa == "99" else empresa
        otra = "2" if e == "1" else "1"
        return (codigo, e) if (codigo, e) in todas_claves else (codigo, otra)

    def es_articulo_pepsico(codigo, empresa):
        return es_pepsico_por_clave.get(clave_equivalente(codigo, empresa), False)

    def es_articulo_pehuamar90(codigo, empresa):
        return es_pehuamar90_por_clave.get(clave_equivalente(codigo, empresa), False)

    def descripcion_de(codigo, empresa):
        return descripcion_por_clave.get(clave_equivalente(codigo, empresa)) or codigo

    def peso_de(codigo, empresa):
        return peso_por_clave.get(clave_equivalente(codigo, empresa), 0.0)

    print("Articulos Pepsico encontrados:", len(es_pepsico_por_clave),
          "| SKUs Pehuamar 90gr:", len(es_pehuamar90_por_clave))

    ventas = api.ventas(inicio_mes.isoformat(), (hoy + dt.timedelta(days=1)).isoformat())
    print("Ventas traidas (mes en curso):", len(ventas))

    compra_cliente = {}
    compra_cliente_marca = {}
    compra_cliente_subgrupo = {}
    kg_por_cliente_fecha = {}
    kg_grupo_por_cliente = {}
    kg_sin_clasificar = 0.0
    pehuamar_compra = {}
    invendible_por_vend = {}
    rechazos_por_vend = {}

    items_pepsico_vistos = 0
    for v in ventas:
        tipo = cod(v.get("codigoTipoVenta"))
        if cod(v.get("estado")).lower().startswith("anul"):
            continue
        emp = cod(v.get("codigoEmpresa"))
        cli = cod(v.get("codigoCliente"))
        codven = cod(v.get("codigoVendedor"))
        nombre_vend = nombre_por_codven.get(codven, codven)
        motivo = (v.get("motivo") or "").strip() or "SIN MOTIVO"
        signo = SIGNO.get(tipo, 1)
        fecha_pedido = (v.get("fechaPedido") or "")[:10]

        for it in v.get("items") or []:
            codigo_it = cod(it.get("codigoItem"))
            q = num(it.get("cantidad")) * num(it.get("unidadFactor") or 1)
            importe = num(it.get("importeNeto"))

            if tipo == "VEN" and es_articulo_pepsico(codigo_it, emp):
                compra_cliente[cli] = compra_cliente.get(cli, 0) + q
                items_pepsico_vistos += 1
                desc_it = descripcion_de(codigo_it, emp)
                marca = marca_de(desc_it)
                if marca:
                    porcli = compra_cliente_marca.setdefault(cli, {})
                    porcli[marca] = porcli.get(marca, 0) + q
                if fecha_pedido:
                    kg = q * peso_de(codigo_it, emp) / 1000.0
                    porfecha = kg_por_cliente_fecha.setdefault(cli, {})
                    porfecha[fecha_pedido] = porfecha.get(fecha_pedido, 0.0) + kg
                    tier = TIER_POR_DESC.get(desc_it.upper())
                    grupo = TIER_GROUP.get(tier)
                    if grupo:
                        porgrupo = kg_grupo_por_cliente.setdefault(cli, {"pg": 0.0, "sb": 0.0})
                        porgrupo[grupo] += kg
                    else:
                        kg_sin_clasificar += kg
            if tipo == "VEN" and es_articulo_pehuamar90(codigo_it, emp):
                pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q
            if tipo == "VEN":
                sub = subgrupo_por_desc.get(descripcion_de(codigo_it, emp).upper())
                if sub:
                    porcli_sub = compra_cliente_subgrupo.setdefault(cli, {})
                    porcli_sub[sub] = porcli_sub.get(sub, 0) + q

            if not es_articulo_pepsico(codigo_it, emp):
                continue

            if tipo == TIPO_CANJE:
                art = descripcion_de(codigo_it, emp)
                acc = invendible_por_vend.setdefault(nombre_vend, {}).setdefault(art, [0.0, 0.0])
                acc[0] += q
                acc[1] += importe * signo
            elif tipo == TIPO_RECHAZO:
                acc = rechazos_por_vend.setdefault(nombre_vend, {}).setdefault(motivo, [0.0, 0.0])
                acc[0] += q
                acc[1] += importe * signo

    print("DIAG items de venta Pepsico contados:", items_pepsico_vistos,
          "| clientes con al menos 1 unidad:", len(compra_cliente))

       # Solo los 12 vendedores de calle de Pepsico (codigos 1-12). El cliente
    # trae a veces otros codigos (deposito, otros canales) que no son parte
    # de este tablero.
    VENDEDORES_PEPSICO = {str(i) for i in range(1, 13)}

    hoy_key = DIAS_CAP[hoy.weekday()]
    no_compradores = []
    pehuamar_no_comprado = []
    universo_por_vend = {}
    marca_cumple_por_vend = {}
    sub_cumple_por_vend = {}
    seg_por_vend = {}
    kg_acum_por_vend = {}
    kg_hoy_por_vend = {}
    kg_ultima_por_vend = {}
    kg_penult_por_vend = {}
    hoy_str = hoy.isoformat()
    for codigo, c in clientes.items():
        if not c["dia"] or c["codven"] not in VENDEDORES_PEPSICO:
            continue
        nombre_vend_cli = nombre_por_codven.get(c["codven"], c["codven"])
        if compra_cliente.get(codigo, 0) < 3:
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

        fechas_kg = kg_por_cliente_fecha.get(codigo, {})
        if fechas_kg:
            grupo_kg = kg_grupo_por_cliente.get(codigo, {"pg": 0.0, "sb": 0.0})
            acc = kg_acum_por_vend.setdefault(codven, {"pg": 0.0, "sb": 0.0})
            acc["pg"] += grupo_kg["pg"]
            acc["sb"] += grupo_kg["sb"]
            kg_hoy_por_vend[codven] = kg_hoy_por_vend.get(codven, 0.0) + fechas_kg.get(hoy_str, 0.0)
            fechas_ordenadas = sorted((f for f in fechas_kg if f <= hoy_str), reverse=True)
            if fechas_ordenadas:
                kg_ultima_por_vend[codven] = kg_ultima_por_vend.get(codven, 0.0) + fechas_kg[fechas_ordenadas[0]]
            if len(fechas_ordenadas) > 1:
                kg_penult_por_vend[codven] = kg_penult_por_vend.get(codven, 0.0) + fechas_kg[fechas_ordenadas[1]]

    escribir("no_compradores_detalle.json", no_compradores)
    escribir("pehuamar90_no_comprado.json", pehuamar_no_comprado)
    print("No compradores:", len(no_compradores), "| Sin Pehuamar 90gr hoy:", len(pehuamar_no_comprado))

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
    escribir("ccc_segmento.json", {"vendedores": ccc_vendedores})
    print("CCC por segmento: %d vendedores" % len(ccc_vendedores))

    dias_habiles = dias_habiles_mes(hoy.year, hoy.month)
    dias_trabajados = dias_habiles_mes(hoy.year, hoy.month, hasta=hoy)
    ratio = dias_trabajados / dias_habiles if dias_habiles else 0
    # El objetivo por vendedor se prorratea segun su participacion en el
    # universo total de clientes (no hay objetivo individual oficial por
    # vendedor disponible via API, es una estimacion proporcional; usar el
    # kg real como base de prorrateo haria que el Avance% de todos cierre
    # igual, porque se cancela con el Acumulado real).
    total_universo = sum(universo_por_vend.values())
    kg_vendedores = []
    for codven in sorted(universo_por_vend, key=lambda x: int(x)):
        acc = kg_acum_por_vend.get(codven, {"pg": 0.0, "sb": 0.0})
        participacion = universo_por_vend[codven] / total_universo if total_universo else 0.0
        p1o = round(KG_OBJETIVO_PG * participacion, 2)
        p2o = round(KG_OBJETIVO_SB * participacion, 2)
        p1a = round(acc["pg"], 2)
        p2a = round(acc["sb"], 2)
        p1p = round(p1a / (p1o * ratio) * 100, 2) if p1o and ratio else 0.0
        p2p = round(p2a / (p2o * ratio) * 100, 2) if p2o and ratio else 0.0
        to = round(p1o + p2o, 2)
        ta = round(p1a + p2a, 2)
        tp = round(ta / (to * ratio) * 100, 2) if to and ratio else 0.0
        promedio = round(ta / dias_trabajados, 2) if dias_trabajados else 0.0
        medianec = round((to - ta) / (dias_habiles - dias_trabajados), 2) if dias_habiles > dias_trabajados else 0.0
        tendencia = round(ta / dias_trabajados * dias_habiles, 2) if dias_trabajados else 0.0
        kg_vendedores.append({
            "n": nombre_por_codven.get(codven, codven),
            "p1o": p1o, "p1a": p1a, "p1p": p1p,
            "p2o": p2o, "p2a": p2a, "p2p": p2p,
            "to": to, "ta": ta, "tp": tp,
            "promedio": promedio, "medianec": medianec, "tendencia": tendencia,
            "real": round(kg_hoy_por_vend.get(codven, 0.0), 2),
            "penult": round(kg_penult_por_vend.get(codven, 0.0), 2),
            "ultima": round(kg_ultima_por_vend.get(codven, 0.0), 2),
        })
    escribir("avance_kg_vendedor.json", {
        "objetivoPG": KG_OBJETIVO_PG, "objetivoSB": KG_OBJETIVO_SB,
        "diasHabiles": dias_habiles, "diasTrabajados": dias_trabajados,
        "vendedores": kg_vendedores,
    })
    total_pg_kg = sum(a["pg"] for a in kg_acum_por_vend.values())
    total_sb_kg = sum(a["sb"] for a in kg_acum_por_vend.values())
    print("Avance kg: %d vendedores | dias %d/%d | total PG %.1f kg | total SB %.1f kg | sin clasificar %.1f kg" %
          (len(kg_vendedores), dias_trabajados, dias_habiles, total_pg_kg, total_sb_kg, kg_sin_clasificar))

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
