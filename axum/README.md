# Panel Axum

Tablero que combina datos de **Axum Orders360** (ventas/pedidos) y **Axum GPS**
(posiciones, visitas, km de los vendedores).

## Cómo funciona

```
GitHub Action (cada 1 h)  →  tools/axum_fetch.py  →  axum/data/*.json  →  index.html (GitHub Pages)
```

1. Una GitHub Action corre `tools/axum_fetch.py` **cada 1 hora, de 8 a 21 hs**
   (hora argentina), todos los dias. Tambien se puede disparar a mano.
2. El script se loguea en ambos sistemas de Axum, baja los datos y escribe los JSON
   en `axum/data/`.
3. Si algo cambió, commitea los JSON. El panel estático (esta carpeta) los lee.

Los JSON que hay ahora son **datos de ejemplo** (`"sample": true`) para que el panel
se vea antes de configurar la Action. Se reemplazan solos en la primera corrida real.

## Configuración (una sola vez)

### 1. Cargar los secrets
En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Crear estos cuatro:

| Secret | Valor |
|--------|-------|
| `AXUM_ORDERS_USER` | usuario de www.axum.com.ar/tiendaperfecta (ej. `admin`) |
| `AXUM_ORDERS_PASS` | contraseña de ese usuario |
| `GPS_USER` | `TIENDAPERFECTAgps` |
| `GPS_PASS` | contraseña del GPS |

> Nunca pongas las credenciales en el código ni en los JSON. Solo como secrets.

### 2. Probar la Action
En la pestaña **Actions → Axum data refresh → Run workflow** (botón manual).
Debería terminar en verde y actualizar `axum/data/*.json`. Si un sistema falla,
lo anota en `data/meta.json` y el panel muestra el aviso, pero no rompe el resto.

### 3. Ver el panel
`https://tiendaperfecta.github.io/axum/`

## Probar el fetch localmente
```bash
pip install requests
set AXUM_ORDERS_USER=...   &  set AXUM_ORDERS_PASS=...
set GPS_USER=TIENDAPERFECTAgps  &  set GPS_PASS=...
python tools/axum_fetch.py
```

## Tres fuentes

| Fuente | Qué aporta |
|---|---|
| **Axum GPS** | por dónde pasó el vendedor, cuánto tiempo estuvo, posiciones, camiones |
| **Axum Orders360** | pedidos y facturación |
| **GesCom** | quién es cada cliente, **dónde está de verdad**, y a quién le tocaba visitar cada día |

> La geolocalización y la cartera de Axum **no se usan**: sus coordenadas no son
> confiables y no tiene cargadas las frecuencias. GesCom devuelve el 100% de los
> clientes con coordenada y la ruta de preventa por día (`rutasPreventa`), así que
> las zonas se **dibujan** con los clientes reales y la cobertura se mide contra
> lo que al vendedor le tocaba hacer ese día.

### Refresco del maestro de GesCom

`tools/gescom.py` regenera `clientes.json`, `rutas.json` y `zonas.json` en cada
corrida **si** están cargados estos secrets; si no, quedan los archivos ya
commiteados (que funcionan igual, solo que congelados):

`GESCOM_REALM` · `GESCOM_CLIENT_ID` · `GESCOM_USERNAME` · `GESCOM_PASSWORD`

Son los mismos que usa el Panel de Fleteros.

## Zonas dibujadas por manzana

La zona de cada vendedor no sale de Axum: se dibuja con los clientes que le
tocan ese día según GesCom, pero **sobre una grilla de 100 m** (una cuadra de
Mar del Plata), no uniendo los puntos:

1. se marcan las celdas donde hay al menos un cliente,
2. se les suma una celda de halo para unir manzanas vecinas,
3. se traza el contorno de esa unión (los lados que comparten dos celdas se
   cancelan; los que quedan son el borde) y se sacan los vértices alineados.

Así el borde cae **por la calle** y no por la puerta de un comercio. Una ruta
repartida en barrios separados da **varias áreas**, que es lo que realmente es:
antes la envolvente convexa daba un triángulo de 6 × 21 km para el vendedor 10;
ahora son 1,7 km² de manzanas.

## Alertas de jornada

Cartel arriba de todo, visible en cualquier pestaña, con los nombres y la hora:

- **Llegó tarde:** primera señal de actividad después de las **09:00**.
- **Se fue temprano:** última señal antes de las **14:00**, y solo se evalúa
  pasada esa hora (si no, a las 11 de la mañana los marcaría a todos).

Los umbrales están en `tools/axum_detalle.py` (`HORA_LLEGADA` / `HORA_SALIDA`).

## Histórico

Cada día se publica entero como `dia-<fecha>.json`, y `dias.json` es el índice.
El panel tiene un selector de día que alimenta Tiempos, Clientes y Zonas. Los
días que falten se completan de a tres por corrida hasta cubrir 30 días hacia
atrás (el histórico de km y de entrada a zona no se reconstruye: son muchas
llamadas y Axum no las conserva bien).

## Qué muestra cada pestaña

