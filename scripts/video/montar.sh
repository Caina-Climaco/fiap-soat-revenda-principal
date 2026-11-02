#!/usr/bin/env bash
# Monta o video final a partir dos clipes gravados pelo gravar.ps1 (roda no Linux, ou no
# Git Bash/WSL com ffmpeg, ffprobe, python3 e Pillow).
#
# Uso:
#   scripts/video/montar.sh [ENTRADA] [opcoes do montar.py]
#     ENTRADA: diretorio com manifesto.json e clipes/ (padrao: .setup/video na raiz do repo)
#   Opcoes: --sem-opcionais | --com-opcionais, --incluir-falhas, --cartela 3, --blocos 6,7
#
# Saidas em ENTRADA/montagem/: video-final-sem-voz.mp4, video-final-sem-voz.srt,
# narracao.md, narracao.txt e montagem.json. Para juntar a voz depois:
#   scripts/video/juntar-audio.sh ENTRADA/montagem/video-final-sem-voz.mp4 voz.mp3 video-final.mp4
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$DIR/../.." && pwd)"
ENTRADA="$RAIZ/.setup/video"
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  ENTRADA="$1"
  shift
fi

for ferramenta in ffmpeg ffprobe python3; do
  command -v "$ferramenta" >/dev/null 2>&1 || { echo "ERRO: $ferramenta nao encontrado" >&2; exit 1; }
done
python3 -c 'import PIL' 2>/dev/null || { echo "ERRO: Pillow ausente (pip install pillow)" >&2; exit 1; }
[ -f "$ENTRADA/manifesto.json" ] || { echo "ERRO: $ENTRADA/manifesto.json nao existe (rode o gravar.ps1 antes)" >&2; exit 1; }

echo "ffmpeg: $(ffmpeg -version | head -n 1)"
exec python3 "$DIR/montar.py" --entrada "$ENTRADA" "$@"
