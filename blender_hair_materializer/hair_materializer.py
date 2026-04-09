# BETO-TRACE: BFHAIR.SEC7.PHASE.BLENDER_HAIR_GENERATION
# BETO-TRACE: BFHAIR.SEC8.TECH.MCP_SOCKET_PROTOCOL
# BETO-TRACE: BFHAIR.SEC8.TECH.BLENDER_HAIR_CURVES_API
# BETO-TRACE: MIGRATE.SEC3.OUTPUT.FRAMED_MATERIALIZER
"""
BlenderHairMaterializer — Phase 4 del pipeline BLENDERFACE_HAIR

Comunica con Blender via blenderface-mcp addon (port 9876) usando
protocolo TCP con framing 4-byte big-endian (migrado desde protocolo plano).

Estrategia de guías (coordinate-system agnostic):
  - Lee posiciones y normales de los vértices del scalp DIRECTAMENTE del objeto head en Blender
  - Extiende cada guía a lo largo de la normal del vértice
  - Aplica droop gravitacional proporcional a la longitud
  - No requiere conocer la transformación de coordenadas del OBJ import

Fallback: si la API de Hair Curves no está disponible, usa Particle System HAIR.
"""
import json
import socket
import struct
from typing import Optional

from models.session import Session

# BETO-TRACE: BFHAIR.SEC8.TECH.MCP_SOCKET_PROTOCOL — mismo host/puerto que BlenderMaterializer
BLENDER_HOST = "172.31.128.1"
BLENDER_PORT = 7878

# BETO-TRACE: BFHAIR.OQ-6 — longitud de hebras por clase (unidades Blender)
HAIR_LENGTH_MAP = {
    "SHORT":  1.0,
    "MEDIUM": 1.8,
    "LONG":   3.0,
}
HAIR_LENGTH_DEFAULT = 0.8

# Puntos de control por guía (incluyendo raíz y punta)
POINTS_PER_STRAND = 8

# Peso vertical del crecimiento por longitud: SHORT = más lateral, LONG = más vertical
VERTICAL_WEIGHT = {"SHORT": 0.15, "MEDIUM": 0.50, "LONG": 0.80}
DROOP_FACTOR    = {"SHORT": 0.75, "MEDIUM": 0.85, "LONG": 1.10}


