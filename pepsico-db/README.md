# pepsico-db

Depósito de los números que se cargan **a mano** en el panel Avance PepsiCo:
Compromiso por segmento y Proyección de IPs por vendedor.

## Por qué existe

El panel venía usando `claude.use("db")`, la API de runtime de los Artifacts de
Claude. Esa API sólo existe cuando la página corre publicada como Artifact;
servida desde GitHub Pages no existe, así que esos campos quedaban
deshabilitados con "no disp." y los totales en `n/d`. Este worker los reemplaza
con el mismo patrón que ya usan `ventas`, `transferencias` y `arca-vs-gescom`:
un worker con KV.

Del lado del panel, el reemplazo es un shim de ~140 líneas dentro de
`pepsico/index.html` que expone la misma superficie que usaba el código
(`doc().onSnapshot()`, `doc().set()`, `collection().onSnapshot()`), así no hubo
que tocar nada más: sólo cambiaron las dos llamadas a `claude.use("db")`.

## API

| | |
|---|---|
| `GET /d/:col` | la colección entera, `{ "<id>": {...} }` |
| `PUT /d/:col/:id` | guarda el documento |
| `GET /health` | `{ok:true}` |

Colecciones aceptadas: `compromiso` e `ipProyeccion`. Cualquier otra da 404, así
un typo no crea una colección nueva.

- `compromiso/<vendedor>` → `{pg, sb, updatedAt}`
- `compromiso/_supervisor` → `{value, updatedAt}`
- `ipProyeccion/<vendedor>` → `{frecuencia, portafolio, pehuamar, sellout, updatedAt}`

Se guardan **sólo números y null**: cualquier otro campo del payload se
descarta, para que nada pueda inflar la metadata por encima del límite de KV.

## Consistencia

KV es de consistencia eventual: una escritura tarda hasta ~40 segundos en
aparecer en el `list()` (medido el 24/09/2026). Sin nada que lo cubra, quien
carga un número y recarga la página enseguida lo ve vacío y parece que se
perdió.

El shim guarda lo propio recién escrito en `localStorage` y lo superpone a lo
que devuelve KV hasta que KV lo devuelve con un `updatedAt` igual o más nuevo,
con un techo de 5 minutos para que no tape indefinidamente un cambio de otra
persona. Verificado en las dos direcciones: el valor aguanta con KV atrasado, y
el pendiente se retira solo cuando KV alcanza.

Entre personas distintas la propagación sigue siendo de hasta ~40 segundos. Para
números que se cargan a mano y se miran, alcanza.

## Seguridad

**No tiene autenticación real.** El panel es público, así que cualquiera que lo
abra puede escribir estos números. Hay restricción por origen (sólo
`tiendaperfecta.github.io` y `localhost:8765`) y quedó preparada una clave
opcional vía el secreto `CLAVE_ESCRITURA` — pero como la clave viajaría en el
HTML público, frena bots y escrituras accidentales de otro origen, no a alguien
decidido.

Para protección de verdad hay que poner el panel detrás de un login, como
`arca-vs-gescom` y `transferencias`.

## Operar

```
npx wrangler deploy                  # desplegar
npx wrangler dev --local             # probar sin tocar producción
npx wrangler kv key list   --namespace-id a87015d636aa483ca58da29f0266bbcd --remote
npx wrangler kv key delete --namespace-id a87015d636aa483ca58da29f0266bbcd --remote "compromiso:<id>"
```
