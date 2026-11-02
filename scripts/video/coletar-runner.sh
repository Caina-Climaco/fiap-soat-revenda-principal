#!/usr/bin/env bash
# Coleta, DENTRO do container revenda-runner-principal (rede kind, com kind/kubectl/terraform/
# docker), as saidas reais exibidas nos clipes de terminal do video. Chamado pelo gravar.ps1:
#   docker cp scripts/video/coletar-runner.sh revenda-runner-principal:/tmp/video-coletar.sh
#   docker exec revenda-runner-principal bash /tmp/video-coletar.sh <modo> /tmp/video-coleta-<modo>
#   docker cp revenda-runner-principal:/tmp/video-coleta-<modo>/. .setup/video/terminal/
# Modos:
#   pre          bloco 3: pods, recursos de cada namespace, NetworkPolicies, os dois states do
#                Terraform e docker ps
#   pos          bloco 6: o veiculo VENDIDO e a cobranca PAGA no PostgreSQL do principal; a
#                compra com o CPF cifrado no MongoDB do servico de venda
#   derrubar     bloco 7: escala o principal para 0 e espera os pods sairem
#   restaurar    bloco 7: volta o principal para 2 replicas e espera o rollout
#   recuperacao  bloco 7: executa a recuperacao de sagas (job a partir do CronJob) e mostra a
#                compra desfeita e o veiculo desbloqueado nos dois bancos
# Para cada saida NOME grava NOME.txt (saida real) e NOME.cmd (o comando exibido no video).
# Se um comando falha, grava NOME.erro (diagnostico) e nao grava o .txt: o clipe e pulado.
# Credenciais dos bancos: so as variaveis de ambiente dos proprios pods (nunca nos .cmd).
set -uo pipefail

MODO="${1:-}"
DEST="${2:-/tmp/video-coleta}"
CLUSTER="revenda"
STATE_P="/revenda-state/revenda-principal.tfstate"
STATE_V="/revenda-state/revenda-vendas.tfstate"

rm -rf "$DEST"
mkdir -p "$DEST"

if ! kind export kubeconfig --internal --name "$CLUSTER" >/dev/null 2>&1; then
  echo "ERRO: kind export kubeconfig --internal --name $CLUSTER falhou" >&2
  exit 1
fi

# coletar NOME COMANDO [COMANDO_EXIBIDO]
coletar() {
  local nome="$1" comando="$2" exibido="${3:-$2}"
  echo ">> $nome"
  if bash -c "$comando" >"$DEST/$nome.txt" 2>&1 && [ -s "$DEST/$nome.txt" ]; then
    printf '%s\n' "$exibido" >"$DEST/$nome.cmd"
    echo "   ok ($(wc -l <"$DEST/$nome.txt") linhas)"
  else
    mv -f "$DEST/$nome.txt" "$DEST/$nome.erro" 2>/dev/null || true
    echo "   FALHOU (detalhes em $nome.erro):"
    sed -n '1,8p' "$DEST/$nome.erro" 2>/dev/null | sed 's/^/     /'
  fi
}

# psql e mongosh dentro dos pods, com o usuario da aplicacao lido do ambiente do pod
# shellcheck disable=SC2016  # as variaveis expandem DENTRO do pod
PSQL='kubectl -n principal exec statefulset/principal-db -- sh -c '"'"'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "$1"'"'"' sh'
# shellcheck disable=SC2016
MONGO='kubectl -n vendas exec statefulset/vendas-db -- sh -c '"'"'mongosh -u "$MONGO_APP_USUARIO" -p "$MONGO_APP_SENHA" --authenticationDatabase "$MONGO_APP_BANCO" "$MONGO_APP_BANCO" --quiet --eval "$1"'"'"' sh'

SQL_VEICULOS="SELECT modelo, preco, status, versao, compra_id FROM catalogo.veiculos ORDER BY criado_em;"
SQL_COBRANCAS="SELECT codigo, valor, status, compra_id FROM pagamentos.cobrancas ORDER BY criada_em;"
JS_COMPRAS='db.compras.find({}, {status: 1, "veiculo.modelo": 1, cpf: 1, codigo_pagamento: 1, data_venda: 1, motivo_cancelamento: 1, "saga.compensacoes_feitas": 1, "saga.compensacoes_pendentes": 1}).sort({criada_em: 1}).toArray()'

