# BETO-TRACE: BLENDERFACE.SEC1.INTENT.PIPELINE_FOTO_A_3D
import sys
sys.path.insert(0, '/home/aramirez/claude_test/BETO-V2/DECA')
# BETO-TRACE: BLENDERFACE.SEC1.INTENT.SIN_INTERVENCION_HUMANA
# BETO-TRACE: BLENDERFACE.SEC1.INTENT.GOBERNANZA_FORMAL
"""
BLENDERFACE — Pipeline foto-frontal → cabeza 3D en Blender
Sin intervención humana. Gobernanza epistémica BETO v4.4.

Uso:
    python main.py --image /path/to/face.jpg
    python main.py --image /path/to/face.jpg --deca-weights ./data/deca_model.tar
    python main.py --image /path/to/face.jpg --threshold 4.0
"""
import argparse
import json
import sys

from models.session import Session                           # BETO-TRACE: BLENDERFACE.SEC4.UNIT.SESION_RECONSTRUCCION
from face_detector.detector import FaceDetector              # BETO-TRACE: BLENDERFACE.SEC2.SCOPE.DETECCION_LANDMARKS
from face_reconstructor.reconstructor import FaceReconstructor  # BETO-TRACE: BLENDERFACE.SEC2.SCOPE.RECONSTRUCCION_3D
from fidelity_verifier.verifier import FidelityVerifier, FIDELITY_THRESHOLD_PX  # BETO-TRACE: BLENDERFACE.SEC2.SCOPE.VERIFICACION_FIDELIDAD
from blender_materializer.materializer import BlenderMaterializer  # BETO-TRACE: BLENDERFACE.SEC2.SCOPE.MATERIALIZACION_BLENDER


def run_pipeline(image_path: str, deca_weights: str = None, threshold: float = None) -> Session:
    """
    BETO-TRACE: BLENDERFACE.SEC7.PHASE.INGESTION → LANDMARK_DETECTION →
                RECONSTRUCTION_3D → FIDELITY_VERIFICATION → BLENDER_MATERIALIZATION

    Orquesta el pipeline completo. Cada componente recibe y devuelve Session.
    Si session.status == "FAIL" en cualquier etapa, el pipeline se detiene.
    """
    # BETO-TRACE: BLENDERFACE.SEC4.FIELD.SESSION_ID — generado aquí, inmutable desde este punto
    session = Session(image_path=image_path)
    print(f"\n[BLENDERFACE] session_id={session.session_id} | image={image_path}")

    # Aplicar threshold override si fue declarado por el operador
    # BETO-TRACE: FV.OQ-07 — threshold configurable (DECLARED_WITH_LIMITS)
    if threshold is not None:
        import fidelity_verifier.verifier as fv_module
        fv_module.FIDELITY_THRESHOLD_PX = threshold
        print(f"[CONFIG] fidelity_threshold overridden to {threshold}px")

    # ─── Phase 1+2: Detección de landmarks ────────────────────────────────────
    print(f"[FACE_DETECTOR] Detecting landmarks...")
    detector = FaceDetector()
    session = detector.run(session)
    if session.status == "FAIL":
        return session

    print(f"[FACE_DETECTOR] OK — landmarks extracted (68 points)")

    # ─── Phase 2: Reconstrucción 3D ───────────────────────────────────────────
    print(f"[FACE_RECONSTRUCTOR] Running DECA reconstruction...")
    reconstructor = FaceReconstructor(weights_path=deca_weights)
    session = reconstructor.run(session)
    if session.status == "FAIL":
        return session

    print(f"[FACE_RECONSTRUCTOR] OK — mesh={session.mesh_path} | texture={session.texture_path}")

    # ─── Phase 3: Verificación de fidelidad ───────────────────────────────────
    # BETO-TRACE: BLENDERFACE.SEC5.INV.THRESHOLD_BEFORE_EXECUTION
    print(f"[FIDELITY_VERIFIER] Checking reprojection fidelity (threshold={FIDELITY_THRESHOLD_PX}px)...")
    verifier = FidelityVerifier()
    session = verifier.run(session)

    print(f"[FIDELITY_VERIFIER] score={session.fidelity_score}px | verdict={session.fidelity_verdict}")

    if session.fidelity_verdict != "PASS":
        # BETO-TRACE: BLENDERFACE.SEC5.INV.NO_MATERIALIZE_WITHOUT_PASS
        return session

    # ─── Phase 4: Materialización en Blender ──────────────────────────────────
    print(f"[BLENDER_MATERIALIZER] Materializing in Blender via MCP...")
    materializer = BlenderMaterializer()
    session = materializer.run(session)

    if session.status == "SUCCESS":
        print(f"[BLENDER_MATERIALIZER] OK — object='{session.blender_object_name}' in Blender scene")

    return session


def main():
    # BETO-TRACE: BLENDERFACE.SEC3.INPUT.FOTO_FRONTAL_PATH — CLI entry point
    parser = argparse.ArgumentParser(
        description="BLENDERFACE — foto frontal → cabeza 3D en Blender (BETO v4.4)"
    )
    parser.add_argument("--image",         required=True,  help="Path to frontal face image (JPEG or PNG)")
    parser.add_argument("--deca-weights",  default=None,   help="Path to DECA model weights (.tar). Default: DECA_WEIGHTS_PATH env var or ./data/deca_model.tar")
    parser.add_argument("--threshold",     type=float, default=None, help="Fidelity threshold in pixels (default: 5.0)")
    args = parser.parse_args()

    session = run_pipeline(
        image_path=args.image,
        deca_weights=args.deca_weights,
        threshold=args.threshold,
    )

    # BETO-TRACE: BLENDERFACE.SEC3.OUTPUT.REPORTE_EJECUCION — siempre emitido
    print("\n" + "=" * 60)
    print("EXECUTION REPORT")
    print("=" * 60)
    print(f"session_id:        {session.session_id}")
    print(f"status:            {session.status}")
    print(f"fidelity_score:    {session.fidelity_score}px")
    print(f"fidelity_verdict:  {session.fidelity_verdict}")
    print(f"mesh_path:         {session.mesh_path}")
    print(f"texture_path:      {session.texture_path}")
    print(f"blender_object:    {session.blender_object_name}")
    if session.error:
        print(f"\nERROR:")
        print(json.dumps(session.error, indent=2))
    print("=" * 60)

    sys.exit(0 if session.status == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
