# Gera a narracao (voz sintetizada) e junta ao video montado.
# Motor padrao: edge-tts (vozes neurais Microsoft pt-BR, precisa de internet).
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Voz pt-BR-FranciscaNeural
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Taxa "+8%"        (mais rapido)
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Motor piper       (voz local antiga)
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Motor xtts        (clone da sua voz)
#     -Motor xtts usa os trechos .wav de .setup\video\voz-referencia; na primeira vez constroi
#     a imagem revenda-voz (torch CPU + coqui-tts) e baixa o modelo XTTS-v2 (~1,8 GB).
#     Falas ja sintetizadas com o mesmo texto e a mesma voz sao reaproveitadas.
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Motor xtts -Amostra "Ola, eu sou o Caina."
#     (so gera .setup\video\voz\amostra.wav, para ouvir antes da narracao completa)
# Dublagem com a sua propria voz:
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Dublagem
#     gera em .setup\video\montagem: video-dublagem.mp4 (sem voz, ja com os cortes e as pausas
#     para cada fala), video-teleprompter.mp4 (o mesmo video com o texto na tela e contagem de
#     5 s) e roteiro-dublagem.md. -Ritmo 2.0 deixa mais tempo para cada fala (padrao 2.3 palavras/s).
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -Gravacao C:\caminho\minha-gravacao.m4a
#     transcreve a gravacao (faster-whisper), acha cada fala do roteiro, usa a ULTIMA tomada de
#     cada uma (pode repetir a fala ate sair certa) e encaixa cada fala no seu tempo do video;
#     gera video-final.mp4 e montagem\tomadas.txt (qual tomada foi usada em cada fala).
#     -Simples: so alinha o inicio da gravacao inteira (sem escolher tomadas);
#     -InicioGravacao 3.2 (com -Simples) informa a mao em que segundo a voz comeca.
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\narrar.ps1 -SomenteJuntar     (usa audios proprios em .setup\video\voz)
param(
    [ValidateSet("edge", "piper", "xtts")][string]$Motor = "edge",
    [string]$Voz = "",
    [string]$Taxa = "+0%",
    [double]$Velocidade = 0,
    [string]$Amostra = "",
    [switch]$Dublagem,
    [double]$Ritmo = 2.3,
    [string]$Gravacao = "",
    [double]$InicioGravacao = -1,
    [switch]$Simples,
    [switch]$SomenteJuntar
)
$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$kit = Join-Path $raiz "scripts\video"
$saida = Join-Path $raiz ".setup\video"
$imagem = "revenda-video:1.63.0"

if (-not $Amostra -and -not (Test-Path (Join-Path $saida "montagem\video-final-sem-voz.mp4"))) {
    Write-Host "ERRO: video montado nao encontrado; rode antes o gravar.ps1 -Montar."
    exit 1
}
$arg = "--motor $Motor --taxa='$Taxa'"
if ($Velocidade -gt 0) { $arg += " --velocidade $($Velocidade.ToString([Globalization.CultureInfo]::InvariantCulture))" }
if ($Voz) { $arg += " --voz $Voz" }
if ($SomenteJuntar) { $arg += " --somente-juntar" }
$inv = [Globalization.CultureInfo]::InvariantCulture
if ($Dublagem) {
    $Motor = "edge"
    $arg = "--dublagem $($Ritmo.ToString($inv))"
}
if ($Gravacao) {
    if (-not (Test-Path $Gravacao)) { Write-Host "ERRO: gravacao $Gravacao nao encontrada."; exit 1 }
    $ext = [IO.Path]::GetExtension($Gravacao).ToLowerInvariant()
    New-Item -ItemType Directory -Force -Path (Join-Path $saida "voz") | Out-Null
    Copy-Item -Force $Gravacao (Join-Path $saida "voz\gravacao$ext")
    $Motor = "edge"
    if ($Simples -or $InicioGravacao -ge 0) {
        $arg = "--gravacao /saida/voz/gravacao$ext"
        if ($InicioGravacao -ge 0) { $arg += " --inicio-gravacao $($InicioGravacao.ToString($inv))" }
    } else {
        $arg = "--tomadas /saida/voz/gravacao$ext"
    }
}
if ($Amostra) {
    # Texto da amostra num arquivo (evita problemas de aspas e acentos na linha de comando)
    New-Item -ItemType Directory -Force -Path (Join-Path $saida "voz") | Out-Null
    [IO.File]::WriteAllText((Join-Path $saida "voz\amostra.txt"), $Amostra, (New-Object System.Text.UTF8Encoding($false)))
    $arg += " --amostra /saida/voz/amostra.txt"
}
$pacote = "edge-tts"
if ($Motor -eq "piper") { $pacote = "piper-tts" }
$cmd = "pip install -q --root-user-action=ignore $pacote && python3 /video/narrar.py $arg"
if ($Dublagem -or $Gravacao) { $cmd = "python3 /video/narrar.py $arg" }
# Modelo do faster-whisper guardado em .setup\video\.cache-hf (baixa uma vez so)
if ($arg -like "--tomadas*") { $cmd = "pip install -q --root-user-action=ignore faster-whisper && HF_HOME=/saida/.cache-hf python3 /video/narrar.py $arg" }
if ($Motor -eq "xtts") {
    if (-not (Get-ChildItem (Join-Path $saida "voz-referencia") -Filter *.wav -ErrorAction SilentlyContinue)) {
        Write-Host "ERRO: nenhum trecho .wav em $(Join-Path $saida 'voz-referencia')."
        exit 1
    }
    docker image inspect revenda-voz:3 *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ">> construindo a imagem revenda-voz:3 (torch CPU + coqui-tts; alguns minutos, uma vez so)"
        docker build -t revenda-voz:3 -f (Join-Path $kit "Dockerfile.voz") $kit
        if ($LASTEXITCODE -ne 0) { Write-Host "ERRO: docker build da imagem de voz falhou"; exit 1 }
    }
    $imagem = "revenda-voz:3"
    $cmd = "python3 /video/narrar.py $arg"
}
Write-Host ">> narracao (motor $Motor) - alguns minutos"
docker run --rm --init `
    --mount "type=bind,source=$kit,target=/video,readonly" `
    --mount "type=bind,source=$saida,target=/saida" `
    --entrypoint bash $imagem -c $cmd
if ($LASTEXITCODE -ne 0) { Write-Host "ERRO: narracao falhou (codigo $LASTEXITCODE)"; exit 1 }
Write-Host ""
if ($Dublagem) {
    Write-Host "Para dublar: $(Join-Path $saida 'montagem\video-teleprompter.mp4')"
    Write-Host "Roteiro:     $(Join-Path $saida 'montagem\roteiro-dublagem.md')"
    exit 0
}
if ($Amostra) { Write-Host "Amostra: $(Join-Path $saida 'voz\amostra.wav')"; exit 0 }
Write-Host "Video final: $(Join-Path $saida 'montagem\video-final.mp4')"
Write-Host "Audios por bloco: $(Join-Path $saida 'voz')  (troque um blocoN.wav por outro audio e rode com -SomenteJuntar)"