case "$MODO" in
  pre)
    coletar kubectl-pods "kubectl get pods -A"
    coletar kubectl-principal "kubectl -n principal get deploy,statefulset,svc,hpa"
    coletar kubectl-vendas "kubectl -n vendas get deploy,statefulset,svc,hpa,cronjob"
    coletar kubectl-netpol "kubectl get networkpolicy -A"
    # States locais compartilhados com o Windows (bind mount). TF_DATA_DIR temporario e
    # diretorio vazio: so le os arquivos de state, sem init e sem tocar neles.
    tf_vazio="$(mktemp -d)"
    if [ -f "$STATE_P" ] && [ -f "$STATE_V" ]; then
      coletar terraform-states \
        "cd '$tf_vazio' && echo '# principal' && TF_DATA_DIR='$tf_vazio/.tf' terraform state list -state='$STATE_P' && echo && echo '# servico de venda' && TF_DATA_DIR='$tf_vazio/.tf' terraform state list -state='$STATE_V'" \
        "$(printf 'terraform state list -state=%s\nterraform state list -state=%s' "$STATE_P" "$STATE_V")"
    else
      echo ">> terraform-states: states ausentes em /revenda-state (bind mount de %USERPROFILE%\\.revenda?)"
    fi
    rm -rf "$tf_vazio"
    coletar docker-ps "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'" \
      "docker ps --format 'table {{.Names}}\\t{{.Image}}\\t{{.Status}}'"
    ;;

  pos)
    coletar psql-principal \
      "$PSQL '$SQL_VEICULOS' && $PSQL '$SQL_COBRANCAS'" \
      "$(printf 'kubectl -n principal exec statefulset/principal-db -- psql -c "%s"\nkubectl -n principal exec statefulset/principal-db -- psql -c "%s"' "$SQL_VEICULOS" "$SQL_COBRANCAS")"
    coletar mongo-compras \
      "$MONGO '$JS_COMPRAS'" \
      "kubectl -n vendas exec statefulset/vendas-db -- mongosh revenda_vendas --eval '$JS_COMPRAS'"
    ;;

  derrubar)
    coletar caos-derrubar \
      "kubectl -n principal scale deployment revenda-principal --replicas=0 && (kubectl -n principal wait --for=delete pod -l app=revenda-principal --timeout=120s || true) && kubectl -n principal get deployment revenda-principal" \
      "$(printf 'kubectl -n principal scale deployment revenda-principal --replicas=0\nkubectl -n principal wait --for=delete pod -l app=revenda-principal --timeout=120s\nkubectl -n principal get deployment revenda-principal')"
    ;;

  restaurar)
    coletar caos-restaurar \
      "kubectl -n principal scale deployment revenda-principal --replicas=2 && kubectl -n principal rollout status deployment/revenda-principal --timeout=180s && kubectl -n principal get pods -l app=revenda-principal" \
      "$(printf 'kubectl -n principal scale deployment revenda-principal --replicas=2\nkubectl -n principal rollout status deployment/revenda-principal\nkubectl -n principal get pods -l app=revenda-principal')"
    ;;

  recuperacao)
    job="recuperacao-video-$(date +%s)"
    coletar caos-recuperacao \
      "kubectl -n vendas create job $job --from=cronjob/revenda-vendas-recuperacao && kubectl -n vendas wait --for=condition=complete job/$job --timeout=120s && kubectl -n vendas logs job/$job | tail -n 3 && echo && $MONGO '$JS_COMPRAS' && echo && $PSQL '$SQL_VEICULOS'" \
      "$(printf 'kubectl -n vendas create job %s --from=cronjob/revenda-vendas-recuperacao\nkubectl -n vendas wait --for=condition=complete job/%s\nkubectl -n vendas logs job/%s\nkubectl -n vendas exec statefulset/vendas-db -- mongosh revenda_vendas --eval %s\nkubectl -n principal exec statefulset/principal-db -- psql -c "%s"' "$job" "$job" "$job" "'db.compras.find(...)'" "$SQL_VEICULOS")"
    kubectl -n vendas delete job "$job" --ignore-not-found >/dev/null 2>&1 || true
    ;;

  *)
    echo "uso: $0 pre|pos|derrubar|restaurar|recuperacao [destino]" >&2
    exit 2
    ;;
esac
exit 0
