# BETO-TRACE: HHAIR.SEC1.INTENT.HAIR_HANDLERS
# BETO-TRACE: BFMCP.SEC8.TECH.MODULAR_ARCH
"""
Hair Handlers

Handlers declared in BETO_CORE_HANDLERS_HAIR:
  create_hair_curves    — HHAIR.SEC3.HANDLER.CREATE_HAIR_CURVES
  create_particle_hair  — HHAIR.SEC3.HANDLER.CREATE_PARTICLE_HAIR
  set_hair_guide_curves — HHAIR.SEC3.HANDLER.SET_HAIR_GUIDE_CURVES

Key invariants (BETO_CORE_HANDLERS_HAIR SEC5):
  - Guide positions and normals are read directly from Blender vertices
    (HHAIR.SEC8.TECH.VERTEX_READ_FROM_BLENDER) — never from external coords.
  - Hair Curves API (native Blender 4.x) is preferred;
    Particle System HAIR is the declared fallback.
  - droop_factor = hair_length * 0.15
"""
import logging

import bpy
import mathutils

log = logging.getLogger(__name__)

# BETO-TRACE: HHAIR.SEC8.TECH.HAIR_LENGTH_MAP
# Inherited from blender_hair_materializer — declared in BETO_CORE_HANDLERS_HAIR SEC8
HAIR_LENGTH_MAP: dict[str, float] = {
    "short":     0.15,
    "medium":    0.25,
    "long":      0.40,
    "very_long": 0.55,
}
HAIR_LENGTH_DEFAULT: float = 0.25

POINTS_PER_STRAND: int = 8


def create_hair_curves(
    head_object_name: str,
    scalp_vertex_indices: list,
    hair_length: float = None,
    num_curves: int = 20,
    style: str = "medium",
) -> dict:
    """
    BETO-TRACE: HHAIR.SEC3.HANDLER.CREATE_HAIR_CURVES
    BETO-TRACE: HHAIR.SEC8.TECH.HAIR_CURVES_API

    Creates a Curve object with POLY splines as hair guides.
    Positions and normals are read from the head object in Blender
    (HHAIR.SEC8.TECH.VERTEX_READ_FROM_BLENDER).
    """
    head_obj = bpy.data.objects.get(head_object_name)
    if head_obj is None:
        raise KeyError(f"Head object '{head_object_name}' not found in scene.")
    if head_obj.type != "MESH":
        raise TypeError(f"Object '{head_object_name}' is not a MESH (type: {head_obj.type}).")

    resolved_length = hair_length or HAIR_LENGTH_MAP.get(style.lower(), HAIR_LENGTH_DEFAULT)
    guide_points = _compute_guide_points(head_obj, scalp_vertex_indices, resolved_length)

    curves_name = f"BFHAIR_curves_{head_object_name}"
    _remove_existing(curves_name)

    crv_data = bpy.data.curves.new(name=curves_name, type="CURVE")
    crv_data.dimensions = "3D"
    crv_data.bevel_depth = 0.003

    for strand_pts in guide_points:
        spline = crv_data.splines.new("POLY")
        spline.points.add(len(strand_pts) - 1)
        for i, pt in enumerate(strand_pts):
            spline.points[i].co = (pt[0], pt[1], pt[2], 1.0)

    curves_obj = bpy.data.objects.new(curves_name, crv_data)
    bpy.context.collection.objects.link(curves_obj)
    curves_obj.parent = head_obj
    curves_obj.matrix_parent_inverse = head_obj.matrix_world.inverted()

    return {
        "curves_object_name": curves_name,
        "curve_count":        len(guide_points),
        "hair_length":        resolved_length,
        "style":              style,
    }


