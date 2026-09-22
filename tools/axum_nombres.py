#!/usr/bin/env python3
"""
axum_nombres.py — Nombres de los vendedores.

Orders360 manda `sellerName` vacio, asi que el panel mostraba "Vendedor 8". El
maestro de vendedores sale de GesCom (tablero de Mar del Plata) y se guarda acá
como tabla estatica: son 27 filas que cambian muy de vez en cuando.
"""
import json
from pathlib import Path

try:
    VENDEDORES = json.loads(
        (Path(__file__).with_name("vendedores.json")).read_text(encoding="utf-8"))
except Exception:
    VENDEDORES = {}


def de(sid):
    """Nombre del vendedor, o 'Vendedor <id>' si no esta en la tabla."""
    sid = str(sid or "")
    return VENDEDORES.get(sid) or ("Vendedor " + sid if sid else "Sin vendedor")
