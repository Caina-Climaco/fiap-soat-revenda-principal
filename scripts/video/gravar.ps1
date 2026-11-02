# Grava automaticamente o video de entrega da Fase 4 (docs/11-roteiro-video.md), no mesmo
# formato da Fase 3. Detalhes, pre-requisitos e limitacoes: scripts/video/README.md.
#
# Uso (PowerShell normal, na raiz do repositorio principal, com o ambiente kind no ar e os
# dois CDs verdes):
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\gravar.ps1 -Montar
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\gravar.ps1 -Blocos 6,7 -Montar
#   powershell -ExecutionPolicy Bypass -File .\scripts\video\gravar.ps1 -Blocos 0 -Montar   (so monta)
#
# Etapas:
#   (a) pre-checagens: docker, gh, kubectl, cluster e rede kind, runner, Kong, Jaeger e nenhum
#       CD em andamento
#   (b) coleta no Windows (arquivos, curl pelo Kong e gh): blocos 3, 4 e 5
#   (c) coleta pelo runner (kubectl, terraform, docker): bloco 3
#   (d) limpeza (blocos 6 e 7): limpar-dados.ps1 -Tudo -Sim, para a vitrine comecar vazia
#   (e) navegador, passada 1: blocos 1 a 6 e 8 (GitHub, SonarCloud, Swagger e Jaeger)
#   (f) coleta pos-uso pelo runner: os dois bancos (bloco 6)
#   (g) falha do principal (bloco 7): derruba o principal, navegador (passada 2), restaura
#       SEMPRE, executa a recuperacao de sagas e grava Grafana e Prometheus (passada 3)
#   (h) clipes de terminal
#   -Montar: monta o video (cartelas, clipes, .srt) e gera o video final legendado
#       (.setup\video\montagem\video-final-legendado.mp4), como na Fase 3
# Saidas em .setup\video\ (fora do Git): clipes\*.webm, terminal\*.txt, falhas\*.png,
# manifesto.json, dados.json, montagem\ e o log gravar.log.
# Segredos: lidos dos Secrets do cluster pelo runner, guardados so em variaveis de ambiente
# deste processo e passados ao container por "-e NOME" (nunca na linha de comando nem no log).
# Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [string]$Blocos = "1,2,3,4,5,6,7,8",
    [object]$LimparDados = $true,
    [string]$Repositorio = "Caina-Climaco/fiap-soat-revenda-principal",
    [int]$PR = 10,
    [string]$RepositorioVendas = "Caina-Climaco/fiap-soat-revenda-vendas",
    [int]$PRVendas = 8,
    [string]$Clipes = "",
    [switch]$Montar,
    [double]$Ritmo = 2.3,
    [switch]$ReconstruirImagem,
    [string]$VersaoPlaywright = "1.63.0",
    [string]$Runner = "revenda-runner-principal"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
# gh e docker escrevem UTF-8; sem isto o PS 5.1 corrompe acentos ao capturar a saida.
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$FormatEnumerationLimit = 30

$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $raiz
$dirVideo = Join-Path $raiz "scripts\video"
$saida = Join-Path $raiz ".setup\video"
$dirTerminal = Join-Path $saida "terminal"
foreach ($d in @($saida, $dirTerminal, (Join-Path $saida "clipes"), (Join-Path $saida "falhas"))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
$log = Join-Path $saida "gravar.log"
Start-Transcript -Path $log -Force | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$imagem = "revenda-video:$VersaoPlaywright"
$avisos = New-Object System.Collections.Generic.List[string]

# ------------------------------------------------------------------ utilitarios (Fase 3)
function Etapa([string]$texto) {
    Write-Host ""
    Write-Host "==== $texto"
}

function Aviso([string]$texto) {
    Write-Host "AVISO: $texto"
    $avisos.Add($texto)
}

function LimparSegredos {
    foreach ($n in @("GESTOR_APIKEY", "GATEWAY_APIKEY", "WEBHOOK_SECRET")) {
        Remove-Item "Env:\$n" -ErrorAction SilentlyContinue
    }
}

function Falhar([string]$motivo) {
    LimparSegredos
    Write-Host ""
    Write-Host "ERRO: $motivo"
    Write-Host "Log: $log"
    Stop-Transcript | Out-Null
    exit 1
}

# Executa um comando nativo mostrando stdout e stderr (inclusive no transcript) e devolve o codigo.
function Invocar {
    param([string]$Exe, [string[]]$Argumentos)
    Write-Host ">> $Exe $($Argumentos -join ' ')"
    $global:LASTEXITCODE = 0
    & $Exe @Argumentos 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    } | Out-Host
    return $LASTEXITCODE
}

