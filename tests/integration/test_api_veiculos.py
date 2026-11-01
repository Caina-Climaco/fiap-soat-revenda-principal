"""Rotas do gestor (/api/v1/veiculos): autorização, validação, edição e outbox."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from apoio.api import GESTOR, Api, tipo_problema
from apoio.cenario import VEICULO


def _outbox(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conexao:
        linhas = conexao.execute(
            text(
                "SELECT tipo, chave, metodo, caminho, corpo, status "
                "FROM mensageria.outbox ORDER BY id"
            )
        )
        return [dict(linha._mapping) for linha in linhas]


def test_cadastro_responde_201_com_location_e_grava_anuncio_na_outbox(
    cliente_http: TestClient, engine: Engine
) -> None:
    resposta = cliente_http.post("/api/v1/veiculos", json=VEICULO, headers=GESTOR)
    assert resposta.status_code == 201
    corpo = resposta.json()
    assert resposta.headers["Location"] == f"/api/v1/veiculos/{corpo['id']}"
    assert (corpo["status"], corpo["versao"], corpo["compra_id"]) == ("A_VENDA", 1, None)
    assert corpo["preco"] == "119900.00"
    [mensagem] = _outbox(engine)
    assert mensagem["tipo"] == "ANUNCIO"
    assert mensagem["caminho"] == f"/internal/v1/anuncios/{corpo['id']}"
    assert mensagem["corpo"]["versao"] == 1  # type: ignore[index]
    assert mensagem["status"] == "PENDENTE"


@pytest.mark.parametrize(
    ("cabecalhos", "status", "tipo"),
    [
        ({}, 401, "nao-autenticado"),
        ({"X-Consumer-Username": "gateway-pagamento"}, 403, "acesso-negado"),
    ],
)
def test_rotas_do_gestor_exigem_o_consumer(
    cliente_http: TestClient, cabecalhos: dict[str, str], status: int, tipo: str
) -> None:
    for metodo, caminho in [
        ("POST", "/api/v1/veiculos"),
        ("GET", "/api/v1/veiculos"),
        ("GET", f"/api/v1/veiculos/{uuid.uuid4()}"),
        ("PATCH", f"/api/v1/veiculos/{uuid.uuid4()}"),
    ]:
        resposta = cliente_http.request(metodo, caminho, json=VEICULO, headers=cabecalhos)
        assert resposta.status_code == status, (metodo, caminho)
        assert tipo_problema(resposta) == tipo


def test_validacao_em_portugues(cliente_http: TestClient) -> None:
    resposta = cliente_http.post(
        "/api/v1/veiculos",
        json={**VEICULO, "ano": 1900, "preco": "0", "status": "VENDIDO"},
        headers=GESTOR,
    )
    assert resposta.status_code == 422
    campos = {e["campo"] for e in resposta.json()["erros"]}
    assert campos == {"ano", "preco", "status"}


def test_ano_acima_do_limite_e_validado_no_dominio(cliente_http: TestClient) -> None:
    resposta = cliente_http.post("/api/v1/veiculos", json={**VEICULO, "ano": 2099}, headers=GESTOR)
    assert resposta.status_code == 422
    assert resposta.json()["erros"] == [
        {"campo": "ano", "mensagem": "deve estar entre 1950 e 2027"}
    ]


def test_edicao_gera_nova_versao_e_nova_mensagem(api: Api, engine: Engine) -> None:
    veiculo = api.cadastrar()
    resposta = api.editar(veiculo["id"], preco="110000.00", cor="Preto")
    assert resposta.status_code == 200
    assert (resposta.json()["versao"], resposta.json()["cor"]) == (2, "Preto")
    assert api.editar(veiculo["id"], cor="Preto").json()["versao"] == 2  # sem mudança real
    versoes = [m["corpo"]["versao"] for m in _outbox(engine)]  # type: ignore[index]
    assert versoes == [1, 2]


def test_edicao_de_veiculo_bloqueado_e_recusada(api: Api) -> None:
    """RN-02: depois do T2, o gestor não altera o preço que o comprador está pagando."""
    veiculo = api.cadastrar()
    api.bloquear(veiculo["id"], str(uuid.uuid4()))
    resposta = api.editar(veiculo["id"], preco="1.00")
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "veiculo-nao-editavel"
    assert api.veiculo(veiculo["id"])["preco"] == "119900.00"


def test_edicao_invalida_e_inexistente(api: Api) -> None:
    veiculo = api.cadastrar()
    assert api.editar(veiculo["id"]).status_code == 422
    assert api.editar(veiculo["id"], marca=None).status_code == 422
    inexistente = api.editar(str(uuid.uuid4()), cor="Azul")
    assert inexistente.status_code == 404
    assert tipo_problema(inexistente) == "veiculo-nao-encontrado"


def test_listagem_da_loja_com_filtro_e_paginacao(api: Api, cliente_http: TestClient) -> None:
    primeiro = api.cadastrar(modelo="Primeiro")
    api.cadastrar(modelo="Segundo")
    api.bloquear(primeiro["id"], str(uuid.uuid4()))
    todos = cliente_http.get("/api/v1/veiculos", headers=GESTOR).json()
    assert todos["total"] == 2
    bloqueados = cliente_http.get(
        "/api/v1/veiculos", params={"status": "BLOQUEADO"}, headers=GESTOR
    ).json()
    assert [v["id"] for v in bloqueados["itens"]] == [primeiro["id"]]
    pagina = cliente_http.get(
        "/api/v1/veiculos", params={"limite": 1, "deslocamento": 1}, headers=GESTOR
    ).json()
    assert (pagina["total"], len(pagina["itens"]), pagina["limite"]) == (2, 1, 1)
    invalido = cliente_http.get("/api/v1/veiculos", params={"limite": 101}, headers=GESTOR)
    assert invalido.status_code == 422
