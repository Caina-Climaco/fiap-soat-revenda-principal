"""Instantes e dados de exemplo comuns aos testes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

AS_10H = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
PRECO = Decimal("119900.00")

VEICULO = {
    "marca": "Toyota",
    "modelo": "Corolla XEi",
    "ano": 2022,
    "cor": "Prata",
    "preco": "119900.00",
}
