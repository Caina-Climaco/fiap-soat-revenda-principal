# 1. Visão geral

Este documento apresenta o problema de negócio, o objetivo, o escopo, os atores, as premissas e as restrições da solução **Revenda de Veículos** na versão distribuída, desenvolvida como Trabalho Substitutivo da Fase 4 do Tech Challenge (FIAP PósTech, Software Architecture). É a porta de entrada da documentação da solução, que fica neste repositório (serviço principal); o repositório do serviço de venda ([fiap-soat-revenda-vendas](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas)) documenta o que é próprio dele e aponta para cá no que é comum.

## 1.1 Problema de negócio

Uma empresa de revenda de veículos automotores quer vender pela internet. A plataforma precisa permitir que:

- a loja cadastre e edite os veículos anunciados (marca, modelo, ano, cor e preço);
- uma pessoa compre um veículo informando o CPF, com o registro da data da venda;
- o processador de pagamentos informe, a partir do código do pagamento, se o pagamento foi efetuado ou cancelado;
- qualquer pessoa consulte os veículos à venda e os vendidos, ordenados do mais barato ao mais caro.

O negócio espera picos repentinos de acesso nas listagens e nas compras. Por isso o enunciado exige que esses endpoints fiquem **isolados num serviço próprio** (serviço de venda de veículos), com **banco de dados isolado**, e que ele se comunique com o software principal **por requisições HTTP**, cada um dentro dos seus limites de responsabilidade.

A interface (front-end) é de outros times. Este projeto entrega o back-end: os dois serviços e a infraestrutura que os executa.

## 1.2 Objetivo

Entregar dois serviços implantáveis de forma independente que, juntos, suportem o ciclo completo de venda (cadastro do veículo, compra, cobrança, confirmação ou cancelamento do pagamento e efetivação) com:

1. o serviço de venda isolado, com banco próprio (MongoDB) e capaz de atender listagens mesmo com o serviço principal fora do ar;
2. consistência entre os serviços sem transação distribuída, por meio do **padrão Saga** orquestrado, com compensações;
3. comunicação HTTP resiliente (timeout, retry, circuit breaker, idempotência e outbox);
4. toda mudança passando por Pull Request, com pipeline de CI/CD por repositório, cobertura mínima de 80% e verificação de qualidade no SonarCloud;
5. observabilidade dos fluxos distribuídos (métricas, logs correlacionados e tracing distribuído);
6. documentação de arquitetura (DDD, C4, ADRs, requisitos, testes) rastreável até o código.

## 1.3 Escopo

### 1.3.1 Dentro do escopo

| Item | Observação |
|---|---|
| **Serviço principal** `revenda-principal` (FastAPI, PostgreSQL), contextos **Catálogo** e **Pagamentos** | Cadastro e edição de veículos, cobrança, webhook do processador de pagamentos |
| **Serviço de venda** `revenda-vendas` (FastAPI, MongoDB), contexto **Vendas** | Listagens públicas, compra, consulta da compra, orquestração da saga |
| Réplica de leitura dos anúncios no serviço de venda | Alimentada pelo principal por HTTP, via outbox |
| Saga orquestrada da compra, com compensações | Estado persistido; recuperação após falha de pod |
| Reserva com expiração | TTL padrão de 30 min; compensação automática |
| Autorização na borda pelo **Kong** (key-auth + ACL) | Consumers `gestor` e `gateway-pagamento`; rotas internas sem rota no Kong |
| Infraestrutura local: cluster **kind** compartilhado, **Terraform** e kustomize | Um namespace, um state e um runner de CD por repositório |
| Observabilidade: **Prometheus**, **Grafana**, **OpenTelemetry Collector** e **Jaeger** | Painel de saga e de resiliência; traces atravessando os dois serviços |
| CI no runner hospedado do GitHub e CD no runner self-hosted | Deploy automático a cada merge na `main` de cada repositório |
| Testes de unidade, integração, contrato, BDD e ponta a ponta | Cobertura mínima de 80% por serviço, com SonarCloud no CI |
| Documentação, README, vídeo e PDF de entrega | |

### 1.3.2 Fora do escopo

