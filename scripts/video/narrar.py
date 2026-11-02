"""Gera a narracao (TTS: edge-tts ou Piper) sincronizada com as cenas e monta o video final.

Roda dentro do container revenda-video (ffmpeg + Python). Entrada:
  /saida/montagem/video-final-sem-voz.mp4 video montado
  /saida/montagem/montagem.json           linha do tempo (inicio/fim de cada clipe)
  /video/sincronia.json                   falas ancoradas por cena e cortes de trechos parados
Saida:
  /saida/voz/fNN_<clipe>.wav              audio de cada fala
  /saida/montagem/video-final.mp4         video reeditado (cortes e congelamentos) com a narracao
  /saida/montagem/video-final.srt         legenda da narracao, no tempo do video final

Cada fala comeca no instante da sua cena. Se a fala anterior ainda nao terminou, o ultimo
quadro antes da cena fica congelado ate ela terminar, entao voz e imagem nunca se separam.

Modo alternativo (--somente-juntar): usa /saida/voz/blocoN.(wav|mp3) feitos por outra
ferramenta (ex.: sua propria voz), um por bloco, sem reeditar o video.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SAIDA = Path(os.environ.get("NARRAR_SAIDA", "/saida"))
KIT = Path(os.environ.get("NARRAR_KIT", Path(__file__).resolve().parent))
MONTAGEM = SAIDA / "montagem"
VOZ = SAIDA / "voz"
MODELOS = SAIDA / "modelos-voz"
CABECALHO = re.compile(r"^Bloco\s+(\d+)\s+.*\((\d+):(\d{2})\s+a\s+(\d+):(\d{2})\)\s*$")
VOZ_PADRAO = {
    "edge": "pt-BR-AntonioNeural",
    "piper": "pt_BR-faber-medium",
    "xtts": "/saida/voz-referencia",
}
VOZ_RESERVA_URL = (
    "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-pt-br-edresson-low.tar.gz"
)
FOLGA = 0.3  # silencio minimo entre duas falas (s)


def log(msg: str) -> None:
    print(msg, flush=True)


def rodar(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def duracao(arquivo: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(arquivo),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return float(out)


def preparar_texto(texto: str) -> str:
    # Pronuncia mais natural de siglas e simbolos comuns no roteiro.
    trocas = {
        "API": "A P I",
        "CI": "C I",
        "CD": "C D",
        "PR ": "P R ",
        "PRs": "P Rs",
        "UUID": "U U I D",
        "SHA": "SHA",
        "HPA": "H P A",
        "PKCE": "P K C E",
        "LGPD": "L G P D",
        "DDD": "D D D",
        "ADRs": "A D Rs",
        "ADR": "A D R",
        "CPF": "C P F",
        "JWT": "J W T",
        "C4": "C 4",
        "UPDATE": "update",
        "6º": "sexto",
        "1.34": "um ponto trinta e quatro",
        "409": "quatrocentos e nove",
        "201": "duzentos e um",
        "200": "duzentos",
        "80%": "oitenta por cento",
        "127.0.0.1": "cento e vinte e sete ponto zero ponto zero ponto um",
        "gestor.loja": "gestor ponto loja",
        "comprador_id": "comprador I D",
        "revenda-api": "revenda A P I",
        "PósTech": "Pós Tech",
        "kind": "káind",
        "Keycloak": "Kí-cloak",
        "webhook": "uébi-húk",
        "Swagger": "Suágger",
        "Terraform": "Terrafórm",
        "runner": "râner",
        "squash merge": "skuóch mârdj",
        "main": "mêin",
        "badge": "bédj",
        "README": "rid-mi",
        "FastAPI": "Fast A P I",
        "PostgreSQL": "Postgres",
        "Docker Desktop": "Dóquer Désktop",
    }
    for de, para in trocas.items():
        texto = re.sub(rf"(?<![\w.]){re.escape(de)}(?![\w])", para, texto)
    return texto


def preparar_texto_edge(texto: str) -> str:
    # Vozes neurais leem bem portugues; so ajuda em siglas e termos em ingles.
    trocas = {
        "API": "A P I",
        "CI": "C I",
        "CD": "C D",
        "PR ": "P R ",
        "PRs": "P Rs",
        "UUID": "U U I D",
        "HPA": "H P A",
        "PKCE": "P K C E",
        "LGPD": "L G P D",
        "DDD": "D D D",
        "ADRs": "A D Rs",
        "ADR": "A D R",
        "CPF": "C P F",
        "6º": "sexto",
        "1.34": "1 ponto 34",
        "127.0.0.1": "127 ponto 0 ponto 0 ponto 1",
        "gestor.loja": "gestor ponto loja",
        "comprador_id": "comprador I D",
        "revenda-api": "revenda A P I",
        "PósTech": "Pós Tech",
        "kind": "cáind",
        "Keycloak": "Kicloak",
        "README": "ríd-mi",
        "FastAPI": "Fast A P I",
        "PostgreSQL": "Postgres",
        "Trivy": "Trívi",
    }
    for de, para in trocas.items():
        texto = re.sub(rf"(?<![\w.]){re.escape(de)}(?![\w])", para, texto)
    return texto


class Motor:
    """Motor de voz: edge (vozes neurais Microsoft), piper (local) ou xtts (clone da voz do
    autor a partir de trechos de referencia, XTTS-v2 da Coqui, local e em CPU)."""

    def __init__(self, nome: str, voz: str, velocidade: float, taxa: str) -> None:
        self.nome, self.voz, self.velocidade, self.taxa = nome, voz, velocidade, taxa
        self.modelo = obter_modelo(voz) if nome == "piper" else None
        self.xtts = None
        self.referencias: list[str] = []
        if nome == "xtts":
            pasta = Path(voz)
            self.referencias = sorted(str(a) for a in pasta.glob("*.wav"))
            if not self.referencias:
                raise SystemExit(f"nenhum trecho de referencia .wav em {pasta}")
            log(f"voz de referencia: {len(self.referencias)} trecho(s) em {pasta}")
            os.environ.setdefault("COQUI_TOS_AGREED", "1")
            from TTS.api import TTS  # import tardio: so existe na imagem revenda-voz

            self.xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

    def chave(self, texto: str) -> str:
        """Identifica a sintese (motor, voz, parametros, texto) para reaproveitar audios."""
        import hashlib

        base = f"{self.nome}|{self.voz}|{self.velocidade}|{self.taxa}|{texto}"
        if self.referencias:
            base += "|" + "|".join(f"{r}:{Path(r).stat().st_size}" for r in self.referencias)
        return hashlib.sha256(base.encode()).hexdigest()[:16]

    def falar(self, texto: str, destino: Path) -> None:
        marca = destino.with_suffix(".chave")
        chave = self.chave(texto)
        if destino.exists() and marca.exists() and marca.read_text() == chave:
            return  # mesma fala ja sintetizada: reaproveita
        self._falar(texto, destino)
        marca.write_text(chave)

    def _falar(self, texto: str, destino: Path) -> None:
        if self.nome == "piper":
            sintetizar(self.modelo, preparar_texto(texto), destino, self.velocidade)
            return
        if self.nome == "xtts":
            bruto = destino.with_name(destino.stem + "_xtts.wav")
            self.xtts.tts_to_file(
                text=preparar_texto_edge(texto),
                speaker_wav=self.referencias,
                language="pt",
                file_path=str(bruto),
                speed=self.velocidade,
            )
            rodar(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(bruto),
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    str(destino),
                ]
            )
            bruto.unlink(missing_ok=True)
            return
        mp3 = destino.with_suffix(".mp3")
        for tentativa in range(4):
            try:
                rodar(
                    [
                        sys.executable,
                        "-m",
                        "edge_tts",
                        "--voice",
                        self.voz,
                        f"--rate={self.taxa}",
                        "--text",
                        preparar_texto_edge(texto),
                        "--write-media",
                        str(mp3),
                    ],
                    stderr=subprocess.DEVNULL,
                )
                break
            except subprocess.CalledProcessError:
                if tentativa == 3:
                    raise
                log("  falha na sintese (rede?); tentando de novo")
        rodar(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp3),
                "-ar",
                "48000",
                "-ac",
                "1",
                str(destino),
            ]
        )
        mp3.unlink(missing_ok=True)


def obter_modelo(voz: str) -> Path:
    MODELOS.mkdir(parents=True, exist_ok=True)
    onnx = MODELOS / f"{voz}.onnx"
    if onnx.exists():
        return onnx
    log(f"baixando a voz {voz} (Hugging Face, rhasspy/piper-voices)")
    try:
        rodar([sys.executable, "-m", "piper.download_voices", "--data-dir", str(MODELOS), voz])
        if onnx.exists():
            return onnx
    except subprocess.CalledProcessError:
        log("  falhou; usando a voz reserva do GitHub (pt-br-edresson-low)")
    reserva = MODELOS / "pt-br-edresson-low.onnx"
    if not reserva.exists():
        pacote = MODELOS / "reserva.tar.gz"
        rodar(["curl", "-fsSL", "-o", str(pacote), VOZ_RESERVA_URL])
        rodar(["tar", "xzf", str(pacote), "-C", str(MODELOS)])
    return reserva


def sintetizar(modelo: Path, texto: str, destino: Path, escala: float) -> None:
    rodar(
        [
            sys.executable,
            "-m",
            "piper",
            "-m",
            str(modelo),
            "-f",
            str(destino),
            "--length-scale",
            f"{escala:.3f}",
            "--sentence-silence",
            "0.25",
        ],
        input=texto,
        text=True,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------- edicao sincronizada


class Edicao:
    """Cortes e congelamentos sobre a linha do tempo original do video."""

    def __init__(self, total: float, cortes: list[tuple[float, float]]) -> None:
        self.total = total
        self.cortes = sorted(cortes)
        self.congelas: list[list[float]] = []  # [ponto_original, segundos]

    def dentro_de_corte(self, t: float) -> float:
        for a, b in self.cortes:
            if a <= t < b:
                return b
        return t

    def novo(self, t: float) -> float:
        """Tempo no video final de um instante t do video original."""
        n = t
        for a, b in self.cortes:
            if t >= b:
                n -= b - a
            elif t > a:
                n -= t - a
        for p, d in self.congelas:
            if t >= p:
                n += d
        return n

    def congelar(self, p: float, d: float) -> None:
        if self.congelas and abs(self.congelas[-1][0] - p) < 1e-6:
            self.congelas[-1][1] += d
        else:
            self.congelas.append([p, d])

    def pecas(self) -> list[tuple[float, float, float]]:
        """(inicio, fim, congelar_no_fim) no video original."""
        eventos = [(a, "corte", b) for a, b in self.cortes] + [
            (p, "congela", d) for p, d in self.congelas
        ]
        eventos.sort(key=lambda e: (e[0], 0 if e[1] == "congela" else 1))
        pecas, pos, extra = [], 0.0, 0.0
        for t, tipo, v in eventos:
            t = min(t, self.total)
            if tipo == "congela":
                if t > pos + 0.04:
                    pecas.append((pos, t, extra + v))
                    pos, extra = t, 0.0
                else:
                    extra += v
            else:
                if t > pos + 0.04:
                    pecas.append((pos, t, extra))
                    extra = 0.0
                pos = max(pos, v)
        pecas.append((pos, self.total, extra))
        return [p for p in pecas if p[1] - p[0] > 0.04 or p[2] > 0]


# Trechos sem fala: nas paginas do GitHub o que passa da fala e so rolagem; corta o excesso e
# deixa uma folga. Grafana e Prometheus: so a cauda depois da ultima fala. Swagger (bloco 6) e
# terminal: nada e cortado, para nao esconder resultados que aparecem depois da fala.
SILENCIO_DEPOIS = 1.2  # s mantidos depois de cada fala
SILENCIO_ANTES = 0.6  # s mantidos antes da proxima fala
CAUDA_MAX = 3.0  # s mantidos depois da ultima fala de uma cena


def cortes_de_silencio(linha: list[dict], falas: list[dict]) -> list[tuple[float, float]]:
    por_clipe: dict[str, list[dict]] = {}
    for f in falas:
        por_clipe.setdefault(f["clipe"], []).append(f)
    cortes = []
    for item in linha:
        cid = item.get("id", "")
        lst = sorted(por_clipe.get(cid, []), key=lambda f: f["ancora"])
        if not lst or cid.startswith("cartela"):
            continue
        # Cenas de acao e de terminal nao sao cortadas: o resultado (resposta do Swagger, saida
        # do comando, a vitrine atualizada) costuma aparecer depois da fala e precisa ficar.
        if cid.startswith(("b6_", "b7_", "t")):
            continue
        so_cauda = cid.startswith("b9_")
        fala_fim = lst[0]["ancora"] + lst[0]["dur"]
        for prox in lst[1:]:
            a, b = fala_fim + SILENCIO_DEPOIS, prox["ancora"] - SILENCIO_ANTES
            if not so_cauda and b - a > 0.8:
                cortes.append((a, b))
            fala_fim = max(fala_fim, prox["ancora"] + prox["dur"])
        a, b = fala_fim + CAUDA_MAX, float(item["fim"])
        if b - a > 0.8:
            cortes.append((a, b))
    return cortes


# Quadro parado sem fala (pagina esperando um diagrama, terminal ja sem saida nova): qualquer
# trecho do video original com a imagem imovel por mais de PARADO_MIN s e cortado, menos o que
# estiver sob uma fala (o tempo de leitura) e as cartelas. A faixa da legenda fica de fora da
# deteccao (a parte de baixo da tela muda com a legenda, nao com a cena).
PARADO_MIN = 3.0  # s de imagem imovel para contar como parado
PARADO_MANTER = 0.8  # s mantidos no inicio de cada trecho parado (a imagem "assenta")
PARADO_RUIDO = 0.003  # tolerancia do freezedetect (compressao)


def trechos_parados(video: Path) -> list[tuple[float, float]]:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-map",
            "0:v",
            "-vf",
            f"crop=iw:ih*0.8:0:0,scale=480:-2,freezedetect=n={PARADO_RUIDO}:d={PARADO_MIN}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    inicios = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", proc.stderr)]
    fins = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", proc.stderr)]
    if len(fins) < len(inicios):
        fins.append(duracao(video))
    return list(zip(inicios, fins, strict=False))


def subtrair(
    trecho: tuple[float, float], protegidos: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    pedacos = [trecho]
    for pa, pb in protegidos:
        novos = []
        for a, b in pedacos:
            if pb <= a or pa >= b:
                novos.append((a, b))
                continue
            if pa > a:
                novos.append((a, pa))
            if pb < b:
                novos.append((pb, b))
        pedacos = novos
    return pedacos


def cortes_de_quadro_parado(
    video: Path, linha: list[dict], falas: list[dict]
) -> list[tuple[float, float]]:
    protegidos = [
        (f["ancora"] - SILENCIO_ANTES, f["ancora"] + f["dur"] + SILENCIO_DEPOIS) for f in falas
    ] + [
        (float(x["inicio"]), float(x["fim"]))
        for x in linha
        if str(x.get("id", "")).startswith("cartela")
    ]
    cortes = []
    for a, b in trechos_parados(video):
        for ca, cb in subtrair((a + PARADO_MANTER, b - 0.2), protegidos):
            if cb - ca > 1.0:
                cortes.append((ca, cb))
    return cortes


def unir_cortes(cortes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    res: list[list[float]] = []
    for a, b in sorted(cortes):
        if res and a <= res[-1][1]:
            res[-1][1] = max(res[-1][1], b)
        else:
            res.append([a, b])
    return [(a, b) for a, b in res]


ANCORA_MARCA = re.compile(r"^m(\d+)([+-]\d+(?:\.\d+)?)?$")


def deslocamento(off: float | str, marcas: list[dict], clipe: str) -> float:
    """Segundos desde o inicio do clipe. `off` e um numero ou "mN[+s]": a N-esima legenda
    marcada pelo gravar.py no clipe (m0 = a primeira), mais um ajuste opcional em segundos.
    Ancorar na legenda mantem a fala junto do passo mesmo que a gravacao mude de ritmo."""
    if isinstance(off, (int, float)):
        return float(off)
    m = ANCORA_MARCA.match(str(off).strip())
    if not m:
        raise SystemExit(f"ancora invalida em {clipe}: {off!r} (use segundos ou mN[+s])")
    ordenadas = sorted(marcas, key=lambda x: x["t"])
    n = int(m.group(1))
    if n >= len(ordenadas):
        log(f"aviso: {clipe} tem {len(ordenadas)} legendas; ancora {off} vai para a ultima")
        n = len(ordenadas) - 1
    base = ordenadas[n]["t"] if ordenadas else 0.0
    return max(0.0, base + float(m.group(2) or 0))


def carregar_falas(linha: list[dict], sinc: dict) -> list[dict]:
    inicio = {x["id"]: x["inicio"] for x in linha}
    marcas = {x["id"]: x.get("marcas", []) for x in linha}
    falas = []
    for clipe, lista in sinc["falas"].items():
        if clipe not in inicio:
            log(f"aviso: clipe {clipe} nao esta na montagem; falas ignoradas")
            continue
        for off, texto in lista:
            ancora = inicio[clipe] + deslocamento(off, marcas[clipe], clipe)
            falas.append(
                {"clipe": clipe, "clipe_ini": inicio[clipe], "ancora": ancora, "texto": texto}
            )
    falas.sort(key=lambda f: f["ancora"])
    return falas


def legenda_srt(falas: list[dict], destino: Path) -> None:
    def hms(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    linhas, n = [], 0
    for f in falas:
        frases = [s for s in re.split(r"(?<=[.:;!?])\s+", f["texto"]) if s]
        total = sum(len(s) for s in frases) or 1
        t = f["inicio"]
        for s in frases:
            d = f["dur"] * len(s) / total
            n += 1
            linhas += [str(n), f"{hms(t)} --> {hms(t + d)}", s, ""]
            t += d
    destino.write_text("\n".join(linhas), encoding="utf-8")


def reeditar_video(video: Path, ed: Edicao, destino: Path) -> None:
    tmp = MONTAGEM / "_pecas"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    lista = []
    pecas = ed.pecas()
    log(f"reeditando o video: {len(pecas)} trechos")
    for i, (a, b, extra) in enumerate(pecas):
        arq = tmp / f"p{i:03d}.mp4"
        vf = "fps=30,format=yuv420p"
        if extra > 0:
            vf += f",tpad=stop_mode=clone:stop_duration={extra:.3f}"
        rodar(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{a:.3f}",
                "-t",
                f"{b - a:.3f}",
                "-i",
                str(video),
                "-an",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-video_track_timescale",
                "15360",
                str(arq),
            ]
        )
        lista.append(f"file '{arq.name}'")
    (tmp / "lista.txt").write_text("\n".join(lista) + "\n")
    rodar(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(tmp / "lista.txt"),
            "-c",
            "copy",
            str(destino),
        ]
    )
    shutil.rmtree(tmp, ignore_errors=True)


def misturar(video: Path, entradas: list[tuple[Path, float]], saida: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    filtros = []
    for i, (arq, ini) in enumerate(entradas, start=1):
        cmd += ["-i", str(arq)]
        ms = int(ini * 1000)
        filtros.append(
            f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,adelay={ms}|{ms}[a{i}]"
        )
    mix = "".join(f"[a{i}]" for i in range(1, len(entradas) + 1))
    filtros.append(f"{mix}amix=inputs={len(entradas)}:normalize=0,loudnorm=I=-16:TP=-1.5,apad[voz]")
    cmd += [
        "-filter_complex",
        ";".join(filtros),
        "-map",
        "0:v",
        "-map",
        "[voz]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-shortest",
        str(saida),
    ]
    rodar(cmd)


def narrar_sincronizado(
    motor: Motor | None, video: Path, dublagem_pps: float | None = None
) -> None:
    linha = json.loads((MONTAGEM / "montagem.json").read_text(encoding="utf-8"))["linha_do_tempo"]
    sinc = json.loads((KIT / "sincronia.json").read_text(encoding="utf-8"))
    inicio = {x["id"]: x["inicio"] for x in linha}
    cortes = [
        (inicio[c] + a, inicio[c] + b)
        for c, lst in sinc.get("cortes", {}).items()
        if c in inicio
        for a, b in lst
    ]
    ed = Edicao(duracao(video), cortes)
    falas = carregar_falas(linha, sinc)

    if dublagem_pps:
        # Dublagem pelo autor: a duracao de cada fala e estimada pelo ritmo de leitura
        # (palavras por segundo) e o video ganha os mesmos cortes e quadros parados.
        log(f"dublagem: {len(falas)} falas, ritmo estimado de {dublagem_pps:.1f} palavras/s")
        for f in falas:
            f["dur"] = len(f["texto"].split()) / dublagem_pps + 0.6
    else:
        sintetizar_falas(motor, falas)
    extra = cortes_de_silencio(linha, falas)
    log(f"trechos sem fala cortados: {len(extra)} ({sum(b - a for a, b in extra):.0f}s)")
    parados = cortes_de_quadro_parado(video, linha, falas)
    log(f"quadros parados cortados: {len(parados)} ({sum(b - a for a, b in parados):.0f}s)")
    ed = Edicao(duracao(video), unir_cortes(cortes + extra + parados))
    planejar_e_montar(video, ed, falas, dublagem=bool(dublagem_pps))


def sintetizar_falas(motor: Motor, falas: list[dict]) -> None:
    # Audios de falas que nao existem mais (o cache por chave reaproveita os demais)
    validos = {f"f{i:03d}_{f['clipe']}" for i, f in enumerate(falas)}
    for velho in list(VOZ.glob("f*_*.wav")) + list(VOZ.glob("f*_*.chave")):
        if velho.stem not in validos:
            velho.unlink()
    log(f"sintetizando {len(falas)} falas")
    for i, f in enumerate(falas):
        f["arq"] = VOZ / f"f{i:03d}_{f['clipe']}.wav"
        motor.falar(f["texto"], f["arq"])
        f["dur"] = duracao(f["arq"])
        if motor.nome == "xtts" or (i + 1) % 10 == 0:
            log(f"  {i + 1}/{len(falas)} {f['clipe']} ({f['dur']:.1f}s)")


def planejar_e_montar(video: Path, ed: Edicao, falas: list[dict], dublagem: bool) -> None:
    cursor, ultimo_p = 0.0, 0.0
    for f in falas:
        ancora = ed.dentro_de_corte(f["ancora"])
        ini = ed.novo(ancora)
        if cursor + FOLGA > ini:
            d = cursor + FOLGA - ini
            # congela o quadro imediatamente anterior a cena da fala
            # (fala no inicio da cena: congela o ULTIMO quadro da cena anterior, que e a que a
            # fala anterior ainda esta descrevendo, e nao o primeiro quadro desta, que costuma
            # ser um terminal ou uma pagina ainda vazios)
            p = f["clipe_ini"] - 0.05 if ancora - f["clipe_ini"] < 1.0 else ancora - 0.3
            p = max(ultimo_p, ed.dentro_de_corte(p))
            ed.congelar(p, d)
            ultimo_p = p
            ini = ed.novo(ancora)
            f["congelou"] = d
        f["inicio"] = ini
        cursor = ini + f["dur"]
    fim_video = ed.novo(ed.total)
    if cursor + 1.0 > fim_video:
        ed.congelar(ed.total, cursor + 1.0 - fim_video)

    total_cong = sum(d for _p, d in ed.congelas)
    total_corte = sum(b - a for a, b in ed.cortes)
    for f in falas:
        extra = (
            f"  (+{f['congelou']:.1f}s de quadro parado antes)"
            if f.get("congelou", 0) > 0.05
            else ""
        )
        log(f"  {f['inicio']:7.1f}s  {f['dur']:5.1f}s  {f['clipe']}{extra}")
    log(f"cortes: -{total_corte:.0f}s; quadros congelados: +{total_cong:.0f}s")

    reeditado = MONTAGEM / "_video-reeditado.mp4"
    reeditar_video(video, ed, reeditado)
    if dublagem:
        preparar_dublagem(reeditado, falas)
        return
    saida = MONTAGEM / "video-final.mp4"
    misturar(reeditado, [(f["arq"], f["inicio"]) for f in falas], saida)
    reeditado.unlink(missing_ok=True)
    legenda_srt(falas, MONTAGEM / "video-final.srt")
    log(f"Video com narracao: {saida} ({duracao(saida):.0f}s)")
    log(f"Legenda: {MONTAGEM / 'video-final.srt'}")


# ---------------------------------------------------------------- dublagem pelo autor


def mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def preparar_dublagem(reeditado: Path, falas: list[dict]) -> None:
    """Video base da dublagem (sem voz), roteiro com tempos e video teleprompter."""
    base = MONTAGEM / "video-dublagem.mp4"
    reeditado.replace(base)
    total = duracao(base)
    for i, f in enumerate(falas):
        prox = falas[i + 1]["inicio"] if i + 1 < len(falas) else total
        f["janela"] = prox - f["inicio"]
    md = [
        "# Roteiro de dublagem",
        "",
        f"Video: `video-dublagem.mp4` ({mmss(total)}). Cada fala comeca no tempo indicado, junto",
        "com a cena; a janela e o tempo ate a proxima fala. O `video-teleprompter.mp4` mostra o",
        "texto na tela na hora certa, depois de uma contagem de 5 s.",
        "",
    ]
    for f in falas:
        md.append(f"- **{mmss(f['inicio'])}** (janela {f['janela']:.0f} s) - {f['texto']}")
    (MONTAGEM / "roteiro-dublagem.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    srt = MONTAGEM / "roteiro-dublagem.srt"
    linhas = []
    for i, f in enumerate(falas, 1):
        fim = f["inicio"] + max(f["janela"] - 0.15, 1.5)
        linhas += [str(i), f"{hms_srt(f['inicio'])} --> {hms_srt(fim)}", f["texto"], ""]
    srt.write_text("\n".join(linhas), encoding="utf-8")
    # Teleprompter: legenda grande em caixa escura + contagem regressiva de 5 s no inicio
    # Alignment no padrao SSA (o do filtro subtitles com .srt): 2 = rodape, centralizado
    estilo = (
        "FontName=DejaVu Sans,FontSize=10,PrimaryColour=&H00FFFFFF,BackColour=&H70000000,"
        "BorderStyle=3,Outline=3,Shadow=0,Alignment=2,MarginV=10,MarginL=60,MarginR=60"
    )
    filtro = (
        f"subtitles={srt}:force_style='{estilo}',"
        "tpad=start_duration=5:start_mode=add:color=black,"
        "drawtext=text='%{eif\\:5-t\\:d}':fontsize=160:fontcolor=white:"
        "x=(w-text_w)/2:y=(h-text_h)/2:enable='lt(t,5)',"
        "drawtext=text='Comece a gravar o audio; fale quando o texto aparecer':fontsize=34:"
        "fontcolor=white:x=(w-text_w)/2:y=h*0.72:enable='lt(t,5)'"
    )
    tele = MONTAGEM / "video-teleprompter.mp4"
    rodar(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(base),
            "-vf",
            filtro,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-an",
            str(tele),
        ]
    )
    # Versao final legendada (sem voz): mesma legenda, sem a contagem; trilha de audio muda
    # para os players que exigem audio. O .srt ao lado serve de legenda no YouTube.
    final = MONTAGEM / "video-final-legendado.mp4"
    rodar(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(base),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            f"subtitles={srt}:force_style='{estilo}'",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )
    shutil.copyfile(srt, final.with_suffix(".srt"))
    log(f"Video final legendado (sem voz): {final} ({mmss(duracao(final))})")
    log(f"Video para dublar (sem voz): {base} ({mmss(total)})")
    log(f"Teleprompter (5 s de contagem + texto na tela): {tele}")
    log(f"Roteiro: {MONTAGEM / 'roteiro-dublagem.md'}")


def hms_srt(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def primeira_fala(audio: Path) -> float:
    """Instante em que a voz comeca na gravacao (primeiro fim de silencio)."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            str(audio),
            "-af",
            "silencedetect=noise=-35dB:d=0.4",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    import re as _re

    m = _re.search(r"silence_end: ([0-9.]+)", out)
    inicio_silencio = _re.search(r"silence_start: ([0-9.]+)", out)
    if inicio_silencio and float(inicio_silencio.group(1)) > 0.05:
        return 0.0  # a gravacao ja comeca com voz
    return float(m.group(1)) if m else 0.0


