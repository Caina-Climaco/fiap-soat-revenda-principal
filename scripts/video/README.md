# Kit de gravação automática do vídeo de entrega (Fase 4)

Mesmo kit da Fase 3, adaptado aos dois serviços. Grava, sem intervenção, os clipes do vídeo de [docs/11-roteiro-video.md](../../docs/11-roteiro-video.md) (8 blocos) e monta o vídeo final **legendado**: cartelas de bloco, a tela real e as falas em primeira pessoa como legenda, com o quadro parado quando a fala precisa de mais tempo. A voz é opcional e fica à parte (`narrar.ps1`).

O navegador roda num container Linux no Docker Desktop, na rede do kind; nada além de `docker`, `gh`, `kubectl` e `curl.exe` roda no Windows.

## Como rodar

Na raiz do repositório principal (`C:\Projetos\fiap\fase4\principal`), com o ambiente no ar, os dois CDs verdes e nenhum CD em andamento:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\video\gravar.ps1 -Montar
```

Resultado: `.setup\video\montagem\video-final-legendado.mp4` (e o `.srt` ao lado). A primeira execução constrói a imagem `revenda-video:1.63.0` (Playwright, cerca de 2 GB).

| Parâmetro | Padrão | Efeito |
|---|---|---|
| `-Blocos 6,7` | `1,2,3,4,5,6,7,8` | Regrava só esses blocos; os clipes dos demais continuam no `manifesto.json`. Grave o 6 e o 7 juntos (o 7 compra o Corolla do 6) |
| `-Clipes b6_04` | todos | Regrava só os clipes cujo id começa por esses prefixos |
| `-LimparDados 0` | ligado | Não roda o `limpar-dados.ps1 -Tudo -Sim` antes dos blocos 6 e 7 |
| `-PR 10 -PRVendas 8` | 10 e 8 | PRs exibidos no bloco 5 |
| `-Montar` | desligado | Monta o vídeo e gera a versão legendada; com `-Blocos 0 -Montar`, só monta |
| `-Ritmo 2.0` | 2.3 | Palavras por segundo usadas para o tempo de leitura de cada legenda |
| `-ReconstruirImagem` | desligado | Refaz a imagem de gravação |

## O que o `gravar.ps1` faz

1. **Pré-checagens**: docker, gh, kubectl, cluster e rede `kind`, runner `revenda-runner-principal`, Kong e Jaeger respondendo, nenhum CD em andamento (trava `revenda-cd-em-andamento`).
2. **Coleta no Windows** (blocos 3, 4 e 5): `cluster.yaml`, configuração do Kong, três chamadas reais pela borda, logs reais do CI (job `testes`, cobertura) e do CD (job `deploy`: e2e e k6) dos dois repositórios, PRs, proteção da `main` e runners.
3. **Coleta pelo runner** (bloco 3): pods, recursos de cada namespace, NetworkPolicies, os dois states do Terraform e `docker ps`.
4. **Limpeza** (blocos 6 e 7): `scripts\windows\limpar-dados.ps1 -Tudo -Sim`, para a vitrine começar vazia.
5. **Navegador, passada 1**: GitHub, SonarCloud, o uso ponta a ponta nos Swaggers dos dois serviços e o trace da compra no Jaeger.
6. **Coleta pós-uso** (bloco 6): o veículo vendido e a cobrança paga no PostgreSQL; a compra com o CPF cifrado no MongoDB.
7. **Falha do principal** (bloco 7): o runner escala o principal para 0, o navegador grava a vitrine no ar, a compra recusada com 503 e o anúncio de volta à venda; o principal é **sempre** restaurado (`finally`), a recuperação de sagas é executada (job a partir do CronJob) e o navegador grava o Grafana e o Prometheus.
8. **Terminal**: um clipe por saída real coletada, num terminal simulado que digita o comando exato executado.
9. **`-Montar`**: `montar.sh` (cartelas, clipes, `.srt`) e `narrar.py --dublagem` (vídeo final legendado), dentro do container. Na edição, os trechos sem fala depois de cada cena e todo trecho com a imagem parada por mais de 3 s são cortados, menos o tempo de leitura das legendas e as cartelas.

## Credenciais

As chaves dos consumers do Kong (`gestor` e `gateway-pagamento`) e o segredo do webhook são lidos dos Secrets pelo runner, ficam só em variáveis de ambiente do processo e entram no container por `-e NOME`; nunca vão para arquivo, log ou tela.

O Swagger não tem como enviar o header `apikey` do Kong: o esquema documentado nas rotas do gestor é o `X-Consumer-Username`, que o próprio Kong injeta depois de validar a chave. Por isso o contexto de cada persona acrescenta o `apikey` só às chamadas a `http://localhost:8080/api/`, como faria o cliente HTTP do gestor ou do processador; o CDN do Swagger e o GitHub nunca recebem a chave. O segredo do webhook entra pelo Authorize do Swagger, num campo mascarado.

## Arquivos

| Arquivo | Papel |
|---|---|
| `gravar.ps1` | Orquestrador no Windows (PowerShell 5.1, somente ASCII) |
| `gravar.py` | Playwright (Chromium headless, 1600x900): clipes de navegador e de terminal |
| `coletar-runner.sh` | Coletas e ações dentro do runner (kubectl, terraform, docker) |
| `clipes.json` | Blocos (cartelas) e clipes de terminal |
| `sincronia.json` | Falas por clipe, ancoradas nas legendas de cada passo (`m2`, `m3+4`) |
| `narracao.json` | As mesmas falas por bloco, para o `narracao.md` do `montar.py` |
| `montar.sh`, `montar.py` | Cartelas, H.264, concatenação, `.srt` e `narracao.md` |
| `narrar.ps1`, `narrar.py` | Versão legendada (`--dublagem`) ou com voz (TTS, ou a sua própria gravação) |
| `Dockerfile`, `entrypoint.sh`, `proxy.py` | Imagem e proxy: `localhost:8080` (Kong), `3000` (Grafana), `9090` (Prometheus) e `16686` (Jaeger) |
| `terminal.html`, `terminal.js` | Terminal simulado com a saída real |

Tudo o que é gerado vai para `.setup/video/` (fora do Git). Um passo que falha não interrompe os demais: o resumo no fim lista `OK`, `FALHOU` (com screenshot em `.setup\video\falhas`) ou `PULADO`.

## Validação

O uso ponta a ponta (blocos 6 e 7), a montagem e a versão legendada foram validados no ambiente de autoria contra os dois serviços reais (PostgreSQL 16 e o MongoDB simulado), atrás de um gateway que reproduz o roteamento e o key-auth do Kong, com o Swagger UI local (`VIDEO_SWAGGER_UI_DIR`). Os clipes do GitHub, do SonarCloud, do Grafana, do Prometheus e do Jaeger dependem do PC: a navegação é a mesma da Fase 3 e cada falha fica registrada sem parar a gravação.
