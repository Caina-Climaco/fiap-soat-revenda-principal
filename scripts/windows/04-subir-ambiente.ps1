# Sobe a PLATAFORMA e a infraestrutura do servico principal no cluster kind local: cria o
# cluster "revenda" com a CLI kind, se faltar (infra/kind/cluster.yaml, identico nos dois
# repositorios), e aplica o Terraform DESTE repositorio: namespaces principal, gateway e
# observabilidade, segredos, principal-db, Kong, Prometheus, Grafana, OpenTelemetry
# Collector, Jaeger e metrics-server (ADR-006). A aplicacao e implantada pelo CD.
# O servico de venda e de OUTRO repositorio (fiap-soat-revenda-vendas): suba-o DEPOIS deste,
# pelo 04-subir-ambiente.ps1 de la (ou pelo CD de la).
# O cluster NAO e criado pelo Terraform: o provider tehcyx/kind nao tem assinatura de
# codigo e e bloqueado pelo Smart App Control do Windows 11.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\04-subir-ambiente.ps1 [-SemBancoExposto] [-RecriarCluster]
#     -SemBancoExposto  nao publica o principal-db em localhost:15432
#     -RecriarCluster   apaga e recria o cluster "revenda" quando ele e de uma fase anterior
#                       (sem a porta do Jaeger); TUDO que estiver nele e perdido
#
# State: %USERPROFILE%\.revenda\revenda-principal.tfstate (TF_DATA_DIR em
# %USERPROFILE%\.revenda\terraform-data-principal). O CD usa o MESMO arquivo de state: o
# runner e um container Linux que monta %USERPROFILE%\.revenda em /revenda-state; so o
# TF_DATA_DIR dele e outro (providers Linux). O state do servico de venda e outro arquivo
# (revenda-vendas.tfstate). Nunca dentro do repositorio.
# Log: .setup\relatorio-ambiente-subir.txt. Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [switch]$SemBancoExposto,
    [switch]$RecriarCluster
)

$ErrorActionPreference = "Continue"
# Recarrega o PATH do registro: ferramentas instaladas pelo winget nesta sessao
# (kind, terraform) so aparecem em janelas novas do PowerShell.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfDir = Join-Path $raiz "infra\terraform"
$log = Join-Path $raiz ".setup\relatorio-ambiente-subir.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null

function Invocar {
    param([string]$Exe, [string[]]$Argumentos)
    Write-Host ">> $Exe $($Argumentos -join ' ')"
    $global:LASTEXITCODE = 0
    & $Exe @Argumentos 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    } | Out-Host
    return $LASTEXITCODE
}

function Falhar([string]$motivo) {
    Write-Host ""
    Write-Host "ERRO: $motivo"
    Write-Host "Log: $log"
    Stop-Transcript | Out-Null
    exit 1
}

