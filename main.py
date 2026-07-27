"""
Assistente vocale museo — versione definitiva.

Avvio sessione:
  "Salve Comandante" → avvia sessione in Italiano
  "Hail Commander"   → avvia sessione in English
  (frase trigger rilevata via STT dopo rilevamento persona)

Chiusura sessione (automatica):
  - Persona esce dalla telecamera → finisce risposta corrente → farewell → IDLE
  - Silenzio per N secondi dopo l'ultima risposta → farewell → IDLE
  - Utente dice parola di congedo (arrivederci, goodbye…) → farewell → IDLE

Controllo operatore:
  Ctrl+C → spegni sistema
"""
import os
import io
import hashlib
import time
import sys
import threading
import traceback
import logging
import logging.handlers
from datetime import datetime
import httpx

import config
from agent import CharacterAgent

if config.USE_TTS:
    from tts import speak as tts_speak, generate_audio, play_audio, play_response_stream

if config.USE_STT:
    from stt import listen, listen_for_trigger, preload as preload_stt

if getattr(config, 'USE_PERSON_DETECTION', False):
    from person_detector import PersonDetector

logger = logging.getLogger(__name__)


# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logging():
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_file = os.path.join(config.LOG_DIR, f"museo_{datetime.now():%Y%m%d}.log")
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if config.LOG_TO_CONSOLE:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    return logging.getLogger(__name__)


# ── Stato sessione ─────────────────────────────────────────────────────────────

_SESSION_IDLE   = "idle"
_SESSION_ACTIVE = "active"
_session_state  = _SESSION_IDLE
_session_count  = 0

_start_event         = threading.Event()   # trigger rilevato → avvia sessione
_stop_event          = threading.Event()   # chiudi sessione (persona esce / idle / esc)
_exit_event          = threading.Event()   # Ctrl+C → spegni sistema
_trigger_stop_event  = threading.Event()   # cancella ascolto trigger

_session_lang  = [None]   # "it" o "en" — settato dal trigger listener
_trigger_thread = [None]  # thread listen_for_trigger attivo

# ── Idle timer ─────────────────────────────────────────────────────────────────

_idle_timer = [None]


def _on_idle_timeout():
    if _session_state == _SESSION_ACTIVE:
        logger.warning(f"[Session #{_session_count}] Idle timeout ({config.IDLE_TIMEOUT_SECONDS}s)")
        _stop_event.set()


def _reset_idle_timer():
    _cancel_idle_timer()
    t = threading.Timer(config.IDLE_TIMEOUT_SECONDS, _on_idle_timeout)
    t.daemon = True
    t.start()
    _idle_timer[0] = t


def _cancel_idle_timer():
    if _idle_timer[0] is not None:
        _idle_timer[0].cancel()
        _idle_timer[0] = None


# ── Person exit timer ──────────────────────────────────────────────────────────

_person_exit_timer = [None]


def _cancel_person_exit_timer():
    if _person_exit_timer[0] is not None:
        _person_exit_timer[0].cancel()
        _person_exit_timer[0] = None


# ── Trigger listener ───────────────────────────────────────────────────────────

def _trigger_listener(person_detected=False):
    """
    Thread: ascolta finché viene detta 'Salve Comandante' (IT) o 'Hail Commander' (EN).
    Quando trovata, imposta la lingua e lancia _start_event.
    person_detected=True stampa la riga 'PERSONA RILEVATA' (usata con telecamera).
    """
    if not config.USE_STT:
        # Fallback senza STT: avvia con lingua default
        _session_lang[0] = config.DEFAULT_LANG
        _start_event.set()
        return

    if person_detected:
        print("\n" + "=" * 70)
        print(" PERSONA RILEVATA — In attesa frase trigger".center(70))
        print("=" * 70)
        print()
        print('  ITA → "Salve Comandante"    |    ENG → "Hail Commander"')
        print()

    lang = listen_for_trigger(stop_event=_trigger_stop_event)

    if lang is None:
        logger.info("[Trigger] Ascolto interrotto (persona uscita o stop)")
        print("\n[Trigger] Ascolto interrotto — sistema in attesa\n")
        return

    if _session_state == _SESSION_IDLE:
        logger.info(f"[Trigger] Lingua rilevata: {lang}")
        _session_lang[0] = lang
        _start_event.set()


def _start_trigger_listener(person_detected=False):
    """Avvia il thread di ascolto trigger, cancellando l'eventuale precedente."""
    _trigger_stop_event.set()
    old = _trigger_thread[0]
    if old and old.is_alive():
        old.join(timeout=1.0)
    _trigger_stop_event.clear()
    t = threading.Thread(target=_trigger_listener, kwargs={"person_detected": person_detected}, daemon=True)
    _trigger_thread[0] = t
    t.start()


