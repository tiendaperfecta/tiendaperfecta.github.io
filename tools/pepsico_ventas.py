#!/usr/bin/env python3
"""
pepsico_ventas.py — pepsico/{no_compradores_detalle,invendible_detalle,
rechazos_detalle,pehuamar90_no_comprado}.json, por API de GesCom.

*** EN VALIDACION — ya corrio una vez contra la API real (24/09, workflow_dispatch) ***
Primer corrida real: 113 articulos Pepsico identificados, 9.569 ventas del mes
traidas, las 2 SKUs de Pehuamar 90gr matchearon exacto, y "sin Pehuamar 90gr
hoy" dio 401 (contra 392 del ultimo export manual, cerca). "No compradores"
dio 1.121 contra ~490 del export manual — con un desvio grande. Se encontro
una causa concreta y se corrigio: el maestro de articulos separa por empresa
(1 vs 99/Pex) y una venta de la empresa 99 puede corresponder a un articulo
cargado solo en la empresa 1 (el mismo problema que georgalos.maestro_articulos()
ya resuelve con un fallback); esta version ya usa el mismo fallback. Falta una
segunda corrida para confirmar si esto cierra la diferencia.

Lo que sigue sin confirmar:
  - fechaCarga: georgalos.py NO usa este campo (usa fechaComprobante o, si no
    hay, fechaPedido). Todo este proyecto encontro que Gescom arrastra ventas
    con comprobante pendiente que solo aparecen por FechaCarga, asi que puede
    hacer falta ese campo aca tambien. Si el registro de venta no lo trae hay
    que revisar con GesCom que campo corresponde.
  - Marca por articulo: se identifica buscando estas palabras en la
    descripcion del articulo (mismo patron ya verificado contra los CSV de
    venta reales en tools locales de este proyecto, ver _cobertura_marca_v3_csv
    en el historial del tablero): 3Ds, Cheetos, Doritos, Lays, Pehuamar, Pep,
    Quaker, Tostitos, Twistos. Encontro 113 articulos en la corrida real — no
    se confirmo a mano si son todos los que corresponden o si falta alguno.
  - codigoTipoVenta "DEV-RE" = Devolucion por Rechazo, "DEV-CA" = Devolucion
    por Canje: se dedujo por analogia con el diccionario SIGNO de
    georgalos.py (que si esta verificado), pero no se confirmo el texto
    exacto que corresponde a cada codigo para GesCom TP/Pex.

Antes de dejar esto corriendo solo hace falta: (1) que alguien con permisos
agregue el workflow (ver tools/pepsico-data.workflow.yml.txt en esta misma
carpeta) o corra esto una vez a mano con las 4 variables de entorno puestas,
y (2) revisar los prints de conteos contra lo que ya sabemos de los reportes
manuales de septiembre para confirmar que da lo mismo.

Credenciales por variables de entorno (GitHub Secrets), nunca en el repo:
    GESCOM_REALM, GESCOM_CLIENT_ID, GESCOM_USERNAME, GESCOM_PASSWORD
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gescom  # noqa: E402
import requests

DIR = Path(__file__).resolve().parent.parent / "pepsico"
TZ_AR = dt.timezone(dt.timedelta(hours=-3))

MARCAS_PEPSICO = ["3DS", "CHEETOS", "DORITOS", "LAYS", "PEHUAMAR", "PEP", "QUAKER", "TOSTITOS", "TWISTOS"]
PEHUAMAR_SKUS = {"PEHUA PAPA LISA 90GX22 RM", "PEHUA PAPA ACANA 90GX22 RM"}
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_CAP = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

# Igual que georgalos.py: codigos de tipo de venta y su signo. "DEV-RE" y
# "DEV-CA" son la parte NO confirmada (ver aviso arriba).
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


class Api:
    """Igual a georgalos.Api: token propio, reintentos, paginado de a 7 dias."""
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


def fecha_de(v):
    """Igual que georgalos.fecha_venta: comprobante si existe, si no el pedido.
    OJO: no usa fechaCarga porque no se confirmo si el endpoint la trae (ver
    aviso al principio del archivo)."""
    comp = v.get("comprobantePrincipal") or {}
    f = comp.get("fechaComprobante") or v.get("fechaPedido") or ""
    return f[:10]


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
        print("Sin credenciales de GesCom: no se corre (quedan los archivos ya publicados).")
        return 0

    hoy = dt.datetime.now(TZ_AR).date()
    inicio_mes = hoy.replace(day=1)

    # --- clientes: mismo endpoint que ya usa gescom.py, probado en produccion ---
    clientes_raw, ramos, subramos = gescom.bajar_clientes()
    clientes = {}
    for c in clientes_raw:
        codigo = cod(c.get("codigo"))
        if not codigo:
            continue
        clientes[codigo] = {
            "codigo": codigo,
            "razon": (c.get("nombre") or c.get("razonSocial") or "").strip(),
            "direccion": (c.get("direccionEntrega") or "").strip(),
            "localidad": (c.get("localidad") or "").strip(),
            "telefono": (c.get("telefono") or "").strip(),
            "seg": cod(c.get("codigoSegmento")).upper(),
            "dia": dia_de_ruta(c),
            "codven": cod((c.get("rutasPreventa") or [{}])[0].get("codigoVendedor")) if c.get("rutasPreventa") else "",
        }

    # --- diagnostico: por que el universo puede no coincidir con el de antes ---
    con_ruta = sum(1 for c in clientes.values() if c["dia"] and c["codven"])
    por_codven = {}
    for c in clientes.values():
        if c["dia"] and c["codven"]:
            por_codven[c["codven"]] = por_codven.get(c["codven"], 0) + 1
    print("DIAG clientes: crudo=%d | con dia+codven=%d" % (len(clientes_raw), con_ruta))
    print("DIAG clientes por codven:", dict(sorted(por_codven.items(), key=lambda kv: -kv[1])))

    api = Api()

    # --- articulos: para saber que items son Pepsico y cuales son Pehuamar 90gr ---
    # Igual que georgalos.maestro_articulos(): la Pex (empresa 99) usa el
    # maestro de la empresa 1, y hay articulos cargados solo en una de las
    # dos. Se prueba la empresa propia y, si no esta, la otra.
    articulos = api.get("/data/cmd/inventario/api/v2/get-articulos")
    es_pepsico_por_clave = {}
    es_pehuamar90_por_clave = {}
    todas_claves = set()
    for a in articulos:
        clave = (cod(a.get("codigo")), cod(a.get("codigoEmpresa")))
        todas_claves.add(clave)
        desc = (a.get("descripcion") or "").upper().strip()
        if es_pepsico(desc):
            es_pepsico_por_clave[clave] = True
        if desc in PEHUAMAR_SKUS:
            es_pehuamar90_por_clave[clave] = True

    def clave_equivalente(codigo, empresa):
        e = "1" if empresa == "99" else empresa
        otra = "2" if e == "1" else "1"
        return (codigo, e) if (codigo, e) in todas_claves else (codigo, otra)

    def es_articulo_pepsico(codigo, empresa):
        return es_pepsico_por_clave.get(clave_equivalente(codigo, empresa), False)

    def es_articulo_pehuamar90(codigo, empresa):
        return es_pehuamar90_por_clave.get(clave_equivalente(codigo, empresa), False)

    print("Articulos Pepsico encontrados:", len(es_pepsico_por_clave),
          "| SKUs Pehuamar 90gr:", len(es_pehuamar90_por_clave))

    # --- ventas del mes en curso ---
    ventas = api.ventas(inicio_mes.isoformat(), (hoy + dt.timedelta(days=1)).isoformat())
    print("Ventas traidas (mes en curso):", len(ventas))

    compra_cliente = {}      # codigo cliente -> unidades Pepsico compradas (CCC, umbral 3)
    venta_rechazo = {}       # vendedor -> [{motivo/importe...}] (placeholder, ver aviso)
    venta_canje = {}
    pehuamar_compra = {}     # codigo cliente -> unidades Pehuamar 90gr compradas

    items_pepsico_vistos = 0
    for v in ventas:
        tipo = cod(v.get("codigoTipoVenta"))
        if cod(v.get("estado")).lower().startswith("anul"):
            continue
        emp = cod(v.get("codigoEmpresa"))
        cli = cod(v.get("codigoCliente"))
        for it in v.get("items") or []:
            codigo_it = cod(it.get("codigoItem"))
            q = num(it.get("cantidad")) * num(it.get("unidadFactor") or 1)
            if tipo == "VEN" and es_articulo_pepsico(codigo_it, emp):
                compra_cliente[cli] = compra_cliente.get(cli, 0) + q
                items_pepsico_vistos += 1
            if tipo == "VEN" and es_articulo_pehuamar90(codigo_it, emp):
                pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q
            if tipo == TIPO_RECHAZO:
                pass  # TODO: agrupar por vendedor/motivo una vez confirmado el campo de motivo
            if tipo == TIPO_CANJE:
                pass  # TODO: idem
    print("DIAG items de venta Pepsico contados:", items_pepsico_vistos,
          "| clientes con al menos 1 unidad:", len(compra_cliente))

    # --- no compradores: clientes con ruta asignada y < 3 unidades Pepsico ---
    hoy_key = DIAS_CAP[hoy.weekday()]
    no_compradores = []
    pehuamar_no_comprado = []
    for codigo, c in clientes.items():
        if not c["dia"] or not c["codven"]:
            continue
        if compra_cliente.get(codigo, 0) < 3:
            no_compradores.append({**{k: c[k] for k in
                                    ("codigo", "razon", "direccion", "localidad", "telefono", "seg", "dia")},
                                    "vendedor": c["codven"]})
        if c["dia"] == hoy_key and pehuamar_compra.get(codigo, 0) <= 0:
            pehuamar_no_comprado.append({**{k: c[k] for k in
                                          ("codigo", "razon", "direccion", "localidad", "telefono", "seg", "dia")},
                                          "vendedor": c["codven"]})

    escribir("no_compradores_detalle.json", no_compradores)
    escribir("pehuamar90_no_comprado.json", pehuamar_no_comprado)
    print("No compradores:", len(no_compradores), "| Sin Pehuamar 90gr hoy:", len(pehuamar_no_comprado))

    # invendible_detalle.json y rechazos_detalle.json: se dejan sin escribir
    # todavia. Hace falta confirmar contra un caso real (1) el nombre del
    # campo de motivo de devolucion y (2) que TIPO_RECHAZO/TIPO_CANJE sean
    # los codigos correctos, antes de generar estos dos con confianza.
    print("invendible_detalle.json y rechazos_detalle.json: NO generados todavia "
          "(falta confirmar campos de motivo/tipo contra la API real).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
