// ---------- depósito de los números que se cargan a mano ----------
// Reemplaza a claude.use("db"), que sólo existe cuando la página corre
// publicada como Artifact de Claude; servida desde GitHub Pages no existe y
// los campos de Compromiso y Proyección de IPs quedaban en "no disp.".
// Expone exactamente la superficie que usa el panel —doc().onSnapshot(),
// doc().set(), collection().onSnapshot()— contra el worker pepsico-db, así el
// resto del código queda igual. Las suscripciones en vivo de Firestore se
// emulan releyendo cada REFRESCO_MS: alcanza para números que se cargan a mano.
const DB_API = 'https://pepsico-db.tienda-perfecta.workers.dev';
const DB_CLAVE = '';           // sólo si se define CLAVE_ESCRITURA en el worker
const REFRESCO_MS = 15000;

let _dbPromesa = null;
function abrirDb() {
  // Una sola instancia para todo el panel: Compromiso e IPs comparten caché y
  // un único poller, en vez de dos pidiendo lo mismo cada 15 segundos.
  if (!_dbPromesa) _dbPromesa = _abrirDb();
  return _dbPromesa;
}

async function _abrirDb() {
  const ping = await fetch(DB_API + '/health', { cache: 'no-store' });
  if (!ping.ok) throw new Error('pepsico-db no responde: ' + ping.status);

  const cache = {};        // coleccion -> { id: documento }
  const subs = [];         // { col, id|null, cb, errCb, visto }
  const colecciones = new Set();
  const comoError = (e) => ({ code: e.code || 'db', message: e.message || String(e) });

  function avisar(col) {
    const datos = cache[col] || {};
    for (const s of subs) {
      if (s.col !== col) continue;
      try {
        // Sólo se avisa cuando cambió algo: si no, cada refresco pisaría el
        // input que la persona está tipeando en ese momento.
        if (s.id === null) {
          const firma = JSON.stringify(datos);
          if (firma === s.visto) continue;
          s.visto = firma;
          s.cb(Object.entries(datos).map(([id, d]) => ({ id, data: () => d })));
        } else {
          const d = datos[s.id];
          const firma = JSON.stringify(d === undefined ? null : d);
          if (firma === s.visto) continue;
          s.visto = firma;
          s.cb({ id: s.id, data: () => d });
        }
      } catch (e) {
        if (s.errCb) s.errCb(comoError(e));
      }
    }
  }

  // KV es de consistencia eventual: una escritura tarda hasta ~40 s en aparecer
  // en el list(). Sin esto, quien carga un numero y recarga enseguida lo ve
  // vacio y parece que se perdio. Se guarda lo propio recien escrito y se
  // superpone hasta que KV lo devuelve con un updatedAt igual o mas nuevo.
  const PENDIENTES = 'pepsico-db:pendientes';
  const VENTANA_MS = 5 * 60 * 1000;   // techo: pasado eso no se tapa mas nada

  function leerPendientes() {
    try { return JSON.parse(localStorage.getItem(PENDIENTES) || '{}'); } catch (e) { return {}; }
  }
  function guardarPendientes(p) {
    try { localStorage.setItem(PENDIENTES, JSON.stringify(p)); } catch (e) { /* modo privado */ }
  }
  function anotarPendiente(col, id, doc) {
    const p = leerPendientes();
    (p[col] = p[col] || {})[id] = { doc: doc, ts: Date.now() };
    guardarPendientes(p);
  }
  function superponer(col, datos) {
    const p = leerPendientes();
    const mios = p[col];
    if (!mios) return datos;
    let cambio = false;
    for (const id of Object.keys(mios)) {
      const ent = mios[id], enKv = datos[id];
      const alcanzado = enKv && typeof enKv.updatedAt === 'number'
        && typeof ent.doc.updatedAt === 'number' && enKv.updatedAt >= ent.doc.updatedAt;
      if (alcanzado || Date.now() - ent.ts > VENTANA_MS) { delete mios[id]; cambio = true; continue; }
      datos[id] = ent.doc;
    }
    if (cambio) { if (!Object.keys(mios).length) delete p[col]; guardarPendientes(p); }
    return datos;
  }

  async function refrescar() {
    for (const col of colecciones) {
      try {
        const r = await fetch(DB_API + '/d/' + col, { cache: 'no-store' });
        if (!r.ok) throw new Error('GET ' + col + ': ' + r.status);
        cache[col] = superponer(col, await r.json());
        avisar(col);
      } catch (e) {
        for (const s of subs) if (s.col === col && s.errCb) s.errCb(comoError(e));
      }
    }
  }

  // El primer refresco va en un timeout, no ya mismo: las suscripciones se
  // registran recién cuando esta promesa resuelve, y el timeout corre después
  // de todos esos microtasks, así la primera lectura ya las incluye a todas.
  setTimeout(refrescar, 0);
  setInterval(refrescar, REFRESCO_MS);

  return {
    doc(path) {
      const corte = path.indexOf('/');
      const col = path.slice(0, corte), id = path.slice(corte + 1);
      colecciones.add(col);
      return {
        onSnapshot(cb, errCb) { subs.push({ col, id, cb, errCb, visto: undefined }); },
        async set(obj) {
          const r = await fetch(DB_API + '/d/' + col + '/' + id, {
            method: 'PUT',
            headers: Object.assign({ 'content-type': 'application/json' },
                                   DB_CLAVE ? { 'x-clave': DB_CLAVE } : {}),
            body: JSON.stringify(obj),
          });
          if (!r.ok) { const e = new Error('no se pudo guardar'); e.code = String(r.status); throw e; }
          const j = await r.json();
          // Se refleja al toque sin esperar el próximo refresco, para que el
          // total de la tabla se actualice apenas la persona sale del campo.
          (cache[col] = cache[col] || {})[id] = j.doc;
          anotarPendiente(col, id, j.doc);
          avisar(col);
        },
      };
    },
    collection(nombre) {
      colecciones.add(nombre);
      return {
        onSnapshot(cb, errCb) { subs.push({ col: nombre, id: null, cb, errCb, visto: undefined }); },
      };
    },
  };
}
