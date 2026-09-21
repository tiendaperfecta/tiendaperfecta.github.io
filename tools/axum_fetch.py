#!/usr/bin/env python3
"""
axum_fetch.py — Baja datos de Axum (Orders360 + GPS) y los deja como JSON
en axum/data/ para que el panel estático los consuma.

Pensado para correr en una GitHub Action programada. Las credenciales se leen
de variables de entorno (GitHub Secrets); NUNCA van en el repo:

    AXUM_ORDERS_USER   usuario de www.axum.com.ar/tiendaperfecta (ej. admin)
    AXUM_ORDERS_PASS   contraseña de ese usuario
    GPS_USER           usuario de gps.axumvm.com.ar (ej. TIENDAPERFECTAgps)
    GPS_PASS           contraseña de ese usuario

Uso local:  python tools/axum_fetch.py
Salida:     axum/data/summary.json, positions.json, visits.json, cross.json, meta.json

Requiere: pip install requests
"""
import os
import re
import sys
import json
import datetime as dt
from pathlib import Path
from collections import defaultdict

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "axum" / "data"

# La Action corre en UTC. Sin esto, las corridas de 21:00 y 22:00 hora argentina
# (00:00 y 01:00 UTC) pedirian los datos del dia siguiente y pisarian las ventas
# del dia con un resumen vacio. Argentina no usa horario de verano desde 2009.
ART = dt.timezone(dt.timedelta(hours=-3))

ORDERS_LOGIN = "https://www.axum.com.ar/tiendaperfecta/login.aspx"
ORDERS_API = "https://masuno-order360.axumweb.com"
GPS_BASE = "https://gps.axumvm.com.ar"


