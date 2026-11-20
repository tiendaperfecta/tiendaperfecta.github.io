# Copia la web de Libros de IVA de Mariano (marianozeballos-tp/libros-iva) al
# panel libros-iva/ de la central, cifrada con clave.
# Pide la clave sin mostrarla en pantalla; no queda escrita en ningún archivo.
# La copia diaria la hace sola .github/workflows/libros-iva-data.yml; esto queda
# para correrlo a mano o para CAMBIAR la clave. Si se cambia la clave acá, hay que
# cargar la misma en el secret LIBROS_IVA_CLAVE o el workflow va a fallar avisando.
$ErrorActionPreference = 'Stop'
$repo   = Split-Path -Parent $PSScriptRoot
$salida = Join-Path $repo 'libros-iva'
$tmp    = Join-Path ([IO.Path]::GetTempPath()) 'libros-iva-original.html'

Write-Host 'Bajando la web de Libros de IVA...'
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/marianozeballos-tp/libros-iva/main/index.html' -OutFile $tmp

$s1 = Read-Host 'Clave del panel' -AsSecureString
$s2 = Read-Host 'Repetila' -AsSecureString
$a = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1))
$b = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2))
if ($a -ne $b) { Write-Host 'Las claves no coinciden. No se cambio nada.' -ForegroundColor Red; exit 1 }

try {
  $env:LIBROS_IVA_CLAVE = $a
  node (Join-Path $PSScriptRoot 'cifrar-libros-iva.mjs') $tmp $salida
  if ($LASTEXITCODE -ne 0) { throw 'Fallo el cifrado' }
} finally {
  Remove-Item Env:LIBROS_IVA_CLAVE -ErrorAction SilentlyContinue
  $a = $null; $b = $null
  Remove-Item $tmp -ErrorAction SilentlyContinue
}
Write-Host 'Listo: libros-iva/data.enc.json, fecha.txt y origen.txt actualizados.' -ForegroundColor Green