| Item | Motivo |
|---|---|
| Front-end | Outros times; a interação é demonstrada pelo Swagger UI e por `curl` |
| Cadastro e autenticação de compradores | O enunciado da Fase 4 identifica o comprador apenas pelo CPF; o Keycloak da Fase 3 sai da solução ([ADR-005](adrs/ADR-005-sem-keycloak-autorizacao-na-borda.md)) |
| Processador de pagamento real, estorno e conciliação | O processador é simulado; ele só chama o webhook |
| Mensageria (broker) | O enunciado fixa HTTP entre os serviços; a outbox entrega a mesma garantia de entrega ([ADR-003](adrs/ADR-003-http-com-outbox.md)) |
| Desistência do comprador e cancelamento manual pela loja | Sem autenticação do comprador não há como provar que quem desiste é quem comprou; a compra termina por pagamento, cancelamento do pagamento ou expiração |
| Placa, chassi, RENAVAM, fotos, filtros avançados, múltiplas lojas, notificações | Não pedidos (mantidos fora como na Fase 3) |
| Service mesh com mTLS entre serviços | Evolução; nesta entrega a proteção é NetworkPolicy + token de serviço |
| Nuvem pública | Restrição de custo; ambiente local |

## 1.4 Atores

| Ator | Descrição | Como interage | Credencial |
|---|---|---|---|
| **Gestor da loja** | Funcionário que mantém o estoque | Cadastra e edita veículos no serviço principal | Consumer `gestor` no Kong (header `apikey`) |
| **Comprador** | Pessoa que compra um veículo informando o CPF | Lista veículos e compra no serviço de venda; consulta a própria compra pelo identificador | Nenhuma |
| **Visitante** | Qualquer pessoa | Consulta as listagens | Nenhuma |
| **Processador de pagamentos (simulado)** | Sistema externo que cobra o comprador | Chama o webhook do serviço principal com o código do pagamento e o resultado | Consumer `gateway-pagamento` no Kong e segredo do webhook |
| **Avaliador / operador** | Professor ou o próprio autor | Executa o ambiente, acompanha pipelines, painéis e traces | Acesso aos repositórios e ao ambiente local |

## 1.5 Premissas

1. O trabalho é **individual**. A proteção da `main` exige PR e checks verdes, com zero aprovações obrigatórias (exceção consciente, como na Fase 3).
2. O pagamento acontece fora da plataforma; ela só conhece o **código do pagamento** e recebe o resultado.
3. O comprador paga o preço anunciado no momento em que inicia a compra.
4. Um veículo é uma unidade física única: só pode ter uma compra ativa por vez.
5. O avaliador dispõe de Docker, kind, kubectl e Terraform para reproduzir o ambiente (primeiro o serviço principal, que traz a plataforma, depois o serviço de venda), ou assiste ao vídeo.
6. O PC do autor fica ligado nos momentos de deploy; os runners self-hosted não são de alta disponibilidade.

## 1.6 Restrições

| Restrição | Consequência |
|---|---|
| Prazo de entrega: **15/11/2026** | Reaproveitamento da base da Fase 3 (stack, infraestrutura, pipelines, documentação) |
| Dois repositórios, cada um com CI/CD, testes e banco próprios | Serviço principal com PostgreSQL; serviço de venda com MongoDB |
| Comunicação entre serviços por HTTP | Sem broker; outbox transacional com entrega HTTP e saga orquestrada por chamadas HTTP |
| Cobertura mínima de 80% e todos os testes passando | Gate no CI de cada repositório e Quality Gate no SonarCloud |
| Deploy baseado em Pull Request, disparado pelo merge na `main` | CD só em `push` na `main` |
| Sem nuvem pública | kind local; imagens carregadas no nó sem registry |
| Repositórios públicos | Exigência da avaliação; também habilita o plano gratuito do SonarCloud |

## 1.7 Mapa da documentação

| # | Documento | Conteúdo |
|---|---|---|
| 01 | [Visão geral](01-visao-geral.md) | Este documento |
| 02 | [Modelagem DDD](02-modelagem-ddd.md) | Domain Storytelling, Event Storming, linguagem ubíqua, contextos, agregados, máquinas de estado, saga e regras de negócio |
| 03 | [Requisitos](03-requisitos.md) | Requisitos funcionais e não funcionais |
| 05 | [Contratos da API](05-api.md) | Rotas públicas e internas dos dois serviços, autenticação, roteamento no Kong e catálogo de erros |
| | [ADRs](adrs/README.md) | Decisões de arquitetura da solução |
| | [ADRs do serviço de venda](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/tree/main/docs/adrs) | Decisões próprias do serviço de venda (MongoDB, saga, réplica, CPF) |

Os demais documentos (arquitetura C4, dados, segurança e LGPD, CI/CD, testes, observabilidade, roteiro do vídeo e DAS) seguem a mesma numeração da Fase 3 e são escritos nas próximas etapas.
