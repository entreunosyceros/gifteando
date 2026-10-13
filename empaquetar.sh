#!/usr/bin/env bash
# Genera un ejecutable de escritorio con flet pack (PyInstaller).
set -euo pipefail
cd "$(dirname "$0")"
flet pack \
  -n Gifteando \
  -i gifteando/img/logo.png \
  --product-name Gifteando \
  --file-description "Buscador de GIFs animados" \
  --add-data "gifteando/img:gifteando/img" \
  main.py
