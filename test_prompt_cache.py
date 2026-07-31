"""
Test isolato per il prompt caching Anthropic — NON tocca agent.py/main.py.

Simula una sessione da 3 turni (risposta 2, 3, 4) usando il prompt
sperimentale prompts/it/comandante_v4_cache.py, chiamando l'API Anthropic
direttamente. Stampa per ogni turno:
  - i token letti dalla cache (cache_read_input_tokens) vs scritti
    (cache_creation_input_tokens) vs normali (input_tokens)
  - il testo della risposta, per controllo a occhio della coerenza
    (in particolare il turno 4 deve chiudere con "Arrivederci" e senza
    lasciare domande in sospeso)

Uso:
    python test_prompt_cache.py
Lancialo due volte di seguito entro 5 minuti per vedere la cache "calda"
(seconda esecuzione: cache_read_input_tokens > 0 fin dal turno 2).
"""
import time
import anthropic

import config
from prompts.it.comandante_v4_cache import SYSTEM_PROMPT_PREFIX, SYSTEM_PROMPT_SUFFIX

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

MAX_TOKENS_PER_RESPONSE = {2: 250, 3: 380, 4: 250}

# Simula una conversazione realistica: welcome iniettato + 3 turni utente
messages = [
    {"role": "user", "content": "[Il turista si avvicina]"},
    {"role": "assistant", "content": "Fermo lì forestiero! Chi si avvicina al mio accampamento? Io sono Marco Aurelio Maximus, comandante della dodicesima legione. Dimmi il tuo nome."},
]

user_turns = [
    "Ciao, mi chiamo Giorgio.",
    "Com'era la vita di tutti i giorni in una legione romana?",
    "Grazie mille, è stato davvero interessante parlare con te!",
]


def build_system(response_number: int):
    return [
        {"type": "text", "text": SYSTEM_PROMPT_PREFIX, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Stai dando la risposta numero: {response_number}\n\nSegui le istruzioni per il numero di risposta indicato sopra:"},
        {"type": "text", "text": SYSTEM_PROMPT_SUFFIX, "cache_control": {"type": "ephemeral"}},
    ]


def run_turn(turn_idx: int, user_text: str):
    response_number = turn_idx + 2  # turno 0 -> risposta 2, turno 1 -> risposta 3, turno 2+ -> risposta 4
    response_number = min(response_number, 4)

    messages.append({"role": "user", "content": user_text})

    t0 = time.time()
    resp = client.messages.create(
        model=config.MODEL,
        system=build_system(response_number),
        messages=messages,
        max_tokens=MAX_TOKENS_PER_RESPONSE.get(response_number, 250),
        thinking={"type": "disabled"},
    )
    elapsed = time.time() - t0

    text = "".join(block.text for block in resp.content if block.type == "text")
    messages.append({"role": "assistant", "content": text})

    u = resp.usage
    print(f"\n{'='*70}")
    print(f"TURNO {turn_idx+1} — risposta numero {response_number} — {elapsed:.2f}s")
    print(f"{'='*70}")
    print(f"[USAGE] input={u.input_tokens}  cache_creation={getattr(u, 'cache_creation_input_tokens', 0)}  "
          f"cache_read={getattr(u, 'cache_read_input_tokens', 0)}  output={u.output_tokens}")
    print(f"[TESTO] {text}")

    return response_number, text


if __name__ == "__main__":
    print(f"Modello: {config.MODEL}")
    print("Lancia questo script due volte di seguito (entro 5 min) per vedere l'effetto della cache.\n")

    last_number, last_text = None, None
    for i, turn_text in enumerate(user_turns):
        last_number, last_text = run_turn(i, turn_text)

    print(f"\n{'='*70}")
    print("CONTROLLO AUTOMATICO SUL TURNO FINALE (risposta 4 — deve chiudere)")
    print(f"{'='*70}")
    has_farewell = "arrivederci" in last_text.lower()
    ends_with_question = last_text.strip().endswith("?")
    print(f"Contiene 'Arrivederci': {has_farewell}")
    print(f"Finisce con un punto interrogativo (NON dovrebbe): {ends_with_question}")
    if has_farewell and not ends_with_question:
        print("OK — la risposta 4 chiude correttamente senza lasciare domande in sospeso.")
    else:
        print("ATTENZIONE — la risposta 4 non sembra chiudere come previsto, controllare il testo sopra.")
