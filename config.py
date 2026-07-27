"""
Configurazione per l'assistente museo — versione definitiva.
TTS: ElevenLabs | STT: Whisper+VAD | Presenza: YOLO webcam
"""
import os
from dotenv import load_dotenv

# Cartella base del progetto (dove si trova questo file config.py)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

# --- API KEYS ---
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
API_TIMEOUT_SECONDS = 30

# --- MODELLO ---
MODEL = "claude-sonnet-5"
MAX_TOKENS = 130      # ~95 parole — margine per non troncare frasi lunghe
TEMPERATURE = 0.5

# --- CONVERSAZIONE ---
MAX_HISTORY_LENGTH = 16   # 8 scambi (user+assistant = 2 msg per scambio)
REMINDER_FREQUENCY = 8    # rinforzo personaggio ogni N turni

# --- PROMPT ---
# V4: template con {{NUMERO_RISPOSTA}}/{{RESPONSE_NUMBER}} iniettato per turno
WELCOME_MESSAGES_V4 = {
    "it": [
        "Fermo lì forestiero! Chi si avvicina al mio accampamento? Io sono Marco Aurelio Maximus, comandante della dodicesima legione. Sei un alleato o soltanto un curioso? Prima di tutto, dimmi il tuo nome.",
        "Chi entra in questa tenda senza essere annunciato? Fermo. Sono Marco Aurelio Maximus, e in questo accampamento ogni uomo risponde di sé davanti alla legione. Tu chi sei? Dimmi il tuo nome.",
        "Ah, un visitatore. Avvicinati, ma con rispetto: questa è la tenda del comandante, non una piazza di mercato. Io sono Marco Aurelio Maximus. Ho guidato uomini in battaglia e li ho riportati a casa. Dimmi, come ti chiami?",
        "Fermati sulla soglia. Prima di entrare nella tenda di un comandante romano, ci si presenta. Io sono Marco Aurelio Maximus, dodicesima legione. Il tuo nome, forestiero?",
        "Entra pure. Stavo rivedendo le mappe di campagna, ma un visitatore è sempre benvenuto, purché sappia come presentarsi. Marco Aurelio Maximus. E tu, come ti chiami?",
    ],
    "en": [
        "Halt, stranger! Who dares approach the tent of Marcus Aurelius Maximus, commander of the Twelfth Legion? Twenty years of marches and battles behind me. Tell me, what is your name? Have you come to fight or only to observe?",
        "Who enters this tent unannounced? Stop. I am Marcus Aurelius Maximus, and in this camp every man answers for himself before the legion. Are you an ally or merely curious? First things first, tell me your name.",
        "Ah, a visitor. Step forward, but with respect: this is a commander's tent, not a market square. I am Marcus Aurelius Maximus. I have led men into battle and brought them home. Tell me, what is your name?",
        "Halt at the threshold. Before entering a Roman commander's tent, one introduces oneself. I am Marcus Aurelius Maximus, Twelfth Legion. Your name, stranger?",
        "Come in. I was reviewing the campaign maps, but a visitor is always welcome, provided they know how to present themselves. Marcus Aurelius Maximus. And you, what is your name?",
    ],
}

FAREWELL_MESSAGES = {
    "it": "Arrivederci, viaggiatore. Che gli dei ti proteggano.",
    "en": "Farewell, traveler. May the gods protect you.",
}

SHUTDOWN_MESSAGE = {
    "it": "Vale. Il sistema si spegne.",
    "en": "Vale. The system is shutting down.",
}

END_KEYWORDS = {
    "it": ["fine", "arrivederci", "stop", "basta"],
    "en": ["goodbye", "bye", "stop", "end", "farewell"],
}

SUPPORTED_LANGUAGES = {"it", "en"}
DEFAULT_LANG = "it"

UNSUPPORTED_LANG_MESSAGES = {
    "it": "Mi dispiace, comprendo solo italiano o inglese.",
    "en": "I'm sorry, I only understand Italian or English.",
}

ERROR_MESSAGES = {
    "it": "Mi dispiace, non ho capito bene. Riprova per favore.",
    "en": "I'm sorry, I didn't understand well. Please try again.",
}

RETRY_FAIL_MESSAGES = {
    "it": "Perdonami, un messaggero dal fronte mi ha distolto un istante. Cosa stavi dicendo?",
    "en": "Forgive me, a messenger from the front took my attention for a moment. What were you saying?",
}

