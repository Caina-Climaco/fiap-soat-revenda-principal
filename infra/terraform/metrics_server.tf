# metrics-server (necessario para os HPAs dos dois servicos). No kind os certificados do
# kubelet sao autoassinados, dai o --kubelet-insecure-tls (aceitavel so em ambiente local).
resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = var.metrics_server_chart_versao

  values = [
    yamlencode({
      args = ["--kubelet-insecure-tls"]
      resources = {
        requests = { cpu = "50m", memory = "64Mi" }
        limits   = { cpu = "250m", memory = "200Mi" }
      }
    })
  ]

  wait    = true
  timeout = 300
}
