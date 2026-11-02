#!/usr/bin/env python3
"""Grava os clipes do video de entrega da Fase 4 (Playwright sync, Chromium headless).

Mesmo kit da Fase 3, adaptado aos dois servicos. Roda dentro do container de gravacao
(scripts/video/entrypoint.sh), com o proxy que expoe o Kong em http://localhost:8080, o
Grafana em :3000, o Prometheus em :9090 e o Jaeger em :16686. Dois modos:

  --modo navegador   GitHub (blocos 1, 2, 3, 4, 5 e 8), uso ponta a ponta no Swagger dos dois
                     servicos e o trace no Jaeger (bloco 6), falha do principal, Grafana e
                     Prometheus (bloco 7)
  --modo terminal    clipes de terminal a partir das saidas reais em .setup/video/terminal/*.txt,
                     mapeadas em clipes.json

Saidas (em VIDEO_SAIDA, padrao /saida = .setup/video no PC):
  clipes/<id>.webm   um arquivo por clipe (bloco e passo no nome)
  falhas/<id>.png    screenshot do passo que falhou
  manifesto.json     ordem, arquivo, duracao, titulo, marcas de legenda e status de cada clipe
  dados.json         ids gerados no fluxo (sem chaves nem segredos)

Credenciais (so variaveis de ambiente, lidas dos Secrets pelo gravar.ps1; nunca em arquivo,
log ou tela): GESTOR_APIKEY e GATEWAY_APIKEY (consumers do Kong) e WEBHOOK_SECRET.
O Swagger nao tem como enviar o header `apikey` do Kong (o esquema documentado e o
X-Consumer-Username, que o proprio Kong injeta depois de validar a chave). Por isso o
contexto de cada persona acrescenta o `apikey` so nas chamadas a http://localhost:8080/api/,
como faria o cliente HTTP do gestor ou do processador; a chave nao aparece na tela nem vai
para outros enderecos (CDN do Swagger, GitHub). O segredo do webhook entra pelo Authorize do
Swagger, num campo mascarado.

Outras variaveis: VIDEO_REPO, VIDEO_REPO_VENDAS, VIDEO_PR, VIDEO_PR_VENDAS, VIDEO_CD_RUN_ID,
VIDEO_CD_RUN_ID_VENDAS, VIDEO_CI_RUN_ID, VIDEO_CI_RUN_ID_VENDAS, VIDEO_SLOWMO, VIDEO_SAIDA.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import TimeoutError as PWTimeout

DIR = Path(__file__).resolve().parent
SAIDA = Path(os.environ.get("VIDEO_SAIDA", "/saida"))
API = os.environ.get("VIDEO_API", "http://localhost:8080").rstrip("/")
GRAFANA = os.environ.get("VIDEO_GRAFANA", "http://localhost:3000").rstrip("/")
PROMETHEUS = os.environ.get("VIDEO_PROMETHEUS", "http://localhost:9090").rstrip("/")
JAEGER = os.environ.get("VIDEO_JAEGER", "http://localhost:16686").rstrip("/")
REPO = os.environ.get("VIDEO_REPO", "Caina-Climaco/fiap-soat-revenda-principal")
REPO_V = os.environ.get("VIDEO_REPO_VENDAS", "Caina-Climaco/fiap-soat-revenda-vendas")
BRANCH = os.environ.get("VIDEO_BRANCH", "main")
PR = os.environ.get("VIDEO_PR", "10")
PR_V = os.environ.get("VIDEO_PR_VENDAS", "8")
CD_RUN_ID = os.environ.get("VIDEO_CD_RUN_ID", "").strip()
CD_RUN_ID_V = os.environ.get("VIDEO_CD_RUN_ID_VENDAS", "").strip()
SONAR = "https://sonarcloud.io/summary/overall?id="
SONAR_P = "Caina-Climaco_fiap-soat-revenda-principal"
SONAR_V = "Caina-Climaco_fiap-soat-revenda-vendas"
SLOWMO = int(os.environ.get("VIDEO_SLOWMO", "60"))
# Validacao offline: serve o swagger-ui-dist local em vez do CDN jsDelivr
SWAGGER_UI_DIR = os.environ.get("VIDEO_SWAGGER_UI_DIR", "").strip()
GH = f"https://github.com/{REPO}"
GH_V = f"https://github.com/{REPO_V}"
CPF_DEMO = "529.982.247-25"  # CPF de exemplo valido (digitos verificadores), sem titular real

VIEWPORT = {"width": 1600, "height": 900}
TIMEOUT_ACAO_MS = 20_000
TIMEOUT_CLIPE_S = 240
MARGEM_TOPO_GITHUB = 110


# Avisos de cookies (SonarCloud, GitHub) cobrem a parte de baixo da tela e a legenda: some com
# eles antes de pintar. So oculta elementos fixos/sticky cujo texto fala de cookies; nao roda no
# localhost (Swagger, Grafana, Prometheus e Jaeger nao tem aviso).
SEM_COOKIES_JS = r"""
(() => {
  if (/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) return;
  // So OCULTA (display: none): remover nos que o React controla derruba a pagina inteira.
  const SELETORES = '#onetrust-consent-sdk, #onetrust-banner-sdk, #CybotCookiebotDialog, ' +
    '#usercentrics-root, .osano-cm-window, #truste-consent-track, #cookie-banner, ' +
    '#cookieConsent, .cookie-consent, .cookie-banner, [data-video-sem-cookies]';
  const TEXTO = /cookie (policy|settings|preferences)|uses? cookies|we use cookies|cookies to|pol[ií]tica de cookies|usamos cookies/i;
  const estilo = () => {
    if (document.getElementById('__video_sem_cookies') || !document.head) return;
    const css = document.createElement('style');
    css.id = '__video_sem_cookies';
    css.textContent = SELETORES + ' { display: none !important; }';
    document.head.appendChild(css);
  };
  const fixo = (el) => {
    for (let e = el; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const pos = getComputedStyle(e).position;
      if (pos === 'fixed' || pos === 'sticky') {
        let topo = e;
        while (topo.parentElement && topo.parentElement !== document.body &&
               ['fixed', 'sticky'].includes(getComputedStyle(topo.parentElement).position)) {
          topo = topo.parentElement;
        }
        return topo;
      }
    }
    return null;
  };
  const ocultar = () => {
    if (!document.body) return;
    estilo();
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeValue && n.nodeValue.length < 400 && TEXTO.test(n.nodeValue)) {
        const f = fixo(n.parentElement);
        if (f && !f.id.startsWith('__video') && !f.hasAttribute('data-video-sem-cookies')) {
          f.setAttribute('data-video-sem-cookies', '');
          f.style.setProperty('display', 'none', 'important');
        }
      }
    }
  };
  let agendado = false;
  const agendar = () => {
    if (agendado) return;
    agendado = true;
    requestAnimationFrame(() => { agendado = false; ocultar(); });
  };
  new MutationObserver(agendar).observe(document, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', ocultar);
})();
"""

OVERLAY_JS = r"""
(() => {
  if (window.__overlayVideo) return;
  window.__overlayVideo = true;
  const ORIGEM_API = %(api)s;
  // A tarja de legenda gravada no rodape e opcional (VIDEO_TARJA=1): o video final ja leva
  // a legenda da narracao no rodape, e as duas juntas tomavam muito espaco.
  const MOSTRAR_TARJA = %(tarja)s;
  const CSS_SWAGGER = `
    .swagger-ui .opblock .opblock-summary-path,
    .swagger-ui .opblock .opblock-summary-path__deprecated { font-size: 19px !important; }
    .swagger-ui .opblock .opblock-summary-method {
      font-size: 17px !important; min-width: 92px !important; }
    .swagger-ui .opblock-summary-description { font-size: 16px !important; }
    .swagger-ui textarea.body-param__text {
      font-size: 17px !important; line-height: 1.4 !important; min-height: 210px !important; }
    .swagger-ui .microlight, .swagger-ui .highlight-code pre, .swagger-ui .highlight-code code {
      font-size: 16px !important; line-height: 1.38 !important; }
    .swagger-ui .highlight-code .microlight { max-height: none !important; }
    .swagger-ui .live-responses-table .response-col_status {
      font-size: 22px !important; font-weight: 700 !important; }
    .swagger-ui .parameters-col_description input { font-size: 16px !important; }
    .swagger-ui .dialog-ux .modal-ux { max-width: 760px !important; }
    .swagger-ui .dialog-ux .modal-ux-header h3 { font-size: 22px !important; }
    .swagger-ui .auth-container h4 { font-size: 19px !important; }
    .swagger-ui .auth-container, .swagger-ui .auth-container p, .swagger-ui .auth-container label,
    .swagger-ui .auth-container input, .swagger-ui .auth-container code {
      font-size: 16px !important; }
    .swagger-ui .auth-container .btn { font-size: 16px !important; }
    .swagger-ui .curl-command, .swagger-ui .curl-command + h4 { display: none !important; }
    .swagger-ui .request-url pre { font-size: 15px !important; }
    @keyframes __destaqueVideo {
      0%%,100%% { box-shadow: 0 0 0 0 rgba(250,204,21,0); }
      50%% { box-shadow: 0 0 0 6px rgba(250,204,21,.95); } }
    .__destaque-video { animation: __destaqueVideo 0.9s ease-in-out 2; border-radius: 6px; }
  `;
  function montar() {
    if (!document.body || document.getElementById("__video_legenda")) return;
    const leg = document.createElement("div");
    leg.id = "__video_legenda";
    leg.style.cssText = "position:fixed;left:50%%;bottom:20px;transform:translateX(-50%%);" +
      "z-index:2147483647;max-width:90%%;padding:12px 28px;border-radius:12px;" +
      "background:rgba(15,23,42,.90);color:#fff;font:600 27px/1.3 'DejaVu Sans',Arial,sans-serif;" +
      "text-align:center;box-shadow:0 8px 28px rgba(0,0,0,.35);pointer-events:none;";
    leg.style.display = "none";
    document.body.appendChild(leg);
    // Ponteiro de seta, como o do Windows (o video headless nao desenha o cursor do sistema);
    // a ponta da seta fica exatamente na posicao do mouse.
    const cur = document.createElement("div");
    cur.id = "__video_cursor";
    cur.style.cssText = "position:fixed;left:-60px;top:-60px;width:20px;height:28px;" +
      "z-index:2147483647;pointer-events:none;transform-origin:0 0;transition:transform .08s;" +
      "filter:drop-shadow(1px 2px 1.5px rgba(0,0,0,.35));";
    cur.innerHTML = '<svg width="20" height="28" viewBox="0 0 20 28" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M1 1 L1 22 L6 17 L9.5 25.5 L13 24 L9.5 15.8 L16.5 15.8 Z" ' +
      'fill="#fff" stroke="#000" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.body.appendChild(cur);
    document.addEventListener("mousemove", (e) => {
      cur.style.left = e.clientX + "px"; cur.style.top = e.clientY + "px";
    }, true);
    // Clique: a seta encolhe um pouco e um anel discreto se abre no ponto do clique
    document.addEventListener("mousedown", (e) => {
      cur.style.transform = "scale(.88)";
      const anel = document.createElement("div");
      anel.style.cssText = "position:fixed;left:" + e.clientX + "px;top:" + e.clientY + "px;" +
        "width:28px;height:28px;margin:-14px 0 0 -14px;border-radius:50%%;" +
        "border:2px solid rgba(59,130,246,.75);z-index:2147483646;pointer-events:none;" +
        "transition:transform .45s ease-out,opacity .45s ease-out;transform:scale(.4);opacity:1;";
      document.body.appendChild(anel);
      requestAnimationFrame(() => { anel.style.transform = "scale(1.6)"; anel.style.opacity = "0"; });
      setTimeout(() => anel.remove(), 600);
    }, true);
    document.addEventListener("mouseup", () => { cur.style.transform = "scale(1)"; }, true);
    if (location.origin === ORIGEM_API && /\/docs\/?$/.test(location.pathname)) {
      const st = document.createElement("style");
      st.textContent = CSS_SWAGGER;
      document.head.appendChild(st);
    }
    window.__legenda = (t) => {
      leg.textContent = t || "";
      leg.style.display = (t && MOSTRAR_TARJA) ? "" : "none";
    };
    if (window.__legendaAtual && !window.opener) {
      window.__legendaAtual().then((t) => window.__legenda(t)).catch(() => {});
    }
  }
  function scrollerDe(el) {
    for (let p = el && el.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll)/.test(s.overflowY) && p.scrollHeight > p.clientHeight + 10) return p;
    }
    return document.scrollingElement || document.documentElement;
  }
  // Rolagem como a de uma roda de mouse: "dentes" de ~110 px com desaceleracao, pequenas
  // pausas entre eles e uma pausa de leitura a cada poucos dentes (nada de deslize uniforme).
  function animar(alvo, destino, ms) {
    return new Promise((ok) => {
      const max = alvo.scrollHeight - alvo.clientHeight;
      const y0 = alvo.scrollTop;
      const fim = Math.max(0, Math.min(max, destino));
      const total = fim - y0;
      if (Math.abs(total) < 2) { ok(y0); return; }
      const n = Math.max(1, Math.round(Math.abs(total) / 110));
      const porDente = Math.max(ms, n * 150) / n;
      let i = 0;
      const dente = () => {
        if (i >= n) { ok(alvo.scrollTop); return; }
        const ini = alvo.scrollTop;
        const meta = y0 + total * (i + 1) / n;
        const t0 = Date.now();
        const dur = Math.max(70, porDente * 0.55);
        const anim = () => {
          const p = Math.min(1, (Date.now() - t0) / dur);
          alvo.scrollTop = ini + (meta - ini) * (1 - Math.pow(1 - p, 3));
          if (p < 1) { setTimeout(anim, 16); return; }
          i++;
          const leitura = (n > 6 && i %% 5 === 0 && i < n) ? 450 + Math.random() * 250 : 0;
          setTimeout(dente, porDente * 0.45 + leitura + Math.random() * 50);
        };
        anim();
      };
      dente();
    });
  }
  window.__rolarAteEl = (el, ms, margem) => {
    const alvo = scrollerDe(el);
    const raiz = alvo === document.scrollingElement || alvo === document.documentElement;
    const topoAlvo = raiz ? 0 : alvo.getBoundingClientRect().top;
    const destino = alvo.scrollTop + el.getBoundingClientRect().top - topoAlvo - margem;
    return animar(alvo, destino, ms);
  };
  window.__rolarPor = (px, ms) => {
    let alvo = document.scrollingElement || document.documentElement;
    if (alvo.scrollHeight <= alvo.clientHeight + 10) {
      let maior = null;
      for (const el of document.querySelectorAll("main, div, section")) {
        const s = getComputedStyle(el);
        if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 10 &&
            (!maior || el.clientHeight > maior.clientHeight)) maior = el;
      }
      if (maior) alvo = maior;
    }
    return animar(alvo, alvo.scrollTop + px, ms);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", montar);
  else montar();
})();
"""


# --------------------------------------------------------------------------- modelos
@dataclass
class Clipe:
    id: str
    bloco: int
    ordem: int
    titulo: str
    tipo: str  # "navegador" ou "terminal"
    persona: str | None = None
    executar: Callable[[Tela], None] | None = None
    opcional: bool = False
    cfg: dict[str, Any] = field(default_factory=dict)


@dataclass
class Resultado:
    id: str
    bloco: int
    ordem: int
    titulo: str
    tipo: str
    opcional: bool
    status: str = "FALHOU"
    arquivo: str | None = None
    duracao_s: float = 0.0
    inicio_corte_s: float = 0.0
    marcas: list[dict[str, Any]] = field(default_factory=list)
    erro: str | None = None
    gravado_em: str = ""


class PassoFalhouError(Exception):
    """Falha esperada de um passo (mensagem curta para o resumo)."""


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Tela:
    """Uma pagina sendo gravada, com os helpers de navegacao, GitHub e Swagger."""

    def __init__(self, gravador: Gravador, page: Page, ctx: BrowserContext, clipe: Clipe):
        self.g = gravador
        self.page = page
        self.ctx = ctx
        self.clipe = clipe
        self.t0 = time.monotonic()
        self.prazo = self.t0 + TIMEOUT_CLIPE_S
        self.inicio_corte: float | None = None
        self.marcas: list[dict[str, Any]] = []

    # ----------------------------------------------------------------- basicos
    def checar_prazo(self) -> None:
        if time.monotonic() > self.prazo:
            raise PassoFalhouError(f"clipe passou de {TIMEOUT_CLIPE_S} s")

    def agora(self) -> float:
        return time.monotonic() - self.t0

    def ir(self, url: str, esperar: str = "domcontentloaded") -> None:
        self.checar_prazo()
        # Logo apos a subida do container o Chromium pode acusar ERR_NETWORK_CHANGED
        # na primeira navegacao (a interface de rede ainda esta assentando): tenta de novo.
        for tentativa in range(4):
            try:
                self.page.goto(url, wait_until=esperar, timeout=45_000)
                break
            except Exception as erro:
                if "ERR_NETWORK_CHANGED" not in str(erro) or tentativa == 3:
                    raise
                log(f"  rede ainda instavel ({tentativa + 1}); tentando de novo em 3 s")
                time.sleep(3)
        if self.inicio_corte is None:
            self.inicio_corte = max(0.0, self.agora() - 0.2)
        self._reaplicar_legenda()

    def legenda(self, texto: str) -> None:
        self.g.legenda_atual = texto
        self.marcas.append({"t": round(self.agora(), 2), "texto": texto})
        self._reaplicar_legenda()

    def _reaplicar_legenda(self) -> None:
        # pagina navegando: o init script busca a legenda sozinho ao carregar
        with contextlib.suppress(Exception):
            self.page.evaluate("t => window.__legenda && window.__legenda(t)", self.g.legenda_atual)

    def pausa(self, segundos: float) -> None:
        self.checar_prazo()
        self.page.wait_for_timeout(int(segundos * 1000))

    def mover_para(self, loc: Locator, inicio: bool = False) -> None:
        caixa = loc.bounding_box()
        if caixa:
            x = caixa["x"] + (min(140, caixa["width"] / 2) if inicio else caixa["width"] / 2)
            self.page.mouse.move(x, caixa["y"] + caixa["height"] / 2, steps=22)

    def clicar(self, loc: Locator, pausa_antes: float = 0.15) -> None:
        self.checar_prazo()
        loc.scroll_into_view_if_needed()
        self.mover_para(loc)
        self.page.wait_for_timeout(int(pausa_antes * 1000))
        loc.click()

    def digitar(self, loc: Locator, texto: str, atraso_ms: int = 35) -> None:
        self.clicar(loc, pausa_antes=0.1)
        loc.fill("")
        loc.press_sequentially(texto, delay=atraso_ms)

    def destacar(self, loc: Locator) -> None:
        with contextlib.suppress(Exception):
            loc.evaluate(
                "el => { el.classList.remove('__destaque-video'); void el.offsetWidth;"
                " el.classList.add('__destaque-video'); }"
            )

    def rolar_ate(self, loc: Locator, ms: int = 1600, margem: int = 90) -> None:
        self.checar_prazo()
        loc.wait_for(state="attached")
        loc.evaluate(
            "(el, a) => window.__rolarAteEl(el, a.ms, a.margem)", {"ms": ms, "margem": margem}
        )

    def rolar_por(self, px: int, ms: int | None = None) -> None:
        self.checar_prazo()
        self.page.evaluate(
            "a => window.__rolarPor(a.px, a.ms)", {"px": px, "ms": ms or abs(px) * 5}
        )

    def screenshot_falha(self) -> None:
        destino = SAIDA / "falhas" / f"{self.clipe.id}.png"
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(destino))
        except Exception as erro:  # pagina pode ter fechado
            log(f"  (sem screenshot: {erro})")

    # ----------------------------------------------------------------- GitHub
    def github(self, caminho: str, base: str = GH) -> None:
        """Abre uma pagina do repositorio clicando pelo caminho (aba, pasta, arquivo, PR), como
        uma pessoa navegaria; se algum clique nao der certo, abre a URL direto."""
        if caminho:
            try:
                self._github_por_cliques(caminho, base)
                self.aceitar_cookies()
                return
            except Exception as erro:
                log(f"  navegacao por cliques falhou ({str(erro)[:120]}); abrindo {caminho} direto")
        self.ir(f"{base}{caminho}")
        self.aceitar_cookies()

    def _clicar_link(self, seletor: str, url_parte: str) -> None:
        link = self.page.locator(seletor).first
        link.wait_for(state="visible", timeout=10_000)
        caixa = link.bounding_box()
        if caixa and (caixa["y"] < 80 or caixa["y"] > VIEWPORT["height"] - 80):
            self.rolar_ate(link, ms=900, margem=200)
            self.pausa(0.4)
        self.pausa(0.5)
        self.clicar(link, pausa_antes=0.35)
        self.page.wait_for_url(re.compile(re.escape(url_parte)), timeout=20_000)
        self.page.wait_for_load_state("domcontentloaded")
        self._reaplicar_legenda()
        self.pausa(1.4)

    def _github_por_cliques(self, caminho: str, base: str) -> None:
        repo = urlparse(base).path.rstrip("/")
        atual = self.page.url
        no_repo = atual.startswith(base)
        m_pr = re.match(r"^/pull/(\d+)(/checks)?$", caminho)
        m_run = re.match(r"^/actions/runs/(\d+)$", caminho)
        if m_pr and no_repo and f"/pull/{m_pr.group(1)}" in atual:
            # ja esta no PR: so troca de aba (Checks)
            if m_pr.group(2):
                self._clicar_link(f'a[href$="{repo}/pull/{m_pr.group(1)}/checks"]', "/checks")
            return
        if not no_repo:
            self.ir(base)
            self.aceitar_cookies()
            self.pausa(2.0)
        elif urlparse(atual).path.rstrip("/") != repo:
            self._clicar_link(f'a[href="{repo}"]:visible', base)
        if caminho.startswith("/blob/"):
            segmentos = caminho.split("/")[3:]
            for seg in segmentos:
                self._clicar_link(
                    f'a.Link--primary[title="{seg}"]:visible, table a[title="{seg}"]:visible', seg
                )
                self.page.locator(
                    ".markdown-body, table, [aria-labelledby='folders-and-files']"
                ).first.wait_for(timeout=20_000)
        elif m_pr or caminho.startswith("/pulls"):
            self._clicar_link("#pull-requests-tab", "/pulls")
            self._clicar_link('a[href*="state%3Aclosed"]', "state%3Aclosed")
            if m_pr:
                n = m_pr.group(1)
                self._clicar_link(f'a[href$="{repo}/pull/{n}"]', f"/pull/{n}")
                if m_pr.group(2):
                    self._clicar_link(f'a[href$="{repo}/pull/{n}/checks"]', "/checks")
        elif m_run or caminho.startswith("/actions"):
            self._clicar_link("#actions-tab", "/actions")
            if m_run:
                self._clicar_link(
                    f'a[href*="/actions/runs/{m_run.group(1)}"]', f"/actions/runs/{m_run.group(1)}"
                )
            elif "workflows/" in caminho:
                self._clicar_link(f'a[href$="{repo}{caminho}"]', caminho)
        else:
            raise RuntimeError(f"caminho sem roteiro de cliques: {caminho}")

    def aceitar_cookies(self) -> None:
        # banner de cookies do GitHub (so aparece em algumas regioes)
        with contextlib.suppress(Exception):
            botao = self.page.get_by_role("button", name=re.compile(r"^\s*Accept", re.I)).first
            botao.wait_for(state="visible", timeout=2500)
            botao.click()

    def fechar_aviso_cookies(self) -> None:
        """Reforco do SEM_COOKIES_JS para avisos que ele nao alcanca (shadow DOM): so age se o aviso
        continuar visivel, recusando os cookies opcionais ou fechando o aviso."""
        texto = self.page.get_by_text(re.compile(r"Cookie Policy|uses cookies", re.I)).first
        with contextlib.suppress(Exception):
            if not texto.is_visible():
                return
        aviso = self.page.locator(
            "div, section, aside", has_text=re.compile(r"Cookie Policy|uses cookies", re.I)
        )
        nomes = r"^\s*(Reject|Decline|Only necessary|Necessary only|Recusar|Close|Fechar|Dismiss)"
        with contextlib.suppress(Exception):
            botao = aviso.get_by_role("button", name=re.compile(nomes, re.I)).last
            botao.wait_for(state="visible", timeout=2500)
            botao.click()
            self.pausa(0.5)

    def esperar_mermaid(self, timeout_ms: int = 25_000) -> None:
        """Espera os diagramas Mermaid (iframes do GitHub) renderizarem; nao falha o clipe."""
        try:
            self.page.wait_for_function(
                """() => {
                  const secs = Array.from(document.querySelectorAll(
                    '.markdown-body [data-type="mermaid"],' +
                    ' .markdown-body section.js-render-needs-enrichment'));
                  if (!secs.length) {
                    return !document.querySelector('.markdown-body pre[lang="mermaid"]');
                  }
                  return secs.every(s => s.querySelector('iframe') &&
                    !s.matches('.is-render-pending, .is-render-requested') &&
                    !s.querySelector('.is-render-pending, .is-render-requested'));
                }""",
                timeout=timeout_ms,
            )
            self.pausa(1.5)
        except PWTimeout:
            log("  aviso: diagramas Mermaid nao confirmados no tempo; seguindo")

    def titulo_md(self, texto: str) -> Locator:
        return self.page.locator(".markdown-body :is(h1, h2, h3, h4)").filter(has_text=texto).first

    def doc_github(self, caminho: str, paradas: list[tuple[str, str, float, int]]) -> None:
        """Abre um .md renderizado e para em cada titulo: (titulo, legenda, pausa, rolagem)."""
        self.github(f"/blob/{BRANCH}/{caminho}")
        self.page.locator(".markdown-body").first.wait_for(timeout=30_000)
        self.esperar_mermaid()
        self.pausa(1.5)
        for titulo, legenda, pausa, rolagem in paradas:
            if legenda:
                self.legenda(legenda)
            try:
                alvo = self.titulo_md(titulo)
                alvo.wait_for(state="attached", timeout=8000)
                self.rolar_ate(alvo, ms=2200, margem=MARGEM_TOPO_GITHUB)
                with contextlib.suppress(Exception):
                    self.mover_para(alvo, inicio=True)
            except PWTimeout:
                log(f"  aviso: titulo '{titulo}' nao encontrado em {caminho}")
                continue
            self.pausa(pausa)
            if rolagem:
                self.rolar_por(rolagem, ms=rolagem * 6)
                self.pausa(1.2)

    # ----------------------------------------------------------------- Swagger
    def abrir_swagger(self, servico: str) -> None:
        """Swagger de um dos servicos, servido pelo Kong (/principal/docs ou /vendas/docs)."""
        self.ir(f"{API}/{servico}/docs")
        self.page.locator(".swagger-ui .opblock").first.wait_for(timeout=30_000)
        self.page.locator(".swagger-ui .auth-wrapper .btn.authorize").first.wait_for()
        self.pausa(0.5)

    def abrir_operacao(self, metodo: str, caminho: str) -> Locator:
        op = (
            self.page.locator(f".opblock.opblock-{metodo.lower()}")
            .filter(has=self.page.locator(f'.opblock-summary-path[data-path="{caminho}"]'))
            .first
        )
        op.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.rolar_ate(op, ms=1300, margem=70)
        if "is-open" not in (op.get_attribute("class") or ""):
            resumo = op.locator(".opblock-summary-control, .opblock-summary").first
            self.clicar(resumo)
        op.locator(".opblock-body").first.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.pausa(0.6)
        return op

    def try_it_out(self, op: Locator) -> None:
        botao = op.locator("button.try-out__btn").first
        botao.wait_for(timeout=TIMEOUT_ACAO_MS)
        if "cancel" not in (botao.inner_text() or "").lower():
            self.clicar(botao)
        op.locator("button.execute").first.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.pausa(0.4)

    def preencher_corpo(self, op: Locator, corpo: dict[str, Any], atraso_ms: int = 6) -> None:
        area = op.locator("textarea.body-param__text").first
        area.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.rolar_ate(area, ms=700, margem=160)
        texto = json.dumps(corpo, ensure_ascii=False, indent=2)
        self.clicar(area, pausa_antes=0.1)
        area.fill("")
        area.press_sequentially(texto, delay=atraso_ms)
        # garante o valor exato (o Swagger e um componente React controlado)
        if area.input_value().strip() != texto:
            area.fill(texto)
        self.pausa(0.5)

    def preencher_parametro(self, op: Locator, nome: str, valor: str) -> None:
        campo = op.locator(f'tr[data-param-name="{nome}"] input').first
        campo.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.digitar(campo, valor, 8)
        self.pausa(0.4)

    def executar(self, op: Locator, metodo: str, padrao_caminho: str) -> tuple[int, Any]:
        """Clica em Execute e devolve (status, json) lidos da resposta HTTP real."""
        regex = re.compile(padrao_caminho)

        def casa(resp: Any) -> bool:
            return resp.request.method == metodo.upper() and bool(
                regex.search(urlparse(resp.url).path)
            )

        botao = op.locator("button.execute").first
        with self.page.expect_response(casa, timeout=TIMEOUT_ACAO_MS) as info:
            self.clicar(botao)
        resposta = info.value
        try:
            corpo = resposta.json()
        except Exception:
            corpo = resposta.text()
        op.locator(".live-responses-table .response-col_status").first.wait_for(
            timeout=TIMEOUT_ACAO_MS
        )
        return resposta.status, corpo

    def rolar_ate_resposta(self, op: Locator, pausa: float = 2.2, rolar_corpo: bool = True) -> None:
        tabela = op.locator(".live-responses-table").first
        tabela.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.rolar_ate(tabela, ms=1500, margem=60)
        self.destacar(tabela.locator(".response-col_status").first)
        self.pausa(pausa)
        if not rolar_corpo:
            return
        excesso = tabela.evaluate(
            "el => { const c = el.querySelector('.highlight-code') || el;"
            " return Math.round(c.getBoundingClientRect().bottom - (window.innerHeight - 110)); }"
        )
        if excesso and excesso > 40:
            self.rolar_por(int(excesso), ms=min(7000, int(excesso) * 6))
            self.pausa(1.5)

    def modal_autorizacao(self) -> Locator:
        botao = self.page.locator(".swagger-ui .auth-wrapper .btn.authorize").first
        self.rolar_ate(
            self.page.locator(".swagger-ui .information-container, .swagger-ui .info").first,
            ms=600,
            margem=0,
        )
        self.clicar(botao)
        modal = self.page.locator(".swagger-ui .modal-ux").first
        modal.wait_for(timeout=TIMEOUT_ACAO_MS)
        self.pausa(0.8)
        return modal

    def fechar_modal(self, modal: Locator) -> None:
        fechar = modal.locator("button.btn-done, button.close-modal").first
        self.clicar(fechar)
        modal.wait_for(state="detached", timeout=TIMEOUT_ACAO_MS)
        self.pausa(0.5)

    def autorizar_api_key(self, segredo: str) -> None:
        modal = self.modal_autorizacao()
        bloco = (
            modal.locator(".auth-container")
            .filter(has=self.page.locator("h4", has_text="webhook"))
            .first
        )
        bloco.wait_for(timeout=TIMEOUT_ACAO_MS)
        sair = bloco.locator("button", has_text=re.compile(r"^\s*Logout\s*$"))
        if sair.count():
            self.clicar(sair.first)
            self.pausa(0.5)
        campo = bloco.locator("input").first
        campo.wait_for(timeout=TIMEOUT_ACAO_MS)
        # o segredo nunca aparece no video: o campo vira type=password antes de digitar
        campo.evaluate("el => { el.type = 'password'; el.autocomplete = 'off'; }")
        self.clicar(campo, pausa_antes=0.1)
        campo.press_sequentially(segredo, delay=6)
        self.pausa(0.5)
        botao = (
            bloco.locator("button.authorize, button.modal-btn.auth")
            .filter(has_text=re.compile(r"Authorize", re.I))
            .first
        )
        self.clicar(botao)
        bloco.locator("button", has_text=re.compile(r"^\s*Logout\s*$")).first.wait_for(
            timeout=TIMEOUT_ACAO_MS
        )
        self.destacar(bloco)
        self.pausa(1.5)
        self.fechar_modal(modal)


# --------------------------------------------------------------------------- gravador
class Gravador:
    def __init__(self, pw: Playwright):
        self.pw = pw
        self.browser = pw.chromium.launch(
            headless=True,
            slow_mo=SLOWMO,
            args=["--hide-scrollbars", "--force-color-profile=srgb", "--lang=pt-BR"],
        )
        self.estados: dict[str, Any] = {}
        self.legenda_atual = ""
        self.dados: dict[str, Any] = {"veiculos": {}, "compras": {}}
        self.api = pw.request.new_context(base_url=API)

    def fechar(self) -> None:
        self.api.dispose()
        self.browser.close()

    def novo_contexto(self, persona: str | None, dir_video: Path) -> BrowserContext:
        estado = self.estados.get(persona) if persona else None
        ctx = self.browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(dir_video),
            record_video_size=VIEWPORT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            color_scheme="light",
            storage_state=estado,
        )
        ctx.set_default_timeout(TIMEOUT_ACAO_MS)
        chave = {
            "gestor": os.environ.get("GESTOR_APIKEY", ""),
            "gateway": os.environ.get("GATEWAY_APIKEY", ""),
        }.get(persona or "", "")
        if chave:
            # So nas chamadas a API pelo Kong; o CDN do Swagger e o GitHub nunca recebem a chave
            def com_chave(rota: Any) -> None:
                rota.continue_(headers={**rota.request.headers, "apikey": chave})

            ctx.route(re.compile(re.escape(API) + r"/api/"), com_chave)
        if SWAGGER_UI_DIR:
            raiz = Path(SWAGGER_UI_DIR)

            def servir(rota: Any) -> None:
                nome = urlparse(rota.request.url).path.rsplit("/", 1)[-1]
                arquivo = raiz / nome
                if arquivo.is_file():
                    rota.fulfill(path=str(arquivo))
                else:
                    rota.abort()

            ctx.route("https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/**", servir)
        ctx.expose_function("__legendaAtual", lambda: self.legenda_atual)
        ctx.add_init_script(script=SEM_COOKIES_JS)
        ctx.add_init_script(
            script=OVERLAY_JS
            % {
                "api": json.dumps(API),
                "tarja": "true" if os.environ.get("VIDEO_TARJA") == "1" else "false",
            }
        )
        return ctx

    def gravar(self, clipe: Clipe) -> Resultado:
        res = Resultado(
            clipe.id, clipe.bloco, clipe.ordem, clipe.titulo, clipe.tipo, clipe.opcional
        )
        res.gravado_em = datetime.now(UTC).isoformat(timespec="seconds")
        tmp = SAIDA / "tmp" / clipe.id
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        destino = SAIDA / "clipes" / f"{clipe.id}.webm"
        destino.parent.mkdir(parents=True, exist_ok=True)
        (SAIDA / "falhas" / f"{clipe.id}.png").unlink(missing_ok=True)
        self.legenda_atual = ""
        ctx = self.novo_contexto(clipe.persona, tmp)
        page = ctx.new_page()
        tela = Tela(self, page, ctx, clipe)
        log(f"> {clipe.id}: {clipe.titulo}")
        try:
            tela.legenda(clipe.titulo)
            if clipe.executar is None:
                raise PassoFalhouError("clipe sem executor")
            clipe.executar(tela)
            tela.pausa(0.6)
            res.status = "OK"
        except Exception as erro:  # um passo que falha nao interrompe os demais
            res.status = "FALHOU"
            res.erro = (
                f"{type(erro).__name__}: {str(erro).splitlines()[0][:300] if str(erro) else ''}"
            )
            log(f"  FALHOU: {res.erro}")
            if not isinstance(erro, PassoFalhouError):
                traceback.print_exc(limit=3)
            tela.screenshot_falha()
        finally:
            if clipe.persona:
                with contextlib.suppress(Exception):
                    self.estados[clipe.persona] = ctx.storage_state()
            video = page.video
            res.duracao_s = round(tela.agora(), 2)
            res.inicio_corte_s = round(tela.inicio_corte or 0.0, 2)
            res.marcas = tela.marcas
            ctx.close()
            if video:
                try:
                    video.save_as(str(destino))
                    res.arquivo = f"clipes/{destino.name}"
                except Exception as erro:
                    log(f"  sem video: {erro}")
            shutil.rmtree(tmp, ignore_errors=True)
        return res

    # ------------------------------------------------------------------ apoio de dados
    def _vitrine(self) -> list[dict[str, Any]]:
        resp = self.api.get("/api/v1/veiculos/a-venda", params={"limite": 100})
        return list(resp.json().get("itens", [])) if resp.ok else []

    def veiculo_id(self, chave: str, modelo: str) -> str:
        vid = self.dados["veiculos"].get(chave)
        if vid:
            return str(vid)
        # fallback: procura na vitrine publica (cadastro regravado em outra execucao)
        for item in self._vitrine():
            if item.get("modelo") == modelo:
                self.dados["veiculos"][chave] = item["veiculo_id"]
                return str(item["veiculo_id"])
        raise PassoFalhouError(f"veiculo {modelo} nao encontrado (o cadastro falhou?)")

    def anuncio(self, chave: str, modelo: str) -> tuple[str, int]:
        """Id e versao atuais do anuncio, lidos da vitrine do servico de venda."""
        vid = self.veiculo_id(chave, modelo)
        resp = self.api.get(f"/api/v1/anuncios/{vid}")
        if not resp.ok:
            raise PassoFalhouError(f"anuncio {modelo} indisponivel ({resp.status})")
        return vid, int(resp.json()["versao"])

    def esperar_na_vitrine(self, modelos: list[str], limite_s: float = 60) -> None:
        """A replica chega pela outbox do principal: espera todos os modelos na vitrine."""
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim:
            presentes = {i.get("modelo") for i in self._vitrine()}
            if all(m in presentes for m in modelos):
                return
            time.sleep(1)
        raise PassoFalhouError(f"vitrine sem {modelos} depois de {limite_s:.0f} s")

    def esperar_status_compra(self, compra_id: str, status: str, limite_s: float = 60) -> None:
        fim = time.monotonic() + limite_s
        atual = ""
        while time.monotonic() < fim:
            resp = self.api.get(f"/api/v1/compras/{compra_id}")
            atual = resp.json().get("status", "") if resp.ok else f"HTTP {resp.status}"
            if atual == status:
                return
            time.sleep(1)
        raise PassoFalhouError(f"compra {compra_id} em {atual}, esperado {status}")


# --------------------------------------------------------------------------- legendas de resultado
def resumo_resposta(status: int, corpo: Any) -> str:
    """Legenda do resultado, montada a partir da resposta HTTP real (nada presumido)."""
    if not isinstance(corpo, dict):
        return f"Resposta {status}"
    if "type" in corpo and "title" in corpo:
        return f"Resposta {status}: {corpo['title']} ({str(corpo['type']).rsplit(':', 1)[-1]})"
    if "itens" in corpo:
        itens = corpo.get("itens") or []
        precos = " < ".join(
            f"{i.get('modelo', '?')} {i.get('preco', i.get('preco_venda', ''))}" for i in itens[:4]
        )
        return f"Resposta {status}: {corpo.get('total', len(itens))} veículo(s)" + (
            f": {precos}" if precos else ""
        )
    if "efeito" in corpo and "status_cobranca" in corpo:
        return (
            f"Resposta {status}: cobrança {corpo.get('status_cobranca')}, "
            f"efeito {corpo.get('efeito')}"
        )
    if "cpf" in corpo and "veiculo" in corpo:
        texto = f"Resposta {status}: compra {corpo.get('status')}"
        if corpo.get("codigo_pagamento"):
            texto += f", pagamento {corpo['codigo_pagamento']}"
        if corpo.get("cpf"):
            texto += f", CPF {corpo['cpf']}"
        if corpo.get("data_venda"):
            texto += f", vendido em {corpo['data_venda']}"
        if corpo.get("motivo_cancelamento"):
            texto += f" ({corpo['motivo_cancelamento']})"
        return texto
    if "versao" in corpo and "status" in corpo:
        return (
            f"Resposta {status}: {corpo.get('modelo')} {corpo.get('preco')}, "
            f"{corpo.get('status')}, versão {corpo.get('versao')}"
        )
    return f"Resposta {status}"


VEICULOS = {
    "corolla": {
        "marca": "Toyota",
        "modelo": "Corolla XEi 2.0",
        "ano": 2022,
        "cor": "Prata",
        "preco": "124900.00",
    },
    "argo": {
        "marca": "Fiat",
        "modelo": "Argo Drive 1.3",
        "ano": 2023,
        "cor": "Vermelho",
        "preco": "79900.00",
    },
    "gol": {
        "marca": "Volkswagen",
        "modelo": "Gol 1.0 MPI",
        "ano": 2021,
        "cor": "Branco",
        "preco": "54900.00",
    },
}


def _jaeger_get(caminho: str, params: dict[str, Any]) -> Any:
    url = f"{JAEGER}{caminho}?{urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - endereco local fixo
        return json.loads(resp.read().decode("utf-8"))


def _hex_trace(valor: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{32}", valor or ""):
        return valor.lower()
    return base64.b64decode(valor).hex()


def trace_da_compra() -> str:
    """Trace mais recente de uma compra aceita: o que tem mais spans entre os que passam pelo
    Kong, pelo servico de venda e pelo principal (spans lidos da API v3 do Jaeger)."""
    agora = datetime.now(UTC)
    dados = _jaeger_get(
        "/api/v3/traces",
        {
            "query.service_name": "revenda-vendas",
            "query.start_time_min": (agora - timedelta(minutes=45)).isoformat(),
            "query.start_time_max": (agora + timedelta(minutes=1)).isoformat(),
            "query.num_traces": 100,
        },
    )
    traces: dict[str, dict[str, Any]] = {}
    for rs in (dados.get("result") or {}).get("resourceSpans", []):
        servico = ""
        for attr in rs.get("resource", {}).get("attributes", []):
            if attr.get("key") == "service.name":
                servico = attr.get("value", {}).get("stringValue", "")
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                tid = _hex_trace(sp.get("traceId", ""))
                t = traces.setdefault(
                    tid, {"servicos": set(), "spans": 0, "compra": False, "ini": 0}
                )
                t["servicos"].add(servico)
                t["spans"] += 1
                t["ini"] = max(t["ini"], int(sp.get("startTimeUnixNano", 0)))
                nome = sp.get("name", "")
                atributos = {a.get("key"): a.get("value", {}) for a in sp.get("attributes", [])}
                codigo = (
                    atributos.get("http.response.status_code")
                    or atributos.get("http.status_code")
                    or {}
                )
                status = str(codigo.get("intValue", codigo.get("stringValue", "")))
                if "compras" in nome and "POST" in nome and status in ("201", ""):
                    t["compra"] = True
    candidatos = [
        (len(t["servicos"]), t["spans"], t["ini"], tid)
        for tid, t in traces.items()
        if t["compra"] and "revenda-principal" in t["servicos"]
    ]
    if not candidatos:
        raise PassoFalhouError("nenhum trace de compra com o principal nos ultimos 45 minutos")
    return max(candidatos)[3]


# --------------------------------------------------------------------------- clipes de navegador
def montar_clipes_navegador(g: Gravador) -> list[Clipe]:
    segredo_webhook = os.environ.get("WEBHOOK_SECRET", "")

    def exigir(valor: str, nome: str) -> str:
        if not valor:
            raise PassoFalhouError(f"variavel {nome} ausente no container")
        return valor

    # ---------------------------------------------------------------- blocos 1, 2, 3 e 8
    def b1_readme(t: Tela) -> None:
        t.github("")
        t.pausa(3)
        leia = t.page.locator("article.markdown-body").first
        leia.wait_for(timeout=30_000)
        t.legenda("Repositório 1: serviço principal (cadastro, edição, cobrança e webhook)")
        t.rolar_ate(leia, ms=2200, margem=MARGEM_TOPO_GITHUB)
        t.pausa(4)
        t.legenda("O que é: as rotas do gestor e do processador, e os passos da saga")
        t.rolar_ate(t.titulo_md("1. O que é"), ms=2200, margem=MARGEM_TOPO_GITHUB)
        t.pausa(6)
        t.rolar_por(450, ms=3500)
        t.pausa(3)

    def b1_readme_vendas(t: Tela) -> None:
        t.github("", base=GH_V)
        t.pausa(3)
        leia = t.page.locator("article.markdown-body").first
        leia.wait_for(timeout=30_000)
        t.legenda("Repositório 2: serviço de venda (listagens e compra), com banco próprio")
        t.rolar_ate(leia, ms=2200, margem=MARGEM_TOPO_GITHUB)
        t.pausa(4)
        t.legenda("A saga da compra, orquestrada por este serviço")
        t.rolar_ate(t.titulo_md("2. A saga da compra"), ms=2200, margem=MARGEM_TOPO_GITHUB)
        t.pausa(7)
        t.rolar_por(350, ms=3000)
        t.pausa(3)

    def b2_arquitetura(t: Tela) -> None:
        t.doc_github(
            "docs/04-arquitetura.md",
            [
                (
                    "4.3 C4 nível 2: Containers",
                    "C4 nível 2: Kong na entrada; principal com PostgreSQL; venda com MongoDB",
                    7,
                    450,
                ),
                (
                    "4.5 Implantação",
                    "Implantação: namespaces principal, vendas, gateway e observabilidade",
                    5,
                    400,
                ),
            ],
        )

    def b2_saga(t: Tela) -> None:
        t.doc_github(
            "docs/02-modelagem-ddd.md",
            [("2.8 Saga da compra", "Saga da compra: passos, compensações e o pivô", 8, 500)],
        )

    def b3_terraform(t: Tela) -> None:
        t.ir(f"{GH}/tree/{BRANCH}/infra/terraform")
        t.aceitar_cookies()
        t.page.locator("table, [aria-labelledby='folders-and-files']").first.wait_for(
            timeout=30_000
        )
        t.legenda("Terraform do principal: cluster, Kong, PostgreSQL e observabilidade")
        t.pausa(6)
        t.ir(f"{GH_V}/tree/{BRANCH}/infra/terraform")
        t.page.locator("table, [aria-labelledby='folders-and-files']").first.wait_for(
            timeout=30_000
        )
        t.legenda("Terraform do serviço de venda: outro repositório, outro state")
        t.pausa(6)

    def b8_documentacao(t: Tela) -> None:
        t.github("")
        t.page.locator("article.markdown-body").first.wait_for(timeout=30_000)
        t.pausa(1)
        t.legenda("Documentação: requisitos, DDD, arquitetura, ADRs, LGPD, testes e DAS")
        t.rolar_ate(t.titulo_md("6. Documentação"), ms=3500, margem=MARGEM_TOPO_GITHUB)
        t.pausa(5)
        t.rolar_por(320, ms=3500)
        t.pausa(5)

    # ---------------------------------------------------------------- bloco 4
    def sonar(projeto: str, nome: str) -> Callable[[Tela], None]:
        def passo(t: Tela) -> None:
            erros: list[str] = []
            t.page.on("pageerror", lambda e: erros.append(str(e)[:200]))
            t.ir(f"{SONAR}{projeto}&branch=main")
            cobertura = t.page.get_by_text(re.compile(r"Coverage|Cobertura", re.I)).first
            for tentativa in range(4):
                try:
                    cobertura.wait_for(timeout=20_000)
                    break
                except PWTimeout:
                    log(f"  SonarCloud em branco ({tentativa + 1}); erros da pagina: {erros[-3:]}")
                    if tentativa == 3:
                        raise PassoFalhouError("SonarCloud nao carregou o resumo") from None
                    erros.clear()
                    t.page.reload(wait_until="domcontentloaded", timeout=45_000)
            # "Coverage" aparece antes do painel (menu, esqueleto): espera o Quality Gate e o
            # percentual de cobertura desenhados
            with contextlib.suppress(PWTimeout):
                t.page.get_by_text(re.compile(r"^\s*(Passed|Failed)\s*$")).first.wait_for(
                    timeout=20_000
                )
                t.page.get_by_text(re.compile(r"^\s*\d+(\.\d+)?%\s*$")).first.wait_for(
                    timeout=10_000
                )
            t.pausa(0.5)
            t.fechar_aviso_cookies()
            # O video comeca com o resumo ja na tela: o carregamento em branco fica de fora.
            t.inicio_corte = max(0.0, t.agora() - 0.3)
            t.pausa(2)
            t.legenda(f"SonarCloud, {nome}: Quality Gate e cobertura da main")
            t.pausa(8)

        return passo

    # ---------------------------------------------------------------- bloco 5
    def pr(base: str, numero: str, nome: str) -> Callable[[Tela], None]:
        def passo(t: Tela) -> None:
            t.github(f"/pull/{numero}", base=base)
            t.pausa(3)
            t.legenda(f"{nome}, PR #{numero}: título em Conventional Commits e squash merge")
            t.rolar_por(100000, ms=6000)
            t.pausa(3)
            t.legenda(
                f"{nome}, PR #{numero}: os checks obrigatórios (qualidade, testes, contrato, "
                "sonarcloud, imagem e infra)"
            )
            t.github(f"/pull/{numero}/checks", base=base)
            t.pausa(6)

        return passo

    def cd(base: str, run_id: str, nome: str) -> Callable[[Tela], None]:
        def passo(t: Tela) -> None:
            if run_id:
                t.github(f"/actions/runs/{run_id}", base=base)
            else:
                t.github("/actions/workflows/cd.yml", base=base)
            t.pausa(4)
            t.legenda(f"CD do {nome}: disparado pelo merge na main, no runner self-hosted")
            t.rolar_por(700, ms=5000)
            t.pausa(6)

        return passo

    # ---------------------------------------------------------------- bloco 6
    def b6_cadastrar(t: Tela) -> None:
        t.abrir_swagger("principal")
        t.legenda("Gestor: o Kong valida a chave do consumer gestor (header apikey, oculto)")
        t.pausa(2)
        op = t.abrir_operacao("POST", "/api/v1/veiculos")
        t.try_it_out(op)
        for n, (chave, carro) in enumerate(VEICULOS.items(), 1):
            t.legenda(
                f"Cadastro {n} de {len(VEICULOS)}: {carro['marca']} {carro['modelo']} "
                f"{carro['ano']}, R$ {carro['preco']}"
            )
            t.preencher_corpo(op, carro)
            status, corpo = t.executar(op, "POST", r"^/api/v1/veiculos$")
            t.pausa(0.3)
            t.legenda(resumo_resposta(status, corpo))
            t.rolar_ate_resposta(op, pausa=2.2, rolar_corpo=(n == 1))
            if status != 201:
                raise PassoFalhouError(
                    f"POST /api/v1/veiculos respondeu {status}: {str(corpo)[:200]}"
                )
            g.dados["veiculos"][chave] = corpo["id"]

    def b6_editar(t: Tela) -> None:
        gol = g.veiculo_id("gol", VEICULOS["gol"]["modelo"])
        t.abrir_swagger("principal")
        t.legenda("Gestor edita o preço do Gol: nova versão, publicada na vitrine")
        op = t.abrir_operacao("PATCH", "/api/v1/veiculos/{veiculo_id}")
        t.try_it_out(op)
        t.preencher_parametro(op, "veiculo_id", gol)
        t.preencher_corpo(op, {"preco": "52900.00"})
        status, corpo = t.executar(op, "PATCH", r"^/api/v1/veiculos/[0-9a-f-]+$")
        t.legenda(resumo_resposta(status, corpo))
        t.rolar_ate_resposta(op)
        if status != 200:
            raise PassoFalhouError(f"PATCH respondeu {status}: {str(corpo)[:200]}")

    def listar(caminho: str, legenda: str | None = None) -> Callable[[Tela], None]:
        def passo(t: Tela) -> None:
            if legenda:
                t.legenda(legenda)
            op = t.abrir_operacao("GET", caminho)
            t.try_it_out(op)
            status, corpo = t.executar(op, "GET", "^" + re.escape(caminho) + "$")
            t.legenda(resumo_resposta(status, corpo))
            t.rolar_ate_resposta(op, pausa=3)
            if status != 200:
                raise PassoFalhouError(f"GET {caminho} respondeu {status}")

        return passo

    def b6_vitrine(t: Tela) -> None:
        g.esperar_na_vitrine([c["modelo"] for c in VEICULOS.values()])
        t.abrir_swagger("vendas")
        listar(
            "/api/v1/veiculos/a-venda",
            "Serviço de venda: a vitrine chegou pela outbox; do mais barato ao mais caro",
        )(t)

    def comprar(chave: str, esperado: int, legenda: str, guardar: str) -> Callable[[Tela], None]:
        def passo(t: Tela) -> None:
            vid, versao = g.anuncio(chave, VEICULOS[chave]["modelo"])
            t.abrir_swagger("vendas")
            t.legenda(legenda)
            op = t.abrir_operacao("POST", "/api/v1/compras")
            t.try_it_out(op)
            t.preencher_corpo(op, {"veiculo_id": vid, "versao": versao, "cpf": CPF_DEMO})
            status, corpo = t.executar(op, "POST", r"^/api/v1/compras$")
            t.legenda(resumo_resposta(status, corpo))
            t.rolar_ate_resposta(op, pausa=3.5)
            if status != esperado:
                raise PassoFalhouError(
                    f"POST /api/v1/compras respondeu {status} (esperado {esperado}): {str(corpo)[:200]}"
                )
            if status == 201 and guardar:
                g.dados["compras"][guardar] = {
                    "id": corpo["id"],
                    "codigo_pagamento": corpo.get("codigo_pagamento"),
                    "status": corpo["status"],
                }

        return passo

    def b6_edicao_bloqueada(t: Tela) -> None:
        argo = g.veiculo_id("argo", VEICULOS["argo"]["modelo"])
        t.abrir_swagger("principal")
        t.legenda("Gestor tenta editar o Argo reservado: o principal recusa (veículo bloqueado)")
        op = t.abrir_operacao("PATCH", "/api/v1/veiculos/{veiculo_id}")
        t.try_it_out(op)
        t.preencher_parametro(op, "veiculo_id", argo)
        t.preencher_corpo(op, {"preco": "75900.00"})
        status, corpo = t.executar(op, "PATCH", r"^/api/v1/veiculos/[0-9a-f-]+$")
        t.legenda(resumo_resposta(status, corpo))
        t.rolar_ate_resposta(op, pausa=3)
        if status != 409:
            raise PassoFalhouError(f"PATCH do Argo reservado respondeu {status} (esperado 409)")

    def b6_webhook(t: Tela) -> None:
        segredo = exigir(segredo_webhook, "WEBHOOK_SECRET")
        compra = g.dados["compras"].get("argo")
        if not compra or not compra.get("codigo_pagamento"):
            raise PassoFalhouError("compra do Argo sem codigo de pagamento (a compra falhou?)")
        t.abrir_swagger("principal")
        t.legenda("Processador de pagamentos: chave do seu consumer no Kong e o segredo do webhook")
        t.autorizar_api_key(segredo)
        t.legenda("Pagamento efetuado: POST /api/v1/pagamentos/webhook com o código da compra")
        op = t.abrir_operacao("POST", "/api/v1/pagamentos/webhook")
        t.try_it_out(op)
        t.preencher_corpo(
            op, {"codigo_pagamento": compra["codigo_pagamento"], "status": "PAGO"}, atraso_ms=8
        )
        status, corpo = t.executar(op, "POST", r"^/api/v1/pagamentos/webhook$")
        t.legenda(resumo_resposta(status, corpo))
        t.rolar_ate_resposta(op, pausa=3.5)
        if status != 200:
            raise PassoFalhouError(f"webhook respondeu {status}: {str(corpo)[:200]}")

    def b6_efetivada(t: Tela) -> None:
        compra = g.dados["compras"].get("argo")
        if not compra:
            raise PassoFalhouError("compra do Argo nao registrada")
        g.esperar_status_compra(compra["id"], "EFETIVADA")
        t.abrir_swagger("vendas")
        t.legenda("O principal repassou o resultado pela outbox: a saga efetivou a compra")
        op = t.abrir_operacao("GET", "/api/v1/compras/{compra_id}")
        t.try_it_out(op)
        t.preencher_parametro(op, "compra_id", compra["id"])
        status, corpo = t.executar(op, "GET", r"^/api/v1/compras/[0-9a-f-]+$")
        t.legenda(resumo_resposta(status, corpo))
        t.rolar_ate_resposta(op, pausa=4)
        if status != 200 or not isinstance(corpo, dict) or corpo.get("status") != "EFETIVADA":
            raise PassoFalhouError(f"compra nao efetivada: {status} {str(corpo)[:200]}")
        t.rolar_por(-100000, ms=1200)
        listar(
            "/api/v1/veiculos/vendidos",
            "Vendidos, do mais barato ao mais caro, sem dados do comprador",
        )(t)

    def b6_jaeger(t: Tela) -> None:
        trace = trace_da_compra()
        g.dados["trace_compra"] = trace
        t.ir(f"{JAEGER}/trace/{trace}")
        with contextlib.suppress(PWTimeout):
            t.page.get_by_text("revenda-principal").first.wait_for(timeout=45_000)
        t.pausa(2)
        t.legenda("Jaeger: a compra de ponta a ponta, do Kong ao serviço de venda e ao principal")
        t.pausa(6)
        t.legenda("Spans dos bancos: MongoDB no serviço de venda, PostgreSQL no principal")
        t.rolar_por(500, ms=4000)
        t.pausa(6)

    # ---------------------------------------------------------------- bloco 7
    def b7_principal_fora(t: Tela) -> None:
        t.abrir_swagger("vendas")
        listar(
            "/api/v1/veiculos/a-venda",
            "Principal fora do ar: a vitrine continua respondendo (réplica local)",
        )(t)
        vid, versao = g.anuncio("corolla", VEICULOS["corolla"]["modelo"])
        t.rolar_por(-100000, ms=1200)
        t.legenda("Compra do Corolla com o principal fora")
        op = t.abrir_operacao("POST", "/api/v1/compras")
        t.try_it_out(op)
        t.preencher_corpo(op, {"veiculo_id": vid, "versao": versao, "cpf": CPF_DEMO})
        status, corpo = t.executar(op, "POST", r"^/api/v1/compras$")
        t.legenda(resumo_resposta(status, corpo) + " (com Retry-After)")
        t.rolar_ate_resposta(op, pausa=4)
        if status != 503:
            raise PassoFalhouError(f"compra com o principal fora respondeu {status} (esperado 503)")
        t.rolar_por(-100000, ms=1200)
        t.legenda("A compra foi desfeita na hora: o Corolla continua à venda")
        op = t.abrir_operacao("GET", "/api/v1/anuncios/{veiculo_id}")
        t.try_it_out(op)
        t.preencher_parametro(op, "veiculo_id", vid)
        status, corpo = t.executar(op, "GET", r"^/api/v1/anuncios/[0-9a-f-]+$")
        t.legenda(
            f"Resposta {status}: anúncio {corpo.get('status') if isinstance(corpo, dict) else ''}"
        )
        t.rolar_ate_resposta(op, pausa=4)

    def b7_grafana(t: Tela) -> None:
        t.ir(f"{GRAFANA}/d/revenda-saga?orgId=1&from=now-30m&to=now&refresh=10s&kiosk")
        t.page.get_by_text("Saga da compra").first.wait_for(timeout=45_000)
        t.pausa(4)
        t.legenda("Grafana: o funil da compra e as compras desfeitas por falha")
        t.pausa(6)
        t.legenda("Resiliência: estado do circuit breaker e chamadas ao principal por resultado")
        t.rolar_por(520, ms=3500)
        t.pausa(7)
        t.legenda("Outbox: o que o principal entrega ao serviço de venda")
        t.rolar_por(520, ms=3500)
        t.pausa(5)

    def b7_prometheus(t: Tela) -> None:
        t.ir(f"{PROMETHEUS}/alerts")
        t.page.get_by_text("SagasParadas").first.wait_for(timeout=45_000)
        t.pausa(2)
        t.legenda("Prometheus: regras de alerta versionadas e testadas no CI (promtool)")
        t.pausa(5)
        t.rolar_por(600, ms=4000)
        t.pausa(4)

    c = Clipe
    p, v = "principal", "venda"
    return [
        c("b1_01_readme", 1, 110, "README do serviço principal", "navegador", None, b1_readme),
        c(
            "b1_02_readme_vendas",
            1,
            120,
            "README do serviço de venda",
            "navegador",
            None,
            b1_readme_vendas,
        ),
        c(
            "b2_01_arquitetura",
            2,
            210,
            "Arquitetura C4 - docs/04-arquitetura.md",
            "navegador",
            None,
            b2_arquitetura,
        ),
        c(
            "b2_02_saga",
            2,
            220,
            "Saga da compra - docs/02-modelagem-ddd.md",
            "navegador",
            None,
            b2_saga,
        ),
        c(
            "b3_01_terraform",
            3,
            390,
            "Terraform dos dois repositórios",
            "navegador",
            None,
            b3_terraform,
        ),
        c(
            "b4_01_sonar_principal",
            4,
            430,
            "SonarCloud do serviço principal",
            "navegador",
            None,
            sonar(SONAR_P, p),
        ),
        c(
            "b4_02_sonar_vendas",
            4,
            440,
            "SonarCloud do serviço de venda",
            "navegador",
            None,
            sonar(SONAR_V, v),
        ),
        c(
            "b5_01_pr_principal",
            5,
            520,
            f"Principal: PR #{PR} com os checks verdes",
            "navegador",
            None,
            pr(GH, PR, "Principal"),
        ),
        c(
            "b5_02_pr_vendas",
            5,
            530,
            f"Venda: PR #{PR_V} com os checks verdes",
            "navegador",
            None,
            pr(GH_V, PR_V, "Venda"),
        ),
        c("b5_03_cd_principal", 5, 540, "CD do principal", "navegador", None, cd(GH, CD_RUN_ID, p)),
        c(
            "b5_05_cd_vendas",
            5,
            560,
            "CD do serviço de venda",
            "navegador",
            None,
            cd(GH_V, CD_RUN_ID_V, "serviço de venda"),
        ),
        c(
            "b6_01_cadastro",
            6,
            601,
            "Gestor cadastra 3 veículos - POST /api/v1/veiculos",
            "navegador",
            "gestor",
            b6_cadastrar,
        ),
        c(
            "b6_02_edicao",
            6,
            602,
            "Gestor edita o preço do Gol - PATCH /api/v1/veiculos/{id}",
            "navegador",
            "gestor",
            b6_editar,
        ),
        c(
            "b6_03_vitrine",
            6,
            603,
            "Vitrine no serviço de venda - GET /api/v1/veiculos/a-venda",
            "navegador",
            "comprador",
            b6_vitrine,
        ),
        c(
            "b6_04_compra",
            6,
            604,
            "Compra do Argo com CPF - POST /api/v1/compras",
            "navegador",
            "comprador",
            comprar(
                "argo",
                201,
                "Comprador informa o CPF e compra o Argo - POST /api/v1/compras",
                "argo",
            ),
        ),
        c(
            "b6_05_segunda_compra",
            6,
            605,
            "Segunda compra do mesmo Argo - 409",
            "navegador",
            "comprador2",
            comprar("argo", 409, "Outro comprador tenta o mesmo Argo: a reserva impede", ""),
        ),
        c(
            "b6_06_edicao_bloqueada",
            6,
            606,
            "Gestor tenta editar o Argo reservado - 409",
            "navegador",
            "gestor",
            b6_edicao_bloqueada,
        ),
        c(
            "b6_07_webhook",
            6,
            607,
            "Processador confirma o pagamento - webhook PAGO",
            "navegador",
            "gateway",
            b6_webhook,
        ),
        c(
            "b6_08_efetivada",
            6,
            608,
            "Compra efetivada e vendidos - GET /api/v1/compras/{id}",
            "navegador",
            "comprador",
            b6_efetivada,
        ),
        c("b6_11_jaeger", 6, 680, "Trace da compra no Jaeger", "navegador", None, b6_jaeger),
        c(
            "b7_02_principal_fora",
            7,
            720,
            "Compra com o principal fora - 503 e compensação",
            "navegador",
            "comprador",
            b7_principal_fora,
        ),
        c(
            "b7_04_grafana",
            7,
            760,
            "Grafana: painel saga e resiliência",
            "navegador",
            None,
            b7_grafana,
        ),
        c(
            "b7_05_prometheus",
            7,
            770,
            "Prometheus: regras de alerta",
            "navegador",
            None,
            b7_prometheus,
        ),
        c(
            "b8_01_documentacao",
            8,
            810,
            "Documentação no README",
            "navegador",
            None,
            b8_documentacao,
        ),
    ]


# --------------------------------------------------------------------------- clipes de terminal
def carregar_config() -> dict[str, Any]:
    with open(DIR / "clipes.json", encoding="utf-8") as f:
        return json.load(f)


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MAX_LINHAS_TERMINAL = 300


def montar_clipes_terminal(cfg: dict[str, Any]) -> list[Clipe]:
    clipes = []
    for item in cfg["terminal"]:
        clipes.append(
            Clipe(
                item["id"],
                int(item["bloco"]),
                int(item["ordem"]),
                item["titulo"],
                "terminal",
                None,
                None,
                cfg=item,
            )
        )
    return clipes


def executor_terminal(
    cfg_geral: dict[str, Any], item: dict[str, Any], saida: str, comando: str
) -> Callable[[Tela], None]:
    def passo(t: Tela) -> None:
        t.ir((DIR / "terminal.html").as_uri())
        t.page.evaluate(
            "o => window.rodarTerminal(o)",
            {
                "prompt": cfg_geral["prompts"][item["origem"]],
                "janela": cfg_geral["janelas"][item["origem"]],
                "comando": comando,
                "saida": saida,
                "msPorCaractere": 26,
                "duracaoMaxSaidaMs": 14000,
                "pausaFinalMs": 3500,
            },
        )
        t.page.wait_for_function("() => window.__terminalFim === true", timeout=120_000)

    return passo


def preparar_terminal(cfg: dict[str, Any], clipe: Clipe) -> str | None:
    """Liga o clipe ao arquivo de saida real; devolve o motivo se precisar pular."""
    item = clipe.cfg
    arquivo = SAIDA / "terminal" / item["arquivo"]
    if not arquivo.is_file() or arquivo.stat().st_size == 0:
        return f"saida ausente: terminal/{item['arquivo']}"
    saida = ANSI.sub("", arquivo.read_text(encoding="utf-8-sig", errors="replace")).replace(
        "\r", ""
    )
    linhas = saida.rstrip("\n").split("\n")
    if len(linhas) > MAX_LINHAS_TERMINAL:
        omitidas = len(linhas) - MAX_LINHAS_TERMINAL
        aviso = (
            f"# ({omitidas} linhas iniciais omitidas no video; arquivo completo em "
            f".setup/video/terminal/{item['arquivo']})"
        )
        linhas = [aviso, *linhas[-MAX_LINHAS_TERMINAL:]]
    arquivo_cmd = arquivo.with_suffix(".cmd")
    comando = item["comando"]
    if arquivo_cmd.is_file():
        comando = arquivo_cmd.read_text(encoding="utf-8-sig", errors="replace").strip() or comando
    clipe.executar = executor_terminal(cfg, item, "\n".join(linhas), comando)
    return None


# --------------------------------------------------------------------------- manifesto
def atualizar_manifesto(resultados: list[Resultado], cfg: dict[str, Any]) -> Path:
    caminho = SAIDA / "manifesto.json"
    atual: dict[str, Any] = {"clipes": []}
    if caminho.is_file():
        try:
            atual = json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("aviso: manifesto.json ilegivel; recriando")
    por_id = {c["id"]: c for c in atual.get("clipes", [])}
    for r in resultados:
        por_id[r.id] = r.__dict__
    clipes = sorted(por_id.values(), key=lambda c: (c["ordem"], c["id"]))
    manifesto = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "viewport": VIEWPORT,
        "repositorio": REPO,
        "blocos": cfg["blocos"],
        "clipes": clipes,
    }
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho


def salvar_dados(g: Gravador) -> None:
    caminho = SAIDA / "dados.json"
    anterior: dict[str, Any] = {}
    if caminho.is_file():
        with contextlib.suppress(json.JSONDecodeError):
            anterior = json.loads(caminho.read_text(encoding="utf-8"))
    anterior.update({k: v for k, v in g.dados.items() if v})
    anterior["atualizado_em"] = datetime.now(UTC).isoformat(timespec="seconds")
    caminho.write_text(json.dumps(anterior, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- principal
def interpretar_lista(texto: str) -> set[str]:
    return {p for p in re.split(r"[,;\s]+", texto or "") if p}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--modo", choices=["navegador", "terminal"], required=True)
    ap.add_argument("--blocos", default=os.environ.get("VIDEO_BLOCOS", "1,2,3,4,5,6,7,8"))
    ap.add_argument("--clipes", default="", help="ids (ou prefixos) para regravar so alguns clipes")
    ap.add_argument(
        "--excluir", default="", help="ids (ou prefixos) que ficam de fora desta passada"
    )
    ap.add_argument("--sem-opcionais", action="store_true", help="nao grava os clipes opcionais")
    ap.add_argument("--listar", action="store_true", help="so lista os clipes que seriam gravados")
    args = ap.parse_args()

    blocos = {int(b) for b in interpretar_lista(args.blocos)}
    filtros = interpretar_lista(args.clipes)
    excluidos = interpretar_lista(args.excluir)
    cfg = carregar_config()
    for sub in ("clipes", "falhas", "terminal"):
        (SAIDA / sub).mkdir(parents=True, exist_ok=True)

    resultados: list[Resultado] = []
    with sync_playwright() as pw:
        g = Gravador(pw)
        try:
            if args.modo == "navegador":
                clipes = montar_clipes_navegador(g)
            else:
                clipes = montar_clipes_terminal(cfg)
            clipes = [
                c
                for c in clipes
                if c.bloco in blocos
                and (not filtros or any(c.id.startswith(f) for f in filtros))
                and not any(c.id.startswith(f) for f in excluidos)
                and not (args.sem_opcionais and c.opcional)
            ]
            clipes.sort(key=lambda c: c.ordem)
            if args.listar:
                for c in clipes:
                    print(f"{c.ordem:4d}  {c.id:28s} {c.titulo}")
                return 0
            log(f"modo {args.modo}: {len(clipes)} clipe(s); blocos {sorted(blocos)}; API {API}")
            for clipe in clipes:
                if clipe.tipo == "terminal":
                    motivo = preparar_terminal(cfg, clipe)
                    if motivo:
                        log(f"> {clipe.id}: PULADO ({motivo})")
                        r = Resultado(
                            clipe.id,
                            clipe.bloco,
                            clipe.ordem,
                            clipe.titulo,
                            clipe.tipo,
                            clipe.opcional,
                        )
                        r.status, r.erro = "PULADO", motivo
                        r.gravado_em = datetime.now(UTC).isoformat(timespec="seconds")
                        (SAIDA / "clipes" / f"{clipe.id}.webm").unlink(missing_ok=True)
                        resultados.append(r)
                        continue
                resultados.append(g.gravar(clipe))
                if args.modo == "navegador":
                    salvar_dados(g)
        finally:
            g.fechar()
            shutil.rmtree(SAIDA / "tmp", ignore_errors=True)

    caminho = atualizar_manifesto(resultados, cfg)
    print("")
    print("Resumo:")
    for r in resultados:
        extra = f"  ({r.erro})" if r.erro else ""
        print(f"  {r.status:7s} {r.id:28s} {r.duracao_s:6.1f} s{extra}")
    print(f"Manifesto: {caminho}")
    falhas = [r for r in resultados if r.status == "FALHOU" and not r.opcional]
    return 3 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
