# BETO-TRACE: FR.SEC1.INTENT.RECONSTRUCT_FLAME_MESH
import os
import json
import numpy as np
from pathlib import Path
from typing import Optional

from models.session import Session


class FaceReconstructor:
    """
    BETO-TRACE: FR.SEC1.INTENT.RECONSTRUCT_FLAME_MESH
    Phase 1: Inferencia DECA — extrae parámetros FLAME.
    Phase 2: Decodificación de malla 3D con topología FLAME (5023 vértices).
    Phase 3: Exportación .obj + .mtl.
    Phase 4: Exportación texture map .png.
    """

    # BETO-TRACE: FR.SEC8.TECH.DECA
    def __init__(self, weights_path: Optional[str] = None):
        import torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # BETO-TRACE: FR.OQ-FR-01 — ruta pesos DECA desde env var o argumento
        self.weights_path = (
            weights_path
            or os.environ.get("DECA_WEIGHTS_PATH")
            or "./data/deca_model.tar"
        )
        self._deca = None  # lazy init

    def _init_deca(self, session: Session) -> bool:
        """Lazy-load DECA model. Falla si los pesos no existen."""
        if self._deca is not None:
            return True

        if not os.path.exists(self.weights_path):
            # BETO-TRACE: FR.OQ-FR-01 — halt si pesos no presentes
            self._fail(session, "MISSING_DECA_WEIGHTS",
                       f"DECA weights not found at '{self.weights_path}'. "
                       f"Set DECA_WEIGHTS_PATH or pass weights_path to FaceReconstructor.")
            return False

        try:
            from decalib.deca import DECA
            from decalib.utils.config import cfg as deca_cfg
            deca_cfg.model.use_tex = False  # texture proyectada desde foto (no requiere FLAME_albedo)
            deca_cfg.pretrained_modelpath = self.weights_path
            self._deca = DECA(config=deca_cfg, device=self.device)
        except ImportError:
            self._fail(session, "DECA_NOT_INSTALLED",
                       "DECA library not found. Install via: pip install git+https://github.com/YadiraF/DECA")
            return False
        except Exception as e:
            self._fail(session, "DECA_INIT_FAILED", str(e))
            return False

        return True

    def run(self, session: Session) -> Session:
        if not self._init_deca(session):
            return session

        # BETO-TRACE: FR.SEC7.PHASE.DECA_INFERENCE
        deca_params = self._run_inference(session)
        if deca_params is None:
            return session

        session.deca_params = deca_params

        output_dir = Path(f"./output/{session.session_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # BETO-TRACE: FR.SEC7.PHASE.MESH_DECODING + FR.SEC7.PHASE.MESH_EXPORT
        if not self._export_mesh(session, deca_params, output_dir):
            return session

        # BETO-TRACE: FR.SEC7.PHASE.TEXTURE_EXPORT
        if not self._export_texture(session, deca_params, output_dir):
            return session

        return session

    # ─── Phase 1 — DECA Inference ────────────────────────────────────────────

    def _run_inference(self, session: Session) -> Optional[dict]:
        import torch
        from decalib.utils import util
        try:
            img = session.validated_image  # (224, 224, 3) uint8
            tensor = torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0).float() / 255.0
            tensor = tensor.to(self.device)

            with torch.no_grad():
                codedict = self._deca.encode(tensor)

                # Bypass DECA's renderer (pytorch3d rasterizer no compilado con CUDA).
                # Llamamos FLAME directamente para obtener vertices y landmarks.
                # BETO-TRACE: FR.SEC7.PHASE.DECA_INFERENCE
                vertices, landmarks2d, landmarks3d = self._deca.flame(
                    shape_params=codedict["shape"],
                    expression_params=codedict["exp"],
                    pose_params=codedict["pose"],
                )

                # Proyección weak perspective (cámara DECA)
                # BETO-TRACE: FV.SEC8.TECH.WEAK_PERSPECTIVE_CAMERA
                trans_verts = util.batch_orth_proj(vertices, codedict["cam"])
                lmk2d_proj  = util.batch_orth_proj(landmarks2d, codedict["cam"])

                # Obtener faces del modelo FLAME
                faces = self._deca.flame.faces_tensor  # (F, 3) int

            # BETO-TRACE: FR.SEC4.FIELD.DECA_PARAMS_DICT
            params = {
                "shape":             codedict["shape"].cpu().numpy(),     # (1, 100)
                "exp":               codedict["exp"].cpu().numpy(),       # (1, 50)
                "pose":              codedict["pose"].cpu().numpy(),       # (1, 6)
                "cam":               codedict["cam"].cpu().numpy(),        # (1, 3) weak perspective
                "vertices":          vertices.cpu().numpy(),              # (1, 5023, 3)
                "trans_verts":       trans_verts.cpu().numpy(),           # (1, 5023, 3) projected
                "faces":             faces.cpu().numpy(),                 # (F, 3)
                "landmarks2d_proj":  lmk2d_proj.cpu().numpy(),           # (1, 68, 2) en [-1,1]
            }
            return params

        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                # BETO-TRACE: FR.OQ-FR-02 — OOM halt
                self._fail(session, "RECONSTRUCTION_FAILED",
                           "out of memory — try running on GPU or with a smaller batch")
            else:
                self._fail(session, "RECONSTRUCTION_FAILED", str(e))
            return None
        except Exception as e:
            self._fail(session, "RECONSTRUCTION_FAILED", str(e))
            return None

    # ─── Phase 3 — Mesh Export ────────────────────────────────────────────────

    def _export_mesh(self, session: Session, params: dict, output_dir: Path) -> bool:
        try:
            vertices = params["vertices"][0] * 10.0  # (5023, 3) — scale FLAME space to Blender units
            texture_path_rel = f"{session.session_id}_texture.png"

            mesh_path = str(output_dir / "mesh.obj")
            mtl_path  = str(output_dir / "mesh.mtl")

            # BETO-TRACE: FR.OQ-FR-02 — .obj con UV correctos de render.uvcoords/uvfaces
            # render.uvcoords: (5118, 3) en [-1,1] — distinto count que vértices (seams UV)
            # render.uvfaces:  (9976, 3) — índices UV por cara, paralelos a faces
            uvcoords = self._deca.render.uvcoords[0].cpu().numpy()  # (5118, 3)
            uvfaces  = self._deca.render.uvfaces[0].cpu().numpy()   # (9976, 3)
            faces    = self._deca.flame.faces_tensor.cpu().numpy()  # (9976, 3)

            # Convertir UV de [-1,1] a [0,1]
            uv_01 = (uvcoords[:, :2] + 1.0) / 2.0  # (5118, 2)

            with open(mesh_path, "w") as f:
                f.write("mtllib mesh.mtl\n")
                for v in vertices:
                    f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
                for uv in uv_01:
                    f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
                f.write("usemtl material0\n")
                for fi in range(len(faces)):
                    v0, v1, v2   = faces[fi][0]+1,   faces[fi][1]+1,   faces[fi][2]+1
                    u0, u1, u2   = uvfaces[fi][0]+1, uvfaces[fi][1]+1, uvfaces[fi][2]+1
                    f.write(f"f {v0}/{u0} {v1}/{u1} {v2}/{u2}\n")

            with open(mtl_path, "w") as f:
                f.write("newmtl material0\n")
                f.write(f"map_Kd {texture_path_rel}\n")

            # BETO-TRACE: FR.SEC3.OUTPUT.MESH_PATH
            session.mesh_path = mesh_path
            session.mtl_path  = mtl_path
            return True

        except Exception as e:
            self._fail(session, "MESH_EXPORT_FAILED", str(e))
            return False

    # ─── Phase 4 — Texture Export ─────────────────────────────────────────────

    def _export_texture(self, session: Session, params: dict, output_dir: Path) -> bool:
        try:
            from PIL import Image as PILImage

            # BETO-TRACE: FR.SEC7.PHASE.TEXTURE_EXPORT — bake foto frontal → UV space FLAME
            tex_array = self._bake_uv_texture(session, params)

            texture_path = str(output_dir / f"{session.session_id}_texture.png")
            PILImage.fromarray(tex_array).save(texture_path)

            # BETO-TRACE: FR.SEC3.OUTPUT.TEXTURE_PATH
            session.texture_path = texture_path
            return True

        except Exception as e:
            self._fail(session, "TEXTURE_EXPORT_FAILED", str(e))
            return False

    def _bake_uv_texture(self, session: Session, params: dict) -> np.ndarray:
        """
        BETO-TRACE: FR.SEC7.PHASE.TEXTURE_EXPORT
        Proyecta la foto frontal al espacio UV de FLAME sin pytorch3d.
        Para cada triángulo en UV space, interpola baricentricamente las posiciones
        proyectadas de sus vértices y samplea la foto.
        """
        photo = session.validated_image.astype(np.float32)  # (224, 224, 3)
        H, W = photo.shape[:2]

        tex_size = 512

        # Base: color de piel uniforme derivado de la foto del sujeto (Opción B)
        # Se calcula DESPUÉS de la proyección UV — ver más abajo
        skin_base = None  # se construye tras el loop de baking

        # Acumulador separado para la foto frontal proyectada
        texture    = np.zeros((tex_size, tex_size, 3), dtype=np.float32)
        weight_map = np.zeros((tex_size, tex_size),    dtype=np.float32)

        uvcoords = self._deca.render.uvcoords[0].cpu().numpy()  # (5118, 3)
        uvfaces  = self._deca.render.uvfaces[0].cpu().numpy().astype(int)   # (9976, 3)
        faces    = self._deca.flame.faces_tensor.cpu().numpy().astype(int)  # (9976, 3)

        # UV coords → pixel space [0, tex_size]
        uv_px_x = (uvcoords[:, 0] + 1.0) / 2.0 * tex_size         # u
        uv_px_y = (1.0 - (uvcoords[:, 1] + 1.0) / 2.0) * tex_size # v flip

        # Projected vertices → photo pixel space [0, 224]
        tv = params["trans_verts"][0]  # (5023, 3) en [-1,1]
        v_px_x = (tv[:, 0] + 1.0) / 2.0 * W
        v_px_y = (1.0 - (tv[:, 1] + 1.0) / 2.0) * H

        # Test de visibilidad: área signada del triángulo en espacio proyectado 2D.
        # Si el área es positiva → triángulo mira hacia la cámara.
        # Más robusto que normales 3D porque usa la proyección real (trans_verts).
        # trans_verts está en [-1,1]; basta con X,Y para el área signada.
        p0 = tv[faces[:, 0], :2]  # (F, 2)
        p1 = tv[faces[:, 1], :2]
        p2 = tv[faces[:, 2], :2]
        # Área signada = 0.5 * cross2d(p1-p0, p2-p0)
        cross2d = (p1[:, 0]-p0[:, 0]) * (p2[:, 1]-p0[:, 1]) \
                - (p1[:, 1]-p0[:, 1]) * (p2[:, 0]-p0[:, 0])
        face_visible = cross2d > 0.0  # (F,)

        for i in range(len(faces)):
            # Saltar caras que apuntan hacia atrás
            if not face_visible[i]:
                continue

            face  = faces[i]    # (3,) índices vértice
            uvf   = uvfaces[i]  # (3,) índices UV

            # Triángulo en espacio UV (textura)
            ax, ay = uv_px_x[uvf[0]], uv_px_y[uvf[0]]
            bx, by = uv_px_x[uvf[1]], uv_px_y[uvf[1]]
            cx, cy = uv_px_x[uvf[2]], uv_px_y[uvf[2]]

            # Posiciones en foto para cada vértice
            pa = np.array([v_px_x[face[0]], v_px_y[face[0]]])
            pb = np.array([v_px_x[face[1]], v_px_y[face[1]]])
            pc = np.array([v_px_x[face[2]], v_px_y[face[2]]])

            # Bounding box en textura
            x0 = max(0, int(min(ax, bx, cx)))
            x1 = min(tex_size, int(max(ax, bx, cx)) + 1)
            y0 = max(0, int(min(ay, by, cy)))
            y1 = min(tex_size, int(max(ay, by, cy)) + 1)

            if x1 <= x0 or y1 <= y0:
                continue

            # Grilla de píxeles
            px = np.arange(x0, x1, dtype=np.float32) + 0.5
            py = np.arange(y0, y1, dtype=np.float32) + 0.5
            PX, PY = np.meshgrid(px, py)  # (dh, dw)

            # Coordenadas baricéntricas respecto al triángulo UV
            denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(denom) < 1e-8:
                continue

            lam_a = ((by - cy) * (PX - cx) + (cx - bx) * (PY - cy)) / denom
            lam_b = ((cy - ay) * (PX - cx) + (ax - cx) * (PY - cy)) / denom
            lam_c = 1.0 - lam_a - lam_b

            mask = (lam_a >= 0) & (lam_b >= 0) & (lam_c >= 0)
            if not mask.any():
                continue

            # Interpolar posición en foto
            interp_x = lam_a * pa[0] + lam_b * pb[0] + lam_c * pc[0]
            interp_y = lam_a * pa[1] + lam_b * pb[1] + lam_c * pc[1]

            ix = np.clip(interp_x[mask], 0, W - 1).astype(int)
            iy = np.clip(interp_y[mask], 0, H - 1).astype(int)

            colors = photo[iy, ix]  # (N, 3)

            rows = np.where(mask)[0] + y0
            cols = np.where(mask)[1] + x0
            np.add.at(texture,    (rows, cols), colors)
            np.add.at(weight_map, (rows, cols), 1.0)

        # Normalizar foto proyectada
        photo_layer = np.zeros((tex_size, tex_size, 3), dtype=np.float32)
        nz = weight_map > 0
        photo_layer[nz] = texture[nz] / weight_map[nz, np.newaxis]

        # Derivar color de piel del sujeto desde los píxeles proyectados
        # Usar solo píxeles con luminancia media (filtrar oscuros=cejas/pelos, claros=dientes)
        if nz.any():
            projected = photo_layer[nz]  # (N, 3)
            lum = 0.299 * projected[:, 0] + 0.587 * projected[:, 1] + 0.114 * projected[:, 2]
            skin_sel = projected[(lum > 80) & (lum < 230)]
            skin_color = skin_sel.mean(axis=0) if len(skin_sel) > 0 else projected.mean(axis=0)
        else:
            skin_color = np.array([200.0, 170.0, 150.0])

        # Base: color de piel uniforme del sujeto para lados/nuca
        skin_base = np.full((tex_size, tex_size, 3), skin_color, dtype=np.float32)

        # Dilate 3px para cubrir costuras UV sin dejar huecos
        try:
            from scipy.ndimage import binary_dilation, distance_transform_edt
            dilated = binary_dilation(nz, iterations=3)
            seam_zone = dilated & ~nz
            if seam_zone.any():
                _, idx = distance_transform_edt(~nz, return_indices=True)
                for c in range(3):
                    photo_layer[seam_zone, c] = photo_layer[idx[0][seam_zone], idx[1][seam_zone], c]
            nz = dilated
        except ImportError:
            pass

        # Blend: foto frontal sobre color de piel del sujeto
        # Zona con foto → 85% foto + 15% piel; zona sin foto → 100% color piel
        alpha = np.where(nz, 0.85, 0.0).astype(np.float32)[:, :, np.newaxis]
        result = alpha * photo_layer + (1.0 - alpha) * skin_base
        return result.astype(np.uint8)

    # ─── helpers ──────────────────────────────────────────────────────────────

    def _fail(self, session: Session, code: str, cause: str) -> None:
        # BETO-TRACE: BLENDERFACE.SEC5.INV.FAIL_PRODUCES_REPORT
        session.status = "FAIL"
        session.error = {
            "session_id": session.session_id,
            "error_code": code,
            "cause": cause,
            "component": "FACE_RECONSTRUCTOR",
        }
