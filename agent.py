"""
Agente AI che impersona Marco Aurelio Maximus.
Nessuna cache, nessun RAG: chiamata diretta all'API Anthropic con streaming.
"""
import anthropic
import httpx
import logging
import config
from conversation import ConversationManager

# --- PROMPT ORIGINALE (senza cache) --- tenuto per riferimento/rollback ---
# from prompts.it.comandante_v4 import SYSTEM_PROMPT_TEMPLATE as SYSTEM_PROMPT_IT
# from prompts.en.commander_v4 import SYSTEM_PROMPT_TEMPLATE as SYSTEM_PROMPT_EN
#
# PROMPTS = {
#     "it": SYSTEM_PROMPT_IT,
#     "en": SYSTEM_PROMPT_EN,
# }

# --- PROMPT CON CACHE (attivo) ---
from prompts.it.comandante_v4_cache import SYSTEM_PROMPT_PREFIX as PREFIX_IT, SYSTEM_PROMPT_SUFFIX as SUFFIX_IT
from prompts.en.commander_v4_cache import SYSTEM_PROMPT_PREFIX as PREFIX_EN, SYSTEM_PROMPT_SUFFIX as SUFFIX_EN

CACHE_PROMPTS = {
    "it": (PREFIX_IT, SUFFIX_IT, "Stai dando la risposta numero: {n}\n\nSegui le istruzioni per il numero di risposta indicato sopra:"),
    "en": (PREFIX_EN, SUFFIX_EN, "You are currently giving response number: {n}\n\nFollow the instructions for the response number indicated above:"),
}

if config.USE_AMBIENT_SOUNDS:
    from audio_manager import AmbientSoundManager

logger = logging.getLogger(__name__)


