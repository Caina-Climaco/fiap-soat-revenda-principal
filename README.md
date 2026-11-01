# Revenda de Veículos: serviço principal

Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4.
Autor: Cainã Clímaco, RM366473 (trabalho individual).

> **Esta entrega tem dois repositórios.** Este é o do **serviço principal** (Catálogo e Pagamentos, PostgreSQL), que também mantém a plataforma comum (Kong e observabilidade) e a documentação da solução. O **serviço de venda** (listagens e compras, MongoDB, orquestração da saga) está em **[fiap-soat-revenda-vendas](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas)**.

**Em construção.** O desenho da solução já está documentado:

| Documento | Conteúdo |
|---|---|
| [01. Visão geral](docs/01-visao-geral.md) | Problema, escopo, atores, premissas e restrições |
| [02. Modelagem DDD](docs/02-modelagem-ddd.md) | Domain Storytelling, Event Storming, contextos, agregados e a saga da compra |
| [03. Requisitos](docs/03-requisitos.md) | Requisitos funcionais e não funcionais |
| [05. Contratos da API](docs/05-api.md) | Rotas públicas e internas dos dois serviços |
| [ADRs](docs/adrs/README.md) | Decisões de arquitetura da solução |
