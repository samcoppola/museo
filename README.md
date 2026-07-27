# Museo Gladiatori — Marco Aurelio Maximus

Sistema di intelligenza artificiale conversazionale per museo.  
Il visitatore interagisce a voce con un comandante romano del II secolo d.C.

---

## Come funziona

1. La telecamera rileva l'arrivo di una persona
2. Il sistema aspetta la frase trigger: **"Salve Comandante"** (IT) o **"Hail Commander"** (EN)
3. Il comandante dà il benvenuto nella lingua rilevata
4. Il visitatore parla liberamente — il sistema ascolta automaticamente (no pulsante)
5. La sessione si chiude se:
   - Il visitatore se ne va (rilevato dalla telecamera)
   - 5 secondi di silenzio dopo l'ultima risposta
   - Il visitatore dice "arrivederci" / "goodbye" / "basta"
6. Dopo 3 secondi il sistema è pronto per il prossimo visitatore

---

## Requisiti hardware

| Componente           | Minimo              | Consigliato            |
|----------------------|---------------------|------------------------|
| Sistema operativo    | Windows 10 64-bit   | Windows 11 64-bit      |
| GPU                  | NVIDIA con 8 GB VRAM | 12+ GB VRAM            |
| RAM                  | 16 GB               | 32 GB                  |
 | Driver NVIDIA        |  550                | pari o superiore a 550 |
| Microfono            | USB o jack          | USB (più stabile)      |
| Webcam               | USB                 | USB                    |
| Connessione internet | Necessaria (API)    | —                      |

---

## Setup su Windows (guida completa)

### Step 1 — Installa Python 3.10

1. Vai su: **https://www.python.org/downloads/release/python-31011/**
2. Scarica `Windows installer (64-bit)`
3. Apri il file scaricato
4. **IMPORTANTE:** spunta **"Add Python to PATH"** in basso prima di procedere
5. Clicca **"Install Now"**, poi **"Close"**

**Verifica:** apri il menu Start → cerca `cmd` → apri il Prompt dei comandi → scrivi:
```
python --version
```
Deve rispondere `Python 3.10.x`.

---

### Step 2 — Verifica i driver NVIDIA

Nel Prompt dei comandi scrivi:
```
nvidia-smi
```
Se mostra una tabella con il nome della GPU e `CUDA Version: 12.x` (o superiore): tutto ok.

Se il comando non viene riconosciuto: vai su **https://www.nvidia.com/drivers**,
cerca il driver per la GPU del PC e installalo.

> **Nota versione CUDA:** il progetto funziona con CUDA 12.1 o qualsiasi versione 12.x superiore (12.2, 12.4…).
---

### Step 3 — Copia il progetto sul PC

Copia la cartella `museo/` dove preferisci, ad esempio `C:\museo\`.

La struttura interna deve essere:
```
C:\museo\
  config.py
  main.py
  tts.py
  stt.py
  agent.py
  audio_manager.py
  conversation.py
  person_detector.py
  requirements.txt
  prompts\
  models\
    whisper-large-v3\      ← cartella con i file del modello (~3 GB)
    yolo11n.pt
  sounds\
    ambient\
      ambient_soundscape.mp3
  data\
    audio_cache\
  logs\