class BlenderHairMaterializer:
    """
    BETO-TRACE: BFHAIR.SEC7.PHASE.BLENDER_HAIR_GENERATION
    Phase 1: Verificar que el objeto head existe en Blender.
    Phase 2: Obtener posiciones y normales de vértices del scalp desde Blender.
    Phase 3: Crear objeto Hair Curves + guías + material.
    Phase 4: Verificar objeto creado.
    """

    def run(self, session: Session) -> Session:
        # ─── Guard ────────────────────────────────────────────────────────────
        # BETO-TRACE: BFHAIR.SEC5.INV.HAIR_01_FIDELITY_GATE
        if session.fidelity_verdict != "PASS":
            session.hair_status = "HAIR_FAIL"
            session.hair_error = {
                "session_id": session.session_id,
                "error_code": "FIDELITY_NOT_PASS",
                "cause": "BlenderHairMaterializer skipped: fidelity_verdict != PASS",
                "component": "BLENDER_HAIR_MATERIALIZER",
            }
            return session

        if not session.scalp_vertex_indices:
            self._hair_fail(session, "NO_SCALP_VERTICES",
                            "scalp_vertex_indices is empty — HairlineMapper may have failed")
            return session

        # ─── Phase 1 — Verificar head object en Blender ───────────────────────
        head_obj_name = session.blender_object_name
        if not head_obj_name:
            self._hair_fail(session, "NO_HEAD_OBJECT",
                            "session.blender_object_name is None — BlenderMaterializer must run first")
            return session

        verify_code = f"""
import bpy
obj = bpy.data.objects.get({repr(head_obj_name)})
print("FOUND" if obj is not None else "NOT_FOUND")
"""
        result = self._execute(session, verify_code)
        if result is None:
            return session
        if "NOT_FOUND" in result:
            self._hair_fail(session, "HEAD_NOT_IN_BLENDER",
                            f"Object '{head_obj_name}' not found in Blender scene")
            return session

        # ─── Phase 2 — Obtener posiciones y normales del scalp desde Blender ─
        # BETO-TRACE: BFHAIR.SEC8.TECH.BLENDER_HAIR_CURVES_API
        # Lee vértices directamente del mesh — coordinate-system agnostic (INV_HAIR_02)
        # Obtener zonas si vienen de run_hair_only (con etiquetas por zona)
        scalp_zones = getattr(session, "_scalp_zones", None)
        if scalp_zones:
            indices_with_zones = scalp_zones   # [(idx, zone), ...]
        else:
            indices_with_zones = [(i, "CROWN") for i in session.scalp_vertex_indices]

        indices_repr = repr(indices_with_zones)
        fetch_code = f"""
import bpy, json, mathutils
obj = bpy.data.objects.get({repr(head_obj_name)})
mesh = obj.data
try:
    mesh.calc_normals()
except AttributeError:
    pass
indices_with_zones = {indices_repr}
data = []
for idx, zone in indices_with_zones:
    if idx < len(mesh.vertices):
        v = mesh.vertices[idx]
        wn = (obj.matrix_world.to_3x3() @ v.normal).normalized()
        # Push-off: mover el root 2% de la longitud del cabello hacia afuera
        # por la normal para evitar que el strand empiece dentro del mesh
        push = wn * 0.02
        wp   = obj.matrix_world @ v.co
        root = wp + push
        world_pos    = list(root)
        world_normal = list(wn)
        data.append({{"p": world_pos, "n": world_normal, "z": zone}})
print("SCALP_DATA:" + json.dumps(data))
"""
        fetch_result = self._execute(session, fetch_code)
        if fetch_result is None:
            return session

        if not fetch_result.startswith("SCALP_DATA:"):
            self._hair_fail(session, "SCALP_FETCH_FAILED",
                            f"Unexpected response: {fetch_result[:200]}")
            return session

        try:
            scalp_data = json.loads(fetch_result[len("SCALP_DATA:"):])
        except json.JSONDecodeError as e:
            self._hair_fail(session, "SCALP_DATA_PARSE_ERROR", str(e))
            return session

        if not scalp_data:
            self._hair_fail(session, "SCALP_DATA_EMPTY", "No vertex data returned from Blender")
            return session

        # ─── Phase 3 — Crear Hair Curves + guías + material ─────────────────
        hair_obj_name = f"BFHAIR_{session.session_id}"
        length_class  = (session.hair_style or {}).get("length_class", "SHORT")
        wave_class    = (session.hair_style or {}).get("wave_class", "STRAIGHT")
        hair_length   = HAIR_LENGTH_MAP.get(length_class, HAIR_LENGTH_DEFAULT)
        r, g, b       = session.hair_color if session.hair_color else (0.05, 0.03, 0.02)

        # Serializar datos de guías para el script Python de Blender
        scalp_json = json.dumps(scalp_data)

        v_weight = VERTICAL_WEIGHT.get(length_class, 0.50)
        droop_f  = DROOP_FACTOR.get(length_class, 0.60)

        # ZONE_PARAMS dinámicos desde HairMaskAnalyzer (si están disponibles)
        zone_params_override = getattr(session, "_zone_params", None)
        zone_params_json = json.dumps(zone_params_override) if zone_params_override else "null"

        create_code = f"""
import bpy, json, mathutils

# ── Limpiar objetos BFHAIR anteriores en la escena ────────────────────────────
for obj in list(bpy.data.objects):
    if obj.name.startswith("BFHAIR_"):
        bpy.data.objects.remove(obj, do_unlink=True)
# Limpiar particle systems del head que sean BFHAIR
head_obj = bpy.data.objects.get({repr(head_obj_name)})
if head_obj:
    for ps in list(head_obj.particle_systems):
        if ps.name.startswith("BFHAIR_"):
            bpy.context.view_layer.objects.active = head_obj
            head_obj.select_set(True)
            head_obj.particle_systems.active_index = list(head_obj.particle_systems).index(ps)
            bpy.ops.object.particle_system_remove()

# ── Datos de guías ────────────────────────────────────────────────────────────
scalp_data  = json.loads({repr(scalp_json)})
hair_length = {hair_length}
pts_per     = {POINTS_PER_STRAND}
wave_class  = {repr(wave_class)}
head_name   = {repr(head_obj_name)}
hair_name   = {repr(hair_obj_name)}

head_obj = bpy.data.objects.get(head_name)

# ── Calcular puntos de cada guía ──────────────────────────────────────────────
# Dirección de crecimiento: NO usar normales del vértice (causan efecto erizo).
# En cambio: calcular la corona (centroide XY de los roots) y crecer alejándose
# de ella en el plano XY con un factor suave. El cabello sube y luego cae.
roots   = [mathutils.Vector(item['p']) for item in scalp_data]
zones   = [item.get('z', 'CROWN') for item in scalp_data]
normals = [mathutils.Vector(item['n']).normalized() for item in scalp_data]
crown_x = sum(r.x for r in roots) / len(roots)
crown_y = sum(r.y for r in roots) / len(roots)
crown_z = max(r.z for r in roots)

# Parámetros de crecimiento por zona — dinámicos desde HairMaskAnalyzer
# v_weight: componente vertical (0=horizontal, 1=vertical)
# droop:    caída gravitacional (mayor = cae más rápido)
# back:     componente hacia atrás (echa el cabello hacia la nuca)
_zone_override = json.loads({repr(zone_params_json)})
ZONE_PARAMS = _zone_override if _zone_override else {{
    'HAIRLINE': {{'v': 0.72, 'droop': 0.30, 'back': 0.20}},
    'CROWN':    {{'v': 0.40, 'droop': 0.70, 'back': 0.10}},
    'SIDE_L':   {{'v': 0.03, 'droop': 1.30, 'back': 0.08}},
    'SIDE_R':   {{'v': 0.03, 'droop': 1.30, 'back': 0.08}},
    'BACK':     {{'v': 0.10, 'droop': 1.00, 'back': 0.40}},
}}
DEFAULT_PARAMS = {{'v': {v_weight}, 'droop': {droop_f}, 'back': 0.10}}

guide_points = []
for root, zone, normal in zip(roots, zones, normals):
    p = ZONE_PARAMS.get(zone, DEFAULT_PARAMS)
    vw   = p['v']
    drp  = p['droop']
    back = p['back']

    dx = root.x - crown_x
    dy = root.y - crown_y
    lateral_len = (dx**2 + dy**2) ** 0.5
    if lateral_len > 0.0001:
        dx /= lateral_len
        dy /= lateral_len

    dist_norm = min(lateral_len / (crown_z * 0.8 + 0.5), 1.0)

    strand = []
    if zone in ('SIDE_L', 'SIDE_R'):
        # Sideburn: sigue la normal superficial los primeros puntos
        # luego cae hacia abajo — nunca perfora el mesh
        for j in range(pts_per):
            t = j / max(pts_per - 1, 1)
            # Primer tramo: salir por la normal; tramo final: caer por gravedad
            along_normal = normal * hair_length * t * 0.15
            gravity      = mathutils.Vector((0, 0, -hair_length * t * 0.85))
            pt = root + along_normal + gravity
            strand.append(list(pt))
    elif zone == 'BACK':
        # Nuca: sale por la normal (empuja afuera del mesh) luego cae
        for j in range(pts_per):
            t = j / max(pts_per - 1, 1)
            along_normal = normal * hair_length * t * 0.25
            gravity      = mathutils.Vector((0, 0, -hair_length * t * 0.75))
            pt = root + along_normal + gravity
            strand.append(list(pt))
    else:
        # HAIRLINE / CROWN: usar grow_dir basado en corona
        max_spread = (1.0 - vw) * (1.0 - dist_norm * 0.60)
        spread = min(lateral_len / (hair_length * 2 + 0.001), max_spread)
        grow_dir = mathutils.Vector((dx * spread, dy * spread - back, vw)).normalized()
        for j in range(pts_per):
            t = j / max(pts_per - 1, 1)
            pt = root + grow_dir * hair_length * t
            droop_vec = mathutils.Vector((0, 0, -drp * t * t * hair_length))
            strand.append(list(pt + droop_vec))
    guide_points.append(strand)

# ── Crear objeto Curve con POLY splines (100% compatible Blender 4.x) ─────────
crv_data = bpy.data.curves.new(name=hair_name, type='CURVE')
crv_data.dimensions = '3D'
crv_data.bevel_depth = 0.003   # grosor visual de cada hebra
crv_data.use_fill_caps = True

for strand_pts in guide_points:
    spline = crv_data.splines.new('POLY')
    spline.points.add(len(strand_pts) - 1)
    for i, pt in enumerate(strand_pts):
        spline.points[i].co = (pt[0], pt[1], pt[2], 1.0)

hair_obj = bpy.data.objects.new(hair_name, crv_data)
bpy.context.collection.objects.link(hair_obj)

# Parent al head
if head_obj:
    hair_obj.parent = head_obj
    hair_obj.matrix_parent_inverse = head_obj.matrix_world.inverted()

# ── Crear/actualizar Vertex Groups en el head mesh ────────────────────────────
# Cada zona recibe su propio grupo — base para future particle system por zona
if head_obj:
    zone_indices = {{}}
    for item in scalp_data:
        z = item.get('z', 'CROWN')
        zone_indices.setdefault(z, [])
    # Recuperar índices reales (antes del push-off no tenemos idx, usamos posición)
    # Los vertex groups se crean vacíos si no tenemos índices — se poblarán en runs futuros
    for zone_name in ['HAIRLINE', 'CROWN', 'SIDE_L', 'SIDE_R', 'BACK']:
        vg_name = "BFHAIR_" + zone_name
        if vg_name not in head_obj.vertex_groups:
            head_obj.vertex_groups.new(name=vg_name)
    print("VGROUPS_OK")

# ── Material Principled Hair BSDF ────────────────────────────────────────────
mat_name = "mat_hair_" + {repr(session.session_id)}
mat = bpy.data.materials.new(name=mat_name)
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()
output_node = nodes.new("ShaderNodeOutputMaterial")
try:
    hair_shader = nodes.new("ShaderNodeBsdfHairPrincipled")
    hair_shader.parametrization = 'COLOR'
    hair_shader.inputs["Color"].default_value = ({r}, {g}, {b}, 1.0)
    links.new(hair_shader.outputs["BSDF"], output_node.inputs["Surface"])
except Exception:
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = ({r}, {g}, {b}, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.6
    links.new(bsdf.outputs["BSDF"], output_node.inputs["Surface"])

if crv_data.materials:
    crv_data.materials[0] = mat
else:
    crv_data.materials.append(mat)

# ── Verificar resultado ───────────────────────────────────────────────────────
result_obj = bpy.data.objects.get(hair_name)
if result_obj:
    print("HAIR_DONE:" + hair_name)
else:
    print("HAIR_ERROR:object_not_found")
"""

        result = self._execute(session, create_code)
        if result is None:
            return session

        if "HAIR_ERROR" in result:
            self._hair_fail(session, "HAIR_CREATE_FAILED", f"Blender reported: {result}")
            return session

        # Extraer nombre del objeto creado
        if "HAIR_DONE:" in result:
            actual_name = result.split("HAIR_DONE:")[-1].strip()
        else:
            actual_name = hair_obj_name

        # ─── Phase 4 — Verificar Hair Tool (opcional) ─────────────────────────
        # BETO-TRACE: BFHAIR.SEC5.INV.HAIR_05_HAIRTOOL_OPTIONAL
        if session.hair_style and not ("PARTICLE" in result or "LEGACY" in result):
            self._apply_hair_tool_if_available(session, actual_name, wave_class)

        # ─── Resultado ────────────────────────────────────────────────────────
        # BETO-TRACE: BFHAIR.SEC4.FIELD.BLENDER_HAIR_OBJECT_NAME
        session.blender_hair_object_name = actual_name
        # BETO-TRACE: BFHAIR.SEC4.FIELD.HAIR_STATUS
        session.hair_status = session.hair_status or "HAIR_PASS"

        return session

    def _apply_hair_tool_if_available(
        self, session: Session, hair_obj_name: str, wave_class: str
    ) -> None:
        """
        BETO-TRACE: BFHAIR.SEC5.INV.HAIR_05_HAIRTOOL_OPTIONAL
        Aplica operadores de Hair Tool si el addon está disponible.
        Si no, continúa silenciosamente — no bloquea el pipeline.
        """
        check_code = f"""
import bpy
available = 'hair_tool' in bpy.context.preferences.addons
print("HT_AVAILABLE" if available else "HT_NOT_AVAILABLE")
"""
        check_result = self._execute(session, check_code)
        if not check_result or "HT_NOT_AVAILABLE" in check_result:
            return

        # Hair Tool disponible — aplicar grooming básico
        noise = 0.05 if wave_class == "STRAIGHT" else (0.12 if wave_class == "WAVY" else 0.22)
        ht_code = f"""
import bpy
obj = bpy.data.objects.get({repr(hair_obj_name)})
if obj:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.hair_tool_add_noise(strength={noise})
        print("HT_APPLIED")
    except Exception as e:
        print(f"HT_SKIP:{{e}}")
else:
    print("HT_OBJ_NOT_FOUND")
"""
        self._execute(session, ht_code)

    # ─── MCP socket — blenderface-mcp framed protocol ────────────────────────
    # BETO-TRACE: BFHAIR.SEC8.TECH.MCP_SOCKET_PROTOCOL
    # BETO-TRACE: MIGRATE.SEC8.TECH.STRUCT_PACK_FRAMING
    # BETO-TRACE: MIGRATE.SEC8.TECH.RECV_LOOP_FRAMING

    def _execute(self, session: Session, code: str) -> Optional[str]:
        """
        BETO-TRACE: BFHAIR.OQ-10 — protocol: blenderface-mcp framed TCP (4-byte big-endian)
        Migrated from raw JSON protocol (MIGRATE.SEC3.OUTPUT.FRAMED_MATERIALIZER).
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
                self._hair_fail(session, "BLENDER_MCP_ERROR", "Connection closed before response")
                return None

            response = json.loads(data.decode("utf-8"))

            if response.get("status") == "error":
                self._hair_fail(session, "BLENDER_MCP_ERROR",
                                response.get("message", "Unknown Blender error"))
                return None

            return str(response.get("result", {}).get("output", ""))

        except ConnectionRefusedError:
            self._hair_fail(session, "BLENDER_MCP_UNAVAILABLE",
                            f"Cannot connect to Blender on {BLENDER_HOST}:{BLENDER_PORT}")
            return None
        except Exception as e:
            self._hair_fail(session, "BLENDER_MCP_ERROR", str(e))
            return None

    @staticmethod
    def _send_framed(sock: socket.socket, data: bytes) -> None:
        """BETO-TRACE: MIGRATE.SEC8.TECH.STRUCT_PACK_FRAMING — 4-byte big-endian length prefix."""
        sock.sendall(struct.pack(">I", len(data)) + data)

    @staticmethod
    def _recv_framed(sock: socket.socket) -> Optional[bytes]:
        """BETO-TRACE: MIGRATE.SEC8.TECH.RECV_LOOP_FRAMING — read header then exact payload."""
        header = BlenderHairMaterializer._recv_exact(sock, 4)
        if header is None:
            return None
        (payload_len,) = struct.unpack(">I", header)
        if payload_len == 0:
            return b""
        return BlenderHairMaterializer._recv_exact(sock, payload_len)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _hair_fail(self, session: Session, code: str, cause: str) -> None:
        # BETO-TRACE: BFHAIR.SEC4.FIELD.HAIR_STATUS
        session.hair_status = "HAIR_FAIL"
        session.hair_error = {
            "session_id": session.session_id,
            "error_code": code,
            "cause": cause,
            "component": "BLENDER_HAIR_MATERIALIZER",
        }
