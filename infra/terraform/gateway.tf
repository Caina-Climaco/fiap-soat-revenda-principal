# API Gateway: Kong em modo DB-less, no namespace gateway (ADR-006).
# E a unica porta de entrada publica no host (localhost:8080 -> NodePort 30080) para os DOIS
# servicos, que sao ClusterIP. Configuracao declarativa em infra/kong/kong.yml.tftpl:
# servicos, rotas (docs/05-api.md, secao 5.3), plugins (rate limiting, correlation-id,
# request-size-limiting, prometheus, opentelemetry) e os consumers gestor e
# gateway-pagamento (key-auth + ACL).

locals {
  kong_rotulos = merge(local.rotulos_comuns, {
    app                           = "kong"
    "app.kubernetes.io/name"      = "kong"
    "app.kubernetes.io/component" = "api-gateway"
  })

  kong_config = templatefile("${path.module}/../kong/kong.yml.tftpl", {
    chave_gestor            = random_password.chave_gestor.result
    chave_gateway_pagamento = random_password.chave_gateway_pagamento.result
    namespace_principal     = kubernetes_namespace_v1.principal.metadata[0].name
    namespace_vendas        = var.namespace_vendas
    limite_geral_minuto     = var.kong_limite_geral_minuto
    limite_compra_minuto    = var.kong_limite_compra_minuto
    otlp_traces             = "http://otel-collector.observabilidade.svc.cluster.local:4318/v1/traces"
  })
}

resource "kubernetes_namespace_v1" "gateway" {
  metadata {
    name   = "gateway"
    labels = local.rotulos_comuns
  }
}

# Secret (e nao ConfigMap): a configuracao contem as chaves dos consumers.
resource "kubernetes_secret_v1" "kong_config" {
  metadata {
    name      = "kong-config"
    namespace = kubernetes_namespace_v1.gateway.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    "kong.yml" = local.kong_config
  }
}

resource "kubernetes_deployment_v1" "kong" {
  metadata {
    name      = "kong"
    namespace = kubernetes_namespace_v1.gateway.metadata[0].name
    labels    = local.kong_rotulos
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "kong"
      }
    }

    template {
      metadata {
        labels = local.kong_rotulos
        annotations = {
          # Reinicia o pod quando a configuracao declarativa muda
          "revenda.io/kong-config-sha256" = sha256(local.kong_config)
          "prometheus.io/scrape"          = "true"
          "prometheus.io/port"            = "8100"
          "prometheus.io/path"            = "/metrics"
        }
      }

      spec {
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "kong"
          image             = var.kong_imagem
          image_pull_policy = "IfNotPresent"

          port {
            name           = "proxy"
            container_port = 8000
            protocol       = "TCP"
          }
          port {
            name           = "status"
            container_port = 8100
            protocol       = "TCP"
          }

          env {
            name  = "KONG_DATABASE"
            value = "off"
          }
          env {
            name  = "KONG_DECLARATIVE_CONFIG"
            value = "/kong/declarativo/kong.yml"
          }
          env {
            name  = "KONG_PREFIX"
            value = "/kong_prefix"
          }
          env {
            name  = "KONG_PROXY_LISTEN"
            value = "0.0.0.0:8000"
          }
          # Admin API so dentro do pod (somente leitura em DB-less); status e metricas em 8100
          env {
            name  = "KONG_ADMIN_LISTEN"
            value = "127.0.0.1:8001"
          }
          env {
            # Kong Manager (GUI, 0.0.0.0:8002 por padrao no 3.x) desligado
            name  = "KONG_ADMIN_GUI_LISTEN"
            value = "off"
          }
          env {
            name  = "KONG_STATUS_LISTEN"
            value = "0.0.0.0:8100"
          }
          env {
            name  = "KONG_NGINX_WORKER_PROCESSES"
            value = "2"
          }
          env {
            name  = "KONG_PROXY_ACCESS_LOG"
            value = "/dev/stdout"
          }
          env {
            name  = "KONG_PROXY_ERROR_LOG"
            value = "/dev/stderr"
          }
          env {
            name  = "KONG_ADMIN_ACCESS_LOG"
            value = "/dev/stdout"
          }
          env {
            name  = "KONG_ADMIN_ERROR_LOG"
            value = "/dev/stderr"
          }
          # Respostas de erro do proprio Kong em JSON
          env {
            name  = "KONG_ERROR_DEFAULT_TYPE"
            value = "application/json"
          }
          # Tracing (ADR-007): o trace comeca na borda; o plugin opentelemetry exporta para o
          # Collector e propaga o traceparent W3C para os servicos. Amostragem de 100% (local).
          env {
            name  = "KONG_TRACING_INSTRUMENTATIONS"
            value = "request,balancer,http_client"
          }
          env {
            name  = "KONG_TRACING_SAMPLING_RATE"
            value = "1.0"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "192Mi"
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
              path = "/status/ready"
              port = "status"
            }
            period_seconds    = 5
            timeout_seconds   = 3
            failure_threshold = 3
          }

          liveness_probe {
            http_get {
              path = "/status"
              port = "status"
            }
            initial_delay_seconds = 10
            period_seconds        = 15
            timeout_seconds       = 3
            failure_threshold     = 4
          }

          volume_mount {
            name       = "config"
            mount_path = "/kong/declarativo"
            read_only  = true
          }
          volume_mount {
            name       = "prefixo"
            mount_path = "/kong_prefix"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "config"
          secret {
            secret_name = kubernetes_secret_v1.kong_config.metadata[0].name
          }
        }
        volume {
          name = "prefixo"
          empty_dir {}
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

# localhost:8080 -> NodePort 30080 (infra/kind/cluster.yaml) -> Kong :8000
resource "kubernetes_service_v1" "kong" {
  metadata {
    name      = "kong"
    namespace = kubernetes_namespace_v1.gateway.metadata[0].name
    labels    = local.kong_rotulos
  }

  spec {
    type = "NodePort"
    selector = {
      app = "kong"
    }
    port {
      name        = "proxy"
      port        = 80
      target_port = "proxy"
      node_port   = 30080
      protocol    = "TCP"
    }
  }
}

# Status/metricas do Kong (so dentro do cluster; o Prometheus coleta pelo pod)
resource "kubernetes_service_v1" "kong_status" {
  metadata {
    name      = "kong-status"
    namespace = kubernetes_namespace_v1.gateway.metadata[0].name
    labels    = local.kong_rotulos
  }

  spec {
    type = "ClusterIP"
    selector = {
      app = "kong"
    }
    port {
      name        = "status"
      port        = 8100
      target_port = "status"
      protocol    = "TCP"
    }
  }
}