# ── Helpers ────────────────────────────────────────────────────────────────────

_welcome_counter = 0


def get_welcome_message(lang):
    global _welcome_counter
    messages = config.WELCOME_MESSAGES_V4.get(lang, config.WELCOME_MESSAGES_V4["it"])
    welcome = messages[_welcome_counter % len(messages)]
    _welcome_counter += 1
    return welcome


def is_goodbye(text, lang):
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in config.END_KEYWORDS.get(lang, []))


def print_banner():
    print("=" * 70)
    print("MUSEO GLADIATORI".center(70))
    print("INCONTRO CON IL COMANDANTE".center(70))
    print("=" * 70)


# ── Pre-gen audio su disco (farewell) ─────────────────────────────────────────

_pregenerated_audio = {}


def _load_pregen_audio(key, text, lang):
    """
    Carica farewell da disco se testo invariato, altrimenti genera e salva.
    Evita chiamate ElevenLabs a ogni avvio (risparmio crediti).
    """
    if not getattr(config, 'PREGEN_AUDIO', True):
        return generate_audio(text, lang=lang, static=True)

    cache_dir = config.PREGEN_AUDIO_DIR
    os.makedirs(cache_dir, exist_ok=True)

    text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    cache_file = os.path.join(cache_dir, f"{key}_{text_hash}.mp3")

    # rimuovi file obsoleti per questo key (testo cambiato → hash diverso)
    for fname in os.listdir(cache_dir):
        if fname.startswith(f"{key}_") and fname != os.path.basename(cache_file):
            try:
                os.remove(os.path.join(cache_dir, fname))
            except Exception:
                pass

    if os.path.exists(cache_file):
        try:
            import pygame as _pg
            if not _pg.mixer.get_init():
                _pg.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            with open(cache_file, 'rb') as f:
                mp3_bytes = f.read()
            sound = _pg.mixer.Sound(io.BytesIO(mp3_bytes))
            logger.info(f"[Pregen] Caricato da disco: {key}")
            return {"type": "pygame", "sound": sound}
        except Exception as e:
            logger.warning(f"[Pregen] Errore caricamento {key}: {e}")

    audio_info = generate_audio(text, lang=lang, static=True)
    if not audio_info:
        return None

    mp3_bytes = audio_info.pop("_mp3_bytes", None)
    if mp3_bytes:
        try:
            with open(cache_file, 'wb') as f:
                f.write(mp3_bytes)
            logger.info(f"[Pregen] Salvato su disco: {key}")
        except Exception as e:
            logger.warning(f"[Pregen] Errore salvataggio {key}: {e}")

    return audio_info


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _session_state, _session_count, logger

    logger = _setup_logging()
    print_banner()

    # 1. Pre-carica Whisper + VAD
    if config.USE_STT:
        print("\n[Avvio] Pre-caricamento modelli STT...")
        preload_stt()

    # 2. Inizializza agente (contiene client Anthropic + audio ambient)
    try:
        agent = CharacterAgent()
        if config.DEBUG_MODE:
            print(f"\n[Debug] {agent}\n")
    except Exception as e:
        print(f"\n[ERRORE CRITICO] Impossibile inizializzare l'agente: {e}")
        sys.exit(1)

    # 3. Avvia person detector (solo per rilevare uscita → chiudi sessione)
    detector = None
    if getattr(config, 'USE_PERSON_DETECTION', False):

        def _on_person_enter():
            _cancel_person_exit_timer()
            if _session_state == _SESSION_IDLE:
                logger.info("[PersonDetector] Persona rilevata — avvio ascolto trigger")
                _start_trigger_listener(person_detected=True)
            else:
                logger.info("[PersonDetector] Persona rilevata (sessione già attiva)")

        def _on_person_exit():
            # Ferma il trigger listener se stiamo aspettando la frase di avvio
            _trigger_stop_event.set()

            if _session_state != _SESSION_ACTIVE:
                logger.info("[PersonDetector] Persona uscita (sessione non attiva)")
                return
            delay = getattr(config, 'PERSON_DETECTION_EXIT_DELAY', 0.0)
            if delay > 0:
                _cancel_person_exit_timer()
                t = threading.Timer(delay, _stop_event.set)
                t.daemon = True
                _person_exit_timer[0] = t
                t.start()
                logger.info(f"[PersonDetector] Persona uscita — chiusura in {delay:.1f}s")
            else:
                _stop_event.set()
                logger.info("[PersonDetector] Persona uscita — sessione chiusa")

        detector = PersonDetector(
            cam_index=getattr(config, 'PERSON_DETECTION_CAM_INDEX', 0),
            conf=getattr(config, 'PERSON_DETECTION_CONF', 0.5),
            persistent_frames=getattr(config, 'PERSON_DETECTION_PERSISTENT_FRAMES', 5),
            resize_width=getattr(config, 'PERSON_DETECTION_RESIZE_WIDTH', 640),
            model_path=config.PERSON_DETECTION_MODEL,
            show_window=False,
            on_person_enter=_on_person_enter,
            on_person_exit=_on_person_exit,
        )
        detector.start()

    # 5. Avvia ambient (solo in modalità tastiera; in modalità telecamera parte con la sessione)
    ambient = getattr(agent, 'ambient', None)
    if ambient and not getattr(config, 'USE_PERSON_DETECTION', False):
        ambient.start()

    # 6. Pre-genera farewell (risparmio crediti ElevenLabs)
    if config.USE_TTS and getattr(config, 'PREGEN_AUDIO', True):
        print("[Avvio] Pre-generazione farewell (cache disco)...")
        for _lang in config.SUPPORTED_LANGUAGES:
            farewell_text = config.FAREWELL_MESSAGES.get(_lang, "")
            if farewell_text:
                audio = _load_pregen_audio(f"farewell_{_lang}", farewell_text, _lang)
                if audio:
                    _pregenerated_audio[f"farewell_{_lang}"] = audio
        print(f"[Avvio] Audio pre-generati: {list(_pregenerated_audio.keys())}")

    logger.info(f"Sistema avviato. Modello: {config.MODEL}, TTS: ElevenLabs")

    print("\n[Sistema] Pronto.")
    if getattr(config, 'USE_PERSON_DETECTION', False):
        print('[Sistema] Telecamera attiva — di\' "Salve Comandante" (IT) o "Hail Commander" (EN) | Esc = Chiudi sessione | Ctrl+C = Spegni\n')
    else:
        print('[Sistema] Di\' "Salve Comandante" (IT) o "Hail Commander" (EN) | Esc = Chiudi sessione | Ctrl+C = Spegni\n')

    try:
        while True:
            # ── IDLE: attendi frase trigger ────────────────────────────────────
            _session_state = _SESSION_IDLE
            _start_event.clear()
            _stop_event.clear()
            _trigger_stop_event.clear()

            print("=" * 70)
            if getattr(config, 'USE_PERSON_DETECTION', False):
                print(" SISTEMA IN ATTESA — Monitoraggio telecamera attivo".center(70))
                print("=" * 70 + "\n")
                # Se una persona è già in campo (rimasta dal turista precedente),
                # avvia subito l'ascolto trigger — il PersonDetector non ri-trigghera
                # _on_person_enter() se la persona non è mai uscita dal frame.
                if detector is not None and detector.person_present:
                    _start_trigger_listener(person_detected=True)
            else:
                print(" IN ATTESA FRASE TRIGGER".center(70))
                print("=" * 70)
                print()
                print('  ITA → "Salve Comandante"    |    ENG → "Hail Commander"')
                print()
                # Senza telecamera: avvia subito l'ascolto trigger
                if config.USE_STT:
                    _start_trigger_listener(person_detected=False)

            while not _start_event.is_set():
                if _exit_event.is_set():
                    raise KeyboardInterrupt
                time.sleep(0.1)

            # ── SESSIONE ATTIVA ────────────────────────────────────────────────
            _session_state = _SESSION_ACTIVE
            _session_count += 1
            session_start_time = time.time()
            session_lang = _session_lang[0] or config.DEFAULT_LANG
            current_lang = session_lang
            is_goodbye_triggered = False
            session_end_reason = "esc"

            logger.info(f"[Session #{_session_count}] Avviata — lingua: {session_lang}")

            if ambient:
                ambient.start()

            # Welcome message
            welcome = get_welcome_message(session_lang)
            print(f"\n  COMANDANTE: {welcome}\n")
            if config.USE_TTS:
                tts_speak(welcome, lang=session_lang, ambient_manager=ambient)
            agent.inject_welcome_message(welcome, lang=session_lang)

            # Avvia idle timer dopo il welcome
            _reset_idle_timer()

            print(f"[Sistema] Sessione attiva | Esc = chiudi sessione\n")
            print("-" * 70)

            # ── LOOP CONVERSAZIONE ─────────────────────────────────────────────
            while not _stop_event.is_set():
                try:
                    # Ascolto VAD
                    if config.USE_STT:
                        if ambient:
                            ambient.duck()
                        question, detected_lang = listen(
                            stop_event=_stop_event,
                            on_speech_start=_cancel_idle_timer,  # cancella timer appena l'utente inizia a parlare
                        )
                        if ambient:
                            ambient.unduck()

                        if not question or not question.strip():
                            # listen() ritornata vuota: o silenzio o stop_event settato
                            continue
                    else:
                        # Modalità testo (debug senza microfono)
                        print("  TU: ", end="", flush=True)
                        try:
                            question = sys.stdin.readline().rstrip('\n')
                        except Exception:
                            continue
                        detected_lang = current_lang

                    if not question.strip():
                        continue

                    # Gestione comandi debug
                    if question.strip().lower() in ['esci', 'exit', 'quit', 'q']:
                        raise KeyboardInterrupt
                    if question.strip().lower() == 'stop':
                        _stop_event.set()
                        continue
                    if question.strip().lower() == 'storia' and config.DEBUG_MODE:
                        print("\n" + "=" * 70)
                        print(agent.get_conversation_history() or "(nessun messaggio)")
                        print("=" * 70 + "\n")
                        continue
                    if question.strip().lower() == 'debug' and config.DEBUG_MODE:
                        print(f"\n[Debug] Messaggi: {agent.get_message_count()} | Lingua: {current_lang} | Model: {config.MODEL}\n")
                        continue

                    # Lingua non supportata
                    if detected_lang not in config.SUPPORTED_LANGUAGES:
                        resp_lang = current_lang or config.DEFAULT_LANG
                        err = config.UNSUPPORTED_LANG_MESSAGES.get(resp_lang, config.UNSUPPORTED_LANG_MESSAGES["it"])
                        print(f"  COMANDANTE: {err}")
                        if config.USE_TTS:
                            tts_speak(err, resp_lang, ambient_manager=ambient)
                        continue

                    # Traccia cambio lingua
                    if detected_lang != current_lang:
                        print(f"\n[Turista] Cambio lingua: {current_lang.upper()} → {detected_lang.upper()}")
                        current_lang = detected_lang

                    logger.debug(f"[Session #{_session_count}] Turista: \"{question[:50]}\"")
                    print(f"\n[Turista] {question}")

                    # Goodbye keyword → farewell immediato
                    if is_goodbye(question, current_lang):
                        farewell = config.FAREWELL_MESSAGES.get(current_lang, config.FAREWELL_MESSAGES["it"])
                        print(f"\n  COMANDANTE: {farewell}")
                        if config.USE_TTS:
                            tts_speak(farewell, current_lang, ambient_manager=ambient)
                        is_goodbye_triggered = True
                        session_end_reason = "goodbye"
                        _stop_event.set()
                        continue

                    # ── PIPELINE STREAMING ─────────────────────────────────────
                    lang = current_lang or config.DEFAULT_LANG
                    t_question = time.time()

                    # Sospendi idle timer per tutta la durata della pipeline
                    # (API + TTS possono durare più di IDLE_TIMEOUT_SECONDS)
                    _cancel_idle_timer()

                    if ambient:
                        ambient.duck()

                    try:
                        # Pipeline LLM → ElevenLabs → sounddevice:
                        # play_response_stream accumula frasi LLM (>=60 chars) e chiama
                        # ElevenLabs per ogni batch. Un unico stream sounddevice rimane
                        # aperto per tutta la risposta → nessuna pausa tra batch,
                        # primo audio in ~2-3s dalla prima frase LLM.
                        first_chunk = [True]

                        def _lm_sentences():
                            for sentence in agent.chat_stream(question, lang=lang):
                                if _stop_event.is_set():
                                    return
                                if first_chunk[0]:
                                    elapsed = time.time() - t_question
                                    print(f"\n[TIMING] Prima frase LLM in {elapsed:.2f}s")
                                    logger.info(f"[Session #{_session_count}] Primo chunk LLM in {elapsed:.2f}s")
                                    first_chunk[0] = False
                                print(f"  COMANDANTE: {sentence}")
                                yield sentence

                        if config.USE_TTS:
                            play_response_stream(_lm_sentences(), lang)
                        else:
                            for _ in _lm_sentences():
                                pass

                        elapsed_total = time.time() - t_question
                        print(f"[TIMING] Risposta completa in {elapsed_total:.2f}s\n")
                        logger.info(f"[Session #{_session_count}] Risposta in {elapsed_total:.2f}s (turno {agent.turn_count})")

                    except Exception as e:
                        _err_lang = current_lang or config.DEFAULT_LANG

                        if isinstance(e, httpx.TimeoutException):
                            logger.error(f"[API] Timeout ({config.API_TIMEOUT_SECONDS}s): {e}")
                            msg = config.NETWORK_ERROR_MESSAGES.get(_err_lang, config.NETWORK_ERROR_MESSAGES["it"])
                            print(f"\n  COMANDANTE: {msg}\n")
                            if config.USE_TTS:
                                tts_speak(msg, lang=_err_lang, ambient_manager=ambient)
                            is_goodbye_triggered = True
                            session_end_reason = "network_error"
                            _stop_event.set()
                        else:
                            logger.error(f"[API] Errore pipeline: {e}", exc_info=True)
                            if config.DEBUG_MODE:
                                traceback.print_exc()
                            msg = config.RETRY_FAIL_MESSAGES.get(_err_lang, config.RETRY_FAIL_MESSAGES["it"])
                            print(f"\n  COMANDANTE: {msg}\n")
                            if config.USE_TTS:
                                tts_speak(msg, lang=_err_lang, ambient_manager=ambient)

                    finally:
                        if ambient:
                            ambient.unduck()

                    # Auto-chiusura se risposta di chiusura V4 (turno ≥3), altrimenti reset idle timer
                    if not _stop_event.is_set():
                        if agent.turn_count >= 3:
                            logger.info(f"[Session #{_session_count}] Chiusura auto dopo risposta chiusura (turno {agent.turn_count})")
                            is_goodbye_triggered = True
                            _stop_event.set()
                        else:
                            _reset_idle_timer()

                    print()
                    print("-" * 70)
                    print()

                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"\n[ERRORE] {e}")
                    if config.DEBUG_MODE:
                        traceback.print_exc()
                    if current_lang:
                        try:
                            if config.USE_TTS:
                                tts_speak(
                                    config.ERROR_MESSAGES.get(current_lang, config.ERROR_MESSAGES["it"]),
                                    current_lang, ambient_manager=ambient
                                )
                        except Exception:
                            pass
                    continue

            # ── SESSIONE CHIUSA ────────────────────────────────────────────────
            _cancel_idle_timer()
            _cancel_person_exit_timer()

            session_duration = time.time() - session_start_time
            logger.info(
                f"[Session #{_session_count}] Chiusa — motivo: {session_end_reason}, "
                f"durata: {session_duration:.0f}s, turni: {agent.turn_count}"
            )
            print("\n[Sistema] Sessione chiusa.")

            # Farewell (se non già detto)
            if not is_goodbye_triggered:
                farewell_lang = current_lang or config.DEFAULT_LANG
                farewell = config.FAREWELL_MESSAGES.get(farewell_lang, config.FAREWELL_MESSAGES["it"])
                print(f"\n  COMANDANTE: {farewell}\n")
                if config.USE_TTS:
                    cached = _pregenerated_audio.get(f"farewell_{farewell_lang}")
                    if cached:
                        if ambient:
                            ambient.duck()
                        try:
                            play_audio(cached)
                        finally:
                            if ambient:
                                ambient.unduck()
                    else:
                        tts_speak(farewell, lang=farewell_lang, ambient_manager=ambient)

            # Reset memoria per prossimo turista
            agent.reset_conversation()
            print("[Sistema] Memoria resettata. Pronto per il prossimo turista.\n")

            # In modalità tastiera l'ambient è sempre attivo tra sessioni (reset_conversation() lo ferma)
            if ambient and not getattr(config, 'USE_PERSON_DETECTION', False):
                ambient.start()

            # Cooldown per evitare ripartenza immediata
            time.sleep(config.GOODBYE_COOLDOWN_SECONDS)

    except KeyboardInterrupt:
        _exit_event.set()
        _cancel_idle_timer()
        print("\n\n[Sistema] Spegnimento...")

        if detector is not None:
            detector.stop()
        if ambient:
            ambient.stop()

        lang = config.DEFAULT_LANG
        shutdown_msg = config.SHUTDOWN_MESSAGE.get(lang, config.SHUTDOWN_MESSAGE["it"])
        print(f"  COMANDANTE: {shutdown_msg}")
        if config.USE_TTS:
            tts_speak(shutdown_msg, lang, ambient_manager=ambient)

        logger.info("Sistema spento")

    print("\nGrazie per aver visitato il museo!\n")


if __name__ == "__main__":
    main()
