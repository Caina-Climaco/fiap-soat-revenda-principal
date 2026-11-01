# ADR-008: Estratégia de qualidade: cobertura, SonarCloud, testes de contrato e BDD

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

O enunciado exige testes automatizados com todos passando e cobertura mínima de 80% nas soluções em microsserviços. O Tech Challenge oficial pede ainda validação de qualidade por SonarQube ou similar no CI e pelo menos um fluxo completo testado com BDD. Com dois serviços, surge um risco que a Fase 3 não tinha: um serviço mudar um contrato HTTP e quebrar o outro, com os testes de cada um passando isoladamente.

## Alternativas avaliadas

| Tema | Escolhida | Alternativas descartadas |
|---|---|---|
| Análise de qualidade | **SonarCloud** (gratuito para repositórios públicos, integrado ao GitHub, Quality Gate como check do PR) | SonarQube no cluster: mais memória e o CI hospedado não alcança o cluster local |
| Contratos | **Contratos OpenAPI versionados + verificação nos dois lados** | Pact com broker: exige um broker hospedado e mais ferramental para dois serviços apenas |
| BDD | **pytest-bdd**, com cenários em Gherkin em português | behave: separa o runner do pytest e da cobertura |

## Decisão

- **Cobertura**: `pytest --cov --cov-fail-under=80` em cada serviço (unit + integração), relatório `coverage.xml` enviado ao SonarCloud e resumo no job summary e no README.
- **SonarCloud**: um projeto por repositório; Quality Gate obrigatório na proteção da `main` (cobertura em código novo, duplicação, *bugs*, vulnerabilidades e *security hotspots*).
- **Contratos**:
  - cada provedor publica o recorte interno da sua OpenAPI em `contratos/` e um teste verifica que a aplicação real continua aderente a ele (provedor);
  - cada consumidor guarda uma cópia do contrato do outro e testa o seu cliente HTTP contra ela, com respostas simuladas validadas pelo esquema (consumidor);
  - um job do CI falha se a cópia do consumidor divergir do contrato publicado pelo provedor na `main`.
- **BDD**: o fluxo completo da compra (caminho feliz, pagamento cancelado, reserva expirada e falha do principal) em Gherkin, executado:
  - no CI do serviço de venda, com o principal simulado por stub de contrato;
  - no CD, de ponta a ponta contra o ambiente implantado.
- Os demais checks da Fase 3 continuam: ruff, mypy strict, import-linter, Trivy, kubeconform, hadolint, `kong config parse`, `promtool`.

## Consequências

### Positivas
- Atende os dois enunciados e os temas de testes de microsserviços.
- A quebra de contrato entre os serviços aparece no PR, não no deploy.

### Negativas
- Mais tempo de CI por PR.
- O SonarCloud depende de um serviço externo e de um token por repositório.

## Mitigações
- Jobs do CI em paralelo; cache do uv.
- Se o SonarCloud estiver fora, o gate de cobertura do próprio pytest continua barrando o PR.
