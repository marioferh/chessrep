#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
command -v podman >/dev/null 2>&1 || { echo 'Podman no está instalado.'; exit 1; }
if ! podman container exists chesslab; then
  echo 'No existe el contenedor chesslab. Ejecuta primero ./podman-run.sh.'
  exit 1
fi
podman exec -e CHESSLAB_REQUIRE_STOCKFISH=1 chesslab python3 -m unittest discover -s tests -v
printf '\nComprobación HTTP del servicio...\n'
curl --fail --silent http://127.0.0.1:8765/api/status >/dev/null
curl --fail --silent http://127.0.0.1:8765/api/engine/status
printf '\nPruebas completadas.\n'
