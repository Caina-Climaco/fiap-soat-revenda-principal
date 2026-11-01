# 10. Plano de execução

Organização da entrega do Trabalho Substitutivo (prazo: 15/11/2026): backlog por épico, critérios de pronto, cronograma e riscos. Toda mudança entrou por Pull Request nos dois repositórios.

## 10.1 Backlog e entregas

| Épico | Escopo | Critério de aceite | Entregue em |
|---|---|---|---|
| EP-01 Modelagem | Domain Storytelling, Event Storming, contextos, agregados, saga, requisitos, ADRs | docs 01, 02, 03, 05 e ADRs publicados | principal #0 (commit inicial), venda #0 |
| EP-02 Catálogo e pagamentos | Cadastro e edição do veículo, cobrança, webhook, passos T2, T3, T6 e compensações, outbox | RF-01, RF-02, RF-06, RF-07, RF-13; 100% de cobertura | principal #1 |
| EP-03 Vitrine e compra | Réplica dos anúncios, listagens, compra por CPF, saga orquestrada, recuperação | RF-03 a RF-05, RF-08 a RF-12; BDD do fluxo | venda #1 |
| EP-04 Plataforma | kind, Terraform, Kong, Prometheus, Grafana, Collector, Jaeger, NetworkPolicies | CD verde com e2e pelo Kong | principal #2, #3, #4 |
| EP-05 Infra do serviço de venda | MongoDB 7, CronJob de recuperação, CD com teste de falha | CD verde com o teste de falha | venda #2, #3 |
| EP-06 Contratos | Contratos OpenAPI das rotas internas, job `contrato` nos dois CIs | Contrato verificado nos dois lados | principal #5, venda #4 |
| EP-07 Operação | Trava comum aos CDs, documentação, roteiro do vídeo, PDF | Entregáveis do enunciado | principal #6, venda #5 e seguintes |

## 10.2 Definition of Ready

- Requisito numerado (RF/RNF/RN) e critério de aceite escrito.
- Contrato HTTP definido em [05](05-api.md) quando a história envolve rota.
- Impacto na saga avaliado (passo novo, compensação, idempotência).

## 10.3 Definition of Done

- PR com título em Conventional Commits e todos os checks obrigatórios verdes: `qualidade`, `testes`, `contrato`, `sonarcloud`, `imagem`, `infra`.
- Cobertura do serviço acima de 80% (na prática, 100%).
- CD da `main` verde, com o e2e pelo Kong.
- Documentação e contratos atualizados no mesmo PR.
- Nenhum CPF em claro em respostas, logs, métricas ou traces.

## 10.4 Cronograma

| Período | Entregas |
|---|---|
| 01/11 | Modelagem, requisitos, ADRs; código dos dois serviços; plataforma, infraestrutura e CDs; contratos; documentação |
| 02/11 a 08/11 | Revisão da documentação, ensaio e gravação do vídeo |
| 09/11 a 12/11 | Edição do vídeo, PDF de entrega |
| 13/11 a 15/11 | Folga para imprevistos; entrega |

## 10.5 Riscos

| ID | Risco | Mitigação |
|---|---|---|
| R-01 | MongoDB 8 não sobe no kernel dos runners do GitHub (SERVER-121912) | MongoDB 7.0 fixado nos dois ambientes (ADR-001 do serviço de venda) |
| R-02 | Memória do PC insuficiente para o ambiente completo | Limits somados em cerca de 5,2 GiB ([08, seção 8.6](08-ci-cd.md#86-recursos)); Docker Desktop com 8 GiB |
| R-03 | Os CDs dos dois repositórios rodando juntos (o teste de falha derruba o principal) | Trava comum aos CDs (rede docker) |
| R-04 | Divergência silenciosa entre os serviços nas rotas internas | Testes de contrato nos dois lados e comparação com a `main` do outro repositório |
| R-05 | SonarCloud fora do ar bloqueando PR | O gate de 80% do pytest continua valendo; o check pode ser reexecutado |
| R-06 | Cluster de fase anterior com outra configuração | O CD recusa o cluster sem a porta do Jaeger; `04-subir-ambiente.ps1 -RecriarCluster` |
| R-07 | Vídeo longo demais | Roteiro com tempos por bloco ([11](11-roteiro-video.md)); deploy e testes gravados com cortes |
