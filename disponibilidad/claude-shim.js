/* Copia del Panel PepsiCo para GitHub Pages.
   Fuera de claude.ai no existe window.claude: este archivo lo reemplaza por una versión
   de sólo lectura que lee las fotos de datos/*.json que sube publicar-github.ps1. */
(function () {
  if (window.claude) return;
  // Copia en la Central (tiendaperfecta.github.io/disponibilidad): los datos se leen de la web de
  // Mariano, que es la que los actualiza (tarea recibido-pepsico, lunes a sábado 8:00 y 14:00).
  var DATOS = 'https://marianozeballos-tp.github.io/panel-pepsico/datos/';
  var cargar = function (n) {
    return fetch(DATOS + n + '.json?v=' + Date.now()).then(function (r) {
      if (!r.ok) throw new Error('No se pudo leer datos/' + n + '.json');
      return r.json();
    });
  };
  var sinPermiso = function () { return Promise.reject({ code: 'invalid_argument', message: 'Copia de sólo lectura' }); };
  var generado = null;

  window.claude = {
    use: function (cap) {
      if (cap === 'mcp') return Promise.resolve({
        watchTool: function (server, tool, input, cb) {
          var vivo = true;
          cargar('pedido').then(function (p) {
            generado = p.generado; marcar();
            if (vivo) cb({ type: 'data', result: { payload: { filas: p.datos.length, recortado: !!p.recortado, datos: p.datos }, cache: { storedAt: Date.parse(p.generado) } } });
          }).catch(function (e) {
            if (vivo) cb({ type: 'error', error: { code: 'unavailable', message: e.message } });
          });
          return function () { vivo = false; };
        }
      });
      if (cap === 'db') return Promise.resolve({
        collection: function (n) {
          return {
            onSnapshot: function (ok, err) {
              cargar(n).then(function (o) {
                ok({ docs: Object.keys(o).map(function (id) { return { id: id, exists: true, data: function () { return o[id]; } }; }) });
              }).catch(function (e) { if (err) err(e); });
              return function () {};
            }
          };
        },
        doc: function () { return { set: sinPermiso, update: sinPermiso }; }
      });
      if (cap === 'user') return Promise.resolve({
        canEdit: function () { return Promise.resolve(false); },
        id: function () { return Promise.resolve(null); }
      });
      return Promise.resolve(null);
    }
  };

  var aviso = null;
  function marcar() {
    if (!aviso) return;
    var cuando = '';
    try { cuando = generado ? ' (datos al ' + new Date(generado).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) + ')' : ''; } catch (e) { /* nada */ }
    aviso.textContent = 'Copia de sólo lectura' + cuando + '. El pedido se calcula con 14 días de stock y entrega en el día. El objetivo y la lista de artículos activos se cargan desde el panel en claude.ai.';
  }
  document.addEventListener('DOMContentLoaded', function () {
    ['p-cob', 'p-plazo', 'p-desc', 'p-reset'].forEach(function (id) { var e = document.getElementById(id); if (e) e.disabled = true; });
    var head = document.querySelector('header.brand');
    aviso = document.createElement('p');
    aviso.className = 'note';
    aviso.style.margin = '0 0 12px';
    if (head) head.insertAdjacentElement('afterend', aviso);
    marcar();
  });
})();
