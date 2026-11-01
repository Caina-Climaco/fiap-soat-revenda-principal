# NetworkPolicies de entrada do servico principal (docs/07-seguranca-lgpd.md). O CNI padrao
# do kind (kindnet) aplica NetworkPolicy desde o kind v0.24.
# Somente regras de ENTRADA: a saida dos pods (DNS, servico de venda, Collector) nao e
# restringida.

locals {
  # Trafego do proprio no: probes do kubelet e NodePort (apos o SNAT do kube-proxy, a
  # origem e o gateway da faixa de pods ou um IP fora dela).
  origem_no = [
    { cidr = "0.0.0.0/0", except = [var.pod_subnet] },
    { cidr = "${cidrhost(var.pod_subnet, 1)}/32", except = [] },
  ]
}

# principal-db: so a API do principal e o Job de migracao (mesmo namespace), mais o host
# quando expor_banco = true (demonstracao do banco no video).
resource "kubernetes_network_policy_v1" "principal_db" {
  metadata {
    name      = "principal-db-somente-api"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }

  spec {
    pod_selector {
      match_labels = {
        app = "principal-db"
      }
    }

    policy_types = ["Ingress"]

    ingress {
      from {
        pod_selector {
          match_expressions {
            key      = "app"
            operator = "In"
            values   = ["revenda-principal", "principal-migracao"]
          }
        }
      }
      ports {
        port     = "5432"
        protocol = "TCP"
      }
    }

    dynamic "ingress" {
      for_each = var.expor_banco ? [1] : []
      content {
        dynamic "from" {
          for_each = local.origem_no
          content {
            ip_block {
              cidr   = from.value.cidr
              except = from.value.except
            }
          }
        }
        ports {
          port     = "5432"
          protocol = "TCP"
        }
      }
    }
  }
}

# revenda-principal: dentro do cluster, so chegam aqui
#   - o Kong (namespace gateway): rotas publicas do gestor e do webhook;
#   - o servico de venda (namespace vendas, API e CronJob de recuperacao): rotas internas,
#     que alem disso exigem o X-Service-Token;
#   - o Prometheus (namespace observabilidade): /metrics.
# Assim o header X-Consumer-Username so chega pelo Kong (ADR-005).
resource "kubernetes_network_policy_v1" "revenda_principal" {
  metadata {
    name      = "revenda-principal-entrada"
    namespace = kubernetes_namespace_v1.principal.metadata[0].name
    labels    = local.rotulos_comuns
  }

  spec {
    pod_selector {
      match_labels = {
        app = "revenda-principal"
      }
    }

    policy_types = ["Ingress"]

    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = kubernetes_namespace_v1.gateway.metadata[0].name
          }
        }
        pod_selector {
          match_labels = {
            app = "kong"
          }
        }
      }
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = var.namespace_vendas
          }
        }
        pod_selector {
          match_expressions {
            key      = "app"
            operator = "In"
            values   = ["revenda-vendas", "revenda-vendas-recuperacao"]
          }
        }
      }
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = kubernetes_namespace_v1.observabilidade.metadata[0].name
          }
        }
        pod_selector {
          match_labels = {
            app = "prometheus"
          }
        }
      }
      ports {
        port     = "8000"
        protocol = "TCP"
      }
    }

    ingress {
      dynamic "from" {
        for_each = local.origem_no
        content {
          ip_block {
            cidr   = from.value.cidr
            except = from.value.except
          }
        }
      }
      ports {
        port     = "8000"
        protocol = "TCP"
      }
    }
  }
}
