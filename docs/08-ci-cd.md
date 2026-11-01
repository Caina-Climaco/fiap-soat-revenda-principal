# 08. CI/CD e infraestrutura

Os dois repositórios seguem o mesmo modelo da Fase 3: **CI** no runner hospedado do GitHub em todo Pull Request, **CD** num runner self-hosted (container no Docker Desktop do PC do autor) a cada merge na `main`, implantando no cluster kind local. Este documento descreve o repositório principal; o de venda é igual, com as diferenças listadas na seção 8.5.

## 8.1 Fluxo de trabalho

1. Branch a partir da `main`, commit, PR com título em Conventional Commits.
2. O CI roda os checks obrigatórios. A `main` é protegida: sem checks verdes não há merge.
3. Merge (squash) na `main` dispara o CD, que implanta e roda os testes ponta a ponta.
4. Rollback: `Actions > CD > Run workflow` com `ref` = SHA anterior (a imagem desse SHA já está no Docker local e a migração é tolerante a "banco à frente").

## 8.2 CI (`.github/workflows/ci.yml`)

| Job | O que garante |
|---|---|
| `qualidade` | `ruff check`, `ruff format --check`, `mypy --strict`, `lint-imports` (camadas da Clean Architecture e isolamento entre contextos) |
| `testes` | Unidade + integração contra PostgreSQL 16 real, **cobertura mínima de 80%** (`--cov-fail-under=80`), relatório no resumo do job e `coverage.xml` como artefato |
| `contrato` | Contratos das rotas internas nos dois lados e comparação com o contrato publicado na `main` do outro repositório ([09, seção 9.4](09-testes.md#94-testes-de-contrato)) |
| `sonarcloud` | Análise estática e **Quality Gate** do SonarCloud com a cobertura do job `testes` ([ADR-008](adrs/ADR-008-qualidade-sonarcloud-contrato-bdd.md)) |
| `imagem` | Build da imagem e Trivy: CVE CRITICAL corrigível bloqueia; HIGH vai para o resumo; varredura de segredos no repositório |
| `infra` | `terraform fmt` e `validate`; `kubectl kustomize` + `kubeconform`; `kong config parse`; `promtool check` e `test rules`; painéis do Grafana; `otelcol validate`; portas do kind; `hadolint` e `shellcheck` do runner |
| `titulo-pr` | Título do PR em Conventional Commits (vira a mensagem do squash) |

Todas as actions são fixadas por SHA de commit.

## 8.3 CD (`.github/workflows/cd.yml`)

| Passo | Detalhe |
|---|---|
| Validação | Disparo manual só implanta commit que pertence à `main` |
| Trava | Um CD por vez somando os dois repositórios (rede docker `revenda-cd-em-andamento`, liberada no fim, abandonada após 45 min): o e2e do serviço de venda derruba o principal de propósito |
| Cluster | Cria o kind `revenda` se faltar, com trava (`docker network create`) contra o CD do outro repositório; recusa um cluster de fase anterior |
| Terraform | Plataforma e infraestrutura do principal, state em `/revenda-state/revenda-principal.tfstate` (bind mount de `%USERPROFILE%\.revenda`) |
| Imagem | `docker build` e `kind load` da `revenda-principal:<sha>` |
| Migração | Job `principal-migracao` antes do rollout; falha aborta sem tocar no Deployment |
| Deploy | `kubectl kustomize` com a tag do SHA, `rollout status` |
| Verificações | Principal alcançável pelo Kong; Prometheus coleta o principal e o Kong; alertas e painéis carregados; Grafana consulta o Prometheus e o Jaeger (health check da fonte); Collector e Jaeger no ar |
| e2e | `tests/e2e` pelo Kong: borda (chaves, ACL, rotas fechadas), cadastro e edição, webhook, tracing no Jaeger. Com o serviço de venda implantado, também a integração: réplica do anúncio, compra paga, compra cancelada e versão antiga recusada |

## 8.4 Ambiente local

| Script (PowerShell) | Função |
|---|---|
| `00-verificar-ambiente.ps1` | Lista versões das ferramentas e portas ocupadas |
| `01-instalar-ferramentas.ps1` | Instala kind, Terraform e Helm (winget) |
| `02-criar-repositorio.ps1` | Cria o repositório e protege a `main` (`-Checks` atualiza os checks obrigatórios) |
| `03-instalar-runner.ps1` | Constrói a imagem do runner e registra o container `revenda-runner-principal` |
| `04-subir-ambiente.ps1` | Cria o cluster e aplica o Terraform (`-RecriarCluster` substitui um cluster da Fase 3) |
| `05-destruir-ambiente.ps1` | `terraform destroy` (e `-ApagarCluster`) |
| `segredos.ps1` | Mostra as chaves do Kong, o segredo do webhook e a senha do Grafana |
| `limpar-dados.ps1` | Apaga os dados deixados pelos testes ponta a ponta (veículos de marca `E2E` e o que deriva deles, nos dois bancos); `-Tudo` zera os dados de negócio antes de gravar o vídeo |
| `abrir-pr.ps1` | Branch, commit, PR e acompanhamento dos checks |

Ordem do primeiro deploy: **principal antes do serviço de venda** (o principal cria o cluster, o Kong, a observabilidade e o Secret de contrato dos tokens).

## 8.5 Diferenças no repositório de venda

- CI com MongoDB 7 real e os cenários BDD; job `contrato` com o principal.
- CD confere antes que o Kong e o Collector respondem e que o Secret de contrato existe; aplica o Terraform do namespace `vendas` (MongoDB, chave do CPF, NetworkPolicies) e o CronJob de recuperação de sagas.
- O e2e do serviço de venda roda o fluxo de compra completo, a demonstração de falha do principal (circuit breaker e compensação) e a recuperação de verdade: a reserva vencida desfeita pelo CronJob e o CPF de compra cancelada apagado depois da retenção (datas adiantadas no MongoDB, job criado a partir do CronJob).
- Depois do e2e, o CD roda a carga das listagens com o k6 (`tests/carga/listagens.js`, 8 requisições por segundo por 1 minuto pelo Kong); p95 acima de 300 ms ou mais de 1% de falhas reprova o deploy (RNF-10). Os limites medidos vão para o resumo do CD.

## 8.6 Recursos

Soma dos *limits* de memória dos componentes permanentes (RNF-18), com os serviços no mínimo do HPA:

| Componente | Réplicas | Limit | Total |
|---|---|---|---|
| revenda-principal | 2 | 320 MiB | 640 MiB |
| principal-db | 1 | 512 MiB | 512 MiB |
| revenda-vendas | 2 | 320 MiB | 640 MiB |
| vendas-db | 1 | 768 MiB | 768 MiB |
| Kong | 1 | 512 MiB | 512 MiB |
| Prometheus | 1 | 768 MiB | 768 MiB |
| Grafana | 1 | 512 MiB | 512 MiB |
| OpenTelemetry Collector | 1 | 256 MiB | 256 MiB |
| Jaeger | 1 | 512 MiB | 512 MiB |
| metrics-server | 1 | 200 MiB | 200 MiB |
| **Total** | | | **cerca de 5,2 GiB** |

O CronJob de recuperação (256 MiB) roda por poucos segundos a cada minuto, e o plano de controle do kind usa por volta de 1 GiB. O ambiente cabe num Docker Desktop com 8 GiB.
