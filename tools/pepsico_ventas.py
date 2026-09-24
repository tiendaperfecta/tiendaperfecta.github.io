#!/usr/bin/env python3
"""
pepsico_ventas.py - datos del panel Avance Pepsico via API de GesCom.
Genera no_compradores_detalle.json, pehuamar90_no_comprado.json,
invendible_detalle.json y rechazos_detalle.json.

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

MARCAS_PEPSICO = ["3DS", "CHEETOS", "DORITOS", "LAYS", "PEHUAMAR", "PEP", "QUAKER", "TOSTITOS", "TWISTOS"]
PEHUAMAR_SKUS = {"PEHUA PAPA LISA 90GX22 RM", "PEHUA PAPA ACANA 90GX22 RM"}
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

    vendedores_raw = api.get("/data/cmd/ventas/api/v1/get-vendedores")
    nombre_por_codven = {cod(x.get("codigo")): (x.get("nombre") or "").strip() for x in vendedores_raw}

    articulos = api.get("/data/cmd/inventario/api/v2/get-articulos")
    es_pepsico_por_clave = {}
    es_pehuamar90_por_clave = {}
    descripcion_por_clave = {}
    todas_claves = set()
    for a in articulos:
        clave = (cod(a.get("codigo")), cod(a.get("codigoEmpresa")))
        todas_claves.add(clave)
        desc = (a.get("descripcion") or "").strip()
        descripcion_por_clave[clave] = desc
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

    print("Articulos Pepsico encontrados:", len(es_pepsico_por_clave),
          "| SKUs Pehuamar 90gr:", len(es_pehuamar90_por_clave))

    ventas = api.ventas(inicio_mes.isoformat(), (hoy + dt.timedelta(days=1)).isoformat())
    print("Ventas traidas (mes en curso):", len(ventas))

    compra_cliente = {}
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

        for it in v.get("items") or []:
            codigo_it = cod(it.get("codigoItem"))
            q = num(it.get("cantidad")) * num(it.get("unidadFactor") or 1)
            importe = num(it.get("importeNeto"))

            if tipo == "VEN" and es_articulo_pepsico(codigo_it, emp):
                compra_cliente[cli] = compra_cliente.get(cli, 0) + q
                items_pepsico_vistos += 1
            if tipo == "VEN" and es_articulo_pehuamar90(codigo_it, emp):
                pehuamar_compra[cli] = pehuamar_compra.get(cli, 0) + q

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

    escribir("no_compradores_detalle.json", no_compradores)
    escribir("pehuamar90_no_comprado.json", pehuamar_no_comprado)
    print("No compradores:", len(no_compradores), "| Sin Pehuamar 90gr hoy:", len(pehuamar_no_comprado))

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
