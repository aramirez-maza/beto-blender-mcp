# BETO-TRACE: FD.SEC1.INTENT.DETECT_LANDMARKS
import os
import numpy as np
from PIL import Image
from typing import Optional

import face_alignment  # BETO-TRACE: FD.SEC8.TECH.FAN_MODEL

from models.session import Session


class FaceDetector:
    """
    BETO-TRACE: FD.SEC1.INTENT.DETECT_LANDMARKS
    Phase 1: valida imagen de entrada.
    Phase 2: extrae 68 landmarks 2D con FAN.
    """

    # BETO-TRACE: FD.SEC8.TECH.FAN_MODEL
    def __init__(self, device: str = "auto"):
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        self.fa = face_alignment.FaceAlignment(
            face_alignment.LandmarksType.TWO_D,
            device=device,
            flip_input=False,
        )

    def run(self, session: Session) -> Session:
        # BETO-TRACE: FD.SEC7.PHASE.IMAGE_LOAD_VALIDATE
        img = self._load_and_validate(session)
        if img is None:
            return session  # error ya registrado en session

        # BETO-TRACE: FD.SEC7.PHASE.LANDMARK_EXTRACTION
        landmarks = self._extract_landmarks(img, session)
        if landmarks is None:
            return session  # NO_FACE_DETECTED registrado

        # BETO-TRACE: FD.SEC7.PHASE.FACE_CROP — recortar cara antes de pasar a DECA
        cropped_img, landmarks_224 = self._crop_face(img, landmarks)

        # BETO-TRACE: FD.SEC3.OUTPUT.LANDMARKS_2D — en espacio 224x224
        session.landmarks_2d = landmarks_224

        # BETO-TRACE: FD.SEC3.OUTPUT.VALIDATED_IMAGE — cara recortada 224x224 RGB
        session.validated_image = cropped_img

        return session

    # ─── Phase 1 ─────────────────────────────────────────────────────────────

    def _load_and_validate(self, session: Session) -> Optional[np.ndarray]:
        # BETO-TRACE: FD.SEC3.INPUT.IMAGE_PATH
        if not os.path.exists(session.image_path):
            self._fail(session, "FILE_NOT_FOUND", f"File not found: {session.image_path}")
            return None

        # BETO-TRACE: FD.OQ-FD-01 — solo JPEG y PNG (DECLARED [BETO_ASSISTED] OQ-11)
        ext = os.path.splitext(session.image_path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png"):
            self._fail(session, "FORMAT_NOT_SUPPORTED",
                       f"Format '{ext}' not supported. Use JPEG or PNG.")
            return None

        img = np.array(Image.open(session.image_path).convert("RGB"))
        h, w = img.shape[:2]

        # Resolución mínima 224x224 — DECLARED [BETO_ASSISTED] OQ-11
        if h < 224 or w < 224:
            self._fail(session, "SIZE_TOO_SMALL",
                       f"Image {w}x{h}px is below minimum 224x224.")
            return None

        return img

    # ─── Phase 2 ─────────────────────────────────────────────────────────────

    def _extract_landmarks(self, img: np.ndarray, session: Session) -> Optional[np.ndarray]:
        preds = self.fa.get_landmarks(img)

        if not preds:
            # BETO-TRACE: FD.SEC5.INV.NO_FACE_HALT
            self._fail(session, "NO_FACE_DETECTED",
                       "FAN could not detect any human face in the image.")
            return None

        # BETO-TRACE: FD.OQ-FD-01 — múltiples rostros: mayor bounding box
        if len(preds) > 1:
            areas = [
                (p[:, 0].max() - p[:, 0].min()) * (p[:, 1].max() - p[:, 1].min())
                for p in preds
            ]
            preds = [preds[int(np.argmax(areas))]]

        # BETO-TRACE: FD.OQ-FD-02 — array (68, 2) float32
        return preds[0][:, :2].astype(np.float32)

    def _crop_face(self, img: np.ndarray, landmarks: np.ndarray):
        """
        BETO-TRACE: FD.SEC7.PHASE.FACE_CROP
        Recorta la cara con 30% de padding alrededor del bounding box de landmarks.
        Redimensiona a 224x224 y transforma landmarks al nuevo espacio.
        """
        h, w = img.shape[:2]
        x_min, x_max = landmarks[:, 0].min(), landmarks[:, 0].max()
        y_min, y_max = landmarks[:, 1].min(), landmarks[:, 1].max()

        # 30% padding alrededor del bounding box de landmarks
        pad_x = (x_max - x_min) * 0.30
        pad_y = (y_max - y_min) * 0.35  # más padding arriba/abajo para incluir frente y mentón

        x1 = max(0, int(x_min - pad_x))
        y1 = max(0, int(y_min - pad_y * 1.2))  # más padding arriba (frente/cabeza)
        x2 = min(w, int(x_max + pad_x))
        y2 = min(h, int(y_max + pad_y * 0.5))

        crop = img[y1:y2, x1:x2]
        crop_h, crop_w = crop.shape[:2]

        # Resize a 224x224
        cropped_224 = np.array(
            Image.fromarray(crop).resize((224, 224))
        ).astype(np.uint8)

        # Transformar landmarks al espacio 224x224
        scale_x = 224.0 / crop_w
        scale_y = 224.0 / crop_h
        lmk_224 = landmarks.copy().astype(np.float32)
        lmk_224[:, 0] = (lmk_224[:, 0] - x1) * scale_x
        lmk_224[:, 1] = (lmk_224[:, 1] - y1) * scale_y

        return cropped_224, lmk_224

    # ─── helpers ──────────────────────────────────────────────────────────────

    def _fail(self, session: Session, code: str, cause: str) -> None:
        # BETO-TRACE: BLENDERFACE.SEC5.INV.FAIL_PRODUCES_REPORT
        session.status = "FAIL"
        session.error = {
            "session_id": session.session_id,
            "error_code": code,
            "cause": cause,
            "component": "FACE_DETECTOR",
        }
