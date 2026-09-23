# Panel de ventas de Georgalos

`https://tiendaperfecta.github.io/georgalos/`

```
GitHub Action (todos los dias 23:30 AR)  →  tools/georgalos.py  →  georgalos/data.json  →  index.html
```

- **Por vendedor:** venta por marca, descuento, margen y cobertura (total y por marca).
- **Por taxonomia:** venta y clientes no compradores por segmento A/B/C/D, con el
  listado de clientes al hacer click.

Usa los mismos secrets de GesCom que el panel de Axum (`GESCOM_REALM`,
`GESCOM_CLIENT_ID`, `GESCOM_USERNAME`, `GESCOM_PASSWORD`). Las formulas y los
criterios estan en el encabezado de `tools/georgalos.py`.

Correr a mano: **Actions → Georgalos data refresh → Run workflow**, o localmente
`python tools/georgalos.py [AAAA-MM-DD]` con esas variables de entorno.

Notas:
- La marca se deduce de la descripcion del articulo (GesCom tiene `codigoMarca = 1`
  para todo Georgalos). Si aparece un producto nuevo que cae en "Otras", sumar
  la regla en `MARCAS`.
- Solo tienen fila los vendedores que vendieron Georgalos en el mes.
- `data.json` incluye nombre, direccion y telefono de los clientes no compradores
  (igual que `pepsico/no_compradores_detalle.json`).
