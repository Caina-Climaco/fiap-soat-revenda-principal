# Observabilidade (ADR-007) no namespace observabilidade:
#   Prometheus      coleta os dois servicos (namespaces principal e vendas) e o Kong por
#                   descoberta de pods e avalia infra/observabilidade/alertas.yml.
#                   localhost:9090 -> NodePort 30900.
#   Grafana         fontes de dados (Prometheus e Jaeger) e paineis provisionados.
#                   localhost:3000 -> NodePort 30300; leitura anonima (Viewer), admin com
#                   senha gerada no Secret grafana-admin.
#   OTel Collector  recebe OTLP do Kong e dos dois servicos, remove atributos com dado
#                   pessoal (segunda barreira, ADR-007) e envia ao Jaeger.
#   Jaeger          traces em memoria; localhost:16686 -> NodePort 30686.
# Sem armazenamento persistente: e um ambiente de demonstracao.

locals {
  dir_observabilidade = "${path.module}/../observabilidade"
  prometheus_config   = file("${local.dir_observabilidade}/prometheus.yml")
  prometheus_alertas  = file("${local.dir_observabilidade}/alertas.yml")
  grafana_fonte       = file("${local.dir_observabilidade}/grafana/fonte-de-dados.yml")
  grafana_provedor    = file("${local.dir_observabilidade}/grafana/paineis.yml")
  grafana_paineis = {
    for arquivo in fileset("${local.dir_observabilidade}/grafana", "painel-*.json") :
    arquivo => file("${local.dir_observabilidade}/grafana/${arquivo}")
  }
  otel_collector_config = file("${local.dir_observabilidade}/otel-collector.yaml")
}

resource "kubernetes_namespace_v1" "observabilidade" {
  metadata {
    name   = "observabilidade"
    labels = local.rotulos_comuns
  }
}

# ---- Prometheus --------------------------------------------------------------------

resource "kubernetes_service_account_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
}

# Leitura de pods em todo o cluster (so get/list/watch de pods): o namespace vendas e de
# outro repositorio e pode nascer depois deste apply, entao uma Role por namespace nao serve.
# A configuracao do Prometheus restringe a coleta aos namespaces principal, vendas e gateway.
resource "kubernetes_cluster_role_v1" "prometheus_leitura" {
  metadata {
    name   = "prometheus-leitura-pods"
    labels = local.rotulos_comuns
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "prometheus_leitura" {
  metadata {
    name   = "prometheus-leitura-pods"
    labels = local.rotulos_comuns
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.prometheus_leitura.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.prometheus.metadata[0].name
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
  }
}

resource "kubernetes_config_map_v1" "prometheus" {
  metadata {
    name      = "prometheus-config"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
  data = {
    "prometheus.yml" = local.prometheus_config
    "alertas.yml"    = local.prometheus_alertas
  }
}

resource "kubernetes_deployment_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "prometheus" })
  }

  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = {
        app = "prometheus"
      }
    }

    template {
      metadata {
        labels = merge(local.rotulos_comuns, { app = "prometheus" })
        annotations = {
          "revenda.io/config-sha256" = sha256("${local.prometheus_config}${local.prometheus_alertas}")
        }
      }

      spec {
        service_account_name            = kubernetes_service_account_v1.prometheus.metadata[0].name
        automount_service_account_token = true
        enable_service_links            = false

        security_context {
          run_as_non_root = true
          run_as_user     = 65534
          run_as_group    = 65534
          fs_group        = 65534
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "prometheus"
          image             = var.prometheus_imagem
          image_pull_policy = "IfNotPresent"
          args = [
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=2d",
          ]

          port {
            name           = "http"
            container_port = 9090
            protocol       = "TCP"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "1"
              memory = "768Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }

          readiness_probe {
            http_get {
              path = "/-/ready"
              port = "http"
            }
            period_seconds  = 5
            timeout_seconds = 3
          }
          liveness_probe {
            http_get {
              path = "/-/healthy"
              port = "http"
            }
            initial_delay_seconds = 15
            period_seconds        = 15
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/prometheus"
            read_only  = true
          }
          volume_mount {
            name       = "dados"
            mount_path = "/prometheus"
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map_v1.prometheus.metadata[0].name
          }
        }
        volume {
          name = "dados"
          empty_dir {}
        }
      }
    }
  }

  wait_for_rollout = true
  timeouts {
    create = "5m"
    update = "5m"
  }

  depends_on = [kubernetes_cluster_role_binding_v1.prometheus_leitura]
}

resource "kubernetes_service_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "prometheus" })
  }
  spec {
    type = "NodePort"
    selector = {
      app = "prometheus"
    }
    port {
      name        = "http"
      port        = 9090
      target_port = "http"
      node_port   = 30900
      protocol    = "TCP"
    }
  }
}