# Clusters kind existentes (a mensagem "No kind clusters found." vai para stderr)
function ClustersKind {
    $saida = @(& kind get clusters 2>$null)
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($saida | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

# O cluster da Fase 4 publica o Jaeger (30686 -> 16686); o da Fase 3 nao.
function ClusterDestaFase {
    $porta = (& docker port revenda-control-plane 30686/tcp 2>$null)
    return [bool]$porta
}

Write-Host "04-subir-ambiente.ps1 - $(Get-Date -Format s)"
foreach ($f in @("docker", "kind", "kubectl", "terraform")) {
    if (-not (Get-Command $f -ErrorAction SilentlyContinue)) {
        Falhar "'$f' nao encontrado no PATH (rode scripts\windows\01-instalar-ferramentas.ps1)."
    }
}
if ((Invocar "docker" @("version", "--format", "docker {{.Server.Version}}")) -ne 0) {
    Falhar "Docker Desktop nao responde. Inicie o Docker Desktop e tente de novo."
}
$null = Invocar "kind" @("version")
$null = Invocar "terraform" @("version")

# ------------------------------------------------------------------ caminhos (state compartilhado com o cd.yml)
# Barras normais e caminho sempre igual, para o terraform init nao pedir migracao de state.
$perfil = $env:USERPROFILE -replace '\\', '/'
$stateDir = "$perfil/.revenda"
$statePath = "$stateDir/revenda-principal.tfstate"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$env:TF_DATA_DIR = "$stateDir/terraform-data-principal"
$env:TF_IN_AUTOMATION = "1"
$env:TF_INPUT = "0"
$env:KUBECONFIG = "$perfil/.kube/config"
$env:TF_VAR_kubeconfig_path = "$perfil/.kube/config"
$env:TF_VAR_expor_banco = if ($SemBancoExposto) { "false" } else { "true" }
Write-Host "State: $statePath | TF_DATA_DIR: $env:TF_DATA_DIR | KUBECONFIG: $env:KUBECONFIG"

# ------------------------------------------------------------------ cluster (CLI kind)
$configKind = Join-Path $raiz "infra\kind\cluster.yaml"
if (-not (Test-Path $configKind)) { Falhar "arquivo $configKind nao encontrado." }

if (((ClustersKind) -contains "revenda") -and -not (ClusterDestaFase)) {
    if (-not $RecriarCluster) {
        Falhar ("o cluster kind 'revenda' existente e de uma fase anterior (sem a porta 16686 do Jaeger). " +
            "Rode de novo com -RecriarCluster para apaga-lo e criar o desta fase (tudo nele e perdido).")
    }
    Write-Host "Apagando o cluster 'revenda' da fase anterior (-RecriarCluster)"
    if ((Invocar "kind" @("delete", "cluster", "--name", "revenda")) -ne 0) { Falhar "kind delete cluster falhou." }
    # States de recursos que deixaram de existir junto com o cluster
    foreach ($antigo in @("revenda-principal.tfstate", "revenda-vendas.tfstate")) {
        Remove-Item -Path "$stateDir/$antigo", "$stateDir/$antigo.backup" -Force -ErrorAction SilentlyContinue
    }
}

# Idempotente: so cria se `kind get clusters` nao listar "revenda". Se o cluster foi
# recriado com o state antigo, o Terraform percebe no refresh que os recursos sumiram e
# os cria de novo (mantendo as senhas do state).
if ((ClustersKind) -contains "revenda") {
    Write-Host "Cluster kind 'revenda' ja existe (nada a criar)."
} else {
    $codigo = Invocar "kind" @("create", "cluster", "--config", $configKind, "--wait", "120s")
    if ($codigo -ne 0) { Falhar "kind create cluster falhou (codigo $codigo)." }
}
# Grava/atualiza o contexto kind-revenda no KUBECONFIG usado pelos providers do Terraform
if ((Invocar "kind" @("export", "kubeconfig", "--name", "revenda")) -ne 0) { Falhar "kind export kubeconfig falhou." }

# ------------------------------------------------------------------ terraform
$codigo = Invocar "terraform" @("-chdir=$tfDir", "init", "-input=false", "-no-color", "-reconfigure", "-backend-config=path=$statePath")
if ($codigo -ne 0) { Falhar "terraform init falhou (codigo $codigo)." }
$codigo = Invocar "terraform" @("-chdir=$tfDir", "apply", "-input=false", "-no-color", "-auto-approve")
if ($codigo -ne 0) { Falhar "terraform apply falhou (codigo $codigo). Diagnostico: kubectl get pods -A" }

# ------------------------------------------------------------------ verificacao
$null = Invocar "kubectl" @("config", "current-context")
$null = Invocar "kubectl" @("get", "nodes", "-o", "wide")
$null = Invocar "kubectl" @("get", "pods", "-n", "principal", "-o", "wide")
$null = Invocar "kubectl" @("get", "pods", "-n", "gateway", "-o", "wide")
$null = Invocar "kubectl" @("get", "pods", "-n", "observabilidade", "-o", "wide")
$null = Invocar "terraform" @("-chdir=$tfDir", "output", "-no-color", "urls")

Write-Host ""
Write-Host "==================== plataforma e servico principal ===================="
Write-Host "Kong (entrada publica)  http://localhost:8080   (Swagger: /principal/docs e /vendas/docs)"
Write-Host "Grafana                 http://localhost:3000   (paineis Revenda de Veiculos; anonimo como Viewer)"
Write-Host "Prometheus              http://localhost:9090   (alvos em /targets, alertas em /alerts)"
Write-Host "Jaeger                  http://localhost:16686  (traces da compra)"
if (-not $SemBancoExposto) { Write-Host "Banco do principal      localhost:15432 (usuario revenda, banco revenda_principal)" }
Write-Host ""
Write-Host "Chaves e segredos para a demonstracao: scripts\windows\segredos.ps1"
Write-Host ""
Write-Host "Proximos passos: CD deste repositorio (merge na main ou gh workflow run cd.yml) e"
Write-Host "depois o servico de venda (repositorio fiap-soat-revenda-vendas)."
Write-Host "Log: $log"
Stop-Transcript | Out-Null
exit 0
