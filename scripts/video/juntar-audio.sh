#!/usr/bin/env bash
# Junta a narracao (gerada por uma ferramenta externa de TTS a partir de narracao.txt) ao
# video sem voz. O video e copiado sem recodificar; o audio vira AAC; a saida termina no
# fim do mais curto dos dois (-shortest).
#
# Uso: scripts/video/juntar-audio.sh VIDEO.mp4 VOZ.(mp3|wav|m4a) SAIDA.mp4 [VOLUME] [ATRASO_S]
#   VOLUME:   multiplicador da voz (padrao 1.0; ex.: 1.5 aumenta, 0.8 reduz)
#   ATRASO_S: segundos de silencio antes da voz (padrao 0), para alinhar o inicio
# Variavel NORMALIZAR=1 aplica loudnorm (-16 LUFS, padrao para video na web).
set -euo pipefail

if [ $# -lt 3 ]; then
  sed -n '2,10p' "$0"
  exit 2
fi
VIDEO="$1"
VOZ="$2"
SAIDA="$3"
VOLUME="${4:-1.0}"
ATRASO="${5:-0}"
command -v ffmpeg >/dev/null 2>&1 || { echo "ERRO: ffmpeg nao encontrado" >&2; exit 1; }
[ -f "$VIDEO" ] || { echo "ERRO: video $VIDEO nao existe" >&2; exit 1; }
[ -f "$VOZ" ] || { echo "ERRO: audio $VOZ nao existe" >&2; exit 1; }

atraso_ms="$(awk -v s="$ATRASO" 'BEGIN { printf "%d", s * 1000 }')"
filtro="[1:a]aresample=48000,adelay=${atraso_ms}:all=1,volume=${VOLUME}"
if [ "${NORMALIZAR:-0}" = "1" ]; then
  filtro="${filtro},loudnorm=I=-16:TP=-1.5:LRA=11"
fi
filtro="${filtro}[voz]"

ffmpeg -y -v error -i "$VIDEO" -i "$VOZ" \
  -filter_complex "$filtro" \
  -map 0:v:0 -map "[voz]" \
  -c:v copy -c:a aac -b:a 192k -ar 48000 -ac 2 \
  -shortest -movflags +faststart "$SAIDA"
echo "OK: $SAIDA"