NETWORK_ERROR_MESSAGES = {
    "it": "Perdonami, forestiero, qualcosa disturba i miei pensieri in questo momento. Torna a trovarmi più tardi. Vale!",
    "en": "Forgive me, stranger, something troubles my thoughts right now. Come find me later. Vale!",
}

# --- TIMING SESSIONE ---
IDLE_TIMEOUT_SECONDS = 12    # secondi di silenzio dopo l'ultima risposta → chiudi sessione
                             # NB: includere overhead STT (~4s registrazione+trascrizione)
GOODBYE_COOLDOWN_SECONDS = 3 # pausa dopo farewell prima di tornare in IDLE

# --- AUDIO AMBIENTALE ---
USE_TTS = True
USE_AMBIENT_SOUNDS = True
AMBIENT_FILE = os.path.join(_BASE_DIR, "sounds", "ambient", "ambient_soundscape.mp3")
AMBIENT_VOLUME = 0.70
DUCK_RATIO = 0.5

# --- AUDIO OUTPUT DEVICE ---
# Linux  : None  (usa ALSA/PulseAudio di sistema)
# Windows: "CABLE Input"  (VB-Audio Virtual Cable → Unity legge da CABLE Output)
#   1. Installa VB-Audio Virtual Cable: https://vb-audio.com/Cable/
#   2. Imposta TTS_OUTPUT_DEVICE = "CABLE Input"
#   3. In Unity: configura lip-sync con input microfono = "CABLE Output"
#   L'ambient sound (pygame) rimane sulle casse fisiche — solo la voce TTS
#   (sounddevice) va al cavo virtuale e quindi a Unity.
TTS_OUTPUT_DEVICE = None  # Windows: "CABLE Input"

# --- ELEVENLABS ---
USE_ELEVEN_LABS = True
ELEVEN_API_KEY = os.environ["ELEVEN_API_KEY"]
ELEVEN_VOICE             = "GgHFK7Wdh706wXHjXaI3"
ELEVEN_SPEED             = 0.85
ELEVEN_STABILITY         = 0.70   # 0=variabile/espressivo, 1=monotono/stabile — sotto 0.30 la voce deriva su testi lunghi
ELEVEN_SIMILARITY_BOOST  = 0.90   # fedeltà alla voce originale
ELEVEN_STREAM  = True   # streaming PCM via sounddevice (~100-200ms latenza)

# --- AUDIO PRE-GENERATI (farewell — risparmio crediti ElevenLabs) ---
# All'avvio, genera una volta sola i farewell e li salva su disco.
# Le successive esecuzioni li caricano dal disco (nessuna chiamata API).
PREGEN_AUDIO     = True
PREGEN_AUDIO_DIR = os.path.join(_BASE_DIR, "data", "audio_cache")

# --- PERSON DETECTION ---
# Ruolo: rileva quando la persona esce → chiude sessione immediatamente.
# L'avvio sessione avviene solo con tasto I/E (non automatico).
USE_PERSON_DETECTION         = True
PERSON_DETECTION_CAM_INDEX   = 2      # 0 = webcam integrata, 1-2 = USB esterna
PERSON_DETECTION_CONF        = 0.5
PERSON_DETECTION_PERSISTENT_FRAMES = 5
PERSON_DETECTION_RESIZE_WIDTH = 640
PERSON_DETECTION_MODEL       = os.path.join(_BASE_DIR, "models", "yolo11n.pt")
PERSON_DETECTION_EXIT_DELAY  = 1.5   # secondi di attesa dopo uscita stabile prima di chiudere

# --- STT ---
USE_STT        = True
WHISPER_MODEL  = os.path.join(_BASE_DIR, "models", "whisper-large-v3")
WHISPER_DEVICE = "cuda"

# VAD (usato solo con USE_STT=True)
VAD_SILENCE_MS      = 1000   # ms di silenzio per fermare registrazione
VAD_MIN_RECORD_MS   = 600    # durata minima registrazione
VAD_START_THRESHOLD = 0.6    # soglia inizio parlato; alzare in ambienti rumorosi (0.7-0.8)
VAD_STOP_THRESHOLD  = 0.25   # soglia fine parlato

# --- TRIGGER PHRASES ---
# La conversazione inizia solo quando viene detta una di queste frasi.
# Frasi brevi/comuni escluse per evitare falsi positivi su altre lingue.
TRIGGER_PHRASES_IT = ["salve comandante"]
TRIGGER_PHRASES_EN = ["hail commander"]

# --- LOGGING ---
LOG_DIR        = os.path.join(_BASE_DIR, "logs")
LOG_LEVEL      = "INFO"
LOG_TO_CONSOLE = False
DEBUG_MODE     = True
