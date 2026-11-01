# Limpa os dados de negocio do ambiente local (cluster kind "revenda"), por exemplo antes de
# gravar o video. Os testes ponta a ponta do CD cadastram veiculos de marca "E2E" a cada
# deploy; sem limpeza eles se acumulam na vitrine.
#
# Padrao: apaga so o que os testes ponta a ponta criaram, nos dois bancos:
#   PostgreSQL (principal): veiculos de marca E2E, as cobrancas e compensacoes das compras
#                           desses veiculos e as mensagens da outbox deles.
#   MongoDB (venda):        anuncios desses veiculos e as compras deles (com o CPF cifrado).
# -Tudo:     apaga TODOS os veiculos, cobrancas, compensacoes, mensagens, anuncios e compras.
# -Simular:  so conta o que seria apagado (nada e alterado).
# -Sim:      nao pede confirmacao no -Tudo.
# -Forcar:   roda mesmo com um CD em andamento (trava revenda-cd-em-andamento).
#
# Usuarios, chaves e configuracao nao sao tocados; as metricas do Prometheus guardam o
# historico (os contadores nao voltam a zero). Rode sem compras em andamento.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1 -Simular
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1 -Tudo
# Arquivo somente ASCII (Windows PowerShell 5.1).

param(
    [switch]$Tudo,
    [switch]$Simular,
    [switch]$Sim,
    [switch]$Forcar
)

# Continue: no Windows PowerShell 5.1, com Stop, qualquer linha no stderr de um programa
# externo (kubectl, docker) vira erro fatal; os codigos de saida sao conferidos um a um.
$ErrorActionPreference = "Continue"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$contexto = "kind-revenda"

# Os comandos dentro do pod nao usam aspas duplas: o Windows PowerShell 5.1 nao as repassa
# corretamente a programas externos. Os scripts (SQL e JavaScript) vao pela entrada padrao.
$psql = 'psql -X -q -At -v ON_ERROR_STOP=1 -U $POSTGRES_USER -d $POSTGRES_DB'
$mongosh = 'cat > /tmp/limpeza.js && mongosh -u $MONGO_APP_USUARIO -p $MONGO_APP_SENHA --authenticationDatabase $MONGO_APP_BANCO $MONGO_APP_BANCO --quiet --file /tmp/limpeza.js; rc=$?; rm -f /tmp/limpeza.js; exit $rc'

function Executar-NoPod([string]$Namespace, [string]$Alvo, [string]$Comando, [string]$Entrada, [string]$Descricao) {
    $saida = $Entrada | & kubectl --context $contexto -n $Namespace exec -i $Alvo -- sh -c $Comando 2>&1
    $linhas = @($saida | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne "" })
    if ($LASTEXITCODE -ne 0) {
        $linhas | ForEach-Object { Write-Host "  $_" }
        throw "Falha em $Descricao (kubectl exec em $Namespace/$Alvo)."
    }
    return $linhas
}

function Postgres([string]$Sql, [string]$Descricao) {
    return Executar-NoPod "principal" "statefulset/principal-db" $psql $Sql $Descricao
}

function Mongo([string]$Js, [string]$Descricao) {
    return Executar-NoPod "vendas" "statefulset/vendas-db" $mongosh $Js $Descricao
}

function Mostrar([string[]]$Linhas, [string]$Banco) {
    foreach ($l in $Linhas) {
        if ($l -cmatch '^([a-z]+) (\d+)$') { Write-Host ("  {0,-9} {1,-14} {2}" -f $Banco, $Matches[1], $Matches[2]) }
    }
}

# ---- Pre-condicoes ---------------------------------------------------------------------

& kubectl config get-contexts $contexto *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: contexto $contexto nao encontrado. Suba o ambiente com 04-subir-ambiente.ps1."
    exit 1
}
& docker network inspect revenda-cd-em-andamento *> $null
if ($LASTEXITCODE -eq 0 -and -not $Forcar) {
    Write-Host "ERRO: ha um CD em andamento (rede docker revenda-cd-em-andamento). Espere terminar ou use -Forcar."
    exit 1
}

$fim = if ($Simular) { "rollback;" } else { "commit;" }
$simularJs = if ($Simular) { "true" } else { "false" }
$verbo = if ($Simular) { "seriam apagados" } else { "apagados" }

# ---- Tudo -------------------------------------------------------------------------------

