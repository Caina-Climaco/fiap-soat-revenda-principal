locals {
  rotulos_comuns = {
    "app.kubernetes.io/part-of"    = "revenda-veiculos"
    "app.kubernetes.io/managed-by" = "terraform"
  }
}

# Servico principal (API, principal-db e Job de migracao). Os namespaces gateway e
# observabilidade estao em gateway.tf e observabilidade.tf. O namespace vendas NAO e deste
# repositorio: e criado pelo Terraform de fiap-soat-revenda-vendas (ADR-006).
resource "kubernetes_namespace_v1" "principal" {
  metadata {
    name   = "principal"
    labels = local.rotulos_comuns
  }
}
