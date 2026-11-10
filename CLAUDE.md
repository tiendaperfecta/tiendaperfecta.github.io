# Central web — tiendaperfecta.github.io

Los paneles públicos de Tienda Perfecta, servidos por GitHub Pages desde este
repo. Cada panel es una carpeta con su propio `index.html`.

| Carpeta | Panel | Datos |
|---|---|---|
| `pepsico/` | PepsiCo | `.github/workflows/pepsico-data.yml` (11 y 15 hs) + `override_*.json` a mano |
| `ctacte/` | Cuenta corriente | `ctacte-data.yml` (8, 10, 12, 14, 16 hs) |
| `axum/` | Axum | `axum-data.yml` (cada hora, 8–20) |
| `georgalos/` | Georgalos | `georgalos-data.yml` (cada hora) |
| `pedidos/` | Pedidos | `pedidos-data.yml` |
| `flujo/` | Flujo de caja | `flujo-vivo.yml` + tarea de Windows 09:00 |
| `disponibilidad/` | Disponibilidad | en vivo, no guarda datos |
| `auditoria-2026/` | Auditoría | — |

El panel de **Fleteros no está acá**: vive en el repo `tiendaperfecta/fleteros`
(carpeta `Documents\Panel-Fleteros`).

## Cómo está armado cada panel

Casi todos son un `index.html` autocontenido, con el CSS y el JS adentro. Las
excepciones son `axum/`, `flujo/` y `disponibilidad/`, que sí referencian
archivos aparte.

De los que tienen archivos externos, **`axum/` y `pepsico/` versionan con
`?v=`**: si se les toca un asset, hay que subir ese número o el cambio no se
ve. Los autocontenidos no lo necesitan, porque el cambio viaja en el propio
`index.html`.

Los `*.json` de datos los escriben los workflows: **no se editan a mano**. La
excepción son los `override_*.json` de PepsiCo, que existen justamente para
corregir a mano.

## Probarlo local

Ya está la configuración lista: levantar el servidor `central` (puerto 8766) y
abrir `http://localhost:8766/<panel>/`.

Para medir en pantalla chica, ver las reglas generales en `~/.claude/CLAUDE.md`.

## Al publicar

Pages tarda uno o dos minutos en reconstruir después del push y el HTML se
sirve con cache. Si el cambio todavía no aparece, mirar el build antes de
tocar nada:

    gh api repos/tiendaperfecta/tiendaperfecta.github.io/pages/builds --jq '.[0] | "\(.status) \(.commit[0:7])"'
