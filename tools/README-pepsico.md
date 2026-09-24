# Generador de datos — Panel Avance PepsiCo

Scripts que arman los 9 archivos que lee `pepsico/index.html`. Estado real a
fecha de esta rama (no confundir con "listo para producción"):

| Archivo                          | Script                    | Fuente                                   | ¿Automatizado? |
|-----------------------------------|---------------------------|-------------------------------------------|-----------------|
| `frescura_data.json`              | `pepsico_frescura.py`     | `frescura.tienda-perfecta.workers.dev`    | ✅ Sí, probado y funcionando |
| `no_compradores_detalle.json`     | `pepsico_ventas.py`       | API GesCom (`ventas/api/v2/get`)          | ⚠️ Borrador sin probar contra la API real |
| `pehuamar90_no_comprado.json`     | `pepsico_ventas.py`       | API GesCom (idem)                         | ⚠️ Borrador sin probar |
| `invendible_detalle.json`         | *(no generado todavía)*   | API GesCom (idem, falta confirmar campo de motivo) | ⚠️ No generado — falta un dato de la API que no se pudo confirmar |
| `rechazos_detalle.json`           | *(no generado todavía)*   | API GesCom (idem)                         | ⚠️ No generado — mismo motivo |
| `combos_detalle.json` + 2 más     | `pepsico_combos.py`       | GesCom, export manual (Ventas/Reportes/Combos) | ❌ No — no hay endpoint de API conocido |
| `oportunidad_portafolio.json`     | `pepsico_portafolio.py`   | GesCom, export manual (6.1 Censo Tienda Perfecta) | ❌ No — reporte a medida, sin endpoint conocido |
| `mn_seguimiento.json` + 1 más     | `pepsico_mn.py`           | Power BI "Mi Negocio +" de Pepsico (**no es GesCom**) | ❌ No — otro sistema, sin credenciales en los secrets |
| `logo_mi_negocio.png`             | *(ninguno)*                | Imagen fija                               | N/A — activo estático |

## Por qué no está más avanzado esto

1. **El token de GitHub de esta sesión no tiene el scope `workflow`.** Sin eso
   no se puede crear ni tocar nada en `.github/workflows/`, que es donde se
   inyectan `GESCOM_REALM/CLIENT_ID/USERNAME/PASSWORD` en CI. Es la única
   forma segura de correr algo contra la API real sin manejar las
   credenciales a mano. El archivo `tools/pepsico-data.workflow.yml.txt` en
   esta carpeta tiene el workflow listo — falta moverlo a
   `.github/workflows/pepsico-data.yml` (con `gh auth refresh -h github.com
   -s workflow` o subiéndolo por la web de GitHub) y correrlo una vez a mano
   (`workflow_dispatch`) para poder debuggear `pepsico_ventas.py` contra
   datos reales.
2. **`pepsico_ventas.py` es un borrador de buena fe**, armado por analogía
   con `tools/georgalos.py` (que sí está probado y en producción), pero varios
   campos no se confirmaron contra la API real: si el registro de venta trae
   `fechaCarga`, si "DEV-RE"/"DEV-CA" son los códigos correctos de Rechazo y
   Canje, y cómo viene el motivo de la devolución. Por eso no genera todavía
   `invendible_detalle.json` ni `rechazos_detalle.json` — preferí dejarlos
   sin hacer antes que inventar un mapeo sin confirmar.
3. **Combos y Portafolio (Tienda Perfecta)** son reportes armados a medida
   para Pepsico dentro de GesCom (menú "Rumbo Pepsico"), no encontramos
   endpoint de API para ninguno de los dos, y no se pudo probar más porque
   justo eso requiere el mismo acceso bloqueado en el punto 1.
4. **Mi Negocio+ no es GesCom.** Es un reporte de Pepsico en Power BI. No hay
   credenciales de eso en los secrets del repo (solo las de GesCom), así que
   no hay forma de automatizarlo con lo que existe hoy — haría falta que
   Pepsico habilite un service principal de la API de Power BI para ese
   reporte.

## Requisitos

```
pip install requests openpyxl
```

(`requests` ya lo instalan los workflows existentes; `openpyxl` hace falta
si corrés a mano `pepsico_combos.py`, `pepsico_portafolio.py` o `pepsico_mn.py`.)

## Créditos / seguridad

Ningún script de esta carpeta tiene credenciales adentro. Los 4 secrets de
GesCom se leen únicamente por `os.environ` en `gescom.py` (reutilizado tal
cual, sin tocar). Los 3 scripts de export manual no se conectan a nada: solo
procesan un archivo que alguien ya descargó a mano.

**Aparte, sin relación con esto:** el repo `tiendaperfecta.github.io` es
público, y varios de los JSON de `pepsico/` ya publicados en `main` traen
nombre, dirección y teléfono de clientes. Eso quedó expuesto a internet, no
solo a la organización. Sigue sin resolverse — no lo toqué en esta rama
porque es una decisión sobre `main`, no sobre el generador.
