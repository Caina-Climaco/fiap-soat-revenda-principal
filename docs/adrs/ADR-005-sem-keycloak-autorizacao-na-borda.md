# ADR-005: Sem Keycloak; autorização na borda e token de serviço nas rotas internas

**Status:** Aceito
**Data:** 2026-11-01
**Substitui:** ADR-001 e ADR-014 da Fase 3 (Keycloak e repositório de identidade)

## Contexto

Na Fase 3, o enunciado exigia cadastro e autorização de compradores apartados, resolvidos com Keycloak em repositório próprio. O enunciado da Fase 4 não pede mais cadastro: a venda registra "CPF da pessoa que comprou e data da venda". Continuam existindo três tipos de chamador que precisam de controle: o gestor (cadastro e edição), o processador de pagamentos (webhook) e o outro serviço (rotas internas). A entrega pede dois repositórios.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Kong key-auth + ACL para gestor e processador; token de serviço + NetworkPolicy para rotas internas** | Nenhum componente novo (o Kong já existe); dois repositórios; controle centralizado na borda | Sem login de usuário final; o gestor usa uma chave |
| Manter o Keycloak como terceiro repositório, só para o gestor | Login real com OIDC | Terceiro repositório fora do pedido; mais memória e mais tempo de vídeo para um ator secundário |
| Sem autenticação | Simples | Qualquer pessoa cadastraria veículos e efetivaria compras |

## Decisão

- O **Keycloak sai da solução**.
- **Borda (Kong)**: consumers `gestor` e `gateway-pagamento` com key-auth (header `apikey`) e ACL por rota. Rotas de cadastro e edição só para `gestor`; webhook só para `gateway-pagamento`. Compra e listagens são públicas, com *rate limiting* mais restrito na compra.
- **Defesa em profundidade no principal**: o webhook continua validando o segredo `X-Webhook-Secret` em tempo constante, como na Fase 3; as rotas do gestor exigem o header `X-Consumer-Username: gestor`, que só chega pelo Kong porque a NetworkPolicy só deixa o Kong (e o outro serviço, nas rotas internas) alcançar a API.
- **Rotas internas** (`/internal/v1`): sem rota no Kong; NetworkPolicy permite só o pod do outro serviço; cada chamada leva `X-Service-Token`, um segredo por direção gerado pelo Terraform e entregue como Secret.

## Consequências

### Positivas
- Menos componentes, menos memória, entrega com exatamente dois repositórios.
- Toda credencial de borda num único lugar, versionada na configuração declarativa do Kong.

### Negativas
- A chave do gestor é uma credencial estática, sem login, MFA nem expiração.
- O comprador não se autentica: qualquer pessoa com o identificador de uma compra consulta o status dela (com o CPF mascarado).

## Mitigações
- Chaves geradas pelo Terraform, nunca versionadas, com rotação por `terraform apply -replace`.
- O identificador da compra é um UUID aleatório e não existe busca por CPF (RN-21).
- Login do gestor por OIDC e mTLS entre serviços ficam documentados como evolução.
