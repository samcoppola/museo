
import socket

UDP_IP = "127.0.0.1"
UDP_PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

trigger_disponibili = {
    1: "RILEVATA_FRASE",
    2: "USCITA_PERSONA",
    3: "FINE_ESPERIENZA",
    4: "RILEVATA_PERSONA"
}

def invia_messaggio(tasto):
    messaggio = trigger_disponibili[tasto]

    # Converte la stringa in byte (utf-8) e la invia sulla porta di Unity
    sock.sendto(messaggio.encode("utf-8"), (UDP_IP, UDP_PORT))

    print(f"[>] Tasto '{tasto}' premuto -> Trigger inviato: {messaggio}")