| Pestaña | Datos | Fuente |
|---|---|---|
| Ventas | facturación, pedidos, canales, top clientes | Orders360 |
| Mapa vendedores | última posición | `lastPositions` |
| Visitas y cobertura | visitas y km del día | `soloClientesVisitados` |
| Cruce ventas + GPS | facturación vs. actividad | ambos |
| **Tiempos y jornada** | minutos por visita, primera/última, km, línea de tiempo, alertas | `pasoPoprPDVAt`, `FindClientesVisitadosConTimestamp`, `kmRecorridosCtrl` |
| **Clientes del día** | pasó / no pasó / le vendió / le vendió sin pasar, con fecha y hora de la venta, y cumplimiento de la ruta | `pasoPoprPDVAt` + GesCom + Orders360 |
| **Zonas y ruta** | la zona dibujada con los clientes, visitados en verde y pendientes en rojo | GesCom |
| **LDR** | choferes: repartos, entregas, efectividad, cartones y ranking del mes + camiones en el mapa | [Panel de Fleteros](https://tiendaperfecta.github.io/fleteros/) (API GesCom) + `lastTruckPositions` |

### Zonas: qué se pudo y qué no (22/09/2026)

Cada vendedor tiene zonas con nombre de día (`allZoneByVendedor` → `LUNES`,
`MARTES`, …), así que en teoría se puede marcar cuándo entra a la zona que le
toca. En la práctica **solo funciona para algunos**: se mide qué porcentaje de
la cartera del vendedor cae dentro de sus zonas (`carteraEnZona`) y da entre
0% y 97% según el vendedor, y un día normal solo 3 de 13 registran algún punto
GPS dentro de su zona del día. Las zonas existen y son suyas, pero no reflejan
dónde trabajan hoy.

Por eso la alerta usa la **primera señal de actividad** (entrada a zona o
primera visita, la que sea más temprana). Si se usara solo la zona, a alguien
que arrancó 08:59 se lo marcaría como llegado 10:25.

### Métodos del GPS verificados (22/09/2026)

Funcionan: `pasoPoprPDVAt(aDate)` → `{sellerId, clientId, tiempo "mm:ss", visito
"SI/NO", horario}` · `FindClientesVisitadosConTimestamp(_dia)` → `"clientId,fecha hora"` ·
`allClientsPositionByVendedor(idVendedor)` → CSV `id,lat,lng,NOMBRE (rubro),canal,dirección` ·
`kmRecorridosCtrl(sellerId, aDate)` · `lastTruckPositions`.

**No funcionan en este sistema** (probados, no es un bug nuestro):
`reporteTiempoEnPDVDiario` y `reporteTiempoEnPDVDiarioBySeller` responden **HTTP 500**;
`coberturaVendedor`, `timeToSellVendedor`, `frecuenciaByVendedorDia`, `trucks`,
`allZonasReparto`, `cantidadDeClientesVisitadosPorCamion`,
`distanciaRecorridaPorCamionesEnFecha`, `allTrucksOilStatus` y `eventsAtDateAndTruck`
devuelven **vacío**. Por eso los tiempos y la cobertura se calculan acá desde los
datos crudos, y la pestaña LDR solo puede mostrar posiciones.

> Ojo con la **cartera**: es el total de clientes del vendedor, no la ruta del día.
> Como las frecuencias no están cargadas en Axum, no hay forma de saber a quién le
> tocaba visitar hoy. El % de cobertura sirve para comparar vendedores, no como meta.

## Aprendido con datos reales (21/09/2026)

- **Login de Orders360:** los campos del form se llaman `ctl03$UserName` /
  `ctl03$Password` (WebForms les pone prefijo). El script los lee del HTML.
  Si Axum rechaza las credenciales, la pagina responde *"Login Incorrecto"* y
  eso queda escrito en `meta.json`.
- **Salvavidas:** si el login deja de funcionar, se puede cargar el JWT a mano
  en un secret `AXUM_TOKEN` (se obtiene entrando al panel y copiando lo que
  sigue a `token=` en la URL). Tiene prioridad sobre el login automatico.
- **El GPS devuelve JSON adentro de un string.** `soloClientesVisitados` no
  devuelve una lista: devuelve un string con el JSON. Hay que `json.loads`
  la respuesta o se termina iterando caracteres sueltos.
- **Km:** `dailySellerTravelledKmReport` responde OK pero vacio durante el dia.
  La columna "Km" queda en `—` hasta confirmar si el reporte se arma al cierre.
- `meta.json` guarda en `formatoGps` una muestra cruda del formato real, para
  no tener que volver a descubrirlo.

## Pendiente / a revisar

- ~~Mapeo de id de vendedor~~ **verificado 21/09/2026: los ids coinciden.** El
  `sellerId` de Orders360 y el id del GPS son el mismo número, no hace falta
  tabla de equivalencias. (Cuidado: en `lastPositions` el id está en el campo 3,
  no en el 5 como decía la doc.)
- **Nombres de vendedores en GPS:** hoy el mapa/visitas muestran el id. Si querés el
  nombre, se puede sumar una llamada al listado de vendedores del GPS.
- Documentación técnica completa de ambas APIs: ver la carpeta de trabajo `axum-api/`
  (README.md y README-GPS.md) generada durante el mapeo.