# Executa e devolve so o stdout (linhas). O stderr vai para a tela/log. Codigo em $script:codigo.
function Capturar {
    param([string]$Exe, [string[]]$Argumentos, [switch]$Silencioso)
    $global:LASTEXITCODE = 0
    $bruto = @(& $Exe @Argumentos 2>&1)
    $script:codigo = $LASTEXITCODE
    $linhas = New-Object System.Collections.Generic.List[string]
    foreach ($item in $bruto) {
        if ($item -is [System.Management.Automation.ErrorRecord]) {
            if (-not $Silencioso) { Write-Host "   $($item.Exception.Message)" }
        } else {
            $linhas.Add("$item")
        }
    }
    return , $linhas.ToArray()
}

function GhJson {
    param([string[]]$Argumentos)
    $linhas = Capturar "gh" $Argumentos
    if ($script:codigo -ne 0) {
        Write-Host "   gh $($Argumentos -join ' ') falhou (codigo $script:codigo)"
        return $null
    }
    $texto = ($linhas -join "`n").Trim()
    if (-not $texto) { return $null }
    try { return (ConvertFrom-Json -InputObject $texto) } catch {
        Write-Host "   JSON invalido de gh $($Argumentos -join ' ')"
        return $null
    }
}

# Grava a saida real (.txt) e o comando exibido no video (.cmd) em .setup\video\terminal.
function SalvarSaida([string]$nome, [string[]]$linhas, [string]$comando) {
    $txt = Join-Path $dirTerminal "$nome.txt"
    $cmd = Join-Path $dirTerminal "$nome.cmd"
    $limpas = @($linhas | ForEach-Object { ($_ -replace "\x1b\[[0-9;?]*[A-Za-z]", "").TrimEnd() })
    while ($limpas.Count -gt 0 -and -not $limpas[$limpas.Count - 1]) { $limpas = @($limpas[0..($limpas.Count - 2)]) }
    while ($limpas.Count -gt 0 -and -not $limpas[0]) { $limpas = @($limpas | Select-Object -Skip 1) }
    if ($limpas.Count -eq 0) {
        Aviso "saida vazia para '$nome': clipe sera pulado"
        RemoverSaida $nome
        return
    }
    [IO.File]::WriteAllText($txt, (($limpas -join "`n") + "`n"), $utf8)
    [IO.File]::WriteAllText($cmd, ($comando + "`n"), $utf8)
    Write-Host "   salvo terminal\$nome.txt ($($limpas.Count) linhas)"
}

function RemoverSaida([string]$nome) {
    foreach ($ext in @("txt", "cmd", "erro")) {
        Remove-Item (Join-Path $dirTerminal "$nome.$ext") -ErrorAction SilentlyContinue
    }
}

function Tabela($objetos) {
    $texto = ($objetos | Format-Table -AutoSize | Out-String -Width 220).Trim("`r", "`n")
    return @($texto -split "`r?`n")
}

function Lista($objeto) {
    $texto = ($objeto | Format-List | Out-String -Width 220).Trim("`r", "`n")
    return @($texto -split "`r?`n")
}

