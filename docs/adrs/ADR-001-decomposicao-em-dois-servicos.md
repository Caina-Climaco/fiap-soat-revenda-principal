# ADR-001: Decomposição em serviço principal e serviço de venda

**Status:** Aceito
**Data:** 2026-11-01
**Substitui:** ADR-002 da Fase 3 (monólito modular)

## Contexto

O enunciado da Fase 4 exige que "os endpoints de listagem e compras de veículos" fiquem "isolados em um serviço único (serviço de venda de veículos), e rodando com um banco de dados isolado", e que o restante fique no "software principal", com outro banco. A comunicação entre os dois deve ser HTTP. A entrega pede exatamente dois repositórios.

Na Fase 3, Catálogo e Vendas eram módulos de um monólito modular ([ADR-002 da Fase 3](https://github.com/Caina-Climaco/fiap-soat-revenda-veiculos/blob/main/docs/adrs/ADR-002-monolito-modular.md)). Aquele ADR já previa a extração: "basta trocar o adaptador in-process por um cliente HTTP e introduzir uma saga".

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Dois serviços: principal (Catálogo + Pagamentos) e venda (Vendas)** | Atende o enunciado à letra; o principal reaproveita quase todo o código da Fase 3; Pagamentos fica com quem gera a cobrança | O principal tem dois contextos |
| Três serviços (Catálogo, Pagamentos, Vendas) | Um contexto por serviço | O enunciado pede dois repositórios; um terceiro serviço dobraria infraestrutura e pipelines sem ganho para a avaliação |
| Webhook no serviço de venda | Menos uma chamada HTTP no fluxo de pagamento | O enunciado isola "listagem e compras"; o webhook é "outra funcionalidade". Deixaria o serviço isolado exposto a um parceiro externo |
| Listagens consultando o principal por HTTP (API Composition) | Sem réplica | O serviço de venda deixaria de ser isolado: a vitrine cairia junto com o principal, justamente nos picos que motivaram o isolamento |

## Decisão

- **Serviço principal** (`revenda-principal`, repositório `fiap-soat-revenda-principal`): contextos **Catálogo** (cadastro, edição, versão, bloqueio e venda do veículo) e **Pagamentos** (cobrança, código do pagamento, webhook do processador). PostgreSQL próprio, com um schema por contexto.
- **Serviço de venda** (`revenda-vendas`, repositório `fiap-soat-revenda-vendas`): contexto **Vendas** (vitrine, compra, reserva, efetivação e orquestração da saga). MongoDB próprio, com uma réplica de leitura dos anúncios.
- Cada serviço tem repositório, pipeline, imagem, namespace, banco, state do Terraform e runner de CD próprios.
- Nenhum serviço acessa o banco do outro; toda interação é por HTTP, por contratos versionados.

## Consequências

### Positivas
- Isolamento real: a vitrine e a consulta de compra funcionam com o principal fora do ar.
- O serviço de venda escala de forma independente (HPA próprio) nos picos de listagem e compra.
- O reaproveitamento da Fase 3 é alto no principal; o domínio de Vendas da Fase 3 vira a base do serviço de venda.

### Negativas
- A compra deixa de ser uma transação ACID: passa a exigir saga, idempotência e compensações.
- Mais componentes para implantar, observar e demonstrar.
- A vitrine é eventualmente consistente com o Catálogo.

## Mitigações
- Saga orquestrada com estado durável ([ADR-002 do serviço de venda](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/blob/main/docs/adrs/ADR-002-saga-orquestrada.md)).
- Outbox para a réplica e para o repasse do pagamento ([ADR-003](ADR-003-http-com-outbox.md)).
- Versão do anúncio no pedido de compra, para que a consistência eventual da vitrine nunca venda um anúncio desatualizado.
- Tracing distribuído para enxergar o fluxo entre os serviços ([ADR-007](ADR-007-tracing-distribuido-opentelemetry.md)).
