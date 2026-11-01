# Mostra as chaves e segredos do ambiente local para a demonstracao (video, Swagger,
# Postman): chave do gestor e do gateway de pagamento no Kong, segredo do webhook e senha
# do admin do Grafana. Le os Secrets do cluster kind "revenda" (contexto kind-revenda);
# nada e gravado em arquivo.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\segredos.ps1
# Arquivo somente ASCII (Windows PowerShell 5.1).

$ErrorActionPreference = "Stop"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path

function Segredo([string]$Namespace, [string]$Nome, [string]$Chave) {
    $b64 = (& kubectl --context kind-revenda -n $Namespace get secret $Nome -o "jsonpath={.data.$Chave}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $b64) { return "(indisponivel: $Namespace/$Nome)" }
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
}

Write-Host "Kong, header apikey do gestor            : $(Segredo 'gateway' 'consumidores-kong' 'GESTOR_APIKEY')"
Write-Host "Kong, header apikey do gateway pagamento : $(Segredo 'gateway' 'consumidores-kong' 'GATEWAY_PAGAMENTO_APIKEY')"
Write-Host "Webhook, header X-Webhook-Secret         : $(Segredo 'principal' 'principal-webhook' 'WEBHOOK_SECRET')"
Write-Host "Grafana, senha do usuario admin          : $(Segredo 'observabilidade' 'grafana-admin' 'GF_SECURITY_ADMIN_PASSWORD')"
Write-Host ""
Write-Host "Exemplo (PowerShell): cadastrar um veiculo pelo Kong"
Write-Host '  $h = @{ apikey = "<chave do gestor>" }'
Write-Host '  $c = @{ marca="Fiat"; modelo="Argo"; ano=2022; cor="Branco"; preco="72900.00" } | ConvertTo-Json'
Write-Host '  Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/veiculos -Headers $h -ContentType application/json -Body $c'
