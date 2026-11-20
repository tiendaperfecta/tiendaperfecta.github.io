// Uso: node cifrar-libros-iva.mjs <libros-iva.html> <carpeta-salida> [--misma-clave]
// La clave NO va por argumento: se lee de la variable de entorno LIBROS_IVA_CLAVE
// (la carga cifrar-libros-iva.ps1 pidiéndola sin mostrarla, o el workflow
// libros-iva-data.yml desde el secret del mismo nombre).
// Comprime el HTML con gzip y lo cifra con PBKDF2-SHA256 + AES-GCM 256, mismo
// sobre que auditoria-2026 pero con "zip":"gzip" (8,9 MB -> ~2,6 MB).
// Escribe <salida>/data.enc.json, <salida>/fecha.txt y <salida>/origen.txt
// (sha de git del HTML de Mariano: el workflow lo compara para no recifrar de balde).
// --misma-clave: si ya hay un data.enc.json, exige que la clave lo abra; así un
// secret mal cargado no le cambia la clave al panel sin avisar.
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync, gunzipSync } from 'node:zlib';
import { webcrypto as crypto, createHash } from 'node:crypto';

const [, , src, outDir, ...flags] = process.argv;
const clave = process.env.LIBROS_IVA_CLAVE || '';
if (!src || !outDir) { console.error('Uso: node cifrar-libros-iva.mjs <libros-iva.html> <carpeta-salida> [--misma-clave]'); process.exit(1); }
if (clave.length < 8) { console.error('Falta la clave (LIBROS_IVA_CLAVE) o tiene menos de 8 caracteres.'); process.exit(1); }

const raw = readFileSync(src);
const html = raw.toString('utf8');
const gen = html.match(/"generado":"(\d{4}-\d{2}-\d{2})"/);
if (!/<h1>Libros de IVA<\/h1>/.test(html) || !gen) { console.error('El archivo no parece la web de Libros de IVA.'); process.exit(1); }
const origen = createHash('sha1').update('blob ' + raw.length + '\0').update(raw).digest('hex');

const ITER = 600000;
const derivar = async (k, s, uso, iter = ITER) => crypto.subtle.deriveKey({ name: 'PBKDF2', salt: s, iterations: iter, hash: 'SHA-256' },
  await crypto.subtle.importKey('raw', new TextEncoder().encode(k), 'PBKDF2', false, ['deriveKey']),
  { name: 'AES-GCM', length: 256 }, false, [uso]);
const out = join(outDir, 'data.enc.json');

if (flags.includes('--misma-clave') && existsSync(out)) {
  const prev = JSON.parse(readFileSync(out, 'utf8'));
  try {
    await crypto.subtle.decrypt({ name: 'AES-GCM', iv: Buffer.from(prev.iv, 'base64') },
      await derivar(clave, Buffer.from(prev.salt, 'base64'), 'decrypt', prev.iter), Buffer.from(prev.ct, 'base64'));
  } catch {
    console.error('La clave no abre el panel publicado: el secret LIBROS_IVA_CLAVE no coincide con la clave actual. No se cambió nada.');
    process.exit(1);
  }
}

const zip = gzipSync(raw, { level: 9 });
const salt = crypto.getRandomValues(new Uint8Array(16));
const iv = crypto.getRandomValues(new Uint8Array(12));
const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await derivar(clave, salt, 'encrypt'), zip));
const b64 = u => Buffer.from(u).toString('base64');
writeFileSync(out, JSON.stringify({ v: 1, zip: 'gzip', iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) }));
writeFileSync(join(outDir, 'fecha.txt'), 'Ultima actualizacion: ' + gen[1] + '\n');
writeFileSync(join(outDir, 'origen.txt'), origen + '\n');

// Verificación: descifrar lo escrito y comparar; con otra clave tiene que fallar
const env = JSON.parse(readFileSync(out, 'utf8'));
const s2 = Buffer.from(env.salt, 'base64'), iv2 = Buffer.from(env.iv, 'base64'), ct2 = Buffer.from(env.ct, 'base64');
const pt = gunzipSync(Buffer.from(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv2 }, await derivar(clave, s2, 'decrypt'), ct2)));
const mb = n => (n / 1048576).toFixed(1) + ' MB';
if (!pt.equals(raw)) { console.error('ERROR: lo cifrado no coincide con el original'); process.exit(1); }
console.log('OK: descifra igual al original');
console.log('Datos al ' + gen[1] + ' | original ' + mb(raw.length) + ' | cifrado ' + mb(Buffer.byteLength(JSON.stringify(env))));
let abreConOtra = false;
try { await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv2 }, await derivar(clave + 'x', s2, 'decrypt'), ct2); abreConOtra = true; } catch {}
if (abreConOtra) { console.error('ERROR: descifró con una clave incorrecta'); process.exit(1); }
console.log('OK: una clave incorrecta se rechaza');