# Linhas do "gh run view --log": "job<TAB>passo<TAB>timestamp texto"
function LinhasDeLog([string[]]$bruto) {
    $res = New-Object System.Collections.Generic.List[object]
    foreach ($l in $bruto) {
        $partes = $l -split "`t", 3
        if ($partes.Count -lt 3) { continue }
        $texto = $partes[2] -replace "^\uFEFF?\d{4}-\d\d-\d\dT[\d:.]+Z ?", ""
        $res.Add([pscustomobject]@{ Passo = $partes[1]; Texto = $texto })
    }
    return , $res.ToArray()
}

function FiltrarLog($linhas, [string]$filtro, [int]$maximo) {
    $saidaLog = New-Object System.Collections.Generic.List[string]
    $passoAtual = ""
    foreach ($l in $linhas) {
        if ($l.Texto -match "^##\[(end)?group\]") { continue }
        if ($l.Texto -notmatch $filtro) { continue }
        if ($l.Passo -ne $passoAtual -and $l.Passo -and $l.Passo -notmatch "UNKNOWN") {
            if ($saidaLog.Count -gt 0) { $saidaLog.Add("") }
            $saidaLog.Add("== $($l.Passo)")
            $passoAtual = $l.Passo
        }
        $saidaLog.Add($l.Texto)
    }
    $resultado = $saidaLog.ToArray()
    if ($resultado.Count -gt $maximo) {
        Write-Host "   log filtrado com $($resultado.Count) linhas; mantidas as ultimas $maximo"
        $resultado = @($resultado | Select-Object -Last $maximo)
    }
    return , $resultado
}

function JobDoRun([string]$repo, [string]$runId, [string]$nomeJob) {
    $run = GhJson @("run", "view", $runId, "-R", $repo, "--json", "jobs")
    if (-not $run) { return $null }
    return (@($run.jobs) | Where-Object { $_.name -eq $nomeJob } | Select-Object -First 1)
}

function SegredoRunner([string]$ns, [string]$nome, [string]$chave) {
    $linhas = Capturar "docker" @("exec", $Runner, "kubectl", "-n", $ns, "get", "secret", $nome, "-o", "jsonpath={.data.$chave}") -Silencioso
    $b64 = ($linhas -join "").Trim()
    if ($script:codigo -ne 0 -or -not $b64) { return "" }
    try { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) } catch { return "" }
}

function CopiarColetor {
    if ($script:coletorCopiado) { return }
    $origem = Join-Path $dirVideo "coletar-runner.sh"
    if ((Invocar "docker" @("cp", $origem, "${Runner}:/tmp/video-coletar.sh")) -ne 0) { Falhar "docker cp do coletor para o runner falhou." }
    $script:coletorCopiado = $true
}

function ColetaRunner([string]$modo, [string[]]$nomes) {
    CopiarColetor
    foreach ($n in $nomes) { RemoverSaida $n }
    $codigoColeta = Invocar "docker" @("exec", $Runner, "bash", "/tmp/video-coletar.sh", $modo, "/tmp/video-coleta-$modo")
    if ($codigoColeta -ne 0) { Aviso "coleta '$modo' no runner terminou com codigo $codigoColeta" }
    $null = Invocar "docker" @("cp", "${Runner}:/tmp/video-coleta-$modo/.", $dirTerminal)
    foreach ($n in $nomes) {
        if (-not (Test-Path (Join-Path $dirTerminal "$n.txt"))) { Aviso "sem saida '$n' (clipe sera pulado)" }
    }
}

function ExecutarGravacao([string]$modo, [string[]]$extras, [bool]$comProxy) {
    $argumentos = @(
        "run", "--rm", "--init",
        "--network", "kind",
        "--shm-size=1g",
        "--mount", "type=bind,source=$dirVideo,target=/video,readonly",
        "--mount", "type=bind,source=$saida,target=/saida",
        "-e", "VIDEO_REPO", "-e", "VIDEO_PR", "-e", "VIDEO_CD_RUN_ID", "-e", "VIDEO_BLOCOS",
        "-e", "VIDEO_REPO_VENDAS", "-e", "VIDEO_PR_VENDAS", "-e", "VIDEO_CD_RUN_ID_VENDAS",
        "-e", "VIDEO_SAIDA=/saida"
    )
    if ($modo -eq "navegador") { $argumentos += @("-e", "GESTOR_APIKEY", "-e", "GATEWAY_APIKEY", "-e", "WEBHOOK_SECRET") }
    if (-not $comProxy) { $argumentos += @("-e", "VIDEO_SEM_PROXY=1") }
    $argumentos += @("--entrypoint", "bash", $imagem, "/video/entrypoint.sh", "--modo", $modo) + $extras
    return (Invocar "docker" $argumentos)
}

