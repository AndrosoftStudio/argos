# Gera o Argos EPI Servidor para Windows (programa com janela + instalador), pronto para subir ao R2:
#   <Saida>\ArgosEPI-Servidor-Setup.exe (+ copias -nvidia, -amd-intel, -cpu: o site baixa a da placa escolhida)
#   <Saida>\win\argos-app-<versao>.zip                programa, backend, interface, modelos, PostgreSQL, cloudflared
#   <Saida>\win\python-<placa>-<id>.zip               Python 3.12 + PyTorch + bibliotecas ja compiladas
#   <Saida>\latest.json                               o que o instalador e o programa leem
# O Python so e refeito quando muda (placa + requirements.txt): as atualizacoes do programa ficam pequenas.
# Requisitos: Windows 10/11 (csc do .NET Framework, curl, tar) e internet na primeira vez.
# Uso: powershell -ExecutionPolicy Bypass -File empacotar_windows.ps1 -Versao 20.1.0 [-Placas cpu,nvidia,dml] [-SoPrograma]
param(
  [string]$Versao = "20.1.0",
  [string[]]$Placas = @("cpu", "nvidia", "dml"),
  [string]$Trabalho = "D:\argos-build",
  [string]$Notas = "",
  [switch]$SoPrograma
)
$ErrorActionPreference = "Stop"
# com -File a lista chega como um texto so ("cpu,dml,nvidia")
$Placas = @($Placas | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$app =Split-Path -Parent $PSScriptRoot             # servidor_app
$raiz = Split-Path -Parent $app                     # pasta do projeto (v20)
$cache = Join-Path $Trabalho "cache"
$saida = Join-Path $Trabalho "saida"
$palco = Join-Path $Trabalho "palco"
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
New-Item -ItemType Directory -Force $cache, "$saida\win", $palco | Out-Null
# cache do uv junto do trabalho (o PyTorch com CUDA ocupa varios GB)
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $Trabalho "uv-cache" }
[Net.ServicePointManager]::SecurityProtocol = "Tls12"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$PG_VERSAO = "16.14-1"
$PY_VERSAO = "3.12"
$TORCH = @{
  cpu    = @{ pacotes = @("torch", "torchvision"); indice = "https://download.pytorch.org/whl/cpu"; onnx = "" }
  nvidia = @{ pacotes = @("torch", "torchvision"); indice = "https://download.pytorch.org/whl/cu126"; onnx = "onnxruntime-gpu==1.23.2" }
  dml    = @{ pacotes = @("torch-directml"); indice = ""; onnx = "onnxruntime-directml==1.24.4" }   # prende o torch 2.4.1 (DirectML)
}
# onnx: rostos (SCRFD + ArcFace) na placa. onnxruntime-gpu 1.23 = CUDA 12 + cuDNN 9, que vem com o torch cu126
# (a 1.24+ ja pede CUDA 13 e cairia para a CPU)
function Passo($t) { Write-Host "`n== $t" -ForegroundColor Yellow }
function Baixar($url, $dest) { if (-not (Test-Path $dest)) { Write-Host "   baixando $url"; curl.exe -L --fail --retry 3 -s -o $dest $url; if ($LASTEXITCODE) { throw "falha ao baixar $url" } } }
function Sha($f) { (Get-FileHash $f -Algorithm SHA256).Hash.ToLower() }

