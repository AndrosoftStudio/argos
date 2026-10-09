# Gera o Argos EPI Servidor para Windows ja compilado (so .exe, .dll/.pyd e arquivos de modelo; nenhum
# codigo-fonte vai para a instalacao), pronto para subir ao R2:
#   <Saida>\ArgosEPI-Servidor-Setup.exe (+ copias -nvidia, -amd-intel, -cpu: o site baixa a da placa escolhida)
#   <Saida>\win\argos-programa-<versao>-<placa>.zip   janela (ArgosEPI.exe), codigo.pak (o Argos compilado) e recursos.pak
#   <Saida>\win\argos-nucleo-<placa>-<id>.zip         motor\ArgosMotor.exe (Python + bibliotecas em Python, sem o codigo do Argos)
#   <Saida>\win\argos-motor-<placa>-<id>.zip          bibliotecas do motor (.dll/.pyd: Python, PyTorch, OpenCV...)
#   <Saida>\win\argos-base-<id>.zip                   modelos (YOLO e rosto), PostgreSQL e cloudflared
#   <Saida>\win\argos-midia-<id>.zip                  midia\: video direto (MediaMTX) e voz dos avisos (Piper + voz em portugues)
#   <Saida>\win\argos-tensorrt-nvidia-<id>.zip        opcional (NVIDIA): DLLs do TensorRT, baixadas pelo botao do painel
#   <Saida>\latest.json                               o que o instalador e o programa leem
# O nucleo, o motor, a base e a midia so sao refeitos/baixados quando o conteudo muda (id):
# uma atualizacao comum do programa baixa so o argos-programa (~3 MB).
# Requisitos: Windows 10/11 (csc do .NET Framework, curl, tar) e internet na primeira vez.
# Uso: powershell -ExecutionPolicy Bypass -File empacotar_windows.ps1 -Versao 20.3.0 [-Placas cpu,nvidia,dml] [-SoJanela]
param(
  [string]$Versao = "20.4.2",
  [string[]]$Placas = @("cpu", "nvidia", "dml"),
  [string]$Trabalho = "D:\argos-build",
  [string]$Notas = "",
  [switch]$SoJanela
)
$ErrorActionPreference = "Stop"
# com -File a lista chega como um texto so ("cpu,dml,nvidia")
$Placas = @($Placas | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$app = Split-Path -Parent $PSScriptRoot             # servidor_app
$raiz = Split-Path -Parent $app                     # pasta do projeto (v20)
$cache = Join-Path $Trabalho "cache"
$saida = Join-Path $Trabalho "saida"
$palco = Join-Path $Trabalho "palco"
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
New-Item -ItemType Directory -Force $cache, "$saida\win", $palco | Out-Null
# cache do uv junto do trabalho (o PyTorch com CUDA ocupa varios GB)
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $Trabalho "uv-cache" }
$env:UV_LINK_MODE = "copy"; $env:UV_HTTP_TIMEOUT = "900"
[Net.ServicePointManager]::SecurityProtocol = "Tls12"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$PG_VERSAO = "16.14-1"
# os mesmos de backend/midia.py (la, quem roda do codigo baixa por conta propria)
$MEDIAMTX_VERSAO = "v1.21.2"
$PIPER_VERSAO = "2023.11.14-2"
$VOZ = "pt_BR-faber-medium"
$PY_VERSAO = "3.12"
$TORCH = @{
  cpu    = @{ pacotes = @("torch", "torchvision"); indice = "https://download.pytorch.org/whl/cpu"; onnx = ""; extras = @() }
  # onnx + tensorrt: o onnx vai no motor; as DLLs do TensorRT viram um pacote opcional (ver abaixo)
  nvidia = @{ pacotes = @("torch", "torchvision"); indice = "https://download.pytorch.org/whl/cu126"; onnx = "onnxruntime-gpu==1.23.2"
              extras = @("onnx>=1.12.0,<2.0.0", "tensorrt-cu12>=10.3,!=10.1.0,!=10.2.0,<11") }
  dml    = @{ pacotes = @("torch-directml"); indice = ""; onnx = "onnxruntime-directml==1.24.4"; extras = @() }   # prende o torch 2.4.1 (DirectML)
}
# onnx: rostos (SCRFD + ArcFace) na placa. onnxruntime-gpu 1.23 = CUDA 12 + cuDNN 9, que vem com o torch cu126
# (a 1.24+ ja pede CUDA 13 e cairia para a CPU)
$MODELOS = "argos_epi_v1.pt", "argos_epi_v1.json", "yolo26n.pt", "yolo26s.pt", "yolo26n-pose.pt", "yolo26s-pose.pt", "yolo26m-pose.pt"
$ROSTO = "det_10g.onnx", "w600k_r50.onnx"
# runtime do Visual C++ ao lado do motor (instalacao local permitida pela Microsoft): o PyTorch nao abre
# com o mais antigo que o PyInstaller pega de outras bibliotecas, nem em PCs sem o Redistributable
$VCRT = "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll"
function Passo($t) { Write-Host "`n== $t" -ForegroundColor Yellow }
function Baixar($url, $dest) { if (-not (Test-Path $dest)) { Write-Host "   baixando $url"; curl.exe -L --fail --retry 3 -s -o $dest $url; if ($LASTEXITCODE) { throw "falha ao baixar $url" } } }
function Sha($f) { (Get-FileHash $f -Algorithm SHA256).Hash.ToLower() }
function Tamanho($pasta) { (Get-ChildItem $pasta -Recurse -File | Measure-Object Length -Sum).Sum }
# id de uma pasta = hash do conteudo (caminho + SHA-256 de cada arquivo): muda so quando algum arquivo muda
function IdDaPasta($pasta, $fora = @()) {
  $linhas = Get-ChildItem $pasta -Recurse -File | Where-Object { $fora -notcontains $_.Name } | Sort-Object FullName | ForEach-Object {
    $_.FullName.Substring($pasta.Length + 1).ToLower() + ":" + (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
  $sha = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($linhas -join "`n"))
  (($sha[0..4] | ForEach-Object { $_.ToString("x2") }) -join "")
}
# conteudo de um .zip (nome + SHA-256 de cada arquivo, em ordem de nome): igual para zips com os mesmos arquivos
function ConteudoDoZip($caminho) {
  $z = [IO.Compression.ZipFile]::OpenRead($caminho)
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    ($z.Entries | Sort-Object FullName | ForEach-Object {
      $s = $_.Open(); try { $_.FullName + ":" + [BitConverter]::ToString($sha.ComputeHash($s)) } finally { $s.Dispose() } }) -join "`n"
  } finally { $z.Dispose() }
}
# O PyInstaller grava o base_library.zip numa ordem que muda a cada compilacao (mesmos arquivos, bytes
# diferentes), o que trocaria o id do motor sem nada ter mudado. Se o conteudo for o mesmo do pacote ja
# publicado, fica o arquivo de la: o id continua igual e quem atualiza nao baixa as bibliotecas de novo.
function ManterBaseLibrary($zipPublicado, $novo) {
  $antigo = "$novo.publicado"
  $z = [IO.Compression.ZipFile]::OpenRead($zipPublicado)
  try {
    $e = $z.Entries | Where-Object { ($_.FullName -replace '\\', '/') -eq "motor/base_library.zip" } | Select-Object -First 1
    if (-not $e) { return }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $antigo, $true)
  } finally { $z.Dispose() }
  if ((Sha $antigo) -ne (Sha $novo) -and (ConteudoDoZip $antigo) -eq (ConteudoDoZip $novo)) {
    Copy-Item $antigo $novo -Force
    Write-Host "   base_library.zip: mesmo conteudo do pacote publicado, mantido o de la"
  }
  Remove-Item $antigo -Force
}
function Zipar($pasta, $zip) {
  if (Test-Path $zip) { Remove-Item $zip }
  [IO.Compression.ZipFile]::CreateFromDirectory($pasta, $zip, "Optimal", $false)
}
function Descrever($zip, $url, $pasta, $id = $null) {
  $o = [ordered]@{}
  if ($id) { $o.id = $id }
  $o.url = $url; $o.tamanho = (Get-Item $zip).Length; $o.sha256 = (Sha $zip); $o.descompactado = (Tamanho $pasta)
  $o
}

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

