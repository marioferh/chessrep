#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
command -v podman >/dev/null 2>&1 || { echo 'Falta Podman.'; exit 1; }
mkdir -p scid-import data/pgn repertoire training
podman build -t chesslab:latest -f Containerfile .
podman rm -f chesslab >/dev/null 2>&1 || true
podman run -d --name chesslab --replace \
  -p 127.0.0.1:8765:8765 \
  -e CHESS_SCID_DIR=/imports/scid \
  -e CHESS_STOCKFISH_PATH=/usr/local/bin/stockfish \
  -v "$PWD/scid-import:/imports/scid:ro,Z" \
  -v "$PWD/data:/app/data:Z,rw" \
  -v "$PWD/repertoire:/app/repertoire:Z,rw" \
  -v "$PWD/training:/app/training:Z,rw" \
  chesslab:latest
echo 'ChessLab: http://127.0.0.1:8765'
echo 'Los archivos SCID están montados en solo lectura; sus datos originales permanecen intactos.'