def juntar_gravacao(gravacao: Path, inicio_manual: float | None) -> None:
    """Junta a gravacao do autor ao video-dublagem.mp4, alinhando a primeira fala."""
    base = MONTAGEM / "video-dublagem.mp4"
    srt = MONTAGEM / "roteiro-dublagem.srt"
    if not base.exists() or not srt.exists():
        raise SystemExit("rode antes o narrar.ps1 -Dublagem (gera o video e o roteiro)")
    primeira = srt.read_text(encoding="utf-8").split("\n")[1].split(" --> ")[0]
    h, m_, rest = primeira.split(":")
    alvo = int(h) * 3600 + int(m_) * 60 + float(rest.replace(",", "."))
    voz = inicio_manual if inicio_manual is not None else primeira_fala(gravacao)
    desloc = alvo - voz
    log(
        f"voz comeca em {voz:.2f}s na gravacao; primeira fala do video em {alvo:.2f}s "
        f"(deslocamento {desloc:+.2f}s)"
    )
    if desloc >= 0:
        ms = int(desloc * 1000)
        filtro = f"[1:a]aresample=48000,adelay={ms}|{ms},"
    else:
        filtro = f"[1:a]aresample=48000,atrim=start={-desloc:.3f},asetpts=PTS-STARTPTS,"
    filtro += "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,apad[voz]"
    saida = MONTAGEM / "video-final.mp4"
    rodar(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(base),
            "-i",
            str(gravacao),
            "-filter_complex",
            filtro,
            "-map",
            "0:v",
            "-map",
            "[voz]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(saida),
        ]
    )
    log(f"Video final com a sua voz: {saida} ({mmss(duracao(saida))})")


