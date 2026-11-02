# 11. Roteiro do vídeo de entrega

O enunciado pede um vídeo "demonstrando a solução funcionando, tanto na infraestrutura quanto no uso, fazendo um teste ponta-a-ponta, com cadastro de veículo para venda, compra e efetivação da compra, mostrando a implementação e o uso dos serviços funcionando de forma segregada, mas se comunicando, com os testes rodando e mostrando evidências da cobertura dos testes". Este roteiro cobre cada item nessa ordem, em cerca de 12 minutos. As falas são sugestões em primeira pessoa, para adaptar.

## 11.1 Blocos e tempos

| # | Bloco | Início | Duração | Item do enunciado |
|---|---|---|---|---|
| 1 | Abertura | 0:00 | 0:40 | Contexto |
| 2 | Arquitetura e segregação | 0:40 | 1:20 | Serviços segregados, bancos diferentes, HTTP entre eles |
| 3 | Infraestrutura | 2:00 | 1:30 | Cluster, Terraform, deployments e services |
| 4 | Testes e cobertura | 3:30 | 1:30 | Testes rodando, cobertura acima de 80% |
| 5 | PR e deploy automatizado | 5:00 | 1:30 | CI/CD por PR, deploy no merge |
| 6 | Uso ponta a ponta | 6:30 | 3:30 | Cadastro, compra, efetivação; serviços se comunicando |
| 7 | Falha do principal | 10:00 | 1:15 | Resiliência (diferencial) |
| 8 | Encerramento | 11:15 | 0:45 | Resumo |

## 11.2 Preparação

### Ambiente no ar

- [ ] Docker Desktop iniciado; os dois CDs verdes na `main`.
- [ ] `kubectl get pods -A -l app.kubernetes.io/part-of=revenda-veiculos` com tudo `Running`.
- [ ] Um PR pequeno pronto para o bloco 5 (por exemplo, um ajuste de texto no README), ainda não aberto.
- [ ] Vitrine limpa: cada CD deixa veículos de marca `E2E` nos dois bancos. Limpe depois do último CD e antes de gravar o bloco 6:

  ```powershell
  cd C:\Projetos\fiap\fase4\principal
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1 -Simular
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\limpar-dados.ps1
  ```

  O primeiro só mostra o que seria apagado; o segundo apaga. Com `-Tudo`, o ambiente fica sem nenhum veículo, cobrança ou compra (pede confirmação).

### Chaves à mão

```powershell
cd C:\Projetos\fiap\fase4\principal
powershell -ExecutionPolicy Bypass -File .\scripts\windows\segredos.ps1
```

Copie para um bloco de notas: chave do gestor, chave do gateway de pagamento e segredo do webhook.

### Abas abertas

| Aba | Endereço |
|---|---|
| Swagger do principal | `http://localhost:8080/principal/docs` |
| Swagger do serviço de venda | `http://localhost:8080/vendas/docs` |
| Grafana, painel "saga e resiliência" | `http://localhost:3000` |
| Jaeger | `http://localhost:16686` |
| GitHub: Actions dos dois repositórios, SonarCloud dos dois projetos | |

### Aparência

Zoom de 125% no navegador; terminal com fonte grande; notificações desligadas.

## 11.3 Roteiro por bloco

### Bloco 1: Abertura (0:00 a 0:40)

**Tela**: README do principal.

> "Sou o Cainã Clímaco, RM366473. Este é o trabalho substitutivo da Fase 4: a plataforma de revenda de veículos dividida em dois microsserviços. O serviço de venda cuida das listagens e da compra, com um banco só dele, e o serviço principal cuida do cadastro dos veículos e do pagamento. Vou mostrar a infraestrutura, os testes, o deploy automatizado e uma compra de ponta a ponta."

### Bloco 2: Arquitetura e segregação (0:40 a 2:00)