# Ultima execucao verde de um workflow na main e o log filtrado de um job dela
function UltimoRunVerde([string]$repo, [string]$workflow) {
    $runs = GhJson @("run", "list", "-R", $repo, "--workflow", $workflow, "--branch", "main", "--status", "success", "--limit", "1", "--json", "databaseId")
    if ($runs -and @($runs).Count -gt 0) { return "$(@($runs)[0].databaseId)" }
    Aviso "nenhuma execucao verde de $workflow na main de $repo"
    return ""
}

function SalvarLogDoJob([string]$nome, [string]$repo, [string]$runId, [string]$job, [string]$filtro, [int]$maximo, [string]$descricao) {
    RemoverSaida $nome
    if (-not $runId) { return }
    $j = JobDoRun $repo $runId $job
    if (-not $j) { Aviso "job '$job' nao encontrado no run $runId de $repo"; return }
    $bruto = Capturar "gh" @("run", "view", $runId, "-R", $repo, "--job", "$($j.databaseId)", "--log")
    $linhas = FiltrarLog (LinhasDeLog $bruto) $filtro $maximo
    $comando = "# `$filtro: $descricao`ngh run view $runId -R $repo --job $($j.databaseId) --log | Select-String `$filtro"
    SalvarSaida $nome $linhas $comando
}

# ------------------------------------------------------------------ parametros
$lista = @($Blocos -split "[,; ]+" | Where-Object { $_ -match "^\d+$" } | ForEach-Object { [int]$_ } | Sort-Object -Unique)
if ($lista.Count -eq 0) { Falhar "-Blocos invalido: '$Blocos' (use, por exemplo, 6 ou 6,7)." }
function Tem([int[]]$quais) { foreach ($q in $quais) { if ($lista -contains $q) { return $true } }; return $false }
$limpar = $true
if ($LimparDados -is [bool]) { $limpar = $LimparDados }
elseif ("$LimparDados" -match "^(0|false|\`$false|nao|no|off)$") { $limpar = $false }
$script:coletorCopiado = $false
$script:codigo = 0

Write-Host "gravar.ps1 - $(Get-Date -Format s)"
Write-Host "Blocos: $($lista -join ',') | LimparDados: $limpar | PRs: #$PR e #$PRVendas | Imagem: $imagem"
if ((Tem @(6)) -xor (Tem @(7))) {
    Aviso "o bloco 7 compra o Corolla cadastrado no bloco 6: grave os dois juntos (-Blocos 6,7)."
}
$soMontar = -not (Tem @(1, 2, 3, 4, 5, 6, 7, 8))
if ($soMontar -and -not $Montar) { Falhar "nenhum bloco entre 1 e 8 em -Blocos '$Blocos'." }

