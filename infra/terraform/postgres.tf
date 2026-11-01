# PostgreSQL 16 do servico principal (ADR-002): principal-db (ns principal) com os schemas
# catalogo, pagamentos e mensageria, sem dados pessoais. O servico de venda usa outro banco
# (MongoDB, no namespace vendas, outro repositorio).
# StatefulSet + PVC (storage class "standard" do kind = local-path) + Service.

locals {
  bancos = {
    principal = {
      nome          = "principal-db"
      namespace     = kubernetes_namespace_v1.principal.metadata[0].name
      secret        = kubernetes_secret_v1.principal_db_credentials.metadata[0].name
      chave_usuario = "DB_USER"
      chave_senha   = "DB_PASSWORD"
      banco         = "revenda_principal" # igual a DB_NAME do Secret principal-db-credentials
      tipo_servico  = var.expor_banco ? "NodePort" : "ClusterIP"
      node_port     = var.expor_banco ? 30432 : null
    }
  }

  # Readiness/liveness: aceita conexao TCP local no banco da aplicacao. Durante a
  # inicializacao (initdb) o entrypoint oficial so escuta no socket unix, entao o pod
  # so fica pronto quando o servidor definitivo sobe.
  pg_isready = ["sh", "-c", "pg_isready -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -h 127.0.0.1 -p 5432"]
}

resource "kubernetes_stateful_set_v1" "postgres" {
  for_each = local.bancos

  metadata {
    name      = each.value.nome
    namespace = each.value.namespace
    labels = merge(local.rotulos_comuns, {
      app                           = each.value.nome
      "app.kubernetes.io/name"      = "postgresql"
      "app.kubernetes.io/instance"  = each.value.nome
      "app.kubernetes.io/component" = "database"
    })
  }

  spec {
    service_name = each.value.nome
    replicas     = 1

    selector {
      match_labels = {
        app = each.value.nome
      }
    }

    template {
      metadata {
        labels = merge(local.rotulos_comuns, {
          app                           = each.value.nome
          "app.kubernetes.io/name"      = "postgresql"
          "app.kubernetes.io/instance"  = each.value.nome
          "app.kubernetes.io/component" = "database"
        })
      }

      spec {
        automount_service_account_token  = false
        enable_service_links             = false
        termination_grace_period_seconds = 30

        # UID/GID 70 = usuario "postgres" da imagem alpine. O diretorio criado pelo
        # local-path do kind tem permissao 0777, entao o initdb roda sem root.
        security_context {
          run_as_non_root = true
          run_as_user     = 70
          run_as_group    = 70
          fs_group        = 70
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "postgres"
          image             = var.postgres_imagem
          image_pull_policy = "IfNotPresent"

          port {
            name           = "postgres"
            container_port = 5432
            protocol       = "TCP"
          }

          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = each.value.secret
                key  = each.value.chave_usuario
              }
            }
          }
          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = each.value.secret
                key  = each.value.chave_senha
              }
            }
          }
          env {
            name  = "POSTGRES_DB"
            value = each.value.banco
          }
          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }

          resources {
            requests = {
              cpu    = "100m"
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

          startup_probe {
            exec {
              command = local.pg_isready
            }
            period_seconds    = 5
            timeout_seconds   = 5
            failure_threshold = 60
          }

          readiness_probe {
            exec {
              command = local.pg_isready
            }
            period_seconds    = 5
            timeout_seconds   = 5
            failure_threshold = 3
          }

          liveness_probe {
            exec {
              command = local.pg_isready
            }
            period_seconds    = 15
            timeout_seconds   = 5
            failure_threshold = 6
          }

          volume_mount {
            name       = "dados"
            mount_path = "/var/lib/postgresql/data"
          }
          volume_mount {
            name       = "socket"
            mount_path = "/var/run/postgresql"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "socket"
          empty_dir {}
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }

    volume_claim_template {
      metadata {
        name = "dados"
      }
      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = "standard"
        resources {
          requests = {
            storage = "1Gi"
          }
        }
      }
    }
  }

  wait_for_rollout = true

  timeouts {
    create = "10m"
    update = "10m"
  }
}

resource "kubernetes_service_v1" "postgres" {
  for_each = local.bancos

  metadata {
    name      = each.value.nome
    namespace = each.value.namespace
    labels = merge(local.rotulos_comuns, {
      app                           = each.value.nome
      "app.kubernetes.io/name"      = "postgresql"
      "app.kubernetes.io/component" = "database"
    })
  }

  spec {
    type = each.value.tipo_servico
    selector = {
      app = each.value.nome
    }
    port {
      name        = "postgres"
      port        = 5432
      target_port = "postgres"
      protocol    = "TCP"
      node_port   = each.value.node_port
    }
  }
}