# ---- Grafana -----------------------------------------------------------------------

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "grafana_admin" {
  metadata {
    name      = "grafana-admin"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    GF_SECURITY_ADMIN_USER     = "admin"
    GF_SECURITY_ADMIN_PASSWORD = random_password.grafana_admin.result
  }
}

resource "kubernetes_config_map_v1" "grafana_provisionamento" {
  metadata {
    name      = "grafana-provisionamento"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
  data = {
    "fonte-de-dados.yml" = local.grafana_fonte
    "paineis.yml"        = local.grafana_provedor
  }
}

resource "kubernetes_config_map_v1" "grafana_paineis" {
  metadata {
    name      = "grafana-paineis"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
  data = local.grafana_paineis
}

resource "kubernetes_deployment_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "grafana" })
  }

  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = {
        app = "grafana"
      }
    }

    template {
      metadata {
        labels = merge(local.rotulos_comuns, { app = "grafana" })
        annotations = {
          "revenda.io/config-sha256" = sha256(join("", concat([local.grafana_fonte, local.grafana_provedor], values(local.grafana_paineis))))
        }
      }

      spec {
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_non_root = true
          run_as_user     = 472
          run_as_group    = 0
          fs_group        = 472
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "grafana"
          image             = var.grafana_imagem
          image_pull_policy = "IfNotPresent"

          port {
            name           = "http"
            container_port = 3000
            protocol       = "TCP"
          }

          # GF_SECURITY_ADMIN_USER / GF_SECURITY_ADMIN_PASSWORD
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.grafana_admin.metadata[0].name
            }
          }
          # Raiz somente leitura: log so no console (o padrao da imagem tambem grava em arquivo)
          env {
            name  = "GF_LOG_MODE"
            value = "console"
          }
          # Grafana 13.2 tenta atualizar os plugins pre-instalados (inclusive o do Prometheus)
          # na subida; com a raiz somente leitura a troca falha e o plugin fica sem registro
          # ("Plugin not registered"). Os plugins ficam na versao que vem na imagem.
          env {
            name  = "GF_PLUGINS_PREINSTALL_AUTO_UPDATE"
            value = "false"
          }
          env {
            name  = "GF_AUTH_ANONYMOUS_ENABLED"
            value = "true"
          }
          env {
            name  = "GF_AUTH_ANONYMOUS_ORG_ROLE"
            value = "Viewer"
          }
          env {
            name  = "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"
            value = "/var/lib/grafana/paineis/painel-visao-geral.json"
          }
          env {
            name  = "GF_USERS_DEFAULT_LANGUAGE"
            value = "pt-BR"
          }
          env {
            name  = "GF_ANALYTICS_REPORTING_ENABLED"
            value = "false"
          }
          env {
            name  = "GF_ANALYTICS_CHECK_FOR_UPDATES"
            value = "false"
          }
          env {
            name  = "GF_NEWS_NEWS_FEED_ENABLED"
            value = "false"
          }

          resources {
            requests = {
              cpu    = "50m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "1"
              memory = "512Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }

          readiness_probe {
            http_get {
              path = "/api/health"
              port = "http"
            }
            period_seconds  = 5
            timeout_seconds = 3
          }
          liveness_probe {
            http_get {
              path = "/api/health"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 15
          }

          volume_mount {
            name       = "dados"
            mount_path = "/var/lib/grafana"
          }
          volume_mount {
            name       = "paineis"
            mount_path = "/var/lib/grafana/paineis"
            read_only  = true
          }
          volume_mount {
            name       = "provisionamento"
            mount_path = "/etc/grafana/provisioning/datasources/fonte-de-dados.yml"
            sub_path   = "fonte-de-dados.yml"
            read_only  = true
          }
          volume_mount {
            name       = "provisionamento"
            mount_path = "/etc/grafana/provisioning/dashboards/paineis.yml"
            sub_path   = "paineis.yml"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "dados"
          empty_dir {}
        }
        volume {
          name = "paineis"
          config_map {
            name = kubernetes_config_map_v1.grafana_paineis.metadata[0].name
          }
        }
        volume {
          name = "provisionamento"
          config_map {
            name = kubernetes_config_map_v1.grafana_provisionamento.metadata[0].name
          }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }

  wait_for_rollout = true
  timeouts {
    create = "5m"
    update = "5m"
  }
}

resource "kubernetes_service_v1" "grafana" {
  metadata {
    name      = "grafana"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "grafana" })
  }
  spec {
    type = "NodePort"
    selector = {
      app = "grafana"
    }
    port {
      name        = "http"
      port        = 3000
      target_port = "http"
      node_port   = 30300
      protocol    = "TCP"
    }
  }
}