```

> Se la cartella `whisper-large-v3` non è presente, verrà scaricata automaticamente
> alla prima esecuzione (~3 GB, richiede internet e diversi minuti).

---

### Step 4 — Installa le dipendenze (metodo automatico consigliato)

Nella cartella `museo/` c'è già lo script `setup_windows.bat` che fa tutto da solo.

1. Apri il Prompt dei comandi
2. Vai nella cartella del progetto:
   ```cmd
   cd C:\museo
   ```
3. Lancia lo script:
   ```cmd
   setup_windows.bat
   ```

Lo script crea il venv, installa PyTorch con CUDA e tutte le dipendenze — scarica circa **4-5 GB** in totale, aspetta finché non finisce. Alla fine mostra un riepilogo con i prossimi passi.

> **Ogni volta che riapri il Prompt dei comandi** per usare il programma devi eseguire:
> ```cmd
> cd C:\museo
> .venv\Scripts\activate
> ```

**Metodo manuale (alternativa se il .bat non funziona):**
```cmd
cd C:\museo
python -m venv .venv
.venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
```

**Verifica che la GPU sia riconosciuta:**
```cmd
python -c "import torch; print(torch.cuda.is_available())"
```
Deve rispondere `True`. Se risponde `False`, i driver NVIDIA non sono installati correttamente (vedi Step 2).

---

### Step 7 — Installa VB-Audio Virtual Cable

Il cavo virtuale instrada la voce del comandante verso Unity per il lip-sync,
separata dall'audio ambientale che va alle casse fisiche.

1. Vai su: **https://vb-audio.com/Cable/** — è gratuito
2. Clicca **Download**
3. Estrai lo zip scaricato
4. Clicca con il **tasto destro** su `VBCABLE_Setup_x64.exe` → **"Esegui come amministratore"**
5. Clicca **Install Driver**
6. **Riavvia il PC** quando richiesto

---

### Step 8 — Trova il nome del device audio

Dopo il riavvio, apri il Prompt dei comandi e scrivi:
```cmd
cd C:\museo
.venv\Scripts\activate
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Vedrai una lista di dispositivi. Cerca la riga che contiene `CABLE Input`, ad esempio:
```
CABLE Input (VB-Audio Virtual Cable)
```
**Copia il nome esatto** — ti servirà nel passo successivo.

---

### Step 9 — Configura config.py

Apri `C:\museo\config.py` con il **Blocco Note**
(tasto destro sul file → Apri con → Blocco Note).

**1. Imposta il device audio per Unity:**

Trova:
```python
TTS_OUTPUT_DEVICE = None  # Windows: "CABLE Input"
```
Cambia `None` con il nome trovato al passo precedente:
```python
TTS_OUTPUT_DEVICE = "CABLE Input (VB-Audio Virtual Cable)"
```

**2. Imposta l'indice della webcam:**

Prima scopri quale indice corrisponde alla webcam giusta. Nel Prompt dei comandi scrivi:
```cmd
python -c "import cv2; [print(f'Webcam {i}: OK' if cv2.VideoCapture(i).isOpened() else f'Webcam {i}: non trovata') for i in range(4)]"
```
Stamperà quali webcam sono disponibili (es. `Webcam 0: OK`, `Webcam 1: OK`, `Webcam 2: non trovata`).

Poi in `config.py` trova:
```python
PERSON_DETECTION_CAM_INDEX = 2
```
e metti l'indice della webcam da usare per il rilevamento persone.

Salva e chiudi il Blocco Note.

---

### Step 9b — Test audio (opzionale ma consigliato)

Prima di avviare il programma, verifica che la voce arrivi correttamente al cavo virtuale:
```cmd
python -c "
import sounddevice as sd, numpy as np
data = (np.sin(2 * np.pi * 440 * np.arange(44100*2) / 44100) * 0.3).astype('float32')
sd.play(data, samplerate=44100, device='CABLE Input (VB-Audio Virtual Cable)')
sd.wait()
print('Audio OK — se Unity è aperto e ascolta CABLE Output, deve aver sentito un tono')
"
```
*(Sostituisci il nome device con quello trovato al Step 8 se diverso)*

Se crasha: il nome del device è sbagliato, ricontrolla con `sd.query_devices()`.  
Se stampa `Audio OK`: il routing verso Unity funziona.

---

### Step 10 — Prima esecuzione

```cmd
cd C:\museo
.venv\Scripts\activate
python main.py
```

**La prima volta** ci vorrà più tempo del normale:
- CTranslate2 (Whisper) compila i kernel CUDA → può richiedere **2-3 minuti**, schermo fermo
- YOLO potrebbe scaricare `yolo11n.pt` (~6 MB) se non è nella cartella `models/`
- Dalla seconda esecuzione tutto parte in **20-30 secondi**

Quando vedi la riga:
```
[Sistema] Pronto.
```
il sistema è operativo.

---

## Avvio rapido (dopo la prima installazione)

