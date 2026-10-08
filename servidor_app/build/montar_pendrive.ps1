# Monta a pasta de instalacao sem internet (pendrive ou pasta da rede) do Argos EPI Servidor.
# Copia o instalador, o latest.json e os pacotes das placas escolhidas, na estrutura que o instalador
# procura ao lado dele (latest.json + win\). Em cada computador basta abrir o instalador dessa pasta.
#
# Uso: powershell -ExecutionPolicy Bypass -File montar_pendrive.ps1 -Destino E:\ArgosEPI [-Placas cpu,dml,nvidia]
#      [-Saida D:\argos-build\saida]
# Placas que nao forem copiadas continuam instalando: o instalador baixa da internet o que faltar.
param(
  [Parameter(Mandatory = $true)][string]$Destino,
  [string[]]$Placas = @("cpu"),
  [string]$Saida = "D:\argos-build\saida"
)
$ErrorActionPreference = "Stop"
$Placas = @($Placas | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$m = Get-Content (Join-Path $Saida "latest.json") -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force (Join-Path $Destino "win") | Out-Null
$arquivos = @("latest.json", "ArgosEPI-Servidor-Setup.exe", $m.windows.base.url)
foreach ($p in $Placas) {
  if (-not $m.windows.programa2.$p) { throw "placa desconhecida ou nao empacotada: $p (use cpu, dml ou nvidia)" }
  $arquivos += $m.windows.programa2.$p.url, $m.windows.nucleo.$p.url, $m.windows.motor.$p.url
}
$total = 0
foreach ($a in $arquivos) {
  $de = Join-Path $Saida ($a -replace "/", "\")
  $para = Join-Path $Destino ($a -replace "/", "\")
  if (-not (Test-Path $de)) { throw "nao encontrei $de (rode o empacotar_windows.ps1 antes)" }
  if (-not (Test-Path $para) -or (Get-Item $para).Length -ne (Get-Item $de).Length) {
    Write-Host ("copiando {0} ({1:N0} MB)" -f $a, ((Get-Item $de).Length / 1MB))
    Copy-Item $de $para -Force
  } else { Write-Host "ja esta la: $a" }
  $total += (Get-Item $de).Length
}
Write-Host ("`nPronto: {0} ({1:N1} GB). Versao {2}, placas: {3}." -f $Destino, ($total / 1GB), $m.versao, ($Placas -join ", "))
Write-Host "Em cada computador: abra ArgosEPI-Servidor-Setup.exe dessa pasta (ele instala sem internet)."
