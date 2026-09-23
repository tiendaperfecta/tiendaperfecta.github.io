#!/usr/bin/env python3
"""
axum_alerta.py — Un solo mail por dia con todas las llegadas tarde.

No manda uno por vendedor: junta a todos los que llegaron despues de la hora y
arma un unico correo. Lleva registro de los dias ya enviados en
axum/data/alertas.json, asi el refresco horario no repite el mismo aviso.

Se espera hasta `MAIL_HORA` (10:30 por defecto) antes de mandar: a las 9:05
todavia hay gente entrando y el mail saldria incompleto.

Configuracion por variables de entorno (GitHub Secrets):

    MAIL_USER     casilla desde la que sale (ej. avisos@tiendaperfecta.com)
    MAIL_PASS     su contrasena de aplicacion
    MAIL_PARA     destinatarios separados por coma
    MAIL_SMTP     servidor, por defecto smtp.gmail.com
    MAIL_PUERTO   por defecto 587
    MAIL_HORA     hora de envio, por defecto 10:30

Sin MAIL_USER/MAIL_PASS no manda nada: deja el aviso armado en
axum/data/alerta.json y sigue de largo.
"""
import os
import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

PANEL = "https://tiendaperfecta.github.io/axum/"


def _env(nombre, defecto=""):
    return os.environ.get(nombre, "").strip() or defecto


def _cuerpo(fecha, tarde, sin_señal, umbral):
    """(asunto, html, texto) del aviso."""
    dia = dt.date.fromisoformat(fecha).strftime("%d/%m/%Y")
    asunto = "Llegadas tarde %s — %d vendedor%s" % (
        dia, len(tarde), "es" if len(tarde) != 1 else "")

    filas = "".join(
        "<tr>"
        "<td style='padding:7px 14px 7px 0'>%s</td>"
        "<td style='padding:7px 14px 7px 0;font-weight:700;color:#b45309'>%s</td>"
        "<td style='padding:7px 0;color:#64748b'>%s</td>"
        "</tr>" % (s["vendedor"], s["inicioJornada"] or "—",
                   ("%d visitas" % s["visitas"]) if s["visitas"] else "sin visitas")
        for s in tarde)

    extra = ""
    if sin_señal:
        extra = ("<p style='margin:22px 0 6px;font-size:14px;color:#0f172a'>"
                 "<b>Sin actividad registrada</b></p>"
                 "<p style='margin:0;font-size:14px;color:#334155'>%s</p>"
                 % " · ".join(s["vendedor"] for s in sin_señal))

    html = """<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;
  max-width:560px;color:#0f172a">
  <p style="margin:0 0 4px;font-size:18px;font-weight:700">Llegadas tarde · %s</p>
  <p style="margin:0 0 18px;font-size:13px;color:#64748b">
    Vendedores cuya primera señal de actividad fue después de las %s.</p>
  <table style="border-collapse:collapse;font-size:14px">%s</table>
  %s
  <p style="margin:26px 0 0;font-size:13px">
    <a href="%s" style="color:#0f766e">Ver el panel</a></p>
  <p style="margin:14px 0 0;font-size:11px;color:#94a3b8">
    Aviso automático del Panel Axum. Se manda una sola vez por día.</p>
</div>""" % (dia, umbral, filas, extra, PANEL)

    texto = "Llegadas tarde %s (despues de las %s)\n\n" % (dia, umbral)
    texto += "\n".join("  %-26s %s" % (s["vendedor"], s["inicioJornada"] or "-")
                       for s in tarde)
    if sin_señal:
        texto += "\n\nSin actividad: " + ", ".join(s["vendedor"] for s in sin_señal)
    texto += "\n\n" + PANEL
    return asunto, html, texto


def revisar(dia, fecha, leer, escribir, meta, ahora=None):
    """Arma el aviso del dia y lo manda si corresponde."""
    ahora = ahora or dt.datetime.now()
    umbral = (dia.get("umbrales") or {}).get("llegada", "09:00")
    stats = dia.get("bySeller") or []
    tarde = sorted((s for s in stats if s.get("llegoTarde")),
                   key=lambda s: s.get("inicioJornada") or "")
    # Con ruta asignada pero ni una senal en todo el dia.
    con_ruta = {c["sellerId"] for c in (dia.get("cobertura") or []) if c.get("ruta")}
    sin_señal = [s for s in stats
                 if s["sellerId"] in con_ruta and not s.get("inicioJornada")]

    asunto, html, texto = _cuerpo(fecha, tarde, sin_señal, umbral) if tarde else \
        ("", "", "")
    escribir("alerta.json", {"date": fecha, "umbral": umbral,
                             "tarde": [{"vendedor": s["vendedor"],
                                        "hora": s["inicioJornada"],
                                        "visitas": s["visitas"]} for s in tarde],
                             "sinSenal": [s["vendedor"] for s in sin_señal],
                             "asunto": asunto})
    if not tarde:
        return

    enviados = (leer("alertas.json") or {}).get("dias", [])
    if fecha in enviados:
        return
    # Antes de la hora de corte el listado saldria incompleto.
    if ahora.strftime("%H:%M") < _env("MAIL_HORA", "10:30"):
        return

    usuario, clave = _env("MAIL_USER"), _env("MAIL_PASS")
    if not (usuario and clave):
        meta["alerta"] = "hay %d llegadas tarde, sin casilla configurada" % len(tarde)
        return

    para = [d.strip() for d in _env("MAIL_PARA", usuario).split(",") if d.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = formataddr(("Panel Axum", usuario))
    msg["To"] = ", ".join(para)
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(_env("MAIL_SMTP", "smtp.gmail.com"),
                          int(_env("MAIL_PUERTO", "587")), timeout=60) as smtp:
            smtp.starttls()
            smtp.login(usuario, clave)
            smtp.sendmail(usuario, para, msg.as_string())
    except Exception as e:
        meta.setdefault("errors", []).append("alerta: %s: %s" % (type(e).__name__, e))
        return

    escribir("alertas.json", {"dias": sorted(set(enviados) | {fecha})[-120:]})
    meta["alerta"] = "enviada a %d destinatario(s): %d llegadas tarde" % (
        len(para), len(tarde))