```cmd
cd C:\museo
.venv\Scripts\activate
python main.py
```

Per fermare il sistema: `Ctrl+C`

---

## Configurazione Unity (lip-sync)

La voce del comandante esce da **`CABLE Output (VB-Audio Virtual Cable)`**.

Nel plugin lip-sync di Unity (OVRLipSync, Salsa, o equivalente):
- Imposta come sorgente audio di input: `CABLE Output (VB-Audio Virtual Cable)`
- Formato segnale: PCM 44100 Hz, mono, 16-bit

L'audio ambientale (musica romana di sottofondo) va direttamente alle casse fisiche
e **non** passa per il cavo virtuale — Unity riceve solo la voce del comandante.

---

## Struttura del progetto

```
museo/
├── main.py              # punto di ingresso — avvia il sistema
├── config.py            # tutte le impostazioni (API keys, soglie, device audio…)
├── agent.py             # logica conversazione con Claude AI
├── tts.py               # sintesi vocale (ElevenLabs)
├── stt.py               # riconoscimento vocale (Whisper + VAD)
├── audio_manager.py     # musica ambientale di sottofondo
├── person_detector.py   # rilevamento presenza (YOLO + webcam)
├── conversation.py      # gestione cronologia conversazione
├── prompts/
│   ├── it/comandante_v4.py   # personalità del comandante (italiano)
│   └── en/commander_v4.py    # personalità del comandante (inglese)
├── sounds/
│   └── ambient/ambient_soundscape.mp3
├── models/
│   ├── whisper-large-v3/     # modello STT (~3 GB)
│   └── yolo11n.pt            # modello rilevamento persone
├── data/audio_cache/         # farewell pre-generati (creata al primo avvio)
├── logs/                     # log sessioni giornalieri
└── requirements.txt
```

---

## Perché native Windows (no Docker, no WSL2)

Il sistema richiede che Python e Unity **condividano lo stesso layer audio Windows**:

```
Python (sounddevice) → CABLE Input (VB-Audio) → CABLE Output → Unity (lip-sync)
```

VB-Audio Virtual Cable è un driver OS-level di Windows — non è visibile
dall'interno di Docker o WSL2, che sono ambienti isolati.

| Opzione | Problema |
|---|---|
| **Docker** | I container non vedono i device audio Windows; GPU richiede configurazione complessa; VB-Audio non raggiungibile da Unity |
| **WSL2** | Nessun accesso diretto ai device audio Windows; webcam richiede `usbipd-win` sperimentale; VB-Audio → Unity non funziona |
| **Native Windows** ✅ | VB-Audio funziona nativamente, GPU diretta, webcam diretta, Unity sulla stessa macchina |

---

## Risoluzione problemi

| Sintomo | Causa probabile | Soluzione |
|---|---|---|
| `python` non riconosciuto | Python non in PATH | Reinstalla Python con "Add to PATH" spuntato |
| `torch.cuda.is_available()` → False | Driver NVIDIA assenti o vecchi | Aggiorna driver da nvidia.com |
| Webcam non trovata | Indice sbagliato | Cambia `PERSON_DETECTION_CAM_INDEX` (prova 0, 1, 2) |
| Audio non arriva a Unity | Nome device sbagliato | Ricontrolla con `sd.query_devices()` |
| `CABLE Input` non compare | VB-Audio non installato | Ripeti Step 7 con "Esegui come amministratore" |
| Sistema fermo 2-3 min al primo avvio | Compilazione kernel CUDA | Normale, aspetta — dalla seconda run è veloce |
| Non sente la voce del visitatore | Microfono non di default | Vai in Impostazioni Windows → Audio → imposta il microfono come predefinito |

---

## Crediti API (costi)

| Servizio | Uso | Costo indicativo |
|---|---|---|
| **Anthropic (Claude)** | Ogni risposta del comandante | ~$0.003 per risposta |
| **ElevenLabs** | Ogni risposta audio | Piano Creator $22/mese (100k caratteri) |

Il farewell è pre-generato all'avvio e salvato su disco — non consuma crediti ElevenLabs ad ogni sessione.