# ---------------------------------------------------------------- ferramentas
Passo "Ferramentas (uv, SDK do WebView2, PostgreSQL, cloudflared)"
$uv = Join-Path $cache "uv.exe"
if (-not (Test-Path $uv)) {
  Baixar "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" "$cache\uv.zip"
  tar.exe -xf "$cache\uv.zip" -C $cache uv.exe
}
$sdk = Join-Path $cache "webview2-sdk"
if (-not (Test-Path "$sdk\Microsoft.Web.WebView2.Core.dll")) {
  Baixar "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.4191.47" "$cache\wv2.zip"
  Expand-Archive "$cache\wv2.zip" "$cache\wv2pkg" -Force
  New-Item -ItemType Directory -Force $sdk | Out-Null
  Copy-Item "$cache\wv2pkg\lib\net462\Microsoft.Web.WebView2.Core.dll", "$cache\wv2pkg\lib\net462\Microsoft.Web.WebView2.WinForms.dll", "$cache\wv2pkg\runtimes\win-x64\native\WebView2Loader.dll" $sdk
}
$pgzip = Join-Path $cache "pgsql-$PG_VERSAO.zip"
Baixar "https://get.enterprisedb.com/postgresql/postgresql-$PG_VERSAO-windows-x64-binaries.zip" $pgzip
$cf = Join-Path $cache "cloudflared.exe"
Baixar "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $cf

# ---------------------------------------------------------------- programa (.exe)
Passo "Compilando o ArgosEPI.exe e o instalador"
& $uv run --no-project --python 3.12 --with pillow python "$PSScriptRoot\gerar_icone.py"
if (-not (Test-Path "$app\windows\argos.ico")) { throw "rode gerar_icone.py (precisa do Pillow)" }
$refs = @("/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll", "/r:System.Web.Extensions.dll")
$exeApp = Join-Path $cache "ArgosEPI.exe"
& $csc /nologo /target:winexe /platform:x64 /optimize+ "/out:$exeApp" "/win32manifest:$app\windows\programa.manifest" "/win32icon:$app\windows\argos.ico" @refs `
  "/r:$sdk\Microsoft.Web.WebView2.Core.dll" "/r:$sdk\Microsoft.Web.WebView2.WinForms.dll" "$app\windows\ArgosServidor.cs"
if ($LASTEXITCODE) { throw "falha ao compilar o ArgosEPI.exe" }
$setup = Join-Path $saida "ArgosEPI-Servidor-Setup.exe"
& $csc /nologo /target:winexe /platform:x64 /optimize+ "/out:$setup" "/win32manifest:$app\windows\instalador.manifest" "/win32icon:$app\windows\argos.ico" @refs `
  "/r:System.IO.Compression.dll" "/r:System.IO.Compression.FileSystem.dll" "/r:System.Management.dll" "$app\windows\Setup.cs"
if ($LASTEXITCODE) { throw "falha ao compilar o instalador" }
foreach ($n in "nvidia", "amd-intel", "cpu") { Copy-Item $setup (Join-Path $saida "ArgosEPI-Servidor-Setup-$n.exe") -Force }
if ($SoPrograma) { Write-Host "OK (so os .exe): $exeApp, $setup"; return }

