// Terminal simulado (scripts/video/terminal.html). Digita o comando real e imprime a saida
// real recebida de gravar.py, com rolagem automatica. Ao terminar, define
// window.__terminalFim = true (gravar.py espera por isso antes de encerrar o clipe).
(function () {
  "use strict";

  const tela = document.getElementById("tela");
  const tituloJanela = document.getElementById("titulo-janela");
  const icone = document.getElementById("icone");

  // Prompt colorido como no terminal real: PowerShell todo em cinza; bash com usuario@host
  // em verde e o diretorio em azul.
  function desenharPrompt(alvo, prompt) {
    const m = /^([^@\s]+@[^:\s]+)(:)([^$#]*)([$#] ?)$/.exec(prompt);
    if (!m) {
      const p = document.createElement("span");
      p.className = "prompt";
      p.textContent = prompt;
      alvo.appendChild(p);
      return;
    }
    for (const [txt, cls] of [[m[1], "p-user"], [m[2], "prompt"], [m[3], "p-dir"], [m[4], "prompt"]]) {
      const s = document.createElement("span");
      s.className = cls;
      s.textContent = txt;
      alvo.appendChild(s);
    }
  }

  const LARGURA_CARACTERE = 0.602; // proporcao largura/altura das fontes mono usadas
  const FONTE_MIN = 15;
  const FONTE_MAX = 24;

  // Tokens coloridos: verde para sucesso, vermelho para falha, amarelo para avisos.
  const TOKENS = [
    [/\b(PASSED|passed|Running|Completed|online|success|succeeded|EFETIVADA|VENDIDO|A_VENDA|Ready|True|true|OK|ok|Bound|Active|met|rolled out|reached)\b/, "ok"],
    [/\b(FAILED|failed|ERROR|Error|error|CrashLoopBackOff|failure|offline|Pending|CANCELADA)\b/, "erro"],
    [/\b(WARNING|Warning|AVISO|RESERVADO|AGUARDANDO_PAGAMENTO|skipped)\b/, "aviso"],
  ];
  const RE_TOKENS = new RegExp(TOKENS.map(([re]) => re.source).join("|"), "g");

  function classeDoToken(token) {
    for (const [re, classe] of TOKENS) {
      if (new RegExp("^(?:" + re.source + ")$").test(token)) return classe;
    }
    return null;
  }

  function colorir(linha) {
    const frag = document.createDocumentFragment();
    let ultimo = 0;
    RE_TOKENS.lastIndex = 0;
    let m;
    while ((m = RE_TOKENS.exec(linha)) !== null) {
      if (m.index > ultimo) frag.appendChild(document.createTextNode(linha.slice(ultimo, m.index)));
      const span = document.createElement("span");
      span.className = classeDoToken(m[0]) || "";
      span.textContent = m[0];
      frag.appendChild(span);
      ultimo = m.index + m[0].length;
      if (m[0].length === 0) RE_TOKENS.lastIndex++;
    }
    if (ultimo < linha.length) frag.appendChild(document.createTextNode(linha.slice(ultimo)));
    return frag;
  }

  function esperar(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function rolarFim() {
    tela.scrollTop = tela.scrollHeight;
  }

  function ajustarFonte(linhas) {
    const maior = Math.min(220, linhas.reduce((m, l) => Math.max(m, l.length), 0));
    const disponivel = window.innerWidth - 56;
    let fonte = FONTE_MAX;
    if (maior > 0) fonte = Math.floor(disponivel / (maior * LARGURA_CARACTERE));
    fonte = Math.max(FONTE_MIN, Math.min(FONTE_MAX, fonte));
    document.documentElement.style.setProperty("--fonte", fonte + "px");
    return fonte;
  }

  function novaLinha() {
    const div = document.createElement("div");
    tela.appendChild(div);
    return div;
  }

  async function digitar(alvo, texto, msPorCaractere) {
    const comentario = texto.trimStart().startsWith("#");
    const primeiro = document.createElement("span");
    primeiro.className = comentario ? "coment" : "cmd1";
    alvo.appendChild(primeiro);
    const span = document.createElement("span");
    span.className = comentario ? "coment" : "cmd";
    alvo.appendChild(span);
    const corte = comentario ? texto.length : (texto.search(/\s/) < 0 ? texto.length : texto.search(/\s/));
    const cursor = document.createElement("span");
    cursor.id = "cursor";
    cursor.textContent = " ";
    alvo.appendChild(cursor);
    let k = 0;
    for (const c of texto) {
      (k++ < corte ? primeiro : span).textContent += c;
      rolarFim();
      if (msPorCaractere >= 4) await esperar(msPorCaractere + (c === " " ? msPorCaractere : 0));
      else if (Math.random() < msPorCaractere / 4) await esperar(4);
    }
    cursor.remove();
  }

  // opcoes: { prompt, janela, comando, saida, msPorCaractere, duracaoMaxSaidaMs, pausaFinalMs }
  window.rodarTerminal = async function (opcoes) {
    window.__terminalFim = false;
    const prompt = opcoes.prompt || "PS C:\\Projetos\\fiap\\fase4> ";
    const comandos = String(opcoes.comando || "").replace(/\r/g, "").split("\n").filter((l) => l.length);
    const saida = String(opcoes.saida || "").replace(/\r/g, "").replace(/\n+$/, "");
    const linhasSaida = saida.length ? saida.split("\n") : [];
    tituloJanela.textContent = opcoes.janela || "Windows PowerShell";
    icone.className = "icone " + (prompt.startsWith("PS ") ? "ps" : "bash");
    tela.textContent = "";
    ajustarFonte(linhasSaida.length ? linhasSaida : comandos.map((c) => prompt + c));

    await esperar(600);
    for (const comando of comandos) {
      const linha = novaLinha();
      desenharPrompt(linha, prompt);
      await esperar(250);
      // comandos longos: no maximo ~4 s de digitacao por linha
      const ms = Math.min(opcoes.msPorCaractere || 28, 4000 / Math.max(comando.length, 1));
      await digitar(linha, comando, ms);
      await esperar(comando.trimStart().startsWith("#") ? 150 : 500);
    }

    // Saida: ritmo de "rolagem de terminal", com teto de duracao para saidas longas.
    const n = linhasSaida.length;
    const total = Math.max(1200, Math.min(opcoes.duracaoMaxSaidaMs || 14000, n * 70));
    const porLinha = n ? total / n : 0;
    const lote = Math.max(1, Math.ceil(16 / Math.max(porLinha, 1)));
    for (let i = 0; i < n; i += lote) {
      for (let j = i; j < Math.min(n, i + lote); j++) {
        novaLinha().appendChild(colorir(linhasSaida[j]));
      }
      rolarFim();
      await esperar(porLinha * lote);
    }
    const fim = novaLinha();
    desenharPrompt(fim, prompt);
    const cursor = document.createElement("span");
    cursor.id = "cursor";
    cursor.textContent = " ";
    fim.appendChild(cursor);
    rolarFim();
    await esperar(opcoes.pausaFinalMs || 4000);
    window.__terminalFim = true;
  };
})();
