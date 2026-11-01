# O cluster kind e criado FORA do Terraform, pela CLI kind (infra/kind/cluster.yaml), no
# CD e em scripts/windows/04-subir-ambiente.ps1 (ADR-006). Os providers usam o contexto
# kind-<cluster> do kubeconfig que o `kind create cluster` / `kind export kubeconfig`
# grava. Como o cluster ja existe antes do plan, basta um unico `terraform apply`.

locals {
  # Vazio = ~/.kube/config (no Windows, %USERPROFILE%\.kube\config)
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : pathexpand("~/.kube/config")
  kube_contexto   = "kind-${var.cluster_nome}"
}

provider "kubernetes" {
  config_path    = local.kubeconfig_path
  config_context = local.kube_contexto
}

provider "helm" {
  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = local.kube_contexto
  }
}