# ------------------------------------------------------------------ (a) pre-checagens
Etapa "(a) Pre-checagens"
if ($soMontar) { Write-Host "   so montagem (-Blocos 0 -Montar): cluster e runner nao sao necessarios" }
else {
    foreach ($f in @("docker", "gh", "kubectl", "curl.exe")) {
        if (-not (Get-Command $f -ErrorAction SilentlyContinue)) { Falhar "'$f' nao encontrado no PATH." }
    }
    if ((Invocar "docker" @("version", "--format", "docker {{.Server.Version}}")) -ne 0) { Falhar "Docker Desktop nao responde." }
    if ((Invocar "gh" @("auth", "status")) -ne 0) { Falhar "gh nao autenticado (gh auth login)." }
    $estado = (Capturar "docker" @("inspect", "-f", "{{.State.Running}}", "revenda-control-plane") -Silencioso) -join ""
    if ($estado -ne "true") { Falhar "container revenda-control-plane (cluster kind 'revenda') nao esta rodando." }
    $null = Capturar "docker" @("network", "inspect", "kind") -Silencioso
    if ($script:codigo -ne 0) { Falhar "rede docker 'kind' nao encontrada." }
    $null = Capturar "docker" @("network", "inspect", "revenda-cd-em-andamento") -Silencioso
    if ($script:codigo -eq 0) { Falhar "ha um CD em andamento (rede revenda-cd-em-andamento): espere terminar." }
    $estado = (Capturar "docker" @("inspect", "-f", "{{.State.Running}}", $Runner) -Silencioso) -join ""
    if ($estado -ne "true") { Falhar "container $Runner parado (docker start $Runner)." }
    if ((Invocar "docker" @("exec", $Runner, "kind", "export", "kubeconfig", "--internal", "--name", "revenda")) -ne 0) {
        Falhar "o runner nao conseguiu exportar o kubeconfig interno do kind."
    }
    if ((Invocar "curl.exe" @("-fsS", "--max-time", "10", "-o", "NUL", "-w", "kong vitrine %{http_code}\n", "http://localhost:8080/api/v1/veiculos/a-venda")) -ne 0) {
        Falhar "o Kong nao respondeu a vitrine em http://localhost:8080 (os dois CDs implantaram?)."
    }
    if ((Invocar "curl.exe" @("-fsS", "--max-time", "10", "-o", "NUL", "-w", "jaeger %{http_code}\n", "http://localhost:16686/")) -ne 0) {
        Falhar "o Jaeger nao respondeu em http://localhost:16686."
    }
}

$env:VIDEO_REPO = $Repositorio
$env:VIDEO_PR = "$PR"
$env:VIDEO_REPO_VENDAS = $RepositorioVendas
$env:VIDEO_PR_VENDAS = "$PRVendas"
$env:VIDEO_CD_RUN_ID = ""
$env:VIDEO_CD_RUN_ID_VENDAS = ""
$env:VIDEO_BLOCOS = ($lista -join ",")
$repos = @($Repositorio, $RepositorioVendas)

