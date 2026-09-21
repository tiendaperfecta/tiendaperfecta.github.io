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

- **Mapeo de id de vendedor:** el "cruce ventas + GPS" une por id de vendedor. Si el
  `sellerId` de Orders360 no es el mismo número que el id del GPS, hay que agregar una
  tabla de equivalencias en `axum_fetch.py`. Con datos reales se verifica en un minuto.
- **Nombres de vendedores en GPS:** hoy el mapa/visitas muestran el id. Si querés el
  nombre, se puede sumar una llamada al listado de vendedores del GPS.
- Documentación técnica completa de ambas APIs: ver la carpeta de trabajo `axum-api/`
  (README.md y README-GPS.md) generada durante el mapeo.
