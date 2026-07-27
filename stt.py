"""
stt.py - Speech-to-Text con Whisper
"""

import tempfile
import os
import logging
from collections import deque

import sounddevice as sd
import soundfile as sf
import numpy as np
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad
import torch

logger = logging.getLogger(__name__)

from config import (
    WHISPER_MODEL,
    WHISPER_DEVICE,
    VAD_SILENCE_MS,
    VAD_MIN_RECORD_MS,
    VAD_START_THRESHOLD,
    VAD_STOP_THRESHOLD,
)

# Costanti audio
SAMPLE_RATE = 16000
CHANNELS = 1

# Carica modelli (singleton)
_whisper_model = None
_vad_model = None


def get_whisper_model():
    """Lazy load Whisper model."""
    global _whisper_model
    if _whisper_model is None:
        import time
        t0 = time.time()
        print(f"[STT] Caricamento Whisper {WHISPER_MODEL} in VRAM...")
        if WHISPER_DEVICE == "cuda":
            torch.cuda.init()  # ctranslate2 richiede contesto CUDA già inizializzato
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type="float16" if WHISPER_DEVICE == "cuda" else "int8"
        )
        print(f"[STT] Whisper caricato in {time.time() - t0:.1f}s")
    return _whisper_model


def preload():
    """Pre-carica Whisper e VAD all'avvio (evita ritardo alla prima domanda)."""
    get_whisper_model()
    get_vad_model()
    print("[STT] Modelli pre-caricati")


def get_vad_model():
    """Lazy load Silero VAD."""
    global _vad_model
    if _vad_model is None:
        _vad_model = load_silero_vad()
    return _vad_model


def record_audio_with_vad(max_seconds=20.0, stop_event=None, on_speech_start=None):
    """
    Registra audio dal microfono con Voice Activity Detection.

    Args:
        max_seconds:     Durata massima registrazione
        stop_event:      threading.Event — se settato interrompe immediatamente la registrazione
        on_speech_start: callable opzionale — chiamato nel momento in cui il VAD rileva
                         l'inizio del parlato (prima della trascrizione Whisper).
                         Usato da main.py per cancellare l'idle timer non appena
                         l'utente inizia a parlare, senza aspettare la fine della registrazione.

    Returns:
        np.ndarray: Audio registrato (shape: [samples, 1])
    """
    vad = get_vad_model()

    chunk_ms = 32
    chunk_size = int(SAMPLE_RATE * (chunk_ms / 1000.0))
    max_samples = int(SAMPLE_RATE * max_seconds)

    pre_roll_chunks = max(1, int(250 / chunk_ms))  # 250ms pre-roll
    pre_buffer = deque(maxlen=pre_roll_chunks)

    frames = []
    total_samples = 0
    speaking_started = False
    silent_ms = 0.0
    recorded_ms = 0.0

    print("🎤 Parla pure (stop automatico quando smetti)...")

    with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=chunk_size,
    ) as stream:
        while True:
            chunk, _ = stream.read(chunk_size)
            chunk = chunk.reshape(-1).astype("float32")

            total_samples += len(chunk)
            recorded_ms = (total_samples / SAMPLE_RATE) * 1000.0

            # Uscita anticipata se richiesta dall'esterno
            if stop_event and stop_event.is_set():
                break

            # Timeout massimo
            if total_samples >= max_samples:
                break

            # VAD: calcola probabilità parlato
            x = torch.from_numpy(chunk).unsqueeze(0)
            speech_prob = vad(x, SAMPLE_RATE).item()

            # Prima dello start: buffering
            if not speaking_started:
                pre_buffer.append(chunk.copy())

                if speech_prob >= VAD_START_THRESHOLD:
                    speaking_started = True
                    silent_ms = 0.0
                    if on_speech_start:
                        on_speech_start()   # cancella idle timer appena l'utente inizia a parlare
                    # Include pre-roll
                    frames.extend(list(pre_buffer))
                    pre_buffer.clear()
                continue

            # Dopo lo start: registrazione
            frames.append(chunk.copy())

            # Controlla silenzio solo dopo durata minima
            if recorded_ms < VAD_MIN_RECORD_MS:
                continue

            if speech_prob < VAD_STOP_THRESHOLD:
                silent_ms += chunk_ms
            else:
                silent_ms = 0.0

            # Stop su silenzio prolungato
            if silent_ms >= VAD_SILENCE_MS:
                break

    if not frames:
        return np.zeros((0, 1), dtype="float32")

    print("✓ Registrazione completata")
    audio = np.concatenate(frames, axis=0).astype("float32")
    return audio.reshape(-1, 1)


