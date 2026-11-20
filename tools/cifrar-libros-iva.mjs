// Uso: node cifrar-libros-iva.mjs <libros-iva.html> <carpeta-salida>
// La clave NO va por argumento: se lee de la variable de entorno LIBROS_IVA_CLAVE
// (la carga cifrar-libros-iva.ps1 pidiéndola sin mostrarla).
// Comprime el HTML con gzip y lo cifra con PBKDF2-SHA256 + AES-GCM 256, mismo
// sobre que auditoria-2026 pero con "zip":"gzip" (8,9 MB -> ~1-2 MB).
// Escribe <salida>/data.enc.json y <salida>/fecha.txt.
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync, gunzipSync } from 'node:zlib';
import { webcrypto as crypto } from 'node:crypto';

const [, , src, outDir] = process.argv;
const clave = process.env.LIBROS_IVA_CLAVE || '';
if (!src || !outDir) { console.error('Uso: node cifrar-libros-iva.mjs <libros-iva.html> <carpeta-salida>'); process.exit(1); }
if (clave.length < 8) { console.error('Falta la clave (LIBROS_IVA_CLAVE) o tiene menos de 8 caracteres.'); process.exit(1); }

const html = readFileSync(src, 'utf8');
const gen = html.match(/"generado":"(\d{4}-\d{2}-\d{2})"/);
if (!/<h1>Libros de IVA<\/h1>/.test(html) || !gen) { console.error('El archivo no parece la web de Libros de IVA.'); process.exit(1); }

const raw = Buffer.from(html, 'utf8');
const zip = gzipSync(raw, { level: 9 });

const ITER = 600000;
const salt = crypto.getRandomValues(new Uint8Array(16));
const iv = crypto.getRandomValues(new Uint8Array(12));
const derivar = async (k, s, uso) => crypto.subtle.deriveKey({ name: 'PBKDF2', salt: s, iterations: ITER, hash: 'SHA-256' },
  await crypto.subtle.importKey('raw', new TextEncoder().encode(k), 'PBKDF2', false, ['deriveKey']),
  { name: 'AES-GCM', length: 256 }, false, [uso]);
const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await derivar(clave, salt, 'encrypt'), zip));
const b64 = u => Buffer.from(u).toString('base64');
const out = join(outDir, 'data.enc.json');
writeFileSync(out, JSON.stringify({ v: 1, zip: 'gzip', iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) }));
writeFileSync(join(outDir, 'fecha.txt'), 'Ultima actualizacion: ' + gen[1] + '\n');

// Verificación: descifrar lo escrito y comparar; con otra clave tiene que fallar
const env = JSON.parse(readFileSync(out, 'utf8'));
const s2 = Buffer.from(env.salt, 'base64'), iv2 = Buffer.from(env.iv, 'base64'), ct2 = Buffer.from(env.ct, 'base64');
const pt = gunzipSync(Buffer.from(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv2 }, await derivar(clave, s2, 'decrypt'), ct2)));
const mb = n => (n / 1048576).toFixed(1) + ' MB';
console.log(pt.equals(raw) ? 'OK: descifra igual al original' : 'ERROR: no coincide con el original');
console.log('Datos al ' + gen[1] + ' | original ' + mb(raw.length) + ' | cifrado ' + mb(Buffer.byteLength(JSON.stringify(env))));
try {
  await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv2 }, await derivar(clave + 'x', s2, 'decrypt'), ct2);
  console.log('ERROR: descifró con una clave incorrecta');
} catch { console.log('OK: una clave incorrecta se rechaza'); }
