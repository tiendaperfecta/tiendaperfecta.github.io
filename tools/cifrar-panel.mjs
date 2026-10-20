// Uso: node cifrar-panel.mjs <panel.html> <salida.enc.json> <clave>
// Cifra el HTML con PBKDF2-SHA256 + AES-GCM 256 (mismo formato que ctacte/data.enc.json).
import { readFileSync, writeFileSync } from 'node:fs';
import { webcrypto as crypto } from 'node:crypto';

const [, , src, out, clave] = process.argv;
if (!src || !out || !clave) { console.error('Faltan argumentos'); process.exit(1); }

let html = readFileSync(src, 'utf8');
html = html.replace('<title>Auditoría Rumbo 2026</title>', '<title>Auditoría 2026</title>');

const ITER = 600000;
const salt = crypto.getRandomValues(new Uint8Array(16));
const iv = crypto.getRandomValues(new Uint8Array(12));
const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(clave), 'PBKDF2', false, ['deriveKey']);
const key = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: ITER, hash: 'SHA-256' },
  base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(html)));
const b64 = u => Buffer.from(u).toString('base64');
writeFileSync(out, JSON.stringify({ v: 1, iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) }));

// Verificación: descifrar lo que se escribió
const env = JSON.parse(readFileSync(out, 'utf8'));
const k2 = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: Buffer.from(env.salt, 'base64'), iterations: env.iter, hash: 'SHA-256' },
  await crypto.subtle.importKey('raw', new TextEncoder().encode(clave), 'PBKDF2', false, ['deriveKey']),
  { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
const pt = new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: Buffer.from(env.iv, 'base64') }, k2, Buffer.from(env.ct, 'base64')));
console.log('ok', pt === html, 'bytes', html.length, 'titulo', /<title>Auditoría 2026<\/title>/.test(pt));
// Con una clave errónea tiene que fallar
try {
  const kx = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: Buffer.from(env.salt, 'base64'), iterations: env.iter, hash: 'SHA-256' },
    await crypto.subtle.importKey('raw', new TextEncoder().encode(clave + 'x'), 'PBKDF2', false, ['deriveKey']),
    { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  await crypto.subtle.decrypt({ name: 'AES-GCM', iv: Buffer.from(env.iv, 'base64') }, kx, Buffer.from(env.ct, 'base64'));
  console.log('ERROR: descifró con clave incorrecta');
} catch { console.log('clave incorrecta rechazada'); }
