#!/usr/bin/env bash
# ==============================================================================
# DistroNomad - Hızlı Başlatıcı (Quick Launcher)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

# Sanal ortam kontrolü
if [ ! -d "${VENV_DIR}" ]; then
    echo "⚡ Python sanal ortamı (.venv) oluşturuluyor..."
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install -e "${SCRIPT_DIR}"
fi

# Uygulamayı sanal ortam Python'ı ile çalıştır
PYTHON_BIN="${VENV_DIR}/bin/python3"

export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON_BIN}" -m distronomad.main "$@"
