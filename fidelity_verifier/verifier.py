# BETO-TRACE: FV.SEC1.INTENT.VERIFY_REPROJECTION_FIDELITY
import numpy as np
from models.session import Session

# BETO-TRACE: FV.SEC8.TECH.5PX_THRESHOLD — DECLARED_WITH_LIMITS (BETO_ASSISTED OQ-07)
FIDELITY_THRESHOLD_PX = 5.0
# BETO-TRACE: FV.SEC8.TECH.WEAK_PERSPECTIVE_CAMERA — imagen normalizada 224x224
NORMALIZED_SIZE = 224


class FidelityVerifier:
    """
    BETO-TRACE: FV.SEC1.INTENT.VERIFY_REPROJECTION_FIDELITY
    Phase 1: Obtiene proyecciones 2D de DECA (landmarks proyectados en 224x224).
    Phase 2: Escala landmarks detectados al espacio 224x224.
    Phase 3: Calcula mean L2 reprojection error.
    Phase 4: Aplica threshold y emite PASS/FAIL.
    """

    def run(self, session: Session) -> Session:
        # ─── Phase 1 — Obtener landmarks proyectados por DECA ────────────────
        # BETO-TRACE: FV.SEC7.PHASE.LANDMARK_MAPPING
        # DECA ya calcula los landmarks 2D proyectados internamente.
        # Se usan deca_params['landmarks2d_proj'] en lugar de re-proyectar manualmente.
        # BETO-TRACE: FV.OQ-FV-01 — mapping FLAME→FAN nativo de DECA
        landmarks_proj = self._get_projected_landmarks(session)
        if landmarks_proj is None:
            return session

        # ─── Phase 2 — Escalar landmarks detectados a espacio 224x224 ────────
        # BETO-TRACE: FV.SEC7.PHASE.3D_2D_PROJECTION + FV.OQ-FV-02
        landmarks_detected_norm = self._normalize_landmarks(session)
        if landmarks_detected_norm is None:
            return session

        # ─── Phase 3 — Mean L2 reprojection error ────────────────────────────
        # BETO-TRACE: FV.SEC7.PHASE.ERROR_COMPUTATION
        # BETO-TRACE: FV.SEC8.TECH.MEAN_L2_REPROJECTION
        diff = landmarks_proj - landmarks_detected_norm   # (68, 2)
        distances = np.linalg.norm(diff, axis=1)          # (68,)
        fidelity_score = float(np.mean(distances))

        # BETO-TRACE: FV.SEC3.OUTPUT.FIDELITY_SCORE
        session.fidelity_score = round(fidelity_score, 4)

        # ─── Phase 4 — Aplicar threshold y emitir veredicto ──────────────────
        # BETO-TRACE: FV.SEC7.PHASE.VERDICT_EMISSION
        # BETO-TRACE: FV.SEC5.INV.PASS_ONLY_BELOW_THRESHOLD
        # BETO-TRACE: FV.OQ-07 — threshold = 5px en espacio 224x224
        self._emit_verdict(session, fidelity_score)
        return session

    # ─── helpers ──────────────────────────────────────────────────────────────

    def _get_projected_landmarks(self, session: Session):
        """
        BETO-TRACE: FV.SEC7.PHASE.LANDMARK_MAPPING
        DECA produce landmarks2d_proj en coordenadas normalizadas [-1, 1].
        Convertir a píxeles en espacio 224x224.
        """
        if session.deca_params is None:
            self._fail(session, "MISSING_DECA_PARAMS", "deca_params is None in session")
            return None

        lmk_proj = session.deca_params.get("landmarks2d_proj")
        if lmk_proj is None:
            self._fail(session, "MISSING_LANDMARKS_PROJ",
                       "deca_params does not contain 'landmarks2d_proj'")
            return None

        # Shape: (1, 68, 2) — coordenadas en [-1, 1]
        lmk = lmk_proj[0] if lmk_proj.ndim == 3 else lmk_proj  # → (68, 2) o (68, 3)
        lmk = lmk[:, :2].copy()  # tomar solo X,Y

        # BETO-TRACE: FV.OQ-FV-02 — conversión a espacio imagen 224x224
        # DECA aplica Y flip antes de convertir a píxeles (decode.py línea 174):
        #   landmarks2d[:,:,1:] = -landmarks2d[:,:,1:]
        # Y conversión: lmk * image_size/2 + image_size/2
        lmk[:, 1] = -lmk[:, 1]  # flip Y: 3D up→down = image top→bottom
        half = NORMALIZED_SIZE / 2.0
        lmk_px = lmk * half + half  # (68, 2) en píxeles 224x224

        return lmk_px.astype(np.float32)

    def _normalize_landmarks(self, session: Session):
        """
        BETO-TRACE: FV.OQ-FV-02 — escalar landmarks detectados a espacio 224x224.
        Los landmarks de FAN están en coordenadas de imagen original.
        """
        if session.landmarks_2d is None:
            self._fail(session, "MISSING_LANDMARKS_2D", "landmarks_2d is None in session")
            return None

        if session.validated_image is None:
            self._fail(session, "MISSING_VALIDATED_IMAGE", "validated_image is None in session")
            return None

        # BETO-TRACE: FV.OQ-FV-02 — landmarks ya están en espacio 224x224
        # FACE_DETECTOR aplica _crop_face() y transforma landmarks al espacio del crop 224x224.
        lmk_norm = session.landmarks_2d.copy().astype(np.float32)
        return lmk_norm  # (68, 2) ya en espacio 224x224

    def _emit_verdict(self, session: Session, score: float) -> None:
        # BETO-TRACE: FV.SEC5.INV.THRESHOLD_DECLARED_FIRST
        threshold_used = FIDELITY_THRESHOLD_PX

        if score < threshold_used:
            # BETO-TRACE: FV.SEC3.OUTPUT.FIDELITY_VERDICT — PASS
            session.fidelity_verdict = "PASS"
        else:
            # BETO-TRACE: FV.SEC3.OUTPUT.FIDELITY_VERDICT — FAIL
            session.fidelity_verdict = "FAIL"
            session.status = "FAIL"
            session.error = {
                "session_id": session.session_id,
                "error_code": "FIDELITY_FAIL",
                "fidelity_score": score,
                "threshold_used": threshold_used,
                "cause": (
                    f"Reprojection error {score:.2f}px exceeds threshold "
                    f"{threshold_used}px. Mesh does not match input face."
                ),
                "component": "FIDELITY_VERIFIER",
            }

    def _fail(self, session: Session, code: str, cause: str) -> None:
        # BETO-TRACE: BLENDERFACE.SEC5.INV.FAIL_PRODUCES_REPORT
        session.status = "FAIL"
        session.error = {
            "session_id": session.session_id,
            "error_code": code,
            "cause": cause,
            "component": "FIDELITY_VERIFIER",
        }
