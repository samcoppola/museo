# Museo Gladiatori — Marco Aurelio Maximus

Sistema di intelligenza artificiale conversazionale per museo.
Il visitatore interagisce a voce con un comandante romano del II secolo d.C.

---

## Indice

1. [Come funziona](#come-funziona)
2. [Architettura a blocchi](#architettura-a-blocchi)
3. [Flusso di interazione fisica](#flusso-di-interazione-fisica-telecamera--trigger--microfono)
4. [Flusso conversazione a livello di chunk](#flusso-conversazione-a-livello-di-chunk)
5. [Modelli usati: quali, parametri, perché](#modelli-usati-quali-parametri-perché)
6. [Cosa gira dove: CPU/GPU, locale/cloud, consumo risorse](#cosa-gira-dove-cpugpu-localecloud-consumo-risorse)
7. [Struttura del progetto — file per file](#struttura-del-progetto--file-per-file)
8. [Configurazione — guida completa](#configurazione--guida-completa)
9. [Fallback e troubleshooting in produzione](#fallback-e-troubleshooting-in-produzione)
10. [Requisiti hardware](#requisiti-hardware)
11. [Setup su Windows](#setup-su-windows-guida-completa)
12. [Configurazione Unity (lip-sync)](#configurazione-unity-lip-sync)
13. [Perché native Windows](#perché-native-windows-no-docker-no-wsl2)
14. [Crediti API (costi)](#crediti-api-costi)

---

## Come funziona

1. La telecamera rileva l'arrivo di una persona
2. Il sistema aspetta la frase trigger: **"Salve Comandante"** (IT) o **"Hail Commander"** (EN)
3. Il comandante dà il benvenuto nella lingua rilevata
4. Il visitatore parla liberamente — il sistema ascolta automaticamente (no pulsante)
5. La sessione si chiude se:
   - Il visitatore se ne va (rilevato dalla telecamera)
   - **12 secondi** di silenzio dopo l'ultima risposta (`config.py:81`, `IDLE_TIMEOUT_SECONDS`)
   - Il visitatore dice "arrivederci" / "goodbye" / "basta" (`config.py:52-55`, `END_KEYWORDS`)
   - Si raggiunge il turno 3 (chiusura naturale della conversazione, vedi §5)
6. Dopo 3 secondi (`GOODBYE_COOLDOWN_SECONDS`) il sistema è pronto per il prossimo visitatore

---

## Architettura a blocchi

```
┌─────────────────────────── PC LOCALE (museo) ────────────────────────────┐
│                                                                            │
│  Webcam ──► person_detector.py (YOLO11n, GPU) ──► main.py (orchestratore)│
│                                                          │                │
│  Microfono ──► stt.py (Silero VAD, CPU + Whisper large-v3, GPU) ──►      │
│                                                          │                │
│                                                          ▼                │
│                                              agent.py (client Anthropic) ─┼──► CLOUD: api.anthropic.com
│                                                          │                │        (Claude Sonnet 5)
│                                                          ▼                │◄──┘ testo in streaming (frasi)
│                                              tts.py (client ElevenLabs) ──┼──► CLOUD: api.elevenlabs.io
│                                                          │                │        (eleven_flash_v2_5)
│                                                          ▼                │◄──┘ audio PCM in streaming
│                                          sounddevice / pygame (playback) │
│                                                          │                │
│                                                          ▼                │
│                                    Casse fisiche  o  VB-Audio → Unity     │
│                                    (lip-sync, se in uso)                  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────┘
```

**Cosa resta sul PC locale (mai esce in rete):** video della webcam (elaborato localmente da YOLO, mai caricato da nessuna parte), audio grezzo del microfono fino alla trascrizione (Whisper gira in locale), file audio ambientale, i due modelli STT/detection scaricati su disco (`models/`).

**Cosa esce verso il cloud e torna:**
- **Testo** della domanda trascritta → Anthropic (mai audio: la trascrizione avviene sempre in locale prima)
- **Testo** della risposta di Claude → ElevenLabs (per la sintesi vocale)
- **Audio PCM** della voce sintetizzata ← ElevenLabs (torna in streaming, viene riprodotto man mano che arriva)

Non c'è nessun altro traffico di rete lato conversazione (niente telemetria esterna, niente analytics di terze parti).

---

## Flusso di interazione fisica (telecamera → trigger → microfono)

```
1. PersonDetector (thread separato, person_detector.py) legge frame dalla
   webcam in loop continuo, gira inferenza YOLO ad ogni frame.
     └─ Debounce: serve che la persona sia rilevata in modo stabile per
        PERSON_DETECTION_PERSISTENT_FRAMES frame consecutivi (config.py:124)
        prima di scattare l'evento "persona entrata".

2. Evento "persona entrata" (_on_person_enter, main.py:269) → avvia un
   secondo thread che ascolta il microfono in loop (listen_for_trigger,
   stt.py:276) aspettando la frase "Salve Comandante" / "Hail Commander".
     └─ Ogni ciclo: registra con Silero VAD finché non rileva silenzio,
        trascrive con Whisper, confronta il testo normalizzato con le
        frasi trigger (config.py:143-144). Se non riconosciuta, ricomincia.

3. Frase trigger riconosciuta → sessione ATTIVA. Il thread trigger si ferma,
   main.py prende il controllo del microfono per il loop di conversazione.

4. Ad ogni turno: record_audio_with_vad (stt.py:70) registra finché il VAD
   non rileva VAD_SILENCE_MS (2000ms, config.py:135) di silenzio consecutivo
   dopo l'inizio del parlato — poi passa tutto a Whisper per la trascrizione.

5. In parallelo, PersonDetector continua a girare per tutta la sessione:
   se rileva "persona uscita" stabile (stesso debounce del punto 1, ma
   simmetrico) → chiude la sessione dopo PERSON_DETECTION_EXIT_DELAY secondi
   (config.py:127).
```

---

## Flusso conversazione a livello di chunk

Il sistema **non aspetta mai la risposta completa** prima di iniziare a
parlare — tutto è pipeline in streaming, a più livelli annidati:

```
Claude (Anthropic)                    tts.py                    Altoparlante
───────────────────                   ──────                    ────────────
stream token per token   ──►  agent.py accumula i token in
                               un buffer e li spezza in FRASI
                               complete (cerca ". ", "! ", "? ",
                               agent.py:129-147) — yield di una
                               frase alla volta, non prima

                                        │
                                        ▼
                          play_response_stream (tts.py:251):
                          un thread in background consuma le
                          frasi da agent.py e le accumula FINCHÉ
                          non raggiungono MIN_CHARS=120 caratteri
                          (tts.py:275) — poi chiama ElevenLabs
                          in streaming per quel blocco

                                        │
                                        ▼
                                                          ElevenLabs risponde
                                                          in streaming PCM
                                                          (chunk audio grezzi,
                                                          non un file intero)
                                        │
                                        ▼
                          I chunk PCM finiscono in una coda
                          (queue.Queue). Il thread principale
                          pre-bufferizza 0.5s di audio, poi apre
                          UNO STREAM sounddevice che resta aperto
                          per tutta la risposta e scrive i chunk
                          man mano che arrivano — nessuna pausa
                          udibile tra un blocco ElevenLabs e il
                          successivo (tts.py:305-327)
```

**Perché questa architettura**: primo audio udibile in ~2-3 secondi dalla
prima frase generata da Claude, invece di aspettare la risposta intera
(che con `max_tokens` alti può richiedere diversi secondi) più il tempo di
generazione TTS del testo completo. Il prezzo pagato: la prosodia di
ElevenLabs è calcolata a blocchi di ~120 caratteri, non sull'intera
risposta, quindi l'intonazione tra un blocco e l'altro è leggermente meno
fluida di quanto sarebbe generando tutto insieme.

**Messaggi fissi (welcome, farewell, errori)** non passano da questa
pipeline: usano `tts.py:speak()` (non-streaming, `tts.py:335`), che genera
l'intero audio prima di riprodurlo. Più lenti da avviare ma più semplici, e
per i messaggi pre-generabili (farewell) il costo è comunque nullo perché
vengono creati una sola volta all'avvio e cacheati su disco
(`data/audio_cache/`, vedi `main.py:192-240`).

---

## Modelli usati: quali, parametri, perché

| Componente | Modello | Dove gira | Parametri chiave | Perché questa scelta |
|---|---|---|---|---|
| **LLM conversazione** | `claude-sonnet-5` (`config.py:15`) | Cloud (Anthropic) | `max_tokens` variabile per turno (250/380/250, `agent.py:67-72`); `thinking={"type": "disabled"}`; niente `temperature` (il modello la rifiuta, vedi §9) | Miglior compromesso qualità/velocità disponibile per roleplay in italiano con streaming a bassa latenza. Il modello precedente (`claude-sonnet-4-20250514`) è stato ritirato dall'API (404) |
| **STT (trascrizione)** | `faster-whisper` `whisper-large-v3` (`config.py:131`) | Locale, GPU (`WHISPER_DEVICE="cuda"`, `config.py:132`) | `compute_type="float16"` su GPU, `beam_size=1` (`stt.py:190`) | Modello Whisper più accurato disponibile, necessario per l'italiano parlato in ambiente rumoroso (museo); `beam_size=1` sacrifica un po' di accuratezza per velocità (non serve ricerca esaustiva per frasi brevi conversazionali) |
| **VAD (rilevamento voce)** | Silero VAD (`stt.py:14`) | Locale, **CPU** (nessun `.to('cuda')`, modello volutamente leggero) | `VAD_START_THRESHOLD=0.6`, `VAD_STOP_THRESHOLD=0.25`, `VAD_SILENCE_MS=2000` (`config.py:137-138,135`) | Serve solo a decidere quando iniziare/fermare la registrazione — troppo leggero per giustificare la GPU, gira in tempo reale su chunk da 32ms |
| **Rilevamento persona** | YOLO11n (`config.py:126`, file `models/yolo11n.pt`) | Locale, GPU (auto-selezionata da ultralytics se disponibile — nessun `device=` esplicito in `person_detector.py`) | `conf=0.5` (`config.py:123`), resize a 640px (`config.py:125`) | Versione "nano" di YOLO11: la più leggera della famiglia, sufficiente per rilevare la sola classe "persona" (COCO class 0) senza appesantire la GPU già occupata da Whisper |
| **TTS (sintesi vocale)** | ElevenLabs `eleven_flash_v2_5` (`config.py:105`) | Cloud (ElevenLabs) | `speed=0.85`, `stability=0.70`, `similarity_boost=0.90` (`config.py:107-109`), streaming PCM 44100Hz | Modello **flash**, non `multilingual_v2`: pensato apposta per bassa latenza in conversazioni real-time (a fronte di una qualità vocale leggermente inferiore al modello di punta) |

### Parametri esatti passati a ogni modello

Utile per capire cosa si può tarare e dove — questi sono i parametri reali
passati nelle chiamate, non solo quelli esposti in `config.py`:

| Chiamata | File:riga | Parametri passati |
|---|---|---|
| Anthropic `messages.stream` | `agent.py:114-119` | `model`, `system` (prompt ricostruito per turno), `messages` (storico), `max_tokens=effective_max_tokens`, `thinking={"type": "disabled"}`. Nessun `temperature`/`top_p`/`top_k` (default API) |
| ElevenLabs streaming (`_elevenlabs_pcm_stream`, risposte conversazionali) | `tts.py:230-248` | `voice_id`, `text`, `model_id=config.ELEVEN_MODEL_ID`, `output_format="pcm_44100"`, `language_code=lang`, `voice_settings` (speed/stability/similarity_boost da config), `apply_text_normalization="on"` |
| ElevenLabs statico (`_generate_elevenlabs`, welcome/farewell/errori pre-generabili) | `tts.py:105-162` | Come sopra ma `output_format="mp3_44100_128"`, nessun `apply_text_normalization` |
| Whisper trascrizione | `stt.py:188-192` | `beam_size=1`, `language=None` (auto-detect), nessuna soglia di confidenza applicata (vedi §9.3) |
| Whisper caricamento modello | `stt.py:46-50` | `compute_type="float16"` su GPU, `"int8"` su CPU |
| YOLO inferenza | `person_detector.py` (`model.predict`) | `conf=self.conf` (da `PERSON_DETECTION_CONF`), `verbose=False`, nessun `device=` esplicito (auto-seleziona GPU se disponibile) |
| Silero VAD | `stt.py` (chiamata `vad(x, SAMPLE_RATE)`) | Nessun parametro di chiamata — le soglie `VAD_START_THRESHOLD`/`VAD_STOP_THRESHOLD` sono applicate *dopo*, confrontando la probabilità ritornata, non passate al modello |

### Rimosso: `MAX_TOKENS` in `config.py`

**Cosa è stato tolto**: la riga `MAX_TOKENS = 130` che stava in `config.py`,
sezione `# --- MODELLO ---` (prima si trovava a riga 16, insieme a `MODEL`
e `TEMPERATURE`), e la riga corrispondente `self.max_tokens =
config.MAX_TOKENS` in `agent.py:33`.

**Perché**: non veniva mai letto da nessuna chiamata reale all'API —
`agent.py` calcola `effective_max_tokens` per conto suo ad ogni turno
(`agent.py:66-71`), quindi quel valore era solo fuorviante (dava
l'impressione di controllare la lunghezza delle risposte, ma non faceva
nulla). **Chi ha una copia del progetto sul PC del museo deve applicare la
stessa rimozione a mano** se vuole restare allineato — non è un problema
se non lo fa (il codice funziona comunque, quel valore era già inerte).

### Da fare in produzione: alzare il cap `effective_max_tokens`

**Il problema**: `agent.py:66-71` ha limiti fissi per tipo di risposta
(attualmente 250/380/250 token). Se il modello supera il limite mentre
genera, l'API taglia la risposta a metà frase e il codice la pronuncia
comunque così com'è tagliata (nessun controllo su `stop_reason`, vedi
§9.2). È il bug di troncamento già osservato in test.

**Perché serve comunque un cap** (non toglierlo del tutto): senza limite,
un modello che "si dilunga" per qualche motivo genererebbe risposte più
lunghe, più lente e più costose — il cap è una rete di sicurezza. Il
vincolo di lunghezza "vero" dovrebbe venire dalle istruzioni nel system
prompt (*"massimo 3-4 frasi"*, vedi
`prompts/it/comandante_v4.py:36-38,48,56,62`), non dal taglio secco sui
token — quindi il cap va tenuto ma **abbastanza alto da non scattare mai
in condizioni normali**.

**Modifica da applicare a mano**, in `agent.py`, righe 66-71:

```python
# PRIMA (valori attuali)
if response_number == 2:
    effective_max_tokens = 250
elif response_number == 3:
    effective_max_tokens = 380
else:
    effective_max_tokens = 250

# DOPO (margine più ampio, consigliato)
if response_number == 2:
    effective_max_tokens = 350
elif response_number == 3:
    effective_max_tokens = 500
else:
    effective_max_tokens = 350
```

Facoltativo ma consigliato, per accorgersi se anche questi valori più alti
non bastassero: aggiungere un log quando la risposta viene comunque
troncata, subito dopo la chiamata `self.client.messages.stream(...)` in
`agent.py:114-119` — dentro il blocco `with ... as stream:`, dopo il ciclo
`for text in stream.text_stream:`, controllare
`stream.get_final_message().stop_reason` e loggare un warning se vale
`"max_tokens"`.

### Da fare in produzione: il fallback `RETRY_FAIL_MESSAGES` non scatta mai

Vedi sopra, "Messaggi di fallback: quando scattano davvero" — il messaggio
di scuse `RETRY_FAIL_MESSAGES` (`config.py`) esiste ed è cablato in
`main.py:520`, ma il codice che dovrebbe farlo scattare non viene quasi
mai raggiunto, perché `play_response_stream()` (`tts.py:251`) inghiotte
ogni eccezione internamente e non la rilancia mai.

**Modifica da applicare a mano**, in `tts.py`, dentro `play_response_stream`:

```python
# PRIMA — tts.py:277-300, la funzione _tts_thread interna
def _tts_thread():
    pending = ""
    try:
        for sentence in sentence_gen:
            ...
    except Exception as e:
        logger.error(f"[TTS] Errore tts_thread: {e}")
    finally:
        audio_q.put(_DONE)

# DOPO — cattura l'eccezione invece di limitarsi a loggarla
def _tts_thread():
    pending = ""
    try:
        for sentence in sentence_gen:
            ...
    except Exception as e:
        logger.error(f"[TTS] Errore tts_thread: {e}")
        _error_holder[0] = e          # <-- nuovo
    finally:
        audio_q.put(_DONE)
```

Poi, subito prima di `def _tts_thread():`, aggiungere
`_error_holder = [None]`, e alla fine di `play_response_stream` (dopo
`tts_t.join()`, ultima riga della funzione), aggiungere:

```python
if _error_holder[0] is not None:
    raise _error_holder[0]
```

Questo fa risalire l'errore fino a `main.py`, dove il blocco
`except Exception` che già esiste attorno alla chiamata a
`play_response_stream` (`main.py:494`, dentro il `try` che parte a riga
473) lo intercetta e finalmente pronuncia `RETRY_FAIL_MESSAGES` invece di
lasciare la sessione muta. **Da testare con attenzione**: questo cambia il
comportamento anche per gli errori di solo-TTS (non LLM) che oggi vengono
ignorati silenziosamente — verificare che non diventi troppo "chiacchierone"
sugli errori minori di rete verso ElevenLabs.

---

## Cosa gira dove: CPU/GPU, locale/cloud, consumo risorse

| Componente | Locale/Cloud | CPU/GPU | Consumo indicativo |
|---|---|---|---|
| Whisper large-v3 | Locale | GPU | ~3 GB VRAM (float16) + qualche % di GPU compute durante la trascrizione (pochi secondi per turno) |
| Silero VAD | Locale | CPU | Trascurabile — piccoli chunk da 32ms elaborati in continuo durante la registrazione |
| YOLO11n | Locale | GPU | Poche centinaia di MB VRAM, inferenza continua per tutta la sessione (loop while-true su ogni frame webcam) — **gira in parallelo a Whisper sulla stessa GPU**, possibile contesa di risorse (vedi §9) |
| pygame (audio ambientale + fallback) | Locale | CPU | Trascurabile |
| sounddevice (playback TTS) | Locale | CPU | Trascurabile |
| Claude Sonnet 5 | Cloud (Anthropic) | — (nessun carico locale) | Rete: richiesta piccola (system prompt + storico), risposta in streaming |
| ElevenLabs `eleven_flash_v2_5` | Cloud | — (nessun carico locale) | Rete: testo piccolo in, audio PCM streaming in ritorno (44.1kHz mono 16-bit ≈ 86 KB/s di banda) |

**VRAM totale attesa**: Whisper (~3GB) + YOLO (poche centinaia di MB) + overhead contesto CUDA di entrambi i framework (PyTorch per Whisper, PyTorch/ultralytics per YOLO) — coerente con il requisito minimo di 8GB GPU indicato in §10, ma con 12+ GB si evita qualunque rischio di pressione sulla memoria.

**Banda di rete**: entrambe le chiamate cloud sono per testo breve in andata (domanda trascritta, risposta di Claude) e streaming leggero in ritorno — non richiede una connessione particolarmente veloce, ma **la latenza della connessione conta** più della banda: ogni turno fa comunque 2 round-trip verso due provider cloud diversi (vedi discussione architetturale su un possibile setup server-side, non implementato).

---

## Struttura del progetto — file per file

```
museo/
├── main.py              # punto di ingresso, orchestratore
├── config.py             # tutte le impostazioni
├── agent.py              # conversazione con Claude
├── tts.py                 # sintesi vocale (ElevenLabs)
├── stt.py                 # riconoscimento vocale (Whisper + VAD)
├── audio_manager.py       # musica ambientale
├── person_detector.py     # rilevamento presenza (YOLO + webcam)
├── conversation.py        # cronologia conversazione
├── prompts/
│   ├── it/comandante_v4.py
│   └── en/commander_v4.py
├── sounds/ambient/ambient_soundscape.mp3
├── models/whisper-large-v3/ , yolo11n.pt
├── data/audio_cache/      # farewell pre-generati
├── logs/                  # log giornalieri
└── requirements.txt
```

### `main.py` — orchestratore

Punto di ingresso (`python main.py`). Contiene il ciclo di vita completo
della sessione (vedi §3 e §4 di `PRODUCTION_NOTES.md`/`§9` qui sotto).

- Importa e inizializza: `CharacterAgent` (da `agent.py`), `PersonDetector`
  (da `person_detector.py`, se `USE_PERSON_DETECTION`), le funzioni di
  `tts.py` (`speak`, `generate_audio`, `play_audio`, `play_response_stream`)
  e `stt.py` (`listen`, `listen_for_trigger`, `preload`)
- Gestisce tre `threading.Event` globali per coordinare i thread:
  `_start_event` (trigger rilevato), `_stop_event` (chiudi sessione),
  `_exit_event` (spegni tutto)
- Gestisce due timer (`threading.Timer`): idle timeout e ritardo di uscita
  persona
- **Non contiene logica di dominio** (niente prompt, niente parsing
  audio) — chiama sempre gli altri moduli

### `agent.py` — conversazione con Claude

- Espone la classe `CharacterAgent`: un client Anthropic (`self.client`),
  la cronologia (`ConversationManager` da `conversation.py`), il conteggio
  turni (`self.turn_count`)
- `_build_system(lang)`: costruisce il system prompt sostituendo il
  placeholder `{{NUMERO_RISPOSTA}}`/`{{RESPONSE_NUMBER}}` nel template
  importato da `prompts/it/comandante_v4.py` o `prompts/en/commander_v4.py`,
  e calcola `effective_max_tokens` per il turno corrente
- `chat_stream(user_message, lang)`: chiama l'API in streaming, spezza il
  testo in frasi complete e le restituisce una alla volta (`yield`) —
  **chiamato da `main.py`**, che a sua volta passa il generatore a
  `tts.py:play_response_stream`
- Gestisce il retry automatico per errori "overloaded" (529)

### `tts.py` — sintesi vocale

- `speak(text, lang, ambient_manager)`: genera e riproduce un testo intero
  (non-streaming) — usato da `main.py` per welcome/farewell/messaggi
  d'errore
- `generate_audio(text, lang, static)`: genera audio senza riprodurlo,
  usato sia per la pipeline streaming che per la pre-generazione dei
  farewell
- `play_response_stream(sentence_gen, lang)`: la pipeline streaming
  completa (vedi §4) — **riceve direttamente il generatore di
  `agent.py:chat_stream`**, passato da `main.py`
- Nessuna dipendenza da `agent.py` o `main.py` a livello di import — riceve
  tutto come parametri, resta un modulo "a valle"

### `stt.py` — riconoscimento vocale

- `preload()`: carica Whisper e Silero VAD in memoria all'avvio (chiamato
  una volta sola da `main.py` prima del loop principale)
- `listen(stop_event, min_lang_prob, on_speech_start)`: registra con VAD e
  trascrive — usato nel loop di conversazione principale
- `listen_for_trigger(stop_event)`: loop dedicato per ascoltare la frase
  trigger, usato solo nella fase IDLE
- Nessuna dipendenza da altri moduli del progetto (usa solo `config.py`)

### `person_detector.py` — rilevamento presenza

- Classe `PersonDetector`, gira come thread daemon indipendente
  (`start()`/`stop()`)
- Riceve due callback dal chiamante (`on_person_enter`, `on_person_exit`)
  invece di importare `main.py` direttamente — **comunicazione a senso
  unico tramite callback**, non ha alcuna dipendenza dagli altri moduli di
  dominio
- `main.py` definisce le callback (`_on_person_enter`, `_on_person_exit`,
  righe 269-294) che a loro volta settano gli `Event` globali o avviano il
  trigger listener

### `audio_manager.py` — audio ambientale

- Classe `AmbientSoundManager`, istanziata dentro `CharacterAgent.__init__`
  (`agent.py:40-43`) — **non da `main.py` direttamente**, ma esposta
  tramite `agent.ambient` e riusata da `main.py` per il duck/unduck durante
  ascolto e riproduzione
- Nessuna dipendenza da altri moduli oltre `config.py`

**Dove si attiva, esattamente** (tutte le chiamate partono da `main.py`,
recuperando l'istanza con `ambient = getattr(agent, 'ambient', None)` a
riga 309):

| Metodo | Quando | File:riga |
|---|---|---|
| `start()` | Modalità senza person detection, all'avvio (`main.py:311`); a inizio di ogni sessione attiva (`main.py:376`); al ritorno in IDLE dopo una sessione, solo in modalità senza person detection (`main.py:594`) | `main.py:311,376,594` |
| `duck()` | Prima di ascoltare il microfono (`main.py:397`); prima di far parlare il comandante (`main.py:472`); prima di un messaggio d'errore (`main.py:579`) | `main.py:397,472,579` |
| `unduck()` | Simmetrico a ogni `duck()`, dopo l'ascolto/la risposta/l'errore | `main.py:403,527,584` |
| `stop()` | Solo allo spegnimento completo (Ctrl+C) | `main.py:607` |

**Parametri volume**: `AMBIENT_VOLUME=0.70` e `DUCK_RATIO=0.5`
(`config.py:89-90` — volume abbassato effettivo = 0.70×0.5 = 0.35), file
sorgente in `AMBIENT_FILE` (`config.py:88`). Le durate del fade (400ms per
il duck, 600ms per l'unduck) sono invece **hardcoded** dentro
`audio_manager.py:49,57` (parametri di default di `duck()`/`unduck()`), non
esposte in `config.py`.

### `conversation.py` — cronologia

- Classe `ConversationManager`, usata internamente da `CharacterAgent`
  (`agent.py:35`) — nessun altro modulo la tocca direttamente
- Gestisce sliding window (`MAX_HISTORY_LENGTH`) e iniezione di un
  "reminder" di personaggio ogni `REMINDER_FREQUENCY` turni (in pratica,
  con sessioni di 3-4 turni, questo reminder quasi non scatta mai — pensato
  per conversazioni più lunghe di quelle tipiche del museo)

### `prompts/it/comandante_v4.py`, `prompts/en/commander_v4.py`

- Solo dati: `SYSTEM_PROMPT_TEMPLATE`, stringa con placeholder
  `{{NUMERO_RISPOSTA}}`/`{{RESPONSE_NUMBER}}`. Nessuna logica.

### Grafo delle dipendenze (chi importa chi)

```
main.py ──► agent.py ──► conversation.py
        │            └─► audio_manager.py
        │            └─► prompts/it|en/*.py
        ├──► tts.py        (nessuna dipendenza a ritroso)
        ├──► stt.py        (nessuna dipendenza a ritroso)
        └──► person_detector.py  (comunica solo via callback)
```

`tts.py`, `stt.py` e `person_detector.py` non importano mai `main.py` né
`agent.py` — sono moduli "foglia", riutilizzabili isolatamente. Tutta
l'orchestrazione vive in `main.py`.

---

## Configurazione — guida completa

Tutto vive in `config.py`. Tabella organizzata per **"perché vorresti
cambiarlo"**, non per ordine nel file:

| Vuoi... | Parametro | File:riga | Note |
|---|---|---|---|
| Rendere il sistema più/meno paziente col silenzio | `IDLE_TIMEOUT_SECONDS` | `config.py:80` | Include l'overhead di STT (~4s) nel conteggio percepito |
| Cambiare quanto in fretta la webcam considera "uscita" una persona | `PERSON_DETECTION_PERSISTENT_FRAMES`, `PERSON_DETECTION_EXIT_DELAY` | `config.py:123,126` | Alzare se ci sono falsi positivi (vedi §9.1) |
| Regolare la sensibilità della webcam a rilevare "c'è una persona" | `PERSON_DETECTION_CONF` | `config.py:122` | Abbassare (es. 0.35) in ambienti con luce scarsa; alzare se rileva falsi positivi su manichini/statue |
| Cambiare quale webcam viene usata | `PERSON_DETECTION_CAM_INDEX` | `config.py:121` | **Può cambiare da solo** dopo un riavvio o se si scollega/ricollega un cavo USB — controllare per primo se il rilevamento smette di funzionare |
| Regolare quanto deve essere "sicuro" il microfono prima di iniziare a registrare | `VAD_START_THRESHOLD` | `config.py:136` | Alzare (0.7-0.8) in ambienti rumorosi per evitare registrazioni fantasma |
| Regolare quanto silenzio serve per considerare finita una frase | `VAD_STOP_THRESHOLD`, `VAD_SILENCE_MS` | `config.py:137,134` | Alzare `VAD_SILENCE_MS` se il sistema taglia le frasi troppo presto (persone che parlano con pause) |
| Cambiare la lunghezza massima delle risposte del comandante | `effective_max_tokens` in `agent.py:66-71` | **non in config.py** | Vedi §9.2 — attenzione al troncamento se troppo basso. **Da alzare in produzione**, vedi istruzione dedicata sotto |
| Attivare/disattivare la musica ambientale di sottofondo | `USE_AMBIENT_SOUNDS` | `config.py:86` | Se `False`, nessun audio ambientale né duck/unduck — non serve toccare altro |
| Regolare il volume della musica ambientale | `AMBIENT_VOLUME` (volume normale), `DUCK_RATIO` (quanto si abbassa mentre il comandante parla/ascolta) | `config.py:88-89` | Volume effettivo durante il parlato = `AMBIENT_VOLUME × DUCK_RATIO` (default 0.70×0.5=0.35). File audio in `AMBIENT_FILE`, `config.py:87` |
| Cambiare voce/velocità/espressività della sintesi vocale | `ELEVEN_VOICE`, `ELEVEN_SPEED`, `ELEVEN_STABILITY`, `ELEVEN_SIMILARITY_BOOST` | `config.py:105-108` | `ELEVEN_STABILITY` sotto 0.30 fa "derivare" la voce su testi lunghi |
| Cambiare modello ElevenLabs (velocità vs qualità) | `ELEVEN_MODEL_ID` | `config.py:104` | Attualmente `eleven_flash_v2_5` (bassa latenza). `eleven_multilingual_v2` è più naturale ma molto più lento |
| Cambiare le lingue supportate | `SUPPORTED_LANGUAGES`, `DEFAULT_LANG` | `config.py:56-57` | Serve anche aggiungere prompt/messaggi per la nuova lingua altrove nel file |
| Cambiare la frase che avvia la conversazione | `TRIGGER_PHRASES_IT`, `TRIGGER_PHRASES_EN` | `config.py:142-143` | Tenerle sufficientemente lunghe/specifiche per evitare falsi positivi (commento già presente nel file) |
| Silenziare il log verboso dopo un debug | `LOG_LEVEL` | `config.py:147` | **Deve restare `INFO` in produzione** — `DEBUG` logga ogni singolo frame della webcam |
| Cambiare dove va a puntare l'audio TTS (Unity vs casse) | `TTS_OUTPUT_DEVICE` | `config.py:99` | Vedi §11, Step 9 |
| Disattivare completamente il rilevamento presenza (solo microfono) | `USE_PERSON_DETECTION` | `config.py:121` | Se `False`, la sessione parte non appena viene detta la frase trigger, senza aspettare una persona — **ma elimina anche il rischio del punto §9.4** |

---

## Fallback e troubleshooting in produzione

### Tutti i modi in cui una sessione si chiude

| # | Trigger | Dove nel codice | Note |
|---|---|---|---|
| 1 | Turno ≥ 3 raggiunto (risposta di chiusura del prompt) | `main.py:530-533` | Chiusura fisiologica, prevista dal design |
| 2 | Silenzio ≥ 12s dopo l'ultima risposta | `main.py:81-92` | Il timer si cancella non appena l'utente inizia a parlare e resta sospeso per tutta la durata della pipeline LLM+TTS |
| 3 | Parola di commiato riconosciuta | `main.py:452-460` | Confrontata sul testo trascritto |
| 4 | Webcam: persona uscita dal campo | `person_detector.py` → `main.py:277-294` | Vedi §9.1 — punto delicato |
| 5 | Errore di rete irreversibile (timeout) | `main.py:506-514` | Solo per timeout; altri errori API non chiudono la sessione |
| 6 | Comando testuale "esci/exit/quit/q" (solo modalità debug senza microfono) | `main.py:421` | Spegne l'intero programma, non solo la sessione |
| 7 | Ctrl+C reale | `main.py:598-616` | Spegnimento totale, nessun riavvio automatico |

### Messaggi di fallback: quando scattano davvero

`config.py` definisce quattro dizionari di messaggi "di scorta". Non
scattano tutti con la stessa facilità — uno in particolare è praticamente
morto nella pratica:

| Messaggio | Scatta quando | File:riga | Chiude la sessione? |
|---|---|---|---|
| `UNSUPPORTED_LANG_MESSAGES` | Lingua rilevata da Whisper non in `SUPPORTED_LANGUAGES` (it/en) | `main.py:436-442` | No |
| `NETWORK_ERROR_MESSAGES` | Solo `httpx.TimeoutException` (la chiamata Anthropic supera `API_TIMEOUT_SECONDS=30s`) | `main.py:507-515` | Sì |
| `RETRY_FAIL_MESSAGES` | Teoricamente: qualunque altro errore nella pipeline LLM/TTS. **In pratica quasi mai raggiunto** — vedi nota sotto | `main.py:516-523` | No |
| `ERROR_MESSAGES` | Catch-all per eccezioni impreviste in *qualunque* punto del turno (STT, controlli lingua/keyword, ecc.) — questo sì raggiungibile nella pratica | `main.py:544-552` | No |

**Nota importante su `RETRY_FAIL_MESSAGES`**: `play_response_stream()`
(`tts.py:251`) cattura **internamente** ogni eccezione che si verifica
generando/riproducendo la risposta (righe 287-288, 295-298, 329-330) e non
la rilancia mai al chiamante. Questo significa che il blocco
`except Exception` di `main.py:504-523`, che dovrebbe far scattare
`RETRY_FAIL_MESSAGES`, **non viene quasi mai raggiunto per errori
LLM/TTS reali** (404 modello non trovato, 400 parametro invalido, errori di
rete durante lo streaming ElevenLabs). È esattamente il motivo per cui,
diagnosticando un bug precedente, un errore 404/400 dell'API produceva una
sessione "muta" invece della frase di scuse — l'eccezione veniva
inghiottita prima di raggiungere il codice che dovrebbe gestirla.

### Fallback per sottosistema

**LLM (Anthropic)** — `agent.py`: nessun fallback ad altro provider/modello. Retry automatico solo per errori "overloaded" (529), 3 tentativi con backoff 1/2/3s. Qualsiasi altro errore (404, 400, 401, 429 non-overload) **non viene ritentato**: risale a `main.py`, che stampa l'errore, prova a dire un messaggio generico se possibile, e **continua la sessione** senza aver dato una risposta reale in quel turno. Nessun controllo su `stop_reason == "max_tokens"` (vedi §9.2). **Attenzione**: quando l'errore avviene durante `chat_stream` consumato dentro `play_response_stream` (il caso più comune, cioè quasi tutte le risposte conversazionali), l'eccezione non arriva mai a questo punto — vedi "Messaggi di fallback" sopra.

**TTS (ElevenLabs)** — `tts.py`: un solo motore vocale, nessun fallback ad altro provider. Se `sounddevice` manca, fallback solo a livello di libreria di riproduzione (`pygame`), non di sintesi. Se la generazione fallisce, `speak()` logga e ritorna silenziosamente (nessun audio, nessun crash). Nella pipeline streaming, un errore a metà genera viene catturato dentro il thread di background e solo loggato — `main.py` non se ne accorge, il turno prosegue come se fosse andato bene.

**STT (Whisper)** — `stt.py`: nessun fallback, un solo motore. **Il caricamento del modello all'avvio non ha try/except**: se il modello manca o CUDA non è disponibile, il programma crasha con un traceback grezzo prima ancora di stampare "Sistema pronto". Rilevamento lingua senza soglia di confidenza (`min_lang_prob` di default 0.0, mai passato da `main.py`) — può causare falsi rifiuti "capisco solo italiano o inglese" su frasi italiane brevi/rumorose trascritte con bassa confidenza linguistica. **Non ancora corretto.**

**Rilevamento presenza (YOLO)** — `person_detector.py`: se la webcam non si apre, il thread termina silenziosamente senza più generare eventi. Con `USE_PERSON_DETECTION=True`, l'avvio di una sessione dipende **esclusivamente** dall'evento "persona entrata": **se la webcam fallisce, il sistema resta bloccato in IDLE per sempre**, senza errore visibile e senza fallback da tastiera (nonostante un commento obsoleto in `config.py` lo lasci intendere — non esiste nessuna gestione tasti nel codice attuale). **Non ancora corretto.**

**Audio ambientale** — `audio_manager.py`: se il file manca, disattiva silenziosamente tutte le funzioni, nessun crash.

**Pre-generazione farewell** — `main.py:192-240`: se fallisce, il farewell viene generato al volo al momento della chiusura invece che pre-cachato — nessun blocco, solo perdita del risparmio crediti ElevenLabs.

### Rischi noti in dettaglio

**§9.1 — Falsi "uscita persona"**: osservato in produzione che YOLO può azzerare la rilevazione per ~1 secondo pieno di frame consecutivi anche a persona ferma davanti alla camera (non sfarfallio, azzeramento sostenuto). Causa non confermata con certezza (luce, angolo, contesa GPU con Whisper — entrambi girano sulla stessa scheda). Mitigato alzando `PERSON_DETECTION_PERSISTENT_FRAMES` (5→15) e `PERSON_DETECTION_EXIT_DELAY` (1.5→3.0s), ma **non risolve azzeramenti sostenuti oltre i 15 frame**. Per diagnosticare: impostare temporaneamente `LOG_LEVEL="DEBUG"` (logga `present_now/person_count/counter` per ogni frame) — **ricordarsi di rimettere `INFO`** dopo, il livello DEBUG è molto verboso.

**§9.2 — Troncamento risposte per limite token**: `agent.py:67-72` ha limiti fissi (200/380/250 token). Se il modello supera il limite, la risposta viene tagliata a metà frase senza avviso. Non ancora corretto — proposta: alzare i limiti e loggare un warning su `stop_reason == "max_tokens"`.

**§9.3 — Nessuna soglia di confidenza lingua**: vedi sopra, "STT". Non ancora corretto.

**§9.4 — Webcam come singolo punto di fallimento**: vedi sopra, "Rilevamento presenza". Non ancora corretto — da considerare un fallback che avvii comunque l'ascolto trigger via microfono se la webcam fallisce, o un allarme visibile per l'operatore.

**§9.5 — Crash all'avvio se Whisper non carica**: nessuna gestione errori sul caricamento STT (vedi sopra).

**§9.6 — Chiavi API**: nel repo standalone (`samcoppola/museo` su GitHub) le chiavi sono caricate da `.env` (gitignored, vedi `.env.example`) — **chi clona il repo deve creare il proprio `.env`** prima di avviare, altrimenti `config.py` solleva `KeyError` all'avvio.

### Comportamento di spegnimento

- **Chiusura di una singola sessione**: torna in IDLE, pronta per il prossimo visitatore.
- **Ctrl+C o comando debug "esci"**: spegne **l'intero programma**, non solo la sessione — ferma il person detector, l'ambient, pronuncia un messaggio di spegnimento diverso dal farewell, poi il processo termina. **Va riavviato manualmente**, nessun auto-restart/watchdog.

---

## Requisiti hardware

| Componente           | Minimo              | Consigliato            |
|----------------------|---------------------|------------------------|
| Sistema operativo    | Windows 10 64-bit   | Windows 11 64-bit      |
| GPU                  | NVIDIA con 8 GB VRAM | 12+ GB VRAM            |
| RAM                  | 16 GB               | 32 GB                  |
| Driver NVIDIA        | 550                 | pari o superiore a 550 |
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
| **Native Windows** | VB-Audio funziona nativamente, GPU diretta, webcam diretta, Unity sulla stessa macchina |

---

## Crediti API (costi)

| Servizio | Uso | Costo indicativo |
|---|---|---|
| **Anthropic (Claude Sonnet 5)** | Ogni risposta del comandante | ~$0.003 per risposta |
| **ElevenLabs (eleven_flash_v2_5)** | Ogni risposta audio | Piano Creator $22/mese (100k caratteri) |

Il farewell è pre-generato all'avvio e salvato su disco — non consuma crediti ElevenLabs ad ogni sessione (vedi §9, "Pre-generazione farewell").

Con una sessione media di 3-4 turni, il costo per visitatore è nell'ordine di pochi centesimi combinando Anthropic + ElevenLabs — utile per stimare il budget mensile in base al traffico atteso del museo.
