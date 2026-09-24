// Depósito de los números que se cargan a mano en el panel Avance PepsiCo.
// Ver wrangler.toml para el porqué y el modelo de claves.
//
// Rutas:
//   GET  /d/:col          -> { "<id>": {...}, ... }   (un list() de KV)
//   PUT  /d/:col/:id      -> guarda el documento       (pide la clave)
//   GET  /health          -> ok
//
// Sólo se aceptan las dos colecciones del panel: una ruta con cualquier otro
// nombre devuelve 404 en vez de crear una colección nueva por un typo.

const COLECCIONES = new Set(['compromiso', 'ipProyeccion']);
const ORIGENES = new Set([
  'https://tiendaperfecta.github.io',
  'http://localhost:8765',   // para probar el panel local antes de publicar
]);

function cors(origin) {
  const h = {
    'access-control-allow-methods': 'GET,PUT,OPTIONS',
    'access-control-allow-headers': 'content-type,x-clave',
    'access-control-max-age': '86400',
  };
  if (origin && ORIGENES.has(origin)) h['access-control-allow-origin'] = origin;
  return h;
}

const json = (obj, status, origin) => new Response(JSON.stringify(obj), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...cors(origin) },
});

// id de documento: lo que manda el panel es un slug ya normalizado
// (sin acentos, [A-Za-z0-9_]). Se valida igual para no guardar basura.
const ID_OK = /^[A-Za-z0-9_]{1,64}$/;

export default {
  async fetch(request, env) {
    const origin = request.headers.get('origin');
    const url = new URL(request.url);
    const partes = url.pathname.split('/').filter(Boolean);

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors(origin) });
    if (partes[0] === 'health') return json({ ok: true }, 200, origin);
    if (partes[0] !== 'd' || !COLECCIONES.has(partes[1])) return json({ error: 'no existe' }, 404, origin);

    const col = partes[1];

    if (request.method === 'GET' && partes.length === 2) {
      // El valor de cada documento viaja en la metadata de la clave, así que
      // un solo list() devuelve la colección entera.
      const out = {};
      let cursor;
      do {
        const r = await env.DATOS.list({ prefix: col + ':', cursor });
        for (const k of r.keys) {
          const id = k.name.slice(col.length + 1);
          if (k.metadata) out[id] = k.metadata;
        }
        cursor = r.list_complete ? null : r.cursor;
      } while (cursor);
      return json(out, 200, origin);
    }

    if (request.method === 'PUT' && partes.length === 3) {
      if (!origin || !ORIGENES.has(origin)) return json({ error: 'origen no permitido' }, 403, origin);
      if (env.CLAVE_ESCRITURA && request.headers.get('x-clave') !== env.CLAVE_ESCRITURA) {
        return json({ error: 'clave invalida' }, 401, origin);
      }
      const id = partes[2];
      if (!ID_OK.test(id)) return json({ error: 'id invalido' }, 400, origin);

      let doc;
      try { doc = await request.json(); } catch { return json({ error: 'json invalido' }, 400, origin); }
      if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return json({ error: 'json invalido' }, 400, origin);

      // Se guardan sólo números y null: el panel manda eso y nada más. Así un
      // payload raro no puede inflar la metadata por encima del límite de KV.
      const limpio = { updatedAt: Date.now() };
      for (const [k, v] of Object.entries(doc)) {
        if (k === 'updatedAt') continue;
        if (v === null || typeof v === 'number') limpio[k] = v;
      }
      const cuerpo = JSON.stringify(limpio);
      if (cuerpo.length > 900) return json({ error: 'documento demasiado grande' }, 413, origin);

      await env.DATOS.put(col + ':' + id, cuerpo, { metadata: limpio });
      return json({ ok: true, id, doc: limpio }, 200, origin);
    }

    return json({ error: 'no existe' }, 404, origin);
  },
};
