#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — crea il venv e installa tutte le dipendenze
#
# Uso:
#   chmod +x setup.sh
#   ./setup.sh
#
# Requisiti di sistema (da installare manualmente se mancano):
#   sudo apt install python3-venv portaudio19-dev libsndfile1
# ─────────────────────────────────────────────────────────────────────────────

set -e

PYTHON=${PYTHON:-python3}
VENV_DIR=".venv"
CUDA_VERSION="cu121"   # cambia in cu118 / cu124 se la tua GPU usa CUDA diverso
                        # oppure usa "cpu" per girare senza GPU

echo "============================================================"
echo " Setup Museo — Assistente Vocale"
echo "============================================================"

# 1. Crea venv
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/4] Creazione venv in $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR"
else
    echo "[1/4] Venv già esistente ($VENV_DIR), skip."
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# 2. PyTorch con CUDA (o CPU)
echo "[2/4] Installazione PyTorch ($CUDA_VERSION)..."
if [ "$CUDA_VERSION" = "cpu" ]; then
    pip install torch==2.5.1 torchaudio==2.5.1 --quiet
else
    pip install torch==2.5.1 torchaudio==2.5.1 \
        --index-url "https://download.pytorch.org/whl/$CUDA_VERSION" --quiet
fi

# 3. Tutte le altre dipendenze
echo "[3/4] Installazione dipendenze (requirements.txt)..."
pip install -r requirements.txt --quiet

# 4. Crea directory necessarie
echo "[4/4] Creazione directory dati..."
mkdir -p data/audio_cache logs

echo ""
echo "============================================================"
echo " Setup completato."
echo ""
echo " Prima esecuzione:"
echo "   source .venv/bin/activate"
echo "   python main.py"
echo ""
echo " Al primo avvio verranno scaricati automaticamente:"
echo "   - Whisper 'large' (~3 GB, salvato in ~/.cache/huggingface)"
echo "   - YOLO yolo11n.pt (~6 MB, salvato nella cartella corrente)"
echo "   - Farewell audio ElevenLabs (salvato in data/audio_cache/)"
echo "============================================================"
