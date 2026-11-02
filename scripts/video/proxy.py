#!/usr/bin/env python3
"""Proxy TCP minimo (asyncio, so biblioteca padrao) para o container de gravacao.

Dentro do container o navegador usa os mesmos enderecos do PC: http://localhost:8080 (Kong),
:3000 (Grafana), :9090 (Prometheus) e :16686 (Jaeger). O container esta na rede docker
"kind" e alcanca os NodePorts pelo nome do no (revenda-control-plane:30080 e outros); este
proxy liga um ao outro.

Uso:
  python3 proxy.py 8080=revenda-control-plane:30080 3000=revenda-control-plane:30300

Cada argumento e PORTA_LOCAL=HOST:PORTA. Escuta so em 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

TAMANHO_BLOCO = 64 * 1024


async def _copiar(origem: asyncio.StreamReader, destino: asyncio.StreamWriter) -> None:
    try:
        while True:
            dados = await origem.read(TAMANHO_BLOCO)
            if not dados:
                break
            destino.write(dados)
            await destino.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            if destino.can_write_eof():
                destino.write_eof()
        except (OSError, RuntimeError):
            pass


def _tratador(host: str, porta: int):
    async def tratar(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            leitor_remoto, escritor_remoto = await asyncio.wait_for(
                asyncio.open_connection(host, porta), timeout=10
            )
        except (TimeoutError, OSError) as erro:
            print(f"proxy: sem conexao com {host}:{porta}: {erro}", file=sys.stderr, flush=True)
            escritor.close()
            return
        await asyncio.gather(_copiar(leitor, escritor_remoto), _copiar(leitor_remoto, escritor))
        for w in (escritor, escritor_remoto):
            with contextlib.suppress(OSError):
                w.close()

    return tratar


def _interpretar(argumento: str) -> tuple[int, str, int]:
    local, _, remoto = argumento.partition("=")
    host, _, porta = remoto.rpartition(":")
    if not (local.isdigit() and host and porta.isdigit()):
        raise SystemExit(f"argumento invalido: {argumento!r} (esperado PORTA=HOST:PORTA)")
    return int(local), host, int(porta)


async def principal(mapas: list[tuple[int, str, int]]) -> None:
    servidores = []
    for local, host, porta in mapas:
        servidor = await asyncio.start_server(_tratador(host, porta), "127.0.0.1", local)
        servidores.append(servidor)
        print(f"proxy: 127.0.0.1:{local} -> {host}:{porta}", flush=True)
    parar = asyncio.Event()
    laco = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            laco.add_signal_handler(sinal, parar.set)
    await parar.wait()
    for servidor in servidores:
        servidor.close()
        await servidor.wait_closed()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(principal([_interpretar(a) for a in sys.argv[1:]]))
