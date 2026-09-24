#!/usr/bin/env python3
"""
pepsico_combos.py — pepsico/{combos_detalle,combos_por_vendedor,combos_por_tipo}.json

*** REQUIERE UN EXPORT MANUAL, NO SE PUEDE AUTOMATIZAR POR API TODAVIA ***
Fuente: GesCom, Ventas / Reportes / Combos (no encontramos un endpoint de API
equivalente en tools/gescom.py ni en el sondeo que se pudo correr; puede
existir uno que no llegamos a probar por falta de acceso a un entorno con
credenciales — ver README.md de esta carpeta). Alguien tiene que entrar a
Gescom, generar el reporte de Combos y exportarlo a Excel con las hojas
"DetalleVendedor" y "Totales", y dejarlo en la ruta de abajo (o pasarla como
argumento) antes de correr este script.

Uso:
    python tools/pepsico_combos.py "C:\\ruta\\al\\export combos.xlsx"
"""
import collections
import json
import sys
from pathlib import Path

import openpyxl

DIR = Path(__file__).resolve().parent.parent / "pepsico"

VEND_NAMES = {
    "1": "Maria Noelia Madia", "2": "Julian Nuñez", "3": "Ezequiel Hack",
    "4": "Lucas Giayetto", "5": "Nicolas Martinez", "6": "Camila Martinez",
    "7": "Ramiro Rando", "8": "Martinica Rodriguez", "9": "Ludmila Aylen Martinez",
    "10": "Matias Parada", "11": "Melanie Larrama", "12": "Agustin Pogorzelski",
}


def escribir(nombre, data):
    ruta = DIR / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Guardado", ruta)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["DetalleVendedor"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    por_vend_combo = collections.defaultdict(list)
    tot_count = collections.Counter()
    tot_reconocer = collections.Counter()

    for r in rows:
        combos_vendidos, combo_codigo, cod_ven, nombre, descripcion, a_reconocer = r
        por_vend_combo[cod_ven].append({
            "combo": descripcion, "cantidad": combos_vendidos, "reconocer": round(a_reconocer, 2)
        })
        tot_count[cod_ven] += combos_vendidos
        tot_reconocer[cod_ven] += a_reconocer

    for cv in por_vend_combo:
        por_vend_combo[cv].sort(key=lambda x: -x["reconocer"])

    result_rows = []
    for cv in sorted(VEND_NAMES, key=lambda k: int(k)):
        result_rows.append({
            "codven": cv, "n": VEND_NAMES[cv],
            "combos": tot_count.get(cv, 0), "reconocer": round(tot_reconocer.get(cv, 0.0), 2),
        })

    tot_combos_general = sum(r["combos"] for r in result_rows)
    tot_reconocer_general = round(sum(r["reconocer"] for r in result_rows), 2)
    detalle_por_vend = {VEND_NAMES[cv]: por_vend_combo[cv] for cv in por_vend_combo}

    escribir("combos_por_vendedor.json",
             {"rows": result_rows, "totCombos": tot_combos_general, "totReconocer": tot_reconocer_general})
    escribir("combos_detalle.json", detalle_por_vend)

    ws2 = wb["Totales"]
    tipo_rows = []
    for r in ws2.iter_rows(min_row=2, values_only=True):
        cant, codigo, desc, reconocer = r
        tipo_rows.append({"combo": desc, "cantidad": cant, "reconocer": round(reconocer, 2)})
    tipo_rows.sort(key=lambda x: -x["reconocer"])
    escribir("combos_por_tipo.json", tipo_rows)

    print("Total combos:", tot_combos_general, "| Total $ a reconocer:", tot_reconocer_general)
    return 0


if __name__ == "__main__":
    sys.exit(main())