# ---- OpenTelemetry Collector -------------------------------------------------------

resource "kubernetes_config_map_v1" "otel_collector" {
  metadata {
    name      = "otel-collector-config"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = local.rotulos_comuns
  }
  data = {
    "config.yaml" = local.otel_collector_config
  }
}

resource "kubernetes_deployment_v1" "otel_collector" {
  metadata {
    name      = "otel-collector"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "otel-collector" })
  }

  spec {
    replicas = 1
    selector {
      match_labels = {
        app = "otel-collector"
      }
    }

    template {
      metadata {
        labels = merge(local.rotulos_comuns, { app = "otel-collector" })
        annotations = {
          "revenda.io/config-sha256" = sha256(local.otel_collector_config)
        }
      }

      spec {
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          run_as_group    = 10001
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "otel-collector"
          image             = var.otel_collector_imagem
          image_pull_policy = "IfNotPresent"
          args              = ["--config=/etc/otelcol/config.yaml"]

          port {
            name           = "otlp-grpc"
            container_port = 4317
            protocol       = "TCP"
          }
          port {
            name           = "otlp-http"
            container_port = 4318
            protocol       = "TCP"
          }
          port {
            name           = "saude"
            container_port = 13133
            protocol       = "TCP"
          }

          resources {
            requests = {
              cpu    = "50m"
              memory = "96Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "256Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }

          readiness_probe {
            http_get {
              path = "/"
              port = "saude"
            }
            period_seconds  = 5
            timeout_seconds = 3
          }
          liveness_probe {
            http_get {
              path = "/"
              port = "saude"
            }
            initial_delay_seconds = 10
            period_seconds        = 15
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/otelcol"
            read_only  = true
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map_v1.otel_collector.metadata[0].name
          }
        }
      }
    }
  }

  wait_for_rollout = true
  timeouts {
    create = "5m"
    update = "5m"
  }

  depends_on = [kubernetes_service_v1.jaeger]
}

# Endereco publicado como contrato para os dois servicos e o Kong (OTEL_EXPORTER_OTLP_ENDPOINT)
resource "kubernetes_service_v1" "otel_collector" {
  metadata {
    name      = "otel-collector"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "otel-collector" })
  }
  spec {
    type = "ClusterIP"
    selector = {
      app = "otel-collector"
    }
    port {
      name        = "otlp-grpc"
      port        = 4317
      target_port = "otlp-grpc"
      protocol    = "TCP"
    }
    port {
      name        = "otlp-http"
      port        = 4318
      target_port = "otlp-http"
      protocol    = "TCP"
    }
    port {
      name        = "saude"
      port        = 13133
      target_port = "saude"
      protocol    = "TCP"
    }
  }
}

# ---- Jaeger ------------------------------------------------------------------------

resource "kubernetes_deployment_v1" "jaeger" {
  metadata {
    name      = "jaeger"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "jaeger" })
  }

  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = {
        app = "jaeger"
      }
    }

    template {
      metadata {
        labels = merge(local.rotulos_comuns, { app = "jaeger" })
      }

      spec {
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          run_as_group    = 10001
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        # Configuracao padrao do all-in-one v2: OTLP em 4317/4318, UI em 16686, memoria.
        container {
          name              = "jaeger"
          image             = var.jaeger_imagem
          image_pull_policy = "IfNotPresent"

          port {
            name           = "otlp-grpc"
            container_port = 4317
            protocol       = "TCP"
          }
          port {
            name           = "ui"
            container_port = 16686
            protocol       = "TCP"
          }
          port {
            name           = "saude"
            container_port = 13133
            protocol       = "TCP"
          }

          resources {
            requests = {
              cpu    = "50m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }

          readiness_probe {
            http_get {
              path = "/status"
              port = "saude"
            }
            period_seconds  = 5
            timeout_seconds = 3
          }
          liveness_probe {
            http_get {
              path = "/status"
              port = "saude"
            }
            initial_delay_seconds = 10
            period_seconds        = 15
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }

  wait_for_rollout = true
  timeouts {
    create = "5m"
    update = "5m"
  }
}

resource "kubernetes_service_v1" "jaeger" {
  metadata {
    name      = "jaeger"
    namespace = kubernetes_namespace_v1.observabilidade.metadata[0].name
    labels    = merge(local.rotulos_comuns, { app = "jaeger" })
  }
  spec {
    type = "NodePort"
    selector = {
      app = "jaeger"
    }
    port {
      name        = "otlp-grpc"
      port        = 4317
      target_port = "otlp-grpc"
      protocol    = "TCP"
    }
    port {
      name        = "ui"
      port        = 16686
      target_port = "ui"
      node_port   = 30686
      protocol    = "TCP"
    }
  }
}
