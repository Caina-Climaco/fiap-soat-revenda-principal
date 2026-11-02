#!/usr/bin/env python3
"""Monta o video final a partir de .setup/video/manifesto.json (chamado por montar.sh).

Gera em <entrada>/montagem/:
  video-final-sem-voz.mp4   cartelas de titulo (3 s por bloco) + clipes, H.264 1600x900 30 fps,
                            com faixa de audio silenciosa (padroniza a concatenacao)
  video-final-sem-voz.srt   legendas a partir dos titulos e das marcas de legenda dos clipes
  narracao.md               por bloco e por clipe: inicio e fim no video final e o texto da
                            narracao, ja cortado para caber no tempo real (~150 palavras/min)
  narracao.txt              so o texto corrido por bloco, para colar numa ferramenta de TTS
  montagem.json             a linha do tempo usada (para conferencia)

Requer ffmpeg/ffprobe (6.x) e Pillow. Os .webm e o manifesto vem do gravar.ps1.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

DIR = Path(__file__).resolve().parent
LARGURA, ALTURA, FPS = 1600, 900, 30
LIMITE_OPCIONAIS_S = 20 * 60
SEM_FALA = "_(sem fala; deixe a imagem respirar)_"
FONTES = {
    "negrito": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "normal": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
}


def fonte(tipo: str, tamanho: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for caminho in FONTES[tipo]:
        if Path(caminho).is_file():
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default(size=tamanho)


def rodar(cmd: list[str]) -> None:
    # cmd: ffmpeg/ffprobe com argumentos montados aqui (nada vem de entrada externa)
    resultado = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if resultado.returncode != 0:
        sys.stderr.write(resultado.stderr[-3000:])
        raise SystemExit(f"falhou: {' '.join(cmd[:6])} ...")


def duracao(arquivo: Path) -> float:
    saida = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(arquivo),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(saida)


def quebrar(desenho: ImageDraw.ImageDraw, texto: str, f: Any, largura: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = f"{atual} {palavra}".strip()
        if desenho.textlength(teste, font=f) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def cartela(destino: Path, bloco: str, titulo: str, subtitulo: str) -> None:
    img = Image.new("RGB", (LARGURA, ALTURA), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, ALTURA], fill=(56, 189, 248))
    f_bloco, f_titulo, f_sub, f_rodape = (
        fonte("negrito", 40),
        fonte("negrito", 82),
        fonte("normal", 38),
        fonte("normal", 26),
    )
    y = 300
    d.text((130, y), f"Bloco {bloco}", font=f_bloco, fill=(56, 189, 248))
    y += 70
    for linha in quebrar(d, titulo, f_titulo, LARGURA - 260):
        d.text((130, y), linha, font=f_titulo, fill=(255, 255, 255))
        y += 100
    y += 20
    for linha in quebrar(d, subtitulo, f_sub, LARGURA - 260):
        d.text((130, y), linha, font=f_sub, fill=(203, 213, 225))
        y += 52
    d.text(
        (130, ALTURA - 90),
        "Revenda de Veículos - FIAP PósTech SOAT, Fase 4 - Cainã Clímaco",
        font=f_rodape,
        fill=(148, 163, 184),
    )
    img.save(destino)


VIDEO_ARGS = [
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "20",
    "-pix_fmt",
    "yuv420p",
    "-r",
    str(FPS),
    "-c:a",
    "aac",
    "-b:a",
    "128k",
    "-ar",
    "48000",
    "-ac",
    "2",
    "-movflags",
    "+faststart",
]
FILTRO = (
    f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=decrease,"
    f"pad={LARGURA}:{ALTURA}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS},format=yuv420p,setsar=1"
)


def converter_clipe(origem: Path, destino: Path, corte: float) -> None:
    rodar(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{max(corte, 0):.2f}",
            "-i",
            str(origem),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            FILTRO,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            *VIDEO_ARGS,
            str(destino),
        ]
    )


def converter_cartela(png: Path, destino: Path, segundos: float) -> None:
    rodar(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{segundos:.2f}",
            "-i",
            str(png),
            "-f",
            "lavfi",
            "-t",
            f"{segundos:.2f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            FILTRO,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            *VIDEO_ARGS,
            str(destino),
        ]
    )


def tempo_srt(s: float) -> str:
    ms = round(s * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    seg, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{seg:02d},{ms:03d}"


def tempo_curto(s: float) -> str:
    m, seg = divmod(round(s), 60)
    return f"{m}:{seg:02d}"


def palavras(texto: str) -> int:
    return len(texto.split())


def ajustar_narracao(frases: list[dict[str, Any]], limite: int) -> list[dict[str, Any]]:
    """Remove frases ate o total caber em `limite` palavras, mantendo a ordem original.

    Corta primeiro a prioridade 3, depois a 2 e, por ultimo, a 1. Dentro da mesma prioridade,
    se uma unica remocao ja resolve, remove a menor frase que resolve; senao, remove a ultima.
    Nunca remove a ultima frase restante.
    """
    mantidas = list(frases)

    def total() -> int:
        return sum(palavras(f["texto"]) for f in mantidas)

    for prioridade in (3, 2, 1):
        while total() > limite and len(mantidas) > 1:
            candidatas = [i for i, f in enumerate(mantidas) if f["prioridade"] == prioridade]
            if not candidatas:
                break
            excesso = total() - limite
            resolvem = [i for i in candidatas if palavras(mantidas[i]["texto"]) >= excesso]
            if resolvem:
                alvo = min(resolvem, key=lambda i: palavras(mantidas[i]["texto"]))
            else:
                alvo = candidatas[-1]
            mantidas.pop(alvo)
    return mantidas


def main() -> int:
    raiz_repo = DIR.parent.parent
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--entrada", default=str(raiz_repo / ".setup" / "video"))
    ap.add_argument("--saida", default=None, help="padrao: <entrada>/montagem")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--com-opcionais", action="store_true")
    grupo.add_argument("--sem-opcionais", action="store_true")
    ap.add_argument("--incluir-falhas", action="store_true", help="inclui clipes com status FALHOU")
    ap.add_argument("--cartela", type=float, default=3.0, help="duracao das cartelas (s)")
    ap.add_argument("--blocos", default="", help="monta so estes blocos (ex.: 6,7)")
    args = ap.parse_args()

    for ferramenta in ("ffmpeg", "ffprobe"):
        if not shutil.which(ferramenta):
            raise SystemExit(f"{ferramenta} nao encontrado")
    entrada = Path(args.entrada)
    saida = Path(args.saida) if args.saida else entrada / "montagem"
    trabalho = saida / "trabalho"
    trabalho.mkdir(parents=True, exist_ok=True)
    manifesto = json.loads((entrada / "manifesto.json").read_text(encoding="utf-8"))
    cfg = json.loads((DIR / "clipes.json").read_text(encoding="utf-8"))
    narracao = json.loads((DIR / "narracao.json").read_text(encoding="utf-8"))
    ppm = float(narracao.get("palavras_por_minuto", 150))
    blocos_meta = {**cfg["blocos"], **manifesto.get("blocos", {})}
    filtro_blocos = {int(b) for b in args.blocos.replace(";", ",").split(",") if b.strip()}

    aceitos = {"OK", "FALHOU"} if args.incluir_falhas else {"OK"}
    clipes, ignorados = [], []
    for c in sorted(manifesto["clipes"], key=lambda c: (c["ordem"], c["id"])):
        arquivo = entrada / (c.get("arquivo") or "")
        if filtro_blocos and c["bloco"] not in filtro_blocos:
            continue
        if c["id"] in set(cfg.get("fora_do_video", [])):
            ignorados.append((c["id"], "FORA", "fora do video (clipes.json: fora_do_video)"))
            continue
        if c["status"] not in aceitos or not c.get("arquivo") or not arquivo.is_file():
            ignorados.append((c["id"], c["status"], c.get("erro") or "sem arquivo"))
            continue
        clipes.append(c)
    estimado = sum(
        max(0.0, c["duracao_s"] - c["inicio_corte_s"]) for c in clipes
    ) + args.cartela * len({c["bloco"] for c in clipes})
    incluir_opcionais = args.com_opcionais or (
        not args.sem_opcionais and estimado <= LIMITE_OPCIONAIS_S
    )
    if not incluir_opcionais:
        for c in [c for c in clipes if c.get("opcional")]:
            ignorados.append((c["id"], "OPCIONAL", "fora (use --com-opcionais)"))
        clipes = [c for c in clipes if not c.get("opcional")]
    if not clipes:
        raise SystemExit("nenhum clipe utilizavel no manifesto")

    print(
        f"{len(clipes)} clipe(s); opcionais "
        f"{'incluidos' if incluir_opcionais else 'fora'} (estimativa {tempo_curto(estimado)})"
    )
    linha_tempo: list[dict[str, Any]] = []
    partes: list[Path] = []
    t = 0.0
    bloco_atual = None
    # numeracao exibida nas cartelas: sequencial entre os blocos que entraram no video
    exibido = {b: i + 1 for i, b in enumerate(dict.fromkeys(str(c["bloco"]) for c in clipes))}
    for c in clipes:
        bloco = str(c["bloco"])
        if bloco != bloco_atual:
            bloco_atual = bloco
            meta = blocos_meta.get(bloco, {"titulo": f"Bloco {bloco}", "subtitulo": ""})
            png = trabalho / f"cartela_{bloco}.png"
            mp4 = trabalho / f"cartela_{bloco}.mp4"
            cartela(png, str(exibido[bloco]), meta["titulo"], meta.get("subtitulo", ""))
            converter_cartela(png, mp4, args.cartela)
            d = duracao(mp4)
            linha_tempo.append(
                {
                    "tipo": "cartela",
                    "bloco": bloco,
                    "id": f"cartela_{bloco}",
                    "inicio": t,
                    "fim": t + d,
                    "titulo": f"Bloco {bloco} - {meta['titulo']}",
                    "marcas": [],
                }
            )
            partes.append(mp4)
            t += d
        mp4 = trabalho / f"{c['id']}.mp4"
        print(f"  convertendo {c['id']}")
        converter_clipe(entrada / c["arquivo"], mp4, float(c.get("inicio_corte_s") or 0))
        d = duracao(mp4)
        corte = float(c.get("inicio_corte_s") or 0)
        marcas = [
            {"t": max(0.0, m["t"] - corte), "texto": m["texto"]} for m in c.get("marcas", [])
        ] or [{"t": 0.0, "texto": c["titulo"]}]
        linha_tempo.append(
            {
                "tipo": "clipe",
                "bloco": bloco,
                "id": c["id"],
                "inicio": t,
                "fim": t + d,
                "titulo": c["titulo"],
                "marcas": marcas,
            }
        )
        partes.append(mp4)
        t += d

    lista = trabalho / "concat.txt"
    lista.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in partes), encoding="utf-8"
    )
    final = saida / "video-final-sem-voz.mp4"
    rodar(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lista),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )
    total = duracao(final)

    # ------------------------------------------------------------------ legendas
    entradas = []
    for item in linha_tempo:
        marcas = sorted(item["marcas"], key=lambda m: m["t"]) or [
            {"t": 0.0, "texto": item["titulo"]}
        ]
        if item["tipo"] == "cartela":
            marcas = [{"t": 0.0, "texto": item["titulo"]}]
        for i, m in enumerate(marcas):
            ini = item["inicio"] + m["t"]
            fim = item["inicio"] + marcas[i + 1]["t"] if i + 1 < len(marcas) else item["fim"]
            if fim - ini >= 0.3:
                entradas.append((ini, fim, m["texto"]))
    srt = saida / "video-final-sem-voz.srt"
    srt.write_text(
        "".join(
            f"{n}\n{tempo_srt(a)} --> {tempo_srt(b)}\n{txt}\n\n"
            for n, (a, b, txt) in enumerate(entradas, 1)
        ),
        encoding="utf-8",
    )

    # ------------------------------------------------------------------ narracao
    md = [
        "# Narração do vídeo de entrega",
        "",
        f"Vídeo: `video-final-sem-voz.mp4` ({tempo_curto(total)}). "
        f"Ritmo de referência: {ppm:.0f} palavras por minuto.",
        "Os tempos abaixo são do vídeo final. O texto de cada bloco já foi encurtado "
        "para caber no tempo real;",
        "gere a voz bloco a bloco (ou por clipe) e alinhe pelo início indicado. "
        "Fonte: `scripts/video/narracao.json`,",
        "adaptado das falas de `docs/11-roteiro-video.md`.",
        "",
    ]
    txt = []
    resumo_blocos = []
    for bloco in sorted({i["bloco"] for i in linha_tempo}, key=int):
        itens = [i for i in linha_tempo if i["bloco"] == bloco]
        ini, fim = itens[0]["inicio"], itens[-1]["fim"]
        dados = narracao["blocos"].get(bloco, {"abertura": [], "clipes": {}})
        frases = [
            {"prioridade": p, "texto": x, "id": itens[0]["id"]}
            for p, x in dados.get("abertura", [])
        ]
        for i in itens:
            if i["tipo"] == "clipe":
                frases += [
                    {"prioridade": p, "texto": x, "id": i["id"]}
                    for p, x in dados.get("clipes", {}).get(i["id"], [])
                ]
        limite = int((fim - ini) * ppm / 60)
        mantidas = ajustar_narracao(frases, limite)
        usadas = sum(palavras(f["texto"]) for f in mantidas)
        meta = blocos_meta.get(bloco, {"titulo": f"Bloco {bloco}"})
        resumo_blocos.append(
            {
                "bloco": bloco,
                "inicio": ini,
                "fim": fim,
                "palavras": usadas,
                "limite": limite,
                "frases_removidas": len(frases) - len(mantidas),
            }
        )
        md += [
            f"## Bloco {bloco} - {meta['titulo']} "
            f"({tempo_curto(ini)} a {tempo_curto(fim)}, {fim - ini:.0f} s)",
            "",
            f"{usadas} palavras de no máximo {limite}"
            + (
                f"; {len(frases) - len(mantidas)} frase(s) cortada(s) para caber."
                if len(frases) > len(mantidas)
                else "."
            ),
            "",
        ]
        for i in itens:
            texto = " ".join(f["texto"] for f in mantidas if f["id"] == i["id"])
            rotulo = "cartela" if i["tipo"] == "cartela" else i["id"]
            md.append(
                f"- **{tempo_curto(i['inicio'])} a {tempo_curto(i['fim'])}** `{rotulo}` "
                f"({i['titulo']}): {texto or SEM_FALA}"
            )
        md.append("")
        txt += [
            f"Bloco {bloco} - {meta['titulo']} ({tempo_curto(ini)} a {tempo_curto(fim)})",
            " ".join(f["texto"] for f in mantidas),
            "",
        ]
    if ignorados:
        md += (
            ["## Clipes fora do vídeo", ""]
            + [f"- `{i}`: {s} ({e})" for i, s, e in ignorados]
            + [""]
        )
    (saida / "narracao.md").write_text("\n".join(md), encoding="utf-8")
    (saida / "narracao.txt").write_text("\n".join(txt), encoding="utf-8")
    (saida / "montagem.json").write_text(
        json.dumps(
            {
                "duracao_total_s": round(total, 2),
                "linha_do_tempo": linha_tempo,
                "blocos": resumo_blocos,
                "ignorados": ignorados,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    shutil.rmtree(trabalho, ignore_errors=True)
    print(f"Vídeo: {final} ({tempo_curto(total)})")
    print(f"Legendas: {srt}")
    print(f"Narração: {saida / 'narracao.md'} e {saida / 'narracao.txt'}")
    for i, s, e in ignorados:
        print(f"  fora do vídeo: {i} [{s}] {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
