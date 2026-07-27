"""
PersonDetector: rilevamento persona via webcam con YOLO.
Usa debounce (PERSISTENT_FRAMES) per filtrare rilevamenti instabili.
Gira in un thread daemon separato; emette callback su enter/exit stabile.

Dipendenze (installare nel venv principale):
    pip install opencv-python ultralytics
"""
import threading
import time
import logging

logger = logging.getLogger(__name__)

PERSON_CLASS_ID = 0  # ID classe "person" in YOLO COCO


class PersonDetector:
    """
    Rilevatore persona via webcam con debounce.

    Parametri:
        cam_index           - indice webcam (default 0)
        conf                - soglia confidenza YOLO (0-1)
        persistent_frames   - frame consecutivi per stato stabile
        resize_width        - larghezza resize frame (None = no resize)
        model_path          - percorso/nome modello YOLO (.pt)
        show_window         - mostra finestra OpenCV con annotazioni (debug)
        on_person_enter     - callback chiamato quando persona entra (stabile)
        on_person_exit      - callback chiamato quando persona esce (stabile)
    """

    def __init__(self,
                 cam_index=0,
                 conf=0.5,
                 persistent_frames=5,
                 resize_width=640,
                 model_path=None,
                 show_window=False,
                 on_person_enter=None,
                 on_person_exit=None):
        self.cam_index = cam_index
        self.conf = conf
        self.persistent_frames = persistent_frames
        self.resize_width = resize_width
        if model_path is None:
            import config as _cfg
            model_path = _cfg.PERSON_DETECTION_MODEL
        self.model_path = model_path
        self.show_window = show_window
        self.on_person_enter = on_person_enter
        self.on_person_exit = on_person_exit

        self._stop_event = threading.Event()
        self._thread = None
        self._stable_state = False  # True = persona presente e stabile

    @property
    def person_present(self):
        """True se una persona è attualmente rilevata (stato stabile)."""
        return self._stable_state

    def start(self):
        """Avvia il thread di rilevamento in background."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="PersonDetector")
        self._thread.start()
        logger.info("[PersonDetector] Thread avviato (cam=%d, conf=%.2f, debounce=%d)",
                    self.cam_index, self.conf, self.persistent_frames)

    def stop(self):
        """Ferma il thread di rilevamento."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("[PersonDetector] Fermato")

    def _run(self):
        """Loop principale del thread di rilevamento."""
        try:
            import cv2
            import numpy as np
            from ultralytics import YOLO
        except ImportError as e:
            logger.error(
                "[PersonDetector] Dipendenza mancante: %s. "
                "Installa con: pip install opencv-python ultralytics", e
            )
            return

        try:
            logger.info("[PersonDetector] Caricamento modello %s...", self.model_path)
            model = YOLO(self.model_path)
            logger.info("[PersonDetector] Modello caricato")
        except Exception as e:
            logger.error("[PersonDetector] Impossibile caricare modello YOLO (%s): %s",
                         self.model_path, e)
            return

        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            logger.error("[PersonDetector] Webcam non disponibile (CAM_INDEX=%d)", self.cam_index)
            return

        stable_state = False
        counter = 0

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    logger.warning("[PersonDetector] Frame non leggibile, riprovo...")
                    time.sleep(0.1)
                    continue

                # Ridimensiona per più FPS (opzionale)
                if self.resize_width is not None:
                    h, w = frame.shape[:2]
                    scale = self.resize_width / float(w)
                    frame = cv2.resize(frame, (self.resize_width, int(h * scale)))

                # Inferenza YOLO
                results = model.predict(frame, conf=self.conf, verbose=False)
                r = results[0]

                # Conta persone nel frame
                present_now = False
                person_count = 0
                if r.boxes is not None and len(r.boxes) > 0:
                    cls = r.boxes.cls.cpu().numpy().astype(int)
                    person_count = int(np.sum(cls == PERSON_CLASS_ID))
                    present_now = person_count > 0

                # Debounce simmetrico:
                #   entrata: counter deve raggiungere persistent_frames (N frame presenti)
                #   uscita:  counter deve scendere a 0 (N frame assenti)
                if present_now:
                    counter = min(self.persistent_frames, counter + 1)
                else:
                    counter = max(0, counter - 1)

                if stable_state:
                    new_stable_state = (counter > 0)              # esce solo quando counter tocca 0
                else:
                    new_stable_state = (counter >= self.persistent_frames)  # entra quando pieno

                # Emetti evento solo al cambio di stato stabile
                if new_stable_state != stable_state:
                    stable_state = new_stable_state
                    self._stable_state = stable_state
                    ts = time.strftime("%H:%M:%S")
                    if stable_state:
                        logger.info("[PersonDetector] [%s] Persona entrata (stabile)", ts)
                        print(f"\n[PersonDetector] [{ts}] Persona rilevata — avvio sessione")
                        if self.on_person_enter:
                            self.on_person_enter()
                    else:
                        logger.info("[PersonDetector] [%s] Persona uscita (stabile)", ts)
                        print(f"\n[PersonDetector] [{ts}] Persona uscita — chiusura sessione")
                        if self.on_person_exit:
                            self.on_person_exit()

                # Nota: cv2.imshow/waitKey devono girare sul thread principale (Linux/Qt).
                # Per visualizzare il feed con bounding box usa test_person_detector.py

        finally:
            cap.release()
            logger.info("[PersonDetector] Webcam rilasciata")