if ($Tudo) {
    if (-not $Simular -and -not $Sim) {
        Write-Host "Isto apaga TODOS os veiculos, cobrancas, anuncios e compras do ambiente local."
        $resposta = Read-Host "Digite APAGAR para continuar"
        if ($resposta -cne "APAGAR") { Write-Host "Cancelado."; exit 1 }
    }
    # Primeiro o PostgreSQL: com a outbox vazia, nada mais e entregue ao servico de venda.
    $sql = @"
begin;
select 'veiculos ' || count(*) from catalogo.veiculos;
select 'compensacoes ' || count(*) from catalogo.compensacoes;
select 'cobrancas ' || count(*) from pagamentos.cobrancas;
select 'outbox ' || count(*) from mensageria.outbox;
truncate catalogo.veiculos, catalogo.compensacoes, pagamentos.cobrancas, mensageria.outbox;
$fim
"@
    $pg = Postgres $sql "limpeza do PostgreSQL"
    $js = @"
const simular = $simularJs;
print('anuncios ' + db.anuncios.countDocuments({}));
print('compras ' + db.compras.countDocuments({}));
if (!simular) { db.compras.deleteMany({}); db.anuncios.deleteMany({}); }
"@
    $mg = Mongo $js "limpeza do MongoDB"
    Write-Host "Registros $verbo (todos os dados de negocio):"
    Mostrar $pg "principal"
    Mostrar $mg "venda"
    exit 0
}

# ---- So os dados dos testes ponta a ponta -------------------------------------------------

$veiculos = @(Postgres "select id from catalogo.veiculos where marca = 'E2E';" "leitura dos veiculos E2E" |
    Where-Object { $_ -match '^[0-9a-f-]{36}$' })

# MongoDB: compras dos veiculos E2E (inclusive anuncios E2E sem veiculo no principal)
$listaJs = "[" + (($veiculos | ForEach-Object { "'$_'" }) -join ",") + "]"
$js = @"
const simular = $simularJs;
const doPrincipal = $listaJs;
const daVitrine = db.anuncios.find({ marca: 'E2E' }, { _id: 1 }).toArray().map(d => d._id);
const veiculos = Array.from(new Set(doPrincipal.concat(daVitrine)));
const compras = db.compras.find({ veiculo_id: { `$in: veiculos } }, { _id: 1 }).toArray().map(d => d._id);
compras.forEach(c => print('COMPRA ' + c));
print('anuncios ' + db.anuncios.countDocuments({ _id: { `$in: veiculos } }));
print('compras ' + compras.length);
if (!simular) {
  db.compras.deleteMany({ _id: { `$in: compras } });
  db.anuncios.deleteMany({ _id: { `$in: veiculos } });
}
"@
$mg = Mongo $js "limpeza do MongoDB"
$compras = @($mg | Where-Object { $_ -match '^COMPRA [0-9a-f-]{36}$' } | ForEach-Object { $_.Substring(7) })

# PostgreSQL: outbox, cobrancas, compensacoes e os veiculos, numa transacao
$arrV = "'{" + ($veiculos -join ",") + "}'::uuid[]"
$arrC = "'{" + ($compras -join ",") + "}'::uuid[]"
$sql = @"
begin;
create temp table alvo_v on commit drop as select unnest($arrV) as id;
create temp table alvo_c on commit drop as
  select unnest($arrC) as id
  union select compra_id from catalogo.veiculos where id in (select id from alvo_v) and compra_id is not null
  union select compra_id from catalogo.compensacoes where veiculo_id in (select id from alvo_v);
with d as (delete from mensageria.outbox where chave in (select 'veiculo:' || id from alvo_v union select 'compra:' || id from alvo_c) returning 1) select 'outbox ' || count(*) from d;
with d as (delete from pagamentos.cobrancas where compra_id in (select id from alvo_c) returning 1) select 'cobrancas ' || count(*) from d;
with d as (delete from catalogo.compensacoes where veiculo_id in (select id from alvo_v) returning 1) select 'compensacoes ' || count(*) from d;
with d as (delete from catalogo.veiculos where id in (select id from alvo_v) returning 1) select 'veiculos ' || count(*) from d;
$fim
"@
$pg = Postgres $sql "limpeza do PostgreSQL"

# Uma mensagem da outbox entregue entre os dois passos recriaria o anuncio: apaga de novo.
if (-not $Simular -and $veiculos.Count -gt 0) {
    $null = Mongo "db.anuncios.deleteMany({ _id: { `$in: $listaJs } });" "repasse no MongoDB"
}

Write-Host "Registros $verbo (dados dos testes ponta a ponta, marca E2E):"
Mostrar $pg "principal"
Mostrar $mg "venda"
if ($Simular) { Write-Host "Simulacao: nada foi alterado. Rode sem -Simular para apagar." }