# ------------------------------------------------------------------ (b) coleta no Windows
if (Tem @(3, 4, 5)) { Etapa "(b) Coleta no Windows (arquivos, Kong e gh)" }
if (Tem @(3)) {
    SalvarSaida "cluster-yaml" @(Get-Content -Path (Join-Path $raiz "infra\kind\cluster.yaml") -Encoding UTF8) "Get-Content infra\kind\cluster.yaml"
    $kong = @(Get-Content -Path (Join-Path $raiz "infra\kong\kong.yml.tftpl") -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(#|$)' })
    SalvarSaida "kong-yml" $kong "Get-Content infra\kong\kong.yml.tftpl | Select-String -NotMatch '^\s*(#|`$)'"
    # Tres chamadas reais pela porta 8080 (o Kong); so a primeira linha e os cabecalhos uteis
    $chamadas = @(
        @{ args = @("-si", "--max-time", "10", "http://localhost:8080/api/v1/veiculos/a-venda?limite=1")
           exibido = 'curl.exe -si "http://localhost:8080/api/v1/veiculos/a-venda?limite=1"' },
        @{ args = @("-si", "--max-time", "10", "-X", "POST", "-H", "Content-Type: application/json", "-d", "{}", "http://localhost:8080/api/v1/veiculos")
           exibido = 'curl.exe -si -X POST -d "{}" http://localhost:8080/api/v1/veiculos' },
        @{ args = @("-si", "--max-time", "10", "http://localhost:8080/internal/v1/cobrancas")
           exibido = 'curl.exe -si http://localhost:8080/internal/v1/cobrancas' }
    )
    $linhasBorda = @()
    $comandosBorda = @()
    foreach ($ch in $chamadas) {
        $r = Capturar "curl.exe" $ch.args
        $uteis = @($r | Where-Object { $_ -match '^(HTTP/|RateLimit-|X-RateLimit|X-Request-ID|X-Kong|WWW-Authenticate|\{)' })
        if ($linhasBorda.Count -gt 0) { $linhasBorda += "" }
        $linhasBorda += @("> $($ch.exibido)") + $uteis
        $comandosBorda += $ch.exibido
    }
    SalvarSaida "kong-borda" $linhasBorda ($comandosBorda -join "`n")
}
if (Tem @(4)) {
    $filtroCi = "(^Name +Stmts|^TOTAL|Required test coverage|Total coverage|^=+ .*(passed|failed|error).* =+$)"
    SalvarLogDoJob "gh-ci-principal" $Repositorio (UltimoRunVerde $Repositorio "ci.yml") "testes" $filtroCi 40 "cobertura (TOTAL) e contagem de testes"
    SalvarLogDoJob "gh-ci-vendas" $RepositorioVendas (UltimoRunVerde $RepositorioVendas "ci.yml") "testes" $filtroCi 40 "cobertura (TOTAL) e contagem de testes"
}
if (Tem @(5)) {
    $linhasPrs = @()
    $comandosPrs = @()
    foreach ($repo in $repos) {
        $prs = GhJson @("pr", "list", "-R", $repo, "--state", "merged", "--limit", "20", "--json", "number,title,mergedAt")
        if ($prs) {
            $t = @($prs | ForEach-Object { [pscustomobject]@{ number = $_.number; title = $_.title; mergedAt = $_.mergedAt } })
            if ($linhasPrs.Count -gt 0) { $linhasPrs += "" }
            $linhasPrs += @("== $repo") + (Tabela $t)
            $comandosPrs += "gh pr list -R $repo --state merged --json number,title,mergedAt | ConvertFrom-Json | Format-Table"
        }
    }
    if ($linhasPrs.Count -gt 0) { SalvarSaida "gh-prs" $linhasPrs ($comandosPrs -join "`n") } else { RemoverSaida "gh-prs" }

    # Expressao --jq sem espacos nem aspas (o PS 5.1 corrompe aspas em argumentos nativos)
    $expr = "{checks_obrigatorios:.required_status_checks.contexts,branch_atualizada:.required_status_checks.strict,pr_obrigatorio:(.required_pull_request_reviews!=null),vale_para_admins:.enforce_admins.enabled,historico_linear:.required_linear_history.enabled,force_push:.allow_force_pushes.enabled,exclusao:.allow_deletions.enabled}"
    $linhasProt = @()
    foreach ($repo in $repos) {
        $prot = GhJson @("api", "repos/$repo/branches/main/protection", "--jq", $expr)
        if ($prot) {
            if ($linhasProt.Count -gt 0) { $linhasProt += "" }
            $linhasProt += @("== $repo") + (Lista $prot)
        } else { Aviso "protecao da main de $repo indisponivel (gh precisa de acesso de admin)" }
    }
    if ($linhasProt.Count -gt 0) {
        SalvarSaida "gh-protecao" $linhasProt "foreach (`$r in '$Repositorio','$RepositorioVendas') { gh api repos/`$r/branches/main/protection --jq '<checks, PR, admins, historico linear...>' }"
    } else { RemoverSaida "gh-protecao" }

    $linhasRunners = @()
    $comandosRunners = @()
    foreach ($repo in $repos) {
        $json = GhJson @("api", "repos/$repo/actions/runners")
        if ($json) {
            $runners = @($json.runners | ForEach-Object {
                [pscustomobject]@{ name = $_.name; status = $_.status; busy = $_.busy; labels = ((@($_.labels | ForEach-Object { $_.name })) -join ", ") }
            })
            if ($linhasRunners.Count -gt 0) { $linhasRunners += "" }
            $linhasRunners += @("== $repo") + (Tabela $runners)
            $comandosRunners += "gh api repos/$repo/actions/runners | ConvertFrom-Json | Select-Object -ExpandProperty runners | Format-Table name, status, busy, labels"
        } else { Aviso "lista de runners de $repo indisponivel" }
    }
    if ($linhasRunners.Count -gt 0) { SalvarSaida "gh-runners" $linhasRunners ($comandosRunners -join "`n") } else { RemoverSaida "gh-runners" }

    $env:VIDEO_CD_RUN_ID = UltimoRunVerde $Repositorio "cd.yml"
    $env:VIDEO_CD_RUN_ID_VENDAS = UltimoRunVerde $RepositorioVendas "cd.yml"
    Write-Host "   CDs no video: principal '$($env:VIDEO_CD_RUN_ID)' | venda '$($env:VIDEO_CD_RUN_ID_VENDAS)'"
    $filtroCd = "(Apply complete|naming to|successfully rolled out|^OK: |PASSED|FAILED|^=+ .*(passed|failed|error).* =+$)"
    SalvarLogDoJob "gh-cd-log-principal" $Repositorio $env:VIDEO_CD_RUN_ID "deploy" $filtroCd 70 "Terraform, imagem, rollout, verificacoes e e2e (PASSED e resumo)"
    $filtroCdV = "(Apply complete|naming to|successfully rolled out|^OK: |PASSED|FAILED|^=+ .*(passed|failed|error).* =+$|p\(95\)|http_req_failed|checks_succeeded)"
    SalvarLogDoJob "gh-cd-log-vendas" $RepositorioVendas $env:VIDEO_CD_RUN_ID_VENDAS "deploy" $filtroCdV 70 "e2e (inclusive a falha do principal) e carga k6"
}

# ------------------------------------------------------------------ (c) coleta pelo runner
if (Tem @(3)) {
    Etapa "(c) Coleta pelo runner (kubectl, terraform, docker)"
    ColetaRunner "pre" @("kubectl-pods", "kubectl-principal", "kubectl-vendas", "kubectl-netpol", "terraform-states", "docker-ps")
}

# ------------------------------------------------------------------ (d) limpar dados
if ((Tem @(6, 7)) -and $limpar) {
    Etapa "(d) Limpando os dados de negocio (vitrine vazia)"
    $limpeza = Join-Path $raiz "scripts\windows\limpar-dados.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $limpeza -Tudo -Sim
    if ($LASTEXITCODE -ne 0) { Falhar "limpar-dados.ps1 falhou (veja acima)." }
}

# ------------------------------------------------------------------ imagem de gravacao
$null = Capturar "docker" @("image", "inspect", $imagem) -Silencioso
if ($script:codigo -ne 0 -or $ReconstruirImagem) {
    Etapa "Construindo a imagem $imagem (primeira vez: alguns minutos)"
    if ((Invocar "docker" @("build", "--pull", "--build-arg", "PLAYWRIGHT_VERSION=$VersaoPlaywright", "-t", $imagem, $dirVideo)) -ne 0) {
        Falhar "docker build da imagem de gravacao falhou."
    }
}

$codigos = @{}
$extras = @()
if ($Clipes) { $extras += @("--clipes", $Clipes) }

# ------------------------------------------------------------------ (e) navegador, passada 1
if (Tem @(1, 2, 3, 4, 5, 6, 7, 8)) {
    if (Tem @(6, 7)) {
        $env:GESTOR_APIKEY = SegredoRunner "gateway" "consumidores-kong" "GESTOR_APIKEY"
        $env:GATEWAY_APIKEY = SegredoRunner "gateway" "consumidores-kong" "GATEWAY_PAGAMENTO_APIKEY"
        $env:WEBHOOK_SECRET = SegredoRunner "principal" "principal-webhook" "WEBHOOK_SECRET"
        if (-not $env:GESTOR_APIKEY -or -not $env:GATEWAY_APIKEY -or -not $env:WEBHOOK_SECRET) {
            Falhar "nao foi possivel ler as chaves do Kong e o segredo do webhook pelo runner."
        }
        Write-Host "   chaves lidas (valores ocultos)"
    }
    Etapa "(e) Navegador: GitHub, SonarCloud, Swagger e Jaeger"
    $codigos["navegador"] = ExecutarGravacao "navegador" ($extras + @("--excluir", "b7_")) $true
}

# ------------------------------------------------------------------ (f) coleta pos-uso
if (Tem @(6)) {
    Etapa "(f) Coleta pos-uso: os dois bancos"
    ColetaRunner "pos" @("psql-principal", "mongo-compras")
}

# ------------------------------------------------------------------ (g) falha do principal
if (Tem @(7)) {
    Etapa "(g) Falha do principal: derruba, grava, restaura e recupera"
    try {
        ColetaRunner "derrubar" @("caos-derrubar")
        $codigos["principal-fora"] = ExecutarGravacao "navegador" @("--clipes", "b7_02") $true
    } finally {
        # Restaura SEMPRE, mesmo se a gravacao falhar no meio
        ColetaRunner "restaurar" @("caos-restaurar")
    }
    Write-Host "   esperando 20 s (backoff da compensacao e circuito meio-aberto)"
    Start-Sleep -Seconds 20
    ColetaRunner "recuperacao" @("caos-recuperacao")
    $codigos["observabilidade"] = ExecutarGravacao "navegador" @("--clipes", "b7_04,b7_05") $true
}
LimparSegredos

# ------------------------------------------------------------------ (h) terminal
if (Tem @(3, 4, 5, 6, 7)) {
    Etapa "(h) Gravacao dos clipes de terminal"
    $codigos["terminal"] = ExecutarGravacao "terminal" $extras $false
}

# ------------------------------------------------------------------ montagem e versao legendada
if ($Montar) {
    Etapa "Montagem (montar.sh) e video final legendado (narrar.py --dublagem), no container"
    $codigos["montagem"] = Invocar "docker" @("run", "--rm", "--init",
        "--mount", "type=bind,source=$dirVideo,target=/video,readonly",
        "--mount", "type=bind,source=$saida,target=/saida",
        "--entrypoint", "bash", $imagem, "/video/montar.sh", "/saida", "--com-opcionais")
    if ($codigos["montagem"] -eq 0) {
        $ritmoTxt = $Ritmo.ToString([Globalization.CultureInfo]::InvariantCulture)
        $codigos["legendado"] = Invocar "docker" @("run", "--rm", "--init",
            "--mount", "type=bind,source=$dirVideo,target=/video,readonly",
            "--mount", "type=bind,source=$saida,target=/saida",
            "--entrypoint", "python3", $imagem, "/video/narrar.py", "--dublagem", $ritmoTxt)
    }
}

# ------------------------------------------------------------------ fim
LimparSegredos
Etapa "Resumo"
foreach ($k in $codigos.Keys) {
    $c = $codigos[$k]
    $txt = $(if ($c -eq 0) { "OK" } elseif ($c -eq 3) { "terminou com clipes FALHOU (veja o resumo acima e .setup\video\falhas)" } else { "codigo $c" })
    Write-Host ("  {0,-16} {1}" -f $k, $txt)
}
foreach ($a in $avisos) { Write-Host "  aviso: $a" }
Write-Host ""
Write-Host "Clipes:     $(Join-Path $saida 'clipes')"
Write-Host "Falhas:     $(Join-Path $saida 'falhas')"
Write-Host "Manifesto:  $(Join-Path $saida 'manifesto.json')"
if ($Montar) { Write-Host "Video:      $(Join-Path $saida 'montagem\video-final-legendado.mp4')" }
else { Write-Host "Montar:     gravar.ps1 -Blocos 0 -Montar" }
Write-Host "Log:        $log"
Stop-Transcript | Out-Null
$falhou = @($codigos.Values | Where-Object { $_ -ne 0 }).Count -gt 0
if ($falhou) { exit 3 } else { exit 0 }