# --------------------------------------------------------------------------- #
# Utilidades WebForms (ASP.NET)
# --------------------------------------------------------------------------- #
def _hidden(html, name):
    m = re.search(r'id="%s"\s+value="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# Orders360
# --------------------------------------------------------------------------- #
class Orders360:
    """Se loguea en el WebForms de tiendaperfecta y extrae el JWT del redirect,
    luego usa la API REST masuno-order360."""

    def __init__(self, user, pwd):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0"})
        self.token = self._get_token(user, pwd)

    def _get_token(self, user, pwd):
        r = self.s.get(ORDERS_LOGIN, timeout=30)
        r.raise_for_status()
        html = r.text
        data = {
            "__VIEWSTATE": _hidden(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(html, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hidden(html, "__EVENTVALIDATION"),
            "UserName": user,
            "Password": pwd,
            "Button1": "Ingresar",
        }
        r = self.s.post(ORDERS_LOGIN, data=data, timeout=30, allow_redirects=True)
        r.raise_for_status()
        # El token aparece en la URL final o en un window.location del cuerpo.
        m = re.search(r"token=([A-Za-z0-9_\-\.]+)", r.url) or \
            re.search(r"token=([A-Za-z0-9_\-\.]+)", r.text)
        if not m:
            raise RuntimeError("No se pudo extraer el token de Orders360 tras el login.")
        return m.group(1)

    def orders(self, date_since, date_until, take=1000):
        r = self.s.post(
            f"{ORDERS_API}/api/Orders",
            params={"entity": "orders", "dateSince": date_since, "dateUntil": date_until},
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps({"filter": None, "skip": 0, "take": take, "sort": None}),
            timeout=90,
        )
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- #
# GPS (LocationService.asmx)
# --------------------------------------------------------------------------- #
class Gps:
    def __init__(self, user, pwd):
        self.user = user
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0"})
        self._login(user, pwd)

    def _login(self, user, pwd):
        r = self.s.get(f"{GPS_BASE}/Login.aspx", timeout=30)
        r.raise_for_status()
        html = r.text
        data = {
            "__VIEWSTATE": _hidden(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(html, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hidden(html, "__EVENTVALIDATION"),
            "UserName": user,
            "Password": pwd,
            "Button1": "Ingresar",
        }
        r = self.s.post(f"{GPS_BASE}/Login.aspx", data=data, timeout=30)
        r.raise_for_status()

    def call(self, method, **params):
        params.setdefault("userName", self.user)
        r = self.s.post(f"{GPS_BASE}/LocationService.asmx/{method}",
                        data=json.dumps(params),
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                        timeout=60)
        r.raise_for_status()
        d = r.json()
        return d["d"] if isinstance(d, dict) and "d" in d else d

    def last_positions(self):
        out = []
        for s in self.call("lastPositions"):
            p = (str(s).split(",") + [""] * 7)[:7]
            try:
                lat, lng = float(p[0]), float(p[1])
            except ValueError:
                continue
            out.append({"idVendedor": p[4], "lat": lat, "lng": lng,
                        "estado": p[2], "hora": p[3]})
        return out

    def visitados_hoy(self, fecha):
        try:
            return self.call("soloClientesVisitados", _dia=fecha)
        except Exception:
            return []

    def km_hoy(self, fecha):
        try:
            return self.call("dailySellerTravelledKmReport", fecha=fecha) or []
        except Exception:
            return []


# --------------------------------------------------------------------------- #
# Construcción de los JSON del panel
# --------------------------------------------------------------------------- #
def env(*names):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def write_json(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    ahora = dt.datetime.now(ART)
    today = ahora.date().isoformat()
    now = ahora.isoformat(timespec="seconds")
    meta = {"generatedAt": now, "date": today, "errors": []}
    orders_ok = gps_ok = False

    # Si no hay credenciales de ningún sistema, no tocamos nada (deja los datos de
    # ejemplo / última corrida buena intactos). Útil antes de cargar los secrets.
    if not (env("AXUM_ORDERS_USER") or env("GPS_USER")):
        print("::warning title=Axum::Faltan los secrets "
              "(AXUM_ORDERS_USER/PASS, GPS_USER/PASS). El panel sigue mostrando "
              "los datos de ejemplo.")
        print("Sin credenciales configuradas; no se modifican los datos.", file=sys.stderr)
        return

    # ---- Orders360 ----
    summary = {"date": today, "totalNet": 0, "totalGross": 0, "orders": 0,
               "bySeller": [], "byChannel": [], "topClients": []}
    orders = []
    try:
        o = Orders360(env("AXUM_ORDERS_USER"), env("AXUM_ORDERS_PASS"))
        orders = o.orders(today, today)
        summary["orders"] = len(orders)
        summary["totalGross"] = round(sum((x.get("total") or 0) for x in orders), 2)
        summary["totalNet"] = round(sum((x.get("totalNetPrice") or 0) for x in orders), 2)

        by_seller = defaultdict(lambda: {"orders": 0, "total": 0.0, "name": ""})
        by_channel = defaultdict(lambda: {"orders": 0, "total": 0.0})
        by_client = defaultdict(lambda: {"orders": 0, "total": 0.0, "name": ""})
        for x in orders:
            sid = str(x.get("sellerId") or "")
            by_seller[sid]["orders"] += 1
            by_seller[sid]["total"] += (x.get("total") or 0)
            by_seller[sid]["name"] = x.get("sellerName") or ""
            ch = x.get("clientChannel") or "s/canal"
            by_channel[ch]["orders"] += 1
            by_channel[ch]["total"] += (x.get("total") or 0)
            cid = str(x.get("clientId") or "")
            by_client[cid]["orders"] += 1
            by_client[cid]["total"] += (x.get("total") or 0)
            by_client[cid]["name"] = x.get("clientName") or ""

        summary["bySeller"] = sorted(
            [{"sellerId": k, "name": v["name"], "orders": v["orders"], "total": round(v["total"], 2)}
             for k, v in by_seller.items()], key=lambda r: -r["total"])
        summary["byChannel"] = sorted(
            [{"channel": k, "orders": v["orders"], "total": round(v["total"], 2)}
             for k, v in by_channel.items()], key=lambda r: -r["total"])
        summary["topClients"] = sorted(
            [{"clientId": k, "name": v["name"], "orders": v["orders"], "total": round(v["total"], 2)}
             for k, v in by_client.items()], key=lambda r: -r["total"])[:20]
        orders_ok = True
    except Exception as e:
        meta["errors"].append(f"orders360: {e}")

    # ---- GPS ----
    positions = {"date": today, "sellers": []}
    visits = {"date": today, "bySeller": [], "detail": []}
    try:
        g = Gps(env("GPS_USER"), env("GPS_PASS"))
        positions["sellers"] = g.last_positions()

        visitados = g.visitados_hoy(today)   # [{sellerID, visitedClient}]
        km = g.km_hoy(today)
        visits["detail"] = visitados
        by = defaultdict(lambda: {"visited": 0, "km": None})
        for v in visitados:
            sid = str(v.get("sellerID") or v.get("Item1") or "")
            by[sid]["visited"] += 1
        # km: estructura variable; se intenta mapear por id si viene como dict
        if isinstance(km, list):
            for row in km:
                if isinstance(row, dict):
                    sid = str(row.get("sellerID") or row.get("Item1") or "")
                    val = row.get("km") or row.get("Item2")
                    if sid:
                        by[sid]["km"] = val
        visits["bySeller"] = [{"sellerId": k, **v} for k, v in by.items()]
        gps_ok = True
    except Exception as e:
        meta["errors"].append(f"gps: {e}")

    # Solo escribimos lo que se obtuvo bien; si una fuente falló, se deja el JSON previo.
    if orders_ok:
        write_json("summary.json", summary)
    if gps_ok:
        write_json("positions.json", positions)
        write_json("visits.json", visits)

    # ---- Cruce ventas + GPS (join por id de vendedor) ----
    # Se recalcula solo si AMBAS fuentes vinieron bien; si no, deja el cruce anterior.
    if orders_ok and gps_ok:
        cross = {"date": today, "rows": [],
                 "note": "Join por id de vendedor; puede requerir mapeo si los ids de "
                         "Orders360 y GPS no coinciden."}
        sales_by = {r["sellerId"]: r for r in summary["bySeller"]}
        gps_by = {r["sellerId"]: r for r in visits["bySeller"]}
        for sid in sorted(set(sales_by) | set(gps_by)):
            s = sales_by.get(sid, {})
            gp = gps_by.get(sid, {})
            cross["rows"].append({
                "sellerId": sid, "name": s.get("name", ""),
                "orders": s.get("orders", 0), "total": s.get("total", 0),
                "visited": gp.get("visited", 0), "km": gp.get("km"),
            })
        write_json("cross.json", cross)

    meta["ordersOk"] = orders_ok
    meta["gpsOk"] = gps_ok
    write_json("meta.json", meta)
    print("OK", meta)
    if meta["errors"]:
        # No hacemos fallar la Action por un sistema caído; queda registrado en meta.json
        print("Con errores:", meta["errors"], file=sys.stderr)


if __name__ == "__main__":
    build()