class CharacterAgent:

    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(config.API_TIMEOUT_SECONDS, connect=5.0),
        )
        # self.prompts = PROMPTS  # vecchio prompt senza cache, vedi import in alto
        self.prompts = CACHE_PROMPTS
        self.model = config.MODEL
        self.temperature = config.TEMPERATURE
        self.conversation = ConversationManager()

        self.turn_count = 0
        self.conversation_start_time = None

        self.ambient = AmbientSoundManager(
            volume=config.AMBIENT_VOLUME,
            duck_ratio=config.DUCK_RATIO,
        ) if config.USE_AMBIENT_SOUNDS else None

        if config.DEBUG_MODE:
            print(f"[Agent] Inizializzato: {self.model}")

    def _build_system(self, lang):
        """
        Costruisce il system prompt (lista di blocchi con cache_control) e il
        max_tokens effettivo per il turno corrente.
        Ritorna (system_blocks, effective_max_tokens).
        """
        import time

        if self.conversation_start_time is None:
            self.conversation_start_time = time.time()
        self.turn_count += 1

        # welcome = risposta 1, turn 1 → risposta 2, turn 2 → risposta 3, turn 3+ → risposta 4
        response_number = min(self.turn_count + 1, 4)

        # --- VECCHIO: prompt come stringa unica, niente cache --- tenuto per rollback ---
        # base_prompt = self.prompts.get(lang, self.prompts["it"])
        # placeholder = "{{NUMERO_RISPOSTA}}" if lang == "it" else "{{RESPONSE_NUMBER}}"
        # system = base_prompt.replace(placeholder, str(response_number))

        # --- NUOVO: prompt a blocchi con cache_control ---
        prefix, suffix, dynamic_template = self.prompts.get(lang, self.prompts["it"])
        system = [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_template.format(n=response_number)},
            {"type": "text", "text": suffix, "cache_control": {"type": "ephemeral"}},
        ]

        if response_number == 2:
            effective_max_tokens = 250
        elif response_number == 3:
            effective_max_tokens = 380
        else:
            effective_max_tokens = 250

        if config.DEBUG_MODE:
            labels = {2: "riconoscimento", 3: "racconto", 4: "chiusura"}
            print(f"[V4] Risposta {response_number} ({labels.get(response_number, 'chiusura')}), max_tokens={effective_max_tokens}")

        return system, effective_max_tokens

    def inject_welcome_message(self, welcome_text, lang="it"):
        """Inietta il welcome nella cronologia come coppia sintetica user+assistant."""
        marker = "[Il turista si avvicina]" if lang == "it" else "[A tourist approaches]"
        self.conversation.add_user_message(marker)
        self.conversation.add_assistant_message(welcome_text)

    def chat_stream(self, user_message, lang="it"):
        """
        Streaming: yield di frasi complete man mano che arrivano dall'API.
        Retry automatico su overload API (fino a 3 tentativi).

        Yields:
            str — frasi terminate da . ! ? o fine stream
        Raises:
            Exception — se tutti i retry falliscono o errore non-overload
        """
        import time

        system, effective_max_tokens = self._build_system(lang)

        self.conversation.add_user_message(user_message)
        api_messages = self.conversation.get_history()

        _RETRY_DELAYS = [1, 2, 3]
        t_start = time.time()

        for _attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                full_response = ""
                sentence_buffer = ""
                chunk_count = 0
                first_token_time = None

                print(f"[STREAM] Chiamata API{f' (tentativo {_attempt+1})' if _attempt > 0 else ''}...")

                with self.client.messages.stream(
                    model=self.model,
                    system=system,
                    messages=api_messages,
                    max_tokens=effective_max_tokens,
                    thinking={"type": "disabled"},
                ) as stream:
                    for text in stream.text_stream:
                        if first_token_time is None:
                            first_token_time = time.time()
                            print(f"[STREAM] Primo token in {first_token_time - t_start:.2f}s")

                        full_response += text
                        sentence_buffer += text

                        while True:
                            best_pos = -1
                            for sep in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                                pos = sentence_buffer.find(sep)
                                # salta i punti delle ellissi (...)
                                while pos != -1 and sep[0] == '.' and pos >= 1 and sentence_buffer[pos - 1] == '.':
                                    pos = sentence_buffer.find(sep, pos + 1)
                                if pos != -1 and (best_pos == -1 or pos < best_pos):
                                    best_pos = pos + len(sep) - 1

                            if best_pos == -1:
                                break

                            sentence = sentence_buffer[:best_pos + 1].strip()
                            sentence_buffer = sentence_buffer[best_pos + 1:].lstrip()

                            if sentence:
                                chunk_count += 1
                                yield sentence

                # testo rimanente nel buffer
                remaining = sentence_buffer.strip()
                if remaining:
                    chunk_count += 1
                    yield remaining

                t_end = time.time()
                print(f"[STREAM] Completato: {chunk_count} chunk in {t_end - t_start:.2f}s")
                logger.info(f"[STREAM] {t_end - t_start:.2f}s, {chunk_count} chunk, turno {self.turn_count}")

                self.conversation.add_assistant_message(full_response)
                break  # successo

            except Exception as e:
                _is_overload = 'overloaded' in str(e).lower() or getattr(e, 'status_code', None) == 529
                _can_retry = _is_overload and full_response == "" and _attempt < len(_RETRY_DELAYS)

                if _can_retry:
                    _wait = _RETRY_DELAYS[_attempt]
                    print(f"[STREAM] API sovraccarica, riprovo in {_wait}s...")
                    time.sleep(_wait)
                    continue
                else:
                    self.conversation.remove_last_message()
                    logger.error(f"[API] Errore: {e}", exc_info=True)
                    raise

    def reset_conversation(self):
        """Resetta memoria per nuovo turista."""
        self.conversation.reset()
        self.turn_count = 0
        self.conversation_start_time = None
        if self.ambient:
            self.ambient.stop()
        if config.DEBUG_MODE:
            print("[Agent] Conversazione resettata")

    def get_message_count(self):
        return len(self.conversation)

    def get_conversation_history(self):
        return self.conversation.export_conversation()

    def __repr__(self):
        return f"CharacterAgent(model={self.model}, messages={len(self.conversation)}, temp={self.temperature})"