# ---------------------------------------------------------------- janela e instalador (.exe)
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
if ($SoJanela) { Write-Host "OK (so os .exe): $exeApp, $setup"; return }

# ---------------------------------------------------------------- base: modelos, PostgreSQL, cloudflared
Passo "Base (modelos de visao computacional, PostgreSQL, cloudflared)"
$b = Join-Path $palco "base"
if (Test-Path $b) { Remove-Item $b -Recurse -Force }
$rostoDir = "$b\models\insightface\models\buffalo_l"
New-Item -ItemType Directory -Force "$b\models", "$b\bin", $rostoDir | Out-Null
foreach ($m in $MODELOS) { Copy-Item "$raiz\models\$m" "$b\models\" }
# modelos de rosto (SCRFD + ArcFace do InsightFace buffalo_l): vao na instalacao, nada e baixado depois
$rostoLocal = "$raiz\models\insightface\models\buffalo_l"
if (-not ($ROSTO | Where-Object { -not (Test-Path "$rostoLocal\$_") })) { foreach ($r in $ROSTO) { Copy-Item "$rostoLocal\$r" $rostoDir } }
else {
  Baixar "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip" "$cache\buffalo_l.zip"
  foreach ($r in $ROSTO) { tar.exe -xf "$cache\buffalo_l.zip" -C $rostoDir $r; if ($LASTEXITCODE) { throw "buffalo_l.zip sem $r" } }
}
# PostgreSQL portatil: so bin, lib e share (sem pgAdmin, docs, include)
tar.exe -xf $pgzip -C "$b\bin" pgsql/bin pgsql/lib pgsql/share
Get-ChildItem "$b\bin\pgsql\bin" -Include "pgAdmin*", "*.pdb", "stackbuilder*" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem "$b\bin\pgsql\lib" -Filter *.lib -Recurse | Remove-Item -Force
Remove-Item "$b\bin\pgsql\share\doc" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item $cf "$b\bin\cloudflared.exe"
$idBase = IdDaPasta $b
Set-Content -Encoding ascii "$b\bin\argos-base-id.txt" $idBase
$zipBase = Join-Path $saida "win\argos-base-$idBase.zip"
if (-not (Test-Path $zipBase)) {
  Get-ChildItem "$saida\win" -Filter "argos-base-*.zip" | Remove-Item -Force
  Zipar $b $zipBase
} else { Write-Host "   base $idBase ja pronta" }
$descBase = Descrever $zipBase "win/argos-base-$idBase.zip" $b $idBase

# ---------------------------------------------------------------- midia: video direto e voz dos avisos
Passo "Midia (MediaMTX para o video direto; Piper e a voz em portugues para os avisos da TV)"
$md = Join-Path $palco "midia"
if (Test-Path $md) { Remove-Item $md -Recurse -Force }
New-Item -ItemType Directory -Force "$md\midia\mediamtx", "$md\midia\voz" | Out-Null
$mtxZip = Join-Path $cache "mediamtx-$MEDIAMTX_VERSAO.zip"
Baixar "https://github.com/bluenviron/mediamtx/releases/download/$MEDIAMTX_VERSAO/mediamtx_${MEDIAMTX_VERSAO}_windows_amd64.zip" $mtxZip
tar.exe -xf $mtxZip -C "$md\midia\mediamtx" mediamtx.exe LICENSE
if ($LASTEXITCODE) { throw "o pacote do MediaMTX nao trouxe mediamtx.exe" }
$piperZip = Join-Path $cache "piper-$PIPER_VERSAO.zip"
Baixar "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSAO/piper_windows_amd64.zip" $piperZip
tar.exe -xf $piperZip -C "$md\midia"          # ja traz a pasta piper\
if ($LASTEXITCODE -or -not (Test-Path "$md\midia\piper\piper.exe")) { throw "o pacote do Piper nao trouxe piper\piper.exe" }
foreach ($f in "$VOZ.onnx", "$VOZ.onnx.json") {
  Baixar "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/$f" "$cache\$f"
  Copy-Item "$cache\$f" "$md\midia\voz\"
}
@"
Programas de terceiros nesta pasta (distribuidos sem alteracao):
  mediamtx\  MediaMTX $MEDIAMTX_VERSAO - licenca MIT - https://github.com/bluenviron/mediamtx
  piper\     Piper $PIPER_VERSAO - licenca MIT - https://github.com/rhasspy/piper
             (inclui o eSpeak NG, licenca GPL-3.0 - https://github.com/espeak-ng/espeak-ng)
  voz\       voz $VOZ do projeto Piper - https://huggingface.co/rhasspy/piper-voices
"@ | Set-Content -Encoding UTF8 "$md\midia\LICENCAS.txt"
# autoteste: a voz precisa falar (gera um .wav de verdade) e o MediaMTX precisa abrir
$wavTeste = Join-Path $cache "teste-voz.wav"
Remove-Item $wavTeste -ErrorAction SilentlyContinue
"Teste de voz do Argos." | & "$md\midia\piper\piper.exe" --model "$md\midia\voz\$VOZ.onnx" --output_file $wavTeste --quiet
if ($LASTEXITCODE -or -not (Test-Path $wavTeste) -or (Get-Item $wavTeste).Length -lt 10000) { throw "a voz (Piper) nao passou no autoteste" }
Remove-Item $wavTeste
& "$md\midia\mediamtx\mediamtx.exe" --version | Out-Null
if ($LASTEXITCODE) { throw "o MediaMTX nao abriu" }
$idMidia = IdDaPasta "$md\midia"
Set-Content -Encoding ascii "$md\midia\argos-midia-id.txt" $idMidia
$zipMidia = Join-Path $saida "win\argos-midia-$idMidia.zip"
if (-not (Test-Path $zipMidia)) {
  Get-ChildItem "$saida\win" -Filter "argos-midia-*.zip" | Remove-Item -Force
  Zipar $md $zipMidia
} else { Write-Host "   midia $idMidia ja pronta" }
$descMidia = Descrever $zipMidia "win/argos-midia-$idMidia.zip" $md $idMidia

# ---------------------------------------------------------------- motor e programa, por placa
$programas = @{}; $nucleos = @{}; $motores = @{}; $trts = @{}
foreach ($placa in $Placas) {
  $t = $TORCH[$placa]
  # --- Python de montagem (fica so na maquina de build; o usuario recebe o motor compilado)
  Passo "Python $PY_VERSAO de montagem para $placa"
  $base = Join-Path $palco "py-$placa"
  $py = "$base\python\python.exe"
  if (-not (Test-Path $py)) {
    if (Test-Path $base) { Remove-Item $base -Recurse -Force }
    New-Item -ItemType Directory -Force $base | Out-Null
    & $uv python install $PY_VERSAO --install-dir "$base\uvpython" --no-bin
    $dir = Get-ChildItem "$base\uvpython" -Directory | Where-Object { $_.Name -like "cpython-3.12*" -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Select-Object -First 1
    Move-Item $dir.FullName "$base\python"
    Remove-Item "$base\uvpython" -Recurse -Force
    Remove-Item "$base\python\Lib\EXTERNALLY-MANAGED" -ErrorAction SilentlyContinue
    if ($t.indice) { & $uv pip install --python $py @($t.pacotes) --index-url $t.indice }
    else { & $uv pip install --python $py @($t.pacotes) }
    if ($LASTEXITCODE) { throw "falha no PyTorch ($placa)" }
  }
  # numpy<2 do requirements: o resto se encaixa no torch ja instalado.
  # Com placa, o onnxruntime comum da lugar ao da placa (os dois pacotes ocupam a mesma pasta).
  $req = Join-Path $base "requirements.txt"
  [IO.File]::WriteAllLines($req, @(Get-Content "$raiz\requirements.txt" | Where-Object { -not ($t.onnx -and $_ -match '^\s*onnxruntime') }))
  & $uv pip install --python $py -r $req pyinstaller
  if ($LASTEXITCODE) { throw "falha no requirements.txt ($placa)" }
  if ($t.onnx) {
    & $uv pip install --python $py $t.onnx "numpy<2"
    if ($LASTEXITCODE) { throw "falha no $($t.onnx) ($placa)" }
  }
  if ($t.extras.Count) {
    # o tensorrt-cu12 monta as dependencias pelo pip (o uv nao serve aqui); numpy travado na versao instalada
    $trava = Join-Path $base "restricoes.txt"
    & $py -c "import numpy; print('numpy==' + numpy.__version__)" | Set-Content -Encoding ascii $trava
    & $py -m pip install --disable-pip-version-check -q -c $trava @($t.extras)
    if ($LASTEXITCODE) { throw "falha nos extras ($placa)" }
  }
  & $py -c "import torch, ultralytics, cv2, flask, psycopg, onnxruntime, cryptography; print('ok', torch.__version__, onnxruntime.get_available_providers())"
  if ($LASTEXITCODE) { throw "o Python de montagem nao importa as bibliotecas ($placa)" }

  # --- compila: motor\ArgosMotor.exe + bibliotecas (.dll/.pyd), sem nenhum .py
  Passo "Compilando o motor ($placa)"
  $pyi = Join-Path $palco "pyi-$placa"
  New-Item -ItemType Directory -Force $pyi | Out-Null
  $env:ARGOS_RAIZ = $raiz; $env:ARGOS_PLACA = $placa
  # o codigo do Argos fica fora do executavel (codigo.pak): ver motor.spec
  $env:ARGOS_CODIGO_FORA = "1"; $env:ARGOS_CODIGO_LISTA = "$pyi\codigo.json"; $env:ARGOS_NUCLEO_ID = "$pyi\nucleo-id.txt"
  $env:PYTHONHASHSEED = "1"                   # compilacao repetivel (recomendado pelo PyInstaller)
  & $py -m PyInstaller --noconfirm --log-level WARN --distpath "$pyi\dist" --workpath "$pyi\work" "$PSScriptRoot\motor.spec"
  $codigo = $LASTEXITCODE
  Remove-Item Env:\PYTHONHASHSEED, Env:\ARGOS_CODIGO_FORA, Env:\ARGOS_CODIGO_LISTA, Env:\ARGOS_NUCLEO_ID
  if ($codigo) { throw "falha ao compilar o motor ($placa)" }
  $motor = "$pyi\dist\motor"
  # o motor procura o codigo.pak na pasta acima da dele, como na instalacao
  $pak = "$pyi\dist\codigo.pak"
  & $py "$PSScriptRoot\montar_codigo.py" "$pyi\codigo.json" $pak
  if ($LASTEXITCODE) { throw "falha ao montar o codigo.pak ($placa)" }
  foreach ($dll in $VCRT) { if (Test-Path "$env:WINDIR\System32\$dll") { Copy-Item "$env:WINDIR\System32\$dll" $motor -Force } }
  # so serve para compilar extensoes em C++ (e scripts de exemplo das bibliotecas): nao vai para o usuario
  Get-ChildItem $motor -Recurse -Include *.lib, *.h, *.hpp, *.cmake, *.pyi, *.pdb, *.sh, *.js, *.html, *.ipynb | Remove-Item -Force -ErrorAction SilentlyContinue
  $fontes = @(Get-ChildItem $motor -Recurse -Include *.py, *.pyw)
  if ($fontes.Count) { throw "sobrou codigo-fonte no motor ($placa): " + (($fontes | Select-Object -First 5 | ForEach-Object { $_.FullName.Substring($motor.Length + 1) }) -join ", ") }
  Set-Content -Encoding ascii "$motor\argos-variante.txt" $placa
  $temTrt = Test-Path "$motor\tensorrt_libs"
  if ($temTrt) { Set-Content -Encoding ascii "$motor\argos-trt-id.txt" "montagem" }   # o conferir abre o TensorRT tambem
  # autoteste: abre PyTorch, OpenCV, Ultralytics e o backend inteiro, detecta num quadro e procura rostos
  $env:INSIGHTFACE_ROOT = "$b\models\insightface"
  & "$motor\ArgosMotor.exe" conferir --rosto --detectar "$b\models\yolo26n.pt"
  $codigo = $LASTEXITCODE
  Remove-Item Env:\INSIGHTFACE_ROOT
  if ($codigo) { throw "o motor compilado nao passou no autoteste ($placa)" }

  # --- TensorRT (so NVIDIA): 3 GB de DLLs que poucos usam. Saem do motor para um pacote opcional, que o
  #     botao "Instalar dependencias do TensorRT" do painel baixa ja compilado e extrai em motor\
  $pt = Join-Path $palco "trt-$placa"
  if (Test-Path $pt) { Remove-Item $pt -Recurse -Force }
  if ($temTrt) {
    New-Item -ItemType Directory -Force "$pt\motor" | Out-Null
    Remove-Item "$motor\argos-trt-id.txt"
    Move-Item "$motor\tensorrt_libs", "$motor\tensorrt_bindings" "$pt\motor\"
    Get-ChildItem $motor -Directory -Filter "tensorrt_cu12*.dist-info" | Move-Item -Destination "$pt\motor\"
    & "$motor\ArgosMotor.exe" conferir          # e continua abrindo sem ele
    if ($LASTEXITCODE) { throw "o motor nao abre sem o pacote do TensorRT ($placa)" }
  }

  # --- bibliotecas do motor (tudo menos o executavel): so mudam quando muda uma dependencia
  $publicado = Get-ChildItem "$saida\win" -Filter "argos-motor-$placa-*.zip" | Select-Object -First 1
  if ($publicado) { ManterBaseLibrary $publicado.FullName "$motor\base_library.zip" }
  $idMotor = IdDaPasta $motor @("ArgosMotor.exe", "argos-motor-id.txt")
  Set-Content -Encoding ascii "$motor\argos-motor-id.txt" $idMotor
  # --- nucleo (o executavel): o id vem do que foi compilado nele (motor.spec) e das bibliotecas com que foi testado
  $sha = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes((Get-Content "$pyi\nucleo-id.txt" -Raw).Trim() + ":" + $idMotor))
  $idNucleo = (($sha[0..4] | ForEach-Object { $_.ToString("x2") }) -join "")
  $zipNucleo = Join-Path $saida "win\argos-nucleo-$placa-$idNucleo.zip"
  $pn = Join-Path $palco "nucleo-$placa"
  if (Test-Path $pn) { Remove-Item $pn -Recurse -Force }
  New-Item -ItemType Directory -Force "$pn\motor" | Out-Null
  if (-not (Test-Path $zipNucleo)) {
    Passo "Empacotando o nucleo do motor ($placa, $idNucleo)"
    Get-ChildItem "$saida\win" -Filter "argos-nucleo-$placa-*.zip" | Remove-Item -Force
    Copy-Item "$motor\ArgosMotor.exe" "$pn\motor\"
    Set-Content -Encoding ascii "$pn\motor\argos-nucleo-id.txt" $idNucleo
    Zipar $pn $zipNucleo
  } else {
    # mesmo id: quem ja instalou fica com o executavel publicado. O codigo novo e testado com ele.
    Write-Host "   nucleo $placa ($idNucleo) ja pronto: conferindo o codigo novo com ele"
    [IO.Compression.ZipFile]::ExtractToDirectory($zipNucleo, $pn)
    Copy-Item "$pn\motor\ArgosMotor.exe" "$motor\ArgosMotor.exe" -Force
    $env:INSIGHTFACE_ROOT = "$b\models\insightface"
    & "$motor\ArgosMotor.exe" conferir --rosto --detectar "$b\models\yolo26n.pt"
    $codigo = $LASTEXITCODE
    Remove-Item Env:\INSIGHTFACE_ROOT
    if ($codigo) { throw "o codigo novo nao abre com o nucleo ja publicado ($placa)" }
  }
  $nucleos[$placa] = Descrever $zipNucleo "win/argos-nucleo-$placa-$idNucleo.zip" $pn $idNucleo
  Remove-Item $pn -Recurse -Force
  if ($temTrt) {
    $idTrt = IdDaPasta "$pt\motor"
    Set-Content -Encoding ascii "$pt\motor\argos-trt-id.txt" $idTrt
    $zipTrt = Join-Path $saida "win\argos-tensorrt-$placa-$idTrt.zip"
    if (-not (Test-Path $zipTrt)) {
      Passo "Empacotando o TensorRT opcional ($placa, $idTrt)"
      Get-ChildItem "$saida\win" -Filter "argos-tensorrt-$placa-*.zip" | Remove-Item -Force
      Zipar $pt $zipTrt
    } else { Write-Host "   pacote do TensorRT $placa ($idTrt) ja pronto" }
    $d = Descrever $zipTrt "win/argos-tensorrt-$placa-$idTrt.zip" $pt $idTrt
    $d.motor = $idMotor                         # so serve neste motor
    $trts[$placa] = $d
    Remove-Item $pt -Recurse -Force
  }
  $zipMotor = Join-Path $saida "win\argos-motor-$placa-$idMotor.zip"
  $pm = Join-Path $palco "motor-$placa"
  if (Test-Path $pm) { Remove-Item $pm -Recurse -Force }
  New-Item -ItemType Directory -Force $pm | Out-Null
  if (-not (Test-Path $zipMotor)) {
    Passo "Empacotando as bibliotecas do motor ($placa, $idMotor)"
    Get-ChildItem "$saida\win" -Filter "argos-motor-$placa-*.zip" | Remove-Item -Force
    robocopy $motor "$pm\motor" /e /xf ArgosMotor.exe /njh /njs /nfl /ndl | Out-Null
    Zipar $pm $zipMotor
    Remove-Item $pm -Recurse -Force
  } else { Write-Host "   bibliotecas do motor $placa ($idMotor) ja prontas" }
  $descMotor = [ordered]@{ id = $idMotor; url = "win/argos-motor-$placa-$idMotor.zip"; tamanho = (Get-Item $zipMotor).Length; sha256 = (Sha $zipMotor)
                           descompactado = (Tamanho $motor) - (Get-Item "$motor\ArgosMotor.exe").Length }
  $motores[$placa] = $descMotor

  # --- programa: janela, codigo do Argos (compilado) e recursos. E o que muda de uma versao para outra.
  Passo "Montando o programa $Versao ($placa)"
  $p = Join-Path $palco "programa-$placa"
  if (Test-Path $p) { Remove-Item $p -Recurse -Force }
  New-Item -ItemType Directory -Force $p | Out-Null
  Copy-Item $exeApp, "$sdk\*.dll" $p
  Copy-Item $pak $p
  & $py "$PSScriptRoot\montar_recursos.py" $raiz "$p\recursos.pak"
  if ($LASTEXITCODE) { throw "falha ao montar o recursos.pak" }
  Set-Content -Encoding ascii "$p\VERSAO.txt" $Versao
  @"
Argos EPI Servidor $Versao
Servidor de cameras com IA para EPIs (TCC SENAI Lauro de Freitas/BA).
Abra pelo atalho "Argos EPI Servidor". Os dados ficam na pasta dados (banco, rostos, gravacoes).
Para desinstalar: Configuracoes do Windows > Aplicativos > Argos EPI Servidor.
"@ | Set-Content -Encoding UTF8 "$p\LEIA-ME.txt"
  Get-ChildItem "$saida\win" -Filter "argos-programa-*-$placa.zip" | Remove-Item -Force
  $zipProg = Join-Path $saida "win\argos-programa-$Versao-$placa.zip"
  Zipar $p $zipProg
  $programas[$placa] = Descrever $zipProg "win/argos-programa-$Versao-$placa.zip" $p
}
# pacotes do formato antigo (Python solto + codigo): nao sao mais usados
Get-ChildItem "$saida\win" -Include "argos-app-*.zip", "python-*.zip", "python-*.zip.json" -Recurse | Remove-Item -Force

# ---------------------------------------------------------------- latest.json
Passo "latest.json"
$manifesto = Join-Path $saida "latest.json"
$atual = if (Test-Path $manifesto) { Get-Content $manifesto -Raw | ConvertFrom-Json } else { $null }
$prog = [ordered]@{}; $nuc = [ordered]@{}; $mot = [ordered]@{}; $trt = [ordered]@{}
foreach ($k in "nvidia", "dml", "cpu") {
  if ($programas.ContainsKey($k)) { $prog[$k] = $programas[$k]; $nuc[$k] = $nucleos[$k]; $mot[$k] = $motores[$k]; if ($trts.ContainsKey($k)) { $trt[$k] = $trts[$k] } }
  elseif ($atual -and $atual.versao -eq $Versao -and $atual.windows.programa2.$k) {
    $prog[$k] = $atual.windows.programa2.$k; $nuc[$k] = $atual.windows.nucleo.$k; $mot[$k] = $atual.windows.motor.$k
    if ($atual.windows.extras.tensorrt.$k) { $trt[$k] = $atual.windows.extras.tensorrt.$k }
  }
}
# pacotes do Linux: o empacotar_linux.sh grava saida\linux\linux.json
$linuxJson = Join-Path $saida "linux\linux.json"
$linux = if (Test-Path $linuxJson) { Get-Content $linuxJson -Raw | ConvertFrom-Json } elseif ($atual -and $atual.linux) { $atual.linux } else { [ordered]@{} }
$m = [ordered]@{
  versao = $Versao; data = (Get-Date -Format "yyyy-MM-dd"); notas = $Notas
  windows = [ordered]@{
    setup = [ordered]@{ url = "ArgosEPI-Servidor-Setup.exe"; tamanho = (Get-Item $setup).Length; sha256 = (Sha $setup) }
    base = $descBase
    midia = $descMidia                          # video direto e voz dos avisos
    programa2 = $prog                           # chave nova: instalador antigo nao instala o formato novo pela metade
    nucleo = $nuc
    motor = $mot
    extras = [ordered]@{ tensorrt = $trt }      # opcional: baixado pelo botao do painel (so NVIDIA)
  }
  linux = $linux
}
[IO.File]::WriteAllText($manifesto, ($m | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
Get-ChildItem $saida -Recurse -File | Where-Object { $_.Extension -ne ".json" -or $_.Name -eq "latest.json" } |
  Select-Object @{ n = "Arquivo"; e = { $_.FullName.Substring($saida.Length + 1) } }, @{ n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
