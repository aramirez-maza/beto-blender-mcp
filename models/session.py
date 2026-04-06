# BETO-TRACE: BLENDERFACE.SEC4.UNIT.SESION_RECONSTRUCCION
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import uuid
import numpy as np


@dataclass
class Session:
    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.SESSION_ID
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.INPUT_IMAGE_PATH
    image_path: str = ""

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.LANDMARKS_2D
    # (68, 2) float32 — coordenadas en píxeles de imagen original
    landmarks_2d: Optional[np.ndarray] = None

    # Imagen 224x224 RGB normalizada — DECLARED [BETO_ASSISTED] OQ-11
    validated_image: Optional[np.ndarray] = None

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.RECONSTRUCTION_PARAMS
    # Parámetros FLAME extraídos por DECA: shape, exp, pose, cam, tex, landmarks2d_proj
    deca_params: Optional[Dict[str, Any]] = None

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.OUTPUT_MESH_PATH
    mesh_path: Optional[str] = None
    mtl_path: Optional[str] = None
    texture_path: Optional[str] = None

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.FIDELITY_SCORE
    fidelity_score: Optional[float] = None

    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.FIDELITY_RESULT
    fidelity_verdict: Optional[str] = None  # PASS | FAIL

    blender_object_name: Optional[str] = None

    # Estado del pipeline — BLENDERFACE.SEC5.INV.FAIL_PRODUCES_REPORT
    status: str = "INIT"  # INIT | SUCCESS | FAIL | BLOCKED_BY_FIDELITY | BLOCKED_BY_MCP
    error: Optional[Dict[str, Any]] = None
