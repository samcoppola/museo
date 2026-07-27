"""
Gestisce la cronologia della conversazione tra utente e agente AI.

Quello che prima era: messages = []
messages.append({"role": "user", "content": "Ciao"})
messages.append({"role": "assistant", "content": "Ave!"})
"""
import config

class ConversationManager:
    """
        Gestisce la lista di messaggi della conversazione.

        Responsabilità:
        - Aggiungere messaggi user/assistant
        - Limitare lunghezza cronologia
        - Fornire cronologia per API
        - Reset conversazione
        """
    def __init__(self, max_length=None):
        """
                Inizializza il gestore conversazione.

                Args:
                    max_length: Numero massimo di messaggi da mantenere.
                               Se None, usa config.MAX_HISTORY_LENGTH
                """
        self.messages = []
        self.max_length = max_length or config.MAX_HISTORY_LENGTH

        self.turns_since_reminder = 0
        self.reminder_frequency = getattr(config, 'REMINDER_FREQUENCY', 8)

        if config.DEBUG_MODE:
            print(f"📝 ConversationManager inizializzato")
            print(f"   Max messages: {self.max_length}")
            print(f"   Reminder ogni: {self.reminder_frequency} turni")

    def add_user_message(self, content):
        """
        Aggiunge un messaggio dell'utente.

        Args:
            content: Testo del messaggio
        """
        self.messages.append({
            "role": "user",
            "content": content
        })
        self._trim_if_needed()

        if config.DEBUG_MODE:
            print(f"User msg aggiunto (totale: {len(self.messages)})")

    def add_assistant_message(self, content):
        """
        Aggiunge un messaggio dell'assistente.

        Args:
            content: Testo del messaggio
        """
        self.messages.append({
            "role": "assistant",
            "content": content
        })
        self._trim_if_needed()

        if config.DEBUG_MODE:
            print(f" Assistant msg aggiunto (totale: {len(self.messages)})")

    def get_history(self):
        """
        Ritorna la cronologia completa per l'API.

        Returns:
            Lista di messaggi in formato API
        """
        if self._should_inject_reminder():
            return self._get_history_with_reminder()

        return self.messages.copy()

    def reset(self):
        """
        Cancella tutta la cronologia.
        Utile quando arriva un nuovo turista.
        """
        self.messages = []

        # NUOVO: Reset contatore reminders
        self.turns_since_reminder = 0

        if config.DEBUG_MODE:
            print(" Conversazione resettata")

    def _trim_if_needed(self):
        """
        Rimuove i messaggi più vecchi se superiamo il limite.

        Nota: Metodo privato (prefisso _) - solo per uso interno.
        """
        if len(self.messages) > self.max_length:
            # Quanti messaggi rimuovere?
            to_remove = len(self.messages) - self.max_length

            # MIGLIORATO: Rimuovi coppie complete (user+assistant)
            if to_remove % 2 != 0:
                to_remove += 1

            # Tieni solo gli ultimi max_length messaggi
            self.messages = self.messages[to_remove:]

            if config.DEBUG_MODE:
                print(f"  Sliding window: rimossi {to_remove} messaggi vecchi")
                print(f"   Messaggi rimanenti: {len(self.messages)}")

    def _should_inject_reminder(self):
        """
        Decide se è il momento di iniettare un system reminder.

        Returns:
            True se serve reminder, False altrimenti
        """
        # Non iniettare se:
        # 1. Conversazione troppo corta (< 4 messaggi)
        # 2. Non abbastanza turni passati

        if len(self.messages) < 4:
            return False

        if self.turns_since_reminder < self.reminder_frequency:
            return False

        return True

    def _get_history_with_reminder(self):
        """
        Ritorna cronologia con system reminder iniettato.

        Il reminder viene aggiunto come messaggio user alla fine,
        così il modello lo vede come istruzione recente.

        Returns:
            Lista messaggi con reminder
        """
        history = self.messages.copy()

        # Crea il reminder
        reminder = self._create_reminder()

        # Inietta come ultimo messaggio user
        history.append({
            "role": "user",
            "content": reminder
        })

        # Reset contatore
        self.turns_since_reminder = 0

        if config.DEBUG_MODE:
            print("💡 System reminder iniettato")

        return history

    def _create_reminder(self):
        """
        Crea il testo del system reminder.

        Puoi personalizzare questo testo per rinforzare
        aspetti specifici del personaggio.

        Returns:
            Stringa del reminder
        """
        reminder = (
            "[SYSTEM REMINDER - Non rispondere a questo messaggio]\n\n"
            "Ricorda:\n"
            "- Sei Marco Aurelio Maximus, Centurione della Legione X Gemina\n"
            "- Stai parlando con un turista nel museo\n"
            "- Risposte BREVI: 2-3 frasi (max 60 parole)\n"
            "- Tono entusiasta e coinvolgente\n"
            "- Usa aneddoti personali, non fatti da manuale\n"
            "- NO asterischi (*) o azioni scritte\n"
            "- Termini latini occasionali (Ave, gladius, testudo)\n\n"
            "[Continua la conversazione normalmente]"
        )

        return reminder

    def get_message_count(self):
        """
        Ritorna il numero di messaggi nella cronologia.

        Returns:
            Numero intero di messaggi
        """
        return len(self.messages)

    def remove_last_message(self):
        """Rimuove l'ultimo messaggio (rollback dopo errore API)."""
        if self.messages:
            self.messages.pop()

    def get_last_message(self):
        """
        Ritorna l'ultimo messaggio della cronologia.

        Returns:
            Dizionario del messaggio o None se vuoto
        """
        return self.messages[-1] if self.messages else None

    def export_conversation(self):
        """
        Esporta la conversazione in formato leggibile.
        Utile per debug o per salvare su file.

        Returns:
            Stringa formattata della conversazione
        """
        output = []
        for msg in self.messages:
            role = "TURISTA" if msg["role"] == "user" else "COMANDANTE"
            output.append(f"{role}: {msg['content']}")
        return "\n\n".join(output)

    def __len__(self):
        """
        Permette di usare len(conversation).

        Returns:
            Numero di messaggi
        """
        return len(self.messages)

    def __repr__(self):
        """
        Rappresentazione stringa dell'oggetto per debug.

        Returns:
            Stringa descrittiva
        """
        return f"ConversationManager({len(self.messages)} messaggi, max={self.max_length})"