# ---------------------------------------------------------------- pacote do programa
Passo "Montando o programa $Versao"
$p = Join-Path $palco "app"
if (Test-Path $p) { Remove-Item $p -Recurse -Force }
New-Item -ItemType Directory -Force $p | Out-Null
robocopy "$raiz\backend" "$p\backend" /e /xd __pycache__ hub /xf *.pyc /njh /njs /nfl /ndl | Out-Null
robocopy "$raiz\frontend" "$p\frontend" /e /xd api /njh /njs /nfl /ndl | Out-Null
robocopy "$raiz\scripts" "$p\scripts" /e /xd __pycache__ /xf *.pyc /njh /njs /nfl /ndl | Out-Null
robocopy "$app" "$p\servidor_app" supervisor.py /njh /njs /nfl /ndl | Out-Null
robocopy "$app\ui" "$p\servidor_app\ui" /e /njh /njs /nfl /ndl | Out-Null
New-Item -ItemType Directory -Force "$p\models", "$p\bin" | Out-Null
foreach ($m in "argos_epi_v1.pt", "argos_epi_v1.json", "yolo26n.pt", "yolo26s.pt", "yolo26n-pose.pt", "yolo26s-pose.pt", "yolo26m-pose.pt") {
  Copy-Item "$raiz\models\$m" "$p\models\" }
Copy-Item "$raiz\run.py", "$raiz\requirements.txt", "$raiz\.env.example" $p
Copy-Item $exeApp, "$sdk\*.dll" $p
Copy-Item "$app\windows\argos.ico" $p
Copy-Item $cf "$p\cloudflared.exe"
Copy-Item $uv "$p\bin\uv.exe"      # o botao "Instalar dependencias do TensorRT" usa
Set-Content -Encoding ascii "$p\VERSAO.txt" $Versao
@"
Argos EPI Servidor $Versao
Servidor de cameras com IA para EPIs (TCC SENAI Lauro de Freitas/BA).
Abra pelo atalho "Argos EPI Servidor". Os dados ficam na pasta dados (banco, rostos, gravacoes).
Para desinstalar: Configuracoes do Windows > Aplicativos > Argos EPI Servidor.
"@ | Set-Content -Encoding UTF8 "$p\LEIA-ME.txt"
# PostgreSQL portatil: so bin, lib e share (sem pgAdmin, docs, include)
tar.exe -xf $pgzip -C "$p\bin" pgsql/bin pgsql/lib pgsql/share
Get-ChildItem "$p\bin\pgsql\bin" -Include "pgAdmin*", "*.pdb", "stackbuilder*" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem "$p\bin\pgsql\lib" -Filter *.lib -Recurse | Remove-Item -Force
Remove-Item "$p\bin\pgsql\share\doc" -Recurse -Force -ErrorAction SilentlyContinue
$zipApp = Join-Path $saida "win\argos-app-$Versao.zip"
if (Test-Path $zipApp) { Remove-Item $zipApp }
[IO.Compression.ZipFile]::CreateFromDirectory($p, $zipApp, "Optimal", $false)
$descApp = (Get-ChildItem $p -Recurse -File | Measure-Object Length -Sum).Sum

# ---------------------------------------------------------------- Python por placa
$reqTexto = [IO.File]::ReadAllText("$raiz\requirements.txt")
$pythons = @{}
foreach ($placa in $Placas) {
  # o id muda quando muda o requirements.txt ou a receita da placa: so ai o Python e refeito
  $receita = $reqTexto + "|" + ($TORCH[$placa].pacotes -join ",") + "|" + $TORCH[$placa].indice + "|" + $TORCH[$placa].onnx
  $sha = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($receita))
  $id = "py312-$placa-" + (($sha[0..3] | ForEach-Object { $_.ToString("x2") }) -join "")
  $zipPy = Join-Path $saida "win\python-$placa-$id.zip"
  $info = "$zipPy.json"
  if ((Test-Path $zipPy) -and (Test-Path $info)) {
    Passo "Python $placa ja pronto ($id)"
    $pythons[$placa] = Get-Content $info -Raw | ConvertFrom-Json
    continue
  }
  Passo "Python $PY_VERSAO para $placa ($id)"
  $base = Join-Path $palco "py-$placa"
  if (Test-Path $base) { Remove-Item $base -Recurse -Force }
  New-Item -ItemType Directory -Force $base | Out-Null
  & $uv python install $PY_VERSAO --install-dir "$base\uvpython" --no-bin
  $dir = Get-ChildItem "$base\uvpython" -Directory | Where-Object { $_.Name -like "cpython-3.12*" -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Select-Object -First 1
  Move-Item $dir.FullName "$base\python"
  Remove-Item "$base\uvpython" -Recurse -Force
  Remove-Item "$base\python\Lib\EXTERNALLY-MANAGED" -ErrorAction SilentlyContinue
  $py = "$base\python\python.exe"
  $env:UV_LINK_MODE = "copy"; $env:UV_HTTP_TIMEOUT = "900"
  $t = $TORCH[$placa]
  if ($t.indice) { & $uv pip install --python $py @($t.pacotes) --index-url $t.indice }
  else { & $uv pip install --python $py @($t.pacotes) }
  if ($LASTEXITCODE) { throw "falha no PyTorch ($placa)" }
  # numpy<2 do requirements: o resto se encaixa no torch ja instalado
  & $uv pip install --python $py -r "$raiz\requirements.txt"
  if ($LASTEXITCODE) { throw "falha no requirements.txt ($placa)" }
  if ($t.onnx) {   # troca o onnxruntime comum pelo da placa
    & $uv pip uninstall --python $py onnxruntime
    & $uv pip install --python $py $t.onnx "numpy<2"
    if ($LASTEXITCODE) { throw "falha no $($t.onnx) ($placa)" }
  }
  & $py -c "import torch, ultralytics, cv2, flask, psycopg, onnxruntime, cryptography; print('ok', torch.__version__, onnxruntime.get_available_providers())"
  if ($LASTEXITCODE) { throw "o Python montado nao importa as bibliotecas ($placa)" }
  # runtime do Visual C++ junto do python.exe (instalacao local permitida pela Microsoft):
  # o PyTorch e o OpenCV nao abrem sem ele em PCs que nunca instalaram o Redistributable
  foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll") {
    if (Test-Path "$env:WINDIR\System32\$dll") { Copy-Item "$env:WINDIR\System32\$dll" "$base\python\" -Force } }
  # so serve para compilar extensoes em C++: nao vai para o usuario
  Get-ChildItem "$base\python\Lib\site-packages\torch\lib" -Filter *.lib -ErrorAction SilentlyContinue | Remove-Item -Force
  Remove-Item "$base\python\Lib\site-packages\torch\include" -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem "$base\python" -Directory -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
  Set-Content -Encoding ascii "$base\python\argos-variante.txt" $placa
  Set-Content -Encoding ascii "$base\python\argos-python-id.txt" $id
  Get-ChildItem (Join-Path $saida "win") -Filter "python-$placa-*.zip*" | Remove-Item -Force
  [IO.Compression.ZipFile]::CreateFromDirectory("$base\python", $zipPy, "Optimal", $true)
  $obj = [ordered]@{ id = $id; url = "win/python-$placa-$id.zip"; tamanho = (Get-Item $zipPy).Length; sha256 = (Sha $zipPy);
                     descompactado = (Get-ChildItem "$base\python" -Recurse -File | Measure-Object Length -Sum).Sum }
  $obj | ConvertTo-Json | Set-Content -Encoding UTF8 $info
  $pythons[$placa] = [pscustomobject]$obj
  Remove-Item $base -Recurse -Force
}

# ---------------------------------------------------------------- latest.json
Passo "latest.json"
$manifesto = Join-Path $saida "latest.json"
$atual = if (Test-Path $manifesto) { Get-Content $manifesto -Raw | ConvertFrom-Json } else { $null }
$py = [ordered]@{}
foreach ($k in "nvidia", "dml", "cpu") { if ($pythons.ContainsKey($k)) { $py[$k] = $pythons[$k] } elseif ($atual -and $atual.windows.python.$k) { $py[$k] = $atual.windows.python.$k } }
# pacotes do Linux: o empacotar_linux.sh grava saida\linux\linux.json
$linuxJson = Join-Path $saida "linux\linux.json"
$linux = if (Test-Path $linuxJson) { Get-Content $linuxJson -Raw | ConvertFrom-Json } elseif ($atual -and $atual.linux) { $atual.linux } else { [ordered]@{} }
$m = [ordered]@{
  versao = $Versao; data = (Get-Date -Format "yyyy-MM-dd"); notas = $Notas
  windows = [ordered]@{
    setup = [ordered]@{ url = "ArgosEPI-Servidor-Setup.exe"; tamanho = (Get-Item $setup).Length; sha256 = (Sha $setup) }
    app = [ordered]@{ url = "win/argos-app-$Versao.zip"; tamanho = (Get-Item $zipApp).Length; sha256 = (Sha $zipApp); descompactado = $descApp }
    python = $py
  }
  linux = $linux
}
[IO.File]::WriteAllText($manifesto, ($m | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
Get-ChildItem $saida -Recurse -File | Where-Object { $_.Extension -ne ".json" -or $_.Name -eq "latest.json" } |
  Select-Object @{ n = "Arquivo"; e = { $_.FullName.Substring($saida.Length + 1) } }, @{ n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
