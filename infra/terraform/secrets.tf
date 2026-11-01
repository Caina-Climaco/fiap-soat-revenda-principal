# Segredos gerados pelo Terraform. Nomes e chaves sao contrato com a aplicacao (k8s/base),
# o CD e o repositorio do servico de venda. Os valores ficam so no state local e nos
# Secrets do cluster. Rotacao: `terraform apply -replace=random_password.<nome>` (os
# tokens de servico exigem tambem um `terraform apply` no repositorio de venda).
# Senhas so com letras e digitos (seguras em URL, cabecalho HTTP e shell).

resource "random_password" "principal_db" {
  length  = 32
  special = false
}

# Segredo compartilhado com o processador de pagamentos (header X-Webhook-Secret, ADR-009)
resource "random_password" "webhook_secret" {
  length  = 48
  special = false
}

# Um token por direcao (ADR-005): quem chama apresenta, quem e chamado confere.
resource "random_password" "token_vendas_para_principal" {
  length  = 48
  special = false
}

resource "random_password" "token_principal_para_vendas" {
  length  = 48
  special = false
}

# Chaves dos consumers do Kong (key-auth, header `apikey`)
resource "random_password" "chave_gestor" {
  length  = 40
  special = false
}

resource "random_password" "chave_gateway_pagamento" {
  length  = 40
  special = false
}

resource "kubernetes_secret_v1" "principal_db_credentials" {
  metadata {
    name      = "principal-db-credentials"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    DB_USER     = "revenda"
    DB_PASSWORD = random_password.principal_db.result
    DB_NAME     = "revenda_principal"
  }
}

resource "kubernetes_secret_v1" "principal_webhook" {
  metadata {
    name      = "principal-webhook"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    WEBHOOK_SECRET = random_password.webhook_secret.result
  }
}

resource "kubernetes_secret_v1" "principal_tokens_servico" {
  metadata {
    name      = "principal-tokens-servico"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    TOKEN_SERVICO_ENTRADA = random_password.token_vendas_para_principal.result
    TOKEN_SERVICO_SAIDA   = random_password.token_principal_para_vendas.result
  }
}

# CONTRATO com o repositorio de venda (ADR-006): o Terraform dele le este Secret (data
# source) e monta o Secret do namespace vendas com os papeis invertidos. Nome e chaves
# nao mudam sem PR nos dois repositorios.
resource "kubernetes_secret_v1" "contrato_tokens_servico" {
  metadata {
    name      = "contrato-tokens-servico"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    VENDAS_PARA_PRINCIPAL = random_password.token_vendas_para_principal.result
    PRINCIPAL_PARA_VENDAS = random_password.token_principal_para_vendas.result
  }
}

# Chaves dos consumers do Kong, para a demonstracao e o e2e (o Kong as recebe no kong.yml).
resource "kubernetes_secret_v1" "consumidores_kong" {
  metadata {
    name      = "consumidores-kong"
    namespace = kubernetes_namespace_v1.gateway.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    GESTOR_APIKEY            = random_password.chave_gestor.result
    GATEWAY_PAGAMENTO_APIKEY = random_password.chave_gateway_pagamento.result
  }
}
