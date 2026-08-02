@echo off
setlocal

cd /d "%~dp0"

echo ================================================================
echo   MUSEO GLADIATORI - Setup Windows
echo ================================================================
echo.

REM ----------------------------------------------------------------
REM Controllo Python
REM ----------------------------------------------------------------

python --version >nul 2>&1

if errorlevel 1 (
    echo ERRORE: Python non trovato.
    echo Scarica Python 3.11 da:
    echo https://www.python.org/downloads/
    echo Assicurati di selezionare Add Python to PATH durante l'installazione.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version') do echo Python trovato: %%v

REM ----------------------------------------------------------------
REM Controllo NVIDIA
REM ----------------------------------------------------------------

nvidia-smi >nul 2>&1

if errorlevel 1 (
    echo ERRORE: nvidia-smi non trovato.
    echo Verifica che i driver NVIDIA siano installati.
    echo Scarica i driver da:
    echo https://www.nvidia.com/drivers
    pause
    exit /b 1
)

echo.
echo GPU rilevata:
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo.

REM ----------------------------------------------------------------
REM Ambiente virtuale
REM ----------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
    echo Creazione ambiente virtuale .venv...
    python -m venv ".venv"

    if errorlevel 1 (
        echo ERRORE: impossibile creare l'ambiente virtuale.
        pause
        exit /b 1
    )
) else (
    echo Ambiente virtuale .venv gia esistente.
)

call ".venv\Scripts\activate.bat"

if errorlevel 1 (
    echo ERRORE: impossibile attivare l'ambiente virtuale.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM Aggiornamento pip
REM ----------------------------------------------------------------

echo.
echo Aggiornamento pip...

python -m pip install --upgrade pip

if errorlevel 1 (
    echo ERRORE: aggiornamento pip fallito.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM PyTorch con CUDA 12.1
REM ----------------------------------------------------------------

echo.
echo Installazione PyTorch con CUDA 12.1...
echo Download di circa 3-4 GB. L'operazione potrebbe richiedere alcuni minuti.

python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

if errorlevel 1 (
    echo ERRORE: installazione PyTorch fallita.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM Dipendenze del progetto
REM ----------------------------------------------------------------

echo.

if not exist "requirements.txt" (
    echo ERRORE: requirements.txt non trovato.
    echo Il file deve trovarsi nella stessa cartella di questo script.
    pause
    exit /b 1
)

echo Installazione dipendenze da requirements.txt...

python -m pip install -r "requirements.txt"

if errorlevel 1 (
    echo ERRORE: installazione dipendenze fallita.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM Verifica CUDA da Python
REM ----------------------------------------------------------------

echo.
echo Verifica CUDA disponibile da Python:

python -c "import torch; print('torch:', torch.__version__, '| CUDA:', torch.cuda.is_available(), '| GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

if errorlevel 1 (
    echo ATTENZIONE: verifica di PyTorch o CUDA fallita.
)

REM ----------------------------------------------------------------
REM Verifica dispositivi audio
REM ----------------------------------------------------------------

echo.
echo Dispositivi audio rilevati:

python -c "import sounddevice as sd; [print(' ', i, d['name']) for i, d in enumerate(sd.query_devices())]"

if errorlevel 1 (
    echo ATTENZIONE: impossibile elencare i dispositivi audio.
)

echo.
echo ================================================================
echo   Setup completato!
echo ================================================================
echo.
echo PROSSIMI PASSI:
echo.
echo 1. AUDIO VIRTUALE per Unity lip-sync
echo    Scarica VB-Audio Virtual Cable da:
echo    https://vb-audio.com/Cable/
echo.
echo    - Installa come amministratore e riavvia il PC
echo    - In config.py imposta:
echo      TTS_OUTPUT_DEVICE = "CABLE Input (VB-Audio Virtual Cable)"
echo    - In Unity usa CABLE Output come sorgente audio per il lip-sync
echo    - L'audio ambientale va alle casse, mentre la voce va a Unity
echo.
echo 2. WEBCAM per il rilevamento delle persone
echo    Esegui questo comando per trovare l'indice corretto:
echo.
echo    python -c "import cv2; [print(f'Webcam {i}: OK' if cv2.VideoCapture(i).isOpened() else f'Webcam {i}: non trovata') for i in range(4)]"
echo.
echo    Poi in config.py imposta:
echo    PERSON_DETECTION_CAM_INDEX = numero_trovato
echo.
echo 3. MODELLI nella cartella models
echo    - models\whisper-large-v3
echo    - models\yolo11n.pt
echo.
echo 4. AVVIO
echo    .venv\Scripts\activate
echo    python main.py
echo.
pause