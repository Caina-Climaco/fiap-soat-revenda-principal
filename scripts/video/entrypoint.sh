#!/usr/bin/env bash
# Entrypoint do container de gravacao (imagem revenda-video, base Playwright Python).
# 1. Sobe o proxy TCP: 127.0.0.1:8080 -> Kong, 3000 -> Grafana, 9090 -> Prometheus e
#    16686 -> Jaeger (NodePorts do kind), para o navegador usar os mesmos enderecos do PC.
# 2. Espera as portas abrirem e roda gravar.py com os argumentos recebidos.
# Variaveis: VIDEO_NO_KIND (padrao revenda-control-plane), VIDEO_SEM_PROXY=1 desliga o proxy.
set -euo pipefail

NO_KIND="${VIDEO_NO_KIND:-revenda-control-plane}"
DIR="$(cd "$(dirname "$0")" && pwd)"
pid_proxy=""

if [ "${VIDEO_SEM_PROXY:-0}" != "1" ]; then
  python3 "$DIR/proxy.py" "8080=${NO_KIND}:30080" "3000=${NO_KIND}:30300" \
    "9090=${NO_KIND}:30900" "16686=${NO_KIND}:30686" &
  pid_proxy=$!
  trap 'kill "$pid_proxy" 2>/dev/null || true' EXIT
  python3 - <<'PY'
import socket, sys, time
for porta in (8080, 3000, 9090, 16686):
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        sys.exit(f"proxy nao abriu a porta {porta}")
PY
fi

python3 "$DIR/gravar.py" "$@"
