#!/usr/bin/env bash
# Genera un ejecutable de escritorio con flet pack (PyInstaller).
# Usa siempre el entorno virtual del proyecto (.venv), no el flet de ~/.local/bin.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creando entorno virtual en $VENV…"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

PYTHON_BIN="$VENV/bin/python"
PIP="$VENV/bin/pip"
FLET_BIN="$VENV/bin/flet"

echo "Instalando dependencias en $VENV…"
"$PIP" install -q -r requirements.txt pyinstaller

if [[ ! -x "$FLET_BIN" ]]; then
  echo "No está el comando flet en $VENV/bin."
  echo "Prueba: $PIP install 'flet>=1.0,<2'"
  exit 1
fi

echo "Python: $PYTHON_BIN"
echo "Flet:   $FLET_BIN"
# main.py va primero: --hidden-import acepta varios valores y si va al final
# se come el script como si fuera otro módulo.
"$FLET_BIN" pack \
  main.py \
  -n Gifteando \
  -i gifteando/img/logo.png \
  --product-name Gifteando \
  --file-description "Buscador de GIFs animados" \
  --add-data "gifteando/img:gifteando/img" \
  --hidden-import pystray \
  --hidden-import PIL