def transcribe_audio(audio, min_lang_prob=0.0):
    """
    Trascrivi audio con Whisper.

    Args:
        audio:        np.ndarray audio
        min_lang_prob: soglia minima confidenza lingua (0.0 = nessun filtro).
                       Se Whisper è meno sicuro di questa soglia, lingua ritornata = "".

    Returns:
        tuple: (testo, lingua)
    """
    if audio.shape[0] == 0:
        return "", ""

    # Salva in temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio, SAMPLE_RATE)

    try:
        whisper = get_whisper_model()

        print("[STT] Trascrizione in corso...")
        segments, info = whisper.transcribe(
            tmp.name,
            beam_size=1,
            language=None,  # Auto-detect
        )

        # Concatena segmenti
        text = "".join(segment.text for segment in segments).strip()
        lang = info.language

        if text:
            print(f"[STT] '{text}' (lingua: {lang}, p={info.language_probability:.2f})")

        # Scarta rilevamento lingua se confidenza troppo bassa
        if min_lang_prob > 0.0 and info.language_probability < min_lang_prob:
            lang = ""

        return text, lang

    finally:
        # Cleanup
        try:
            os.remove(tmp.name)
        except:
            pass


def record_while_held(stop_event):
    """
    Registra audio dal microfono finché stop_event non viene settato (push-to-talk).

    Args:
        stop_event: threading.Event — settato al rilascio del tasto PTT

    Returns:
        np.ndarray: Audio registrato (shape: [samples, 1]), vuoto se nessun frame
    """
    frames = []
    chunk_size = 512
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='float32',
        blocksize=chunk_size,
    ) as stream:
        while not stop_event.is_set():
            chunk, _ = stream.read(chunk_size)
            frames.append(chunk.reshape(-1))

    if not frames:
        return np.zeros((0,), dtype='float32').reshape(-1, 1)
    audio = np.concatenate(frames).astype('float32')
    return audio.reshape(-1, 1)


def listen(stop_event=None, min_lang_prob=0.0, on_speech_start=None):
    """
    Ascolta dal microfono e trascrivi.

    Args:
        stop_event:      threading.Event — se settato interrompe la registrazione
        min_lang_prob:   soglia minima confidenza lingua (0.0 = nessun filtro)
        on_speech_start: callable opzionale — passato a record_audio_with_vad,
                         chiamato nel momento in cui il VAD rileva inizio parlato

    Returns:
        tuple: (testo, lingua) o ("", "") se nessun input
    """
    audio = record_audio_with_vad(stop_event=stop_event, on_speech_start=on_speech_start)

    if audio.shape[0] == 0:
        return "", ""

    # Salta la trascrizione se la sessione è già in chiusura
    if stop_event and stop_event.is_set():
        return "", ""

    return transcribe_audio(audio, min_lang_prob=min_lang_prob)


def _normalize_for_trigger(text: str) -> str:
    """Rimuove punteggiatura e normalizza spazi per il confronto con le frasi trigger."""
    import re
    # Rimuovi tutto tranne lettere Unicode e spazi
    normalized = re.sub(r'[^\w\s]', ' ', text.lower(), flags=re.UNICODE)
    return ' '.join(normalized.split())


def listen_for_trigger(stop_event=None):
    """
    Ascolta in loop finché non viene detta una frase trigger IT o EN.

    Returns:
        "it"  → rilevato TRIGGER_PHRASES_IT
        "en"  → rilevato TRIGGER_PHRASES_EN
        None  → stop_event settato prima del rilevamento
    """
    from config import TRIGGER_PHRASES_IT, TRIGGER_PHRASES_EN

    while True:
        if stop_event and stop_event.is_set():
            return None

        audio = record_audio_with_vad(max_seconds=8.0, stop_event=stop_event)

        if stop_event and stop_event.is_set():
            return None

        if audio.shape[0] == 0:
            continue

        text, _ = transcribe_audio(audio)
        if not text:
            continue

        text_normalized = _normalize_for_trigger(text)
        logger.debug(f"[Trigger] Ascoltato: '{text}' → normalizzato: '{text_normalized}'")

        if any(phrase in text_normalized for phrase in TRIGGER_PHRASES_IT):
            logger.info(f"[Trigger] Frase IT rilevata: '{text}'")
            return "it"

        if any(phrase in text_normalized for phrase in TRIGGER_PHRASES_EN):
            logger.info(f"[Trigger] Frase EN rilevata: '{text}'")
            return "en"

        print(f"[Trigger] Non riconosciuto: '{text}' — riprova")


if __name__ == "__main__":
    # Test
    print("Test STT:")
    text, lang = listen()
    print(f"Risultato: '{text}' (lingua: {lang})")
