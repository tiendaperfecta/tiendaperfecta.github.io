#!/usr/bin/env python3
"""
pepsico_ventas.py — pepsico/{no_compradores_detalle,invendible_detalle,
rechazos_detalle,pehuamar90_no_comprado}.json, por API de GesCom.

*** BORRADOR SIN PROBAR ***
No se pudo correr contra la API real todavia: este token de GitHub no tiene
el scope "workflow" (hace falta para crear o tocar archivos en
.github/workflows/, que es donde se inyectan los secrets de GesCom en CI), asi
que no hubo forma de validar esto en un entorno con credenciales sin pedirle a
alguien que lo autorice a mano. Todo lo de aca abajo esta armado por analogia
con tools/georgalos.py (que si esta probado y anda en produccion), pero varios
campos son supuestos de buena fe, no confirmados:

  - fechaCarga: georgalos.py NO usa este campo (usa fechaComprobante o, si no
    hay, fechaPedido). Todo este proyecto encontro que Gescom arrastra ventas
    con comprobante pendiente que solo aparecen por FechaCarga, asi que puede
    hacer falta ese campo aca tambien. Si el registro de venta no lo trae hay
    que revisar con GesCom que campo corresponde.
  - Marca por articulo: se identifica buscando estas palabras en la
    descripcion del articulo (mismo patron ya verificado contra los CSV de
    venta reales en tools locales de este proyecto, ver _cobertura_marca_v3_csv
    en el historial del tablero): 3Ds, Cheetos, Doritos, Lays, Pehuamar, Pep,
    Quaker, Tostitos, Twistos. No se confirmo si el articulo de GesCom trae un
    campo "marca" propio (como en inventario/api/v2/get-articulos de otros
    proveedores) que sea mas confiable que este texto.
  - codigoTipoVenta "DEV-RE" = Devolucion por Rechazo, "DEV-CA" = Devolucion
    por Canje: se dedujo por analogia con el diccionario SIGNO de
    georgalos.py (que si esta verificado), pero no se confirmo el texto
    exacto que corresponde a cada codigo para GesCom TP/Pex.
  - Pehuamar 90gr Lisa/Acanalada: se buscan estos dos articulos por
    descripcion ("PEHUA PAPA LISA 90GX22 RM", "PEHUA PAPA ACANA 90GX22 RM"),
    tal como aparecen en el export manual de Gescom que se uso hasta ahora.
    Puede que la API use una descripcion levemente distinta.

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

    api = Api()

    # --- articulos: para saber que items son Pepsico y cuales son Pehuamar 90gr ---
    articulos = api.get("/data/cmd/inventario/api/v2/get-articulos")
    marca_pepsico = {}   # (codigo, empresa) -> True si es de una marca Pepsico
    pehuamar90 = set()   # (codigo, empresa) de las 2 SKUs de Pehuamar 90gr
    for a in articulos:
        clave = (cod(a.get("codigo")), cod(a.get("codigoEmpresa")))
        desc = (a.get("descripcion") or "").upper().strip()
        if es_pepsico(desc):
            marca_pepsico[clave] = True
        if desc in PEHUAMAR_SKUS:
            pehuamar90.add(clave)
    print("Articulos Pepsico encontrados:", len(marca_pepsico), "| SKUs Pehuamar 90gr:", len(pehuamar90))

    # --- ventas del mes en curso ---
    ventas = api.ventas(inicio_mes.isoformat(), (hoy + dt.timedelta(days=1)).isoformat())
    print("Ventas traidas (mes en curso):", len(ventas))

    compra_cliente = {}      # codigo cliente -> unidades Pepsico compradas (CCC, umbral 3)
    venta_rechazo = {}       # vendedor -> [{motivo/importe...}] (placeholder, ver aviso)
    venta_canje = {}
    pehuamar_compra = {}     # codigo cliente -> unidades Pehuamar 90gr compradas

    for v in ventas:
        tipo = cod(v.get("codigoTipoVenta"))
        if cod(v.get("estado")).lower().startswith("anul"):
            continue
        emp = cod(v.get("codigoEmpresa"))
        cli = cod(v.get("codigoCliente"))
        for it in v.get("items") or []:
            clave = (cod(it.get("codigoItem")), emp)
            q = num(it.get("cantidad")) * num(it.get("unidadFactor") or 1)
            if clave in marca_pepsico and tipo == "VEN":
                compra_cliente[cli] = compra_cliente.get(cli, 0) + q
            if clave in pehuamar90 and tipo == "VEN":
                pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q
            if tipo == TIPO_RECHAZO:
                pass  # TODO: agrupar por vendedor/motivo una vez confirmado el campo de motivo
            if tipo == TIPO_CANJE:
                pass  # TODO: idem

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
