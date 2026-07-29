"""
TTS — ElevenLabs unico motore.
generate_audio(): produce audio_info senza riprodurlo (per pipeline streaming).
play_audio():     riproduce audio_info (blocca fino a fine).
speak():          generate + play con duck/unduck ambientale (per messaggi fissi).
"""
import os
import io
import logging
import queue
import threading
import pygame
import config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ELEVENLABS — streaming PCM playback
# ═══════════════════════════════════════════════════════════════════════════════

def _play_pcm_stream(chunks_iter, sample_rate=44100, interrupt_event=None):
    """
    Riproduce stream PCM chunk per chunk via sounddevice.
    Fallback a pygame WAV se sounddevice non è installato.
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.warning("[TTS] sounddevice non trovato — fallback pygame WAV")
        _play_pcm_as_wav(chunks_iter, sample_rate, interrupt_event)
        return

    output_device = getattr(config, 'TTS_OUTPUT_DEVICE', None)

    # Pre-buffer: raccoglie ~0.5s di PCM prima di aprire lo stream.
    # Evita underrun (crackle/freeze) quando ElevenLabs ha micro-ritardi
    # tra un chunk e l'altro (jitter di rete tipico).
    PRE_BYTES = sample_rate * 2 // 2  # 0.5s × sample_rate × 2 byte/campione
    prebuf = bytearray()
    for chunk in chunks_iter:
        if interrupt_event and interrupt_event.is_set():
            return
        if chunk:
            prebuf.extend(chunk)
            if len(prebuf) >= PRE_BYTES:
                break

    if not prebuf:
        return

    try:
        interrupted = False
        with sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype='int16', device=output_device) as stream:
            stream.write(bytes(prebuf))
            for chunk in chunks_iter:
                if interrupt_event and interrupt_event.is_set():
                    stream.abort()
                    interrupted = True
                    break
                if chunk:
                    stream.write(chunk)
            if not interrupted:
                # Blocca finché il buffer audio è completamente svuotato.
                # Senza questo, la funzione ritorna mentre il comandante parla ancora.
                stream.stop()
    except Exception as e:
        logger.error(f"[TTS] Errore sounddevice: {e}")


def _play_pcm_as_wav(chunks_iter, sample_rate, interrupt_event=None):
    """Fallback: raccoglie PCM, aggiunge header WAV, riproduce via pygame."""
    import wave as _wave
    import time as _time

    data = b"".join(chunks_iter)
    if not data:
        return

    buf = io.BytesIO()
    with _wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    buf.seek(0)

    if not pygame.mixer.get_init():
        pygame.mixer.init(frequency=sample_rate, size=-16, channels=1, buffer=512)
    sound = pygame.mixer.Sound(buf)
    channel = sound.play()
    if channel:
        end_time = _time.time() + sound.get_length() + 0.1
        while _time.time() < end_time:
            if interrupt_event and interrupt_event.is_set():
                channel.stop()
                break
            _time.sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════════
# ELEVENLABS — generazione audio
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_elevenlabs(text, lang, static=False):
    """
    Genera audio con ElevenLabs API.

    static=False: streaming PCM via sounddevice (latenza ~100-200ms primo audio).
      Ritorna quasi subito — play_audio() consuma i chunk in arrivo.
      → {"type": "stream_pcm", "chunks": iterator, "sample_rate": 44100}

    static=True: MP3 completo in memoria (per pre-gen su disco).
      → {"type": "pygame", "sound": Sound, "_mp3_bytes": bytes}
    """
    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.types import VoiceSettings

        client = ElevenLabs(api_key=config.ELEVEN_API_KEY)
        voice_settings = VoiceSettings(
            speed=config.ELEVEN_SPEED,
            stability=getattr(config, 'ELEVEN_STABILITY', 0.55),
            similarity_boost=getattr(config, 'ELEVEN_SIMILARITY_BOOST', 0.80),
        )
        use_stream = (not static) and config.ELEVEN_STREAM

        if use_stream:
            chunks_iter = client.text_to_speech.stream(
                text=text,
                voice_id=config.ELEVEN_VOICE,
                model_id=config.ELEVEN_MODEL_ID,
                output_format="pcm_44100",
                language_code=lang,
                voice_settings=voice_settings,
                apply_text_normalization="on",
            )
            return {
                "type": "stream_pcm",
                "chunks": chunks_iter,
                "sample_rate": 44100,
            }
        else:
            import base64
            response = client.text_to_speech.convert(
                text=text,
                voice_id=config.ELEVEN_VOICE,
                model_id=config.ELEVEN_MODEL_ID,
                output_format="mp3_44100_128",
                language_code=lang,
                voice_settings=voice_settings,
            )
            audio_bytes = response if isinstance(response, bytes) else b"".join(response)

            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

            return {
                "type": "pygame",
                "sound": pygame.mixer.Sound(io.BytesIO(audio_bytes)),
                "_mp3_bytes": audio_bytes,
            }

    except Exception as e:
        logger.error(f"[TTS] Errore ElevenLabs: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# API PUBBLICA
# ═══════════════════════════════════════════════════════════════════════════════

def generate_audio(text, lang="it", static=False):
    """
    Genera audio TTS senza riprodurlo. Per la pipeline streaming e pre-gen.
    Ritorna audio_info o None se ElevenLabs fallisce.
    """
    if not text or not text.strip():
        return None

    result = _generate_elevenlabs(text, lang, static=static)
    if not result:
        logger.error("[TTS] ElevenLabs fallito — nessun audio disponibile")
    return result


def play_audio(audio_info, interrupt_event=None):
    """
    Riproduce audio_info pre-generato. Blocca fino a fine o interrupt.

    Tipi:
      "stream_pcm" — ElevenLabs streaming, riprodotto chunk per chunk via sounddevice
      "pygame"     — pygame.mixer.Sound (ElevenLabs static / pre-gen da disco)
    """
    if not audio_info:
        return

    if audio_info.get("type") == "stream_pcm":
        _play_pcm_stream(
            audio_info["chunks"],
            audio_info.get("sample_rate", 44100),
            interrupt_event,
        )
        return

    # pygame Sound
    try:
        import time as _time

        if pygame.mixer.get_num_channels() < 16:
            pygame.mixer.set_num_channels(16)

        channel = audio_info["sound"].play()
        if channel is None:
            logger.warning("[TTS] Nessun canale pygame disponibile")
            return

        duration = audio_info["sound"].get_length()
        end_time = _time.time() + duration + 0.1
        while _time.time() < end_time:
            if interrupt_event and interrupt_event.is_set():
                channel.stop()
                break
            _time.sleep(0.05)

    except Exception as e:
        logger.error(f"[TTS] Errore playback pygame: {e}")


def _elevenlabs_pcm_stream(text: str, lang: str):
    """Chiama ElevenLabs stream() e restituisce un iteratore di chunk PCM bytes."""
    from elevenlabs.client import ElevenLabs
    from elevenlabs.types import VoiceSettings
    client = ElevenLabs(api_key=config.ELEVEN_API_KEY)
    voice_settings = VoiceSettings(
        speed=config.ELEVEN_SPEED,
        stability=getattr(config, 'ELEVEN_STABILITY', 0.55),
        similarity_boost=getattr(config, 'ELEVEN_SIMILARITY_BOOST', 0.80),
    )
    return client.text_to_speech.stream(
        voice_id=config.ELEVEN_VOICE,
        text=text,
        model_id=config.ELEVEN_MODEL_ID,
        output_format="pcm_44100",
        language_code=lang,
        voice_settings=voice_settings,
        apply_text_normalization="on",  # rispetta punti, virgole e ! per pause e intonazione
    )


def play_response_stream(sentence_gen, lang="it"):
    """
    Pipeline LLM → ElevenLabs → sounddevice per risposte conversazionali.

    - tts_thread: consuma sentence_gen, accumula frasi fino a MIN_CHARS,
      poi chiama ElevenLabs per ogni batch e mette i chunk PCM in audio_q.
    - main thread: pre-bufferizza 0.5s di PCM, poi apre un UNICO stream
      sounddevice e scrive continuamente dalla queue.

    Risultato: nessuna pausa tra batch (stream rimane aperto), primo audio
    in ~2-3s dalla prima frase LLM, prosodia naturale (ogni batch ha contesto
    multi-frase per ElevenLabs).
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.warning("[TTS] sounddevice non installato")
        for _ in sentence_gen:
            pass
        return

    output_device = getattr(config, 'TTS_OUTPUT_DEVICE', None)
    audio_q = queue.Queue()
    _DONE = object()
    MIN_CHARS = 120  # minimo per chiamata ElevenLabs — abbastanza per prosodia naturale, abbastanza piccolo da iniziare presto

    def _tts_thread():
        pending = ""
        try:
            for sentence in sentence_gen:
                pending = (pending + " " + sentence).strip() if pending else sentence
                if len(pending) >= MIN_CHARS:
                    try:
                        for chunk in _elevenlabs_pcm_stream(pending, lang):
                            if chunk:
                                audio_q.put(chunk)
                    except Exception as e:
                        logger.error(f"[TTS] Errore ElevenLabs: {e}")
                    pending = ""
            if pending:
                try:
                    for chunk in _elevenlabs_pcm_stream(pending, lang):
                        if chunk:
                            audio_q.put(chunk)
                except Exception as e:
                    logger.error(f"[TTS] Errore ElevenLabs (finale): {e}")
        except Exception as e:
            logger.error(f"[TTS] Errore tts_thread: {e}")
        finally:
            audio_q.put(_DONE)

    tts_t = threading.Thread(target=_tts_thread, daemon=True)
    tts_t.start()

    # Pre-buffer: raccoglie 0.5s prima di aprire sounddevice
    PRE_BYTES = 44100 * 2 // 2
    prebuf = bytearray()
    done_in_prebuf = False
    while len(prebuf) < PRE_BYTES:
        item = audio_q.get()
        if item is _DONE:
            done_in_prebuf = True
            break
        prebuf.extend(item)

    if not prebuf:
        tts_t.join()
        return

    try:
        with sd.RawOutputStream(samplerate=44100, channels=1, dtype='int16',
                                 device=output_device) as stream:
            stream.write(bytes(prebuf))
            if not done_in_prebuf:
                for item in iter(audio_q.get, _DONE):
                    if item:
                        stream.write(item)
            stream.stop()
    except Exception as e:
        logger.error(f"[TTS] Errore sounddevice: {e}")

    tts_t.join()


def speak(text, lang="it", ambient_manager=None):
    """
    Genera e riproduce testo (versione semplice, non-streaming).
    Usata per welcome, farewell e messaggi di errore.
    """
    if not text or not text.strip():
        return

    if ambient_manager:
        ambient_manager.duck()
    try:
        audio_info = generate_audio(text, lang)
        if audio_info:
            play_audio(audio_info)
        else:
            logger.error("[TTS] Nessun audio generato per speak()")
    finally:
        if ambient_manager:
            ambient_manager.unduck()