# ---------------------------------------------------------------- gravacao com varias tomadas


def _normalizar(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def ler_srt(arquivo: Path) -> list[dict]:
    falas = []
    for bloco in re.split(r"\n\s*\n", arquivo.read_text(encoding="utf-8").strip()):
        linhas = bloco.strip().splitlines()
        if len(linhas) < 3 or "-->" not in linhas[1]:
            continue

        def seg(x: str) -> float:
            h, m_, r = x.strip().split(":")
            return int(h) * 3600 + int(m_) * 60 + float(r.replace(",", "."))

        ini, fim = linhas[1].split("-->")
        falas.append({"inicio": seg(ini), "fim": seg(fim), "texto": " ".join(linhas[2:])})
    return falas


def transcrever_palavras(audio: Path, modelo: str = "small") -> list[dict]:
    import numpy as np
    from faster_whisper import WhisperModel

    bruto = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(audio),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    sinal = np.frombuffer(bruto, np.int16).astype(np.float32) / 32768.0
    log(f"transcrevendo a gravacao (modelo {modelo}; primeira vez baixa o modelo)")
    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(
        sinal,
        language="pt",
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    palavras = []
    for sgm in segs:
        for w in sgm.words or []:
            n = _normalizar(w.word).strip()
            if n:
                palavras.append({"p": n, "ini": float(w.start), "fim": float(w.end)})
    return palavras


def juntar_tomadas(gravacao: Path, modelo: str = "small") -> None:
    """Gravacao livre, com repeticoes: para cada fala do roteiro acha na transcricao as
    tomadas parecidas, usa a ULTIMA boa (quem erra repete) e a encaixa no tempo da fala."""
    from difflib import SequenceMatcher

    base = MONTAGEM / "video-dublagem.mp4"
    srt = MONTAGEM / "roteiro-dublagem.srt"
    if not base.exists() or not srt.exists():
        raise SystemExit("rode antes o narrar.ps1 -Dublagem (gera o video e o roteiro)")
    falas = ler_srt(srt)
    palavras = transcrever_palavras(gravacao, modelo)
    if not palavras:
        raise SystemExit("nenhuma fala reconhecida na gravacao")
    texto_pal = [w["p"] for w in palavras]
    escolhidas = []
    cursor = 0  # as tomadas de uma fala vem depois da fala anterior escolhida
    relatorio = []
    for i, f in enumerate(falas, 1):
        alvo = _normalizar(f["texto"]).split()
        n = len(alvo)
        alvo_txt = " ".join(alvo)
        candidatos = []
        for tam in sorted({max(1, int(n * k)) for k in (0.8, 0.9, 1.0, 1.1, 1.2)}):
            for j in range(cursor, max(cursor, len(texto_pal) - tam + 1)):
                # filtro barato: alguma das 3 primeiras palavras precisa bater
                if not set(texto_pal[j : j + 3]) & set(alvo[:3]):
                    continue
                sc = SequenceMatcher(None, alvo_txt, " ".join(texto_pal[j : j + tam])).ratio()
                if sc >= 0.6:
                    candidatos.append((sc, j, j + tam - 1))
        if not candidatos:
            relatorio.append(f"{i:3d} NAO ENCONTRADA  | {f['texto'][:70]}")
            continue
        melhor = max(c[0] for c in candidatos)
        bons = [c for c in candidatos if c[0] >= melhor - 0.08]
        # tomadas distintas: agrupa candidatos que se sobrepoem; escolhe a ultima tomada
        bons.sort(key=lambda c: c[1])
        tomadas = []
        for c in bons:
            if tomadas and c[1] <= tomadas[-1][-1][2]:
                tomadas[-1].append(c)
            else:
                tomadas.append([c])
        ultima = max(tomadas[-1], key=lambda c: c[0])
        sc, a, b = ultima
        ini = max(0.0, palavras[a]["ini"] - 0.12)
        fim = palavras[b]["fim"] + 0.25
        if b + 1 < len(palavras):
            fim = min(fim, palavras[b + 1]["ini"] - 0.02)
        escolhidas.append({"fala": i, "de": ini, "ate": fim, "alvo": f["inicio"]})
        cursor = b + 1
        relatorio.append(
            f"{i:3d} tomadas={len(tomadas)} usada={mmss(ini)} nota={sc:.2f} | {f['texto'][:60]}"
        )
    # posiciona em sequencia: cada fala no seu tempo, sem sobrepor a anterior
    pos_fim = 0.0
    filtros, rotulos = [], []
    for k, e in enumerate(escolhidas):
        pos = max(e["alvo"], pos_fim + 0.15)
        atraso = pos - e["alvo"]
        if atraso > 1.0:
            relatorio.append(f"    aviso: fala {e['fala']} entrou {atraso:.1f}s depois do previsto")
        pos_fim = pos + (e["ate"] - e["de"])
        ms = int(pos * 1000)
        filtros.append(
            f"[1:a]atrim=start={e['de']:.3f}:end={e['ate']:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.04,aresample=48000,adelay={ms}|{ms}[t{k}]"
        )
        rotulos.append(f"[t{k}]")
    # a entrada de audio e uma so: divide em N copias, uma por trecho
    divisao = f"[1:a]asplit={len(filtros)}" + "".join(f"[s{k}]" for k in range(len(filtros)))
    filtros = [f.replace("[1:a]", f"[s{k}]", 1) for k, f in enumerate(filtros)]
    filtro = (
        divisao
        + ";"
        + ";".join(filtros)
        + ";"
        + "".join(rotulos)
        + (
            f"amix=inputs={len(rotulos)}:normalize=0:dropout_transition=0,"
            "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,apad[voz]"
        )
    )
    roteiro_filtro = VOZ / "tomadas-filtro.txt"
    roteiro_filtro.write_text(filtro, encoding="utf-8")
    saida = MONTAGEM / "video-final.mp4"
    rodar(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(base),
            "-i",
            str(gravacao),
            "-filter_complex_script",
            str(roteiro_filtro),
            "-map",
            "0:v",
            "-map",
            "[voz]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(saida),
        ]
    )
    rel = MONTAGEM / "tomadas.txt"
    rel.write_text("\n".join(relatorio) + "\n", encoding="utf-8")
    faltam = sum(1 for r in relatorio if "NAO ENCONTRADA" in r)
    log(
        f"{len(escolhidas)} de {len(falas)} falas encaixadas; {faltam} nao encontradas "
        f"(detalhes em {rel})"
    )
    log(f"Video final com a sua voz: {saida} ({mmss(duracao(saida))})")


# ---------------------------------------------------------------- audio proprio por bloco


def ler_blocos(arquivo: Path) -> list[dict]:
    blocos: list[dict] = []
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        m = CABECALHO.match(linha.strip())
        if m:
            blocos.append(
                {
                    "n": int(m.group(1)),
                    "inicio": int(m.group(2)) * 60 + int(m.group(3)),
                    "fim": int(m.group(4)) * 60 + int(m.group(5)),
                }
            )
    if not blocos:
        raise SystemExit(f"nenhum bloco encontrado em {arquivo}")
    return blocos


def juntar_blocos(video: Path) -> None:
    entradas = []
    for b in ler_blocos(MONTAGEM / "narracao.txt"):
        pronto = next(
            (p for p in (VOZ / f"bloco{b['n']}.wav", VOZ / f"bloco{b['n']}.mp3") if p.exists()),
            None,
        )
        if pronto is None:
            log(f"bloco {b['n']}: sem audio em {VOZ}; ficara mudo")
            continue
        final = VOZ / f"_ajustado{b['n']}.wav"
        d = duracao(pronto)
        janela = b["fim"] - b["inicio"]
        fator = min(max(1.0, d / max(1.0, janela - 0.6)), 1.3)
        rodar(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(pronto),
                "-filter:a",
                f"atempo={fator:.4f}",
                str(final),
            ]
        )
        log(
            f"bloco {b['n']}: usando {pronto.name}"
            + (f" (acelerado {fator:.2f}x)" if fator > 1 else "")
        )
        entradas.append((final, float(b["inicio"]) + 0.4))
    if not entradas:
        raise SystemExit("nenhum audio para juntar")
    saida = MONTAGEM / "video-final.mp4"
    misturar(video, entradas, saida)
    log(f"Video com narracao: {saida} ({duracao(saida):.0f}s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--motor",
        choices=["edge", "piper", "xtts"],
        default="edge",
        help="edge = vozes neurais Microsoft (padrao); piper = local; "
        "xtts = clone da voz a partir de /saida/voz-referencia/*.wav",
    )
    ap.add_argument(
        "--voz",
        default=None,
        help="edge: pt-BR-AntonioNeural (padrao), pt-BR-FranciscaNeural, "
        "pt-BR-ThalitaMultilingualNeural; piper: pt_BR-faber-medium",
    )
    ap.add_argument("--taxa", default="+0%", help="edge: velocidade, ex. +10%% ou -5%%")
    ap.add_argument(
        "--velocidade",
        type=float,
        default=None,
        help="piper: length-scale (padrao 1.1; <1 mais rapido); "
        "xtts: fator de velocidade (padrao 1.0; >1 mais rapido)",
    )
    ap.add_argument(
        "--amostra",
        default=None,
        help="arquivo UTF-8 com um texto: so sintetiza /saida/voz/amostra.wav e sai",
    )
    ap.add_argument(
        "--dublagem",
        type=float,
        nargs="?",
        const=2.3,
        default=None,
        help="prepara a dublagem pelo autor: video reeditado, roteiro e teleprompter "
        "(valor = ritmo de leitura em palavras/s, padrao 2.3)",
    )
    ap.add_argument(
        "--gravacao", default=None, help="junta esta gravacao (wav/mp3/m4a) ao video-dublagem.mp4"
    )
    ap.add_argument(
        "--inicio-gravacao",
        type=float,
        default=None,
        help="segundo em que a voz comeca na gravacao (padrao: detecta sozinho)",
    )
    ap.add_argument(
        "--tomadas",
        default=None,
        help="gravacao livre com repeticoes: escolhe a ultima tomada de cada fala",
    )
    ap.add_argument(
        "--somente-juntar",
        action="store_true",
        help="nao sintetiza: usa /saida/voz/blocoN.(wav|mp3) feitos fora daqui",
    )
    a = ap.parse_args()

    video = MONTAGEM / "video-final-sem-voz.mp4"
    VOZ.mkdir(parents=True, exist_ok=True)
    if a.somente_juntar:
        juntar_blocos(video)
        return 0
    if a.tomadas:
        juntar_tomadas(Path(a.tomadas))
        return 0
    if a.gravacao:
        juntar_gravacao(Path(a.gravacao), a.inicio_gravacao)
        return 0
    if a.dublagem:
        narrar_sincronizado(None, video, dublagem_pps=a.dublagem)
        return 0
    voz = a.voz or VOZ_PADRAO[a.motor]
    if a.velocidade is None:
        a.velocidade = 1.0 if a.motor == "xtts" else 1.1
    log(f"motor {a.motor}, voz {voz}")
    if a.amostra:
        motor = Motor(a.motor, voz, a.velocidade, a.taxa)
        destino = VOZ / "amostra.wav"
        destino.with_suffix(".chave").unlink(missing_ok=True)
        motor.falar(Path(a.amostra).read_text(encoding="utf-8").strip(), destino)
        log(f"Amostra: {destino} ({duracao(destino):.1f}s)")
        return 0
    narrar_sincronizado(Motor(a.motor, voz, a.velocidade, a.taxa), video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
