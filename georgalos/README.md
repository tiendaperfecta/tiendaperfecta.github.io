# Panel de ventas de Georgalos

`https://tiendaperfecta.github.io/georgalos/`

```
GitHub Action (cada 1 hora, lunes a sabado)  →  tools/georgalos.py  →  georgalos/*.json  →  index.html
```

- **Filtro de fechas:** desde / hasta, cualquier dia del año, con atajos (hoy, ayer,
  esta semana, este mes, mes anterior, año). El rango queda en la URL
  (`?desde=2026-03-01&hasta=2026-03-31`), asi se puede compartir.
- **Por vendedor:** venta por marca, descuento, margen y cobertura (total y por marca).
- **Por taxonomia:** venta y clientes no compradores por segmento A/B/C/D, con el
  listado de clientes al hacer click.

## Datos

| Archivo | Contenido |
|---|---|
| `meses/AAAA-MM.json` | una fila por dia, vendedor, cliente y marca: `[dia, vendedor, cliente, marca, bruta, neta, costo, unidades, sinFacturar]` |
| `maestro.json` | clientes (nombre, direccion, localidad, telefono, segmento), ruta de cada vendedor y nombres |
| `indice.json` | meses disponibles y hora de la ultima actualizacion |

El panel baja solo los meses del rango elegido y calcula todo en el navegador.

La Action refresca el mes en curso (y el anterior los primeros 7 dias, por lo
que se factura tarde). Para rehacer meses viejos:

```
python tools/georgalos.py 2026-03 2026-04     # esos meses
python tools/georgalos.py --anio 2026         # todo el año hasta hoy
```

Usa los mismos secrets de GesCom que el panel de Axum (`GESCOM_REALM`,
`GESCOM_CLIENT_ID`, `GESCOM_USERNAME`, `GESCOM_PASSWORD`). Las formulas y los
criterios estan en el encabezado de `tools/georgalos.py`.

Notas:
- La marca se deduce de la descripcion del articulo (GesCom tiene `codigoMarca = 1`
  para todo Georgalos). Si aparece un producto nuevo que cae en "Otras", sumar
  la regla en `MARCAS` y volver a correr los meses afectados.
- El universo de cada vendedor es su ruta de preventa **actual**, tambien para
  fechas pasadas (GesCom no guarda el historial de rutas).
- Solo tienen fila los vendedores que vendieron Georgalos en el periodo.
- `maestro.json` incluye nombre, direccion y telefono de los clientes
  (igual que `pepsico/no_compradores_detalle.json`).
