"""Flujo de Caja TP-RL -> flujo/data.enc.json

Junta, en esta PC de la oficina (necesita la unidad W: y la carpeta Descargas):
  - W:\\FLUJO DE CAJA\\*2026*.xls          pagos, cheques, vencidos y recaudacion estimada
  - W:\\Tesoreria\\Finanzas_Diarias_*.xlsx  ingresos de la caja de tesoreria
  - Descargas\\Extracto_CC*, Ultimos*movimientos*   creditos de Galicia, Nacion y Provincia

El repo es publico: el JSON sale cifrado con AES-256-GCM, clave derivada con
PBKDF2-SHA256 (mismo esquema que ctacte). El panel la pide y descifra en el navegador.

La clave se toma de FLUJO_CLAVE o, si no esta, de tools/flujo.secret (no se sube: *.secret
esta en .gitignore).

Uso:  python tools/flujo.py     y despues commit + push de flujo/data.enc.json y flujo/fecha.txt
Requiere: pip install xlrd openpyxl cryptography
"""
import base64, json, os, re, subprocess, sys, tempfile, datetime as dt
from pathlib import Path

AQUI = Path(__file__).resolve().parent
OUT = AQUI.parent / "flujo"
ITER = 200_000


def cifrar(obj, clave):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    sal, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal,
                     iterations=ITER).derive(clave.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "iter": ITER, "salt": b64(sal), "iv": b64(iv), "ct": b64(ct)}


def main():
    clave = os.environ.get("FLUJO_CLAVE", "").strip()
    if not clave and (AQUI / "flujo.secret").exists():
        clave = (AQUI / "flujo.secret").read_text(encoding="utf-8").strip()
    if not clave:
        sys.exit("Falta la clave: FLUJO_CLAVE o tools/flujo.secret.")

    tmp = Path(tempfile.gettempdir())
    subprocess.run([sys.executable, str(AQUI / "flujo_planillas.py"), str(tmp / "flujo_data.json")], check=True)
    subprocess.run([sys.executable, str(AQUI / "flujo_bancos.py"), str(tmp / "flujo_bancos.json")], check=True)
    d = json.loads((tmp / "flujo_data.json").read_text(encoding="utf-8"))
    b = json.loads((tmp / "flujo_bancos.json").read_text(encoding="utf-8"))

    bancos = {}
    for f, gs in b["bancos"].items():
        agg = {}
        for cu, con, imp, n in gs:
            if re.match(r"(?i)^(ING )?TRANSF ", con):
                con = "Transferencia Recibida"
            a = agg.setdefault((cu, con), [0, 0])
            a[0] += imp
            a[1] += n
        bancos[f] = [[cu, con, round(i, 2), n] for (cu, con), (i, n) in agg.items()]
    d["bancos"] = bancos
    d["archivosBanco"] = b["archivos"]
    ahora = dt.datetime.now()
    d["generado"] = ahora.isoformat(timespec="seconds")

    OUT.mkdir(exist_ok=True)
    (OUT / "data.enc.json").write_text(json.dumps(cifrar(d, clave)), encoding="utf-8")
    (OUT / "fecha.txt").write_text("Ultima actualizacion: %s\n" % ahora.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
    print("listo:", OUT / "data.enc.json")


if __name__ == "__main__":
    main()
