# Actualizacion semanal del panel Flujo de Caja (tarea programada de Windows "Flujo de Caja TP-RL").
# Corre en la PC de la oficina porque necesita W:\ y la carpeta Descargas.
# Sube SOLO flujo/data.enc.json y flujo/fecha.txt (cifrados). Deja registro en tools/flujo-semanal.log.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $PSScriptRoot 'flujo-semanal.log'
function Anotar($t) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $t" | Out-File -FilePath $log -Append -Encoding utf8 }

try {
  Set-Location $repo
  if (-not (Test-Path 'W:\FLUJO DE CAJA')) { throw 'La unidad W: no esta disponible.' }
  git pull --rebase --autostash -q origin main
  if ($LASTEXITCODE -ne 0) { throw 'git pull fallo' }
  python tools/flujo.py
  if ($LASTEXITCODE -ne 0) { throw 'tools/flujo.py fallo' }
  git add flujo/data.enc.json flujo/fecha.txt
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) { Anotar 'Sin cambios.'; exit 0 }
  git commit -q -m "chore(flujo): refresh data $(Get-Date -Format 'yyyy-MM-dd')" -- flujo/data.enc.json flujo/fecha.txt
  # los robots de axum/georgalos suben seguido: si el push choca, se rebasa y se reintenta
  for ($i = 1; $i -le 3; $i++) {
    git push -q origin main
    if ($LASTEXITCODE -eq 0) { Anotar 'Actualizado y subido.'; exit 0 }
    git pull --rebase --autostash -q origin main
  }
  throw 'git push fallo 3 veces'
} catch {
  Anotar "ERROR: $($_.Exception.Message)"
  exit 1
}
