"""Mensagens que o Catálogo publica na outbox (contrato: docs/05-api.md, seção 5.7)."""

from __future__ import annotations

from principal.catalogo.domain.veiculo import Veiculo
from principal.shared.outbox import MensagemSaida

TIPO_ANUNCIO = "ANUNCIO"


def mensagem_anuncio(veiculo: Veiculo) -> MensagemSaida:
    """Réplica do anúncio no serviço de venda (ADR-003 do serviço de venda).

    Leva só dados públicos do veículo e a versão; o receptor descarta versões antigas.
    """
    return MensagemSaida(
        tipo=TIPO_ANUNCIO,
        chave=f"veiculo:{veiculo.id}",
        metodo="PUT",
        caminho=f"/internal/v1/anuncios/{veiculo.id}",
        corpo={
            "versao": veiculo.versao,
            "marca": veiculo.marca,
            "modelo": veiculo.modelo,
            "ano": veiculo.ano,
            "cor": veiculo.cor,
            "preco": f"{veiculo.preco:.2f}",
            "criado_em": veiculo.criado_em.isoformat(),
        },
    )