def create_particle_hair(
    head_object_name: str,
    scalp_vertex_indices: list,
    hair_length: float = None,
    num_curves: int = 20,
    style: str = "medium",
) -> dict:
    """
    BETO-TRACE: HHAIR.SEC3.HANDLER.CREATE_PARTICLE_HAIR
    BETO-TRACE: HHAIR.SEC8.TECH.PARTICLE_HAIR_FALLBACK

    Fallback: creates a Particle System HAIR on the head object.
    Used when Hair Curves API is not available or explicitly requested.
    """
    head_obj = bpy.data.objects.get(head_object_name)
    if head_obj is None:
        raise KeyError(f"Head object '{head_object_name}' not found in scene.")

    resolved_length = hair_length or HAIR_LENGTH_MAP.get(style.lower(), HAIR_LENGTH_DEFAULT)
    ps_name = f"BFHAIR_particles_{head_object_name}"

    # Remove existing particle system with same name
    bpy.context.view_layer.objects.active = head_obj
    for ps in list(head_obj.particle_systems):
        if ps.name == ps_name:
            head_obj.particle_systems.active_index = list(head_obj.particle_systems).index(ps)
            bpy.ops.object.particle_system_remove()
            break

    bpy.ops.object.particle_system_add()
    ps = head_obj.particle_systems[-1]
    ps.name = ps_name
    settings = ps.settings
    settings.type = "HAIR"
    settings.count = num_curves
    settings.hair_length = resolved_length

    return {
        "particle_system_name": ps_name,
        "count":                num_curves,
        "hair_length":          resolved_length,
        "style":                style,
    }


def set_hair_guide_curves(
    curves_object_name: str,
    scalp_vertex_indices: list,
    hair_length: float = 0.25,
) -> dict:
    """
    BETO-TRACE: HHAIR.SEC3.HANDLER.SET_HAIR_GUIDE_CURVES
    BETO-TRACE: HHAIR.SEC8.TECH.VERTEX_READ_FROM_BLENDER

    Updates guide curves on an existing Curve object using new vertex indices.
    Reads positions from the parent mesh object in Blender.
    """
    curves_obj = bpy.data.objects.get(curves_object_name)
    if curves_obj is None:
        raise KeyError(f"Curves object '{curves_object_name}' not found.")
    if curves_obj.parent is None or curves_obj.parent.type != "MESH":
        raise ValueError(f"Curves object '{curves_object_name}' has no MESH parent.")

    head_obj = curves_obj.parent
    guide_points = _compute_guide_points(head_obj, scalp_vertex_indices, hair_length)

    crv_data = curves_obj.data
    # Remove existing splines
    while crv_data.splines:
        crv_data.splines.remove(crv_data.splines[0])

    for strand_pts in guide_points:
        spline = crv_data.splines.new("POLY")
        spline.points.add(len(strand_pts) - 1)
        for i, pt in enumerate(strand_pts):
            spline.points[i].co = (pt[0], pt[1], pt[2], 1.0)

    return {
        "guide_count": len(guide_points),
        "hair_length": hair_length,
    }


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _compute_guide_points(
    head_obj: bpy.types.Object,
    scalp_vertex_indices: list,
    hair_length: float,
) -> list[list[list[float]]]:
    """
    BETO-TRACE: HHAIR.SEC8.TECH.VERTEX_READ_FROM_BLENDER
    BETO-TRACE: HHAIR.SEC8.TECH.HAIR_CURVES_API

    Reads vertex positions and normals directly from the Blender mesh.
    Extends each guide along the normal with gravitational droop.
    droop_factor = hair_length * 0.15 (declared in BETO_CORE_HANDLERS_HAIR SEC5).
    """
    mesh = head_obj.data
    world_mat = head_obj.matrix_world
    world_mat_3x3 = world_mat.to_3x3()

    # BETO-TRACE: HHAIR.SEC8.TECH.VERTEX_READ_FROM_BLENDER
    droop_factor = hair_length * 0.15

    guide_points: list[list[list[float]]] = []

    for idx in scalp_vertex_indices:
        if idx >= len(mesh.vertices):
            log.warning(f"Vertex index {idx} out of range (mesh has {len(mesh.vertices)} vertices)")
            continue

        vert = mesh.vertices[idx]
        world_pos = world_mat @ vert.co
        world_normal = (world_mat_3x3 @ vert.normal).normalized()

        # Push root slightly off the surface to avoid Z-fighting
        root = world_pos + world_normal * 0.005

        strand: list[list[float]] = []
        for j in range(POINTS_PER_STRAND):
            t = j / max(POINTS_PER_STRAND - 1, 1)
            pt = root + world_normal * hair_length * t
            # Gravitational droop — quadratic along strand
            droop = mathutils.Vector((0.0, 0.0, -droop_factor * t * t))
            final = pt + droop
            strand.append([final.x, final.y, final.z])

        guide_points.append(strand)

    return guide_points


def _remove_existing(object_name: str) -> None:
    obj = bpy.data.objects.get(object_name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
