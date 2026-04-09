# BETO-TRACE: BM.SEC1.INTENT.MATERIALIZE_IN_BLENDER
# BETO-TRACE: MIGRATE.SEC3.OUTPUT.FRAMED_MATERIALIZER
import json
import os
import socket
import struct
from typing import Optional

from models.session import Session

# BETO-TRACE: BM.SEC8.TECH.BLENDER_MCP_CHANNEL
# Blender MCP addon escucha en localhost:9876 (BlenderConnection protocol)
BLENDER_HOST = "172.31.128.1"  # Windows host IP desde WSL2
BLENDER_PORT = 7878


class BlenderMaterializer:
    """
    BETO-TRACE: BM.SEC1.INTENT.MATERIALIZE_IN_BLENDER
    Phase 1: Verificar precondición verdict=PASS (INV-1).
    Phase 2: Importar .obj en escena Blender activa vía socket MCP.
    Phase 3: Asignar material con texture .png al objeto importado.
    Phase 4: Verificar existencia del objeto y emitir reporte final.
    """

    def run(self, session: Session) -> Session:
        # ─── Phase 1 — Guard: verdict=PASS obligatorio ────────────────────────
        # BETO-TRACE: BM.SEC7.PHASE.PRECONDITION_CHECK
        # BETO-TRACE: BLENDERFACE.SEC5.INV.NO_MATERIALIZE_WITHOUT_PASS
        if session.fidelity_verdict != "PASS":
            session.status = "BLOCKED_BY_FIDELITY"
            session.error = {
                "session_id": session.session_id,
                "error_code": "FIDELITY_FAIL_BLOCKED",
                "cause": (
                    f"fidelity_verdict='{session.fidelity_verdict}'. "
                    "Materialization blocked by INV-1: no mesh sent to Blender without PASS."
                ),
                "component": "BLENDER_MATERIALIZER",
            }
            return session

        # ─── Phase 2 — Import .obj en Blender ────────────────────────────────
        # BETO-TRACE: BM.SEC7.PHASE.MCP_IMPORT
        # BETO-TRACE: BM.OQ-13 — resolved: execute_blender_code via socket
        # Blender corre en Windows — convertir ruta Linux a UNC WSL2
        mesh_abs = self._to_windows_path(os.path.abspath(session.mesh_path))
        texture_abs_win = self._to_windows_path(os.path.abspath(session.texture_path))
        obj_name = f"BLENDERFACE_{session.session_id}"

        import_code = f"""
import bpy
bpy.ops.wm.obj_import(filepath={repr(mesh_abs)})
imported = bpy.context.selected_objects
if imported:
    imported[0].name = {repr(obj_name)}
    print("IMPORTED:" + imported[0].name)
else:
    print("NO_OBJECT_SELECTED")
"""
        response = self._execute_in_blender(session, import_code)
        if response is None:
            return session  # error already set

        if not str(response).startswith("IMPORTED:"):
            self._fail(session, "BLENDER_IMPORT_FAILED",
                       f"Import did not produce expected object. Response: {response}")
            return session

        # BETO-TRACE: BM.SEC3.OUTPUT.BLENDER_OBJECT_NAME
        session.blender_object_name = obj_name

        # ─── Phase 3 — Asignar material con textura ───────────────────────────
        # BETO-TRACE: BM.SEC7.PHASE.TEXTURE_ASSIGNMENT
        texture_abs = texture_abs_win
        material_code = f"""
import bpy
obj = bpy.data.objects.get({repr(obj_name)})
if obj is None:
    print("OBJECT_NOT_FOUND")
else:
    mat = bpy.data.materials.new(name="mat_" + {repr(session.session_id)})
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output   = nodes.new("ShaderNodeOutputMaterial")
    bsdf     = nodes.new("ShaderNodeBsdfPrincipled")
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load({repr(texture_abs)})
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    print("MATERIAL_ASSIGNED")
"""
        mat_response = self._execute_in_blender(session, material_code)
        if mat_response is None:
            return session

        if str(mat_response).strip() != "MATERIAL_ASSIGNED":
            self._fail(session, "TEXTURE_ASSIGNMENT_FAILED",
                       f"Unexpected response: {mat_response}")
            return session

        # ─── Phase 4 — Verificación + reporte ────────────────────────────────
        # BETO-TRACE: BM.SEC7.PHASE.VERIFICATION
        # BETO-TRACE: BLENDERFACE.SEC5.INV.NO_MATERIALIZE_WITHOUT_PASS (confirmación)
        verify_code = f"""
import bpy
obj = bpy.data.objects.get({repr(obj_name)})
if obj is not None:
    print("EXISTS")
else:
    print("NOT_FOUND")
"""
        verify_response = self._execute_in_blender(session, verify_code)
        if verify_response is None:
            return session

        if str(verify_response).strip() != "EXISTS":
            self._fail(session, "BLENDER_OBJECT_NOT_FOUND",
                       f"Object '{obj_name}' not found in scene after import.")
            return session

        # BETO-TRACE: BM.SEC3.OUTPUT.EXECUTION_REPORT — SUCCESS
        session.status = "SUCCESS"
        return session

    # ─── Blender socket communication ────────────────────────────────────────
    # BETO-TRACE: MIGRATE.SEC8.TECH.STRUCT_PACK_FRAMING
    # BETO-TRACE: MIGRATE.SEC8.TECH.RECV_LOOP_FRAMING

    def _execute_in_blender(self, session: Session, code: str) -> Optional[str]:
        """
        BETO-TRACE: BM.OQ-13 — protocol: blenderface-mcp framed TCP (4-byte big-endian length prefix)
        Migrated from raw JSON protocol to framed protocol (MIGRATE.SEC3.OUTPUT.FRAMED_MATERIALIZER).
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(60.0)
            sock.connect((BLENDER_HOST, BLENDER_PORT))

            command = json.dumps({"type": "execute_code", "params": {"code": code}})
            self._send_framed(sock, command.encode("utf-8"))

            data = self._recv_framed(sock)
            sock.close()

            if data is None:
                self._fail(session, "BLENDER_MCP_ERROR", "Connection closed before response received")
                return None

            response = json.loads(data.decode("utf-8"))

            if response.get("status") == "error":
                self._fail(session, "BLENDER_MCP_ERROR",
                           response.get("message", "Unknown Blender error"))
                return None

            return str(response.get("result", {}).get("output", ""))

        except ConnectionRefusedError:
            # BETO-TRACE: BM.OQ-BM-01 — Blender no disponible
            self._fail(session, "BLENDER_MCP_UNAVAILABLE",
                       f"Cannot connect to Blender on {BLENDER_HOST}:{BLENDER_PORT}. "
                       "Make sure Blender is open with the blenderface-mcp addon running.")
            return None
        except Exception as e:
            self._fail(session, "BLENDER_MCP_ERROR", str(e))
            return None

    @staticmethod
    def _send_framed(sock: socket.socket, data: bytes) -> None:
        """BETO-TRACE: MIGRATE.SEC8.TECH.STRUCT_PACK_FRAMING — 4-byte big-endian length prefix."""
        sock.sendall(struct.pack(">I", len(data)) + data)

    @staticmethod
    def _recv_framed(sock: socket.socket) -> Optional[bytes]:
        """BETO-TRACE: MIGRATE.SEC8.TECH.RECV_LOOP_FRAMING — read header then exact payload."""
        header = BlenderMaterializer._recv_exact(sock, 4)
        if header is None:
            return None
        (payload_len,) = struct.unpack(">I", header)
        if payload_len == 0:
            return b""
        return BlenderMaterializer._recv_exact(sock, payload_len)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    # ─── helpers ──────────────────────────────────────────────────────────────

    def _to_windows_path(self, linux_path: str) -> str:
        """Convierte ruta Linux WSL2 a ruta UNC accesible desde Windows."""
        import subprocess
        try:
            result = subprocess.run(
                ["wslpath", "-w", linux_path],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        # Fallback manual
        return linux_path.replace("/home/", "\\\\wsl.localhost\\Ubuntu\\home\\")

    def _fail(self, session: Session, code: str, cause: str) -> None:
        # BETO-TRACE: BLENDERFACE.SEC5.INV.FAIL_PRODUCES_REPORT
        session.status = "BLOCKED_BY_MCP" if "MCP" in code else "FAIL"
        session.error = {
            "session_id": session.session_id,
            "error_code": code,
            "cause": cause,
            "component": "BLENDER_MATERIALIZER",
        }
