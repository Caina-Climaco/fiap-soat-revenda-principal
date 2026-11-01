# Remove a plataforma e o servico principal do cluster local: terraform destroy do state
# DESTE repositorio (namespaces principal, gateway e observabilidade, principal-db,
# segredos e metrics-server; o Deployment da API some junto com o namespace) e remove o
# state. Os dados do principal-db sao PERDIDOS. O namespace vendas (outro repositorio) nao
# e tocado, mas fica sem Kong e sem principal; o cluster kind, compartilhado, so e apagado
# com -ApagarCluster (ADR-006).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\05-destruir-ambiente.ps1 [-Forcar] [-ApagarCluster]
#     -Forcar         nao pede confirmacao
#     -ApagarCluster  tambem apaga o cluster kind "revenda" (derruba o servico de venda junto)
# Log: .setup\relatorio-ambiente-destruir.txt. Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [switch]$Forcar,
    [switch]$ApagarCluster
)

$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfDir = Join-Path $raiz "infra\terraform"
$log = Join-Path $raiz ".setup\relatorio-ambiente-destruir.txt"
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

function ClustersKind {
    $saida = @(& kind get clusters 2>$null)
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($saida | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

Write-Host "05-destruir-ambiente.ps1 - $(Get-Date -Format s)"
foreach ($f in @("docker", "kind", "terraform")) {
    if (-not (Get-Command $f -ErrorAction SilentlyContinue)) { Falhar "'$f' nao encontrado no PATH." }
}
if ((Invocar "docker" @("version", "--format", "docker {{.Server.Version}}")) -ne 0) {
    Falhar "Docker Desktop nao responde. Inicie o Docker Desktop e tente de novo."
}

if (-not $Forcar) {
    $alvo = "os namespaces principal, gateway e observabilidade (API, principal-db, Kong, monitoramento)"
    if ($ApagarCluster) { $alvo += " E o cluster kind 'revenda' inteiro (servico de venda inclusive)" }
    Write-Host "Isto apaga $alvo."
    $resposta = Read-Host "Digite SIM para continuar"
    if ($resposta -cne "SIM") {
        Write-Host "Cancelado."
        Stop-Transcript | Out-Null
        exit 0
    }
}

# Mesmos caminhos do 04-subir-ambiente.ps1 (o cd.yml usa este state via /revenda-state)
$perfil = $env:USERPROFILE -replace '\\', '/'
$stateDir = "$perfil/.revenda"
$statePath = "$stateDir/revenda-principal.tfstate"
$env:TF_DATA_DIR = "$stateDir/terraform-data-principal"
$env:TF_IN_AUTOMATION = "1"
$env:TF_INPUT = "0"
$env:KUBECONFIG = "$perfil/.kube/config"
$env:TF_VAR_kubeconfig_path = "$perfil/.kube/config"

if ((ClustersKind) -contains "revenda") {
    if (Test-Path $statePath) {
        $codigo = Invocar "kind" @("export", "kubeconfig", "--name", "revenda")
        if ($codigo -eq 0) {
            $codigo = Invocar "terraform" @("-chdir=$tfDir", "init", "-input=false", "-no-color", "-reconfigure", "-backend-config=path=$statePath")
        }
        if ($codigo -eq 0) {
            $codigo = Invocar "terraform" @("-chdir=$tfDir", "destroy", "-input=false", "-no-color", "-auto-approve")
        }
        if ($codigo -ne 0) { Falhar "terraform destroy falhou (codigo $codigo); o state foi mantido em $statePath." }
    } else {
        Write-Host "Sem state em $statePath; nada do principal a destruir pelo Terraform."
    }
    if ($ApagarCluster) {
        if ((Invocar "kind" @("delete", "cluster", "--name", "revenda")) -ne 0) { Falhar "kind delete cluster falhou." }
        # O state do servico de venda descrevia recursos do cluster apagado
        Remove-Item -Path "$stateDir/revenda-vendas.tfstate", "$stateDir/revenda-vendas.tfstate.backup" -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "Cluster kind 'revenda' nao existe; nada a destruir no cluster."
}

# Os recursos do state nao existem mais (o state tem segredos em texto claro)
Remove-Item -Path $statePath, "$statePath.backup" -Force -ErrorAction SilentlyContinue
Write-Host "State removido: $statePath"

if ($ApagarCluster -and ((ClustersKind) -contains "revenda")) { Falhar "o cluster 'revenda' ainda existe." }
Write-Host ""
Write-Host "Principal removido. Para recriar: scripts\windows\04-subir-ambiente.ps1 e depois o CD."
Write-Host "As imagens revenda-principal:<sha> continuam no Docker local (docker image prune para limpar)."
Write-Host "Log: $log"
Stop-Transcript | Out-Null
exit 0
