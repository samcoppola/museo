"""
Gestione suono ambientale di sottofondo.
Riproduce ambient_soundscape.mp3 in loop con duck/unduck quando parla l'AI.
"""
import os
import time
import threading
import pygame
import config


class AmbientSoundManager:
    """
    Riproduce il soundscape ambientale in loop.
    Duck: abbassa volume quando l'AI parla o il microfono ascolta.
    Unduck: ripristina volume normale.
    """

    def __init__(self, volume=0.40, duck_ratio=0.5):
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        if pygame.mixer.get_num_channels() < 16:
            pygame.mixer.set_num_channels(16)

        self.volume = volume
        self.volume_ducked = volume * duck_ratio
        self._ducked = False
        self._sound = None
        self._channel = None
        self._fade_lock = threading.Lock()

        ambient_file = config.AMBIENT_FILE
        if os.path.exists(ambient_file):
            self._sound = pygame.mixer.Sound(ambient_file)
        else:
            print(f"[Audio] File ambient non trovato: {ambient_file}")
            print(f"[Audio] Verifica il percorso AMBIENT_FILE in config.py")

    def start(self):
        if not self._sound:
            return
        if self._channel and self._channel.get_busy():
            return  # già in riproduzione
        self._sound.set_volume(self.volume)
        self._channel = self._sound.play(loops=-1, fade_ms=2000)
        self._ducked = False
        print(f"[Audio] Ambient avviato (vol={self.volume}, duck={self.volume_ducked:.2f})")

    def duck(self, duration_ms=400):
        if self._ducked or not self._sound:
            return
        self._ducked = True
        threading.Thread(
            target=self._fade_volume, args=(self.volume_ducked, duration_ms), daemon=True
        ).start()

    def unduck(self, duration_ms=600):
        if not self._ducked or not self._sound:
            return
        self._ducked = False
        threading.Thread(
            target=self._fade_volume, args=(self.volume, duration_ms), daemon=True
        ).start()

    def _fade_volume(self, target, duration_ms):
        with self._fade_lock:
            current = self._sound.get_volume()
            if abs(current - target) < 0.005:
                return
            steps = max(1, duration_ms // 20)
            step_time = duration_ms / 1000.0 / steps
            delta = (target - current) / steps
            for i in range(steps):
                new_vol = current + delta * (i + 1)
                self._sound.set_volume(max(0.0, min(1.0, new_vol)))
                time.sleep(step_time)
            self._sound.set_volume(target)

    def stop(self):
        if self._channel and self._channel.get_busy():
            self._channel.fadeout(2000)
        self._ducked = False
