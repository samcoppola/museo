@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ================================================================
echo   MUSEO GLADIATORI — Setup Windows
echo ================================================================
echo.

:: ── Controllo Python ──────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato.
    echo Scarica Python 3.11 da https://www.python.org/downloads/
    echo Assicurati di spuntare "Add Python to PATH" durante l'installazione.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo Python trovato: %%v

:: ── Controllo NVIDIA ──────────────────────────────────────────────
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo ERRORE: nvidia-smi non trovato.
    echo Verifica che i driver NVIDIA siano installati.
    echo Scarica da: https://www.nvidia.com/drivers
    pause & exit /b 1
)
echo.
echo GPU rilevata:
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo.

:: ── Ambiente virtuale ─────────────────────────────────────────────
if not exist ".venv" (
    echo Creazione ambiente virtuale .venv ...
    python -m venv .venv
    if errorlevel 1 ( echo ERRORE: impossibile creare venv. & pause & exit /b 1 )
) else (
    echo Ambiente virtuale .venv gia' esistente.
)

call .venv\Scripts\activate.bat

:: ── PyTorch con CUDA 12.1 ─────────────────────────────────────────
echo.
echo Installazione PyTorch con CUDA 12.1 ...
echo (download ~3-4 GB — attendere, puo' volerci qualche minuto)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (
    echo ERRORE: installazione PyTorch fallita.
    pause & exit /b 1
)

:: ── Dipendenze progetto ───────────────────────────────────────────
echo.
echo Installazione dipendenze requirements.txt ...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERRORE: installazione dipendenze fallita.
    pause & exit /b 1
)

:: ── Verifica CUDA da Python ───────────────────────────────────────
echo.
echo Verifica CUDA disponibile da Python:
python -c "import torch; print('  torch:', torch.__version__, '| CUDA:', torch.cuda.is_available(), '| GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

:: ── Verifica audio devices ────────────────────────────────────────
echo.
echo Dispositivi audio rilevati:
python -c "import sounddevice as sd; [print(' ', i, d['name']) for i, d in enumerate(sd.query_devices())]"

echo.
echo ================================================================
echo   Setup completato!
echo ================================================================
echo.
echo PROSSIMI PASSI:
echo.
echo  1. AUDIO VIRTUALE (per Unity lip-sync)
echo     Scarica VB-Audio Virtual Cable da: https://vb-audio.com/Cable/
echo     - Installa come amministratore e riavvia il PC
echo     - In config.py imposta: TTS_OUTPUT_DEVICE = "CABLE Input (VB-Audio Virtual Cable)"
echo     - In Unity: usa "CABLE Output" come sorgente audio per il lip-sync
echo     - L'audio ambientale va alle casse, solo la voce va a Unity
echo.
echo  2. WEBCAM (telecamera per rilevamento persone)
echo     Esegui per trovare l'indice giusto:
echo     python -c "import cv2; [print(f'Webcam {i}: OK' if cv2.VideoCapture(i).isOpened() else f'Webcam {i}: non trovata') for i in range(4)]"
echo     Poi in config.py imposta: PERSON_DETECTION_CAM_INDEX = (numero trovato)
echo.
echo  3. MODELLI (devono essere nella cartella models\)
echo     - models\whisper-large-v3\  → necessario, copiare dalla chiavetta
echo     - models\yolo11n.pt         → scaricato automaticamente al primo avvio
echo.
echo  4. AVVIO
echo     .venv\Scripts\activate
echo     python main.py
echo.
pause
