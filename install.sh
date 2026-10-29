#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Este instalador está pensado para Kali Linux." >&2
  exit 1
fi

if ! command -v pipx >/dev/null 2>&1; then
  echo "Falta pipx. En Kali, instálalo con: sudo apt install pipx" >&2
  exit 1
fi

pipx install .
echo "Instalado. Inicia la aplicación con: kaliscan-studio"
