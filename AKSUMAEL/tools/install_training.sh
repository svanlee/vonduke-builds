#!/usr/bin/env bash
# Install training dependencies for AKSUMAEL fine-tuning
set -e
cd ~/vonduke-builds/AKSUMAEL

echo "[INSTALL] Installing bitsandbytes..."
venv/bin/python3 -m pip install bitsandbytes --quiet

echo "[INSTALL] Installing unsloth from GitHub..."
venv/bin/python3 -m pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" --quiet

echo "[INSTALL] Installing trl, datasets, transformers..."
venv/bin/python3 -m pip install trl datasets transformers accelerate --quiet

echo "[INSTALL] Done."
venv/bin/python3 -c "import unsloth; print('[INSTALL] unsloth:', unsloth.__version__)"