**Tela**: diagrama de containers em [04, seção 4.3](04-arquitetura.md#43-c4-nível-2-containers).

> "O Kong é a única entrada. O serviço de venda tem MongoDB; o principal, PostgreSQL. Eles conversam só por HTTP: na compra, o serviço de venda chama o principal para bloquear o veículo e gerar a cobrança, numa saga com compensação; no outro sentido, o principal entrega ao serviço de venda a réplica dos anúncios e o resultado do pagamento por uma outbox, que garante a entrega mesmo se o outro lado estiver fora. Cada banco só aceita o próprio serviço, por NetworkPolicy."

**Tela**: [02, seção 2.8](02-modelagem-ddd.md#28-saga-da-compra), a tabela dos passos da saga, por alguns segundos.

### Bloco 3: Infraestrutura (2:00 a 3:30)

**Terminal**:

```powershell
kubectl get pods,svc -n principal
kubectl get pods,svc,cronjob -n vendas
kubectl get networkpolicy -A
```

> "Tudo roda num cluster kind, criado e configurado por Terraform: cada repositório tem o seu, com state próprio. Aqui os deployments e os services do principal e do serviço de venda, cada um com o seu banco, duas réplicas com HPA, e o CronJob que retoma sagas interrompidas."

**Tela**: no GitHub, `infra/terraform` do principal (o `gateway.tf` e o `kong.yml.tftpl`) e `k8s/base` do serviço de venda, rapidamente.

### Bloco 4: Testes e cobertura (3:30 a 5:00)

**Terminal**, na pasta do serviço de venda:

```powershell
uv run pytest -m "unit or integration or bdd or contrato" --cov=vendas --cov-branch --cov-fail-under=80
```

> "São 172 testes no serviço de venda: unidade, integração, os cenários BDD da compra em Gherkin e os testes de contrato com o principal. Cobertura de 100%, acima dos 80% exigidos. O principal tem 250 testes, também com 100%."

Nota: na tela, o pytest mostra 170 aprovados e 2 pulados no serviço de venda. Os 2 pulados são os de concorrência real, que só rodam com MongoDB (no CI); localmente o banco é simulado.

**Tela**: SonarCloud dos dois projetos (Quality Gate e cobertura) e o resumo de cobertura de um job `testes` no GitHub Actions.

### Bloco 5: PR e deploy automatizado (5:00 a 6:30)

**Tela**: abrir o PR preparado com o `abrir-pr.ps1`; mostrar os checks obrigatórios (`qualidade`, `testes`, `contrato`, `sonarcloud`, `imagem`, `infra`). Com corte de edição, os checks verdes; merge; o CD em execução e o resumo final com o e2e.

> "A `main` é protegida: só entra mudança por PR, com todos os checks verdes. O merge dispara o CD, que roda num runner no meu PC, aplica o Terraform, constrói a imagem, implanta no cluster e executa os testes ponta a ponta pelo Kong."

### Bloco 6: Uso ponta a ponta (6:30 a 10:00)

1. **Cadastro do veículo** (Swagger do principal, Authorize com a chave do gestor no header `apikey` ou pelo terminal):

   ```powershell
   $g = @{ apikey = "<chave do gestor>" }
   $v = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/veiculos -Headers $g -ContentType application/json -Body '{"marca":"Fiat","modelo":"Argo","ano":2022,"cor":"Branco","preco":"72900.00"}'
   $v
   ```

   > "O veículo nasce à venda no principal. A outbox entrega o anúncio ao serviço de venda."

2. **Vitrine** (serviço de venda): `GET http://localhost:8080/api/v1/veiculos/a-venda`, mostrando o veículo e a ordenação por preço.

3. **Compra** (Swagger do serviço de venda, `POST /api/v1/compras`):

   ```json
   {"veiculo_id": "<id>", "versao": 1, "cpf": "529.982.247-25"}
   ```

   > "A resposta traz o código do pagamento, o prazo e o CPF mascarado. Nesse momento o serviço de venda reservou o anúncio e chamou o principal para bloquear o veículo e gerar a cobrança."

   Mostrar que o gestor não consegue editar o veículo (`PATCH` responde 409) e que ele saiu da vitrine.

4. **Efetivação** (webhook, como o processador de pagamentos):

   ```powershell
   $p = @{ apikey = "<chave do gateway>"; "X-Webhook-Secret" = "<segredo>" }
   Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/pagamentos/webhook -Headers $p -ContentType application/json -Body '{"codigo_pagamento":"<codigo>","status":"PAGO"}'
   ```

   Depois `GET /api/v1/compras/{id}` (status `EFETIVADA` e data da venda) e `GET /api/v1/veiculos/vendidos`.

   > "O principal confirmou a cobrança e repassou o resultado ao serviço de venda, que efetivou a compra e marcou o veículo como vendido nos dois lados."

5. **Os dois bancos**, para mostrar a segregação: no PostgreSQL do principal, o veículo `VENDIDO` e a cobrança `PAGA`; no MongoDB do serviço de venda, a compra com o CPF cifrado. O comando do MongoDB não usa aspas duplas dentro do `sh -c`, porque o Windows PowerShell 5.1 não as repassa corretamente ao `kubectl`.

   ```powershell
   kubectl -n principal exec statefulset/principal-db -- psql -U revenda -d revenda_principal -c "select status, versao, compra_id from catalogo.veiculos order by atualizado_em desc limit 1"
   kubectl -n vendas exec statefulset/vendas-db -- sh -c 'mongosh -u $MONGO_APP_USUARIO -p $MONGO_APP_SENHA --authenticationDatabase $MONGO_APP_BANCO $MONGO_APP_BANCO --quiet --eval ''db.compras.find({}, {cpf: 1, status: 1}).sort({criada_em: -1}).limit(1)'''
   ```

6. **Jaeger**: buscar o serviço `kong`, abrir o trace do `POST /api/v1/compras` e mostrar os spans do Kong, do serviço de venda, do principal e dos dois bancos.

### Bloco 7: Falha do principal (10:00 a 11:15)

```powershell
kubectl -n principal scale deployment revenda-principal --replicas=0
```

Cadastre antes um segundo veículo para esta parte. Mostre a vitrine respondendo; uma compra que responde 503 com `Retry-After`; o veículo de volta à vitrine; no Grafana, o circuito aberto e as compensações. Depois:

```powershell
kubectl -n principal scale deployment revenda-principal --replicas=2
```

> "Com o principal fora, a vitrine continua no ar, porque é uma réplica local. A compra é desfeita na hora e o veículo volta à vitrine; o circuit breaker passa a recusar sem esperar. Quando o principal volta, a recuperação conclui a compensação pendente e as compras voltam a funcionar."

### Bloco 8: Encerramento (11:15 a 12:00)

**Tela**: PDF de entrega com os links.

> "Resumindo: dois serviços com bancos diferentes, conversando por HTTP com saga, outbox e resiliência; testes com 100% de cobertura e contratos verificados nos dois lados; tudo entregue por PR, com deploy automático no merge e testes ponta a ponta no final. Obrigado."
